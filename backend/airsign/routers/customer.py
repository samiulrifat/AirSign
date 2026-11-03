"""Customer session, enrollment and signature management."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .. import biometrics
from ..audit import audit
from ..auth import SESSION_COOKIE, customer, optional_customer, start_session
from ..config import get_settings
from ..db import get_db
from ..models import Device, EnrollmentSession, Invoice, PaymentHistory, SignatureTemplate, User, now
from ..wallets import active_wallet, forget_wallet
from ..paypal.gateway import get_gateway
from ..ratelimit import rate_limit
from ..seed import seed_history
from ..signal import enroll as enrollment
from ..ai import gemini
from ..signal import pipeline
from ..signal.siamese import get_model

router = APIRouter(prefix="/api")

TARGET_REPS = 7


def fail(code: str, message: str, status: int = 400):
    raise HTTPException(status, {"code": code, "message": message})


@router.get("/config")
def config():
    s = get_settings()
    gw = get_gateway()
    return {
        "paypal_mode": gw.mode,
        "paypal_client_id": gw.client_id,
        "allow_simulation": s.allow_simulation,
        "currency": s.currency,
        "neural_model": get_model() is not None,
        "gemini": gemini.enabled(),
    }


class SessionIn(BaseModel):
    display_name: str = Field(min_length=1, max_length=60)
    consent: bool
    demo_history: bool = True


@router.post("/session", dependencies=[Depends(rate_limit("session"))])
def create_session(body: SessionIn, response: Response, db: Session = Depends(get_db)):
    if not body.consent:
        fail("consent_required", "AirSign needs your consent to store an encrypted motion signature.")
    user = User(display_name=body.display_name.strip(), consent_at=now())
    db.add(user)
    db.flush()
    device = Device(user_id=user.id)
    db.add(device)
    audit(db, user.id, "user.created", None, device=device.id)
    db.commit()
    if body.demo_history:
        seed_history(db, user)
    start_session(response, user, device)
    return me_view(db, user, device)


@router.post("/session/logout")
def logout(response: Response):
    response.delete_cookie(SESSION_COOKIE)
    return {"ok": True}


def me_view(db: Session, user: User, device: Device) -> dict:
    tpl = db.get(SignatureTemplate, user.id)
    es = db.get(EnrollmentSession, user.id)
    wallet = active_wallet(db, user.id)
    synthetic = db.scalar(
        select(func.count()).select_from(PaymentHistory).where(PaymentHistory.user_id == user.id, PaymentHistory.synthetic.is_(True))
    )
    return {
        "user": {"id": user.id, "display_name": user.display_name},
        "device_id": device.id,
        "signature": None
        if tpl is None
        else {"repetitions": tpl.n_reps, "method": tpl.method, "simulated": tpl.simulated, "created_at": tpl.created_at},
        "enrollment": None if es is None else {"accepted": es.accepted, "rejected": es.rejected, "simulated": es.simulated},
        "synthetic_history": synthetic,
        "wallet": None if wallet is None else {"status": wallet.status, "email": wallet.payer_email},
    }


@router.get("/me")
def me(session=Depends(optional_customer), db: Session = Depends(get_db)):
    if session is None:
        return {"user": None}
    view = me_view(db, *session)
    db.commit()
    return view


@router.delete("/me/signature")
def delete_signature(session=Depends(customer), db: Session = Depends(get_db)):
    user, device = session
    biometrics.delete_signature(db, user.id)
    audit(db, user.id, "signature.deleted")
    db.commit()
    return me_view(db, user, device)


class HistoryIn(BaseModel):
    enabled: bool


@router.post("/me/demo-history")
def demo_history(body: HistoryIn, session=Depends(customer), db: Session = Depends(get_db)):
    user, device = session
    if body.enabled:
        seed_history(db, user)
    else:
        db.query(PaymentHistory).filter(PaymentHistory.user_id == user.id, PaymentHistory.synthetic.is_(True)).delete()
        db.commit()
    return me_view(db, user, device)


@router.delete("/me")
def delete_me(response: Response, session=Depends(customer), db: Session = Depends(get_db)):
    user, _ = session
    biometrics.delete_signature(db, user.id)
    forget_wallet(db, user.id)
    db.query(Invoice).filter(Invoice.customer_id == user.id).update({Invoice.customer_id: None})
    audit(db, user.id, "user.deleted")
    db.delete(user)
    db.commit()
    response.delete_cookie(SESSION_COOKIE)
    return {"ok": True}


# Enrollment ----------------------------------------------------------------------


class DeviceInfo(BaseModel):
    platform: str | None = None
    nominal_hz: float | None = None
    accel_includes_gravity: bool = False


class SampleIn(BaseModel):
    samples: list[list[float]] = Field(max_length=pipeline.MAX_ROWS)
    device: DeviceInfo = DeviceInfo()
    simulated: bool = False


class EnrollStartIn(BaseModel):
    simulated: bool = False


@router.post("/enroll/start", dependencies=[Depends(rate_limit("enroll"))])
def enroll_start(body: EnrollStartIn, session=Depends(customer), db: Session = Depends(get_db)):
    user, _ = session
    if body.simulated and not get_settings().allow_simulation:
        fail("simulation_disabled", "Simulation is turned off on this server.", 403)
    es = db.get(EnrollmentSession, user.id)
    if es is not None:
        db.delete(es)
        db.flush()
    es = EnrollmentSession(user_id=user.id, wrapped_key=b"", simulated=body.simulated)
    db.add(es)
    audit(db, user.id, "enroll.started", None, simulated=body.simulated)
    db.commit()
    return {"accepted": 0, "target": TARGET_REPS, "minimum": enrollment.MIN_REPS}


@router.post("/enroll/sample", dependencies=[Depends(rate_limit("enroll"))])
def enroll_sample(body: SampleIn, session=Depends(customer), db: Session = Depends(get_db)):
    user, _ = session
    es, samples = biometrics.load_enrollment(db, user.id)
    if es is None:
        fail("no_enrollment", "Start enrollment first.", 409)
    if body.simulated != es.simulated:
        fail("mixed_sources", "Do not mix simulated and real recordings in one enrollment.", 409)
    if len(samples) >= enrollment.MAX_REPS:
        fail("enough", "You already have enough repetitions. Finish enrollment.", 409)
    try:
        p = pipeline.preprocess(body.samples, body.device.accel_includes_gravity)
        enrollment.check_sample(p)
    except (pipeline.RecordingError, enrollment.EnrollmentError) as e:
        es.rejected += 1
        db.commit()
        return {"accepted": False, "code": e.code, "reason": e.message, "count": len(samples), "target": TARGET_REPS}
    samples.append(p)
    biometrics.save_enrollment(db, es, samples)
    es.accepted = len(samples)
    db.commit()
    return {
        "accepted": True,
        "count": len(samples),
        "target": TARGET_REPS,
        "quality": {"duration_s": round(p.duration_s, 2), "direction_changes": p.direction_changes, "peak_rotation": round(p.peak_rotation, 2)},
    }


@router.post("/enroll/finish")
def enroll_finish(session=Depends(customer), db: Session = Depends(get_db)):
    user, device = session
    es, samples = biometrics.load_enrollment(db, user.id)
    if es is None:
        fail("no_enrollment", "Start enrollment first.", 409)
    try:
        template, report = enrollment.build_template(samples, get_model())
    except enrollment.EnrollmentError as e:
        fail(e.code, e.message, 422)
    biometrics.store_template(db, user.id, template, simulated=es.simulated)
    db.delete(es)
    audit(db, user.id, "enroll.finished", None, kept=report["kept"], dropped=report["dropped_inconsistent"], simulated=es.simulated)
    db.commit()
    return {"ok": True, "report": {"kept": report["kept"], "dropped": report["dropped_inconsistent"]}, "me": me_view(db, user, device)}
