"""Payment intents, sign-off, PayPal webhooks and the mock PayPal approval."""

from __future__ import annotations

import json
import logging

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import intents as svc
from ..auth import customer
from ..db import SessionLocal, get_db
from ..models import Invoice, Merchant, PaymentIntent, SignoffAttempt
from ..paypal.gateway import MockGateway, PayPalError, get_gateway
from ..ratelimit import rate_limit
from ..signal import pipeline
from .customer import DeviceInfo
from .shops import checkin_pass_valid, invoice_view, shop_view

router = APIRouter(prefix="/api")
log = logging.getLogger("airsign.webhooks")


def fail(code: str, message: str, status: int = 400):
    raise HTTPException(status, {"code": code, "message": message})


def intent_view(db: Session, intent: PaymentIntent) -> dict:
    """Everything the sign-off screen shows comes from stored records, never from the phone."""
    invoice = db.get(Invoice, intent.invoice_id)
    merchant = db.get(Merchant, intent.merchant_id)
    attempts = db.scalars(
        select(SignoffAttempt).where(SignoffAttempt.intent_id == intent.id).order_by(SignoffAttempt.id)
    ).all()
    risk = intent.risk_details or {}
    return {
        "id": intent.id,
        "state": intent.state,
        "invoice": invoice_view(invoice),
        "shop": shop_view(merchant),
        "amount_cents": intent.amount_cents,
        "currency": intent.currency,
        "funding": intent.funding,
        "paypal": {"mode": get_gateway().mode, "order_id": intent.paypal_order_id, "capture_id": intent.capture_id, "webhook_confirmed": intent.webhook_confirmed},
        "risk": None
        if intent.risk_tier is None
        else {
            "tier": intent.risk_tier,
            "score": intent.risk_score,
            "explanation": risk.get("explanation"),
            "explanation_source": risk.get("explanation_source", "template"),
            "contributions": risk.get("contributions"),
            "ml_score": risk.get("ml_score"),
            "history_is_synthetic": risk.get("history_is_synthetic"),
        },
        "signatures_required": intent.signatures_required,
        "signatures_passed": intent.signatures_passed,
        "attempts_left": svc.attempts_left(intent),
        "location_verified": intent.location_verified,
        "confirmed_by": intent.shop_confirmed_by,
        "simulated": intent.simulated,
        "failure_reason": intent.failure_reason,
        "created_at": intent.created_at,
        "completed_at": intent.completed_at,
        "attempts": [
            {"decision": a.decision, "reason": a.reason, "flags": a.flags, "simulated": a.simulated, "created_at": a.created_at,
             "latency_ms": None if a.latency_ms is None else round(a.latency_ms, 1)}
            for a in attempts
        ],
    }


def run(fn, *args, **kwargs):
    try:
        return fn(*args, **kwargs)
    except svc.IntentError as e:
        fail(e.code, e.message, e.status)


class Location(BaseModel):
    lat: float = Field(ge=-90, le=90)
    lon: float = Field(ge=-180, le=180)
    accuracy: float | None = Field(default=None, gt=0)


class IntentIn(BaseModel):
    invoice_id: str
    confirmed_by: str = Field(pattern="^(popup|list|code|qr|sent)$")
    location: Location | None = None
    simulated_location: bool = False
    checkin_pass: str | None = None


@router.post("/intents", dependencies=[Depends(rate_limit("intent"))])
def create_intent(body: IntentIn, session=Depends(customer), db: Session = Depends(get_db)):
    user, device = session
    invoice = db.get(Invoice, body.invoice_id)
    if invoice is None:
        fail("not_found", "Invoice not found.", 404)
    verified = checkin_pass_valid(body.checkin_pass, user.id, invoice.merchant_id)
    intent, nonce = run(
        svc.create_intent,
        db,
        user,
        device,
        invoice,
        confirmed_by=body.confirmed_by,
        location=body.location.model_dump() if body.location else None,
        location_verified=verified,
        simulated=body.simulated_location,
    )
    return {**intent_view(db, intent), "nonce": nonce}


@router.get("/intents")
def my_intents(session=Depends(customer), db: Session = Depends(get_db)):
    user, _ = session
    rows = db.scalars(
        select(PaymentIntent).where(PaymentIntent.user_id == user.id).order_by(PaymentIntent.created_at.desc()).limit(20)
    ).all()
    return [intent_view(db, i) for i in rows]


@router.get("/intents/{intent_id}")
def get_intent(intent_id: str, session=Depends(customer), db: Session = Depends(get_db)):
    intent = run(svc.get_intent_for_user, db, intent_id, session[0])
    return intent_view(db, intent)


@router.post("/intents/{intent_id}/approved")
def approved(intent_id: str, session=Depends(customer), db: Session = Depends(get_db)):
    user, device = session
    intent = run(svc.get_intent_for_user, db, intent_id, user)
    nonce = run(svc.confirm_approval, db, intent, user, device)
    return {"nonce": nonce, "intent": intent_view(db, intent)}


@router.post("/intents/{intent_id}/nonce")
def renew_nonce(intent_id: str, session=Depends(customer), db: Session = Depends(get_db)):
    user, _ = session
    intent = run(svc.get_intent_for_user, db, intent_id, user)
    return {"nonce": run(svc.renew_nonce, db, intent, user)}


@router.post("/intents/{intent_id}/cancel")
def cancel(intent_id: str, session=Depends(customer), db: Session = Depends(get_db)):
    user, _ = session
    intent = run(svc.get_intent_for_user, db, intent_id, user)
    run(svc.cancel, db, intent, user)
    return intent_view(db, intent)


class SignoffIn(BaseModel):
    nonce: str = Field(min_length=10, max_length=64)
    device: DeviceInfo = DeviceInfo()
    samples: list[list[float]] = Field(max_length=pipeline.MAX_ROWS)
    simulated: bool = False


def _deliver_mock_webhook(intent_id: str) -> None:
    with SessionLocal() as db:
        intent = db.get(PaymentIntent, intent_id)
        if intent and intent.capture_id:
            svc.handle_webhook_event(db, svc.mock_capture_event(intent), verified=True, source="mock-paypal")


@router.post("/intents/{intent_id}/signoff", dependencies=[Depends(rate_limit("signoff"))])
def signoff(intent_id: str, body: SignoffIn, background: BackgroundTasks, session=Depends(customer), db: Session = Depends(get_db)):
    """The only endpoint that can lead to a PayPal capture."""
    user, _ = session
    result = run(svc.signoff, db, intent_id, user, body.nonce, body.model_dump())
    intent = db.get(PaymentIntent, intent_id)
    if isinstance(get_gateway(), MockGateway) and intent.capture_id:
        background.add_task(_deliver_mock_webhook, intent_id)
    return {**result, "intent": intent_view(db, intent)}


# PayPal webhooks --------------------------------------------------------------------


@router.post("/webhooks/paypal")
async def paypal_webhook(request: Request, db: Session = Depends(get_db)):
    raw = await request.body()
    try:
        event = json.loads(raw)
    except json.JSONDecodeError:
        fail("bad_json", "Body is not JSON.")
    gw = get_gateway()
    try:
        verified = gw.verify_webhook(dict(request.headers), raw)
    except Exception:  # verification service unreachable: record, do not trust
        log.exception("webhook verification failed")
        verified = False
    status = svc.handle_webhook_event(db, event, verified=verified, source="paypal")
    return {"status": status}


# Mock PayPal approval (only when sandbox credentials are not configured) ---------------


def _mock_gateway() -> MockGateway:
    gw = get_gateway()
    if not isinstance(gw, MockGateway):
        fail("not_mock", "The mock PayPal is only available when sandbox credentials are not set.", 404)
    return gw


def _own_order(db: Session, order_id: str, user) -> PaymentIntent:
    intent = db.scalar(select(PaymentIntent).where(PaymentIntent.paypal_order_id == order_id))
    if intent is None or intent.user_id != user.id:
        fail("not_found", "Order not found.", 404)
    return intent


@router.get("/mock-paypal/orders/{order_id}")
def mock_order(order_id: str, session=Depends(customer), db: Session = Depends(get_db)):
    gw = _mock_gateway()
    _own_order(db, order_id, session[0])
    o = gw.get_order(order_id)
    return {"id": o.id, "status": o.status, "amount": o.amount, "currency": o.currency}


@router.post("/mock-paypal/orders/{order_id}/approve")
def mock_approve(order_id: str, session=Depends(customer), db: Session = Depends(get_db)):
    gw = _mock_gateway()
    _own_order(db, order_id, session[0])
    try:
        gw.approve(order_id)
    except PayPalError as e:
        fail("mock_error", str(e))
    return {"status": "APPROVED"}
