"""Buildings, shop detection, open invoices and check-in."""

from __future__ import annotations

import time
from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..audit import audit
from ..auth import customer
from ..config import get_settings
from ..db import get_db
from ..intents import local_now
from ..models import Building, CheckinToken, Invoice, Merchant, PaymentHistory, now
from ..security import read_checkin_token, sign_value, unsign_value, verify_checkin_signature
from ..seed import move_building
from ..shops import MAX_ACCURACY_M, ShopCandidate, haversine_m, is_open, rank

router = APIRouter(prefix="/api")

OPEN_INVOICE_WINDOW = timedelta(minutes=10)
LISTED_INVOICE_WINDOW = timedelta(minutes=30)
CHECKIN_PASS_TTL_S = 600


def fail(code: str, message: str, status: int = 400):
    raise HTTPException(status, {"code": code, "message": message})


def shop_view(m: Merchant, hour: int | None = None) -> dict:
    out = {"id": m.id, "name": m.name, "floor": m.floor, "building_id": m.building_id, "category": m.category}
    if hour is not None:
        out["open_now"] = is_open(m.open_hour, m.close_hour, hour)
    return out


@router.get("/buildings")
def buildings(db: Session = Depends(get_db)):
    hour = local_now().hour
    out = []
    for b in db.scalars(select(Building).order_by(Building.name)):
        shops = db.scalars(select(Merchant).where(Merchant.building_id == b.id).order_by(Merchant.floor)).all()
        out.append({"id": b.id, "name": b.name, "lat": b.lat, "lon": b.lon, "shops": [shop_view(m, hour) for m in shops]})
    return out


@router.get("/shops/nearby")
def nearby(
    lat: float = Query(ge=-90, le=90),
    lon: float = Query(ge=-180, le=180),
    accuracy: float = Query(gt=0),
    altitude: float | None = None,
    altitude_accuracy: float | None = None,
    session=Depends(customer),
    db: Session = Depends(get_db),
):
    user, _ = session
    if accuracy > MAX_ACCURACY_M:
        return {"status": "low_accuracy", "message": "Your location is too approximate. Turn on precise location, or pick the shop from the list."}

    best = None
    for b in db.scalars(select(Building)):
        d = haversine_m(lat, lon, b.lat, b.lon)
        if d <= b.radius_m + accuracy and (best is None or d < best[0]):
            best = (d, b)
    if best is None:
        return {"status": "none", "message": "No AirSign shops near you."}
    building = best[1]

    current = local_now()
    merchants = db.scalars(select(Merchant).where(Merchant.building_id == building.id)).all()
    history = db.scalars(select(PaymentHistory).where(PaymentHistory.user_id == user.id)).all()
    recent_open = {
        inv.merchant_id
        for inv in db.scalars(
            select(Invoice).where(Invoice.status == "OPEN", Invoice.created_at >= now() - OPEN_INVOICE_WINDOW)
        )
    }
    candidates = []
    for m in merchants:
        visits = [h for h in history if h.merchant_id == m.id]
        at_hour = sum(1 for h in visits if h.at.astimezone(current.tzinfo).hour == current.hour)
        candidates.append(
            ShopCandidate(
                id=m.id,
                name=m.name,
                floor=m.floor,
                lat=m.lat,
                lon=m.lon,
                radius_m=m.radius_m,
                open_hour=m.open_hour,
                close_hour=m.close_hour,
                visits=len(visits),
                hour_share=at_hour / len(visits) if visits else 0.0,
                open_invoice=m.id in recent_open,
            )
        )
    result = rank(candidates, lat, lon, accuracy, current, altitude_m=altitude, altitude_accuracy_m=altitude_accuracy)
    by_id = {m.id: m for m in merchants}
    return {
        "status": "ok",
        "building": {"id": building.id, "name": building.name, "distance_m": round(best[0], 1)},
        "suggestion": result["suggestion"],
        "shops": [
            {**shop_view(by_id[r["shop_id"]], current.hour), "probability": round(r["probability"], 3), "features": r["features"]}
            for r in result["ranked"]
        ],
    }


@router.get("/shops/{shop_id}/invoices")
def open_invoices(shop_id: str, session=Depends(customer), db: Session = Depends(get_db)):
    merchant = db.get(Merchant, shop_id)
    if merchant is None:
        fail("not_found", "Shop not found.", 404)
    rows = db.scalars(
        select(Invoice)
        .where(Invoice.merchant_id == shop_id, Invoice.status == "OPEN", Invoice.created_at >= now() - LISTED_INVOICE_WINDOW)
        .order_by(Invoice.created_at.desc())
    ).all()
    return {"shop": shop_view(merchant), "invoices": [invoice_view(i) for i in rows]}


def invoice_view(i: Invoice) -> dict:
    return {
        "id": i.id,
        "amount_cents": i.amount_cents,
        "currency": i.currency,
        "items": i.items,
        "status": i.status,
        "created_at": i.created_at,
    }


class CheckinIn(BaseModel):
    token: str | None = None
    code: str | None = None


@router.post("/checkin")
def checkin(body: CheckinIn, session=Depends(customer), db: Session = Depends(get_db)):
    """Verify a scanned shop QR token or its six-digit fallback code. Single use."""
    user, _ = session
    row = None
    if body.token:
        parsed = read_checkin_token(body.token)
        if parsed is None:
            fail("bad_token", "This code is not an AirSign check-in code.")
        payload, b, sig = parsed
        merchant = db.get(Merchant, payload.get("shop"))
        if merchant is None or not verify_checkin_signature(merchant.secret, b, sig):
            fail("bad_token", "This check-in code is not valid.")
        if payload.get("exp", 0) < time.time():
            fail("expired", "This check-in code has expired. Scan the current one.", 410)
        row = db.get(CheckinToken, payload.get("jti"))
    elif body.code:
        row = db.scalar(
            select(CheckinToken).where(CheckinToken.code == body.code.strip(), CheckinToken.expires_at > now())
        )
    if row is None or row.expires_at < now():
        fail("bad_code", "This check-in code is not valid or has expired.")
    if row.used_at is not None:
        fail("used", "This check-in code was already used. Ask for a fresh one.", 409)
    row.used_at = now()
    merchant = db.get(Merchant, row.merchant_id)
    invoice = db.get(Invoice, row.invoice_id) if row.invoice_id else None
    exp = int(time.time()) + CHECKIN_PASS_TTL_S
    audit(db, user.id, "checkin.verified", None, shop=merchant.id, via="token" if body.token else "code")
    db.commit()
    return {
        "shop": shop_view(merchant),
        "invoice": invoice_view(invoice) if invoice and invoice.status == "OPEN" else None,
        "checkin_pass": sign_value(f"{user.id}|{merchant.id}|{exp}", "checkin"),
    }


def checkin_pass_valid(value: str | None, user_id: str, merchant_id: str) -> bool:
    raw = unsign_value(value, "checkin")
    if not raw:
        return False
    uid, mid, exp = raw.split("|")
    return uid == user_id and mid == merchant_id and int(exp) >= time.time()


class MoveIn(BaseModel):
    building_id: str
    lat: float
    lon: float


@router.post("/sim/move-building")
def sim_move_building(body: MoveIn, session=Depends(customer), db: Session = Depends(get_db)):
    """Simulation: put a demo building where the tester is, so real-phone location works anywhere."""
    if not get_settings().allow_simulation:
        fail("simulation_disabled", "Simulation is turned off on this server.", 403)
    if db.get(Building, body.building_id) is None:
        fail("not_found", "Building not found.", 404)
    b = move_building(db, body.building_id, body.lat, body.lon)
    audit(db, session[0].id, "sim.building_moved", None, building=b.id)
    db.commit()
    return {"id": b.id, "lat": b.lat, "lon": b.lon}
