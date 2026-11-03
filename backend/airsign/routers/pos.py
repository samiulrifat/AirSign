"""Cashier point-of-sale screen: invoices and the rotating check-in code."""

from __future__ import annotations

import base64
import binascii
import re
import secrets
import time
from datetime import timedelta
from decimal import Decimal, InvalidOperation

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..ai import cashier as ai_cashier
from ..ai.gemini import GeminiUnavailable
from ..ai.names import match_name
from ..audit import audit
from ..auth import POS_COOKIE, cashier, start_pos_session
from ..config import get_settings
from ..db import get_db
from ..intents import TERMINAL
from ..ratelimit import rate_limit
from .. import presence as nearby_customers
from ..models import CheckinToken, Invoice, Merchant, PaymentIntent, Presence, User, now
from ..security import check_pin, make_checkin_token
from ..seed import catalog_for
from .shops import invoice_view, shop_view

router = APIRouter(prefix="/api/pos")

MAX_INVOICE_CENTS = 200_000  # per-shop limit for the demo
TOKEN_TTL_S = 60
MAX_IMAGE_CHARS = 5_000_000  # the screen shrinks photos first, so this is well above a real one
DATA_URL = re.compile(r"^data:(image/(?:jpeg|png|webp));base64,(.+)$", re.DOTALL)


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


def _customer_view(db: Session, inv: Invoice) -> dict | None:
    if not inv.customer_id:
        return None
    user = db.get(User, inv.customer_id)
    row = db.scalar(select(Presence).where(Presence.user_id == inv.customer_id))
    return None if user is None else {"name": user.display_name, "code": row.code if row else None}


def _open_invoice(db: Session, invoice_id: str, merchant: Merchant) -> Invoice:
    inv = db.get(Invoice, invoice_id)
    if inv is None or inv.merchant_id != merchant.id:
        fail("not_found", "Invoice not found.", 404)
    if inv.status != "OPEN":
        fail("not_open", "That invoice is not open.", 409)
    return inv


def _assign(db: Session, inv: Invoice, merchant: Merchant, row: Presence) -> None:
    active = db.scalar(
        select(PaymentIntent.id).where(
            PaymentIntent.invoice_id == inv.id,
            PaymentIntent.user_id != row.user_id,
            PaymentIntent.state.not_in(tuple(TERMINAL)),
            PaymentIntent.expires_at > now(),
        )
    )
    if active:
        fail("in_progress", "Someone is already paying this bill.", 409)
    inv.customer_id = row.user_id
    inv.broadcast = False
    audit(db, merchant.id, "invoice.sent", None, invoice=inv.id, customer=row.user_id)


@router.get("/nearby")
def nearby(merchant: Merchant = Depends(cashier), db: Session = Depends(get_db)):
    """Customers with bill alerts on in this building. It can include other floors."""
    return [nearby_customers.view(db, r) for r in nearby_customers.fresh_rows(db, merchant.building_id)]


class SendIn(BaseModel):
    presence_id: str | None = None  # None takes the bill back


@router.post("/invoices/{invoice_id}/send", dependencies=[Depends(rate_limit("send_bill"))])
def send_bill(invoice_id: str, body: SendIn, merchant: Merchant = Depends(cashier), db: Session = Depends(get_db)):
    """Person mode: the cashier asks the name and picks the customer."""
    inv = _open_invoice(db, invoice_id, merchant)
    if body.presence_id is None:
        inv.customer_id = None
        audit(db, merchant.id, "invoice.taken_back", None, invoice=inv.id)
    else:
        rows = {r.id: r for r in nearby_customers.fresh_rows(db, merchant.building_id)}
        row = rows.get(body.presence_id)
        if row is None:
            fail("gone", "That customer is no longer nearby.", 409)
        _assign(db, inv, merchant, row)
    db.commit()
    return {**invoice_view(inv), "customer": _customer_view(db, inv)}


class HeardIn(BaseModel):
    heard: list[str] = Field(min_length=1, max_length=5)


@router.post("/invoices/{invoice_id}/match-name", dependencies=[Depends(rate_limit("send_bill"))])
def match_spoken_name(invoice_id: str, body: HeardIn, merchant: Merchant = Depends(cashier), db: Session = Depends(get_db)):
    """AI auto mode: send on one clear match, otherwise give the cashier the likely customers."""
    inv = _open_invoice(db, invoice_id, merchant)
    rows = {r.id: nearby_customers.view(db, r) for r in nearby_customers.fresh_rows(db, merchant.building_id)}
    heard = [h[:120] for h in body.heard]
    match, likely = match_name(heard, [(i, v["name"]) for i, v in rows.items()])
    if match is None:
        return {"status": "choose" if likely else "nobody", "candidates": [rows[i] for i in likely], "customer": None}
    row = db.get(Presence, match)
    _assign(db, inv, merchant, row)
    db.commit()
    return {"status": "sent", "candidates": [], "customer": _customer_view(db, inv)}


@router.post("/invoices/{invoice_id}/broadcast", dependencies=[Depends(rate_limit("send_bill"))])
def broadcast_bill(invoice_id: str, merchant: Merchant = Depends(cashier), db: Session = Depends(get_db)):
    """Tell everyone nearby a bill is ready at this shop. They see the shop only, until they open it."""
    inv = _open_invoice(db, invoice_id, merchant)
    inv.customer_id = None
    inv.broadcast = True
    audit(db, merchant.id, "invoice.broadcast", None, invoice=inv.id)
    db.commit()
    return {**invoice_view(inv), "customer": None}


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
                "broadcast": inv.broadcast,
                "customer": _customer_view(db, inv),
                "payment": None
                if latest is None
                else {"state": latest.state, "tier": latest.risk_tier, "simulated": latest.simulated, "webhook_confirmed": latest.webhook_confirmed},
            }
        )
    return out


@router.get("/catalog")
def catalog(merchant: Merchant = Depends(cashier)):
    return [{"name": c["name"], "price": c["price"]} for c in catalog_for(merchant.category)]


class AiBillIn(BaseModel):
    text: str | None = Field(default=None, max_length=300)
    image: str | None = Field(default=None, max_length=MAX_IMAGE_CHARS)  # a data: URL


def _image(data_url: str) -> tuple[str, bytes]:
    match = DATA_URL.match(data_url)
    if not match:
        fail("bad_image", "Use a JPEG, PNG or WebP photo.")
    try:
        return match.group(1), base64.b64decode(match.group(2), validate=True)
    except binascii.Error:
        fail("bad_image", "That photo could not be read.")


@router.post("/ai-bill", dependencies=[Depends(rate_limit("ai_bill"))])
def ai_bill(body: AiBillIn, merchant: Merchant = Depends(cashier), db: Session = Depends(get_db)):
    """Draft bill lines from a photo or a description. Creates nothing: the cashier checks it first."""
    text = (body.text or "").strip() or None
    if not text and not body.image:
        fail("empty", "Describe the order or add a photo.")
    image = _image(body.image) if body.image else None
    try:
        draft = ai_cashier.draft_bill(merchant.category, text, image)
    except GeminiUnavailable:
        fail("ai_unavailable", "The AI could not read the photo right now. Type the items instead.", 503)
    audit(db, merchant.id, "pos.ai_bill", None, source=draft["source"], photo=image is not None, lines=len(draft["lines"]))
    db.commit()
    return draft


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
