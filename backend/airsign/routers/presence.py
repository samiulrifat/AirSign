"""The customer side of bill alerts: check in while alerts are on, and receive bills."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import presence as svc
from ..audit import audit
from ..auth import customer
from ..db import get_db
from ..intents import TERMINAL
from ..models import Building, Invoice, Merchant, PaymentIntent, now
from ..ratelimit import rate_limit
from ..shops import MAX_ACCURACY_M
from .shops import OPEN_INVOICE_WINDOW, buildings_in_range, checkin_pass_merchant, invoice_view, shop_view

router = APIRouter(prefix="/api")


def fail(code: str, message: str, status: int = 400):
    raise HTTPException(status, {"code": code, "message": message})


def busy_with_someone_else(db: Session, invoice_ids: list[str], user_id: str | None) -> set[str]:
    query = select(PaymentIntent.invoice_id).where(
        PaymentIntent.invoice_id.in_(invoice_ids),
        PaymentIntent.state.not_in(tuple(TERMINAL)),
        PaymentIntent.expires_at > now(),
    )
    if user_id:
        query = query.where(PaymentIntent.user_id != user_id)
    return set(db.scalars(query))


class PresenceIn(BaseModel):
    lat: float | None = Field(default=None, ge=-90, le=90)
    lon: float | None = Field(default=None, ge=-180, le=180)
    accuracy: float | None = Field(default=None, gt=0)
    simulated: bool = False
    # From typing or scanning the code on the cashier's screen. Works without location.
    checkin_pass: str | None = None
    # The customer picked the shop themselves because location could not find it.
    shop_id: str | None = Field(default=None, max_length=64)


def _buildings(db: Session, body: PresenceIn, user_id: str) -> tuple[list[Building], str, Merchant | None, str | None]:
    """The buildings this customer could be in, how we know, the shop they chose, and why none was found."""
    merchant_id = checkin_pass_merchant(body.checkin_pass, user_id)
    if merchant_id:
        m = db.get(Merchant, merchant_id)
        return [db.get(Building, m.building_id)], "code", None, None
    if body.shop_id:
        m = db.get(Merchant, body.shop_id)
        if m is None:
            fail("not_found", "Shop not found.", 404)
        return [db.get(Building, m.building_id)], "chosen", m, None
    if body.lat is None or body.lon is None or body.accuracy is None:
        return [], "location", None, "no_location"
    if body.accuracy > MAX_ACCURACY_M:
        return [], "location", None, "low_accuracy"
    found = [b for _, b in buildings_in_range(db, body.lat, body.lon, body.accuracy)]
    return found, "location", None, None if found else "no_shop"


@router.post("/presence", dependencies=[Depends(rate_limit("bills"))])
def check_in(body: PresenceIn, session=Depends(customer), db: Session = Depends(get_db)):
    """Called every few seconds while alerts are on. Stores the building, not the position.

    `bills` were sent to this customer. `ready` were announced to everyone nearby, so
    they show only the shop until the customer opens one. Nothing else reaches the
    phone: choosing a shop never hands over a bill, the cashier still sends it.
    """
    user, _ = session
    found, via, chosen, reason = _buildings(db, body, user.id)
    row = svc.touch(db, user.id, [b.id for b in found], body.simulated, via, chosen.id if chosen else None) if found else None

    mine = db.execute(
        select(Invoice, Merchant)
        .join(Merchant, Invoice.merchant_id == Merchant.id)
        .where(Invoice.customer_id == user.id, Invoice.status == "OPEN", Invoice.created_at >= now() - OPEN_INVOICE_WINDOW)
        .order_by(Invoice.created_at.desc())
    ).all()
    announced = []
    if found:
        announced = db.execute(
            select(Invoice, Merchant)
            .join(Merchant, Invoice.merchant_id == Merchant.id)
            .where(
                Merchant.building_id.in_([b.id for b in found]),
                Invoice.broadcast.is_(True),
                Invoice.customer_id.is_(None),
                Invoice.status == "OPEN",
                Invoice.created_at >= now() - OPEN_INVOICE_WINDOW,
            )
            .order_by(Invoice.created_at.desc())
        ).all()
    busy = busy_with_someone_else(db, [i.id for i, _ in announced], user.id)
    you = None
    if row is not None:
        you = {
            "name": user.display_name,
            "code": row.code,
            "building": " or ".join(b.name for b in found),
            "building_ids": [b.id for b in found],
            "via": via,
            "shop": shop_view(chosen) if chosen else None,
        }
    out = {
        "you": you,
        "reason": reason,
        "bills": [{"shop": shop_view(m), "invoice": invoice_view(i)} for i, m in mine],
        "ready": [{"shop": shop_view(m), "invoice_id": i.id} for i, m in announced if i.id not in busy],
    }
    db.commit()
    return out


@router.delete("/presence")
def turn_off(session=Depends(customer), db: Session = Depends(get_db)):
    svc.leave(db, session[0].id)
    db.commit()
    return {"ok": True}


@router.post("/bills/{invoice_id}/open", dependencies=[Depends(rate_limit("bills"))])
def open_announced(invoice_id: str, session=Depends(customer), db: Session = Depends(get_db)):
    """The amount and items of an announced bill, once the customer taps it."""
    inv = db.get(Invoice, invoice_id)
    if inv is None or inv.status != "OPEN" or not inv.broadcast or inv.customer_id not in (None, session[0].id):
        fail("not_found", "This bill is no longer available.", 404)
    audit(db, session[0].id, "bill.opened", None, invoice=inv.id)
    db.commit()
    return {"shop": shop_view(db.get(Merchant, inv.merchant_id)), "invoice": invoice_view(inv)}


@router.post("/bills/{invoice_id}/decline", dependencies=[Depends(rate_limit("bills"))])
def decline(invoice_id: str, session=Depends(customer), db: Session = Depends(get_db)):
    """Not mine: the bill goes back to the cashier, who can send it to someone else."""
    inv = db.get(Invoice, invoice_id)
    if inv is None or inv.customer_id != session[0].id:
        fail("not_found", "This bill is no longer available.", 404)
    inv.customer_id = None
    audit(db, session[0].id, "bill.declined", None, invoice=inv.id)
    db.commit()
    return {"ok": True}
