"""Cashier point-of-sale screen: invoices and the rotating check-in code."""

from __future__ import annotations

import secrets
import time
from datetime import timedelta
from decimal import Decimal, InvalidOperation

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..audit import audit
from ..auth import POS_COOKIE, cashier, start_pos_session
from ..config import get_settings
from ..db import get_db
from ..intents import TERMINAL
from ..ratelimit import rate_limit
from ..models import CheckinToken, Invoice, Merchant, PaymentIntent, now
from ..security import check_pin, make_checkin_token
from .shops import invoice_view, shop_view

router = APIRouter(prefix="/api/pos")

MAX_INVOICE_CENTS = 200_000  # per-shop limit for the demo
TOKEN_TTL_S = 60


def fail(code: str, message: str, status: int = 400):
    raise HTTPException(status, {"code": code, "message": message})


@router.get("/shops")
def shops(db: Session = Depends(get_db)):
    return [shop_view(m) for m in db.scalars(select(Merchant).order_by(Merchant.building_id, Merchant.floor))]


class LoginIn(BaseModel):
    merchant_id: str
    pin: str = Field(min_length=4, max_length=12)


@router.post("/login", dependencies=[Depends(rate_limit("pos_login"))])
def login(body: LoginIn, response: Response, db: Session = Depends(get_db)):
    m = db.get(Merchant, body.merchant_id)
    if m is None or not check_pin(body.pin, m.pin_hash):
        fail("bad_login", "Shop or PIN is not correct.", 401)
    start_pos_session(response, m)
    audit(db, m.id, "pos.login")
    db.commit()
    return shop_view(m)


@router.post("/logout")
def logout(response: Response):
    response.delete_cookie(POS_COOKIE)
    return {"ok": True}


@router.get("/me")
def me(request: Request, db: Session = Depends(get_db)):
    """The logged-in shop, or null. Not a 401, so the login screen loads quietly."""
    try:
        return shop_view(cashier(request, db))
    except HTTPException:
        return None


class Item(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    qty: int = Field(default=1, ge=1, le=99)
    price: str


class InvoiceIn(BaseModel):
    amount: str | None = None  # decimal string; computed from items if omitted
    items: list[Item] = Field(default_factory=list, max_length=30)


def _cents(text: str) -> int:
    try:
        d = Decimal(text)
    except InvalidOperation:
        fail("bad_amount", "Amount must be a number like 12.50.")
    if d != d.quantize(Decimal("0.01")):
        fail("bad_amount", "Amount can have at most two decimals.")
    return int(d * 100)


@router.post("/invoices")
def create_invoice(body: InvoiceIn, merchant: Merchant = Depends(cashier), db: Session = Depends(get_db)):
    items = [{"name": i.name, "qty": i.qty, "price_cents": _cents(i.price)} for i in body.items]
    cents = _cents(body.amount) if body.amount else sum(i["qty"] * i["price_cents"] for i in items)
    if items and body.amount and cents != sum(i["qty"] * i["price_cents"] for i in items):
        fail("bad_amount", "The amount does not match the items.")
    if cents <= 0:
        fail("bad_amount", "The amount must be above zero.")
    if cents > MAX_INVOICE_CENTS:
        fail("over_limit", "The amount is above this shop's limit.")
    inv = Invoice(merchant_id=merchant.id, amount_cents=cents, currency=get_settings().currency, items=items)
    db.add(inv)
    db.flush()
    audit(db, merchant.id, "invoice.created", None, invoice=inv.id, amount_cents=cents)
    db.commit()
    return invoice_view(inv)


@router.get("/invoices")
def list_invoices(merchant: Merchant = Depends(cashier), db: Session = Depends(get_db)):
    rows = db.scalars(
        select(Invoice).where(Invoice.merchant_id == merchant.id).order_by(Invoice.created_at.desc()).limit(20)
    ).all()
    out = []
    for inv in rows:
        latest = db.scalar(
            select(PaymentIntent).where(PaymentIntent.invoice_id == inv.id).order_by(PaymentIntent.created_at.desc()).limit(1)
        )
        out.append(
            {
                **invoice_view(inv),
                "payment": None
                if latest is None
                else {"state": latest.state, "tier": latest.risk_tier, "simulated": latest.simulated, "webhook_confirmed": latest.webhook_confirmed},
            }
        )
    return out


@router.post("/invoices/{invoice_id}/cancel")
def cancel_invoice(invoice_id: str, merchant: Merchant = Depends(cashier), db: Session = Depends(get_db)):
    inv = db.get(Invoice, invoice_id)
    if inv is None or inv.merchant_id != merchant.id:
        fail("not_found", "Invoice not found.", 404)
    if inv.status != "OPEN":
        fail("not_open", "Only open invoices can be cancelled.", 409)
    busy = db.scalar(
        select(PaymentIntent.id).where(
            PaymentIntent.invoice_id == inv.id, PaymentIntent.state.in_(("SIGNING", "VERIFIED", "CAPTURING"))
        )
    )
    if busy:
        fail("in_progress", "A payment for this invoice is being completed.", 409)
    for intent in db.scalars(select(PaymentIntent).where(PaymentIntent.invoice_id == inv.id, PaymentIntent.state.not_in(tuple(TERMINAL)))):
        audit(db, merchant.id, "intent.transition", intent.id, frm=intent.state, to="CANCELLED", reason="invoice_cancelled")
        intent.state = "CANCELLED"
    inv.status = "CANCELLED"
    audit(db, merchant.id, "invoice.cancelled", None, invoice=inv.id)
    db.commit()
    return invoice_view(inv)


class TokenIn(BaseModel):
    invoice_id: str | None = None


@router.post("/checkin-token")
def checkin_token(body: TokenIn, merchant: Merchant = Depends(cashier), db: Session = Depends(get_db)):
    """A fresh signed token (for the QR code) and six-digit code. The screen asks every 30 s."""
    if body.invoice_id:
        inv = db.get(Invoice, body.invoice_id)
        if inv is None or inv.merchant_id != merchant.id or inv.status != "OPEN":
            fail("not_open", "That invoice is not open.", 409)
    iat = int(time.time())
    jti = secrets.token_urlsafe(12)
    payload = {"v": 1, "shop": merchant.id, "floor": merchant.floor, "iat": iat, "exp": iat + TOKEN_TTL_S, "jti": jti}
    if body.invoice_id:
        payload["inv"] = body.invoice_id
    for _ in range(10):
        code = f"{secrets.randbelow(1_000_000):06d}"
        clash = db.scalar(select(CheckinToken.jti).where(CheckinToken.code == code, CheckinToken.expires_at > now()))
        if not clash:
            break
    db.add(CheckinToken(jti=jti, merchant_id=merchant.id, invoice_id=body.invoice_id, code=code, expires_at=now() + timedelta(seconds=TOKEN_TTL_S)))
    db.commit()
    return {"token": make_checkin_token(merchant.secret, payload), "code": code, "expires_in": TOKEN_TTL_S}
