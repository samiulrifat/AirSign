"""Saved PayPal accounts (PayPal Vault payment tokens)."""

from __future__ import annotations

import logging

from sqlalchemy.orm import Session

from .models import PayPalWallet
from .paypal.gateway import PayPalError, get_gateway

log = logging.getLogger("airsign.wallets")


def active_wallet(db: Session, user_id: str) -> PayPalWallet | None:
    wallet = db.get(PayPalWallet, user_id)
    return wallet if wallet is not None and wallet.status == "ACTIVE" and wallet.payment_token_id else None


def forget_wallet(db: Session, user_id: str, keep_row: bool = False) -> None:
    """Delete the token at PayPal, then here. A PayPal failure is logged and does not block the user."""
    wallet = db.get(PayPalWallet, user_id)
    if wallet is None:
        return
    if wallet.payment_token_id:
        try:
            get_gateway().delete_payment_token(wallet.payment_token_id)
        except PayPalError as e:
            log.error("could not delete the PayPal token for %s: %s", user_id, e)
    if not keep_row:
        db.delete(wallet)
