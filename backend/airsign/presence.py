"""Who is around: customers with bill alerts on, so a cashier can send them a bill.

A row holds the building and a short code, never a position. It lasts only while
the phone keeps checking in, and turning alerts off deletes it.

A customer gets on a cashier's list in one of three ways: their location (every
building it could be in, so overlapping buildings never hide anyone), a code from
the cashier's screen, or choosing the shop themselves. However they got there, the
cashier still decides who gets a bill.
"""

from __future__ import annotations

import secrets
from datetime import timedelta

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from .models import Merchant, Presence, User, now

# Long enough for a locked phone or a weak signal not to drop someone from the cashier's list.
TTL = timedelta(seconds=120)
# Easy to read aloud: no 0/O, 1/I or similar pairs.
LETTERS = "ACDEFGHJKMNPRTUVWXY"
DIGITS = "2346789"


def fresh_rows(db: Session, building_id: str) -> list[Presence]:
    return list(
        db.scalars(
            select(Presence)
            .where(
                or_(Presence.building_id == building_id, Presence.buildings.like(f"%,{building_id},%")),
                Presence.last_seen >= now() - TTL,
            )
            .order_by(Presence.arrived_at.desc())
        )
    )


def _new_code(db: Session, building_ids: list[str]) -> str:
    taken = {r.code for b in building_ids for r in fresh_rows(db, b)}
    for _ in range(20):
        code = secrets.choice(LETTERS) + secrets.choice(DIGITS)
        if code not in taken:
            return code
    return secrets.choice(LETTERS) + secrets.choice(DIGITS) + secrets.choice(DIGITS)


def touch(db: Session, user_id: str, building_ids: list[str], simulated: bool, via: str = "location", shop_id: str | None = None) -> Presence:
    """Keep this customer listed. The first building is the most likely one."""
    joined = "," + ",".join(building_ids) + ","
    row = db.scalar(select(Presence).where(Presence.user_id == user_id))
    if row is None:
        row = Presence(user_id=user_id, building_id=building_ids[0], buildings=joined, code=_new_code(db, building_ids))
        db.add(row)
    elif row.buildings != joined or row.building_id != building_ids[0] or row.last_seen < now() - TTL:
        moved = set(building_ids) != set((row.buildings or f",{row.building_id},").strip(",").split(","))
        row.building_id = building_ids[0]
        row.buildings = joined
        if moved or row.last_seen < now() - TTL:
            row.code = _new_code(db, building_ids)
            row.arrived_at = now()
    row.via = via
    row.shop_id = shop_id
    row.simulated = simulated
    row.last_seen = now()
    return row


def leave(db: Session, user_id: str) -> None:
    row = db.scalar(select(Presence).where(Presence.user_id == user_id))
    if row is not None:
        db.delete(row)


def view(db: Session, row: Presence) -> dict:
    user = db.get(User, row.user_id)
    shop = db.get(Merchant, row.shop_id) if row.shop_id else None
    return {
        "id": row.id,
        "name": user.display_name,
        "code": row.code,
        "arrived_at": row.arrived_at,
        "simulated": row.simulated,
        "via": row.via or "location",
        "chose": shop.name if shop else None,
    }
