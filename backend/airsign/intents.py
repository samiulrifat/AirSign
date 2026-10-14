"""Payment intents: the state machine and the single path to money (sections 4.2, 9.1, 12).

Design rule: `_capture` is the only function that calls PayPal capture. It is
called only from `signoff`, and only after the intent reached VERIFIED inside
the same request. Tests check that no other code path references the capture.
"""

from __future__ import annotations

import time
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from . import biometrics
from .audit import audit
from .config import get_settings
from .models import Device, Invoice, Merchant, PaymentHistory, PaymentIntent, SignoffAttempt, User, WebhookEvent, now
from .paypal.gateway import PayPalError, get_gateway
from .risk import PastPayment, RiskInput, assess
from .security import hash_nonce, new_nonce
from .shops import haversine_m
from .signal import pipeline, replay, verify
from .signal.fingerprint import fingerprint
from .signal.siamese import get_model

INTENT_TTL = timedelta(minutes=10)
NONCE_TTL = timedelta(seconds=120)
MAX_ATTEMPTS = 3
SENSOR_FLAGS_TO_REJECT = 2

TERMINAL = {"COMPLETED", "LOCKED", "EXPIRED", "CANCELLED", "FAILED"}
PRE_SIGNOFF = {"CREATED", "PROPOSED", "APPROVED", "ARMED"}
TRANSITIONS = {
    "CREATED": {"PROPOSED", "FAILED", "CANCELLED", "EXPIRED"},
    "PROPOSED": {"APPROVED", "CANCELLED", "EXPIRED"},
    "APPROVED": {"ARMED", "CANCELLED", "EXPIRED"},
    "ARMED": {"SIGNING", "CANCELLED", "EXPIRED"},
    "SIGNING": {"VERIFIED", "ARMED", "PARTIAL", "LOCKED"},
    "PARTIAL": {"ARMED"},
    "VERIFIED": {"CAPTURING"},
    "CAPTURING": {"COMPLETED", "FAILED"},
    # Reconciliation only: a verified PayPal webhook proves a capture we lost track of.
    "FAILED": {"COMPLETED"},
}


class IntentError(Exception):
    def __init__(self, code: str, message: str, status: int = 400):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status


def local_now() -> datetime:
    return now().astimezone(ZoneInfo(get_settings().timezone))


def money(cents: int) -> str:
    return f"{cents // 100}.{cents % 100:02d}"


def transition(db: Session, intent: PaymentIntent, to: str, actor: str, **details) -> None:
    if to not in TRANSITIONS.get(intent.state, set()):
        raise IntentError("bad_transition", f"Cannot move a payment from {intent.state} to {to}.", 409)
    audit(db, actor, "intent.transition", intent.id, frm=intent.state, to=to, **details)
    intent.state = to
    intent.updated_at = now()


def expire_if_needed(db: Session, intent: PaymentIntent) -> None:
    if intent.state in PRE_SIGNOFF and now() > intent.expires_at:
        transition(db, intent, "EXPIRED", "system")
        db.commit()


def get_intent_for_user(db: Session, intent_id: str, user: User) -> PaymentIntent:
    intent = db.get(PaymentIntent, intent_id)
    if intent is None or intent.user_id != user.id:
        raise IntentError("not_found", "Payment not found.", 404)
    expire_if_needed(db, intent)
    return intent


# Creation and PayPal approval -------------------------------------------------------


def create_intent(
    db: Session,
    user: User,
    device: Device,
    invoice: Invoice,
    *,
    confirmed_by: str,
    location: dict | None,
    location_verified: bool,
    simulated: bool,
) -> PaymentIntent:
    if biometrics.load_template(db, user.id) is None:
        raise IntentError("not_enrolled", "Set up your air signature before paying.", 409)
    if invoice.status != "OPEN":
        raise IntentError("invoice_closed", "This invoice is no longer open.", 409)

    active = db.scalars(
        select(PaymentIntent).where(PaymentIntent.invoice_id == invoice.id, PaymentIntent.state.not_in(TERMINAL))
    ).all()
    for other in active:
        expire_if_needed(db, other)
        if other.state in TERMINAL:
            continue
        if other.user_id != user.id:
            raise IntentError("invoice_busy", "Someone else is already paying this invoice.", 409)
        if other.state in PRE_SIGNOFF:
            transition(db, other, "CANCELLED", user.id, reason="replaced")
        else:
            raise IntentError("in_progress", "This payment is already being completed.", 409)

    merchant = db.get(Merchant, invoice.merchant_id)
    distance = accuracy = None
    if location:
        distance = haversine_m(location["lat"], location["lon"], merchant.lat, merchant.lon)
        accuracy = location.get("accuracy")

    intent = PaymentIntent(
        invoice_id=invoice.id,
        user_id=user.id,
        device_id=device.id,
        merchant_id=merchant.id,
        amount_cents=invoice.amount_cents,
        currency=invoice.currency,
        state="CREATED",
        location_verified=location_verified,
        location_distance_m=distance,
        location_accuracy_m=accuracy,
        shop_confirmed_by=confirmed_by,
        simulated=simulated,
        expires_at=now() + INTENT_TTL,
    )
    db.add(intent)
    db.flush()
    audit(db, user.id, "intent.created", intent.id, invoice=invoice.id, shop=merchant.id, confirmed_by=confirmed_by)
    db.commit()  # never hold a transaction open across a PayPal call

    try:
        order = get_gateway().create_order(
            reference_id=invoice.id,
            invoice_id=intent.id,
            amount=money(invoice.amount_cents),
            currency=invoice.currency,
            description=f"{merchant.name}, invoice {invoice.id}",
        )
    except PayPalError as e:
        transition(db, intent, "FAILED", "paypal", error=str(e))
        intent.failure_reason = "PayPal could not create the order."
        db.commit()
        raise IntentError("paypal_error", "PayPal could not create the order. Please try again.", 502)
    intent.paypal_order_id = order.id
    transition(db, intent, "PROPOSED", "paypal", order_id=order.id)
    db.commit()
    return intent


def confirm_approval(db: Session, intent: PaymentIntent, user: User, device: Device) -> str | None:
    """The app reports approval. We check it with PayPal, score the risk and arm a nonce."""
    if intent.state in ("APPROVED", "ARMED"):
        # Idempotent: hand out a fresh nonce rather than failing a retried request.
        return renew_nonce(db, intent, user)
    if intent.state != "PROPOSED":
        raise IntentError("bad_state", "This payment cannot be approved now.", 409)

    try:
        order = get_gateway().get_order(intent.paypal_order_id)
    except PayPalError:
        raise IntentError("paypal_error", "Could not confirm the approval with PayPal.", 502)
    if order.status != "APPROVED":
        raise IntentError("not_approved", "PayPal has not approved this order yet.", 409)
    if order.amount != money(intent.amount_cents) or order.currency != intent.currency:
        audit(db, "system", "intent.amount_mismatch", intent.id, paypal=order.amount, expected=money(intent.amount_cents))
        transition(db, intent, "CANCELLED", "system", reason="amount_mismatch")
        db.commit()
        raise IntentError("amount_mismatch", "The PayPal order does not match the invoice.", 409)

    transition(db, intent, "APPROVED", "paypal")
    _score_risk(db, intent, user, device)
    transition(db, intent, "ARMED", "system", tier=intent.risk_tier)
    nonce = _issue_nonce(intent)
    db.commit()
    return nonce


def _score_risk(db: Session, intent: PaymentIntent, user: User, device: Device) -> None:
    tz = ZoneInfo(get_settings().timezone)
    rows = db.scalars(
        select(PaymentHistory).where(PaymentHistory.user_id == user.id).order_by(PaymentHistory.at)
    ).all()
    history = [PastPayment(r.merchant_id, r.amount_cents, r.at.astimezone(tz)) for r in rows]
    real_elsewhere = any(not r.synthetic for r in rows)
    device_paid_before = db.scalar(
        select(PaymentIntent.id).where(PaymentIntent.device_id == device.id, PaymentIntent.state == "COMPLETED").limit(1)
    )
    merchant = db.get(Merchant, intent.merchant_id)
    result = assess(
        RiskInput(
            amount_cents=intent.amount_cents,
            merchant_id=intent.merchant_id,
            now=local_now(),
            history=history,
            location_verified=intent.location_verified,
            distance_m=intent.location_distance_m,
            geofence_m=merchant.radius_m,
            accuracy_m=intent.location_accuracy_m,
            new_device=real_elsewhere and device_paid_before is None,
        )
    )
    intent.risk_score = result.score
    intent.risk_tier = result.tier
    intent.signatures_required = 2 if result.tier == 3 else 1
    intent.risk_details = {
        "rule_score": result.rule_score,
        "ml_score": result.ml_score,
        "features": result.features,
        "contributions": result.contributions,
        "explanation": result.explanation,
        "history_is_synthetic": bool(rows) and not real_elsewhere,
    }
    audit(db, "risk", "intent.risk", intent.id, score=result.score, tier=result.tier, reasons=result.reasons)


def _issue_nonce(intent: PaymentIntent) -> str:
    nonce = new_nonce()
    intent.nonce_hash = hash_nonce(nonce, intent.id)
    intent.nonce_expires_at = now() + NONCE_TTL
    return nonce


def renew_nonce(db: Session, intent: PaymentIntent, user: User) -> str:
    """A fresh nonce while ARMED, for example when the old one expired. Not an attempt."""
    if intent.state != "ARMED":
        raise IntentError("bad_state", "This payment is not waiting for a signature.", 409)
    nonce = _issue_nonce(intent)
    audit(db, user.id, "intent.nonce_renewed", intent.id)
    db.commit()
    return nonce


def cancel(db: Session, intent: PaymentIntent, user: User) -> None:
    if intent.state not in PRE_SIGNOFF:
        raise IntentError("bad_state", "This payment can no longer be cancelled.", 409)
    transition(db, intent, "CANCELLED", user.id)
    db.commit()


# Sign-off: the only path to capture ------------------------------------------------


MESSAGES = {
    "accept": "Signature matched.",
    "step_up": "Signature matched, but only just. Please sign once more to be sure.",
    "partial": "First signature accepted. This payment needs a second one.",
    "reject": "Signature did not match. {left} attempt{s} left.",
    "locked": "Three signatures did not match, so this payment is locked. Your account is not affected. Ask the cashier to start again.",
    "replay": "This looks like a recording of an earlier signature, not a live one. The payment is locked.",
    "synthetic": "The motion data does not look like it came from a phone's sensors.",
}


def _consume_nonce(db: Session, intent_id: str, user: User, nonce: str) -> PaymentIntent:
    """Atomically move ARMED -> SIGNING if the nonce is live and unused."""
    stmt = (
        update(PaymentIntent)
        .where(
            PaymentIntent.id == intent_id,
            PaymentIntent.user_id == user.id,
            PaymentIntent.state == "ARMED",
            PaymentIntent.nonce_hash == hash_nonce(nonce, intent_id),
            PaymentIntent.nonce_expires_at > now(),
            PaymentIntent.attempts < MAX_ATTEMPTS,
        )
        .values(state="SIGNING", nonce_hash=None, attempts=PaymentIntent.attempts + 1, updated_at=now())
    )
    if db.execute(stmt).rowcount != 1:
        db.rollback()
        intent = get_intent_for_user(db, intent_id, user)
        if intent.state != "ARMED":
            raise IntentError("bad_state", "This payment is not waiting for a signature.", 409)
        if intent.nonce_expires_at and intent.nonce_expires_at <= now():
            raise IntentError("nonce_expired", "The sign-off window expired. Request a new one.", 410)
        raise IntentError("nonce_invalid", "This sign-off request is not valid. It may have been used already.", 403)
    audit(db, user.id, "intent.transition", intent_id, frm="ARMED", to="SIGNING")
    db.commit()
    intent = db.get(PaymentIntent, intent_id)
    db.refresh(intent)
    return intent


def signoff(db: Session, intent_id: str, user: User, nonce: str, payload: dict) -> dict:
    started = time.perf_counter()
    if payload.get("simulated") and not get_settings().allow_simulation:
        raise IntentError("simulation_disabled", "Simulated signatures are turned off on this server.", 403)
    intent = _consume_nonce(db, intent_id, user, nonce)
    tier = intent.risk_tier or 1
    simulated = bool(payload.get("simulated"))
    if simulated:
        intent.simulated = True

    template = biometrics.load_template(db, user.id)
    device = payload.get("device") or {}
    try:
        cand = pipeline.preprocess(payload["samples"], bool(device.get("accel_includes_gravity")))
    except pipeline.RecordingError as e:
        # A bad recording is not a failed signature: give the attempt back.
        intent.attempts -= 1
        transition(db, intent, "ARMED", "verifier", unusable=e.code)
        nonce = _issue_nonce(intent)
        db.add(SignoffAttempt(intent_id=intent.id, tier=tier, decision="UNUSABLE", flags=[e.code], reason=e.message, simulated=simulated))
        db.commit()
        return _result(intent, "UNUSABLE", e.message, nonce=nonce)

    model = get_model()
    scores = verify.score(template, cand, model)
    flags = replay.replay_flags(template, cand, scores.min_dtw, biometrics.prior_fingerprints(db, user.id))
    sensor = replay.sensor_flags(cand, device.get("nominal_hz"))
    decision = verify.decide(scores.z, tier)

    if flags:
        decision, reason, to = "REJECT", MESSAGES["replay"], "LOCKED"
    elif len(sensor) >= SENSOR_FLAGS_TO_REJECT:
        decision, reason, to = "REJECT", MESSAGES["synthetic"], None
    elif decision == verify.REJECT:
        reason, to = None, None
    else:
        intent.signatures_passed += 1
        if decision == verify.STEP_UP and intent.signatures_required < 2:
            intent.signatures_required = 2
        if intent.signatures_passed >= intent.signatures_required:
            reason, to = MESSAGES["accept"], "VERIFIED"
        else:
            reason = MESSAGES["step_up"] if decision == verify.STEP_UP else MESSAGES["partial"]
            to = "PARTIAL"
        biometrics.add_fingerprint(db, user.id, fingerprint(cand.X_fixed))

    if to is None:  # a rejection that does not lock by itself
        left = MAX_ATTEMPTS - intent.attempts
        if left <= 0:
            to, reason = "LOCKED", MESSAGES["locked"]
        else:
            to = "ARMED"
            reason = (reason + " " if reason else "") + MESSAGES["reject"].format(left=left, s="" if left == 1 else "s")

    k = verify.TIER_K[tier]
    db.add(
        SignoffAttempt(
            intent_id=intent.id,
            tier=tier,
            scores={**scores.as_dict(), "k": k},
            decision=decision,
            flags=flags + sensor,
            reason=reason,
            simulated=simulated,
            latency_ms=(time.perf_counter() - started) * 1000,
        )
    )
    transition(db, intent, to, "verifier", decision=decision, flags=flags + sensor, z=round(scores.z, 3))

    new_nonce_value = None
    if to == "PARTIAL":
        transition(db, intent, "ARMED", "system")
        new_nonce_value = _issue_nonce(intent)
    elif to == "ARMED":
        new_nonce_value = _issue_nonce(intent)
    db.commit()

    if to == "VERIFIED":
        _capture(db, intent)

    return _result(intent, decision, reason, nonce=new_nonce_value, scores=scores, k=k, flags=flags + sensor)


def _capture(db: Session, intent: PaymentIntent) -> None:
    """The only call site of PayPal capture. Requires VERIFIED."""
    if intent.state != "VERIFIED":
        raise IntentError("bad_state", "Only a verified payment can be captured.", 409)
    intent.capture_request_id = f"airsign-capture-{intent.id}"
    transition(db, intent, "CAPTURING", "system")
    db.commit()
    try:
        result = get_gateway().capture_order(intent.paypal_order_id, intent.capture_request_id)
    except PayPalError as e:
        intent.failure_reason = "PayPal could not complete the payment."
        transition(db, intent, "FAILED", "paypal", error=str(e))
        db.commit()
        return
    intent.capture_id = result.capture_id
    if result.capture_status == "COMPLETED":
        complete(db, intent, "capture_response")
    elif result.capture_status == "PENDING":
        audit(db, "paypal", "intent.capture_pending", intent.id)
    else:
        intent.failure_reason = f"PayPal returned {result.capture_status}."
        transition(db, intent, "FAILED", "paypal", capture_status=result.capture_status)
    db.commit()


def complete(db: Session, intent: PaymentIntent, source: str) -> None:
    transition(db, intent, "COMPLETED", "paypal", source=source)
    intent.completed_at = now()
    invoice = db.get(Invoice, intent.invoice_id)
    invoice.status = "PAID"
    invoice.paid_at = now()
    db.add(PaymentHistory(user_id=intent.user_id, merchant_id=intent.merchant_id, amount_cents=intent.amount_cents, at=now()))


def _result(intent: PaymentIntent, decision: str, reason: str, *, nonce=None, scores=None, k=None, flags=None) -> dict:
    out = {
        "decision": decision,
        "reason": reason,
        "state": intent.state,
        "attempts_left": MAX_ATTEMPTS - intent.attempts,
        "signatures_passed": intent.signatures_passed,
        "signatures_required": intent.signatures_required,
        "nonce": nonce,
        "flags": flags or [],
    }
    if scores is not None:
        # Shown in the demo so judges can see the model at work. A production
        # system would not return scores, since they help an attacker tune attempts.
        out["match"] = {"z": round(scores.z, 3), "k": k, "method": scores.method}
    return out


# Webhooks ---------------------------------------------------------------------------


def handle_webhook_event(db: Session, event: dict, verified: bool, source: str) -> str:
    event_id = event.get("id") or ""
    if not event_id:
        return "ignored"
    if db.get(WebhookEvent, event_id):
        return "duplicate"
    etype = event.get("event_type", "")
    resource = event.get("resource") or {}
    order_id = ((resource.get("supplementary_data") or {}).get("related_ids") or {}).get("order_id")
    db.add(WebhookEvent(id=event_id, event_type=etype, verified=verified, resource_id=resource.get("id"), order_id=order_id))
    audit(db, source, "webhook.received", None, event_type=etype, verified=verified, order_id=order_id)
    if not verified:
        db.commit()
        return "unverified"

    intent = db.scalar(select(PaymentIntent).where(PaymentIntent.paypal_order_id == order_id)) if order_id else None
    if intent is None:
        db.commit()
        return "no_intent"

    if etype == "PAYMENT.CAPTURE.COMPLETED":
        amount = resource.get("amount") or {}
        if amount.get("value") != money(intent.amount_cents) or amount.get("currency_code") != intent.currency:
            audit(db, source, "webhook.amount_mismatch", intent.id, amount=amount)
            db.commit()
            return "amount_mismatch"
        intent.webhook_confirmed = True
        intent.capture_id = intent.capture_id or resource.get("id")
        if intent.state in ("CAPTURING", "FAILED"):
            complete(db, intent, f"webhook:{source}")
    elif etype in ("PAYMENT.CAPTURE.DENIED", "PAYMENT.CAPTURE.DECLINED"):
        if intent.state == "CAPTURING":
            intent.failure_reason = "PayPal denied the payment."
            transition(db, intent, "FAILED", source, event_type=etype)
    db.commit()
    return "processed"


def mock_capture_event(intent: PaymentIntent) -> dict:
    """The event PayPal would send after a capture, for the mock gateway."""
    return {
        "id": f"WH-MOCK-{intent.capture_id}",
        "event_type": "PAYMENT.CAPTURE.COMPLETED",
        "resource": {
            "id": intent.capture_id,
            "status": "COMPLETED",
            "amount": {"value": money(intent.amount_cents), "currency_code": intent.currency},
            "supplementary_data": {"related_ids": {"order_id": intent.paypal_order_id}},
        },
    }
