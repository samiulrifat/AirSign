"""PayPal access behind one small interface.

SandboxGateway  the PayPal sandbox via PayPal's official Python Server SDK
                (Orders v2) plus the REST webhook verification endpoint.
MockGateway     a local stand-in with the same behavior, used until sandbox
                credentials are configured. Its approval step is a plainly
                labeled page in the app, not an imitation of PayPal.

Only the intent service calls `capture_order`, and only for a VERIFIED intent.

Checked against PayPal's own integration guidance (the paypal-best-practices skill
in github.com/paypal/AI-Toolkit): every POST carries a PayPal-Request-Id, 429 and
5xx are retried with backoff (safe because of those ids), the debug_id of every
error is logged, INSTRUMENT_DECLINED is reported separately, and webhooks are
verified against the raw request body.
"""

from __future__ import annotations

import json
import logging
import secrets
import threading
import time
from dataclasses import dataclass
from functools import lru_cache

import httpx
from sqlalchemy.orm import sessionmaker

from ..config import get_settings
from ..models import MockOrder

log = logging.getLogger("airsign.paypal")
SANDBOX_API = "https://api-m.sandbox.paypal.com"


class PayPalError(RuntimeError):
    def __init__(self, message: str, issue: str | None = None, debug_id: str | None = None):
        super().__init__(message)
        self.issue = issue  # PayPal's issue code, for example INSTRUMENT_DECLINED
        self.debug_id = debug_id  # needed when asking PayPal support about a failure


def _paypal_error(action: str, e: Exception) -> PayPalError:
    """Turn an SDK exception into a PayPalError and log PayPal's debug_id."""
    name = getattr(e, "name", None)
    debug_id = getattr(e, "debug_id", None)
    details = getattr(e, "details", None) or []
    issue = getattr(details[0], "issue", None) if details else None
    status = getattr(e, "response_code", None)
    log.error("PayPal %s failed: status=%s name=%s issue=%s debug_id=%s", action, status, name, issue, debug_id)
    return PayPalError(f"{action} failed: {issue or name or status}", issue=issue or name, debug_id=debug_id)


@dataclass
class OrderInfo:
    id: str
    status: str  # CREATED, APPROVED, COMPLETED, ...
    amount: str
    currency: str
    reference_id: str | None


@dataclass
class CaptureResult:
    order_status: str
    capture_id: str | None
    capture_status: str | None  # COMPLETED, PENDING, DECLINED, ...


class SandboxGateway:
    mode = "sandbox"

    def __init__(self, client_id: str, client_secret: str, webhook_id: str):
        from paypalserversdk.configuration import Environment
        from paypalserversdk.http.auth.o_auth_2 import ClientCredentialsAuthCredentials
        from paypalserversdk.paypal_serversdk_client import PaypalServersdkClient

        self.client_id = client_id
        self._secret = client_secret
        self.webhook_id = webhook_id
        self.sdk = PaypalServersdkClient(
            client_credentials_auth_credentials=ClientCredentialsAuthCredentials(
                o_auth_client_id=client_id, o_auth_client_secret=client_secret
            ),
            environment=Environment.SANDBOX,
            timeout=20,
            # Retry 429 and 5xx with exponential backoff. POST is safe to retry
            # because every POST below carries a PayPal-Request-Id.
            max_retries=2,
            backoff_factor=1,
            retry_methods=["GET", "POST"],
        )
        self._token: tuple[str, float] | None = None
        self._lock = threading.Lock()

    @staticmethod
    def _order_info(order) -> OrderInfo:
        pu = (order.purchase_units or [None])[0]
        amount = getattr(pu, "amount", None)
        return OrderInfo(
            id=order.id,
            status=order.status,
            amount=getattr(amount, "value", None),
            currency=getattr(amount, "currency_code", None),
            reference_id=getattr(pu, "reference_id", None),
        )

    def create_order(self, *, reference_id: str, invoice_id: str, amount: str, currency: str, description: str) -> OrderInfo:
        from paypalserversdk.exceptions.api_exception import ApiException
        from paypalserversdk.models.amount_with_breakdown import AmountWithBreakdown
        from paypalserversdk.models.checkout_payment_intent import CheckoutPaymentIntent
        from paypalserversdk.models.order_request import OrderRequest
        from paypalserversdk.models.purchase_unit_request import PurchaseUnitRequest

        body = OrderRequest(
            intent=CheckoutPaymentIntent.CAPTURE,
            purchase_units=[
                PurchaseUnitRequest(
                    reference_id=reference_id,
                    invoice_id=invoice_id,
                    description=description[:127],
                    amount=AmountWithBreakdown(currency_code=currency, value=amount),
                )
            ],
        )
        try:
            res = self.sdk.orders.create_order(
                {"body": body, "paypal_request_id": f"airsign-order-{invoice_id}", "prefer": "return=representation"}
            )
        except ApiException as e:
            raise _paypal_error("create order", e) from e
        return self._order_info(res.body)

    def get_order(self, order_id: str) -> OrderInfo:
        from paypalserversdk.exceptions.api_exception import ApiException

        try:
            res = self.sdk.orders.get_order({"id": order_id})
        except ApiException as e:
            raise _paypal_error("get order", e) from e
        return self._order_info(res.body)

    def capture_order(self, order_id: str, request_id: str) -> CaptureResult:
        from paypalserversdk.exceptions.api_exception import ApiException

        try:
            res = self.sdk.orders.capture_order(
                {"id": order_id, "paypal_request_id": request_id, "prefer": "return=representation"}
            )
        except ApiException as e:
            raise _paypal_error("capture", e) from e
        order = res.body
        capture = None
        for pu in order.purchase_units or []:
            caps = getattr(getattr(pu, "payments", None), "captures", None) or []
            if caps:
                capture = caps[0]
                break
        return CaptureResult(
            order_status=order.status,
            capture_id=getattr(capture, "id", None),
            capture_status=getattr(capture, "status", None),
        )

    # Webhook verification uses the REST endpoint directly.
    def _access_token(self) -> str:
        with self._lock:
            if self._token and self._token[1] > time.time() + 60:
                return self._token[0]
            r = httpx.post(
                f"{SANDBOX_API}/v1/oauth2/token",
                auth=(self.client_id, self._secret),
                data={"grant_type": "client_credentials"},
                timeout=20,
            )
            r.raise_for_status()
            data = r.json()
            self._token = (data["access_token"], time.time() + float(data.get("expires_in", 300)))
            return self._token[0]

    def verify_webhook(self, headers: dict[str, str], raw_body: bytes) -> bool:
        if not self.webhook_id:
            log.error("AIRSIGN_PAYPAL_WEBHOOK_ID is not set, rejecting webhook")
            return False
        h = {k.lower(): v for k, v in headers.items()}
        fields = {
            "auth_algo": h.get("paypal-auth-algo"),
            "cert_url": h.get("paypal-cert-url"),
            "transmission_id": h.get("paypal-transmission-id"),
            "transmission_sig": h.get("paypal-transmission-sig"),
            "transmission_time": h.get("paypal-transmission-time"),
            "webhook_id": self.webhook_id,
        }
        if not all(fields.values()):
            return False
        # The signature covers the body exactly as PayPal sent it. Parsing and
        # re-serializing the JSON changes the bytes and breaks verification, so
        # the raw body is spliced into the request unchanged.
        body = json.dumps(fields)[:-1] + ', "webhook_event": ' + raw_body.decode("utf-8") + "}"
        r = httpx.post(
            f"{SANDBOX_API}/v1/notifications/verify-webhook-signature",
            headers={"Authorization": f"Bearer {self._access_token()}", "Content-Type": "application/json"},
            content=body.encode("utf-8"),
            timeout=20,
        )
        if r.status_code != 200:
            log.error("webhook verification call failed: status=%s debug_id=%s", r.status_code, r.headers.get("paypal-debug-id"))
            return False
        return r.json().get("verification_status") == "SUCCESS"


class MockGateway:
    mode = "mock"
    client_id = None

    def __init__(self, session_factory: sessionmaker):
        self._sessions = session_factory

    def create_order(self, *, reference_id: str, invoice_id: str, amount: str, currency: str, description: str) -> OrderInfo:
        with self._sessions() as db:
            order = MockOrder(
                id="MOCK-" + secrets.token_hex(6).upper(),
                reference_id=reference_id,
                amount=amount,
                currency=currency,
                status="CREATED",
            )
            db.add(order)
            db.commit()
            return OrderInfo(order.id, order.status, amount, currency, reference_id)

    def approve(self, order_id: str) -> None:
        """Stands in for the buyer approving in the PayPal window."""
        with self._sessions() as db:
            order = db.get(MockOrder, order_id)
            if order is None:
                raise PayPalError("unknown order")
            if order.status == "CREATED":
                order.status = "APPROVED"
                db.commit()

    def get_order(self, order_id: str) -> OrderInfo:
        with self._sessions() as db:
            order = db.get(MockOrder, order_id)
            if order is None:
                raise PayPalError("unknown order")
            return OrderInfo(order.id, order.status, order.amount, order.currency, order.reference_id)

    def capture_order(self, order_id: str, request_id: str) -> CaptureResult:
        with self._sessions() as db:
            order = db.get(MockOrder, order_id)
            if order is None:
                raise PayPalError("unknown order")
            if order.status == "COMPLETED":
                # Idempotent replay of the same request returns the same result,
                # a different request id is refused, like PayPal does.
                if request_id in order.capture_request_ids:
                    return CaptureResult("COMPLETED", order.capture_id, "COMPLETED")
                raise PayPalError("ORDER_ALREADY_CAPTURED")
            if order.status != "APPROVED":
                raise PayPalError("ORDER_NOT_APPROVED")
            order.status = "COMPLETED"
            order.capture_id = "MOCKCAP-" + secrets.token_hex(6).upper()
            order.capture_request_ids = [*order.capture_request_ids, request_id]
            db.commit()
            return CaptureResult("COMPLETED", order.capture_id, "COMPLETED")

    def verify_webhook(self, headers: dict[str, str], raw_body: bytes) -> bool:
        # Mock events are delivered in-process, never over HTTP.
        return False


@lru_cache(maxsize=1)
def get_gateway():
    s = get_settings()
    if s.paypal_mode == "sandbox":
        return SandboxGateway(s.paypal_client_id, s.paypal_client_secret, s.paypal_webhook_id)
    from ..db import SessionLocal

    return MockGateway(SessionLocal)
