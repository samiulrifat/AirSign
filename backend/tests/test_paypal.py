"""The sandbox gateway follows PayPal's integration guidance (github.com/paypal/AI-Toolkit).

No network: the HTTP call and the SDK are replaced by fakes.
"""

import json
from types import SimpleNamespace

import pytest

from airsign.paypal import gateway
from airsign.paypal.gateway import PayPalError, SandboxGateway, _paypal_error

HEADERS = {
    "PAYPAL-AUTH-ALGO": "SHA256withRSA",
    "PAYPAL-CERT-URL": "https://api.sandbox.paypal.com/v1/notifications/certs/CERT-1",
    "PAYPAL-TRANSMISSION-ID": "tx-1",
    "PAYPAL-TRANSMISSION-SIG": "sig==",
    "PAYPAL-TRANSMISSION-TIME": "2026-10-26T10:00:00Z",
}


@pytest.fixture
def gw(monkeypatch):
    g = SandboxGateway("client-id", "client-secret", "WH-123")
    monkeypatch.setattr(g, "_access_token", lambda: "token")
    return g


def test_webhook_verification_sends_the_raw_body_unchanged(gw, monkeypatch):
    # Odd spacing and key order: re-serializing would change these bytes.
    raw = b'{"id":"WH-1",  "event_type" : "PAYMENT.CAPTURE.COMPLETED","resource":{"amount":{"value":"18.50"}}}'
    sent = {}

    def fake_post(url, headers, content, timeout):
        sent["url"], sent["body"] = url, content
        return SimpleNamespace(status_code=200, headers={}, json=lambda: {"verification_status": "SUCCESS"})

    monkeypatch.setattr(gateway.httpx, "post", fake_post)
    assert gw.verify_webhook(HEADERS, raw) is True
    assert sent["url"].endswith("/v1/notifications/verify-webhook-signature")
    assert raw in sent["body"]  # byte for byte
    body = json.loads(sent["body"])
    assert body["webhook_id"] == "WH-123" and body["transmission_id"] == "tx-1"
    assert body["webhook_event"]["event_type"] == "PAYMENT.CAPTURE.COMPLETED"


def test_webhook_rejected_without_headers_or_on_failure(gw, monkeypatch):
    monkeypatch.setattr(
        gateway.httpx, "post",
        lambda *a, **k: SimpleNamespace(status_code=200, headers={}, json=lambda: {"verification_status": "FAILURE"}),
    )
    assert gw.verify_webhook({}, b"{}") is False
    assert gw.verify_webhook(HEADERS, b'{"id":"x"}') is False


def test_create_order_sends_an_idempotency_key(gw, monkeypatch):
    seen = {}

    def create_order(options):
        seen.update(options)
        order = SimpleNamespace(
            id="ORDER-1", status="CREATED",
            purchase_units=[SimpleNamespace(reference_id="inv_1", amount=SimpleNamespace(value="18.50", currency_code="USD"))],
        )
        return SimpleNamespace(body=order)

    monkeypatch.setattr(gw.sdk, "_orders", SimpleNamespace(create_order=create_order), raising=False)
    monkeypatch.setattr(type(gw.sdk), "orders", property(lambda self: self._orders))
    info = gw.create_order(reference_id="inv_1", invoice_id="pi_1", amount="18.50", currency="USD", description="test")
    assert seen["paypal_request_id"] == "airsign-order-pi_1"
    assert info.id == "ORDER-1" and info.amount == "18.50"


def test_errors_carry_issue_and_debug_id():
    e = SimpleNamespace(
        name="UNPROCESSABLE_ENTITY", debug_id="abc123", response_code=422,
        details=[SimpleNamespace(issue="INSTRUMENT_DECLINED")],
    )
    err = _paypal_error("capture", e)
    assert isinstance(err, PayPalError)
    assert err.issue == "INSTRUMENT_DECLINED" and err.debug_id == "abc123"
