"""Audit log. Never receives raw sensor data."""

from __future__ import annotations

from sqlalchemy.orm import Session

from .models import AuditLog


def audit(db: Session, actor: str, event: str, intent_id: str | None = None, **details) -> None:
    db.add(AuditLog(actor=actor, event=event, intent_id=intent_id, details=details))
