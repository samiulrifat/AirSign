"""Storage of biometric material: always encrypted, never raw sensor streams."""

from __future__ import annotations

import io

import numpy as np
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from .models import AttemptFingerprint, EnrollmentSession, SignatureTemplate
from .security import seal, unseal
from .signal.enroll import Template
from .signal.pipeline import Processed

MAX_FINGERPRINTS = 50


def _aad(user_id: str, kind: str) -> str:
    return f"{kind}:{user_id}"


# Enrollment samples held between requests ------------------------------------------


def pack_samples(samples: list[Processed]) -> bytes:
    arrays = {}
    for i, p in enumerate(samples):
        arrays[f"X{i}"] = p.X.astype(np.float32)
        arrays[f"F{i}"] = p.X_fixed.astype(np.float32)
        arrays[f"d{i}"] = np.array([p.duration_s])
    buf = io.BytesIO()
    np.savez_compressed(buf, **arrays)
    return buf.getvalue()


def unpack_samples(data: bytes) -> list[Processed]:
    out = []
    with np.load(io.BytesIO(data)) as z:
        n = len([k for k in z.files if k.startswith("X")])
        for i in range(n):
            out.append(
                Processed(
                    X=z[f"X{i}"].astype(np.float64),
                    X_fixed=z[f"F{i}"].astype(np.float64),
                    duration_s=float(z[f"d{i}"][0]),
                    peak_rotation=0.0,
                    direction_changes=0,
                    energy=0.0,
                    flat_channels=[],
                    sampling={},
                )
            )
    return out


def load_enrollment(db: Session, user_id: str) -> tuple[EnrollmentSession | None, list[Processed]]:
    es = db.get(EnrollmentSession, user_id)
    if es is None or es.ciphertext is None:
        return es, []
    return es, unpack_samples(unseal(es.wrapped_key, es.ciphertext, _aad(user_id, "enroll")))


def save_enrollment(db: Session, es: EnrollmentSession, samples: list[Processed]) -> None:
    es.wrapped_key, es.ciphertext = seal(pack_samples(samples), _aad(es.user_id, "enroll"))


# Templates --------------------------------------------------------------------------


def store_template(db: Session, user_id: str, t: Template, simulated: bool) -> SignatureTemplate:
    wrapped, ct = seal(t.to_bytes(), _aad(user_id, "template"))
    row = db.get(SignatureTemplate, user_id)
    if row is None:
        row = SignatureTemplate(user_id=user_id)
        db.add(row)
    row.wrapped_key, row.ciphertext = wrapped, ct
    row.n_reps = t.n
    row.method = "fused" if t.proto is not None else "dtw"
    row.simulated = simulated
    # A new template starts a new replay history.
    db.execute(delete(AttemptFingerprint).where(AttemptFingerprint.user_id == user_id))
    return row


def load_template(db: Session, user_id: str) -> Template | None:
    row = db.get(SignatureTemplate, user_id)
    if row is None:
        return None
    return Template.from_bytes(unseal(row.wrapped_key, row.ciphertext, _aad(user_id, "template")))


# Fingerprints of accepted attempts ------------------------------------------------


def prior_fingerprints(db: Session, user_id: str) -> list[np.ndarray]:
    rows = db.scalars(select(AttemptFingerprint).where(AttemptFingerprint.user_id == user_id)).all()
    return [np.frombuffer(unseal(r.fp[:60], r.fp[60:], _aad(user_id, "fp")), dtype=np.float16) for r in rows]


def add_fingerprint(db: Session, user_id: str, fp: np.ndarray) -> None:
    wrapped, ct = seal(fp.astype(np.float16).tobytes(), _aad(user_id, "fp"))
    assert len(wrapped) == 60  # 12-byte nonce + 32-byte key + 16-byte tag
    db.add(AttemptFingerprint(user_id=user_id, fp=wrapped + ct))
    rows = db.scalars(
        select(AttemptFingerprint.id)
        .where(AttemptFingerprint.user_id == user_id)
        .order_by(AttemptFingerprint.id.desc())
        .offset(MAX_FINGERPRINTS)
    ).all()
    if rows:
        db.execute(delete(AttemptFingerprint).where(AttemptFingerprint.id.in_(rows)))


def delete_signature(db: Session, user_id: str) -> None:
    for model in (SignatureTemplate, EnrollmentSession, AttemptFingerprint):
        db.execute(delete(model).where(model.user_id == user_id))
