"""End-to-end payment flow through the API, using the mock PayPal and simulated gestures."""

from pathlib import Path

import pytest

from .conftest import new_client

SHOP = "shop_lumen"
SHOP_LAT, SHOP_LON = 40.74204, -73.98901


def customer(name="Ada"):
    c = new_client()
    r = c.post("/api/session", json={"display_name": name, "consent": True})
    assert r.status_code == 200, r.text
    return c


def enroll(c, n=7):
    assert c.post("/api/enroll/start", json={"simulated": True}).status_code == 200
    for i in range(n):
        g = c.get("/api/sim/gesture", params={"kind": "genuine", "enroll_index": i}).json()
        r = c.post("/api/enroll/sample", json={k: g[k] for k in ("samples", "device", "simulated")})
        assert r.json()["accepted"], r.json()
    r = c.post("/api/enroll/finish")
    assert r.status_code == 200, r.text
    return r.json()


def cashier(shop=SHOP):
    c = new_client()
    assert c.post("/api/pos/login", json={"merchant_id": shop, "pin": "1234"}).status_code == 200
    return c


def open_invoice(pos, amount="18.50"):
    r = pos.post("/api/pos/invoices", json={"amount": amount})
    assert r.status_code == 200, r.text
    return r.json()


def start_payment(c, invoice_id, **extra):
    body = {"invoice_id": invoice_id, "confirmed_by": "popup", "location": {"lat": SHOP_LAT, "lon": SHOP_LON, "accuracy": 20}}
    body.update(extra)
    r = c.post("/api/intents", json=body)
    assert r.status_code == 200, r.text
    intent = r.json()
    assert intent["state"] == "PROPOSED"
    order = intent["paypal"]["order_id"]
    assert c.post(f"/api/mock-paypal/orders/{order}/approve").status_code == 200
    r = c.post(f"/api/intents/{intent['id']}/approved")
    assert r.status_code == 200, r.text
    return r.json()


def sign(c, intent_id, nonce, kind="genuine", gesture=None):
    g = gesture or c.get("/api/sim/gesture", params={"kind": kind}).json()
    r = c.post(
        f"/api/intents/{intent_id}/signoff",
        json={"nonce": nonce, "samples": g["samples"], "device": g["device"], "simulated": True},
    )
    return r, g


def sign_until_done(c, intent_id, nonce, max_tries=3):
    """Genuine signatures, as many as the verifier asks for (step-up or a rare false reject)."""
    for _ in range(max_tries):
        r, g = sign(c, intent_id, nonce)
        out = r.json()
        if out["state"] != "ARMED":
            return out, g
        nonce = out["nonce"]
    return out, g


def test_happy_path_and_receipt():
    c = customer()
    enroll(c)
    pos = cashier()
    inv = open_invoice(pos)

    near = c.get("/api/shops/nearby", params={"lat": SHOP_LAT, "lon": SHOP_LON, "accuracy": 25}).json()
    assert near["status"] == "ok" and near["building"]["id"] == "bld_meridian"
    assert near["suggestion"] == SHOP, near  # the open invoice breaks the three-floor tie

    listed = c.get(f"/api/shops/{SHOP}/invoices").json()["invoices"]
    assert inv["id"] in [i["id"] for i in listed]

    armed = start_payment(c, inv["id"])
    intent = armed["intent"]
    assert intent["state"] == "ARMED" and intent["risk"]["tier"] == 1, intent["risk"]
    assert intent["amount_cents"] == 1850 and intent["shop"]["floor"] == 4

    out, _ = sign_until_done(c, intent["id"], armed["nonce"])
    assert out["decision"] in ("ACCEPT", "STEP_UP"), out
    assert out["intent"]["state"] == "COMPLETED"
    assert out["intent"]["paypal"]["capture_id"].startswith("MOCKCAP-")

    # The mock webhook arrives after the response and reconciles.
    again = c.get(f"/api/intents/{intent['id']}").json()
    assert again["paypal"]["webhook_confirmed"] is True
    assert pos.get("/api/pos/invoices").json()[0]["status"] == "PAID"


def test_nonce_is_single_use_and_bound_to_intent():
    c = customer()
    enroll(c)
    inv = open_invoice(cashier(), "5.00")
    armed = start_payment(c, inv["id"])
    iid, nonce = armed["intent"]["id"], armed["nonce"]
    r, g = sign(c, iid, nonce, kind="impostor")
    assert r.json()["decision"] == "REJECT"
    # Same nonce again is refused before any scoring.
    r2, _ = sign(c, iid, nonce, gesture=g)
    assert r2.status_code == 403 and r2.json()["code"] == "nonce_invalid"


def test_imitation_rejected_three_times_locks_intent_not_account():
    c = customer()
    enroll(c)
    pos = cashier()
    inv = open_invoice(pos, "9.00")
    armed = start_payment(c, inv["id"])
    iid, nonce = armed["intent"]["id"], armed["nonce"]
    for i in range(3):
        r, _ = sign(c, iid, nonce, kind="imitation")
        out = r.json()
        assert out["decision"] == "REJECT", out
        nonce = out["nonce"]
    assert out["state"] == "LOCKED" and nonce is None
    # The account still works: a new invoice can be paid.
    inv2 = open_invoice(pos, "9.00")
    armed2 = start_payment(c, inv2["id"])
    out, _ = sign_until_done(c, armed2["intent"]["id"], armed2["nonce"])
    assert out["state"] == "COMPLETED"


def test_replay_of_accepted_signature_locks():
    c = customer()
    enroll(c)
    pos = cashier()
    armed = start_payment(c, open_invoice(pos, "4.20")["id"])
    out, g = sign_until_done(c, armed["intent"]["id"], armed["nonce"])
    assert out["state"] == "COMPLETED"
    # Resubmit the exact same recording for a new payment.
    armed2 = start_payment(c, open_invoice(pos, "4.20")["id"])
    r2, _ = sign(c, armed2["intent"]["id"], armed2["nonce"], gesture=g)
    out = r2.json()
    assert out["state"] == "LOCKED" and "replay_of_earlier_attempt" in out["flags"], out


def test_replay_of_enrollment_recording_locks():
    c = customer()
    enroll(c)
    armed = start_payment(c, open_invoice(cashier(), "3.00")["id"])
    g = c.get("/api/sim/gesture", params={"kind": "genuine", "enroll_index": 3}).json()
    r, _ = sign(c, armed["intent"]["id"], armed["nonce"], gesture=g)
    assert r.json()["state"] == "LOCKED" and "replay_of_enrollment" in r.json()["flags"]


def test_injected_sensor_data_rejected():
    c = customer()
    enroll(c)
    armed = start_payment(c, open_invoice(cashier(), "3.00")["id"])
    r, _ = sign(c, armed["intent"]["id"], armed["nonce"], kind="injection")
    out = r.json()
    assert out["decision"] == "REJECT" and "no_sensor_noise" in out["flags"], out


def test_large_payment_new_shop_far_away_needs_two_signatures():
    c = customer()
    enroll(c)
    pos = cashier("shop_volt")
    # Tier 3 needs two passes within three attempts at the strict threshold, which a
    # genuine signer misses a few percent of the time. Allow one fresh bill for that.
    for _ in range(2):
        armed = start_payment(c, open_invoice(pos, "480.00")["id"], location={"lat": 40.80, "lon": -73.90, "accuracy": 30})
        intent = armed["intent"]
        assert intent["risk"]["tier"] == 3, intent["risk"]
        assert intent["signatures_required"] == 2
        assert "new shop" in intent["risk"]["explanation"]
        nonce = armed["nonce"]
        while True:
            r, _ = sign(c, intent["id"], nonce)
            out = r.json()
            if out["state"] != "ARMED":
                break
            nonce = out["nonce"]
        if out["state"] == "COMPLETED":
            assert out["signatures_passed"] == 2, "one good signature alone must not complete a tier 3 payment"
            return
        assert out["state"] == "LOCKED", out
    raise AssertionError("genuine signer locked out of two tier 3 payments in a row")


def test_checkin_code_verifies_location_and_is_single_use():
    c = customer()
    enroll(c)
    pos = cashier()
    inv = open_invoice(pos, "7.00")
    tok = pos.post("/api/pos/checkin-token", json={"invoice_id": inv["id"]}).json()
    r = c.post("/api/checkin", json={"code": tok["code"]})
    assert r.status_code == 200 and r.json()["invoice"]["id"] == inv["id"]
    assert c.post("/api/checkin", json={"code": tok["code"]}).status_code in (400, 409)
    armed = start_payment(c, inv["id"], confirmed_by="code", location=None, checkin_pass=r.json()["checkin_pass"])
    assert armed["intent"]["location_verified"] is True
    assert armed["intent"]["risk"]["contributions"]["loc"] == 0


def test_qr_token_signature_checked():
    c = customer()
    pos = cashier()
    tok = pos.post("/api/pos/checkin-token", json={}).json()["token"]
    body, sig = tok.split(".")
    forged = body + "." + ("A" * len(sig))
    assert c.post("/api/checkin", json={"token": forged}).json()["code"] == "bad_token"
    assert c.post("/api/checkin", json={"token": tok}).status_code == 200


def test_unapproved_order_cannot_be_armed():
    c = customer()
    enroll(c)
    inv = open_invoice(cashier(), "6.00")
    intent = c.post("/api/intents", json={"invoice_id": inv["id"], "confirmed_by": "list"}).json()
    r = c.post(f"/api/intents/{intent['id']}/approved")
    assert r.status_code == 409 and r.json()["code"] == "not_approved"


def test_someone_else_cannot_take_an_invoice_in_progress():
    a, b = customer("A"), customer("B")
    enroll(a)
    enroll(b)
    inv = open_invoice(cashier(), "8.00")
    start_payment(a, inv["id"])
    r = b.post("/api/intents", json={"invoice_id": inv["id"], "confirmed_by": "list"})
    assert r.status_code == 409 and r.json()["code"] == "invoice_busy"


def test_other_user_cannot_sign_my_intent():
    a, b = customer("A"), customer("B")
    enroll(a)
    armed = start_payment(a, open_invoice(cashier(), "2.00")["id"])
    r, _ = sign(b, armed["intent"]["id"], armed["nonce"])
    assert r.status_code == 404


def test_delete_signature_blocks_payment():
    c = customer()
    enroll(c)
    assert c.delete("/api/me/signature").json()["signature"] is None
    r = c.post("/api/intents", json={"invoice_id": open_invoice(cashier(), "2.00")["id"], "confirmed_by": "list"})
    assert r.status_code == 409 and r.json()["code"] == "not_enrolled"


def test_enrollment_rejects_trivial_gesture():
    c = customer()
    c.post("/api/enroll/start", json={"simulated": True})
    g = c.get("/api/sim/gesture", params={"kind": "shake"}).json()
    r = c.post("/api/enroll/sample", json={k: g[k] for k in ("samples", "device", "simulated")}).json()
    assert r["accepted"] is False and r["code"] in ("too_simple", "too_weak", "too_flat")


def test_unverified_webhook_does_not_complete_anything():
    c = new_client()
    r = c.post(
        "/api/webhooks/paypal",
        json={"id": "WH-FAKE-1", "event_type": "PAYMENT.CAPTURE.COMPLETED", "resource": {"supplementary_data": {"related_ids": {"order_id": "X"}}}},
    )
    assert r.json()["status"] == "unverified"


def test_capture_has_a_single_call_site():
    """Design rule: only the sign-off path can move money."""
    src = Path(__file__).resolve().parents[1] / "airsign"
    callers = [
        (p.name, line.strip())
        for p in src.rglob("*.py")
        if p.parent.name != "paypal"  # the gateway implementations themselves
        for line in p.read_text().splitlines()
        if "capture_order(" in line and "def capture_order" not in line
    ]
    assert callers == [("intents.py", "result = get_gateway().capture_order(intent.paypal_order_id, intent.capture_request_id)")], callers
    intents_src = (src / "intents.py").read_text()
    assert intents_src.count("_capture(db, intent)") == 1  # called once, from signoff
