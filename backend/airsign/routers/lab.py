"""Recording lab: collect labeled gestures on real phones for the evaluation (section 16.3).

Protected by AIRSIGN_LAB_CODE. Every recording gets instant diagnostics: is it
usable, what sampling rate and sensor noise the phone produced, and, once the
participant has enrolled, how close it is to their template.
"""

from __future__ import annotations

import hmac
import math
from typing import Literal

import numpy as np
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .. import evaluation
from ..auth import set_cookie
from ..config import get_settings
from ..db import get_db
from ..ratelimit import rate_limit
from ..models import LabParticipant, LabRecording, now
from ..security import sign_value, unsign_value
from ..signal import enroll, pipeline, replay, verify
from .customer import DeviceInfo

router = APIRouter(prefix="/api/lab")
LAB_COOKIE = "airsign_lab"

# The protocol targets from the report, per participant.
TARGETS = {"enroll": 7, "genuine": 15, "imitation": 10}


def fail(code: str, message: str, status: int = 400):
    raise HTTPException(status, {"code": code, "message": message})


def lab_enabled() -> str:
    code = get_settings().lab_code
    if not code:
        fail("lab_disabled", "The recording lab is turned off on this server.", 404)
    return code


def lab_access(request: Request) -> None:
    lab_enabled()
    if unsign_value(request.cookies.get(LAB_COOKIE), "lab") != "ok":
        fail("lab_login", "Enter the lab code first.", 401)


class LoginIn(BaseModel):
    code: str


@router.post("/login", dependencies=[Depends(rate_limit("lab_login"))])
def login(body: LoginIn, response: Response):
    code = lab_enabled()
    if not hmac.compare_digest(body.code.strip().encode(), code.encode()):
        fail("bad_code", "That lab code is not right.", 401)
    set_cookie(response, LAB_COOKIE, sign_value("ok", "lab"))
    return {"ok": True}


@router.get("/status")
def status(request: Request):
    if not get_settings().lab_code:
        return {"enabled": False, "authorized": False}
    return {"enabled": True, "authorized": unsign_value(request.cookies.get(LAB_COOKIE), "lab") == "ok"}


# Participants ---------------------------------------------------------------------


def _progress(db: Session) -> list[dict]:
    rows = db.execute(
        select(LabRecording.participant_id, LabRecording.kind, LabRecording.target_id, LabRecording.posture, func.count())
        .group_by(LabRecording.participant_id, LabRecording.kind, LabRecording.target_id, LabRecording.posture)
    ).all()
    out = []
    for p in db.scalars(select(LabParticipant).order_by(LabParticipant.consent_at)):
        mine = [r for r in rows if r[0] == p.id]
        counts = {k: sum(r[4] for r in mine if r[1] == k) for k in TARGETS}
        out.append(
            {
                "id": p.id,
                "alias": p.alias,
                "counts": counts,
                "targets": TARGETS,
                "postures": {pos: sum(r[4] for r in mine if r[1] == "genuine" and r[3] == pos) for pos in ("standing", "sitting", "walking")},
                "imitated": {t: sum(r[4] for r in mine if r[1] == "imitation" and r[2] == t) for t in {r[2] for r in mine if r[2]}},
            }
        )
    return out


@router.get("/participants", dependencies=[Depends(lab_access)])
def participants(db: Session = Depends(get_db)):
    return _progress(db)


class ParticipantIn(BaseModel):
    alias: str = Field(min_length=1, max_length=40)
    consent: bool


@router.post("/participants", dependencies=[Depends(lab_access)])
def add_participant(body: ParticipantIn, db: Session = Depends(get_db)):
    if not body.consent:
        fail("consent_required", "Participants must consent before recording.")
    n = db.scalar(select(func.count()).select_from(LabParticipant)) or 0
    pid = f"P{n + 1}"
    while db.get(LabParticipant, pid):
        n += 1
        pid = f"P{n + 1}"
    db.add(LabParticipant(id=pid, alias=body.alias.strip(), consent_at=now()))
    db.commit()
    return {"id": pid}


@router.delete("/participants/{pid}", dependencies=[Depends(lab_access)])
def delete_participant(pid: str, db: Session = Depends(get_db)):
    """Withdraw consent: removes the participant and every recording they made."""
    p = db.get(LabParticipant, pid)
    if p is None:
        fail("not_found", "Participant not found.", 404)
    db.query(LabRecording).filter(LabRecording.participant_id == pid).delete()
    db.delete(p)
    db.commit()
    return {"ok": True}


# Recordings -------------------------------------------------------------------------

_template_cache: dict[tuple[str, int], enroll.Template | str] = {}


def _template(db: Session, pid: str) -> enroll.Template | None:
    recs = db.scalars(
        select(LabRecording).where(LabRecording.participant_id == pid, LabRecording.kind == "enroll").order_by(LabRecording.id)
    ).all()
    if len(recs) < enroll.MIN_REPS:
        return None
    key = (pid, recs[-1].id)
    if key not in _template_cache:
        reps = []
        for r in recs:
            try:
                reps.append(pipeline.preprocess(r.samples, r.accel_includes_gravity))
            except pipeline.RecordingError:
                pass
        try:
            _template_cache[key], _ = enroll.build_template(reps)
        except enroll.EnrollmentError as e:
            _template_cache[key] = e.message
    t = _template_cache[key]
    return t if isinstance(t, enroll.Template) else None


class RecordingIn(BaseModel):
    participant_id: str
    kind: Literal["enroll", "genuine", "imitation"]
    target_id: str | None = None
    session: str | None = Field(default=None, max_length=12)
    posture: Literal["standing", "sitting", "walking"] | None = None
    device_label: str | None = Field(default=None, max_length=60)
    samples: list[list[float]] = Field(max_length=pipeline.MAX_ROWS)
    device: DeviceInfo = DeviceInfo()


def diagnose(samples, device: DeviceInfo) -> tuple[dict, object]:
    try:
        p = pipeline.preprocess(samples, device.accel_includes_gravity)
    except pipeline.RecordingError as e:
        return {"usable": False, "code": e.code, "message": e.message}, None
    hf = np.array(p.plausibility["hf_var"])
    d = {
        "usable": True,
        "duration_s": round(p.duration_s, 2),
        "direction_changes": p.direction_changes,
        "peak_rotation": round(p.peak_rotation, 2),
        "rate_hz": round(1000 / p.sampling["mean_dt_ms"], 1),
        "jitter_ms": round(p.sampling["std_dt_ms"], 3),
        "noise_log10": [round(math.log10(max(v, 1e-12)), 2) for v in hf],
        "quiet_noise_log10": round(math.log10(max(max(p.plausibility["quiet_hf_var"]), 1e-12)), 2),
        "lead_in_still": p.plausibility["lead_in_still"],
        "sensor_flags": replay.sensor_flags(p, device.nominal_hz),
    }
    try:
        enroll.check_sample(p)
        d["enroll_quality"] = "ok"
    except enroll.EnrollmentError as e:
        d["enroll_quality"] = e.message
    return d, p


@router.post("/recordings", dependencies=[Depends(lab_access)])
def add_recording(body: RecordingIn, db: Session = Depends(get_db)):
    if db.get(LabParticipant, body.participant_id) is None:
        fail("not_found", "Participant not found.", 404)
    if body.kind == "imitation":
        if not body.target_id or body.target_id == body.participant_id or db.get(LabParticipant, body.target_id) is None:
            fail("bad_target", "Choose whose signature is being imitated.")
    diag, processed = diagnose(body.samples, body.device)

    # Instant score against the relevant template, if one exists.
    owner = body.target_id if body.kind == "imitation" else body.participant_id
    if processed is not None and body.kind != "enroll":
        t = _template(db, owner)
        if t is not None:
            s = verify.score(t, processed)
            diag["score"] = {
                "z": round(s.z, 3),
                "tier1": verify.decide(s.z, 1),
                "tier2": verify.decide(s.z, 2),
            }

    rec = LabRecording(
        participant_id=body.participant_id,
        kind=body.kind,
        target_id=body.target_id if body.kind == "imitation" else None,
        session=body.session,
        posture=body.posture,
        device_label=body.device_label,
        platform=body.device.platform,
        nominal_hz=body.device.nominal_hz,
        accel_includes_gravity=body.device.accel_includes_gravity,
        samples=body.samples,
        diagnostics=diag,
    )
    db.add(rec)
    db.commit()
    return {"id": rec.id, "diagnostics": diag, "progress": _progress(db)}


@router.delete("/recordings/{rec_id}", dependencies=[Depends(lab_access)])
def delete_recording(rec_id: int, db: Session = Depends(get_db)):
    rec = db.get(LabRecording, rec_id)
    if rec is None:
        fail("not_found", "Recording not found.", 404)
    db.delete(rec)
    db.commit()
    return {"progress": _progress(db)}


def load_recs(db: Session) -> list[evaluation.Rec]:
    return [
        evaluation.Rec(
            id=r.id,
            participant=r.participant_id,
            kind=r.kind,
            samples=r.samples,
            target=r.target_id,
            platform=r.platform,
            device_label=r.device_label,
            session=r.session,
            posture=r.posture,
            accel_includes_gravity=r.accel_includes_gravity,
            nominal_hz=r.nominal_hz,
        )
        for r in db.scalars(select(LabRecording).order_by(LabRecording.id))
    ]


@router.get("/summary", dependencies=[Depends(lab_access), Depends(rate_limit("lab_summary"))])
def summary(db: Session = Depends(get_db)):
    scored, notes = evaluation.score_all(load_recs(db))
    return evaluation.summarize(scored, notes)


@router.get("/export", dependencies=[Depends(lab_access)])
def export(db: Session = Depends(get_db)):
    """All lab data as JSON, for offline evaluation and model training."""
    data = {
        "format": "airsign-lab-v1",
        "exported_at": now().isoformat(),
        "participants": [{"id": p.id, "consent_at": p.consent_at.isoformat()} for p in db.scalars(select(LabParticipant))],
        "recordings": [
            {
                "id": r.id,
                "participant": r.participant_id,
                "kind": r.kind,
                "target": r.target_id,
                "session": r.session,
                "posture": r.posture,
                "device_label": r.device_label,
                "platform": r.platform,
                "nominal_hz": r.nominal_hz,
                "accel_includes_gravity": r.accel_includes_gravity,
                "created_at": r.created_at.isoformat(),
                "samples": r.samples,
            }
            for r in db.scalars(select(LabRecording).order_by(LabRecording.id))
        ],
    }
    return JSONResponse(data, headers={"Content-Disposition": 'attachment; filename="airsign-lab.json"'})
