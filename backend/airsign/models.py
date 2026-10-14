"""Database tables (technical report, section 14.1, plus the tables the prototype needs)."""

from __future__ import annotations

import secrets
from datetime import datetime, timezone

from sqlalchemy import JSON, Boolean, DateTime, Float, ForeignKey, Integer, LargeBinary, String, Text
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import TypeDecorator

from .db import Base


def now() -> datetime:
    return datetime.now(timezone.utc)


def new_id(prefix: str) -> str:
    return f"{prefix}_{secrets.token_hex(8)}"


class UTCDateTime(TypeDecorator):
    """Stored as naive UTC, returned as timezone-aware UTC."""

    impl = DateTime
    cache_ok = True

    def process_bind_param(self, value, dialect):
        if value is not None and value.tzinfo is not None:
            value = value.astimezone(timezone.utc).replace(tzinfo=None)
        return value

    def process_result_value(self, value, dialect):
        if value is not None and value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value


class User(Base):
    __tablename__ = "users"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: new_id("usr"))
    display_name: Mapped[str] = mapped_column(String(60))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=now)
    consent_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    is_demo: Mapped[bool] = mapped_column(Boolean, default=False)


class Device(Base):
    __tablename__ = "devices"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: new_id("dev"))
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=now)
    last_seen_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=now)


class Building(Base):
    __tablename__ = "buildings"
    id: Mapped[str] = mapped_column(String, primary_key=True)
    name: Mapped[str] = mapped_column(String(120))
    lat: Mapped[float] = mapped_column(Float)
    lon: Mapped[float] = mapped_column(Float)
    radius_m: Mapped[float] = mapped_column(Float, default=40.0)


class Merchant(Base):
    __tablename__ = "merchants"
    id: Mapped[str] = mapped_column(String, primary_key=True)
    name: Mapped[str] = mapped_column(String(120))
    building_id: Mapped[str] = mapped_column(ForeignKey("buildings.id"), index=True)
    floor: Mapped[int] = mapped_column(Integer)
    lat: Mapped[float] = mapped_column(Float)
    lon: Mapped[float] = mapped_column(Float)
    radius_m: Mapped[float] = mapped_column(Float, default=15.0)
    open_hour: Mapped[int] = mapped_column(Integer, default=9)  # local hour, inclusive
    close_hour: Mapped[int] = mapped_column(Integer, default=21)  # exclusive
    category: Mapped[str] = mapped_column(String(40), default="shop")
    # HMAC key for check-in tokens. A production system keeps this in a key service.
    secret: Mapped[str] = mapped_column(String, default=lambda: secrets.token_hex(32))
    pin_hash: Mapped[str] = mapped_column(String)


class Invoice(Base):
    __tablename__ = "invoices"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: new_id("inv"))
    merchant_id: Mapped[str] = mapped_column(ForeignKey("merchants.id"), index=True)
    amount_cents: Mapped[int] = mapped_column(Integer)
    currency: Mapped[str] = mapped_column(String(3))
    items: Mapped[list] = mapped_column(JSON, default=list)
    status: Mapped[str] = mapped_column(String(12), default="OPEN", index=True)  # OPEN, PAID, CANCELLED
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=now)
    paid_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)


class SignatureTemplate(Base):
    __tablename__ = "signature_templates"
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    wrapped_key: Mapped[bytes] = mapped_column(LargeBinary)
    ciphertext: Mapped[bytes] = mapped_column(LargeBinary)
    n_reps: Mapped[int] = mapped_column(Integer)
    method: Mapped[str] = mapped_column(String(10))  # dtw or fused
    simulated: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=now)


class EnrollmentSession(Base):
    """Preprocessed repetitions held between /enroll/sample calls. Deleted at finish."""

    __tablename__ = "enrollment_sessions"
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    wrapped_key: Mapped[bytes] = mapped_column(LargeBinary)
    ciphertext: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)
    accepted: Mapped[int] = mapped_column(Integer, default=0)
    rejected: Mapped[int] = mapped_column(Integer, default=0)
    simulated: Mapped[bool] = mapped_column(Boolean, default=False)
    started_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=now)


class AttemptFingerprint(Base):
    """Compact fingerprints of accepted sign-offs, for replay detection. Not raw data."""

    __tablename__ = "attempt_fingerprints"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    fp: Mapped[bytes] = mapped_column(LargeBinary)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=now)


class PaymentIntent(Base):
    __tablename__ = "payment_intents"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: new_id("pi"))
    invoice_id: Mapped[str] = mapped_column(ForeignKey("invoices.id"), index=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    device_id: Mapped[str | None] = mapped_column(String, nullable=True)
    merchant_id: Mapped[str] = mapped_column(ForeignKey("merchants.id"), index=True)
    amount_cents: Mapped[int] = mapped_column(Integer)
    currency: Mapped[str] = mapped_column(String(3))
    state: Mapped[str] = mapped_column(String(12), index=True)
    paypal_order_id: Mapped[str | None] = mapped_column(String, nullable=True, index=True)
    risk_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    risk_tier: Mapped[int | None] = mapped_column(Integer, nullable=True)
    risk_details: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    signatures_required: Mapped[int] = mapped_column(Integer, default=1)
    signatures_passed: Mapped[int] = mapped_column(Integer, default=0)
    nonce_hash: Mapped[str | None] = mapped_column(String, nullable=True)
    nonce_expires_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    location_verified: Mapped[bool] = mapped_column(Boolean, default=False)
    location_distance_m: Mapped[float | None] = mapped_column(Float, nullable=True)
    location_accuracy_m: Mapped[float | None] = mapped_column(Float, nullable=True)
    shop_confirmed_by: Mapped[str] = mapped_column(String(12), default="popup")  # popup, list, code, qr
    simulated: Mapped[bool] = mapped_column(Boolean, default=False)
    capture_request_id: Mapped[str | None] = mapped_column(String, nullable=True)
    capture_id: Mapped[str | None] = mapped_column(String, nullable=True)
    webhook_confirmed: Mapped[bool] = mapped_column(Boolean, default=False)
    failure_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=now)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=now, onupdate=now)
    expires_at: Mapped[datetime] = mapped_column(UTCDateTime())
    completed_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)


class SignoffAttempt(Base):
    __tablename__ = "signoff_attempts"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    intent_id: Mapped[str] = mapped_column(ForeignKey("payment_intents.id", ondelete="CASCADE"), index=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=now)
    tier: Mapped[int] = mapped_column(Integer)
    scores: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    decision: Mapped[str] = mapped_column(String(12))
    flags: Mapped[list] = mapped_column(JSON, default=list)
    reason: Mapped[str] = mapped_column(Text)
    simulated: Mapped[bool] = mapped_column(Boolean, default=False)
    latency_ms: Mapped[float | None] = mapped_column(Float, nullable=True)


class CheckinToken(Base):
    __tablename__ = "checkin_tokens"
    jti: Mapped[str] = mapped_column(String, primary_key=True)
    merchant_id: Mapped[str] = mapped_column(ForeignKey("merchants.id"), index=True)
    invoice_id: Mapped[str | None] = mapped_column(ForeignKey("invoices.id"), nullable=True)
    code: Mapped[str] = mapped_column(String(6), index=True)
    expires_at: Mapped[datetime] = mapped_column(UTCDateTime())
    used_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)


class AuditLog(Base):
    __tablename__ = "audit_log"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    ts: Mapped[datetime] = mapped_column(UTCDateTime(), default=now, index=True)
    actor: Mapped[str] = mapped_column(String)
    event: Mapped[str] = mapped_column(String(60))
    intent_id: Mapped[str | None] = mapped_column(String, nullable=True, index=True)
    details: Mapped[dict] = mapped_column(JSON, default=dict)


class WebhookEvent(Base):
    __tablename__ = "webhook_events"
    id: Mapped[str] = mapped_column(String, primary_key=True)
    event_type: Mapped[str] = mapped_column(String(80))
    received_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=now)
    verified: Mapped[bool] = mapped_column(Boolean)
    resource_id: Mapped[str | None] = mapped_column(String, nullable=True)
    order_id: Mapped[str | None] = mapped_column(String, nullable=True)


class MockOrder(Base):
    """Orders for the built-in PayPal stand-in used until sandbox credentials are set."""

    __tablename__ = "mock_orders"
    id: Mapped[str] = mapped_column(String, primary_key=True)
    reference_id: Mapped[str] = mapped_column(String)
    amount: Mapped[str] = mapped_column(String)
    currency: Mapped[str] = mapped_column(String(3))
    status: Mapped[str] = mapped_column(String(20))
    capture_id: Mapped[str | None] = mapped_column(String, nullable=True)
    capture_request_ids: Mapped[list] = mapped_column(JSON, default=list)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=now)


class PaymentHistory(Base):
    """Completed payments used by the risk engine, including the seeded synthetic history."""

    __tablename__ = "payment_history"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    merchant_id: Mapped[str] = mapped_column(String)
    amount_cents: Mapped[int] = mapped_column(Integer)
    at: Mapped[datetime] = mapped_column(UTCDateTime())
    synthetic: Mapped[bool] = mapped_column(Boolean, default=False)


class LabParticipant(Base):
    """A consenting participant in the evaluation study. Pseudonymous: P1, P2, ..."""

    __tablename__ = "lab_participants"
    id: Mapped[str] = mapped_column(String, primary_key=True)
    alias: Mapped[str] = mapped_column(String(40))
    consent_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=now)


class LabRecording(Base):
    """Raw recordings kept, with consent, only for evaluating and training the verifier.

    Separate from the payment system: nothing here is used to authorise a payment.
    """

    __tablename__ = "lab_recordings"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    participant_id: Mapped[str] = mapped_column(ForeignKey("lab_participants.id", ondelete="CASCADE"), index=True)
    kind: Mapped[str] = mapped_column(String(12))  # enroll, genuine, imitation
    target_id: Mapped[str | None] = mapped_column(String, nullable=True)  # imitated participant
    session: Mapped[str | None] = mapped_column(String(12), nullable=True)
    posture: Mapped[str | None] = mapped_column(String(12), nullable=True)
    device_label: Mapped[str | None] = mapped_column(String(60), nullable=True)
    platform: Mapped[str | None] = mapped_column(String(20), nullable=True)
    nominal_hz: Mapped[float | None] = mapped_column(Float, nullable=True)
    accel_includes_gravity: Mapped[bool] = mapped_column(Boolean, default=False)
    samples: Mapped[list] = mapped_column(JSON)
    diagnostics: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=now)
