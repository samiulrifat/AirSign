"""Link a PayPal account once (PayPal Vault), so later payments skip the PayPal window."""

from __future__ import annotations

import secrets

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session

from ..audit import audit
from ..auth import customer
from ..db import get_db
from ..models import PayPalWallet, now
from ..paypal.gateway import PayPalError, get_gateway
from ..ratelimit import rate_limit
from ..wallets import forget_wallet
from .customer import me_view

router = APIRouter(prefix="/api/wallet")


def fail(code: str, message: str, status: int = 400):
    raise HTTPException(status, {"code": code, "message": message})


@router.post("/link", dependencies=[Depends(rate_limit("wallet"))])
def start_link(request: Request, session=Depends(customer), db: Session = Depends(get_db)):
    """Create a PayPal setup token. The browser then goes to PayPal to approve saving it."""
    user, _ = session
    origin = request.headers.get("origin") or str(request.base_url).rstrip("/")
    try:
        setup = get_gateway().create_setup_token(
            return_url=f"{origin}/wallet/return",
            cancel_url=f"{origin}/wallet/return?cancelled=1",
            request_id=f"airsign-setup-{user.id}-{secrets.token_hex(6)}",
        )
    except PayPalError:
        fail(
            "vault_unavailable",
            "PayPal did not allow saving an account on this app. You can still pay by approving each payment in PayPal.",
            502,
        )
    wallet = db.get(PayPalWallet, user.id)
    if wallet is None:
        wallet = PayPalWallet(user_id=user.id)
        db.add(wallet)
    if wallet.status != "ACTIVE":
        wallet.status = "PENDING"
    wallet.setup_token_id = setup.id
    db.commit()
    return {"approve_url": setup.approve_url, "mock": get_gateway().mode == "mock"}


@router.post("/link/finish", dependencies=[Depends(rate_limit("wallet"))])
def finish_link(session=Depends(customer), db: Session = Depends(get_db)):
    """Back from PayPal. Uses the setup token stored for this user, never one from the browser."""
    user, device = session
    wallet = db.get(PayPalWallet, user.id)
    if wallet is None or not wallet.setup_token_id:
        fail("no_link", "Start linking PayPal from the home screen.", 409)
    gw = get_gateway()
    try:
        saved = gw.create_payment_token(wallet.setup_token_id, request_id=f"airsign-token-{wallet.setup_token_id}")
    except PayPalError:
        fail("vault_failed", "PayPal did not confirm the link. Please try again.", 502)
    if wallet.payment_token_id and wallet.payment_token_id != saved.id:
        forget_wallet(db, user.id, keep_row=True)
    wallet.status = "ACTIVE"
    wallet.setup_token_id = None
    wallet.payment_token_id = saved.id
    wallet.payer_email = saved.payer_email
    wallet.linked_at = now()
    audit(db, user.id, "wallet.linked")
    db.commit()
    return me_view(db, user, device)


@router.delete("", dependencies=[Depends(rate_limit("wallet"))])
def remove(session=Depends(customer), db: Session = Depends(get_db)):
    user, device = session
    forget_wallet(db, user.id)
    audit(db, user.id, "wallet.removed")
    db.commit()
    return me_view(db, user, device)
