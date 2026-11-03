"""Sending a bill to one customer: presence, Person mode, AI auto mode and everyone-nearby alerts."""

from airsign.ai.names import match_name, score
from airsign.models import Presence
from airsign.db import SessionLocal
from airsign.presence import TTL

from .conftest import new_client
from .test_flow import SHOP_LAT, SHOP_LON, cashier, customer, enroll, open_invoice, sign_until_done

HERE = {"lat": SHOP_LAT, "lon": SHOP_LON, "accuracy": 20}
ELSEWHERE = {"lat": 0, "lon": 0, "accuracy": 20}


def present(name):
    c = customer(name)
    assert c.post("/api/presence", json=HERE).json()["you"]["name"] == name
    return c


def row_for(pos, name, code=None):
    rows = [r for r in pos.get("/api/pos/nearby").json() if r["name"] == name and (code is None or r["code"] == code)]
    assert rows, f"{name} is not nearby"
    return rows[0]


def unique(name):
    # The suite shares a database and nearby customers, so names must not collide across tests.
    import secrets

    letters = "bdfgklmnpqrstvwxz"
    return name + "".join(secrets.choice(letters) for _ in range(6))


def bills_of(c, where=HERE):
    return c.post("/api/presence", json=where).json()


# Presence ---------------------------------------------------------------------------


def test_only_customers_in_the_building_with_alerts_on_appear():
    pos = cashier()
    name = unique("Nila")
    c = present(name)
    me = bills_of(c)["you"]
    assert row_for(pos, name)["code"] == me["code"] and len(me["code"]) >= 2
    away = customer(unique("Away"))
    assert away.post("/api/presence", json=ELSEWHERE).json()["you"] is None
    assert away.post("/api/presence", json={**HERE, "accuracy": 5000}).json()["you"] is None
    assert all(r["name"] != "Away" for r in pos.get("/api/pos/nearby").json())
    assert new_client().get("/api/pos/nearby").status_code == 401


def test_turning_off_or_going_quiet_removes_the_customer():
    pos = cashier()
    name, quiet = unique("Tara"), unique("Quiet")
    c, q = present(name), present(quiet)
    assert c.delete("/api/presence").status_code == 200
    assert name not in [r["name"] for r in pos.get("/api/pos/nearby").json()]
    with SessionLocal() as db:
        row = db.get(Presence, row_for(pos, quiet)["id"])
        row.last_seen = row.last_seen - TTL * 2
        db.commit()
    assert quiet not in [r["name"] for r in pos.get("/api/pos/nearby").json()]
    assert q is not None


def test_presence_stores_no_position():
    assert not {"lat", "lon", "accuracy"} & set(Presence.__table__.columns.keys())


# Person mode ------------------------------------------------------------------------


def test_a_sent_bill_reaches_only_that_customer():
    pos = cashier()
    a, b = unique("Rumi"), unique("Sumi")
    ca, cb = present(a), present(b)
    inv = open_invoice(pos, "12.50")
    assert bills_of(ca)["bills"] == [] and bills_of(ca)["ready"] == []  # creating a bill alerts nobody yet

    r = pos.post(f"/api/pos/invoices/{inv['id']}/send", json={"presence_id": row_for(pos, a)["id"]})
    assert r.status_code == 200 and r.json()["customer"]["name"] == a
    assert [x["invoice"]["id"] for x in bills_of(ca)["bills"]] == [inv["id"]]
    assert bills_of(cb)["bills"] == [] and bills_of(cb)["ready"] == []
    assert [x["customer"]["name"] for x in pos.get("/api/pos/invoices").json() if x["id"] == inv["id"]] == [a]


def test_nobody_else_can_find_or_pay_a_sent_bill():
    pos = cashier()
    a = unique("Mitu")
    ca = present(a)
    other = customer(unique("Other"))
    enroll(other)
    inv = open_invoice(pos, "13.00")
    pos.post(f"/api/pos/invoices/{inv['id']}/send", json={"presence_id": row_for(pos, a)["id"]})

    listed = other.get("/api/shops/shop_lumen/invoices").json()["invoices"]
    assert inv["id"] not in [i["id"] for i in listed]
    assert inv["id"] in [i["id"] for i in ca.get("/api/shops/shop_lumen/invoices").json()["invoices"]]
    r = other.post("/api/intents", json={"invoice_id": inv["id"], "confirmed_by": "list"})
    assert r.status_code == 403 and r.json()["code"] == "not_your_bill"

    token = pos.post("/api/pos/checkin-token", json={"invoice_id": inv["id"]}).json()
    assert other.post("/api/checkin", json={"token": token["token"]}).json()["invoice"] is None


def test_the_customer_pays_a_sent_bill_by_signing():
    pos = cashier()
    a = unique("Lina")
    c = present(a)
    enroll(c)
    assert c.post("/api/wallet/link").status_code == 200
    assert c.post("/api/wallet/link/finish").status_code == 200
    inv = open_invoice(pos, "14.25")
    pos.post(f"/api/pos/invoices/{inv['id']}/send", json={"presence_id": row_for(pos, a)["id"]})
    intent = c.post("/api/intents", json={"invoice_id": inv["id"], "confirmed_by": "sent", "location": HERE}).json()
    assert intent["state"] == "ARMED" and intent["confirmed_by"] == "sent"
    out, _ = sign_until_done(c, intent["id"], intent["nonce"])
    assert out["state"] == "COMPLETED"
    assert bills_of(c)["bills"] == []


def test_not_mine_and_cancel_give_the_bill_back_to_the_cashier():
    pos = cashier()
    a = unique("Joya")
    c = present(a)
    enroll(c)
    inv = open_invoice(pos, "15.00")
    pid = row_for(pos, a)["id"]
    pos.post(f"/api/pos/invoices/{inv['id']}/send", json={"presence_id": pid})
    assert c.post(f"/api/bills/{inv['id']}/decline").status_code == 200
    assert bills_of(c)["bills"] == []
    assert [x["customer"] for x in pos.get("/api/pos/invoices").json() if x["id"] == inv["id"]] == [None]

    pos.post(f"/api/pos/invoices/{inv['id']}/send", json={"presence_id": pid})
    intent = c.post("/api/intents", json={"invoice_id": inv["id"], "confirmed_by": "sent", "location": HERE}).json()
    assert c.post(f"/api/intents/{intent['id']}/cancel").status_code == 200
    assert [x["customer"] for x in pos.get("/api/pos/invoices").json() if x["id"] == inv["id"]] == [None]


def test_sending_rules():
    pos = cashier()
    payer, late = unique("Payer"), unique("Late")
    cp, cl = present(payer), present(late)
    enroll(cp)
    inv = open_invoice(pos, "16.00")
    assert pos.post(f"/api/pos/invoices/{inv['id']}/send", json={"presence_id": "prs_gone"}).status_code == 409
    other_shop = cashier("shop_kettle")
    assert other_shop.post(f"/api/pos/invoices/{inv['id']}/send", json={"presence_id": None}).status_code == 404
    assert new_client().post(f"/api/pos/invoices/{inv['id']}/send", json={"presence_id": None}).status_code == 401

    pos.post(f"/api/pos/invoices/{inv['id']}/send", json={"presence_id": row_for(pos, payer)["id"]})
    assert cp.post("/api/intents", json={"invoice_id": inv["id"], "confirmed_by": "sent", "location": HERE}).status_code == 200
    r = pos.post(f"/api/pos/invoices/{inv['id']}/send", json={"presence_id": row_for(pos, late)["id"]})
    assert r.status_code == 409 and r.json()["code"] == "in_progress"
    assert cl is not None


# AI auto mode -----------------------------------------------------------------------


def test_one_clear_spoken_name_sends_the_bill():
    pos = cashier()
    name = unique("Zarif")
    c = present(name)
    inv = open_invoice(pos, "17.00")
    r = pos.post(f"/api/pos/invoices/{inv['id']}/match-name", json={"heard": [f"my name is {name}"]})
    assert r.json()["status"] == "sent" and r.json()["customer"]["name"] == name
    assert [x["invoice"]["id"] for x in bills_of(c)["bills"]] == [inv["id"]]


def test_same_names_and_unclear_names_go_back_to_the_cashier():
    pos = cashier()
    name = unique("Twin")
    c1, c2 = present(name), present(name)
    inv = open_invoice(pos, "18.00")
    out = pos.post(f"/api/pos/invoices/{inv['id']}/match-name", json={"heard": [name]}).json()
    assert out["status"] == "choose" and [x["name"] for x in out["candidates"]][:2] == [name, name]
    assert out["candidates"][0]["code"] != out["candidates"][1]["code"]
    assert bills_of(c1)["bills"] == [] and bills_of(c2)["bills"] == []


def test_name_matching_handles_spellings_and_filler():
    assert score(["Raheem"], "Rahim") >= 0.8 and score(["Rohim"], "Rahim") >= 0.8
    assert score(["my name is Mahmood"], "Mahmud") >= 0.8
    assert score(["amar nam Rifat"], "Md Rifat") == 1.0
    assert score(["my name is"], "Rahim") == 0.0
    people = [("a", "Rahim"), ("b", "Karim"), ("c", "Nila")]
    assert match_name(["Rahim"], people) == ("a", ["a"])
    assert match_name(["Raheem"], people)[0] == "a"
    assert match_name(["Zzz"], people)[0] is None
    assert match_name(["Rahim"], [("a", "Rahim"), ("b", "Rahim")])[0] is None
    assert match_name(["Rahim"], []) == (None, [])


# Everyone nearby --------------------------------------------------------------------


def test_announced_bills_hide_the_details_until_opened():
    pos = cashier()
    a = present(unique("Anu"))
    enroll_customer = present(unique("Bina"))
    enroll(enroll_customer)
    inv = open_invoice(pos, "19.00")
    assert pos.post(f"/api/pos/invoices/{inv['id']}/broadcast").status_code == 200

    ready = [x for x in bills_of(a)["ready"] if x["invoice_id"] == inv["id"]]
    assert len(ready) == 1 and "amount_cents" not in str(ready[0]) and ready[0]["shop"]["id"] == "shop_lumen"
    opened = a.post(f"/api/bills/{inv['id']}/open").json()
    assert opened["invoice"]["amount_cents"] == 1900
    away = customer(unique("Far"))
    assert away.post("/api/presence", json=ELSEWHERE).json()["ready"] == []

    # Once someone starts paying it, it stops being offered to others.
    assert enroll_customer.post("/api/intents", json={"invoice_id": inv["id"], "confirmed_by": "popup", "location": HERE}).status_code == 200
    assert inv["id"] not in [x["invoice_id"] for x in bills_of(a)["ready"]]


def test_a_plain_bill_cannot_be_opened_as_announced():
    pos = cashier()
    c = customer(unique("Curious"))
    inv = open_invoice(pos, "20.00")
    assert c.post(f"/api/bills/{inv['id']}/open").status_code == 404
