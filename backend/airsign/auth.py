"""Light sessions for the prototype: a signed cookie per customer device and per cashier.

Customers get an account bound to the device on first visit. A production system
would use a real login (for example Log in with PayPal) and device-bound keys.
"""

from __future__ import annotations

from datetime import timedelta

from fastapi import Depends, HTTPException, Request, Response
from sqlalchemy.orm import Session

from .config import get_settings
from .db import get_db
from .models import Device, Merchant, User, now
from .security import sign_value, unsign_value

SESSION_COOKIE = "airsign_session"
POS_COOKIE = "airsign_pos"
MAX_AGE = 60 * 60 * 24 * 30


def set_cookie(response: Response, name: str, value: str) -> None:
    response.set_cookie(
        name,
        value,
        max_age=MAX_AGE,
        httponly=True,
        samesite="lax",
        secure=get_settings().secure_cookies,
    )


def start_session(response: Response, user: User, device: Device) -> None:
    set_cookie(response, SESSION_COOKIE, sign_value(f"{user.id}|{device.id}", "session"))


def optional_customer(request: Request, db: Session = Depends(get_db)) -> tuple[User, Device] | None:
    value = unsign_value(request.cookies.get(SESSION_COOKIE), "session")
    if not value or "|" not in value:
        return None
    user_id, device_id = value.split("|", 1)
    user = db.get(User, user_id)
    device = db.get(Device, device_id)
    if user is None or device is None or device.user_id != user.id:
        return None
    if now() - device.last_seen_at > timedelta(minutes=5):
        device.last_seen_at = now()
        db.commit()  # do not keep a write transaction open for the whole request
    return user, device


def customer(session=Depends(optional_customer)) -> tuple[User, Device]:
    if session is None:
        raise HTTPException(401, {"code": "no_session", "message": "Please start a session first."})
    return session


def start_pos_session(response: Response, merchant: Merchant) -> None:
    set_cookie(response, POS_COOKIE, sign_value(merchant.id, "pos"))


def cashier(request: Request, db: Session = Depends(get_db)) -> Merchant:
    merchant_id = unsign_value(request.cookies.get(POS_COOKIE), "pos")
    merchant = db.get(Merchant, merchant_id) if merchant_id else None
    if merchant is None:
        raise HTTPException(401, {"code": "no_pos_session", "message": "Cashier login required."})
    return merchant
