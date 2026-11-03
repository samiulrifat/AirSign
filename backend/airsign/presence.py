"""Who is around: customers with bill alerts on, so a cashier can send them a bill.

A row holds the building and a short code, never a position. It lasts only while
the phone keeps checking in, and turning alerts off deletes it.
"""

from __future__ import annotations

import secrets
from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import Presence, User, now

# Long enough for a locked phone or a weak signal not to drop someone from the cashier's list.
TTL = timedelta(seconds=120)
# Easy to read aloud: no 0/O, 1/I or similar pairs.
LETTERS = "ACDEFGHJKMNPRTUVWXY"
DIGITS = "2346789"


def fresh_rows(db: Session, building_id: str) -> list[Presence]:
    return list(
        db.scalars(
            select(Presence).where(Presence.building_id == building_id, Presence.last_seen >= now() - TTL).order_by(Presence.arrived_at.desc())
        )
    )


def _new_code(db: Session, building_id: str) -> str:
    taken = {r.code for r in fresh_rows(db, building_id)}
    for _ in range(20):
        code = secrets.choice(LETTERS) + secrets.choice(DIGITS)
        if code not in taken:
            return code
    return secrets.choice(LETTERS) + secrets.choice(DIGITS) + secrets.choice(DIGITS)


def touch(db: Session, user_id: str, building_id: str, simulated: bool) -> Presence:
    row = db.scalar(select(Presence).where(Presence.user_id == user_id))
    if row is None:
        row = Presence(user_id=user_id, building_id=building_id, code=_new_code(db, building_id))
        db.add(row)
    elif row.building_id != building_id or row.last_seen < now() - TTL:
        row.building_id = building_id
        row.code = _new_code(db, building_id)
        row.arrived_at = now()
    row.simulated = simulated
    row.last_seen = now()
    return row


def leave(db: Session, user_id: str) -> None:
    row = db.scalar(select(Presence).where(Presence.user_id == user_id))
    if row is not None:
        db.delete(row)


def view(db: Session, row: Presence) -> dict:
    user = db.get(User, row.user_id)
    return {"id": row.id, "name": user.display_name, "code": row.code, "arrived_at": row.arrived_at, "simulated": row.simulated}
