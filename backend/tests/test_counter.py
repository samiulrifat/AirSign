"""Getting on the cashier's list without a precise location, and building the bill from many inputs."""

import pytest

from airsign.ai import cashier as ai_cashier
from airsign.ai import gemini
from airsign.db import SessionLocal
from airsign.models import Building, Merchant
from airsign.seed import BUILDINGS, MERCHANTS, catalog_for

from .test_flow import SHOP_LAT, SHOP_LON, cashier, customer, enroll, open_invoice
from .test_modes import HERE, ELSEWHERE, row_for, unique

PNG = "data:image/png;base64,iVBORw0KGgo="


@pytest.fixture
def restore_buildings():
    """Tests here move demo buildings. Put them back for everyone else."""
    yield
    with SessionLocal() as db:
        for b in BUILDINGS:
            row = db.get(Building, b["id"])
            row.lat, row.lon, row.radius_m = b["lat"], b["lon"], b["radius_m"]
        for m in MERCHANTS:
            b = next(x for x in BUILDINGS if x["id"] == m["building_id"])
            row = db.get(Merchant, m["id"])
            row.lat, row.lon = b["lat"] + m["dlat"], b["lon"] + m["dlon"]
        db.commit()


# Choosing the shop ------------------------------------------------------------------


def test_choosing_the_shop_lists_the_name_but_never_hands_over_a_bill():
    pos = cashier()
    name = unique("Mira")
    c = customer(name)
    enroll(c)
    inv = open_invoice(pos, "9.00")

    out = c.post("/api/presence", json={**ELSEWHERE, "shop_id": "shop_lumen"}).json()
    assert out["you"]["via"] == "chosen" and out["you"]["shop"]["id"] == "shop_lumen"
    assert out["bills"] == [] and out["ready"] == []
    row = row_for(pos, name)
    assert row["via"] == "chosen" and row["chose"] == "Lumen Books"

    # The bill on the till is not theirs until the cashier sends it.
    r = c.post("/api/intents", json={"invoice_id": inv["id"], "confirmed_by": "list"})
    assert r.status_code == 403 and r.json()["code"] == "not_sent"
    pos.post(f"/api/pos/invoices/{inv['id']}/send", json={"presence_id": row["id"]})
    out = c.post("/api/presence", json={"shop_id": "shop_lumen"}).json()
    assert [b["invoice"]["id"] for b in out["bills"]] == [inv["id"]]
    assert c.post("/api/presence", json={"shop_id": "shop_nowhere"}).status_code == 404


def test_scanning_a_bills_code_hands_it_to_that_customer_only():
    pos = cashier()
    a, b = customer(unique("Ola")), customer(unique("Pia"))
    inv = open_invoice(pos, "4.00")
    tok = pos.post("/api/pos/checkin-token", json={"invoice_id": inv["id"]}).json()
    assert a.post("/api/checkin", json={"code": tok["code"]}).json()["invoice"]["id"] == inv["id"]
    assert pos.get("/api/pos/invoices").json()[0]["customer"]["name"].startswith("Ola")
    tok = pos.post("/api/pos/checkin-token", json={"invoice_id": inv["id"]}).json()
    assert b.post("/api/checkin", json={"code": tok["code"]}).json()["invoice"] is None


def test_a_location_that_fits_two_buildings_lists_the_customer_in_both(restore_buildings):
    with SessionLocal() as db:
        river = db.get(Building, "bld_riverside")
        river.lat, river.lon = SHOP_LAT + 0.0003, SHOP_LON  # about 33 m away
        db.commit()
    name = unique("Duo")
    c = customer(name)
    you = c.post("/api/presence", json={**HERE, "accuracy": 30}).json()["you"]
    assert you["building_ids"] == ["bld_meridian", "bld_riverside"] and you["via"] == "location"
    assert row_for(cashier("shop_lumen"), name)["code"] == you["code"]
    assert row_for(cashier("shop_fig"), name)["code"] == you["code"]


def test_the_till_puts_its_building_where_it_stands(restore_buildings):
    pos = cashier("shop_fig")
    r = pos.post("/api/pos/sim/place-building", json={"lat": 23.7773, "lon": 90.4119, "accuracy": 400})
    assert r.status_code == 200 and r.json()["id"] == "bld_riverside" and r.json()["radius_m"] == 400
    name = unique("Rafi")
    c = customer(name)
    you = c.post("/api/presence", json={"lat": 23.7781, "lon": 90.4122, "accuracy": 15}).json()["you"]
    assert you["building_ids"] == ["bld_riverside"]
    assert row_for(pos, name)
    assert customer().post("/api/pos/sim/place-building", json={"lat": 0, "lon": 0, "accuracy": 5}).status_code == 401


# Building the bill ------------------------------------------------------------------


def _picks(text, category="cafe"):
    return ai_cashier.keyword_picks(catalog_for(category), text)


def test_spoken_changes_without_gemini():
    assert _picks("remove the croissant") == (False, [("remove", "croissant", 0)], [])
    assert _picks("take off one muffin and add two teas") == (False, [("remove", "muffin", 1), ("add", "tea", 2)], [])
    assert _picks("make it three flat whites") == (False, [("set", "flat-white", 3)], [])
    assert _picks("clear all")[0] is True
    assert _picks("cancel the whole order")[0] is True
    assert _picks("clear the bill then add a sandwich") == (True, [("add", "sandwich", 1)], [])
    assert _picks("remove all croissants") == (False, [("remove", "croissant", 0)], [])


def test_typed_changes_come_back_as_ops():
    r = cashier().post("/api/pos/ai-bill", json={"text": "remove the notebook", "cart": [{"name": "Notebook", "qty": 2}]})
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["ops"] == [{"action": "remove", "name": "Notebook", "qty": 0, "price": "9.00"}] and out["lines"] == []
    assert out["clear"] is False


def test_gemini_ops_are_checked_and_a_photo_only_adds(monkeypatch):
    replies = []

    def fake(prompt, schema, *, image=None, **kw):
        replies.append((prompt, image))
        return {
            "clear": True,
            "items": [{"id": "pen", "qty": 2, "action": "remove"}, {"id": "bookmark", "qty": 3, "action": "set"}, {"id": "x", "qty": 1, "action": "add"}],
            "unmatched": [],
        }

    monkeypatch.setattr(gemini, "generate_json", fake)
    pos = cashier()
    out = pos.post("/api/pos/ai-bill", json={"text": "start over, two fewer pens, three bookmarks"}).json()
    assert out["clear"] is True
    assert [(o["action"], o["name"], o["qty"]) for o in out["ops"]] == [("remove", "Pen", 2), ("set", "Bookmark", 3)]

    out = pos.post("/api/pos/ai-scan", json={"image": PNG}).json()
    assert out["clear"] is False and {o["action"] for o in out["ops"]} == {"add"}
    assert "live camera frame" in replies[-1][0] and replies[-1][1][0] == "image/png"
    assert pos.post("/api/pos/ai-scan", json={"text": "a pen"}).status_code == 400
