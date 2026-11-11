"""Demo data: fictional buildings and shops, and a synthetic payment history.

Everything here is made up. The UI labels the history as synthetic.
"""

from __future__ import annotations

import random
from datetime import timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from .config import get_settings
from .models import Building, Merchant, PaymentHistory, User, now
from .security import hash_pin

DEMO_PIN = "1234"

BUILDINGS = [
    {"id": "bld_meridian", "name": "Meridian Arcade", "lat": 40.74205, "lon": -73.98903, "radius_m": 45},
    {"id": "bld_riverside", "name": "Riverside Market Hall", "lat": 40.75112, "lon": -73.99561, "radius_m": 40},
]

# Three shops stacked on floors 3, 4 and 5 share almost the same coordinates,
# which is the case location alone cannot solve (report, section 10).
MERCHANTS = [
    {"id": "shop_kettle", "name": "Kettle & Crumb Cafe", "building_id": "bld_meridian", "floor": 3, "dlat": 0.00002, "dlon": -0.00001, "open_hour": 7, "close_hour": 21, "category": "cafe"},
    {"id": "shop_lumen", "name": "Lumen Books", "building_id": "bld_meridian", "floor": 4, "dlat": -0.00001, "dlon": 0.00002, "open_hour": 8, "close_hour": 23, "category": "books"},
    {"id": "shop_volt", "name": "Volt Phone Repair", "building_id": "bld_meridian", "floor": 5, "dlat": 0.0, "dlon": 0.00001, "open_hour": 9, "close_hour": 22, "category": "electronics"},
    {"id": "shop_fig", "name": "Fig & Thistle Grocer", "building_id": "bld_riverside", "floor": 1, "dlat": 0.00003, "dlon": 0.0, "open_hour": 7, "close_hour": 23, "category": "grocery"},
    {"id": "shop_northwind", "name": "Northwind Outfitters", "building_id": "bld_riverside", "floor": 2, "dlat": -0.00002, "dlon": 0.00001, "open_hour": 10, "close_hour": 21, "category": "clothing"},
]


# What each kind of shop sells. Bill prices always come from here, never from the AI.
CATALOGS = {
    "cafe": [
        {"id": "flat-white", "name": "Flat white", "price": "4.80", "aliases": ["coffee", "latte"]},
        {"id": "croissant", "name": "Croissant", "price": "3.60", "aliases": ["pastry"]},
        {"id": "cold-brew", "name": "Cold brew", "price": "5.20", "aliases": ["iced coffee"]},
        {"id": "sandwich", "name": "Sandwich", "price": "8.90", "aliases": []},
        {"id": "tea", "name": "Tea", "price": "3.40", "aliases": []},
        {"id": "muffin", "name": "Muffin", "price": "3.90", "aliases": []},
    ],
    "books": [
        {"id": "paperback", "name": "Paperback", "price": "14.99", "aliases": ["book", "novel"]},
        {"id": "hardcover", "name": "Hardcover", "price": "27.50", "aliases": ["hardback"]},
        {"id": "notebook", "name": "Notebook", "price": "9.00", "aliases": ["journal"]},
        {"id": "gift-card", "name": "Gift card", "price": "50.00", "aliases": []},
        {"id": "pen", "name": "Pen", "price": "3.50", "aliases": []},
        {"id": "bookmark", "name": "Bookmark", "price": "2.00", "aliases": []},
    ],
    "electronics": [
        {"id": "screen-repair", "name": "Screen repair", "price": "189.00", "aliases": ["screen"]},
        {"id": "battery-swap", "name": "Battery swap", "price": "79.00", "aliases": ["battery"]},
        {"id": "charging-cable", "name": "Charging cable", "price": "19.00", "aliases": ["cable", "charger"]},
        {"id": "case", "name": "Case", "price": "24.00", "aliases": ["cover"]},
        {"id": "earphones", "name": "Earphones", "price": "35.00", "aliases": ["headphones", "earbuds"]},
        {"id": "screen-protector", "name": "Screen protector", "price": "15.00", "aliases": []},
    ],
    "grocery": [
        {"id": "fruit-box", "name": "Fruit box", "price": "12.40", "aliases": ["fruit", "apple", "banana"]},
        {"id": "bread", "name": "Bread", "price": "4.20", "aliases": ["loaf"]},
        {"id": "cheese", "name": "Cheese", "price": "9.75", "aliases": []},
        {"id": "olive-oil", "name": "Olive oil", "price": "15.00", "aliases": ["oil"]},
        {"id": "milk", "name": "Milk", "price": "3.10", "aliases": []},
        {"id": "eggs", "name": "Eggs", "price": "5.60", "aliases": ["egg"]},
    ],
    "clothing": [
        {"id": "t-shirt", "name": "T-shirt", "price": "25.00", "aliases": ["shirt", "tee"]},
        {"id": "jacket", "name": "Jacket", "price": "140.00", "aliases": ["coat"]},
        {"id": "socks", "name": "Socks", "price": "9.00", "aliases": ["sock"]},
        {"id": "cap", "name": "Cap", "price": "22.00", "aliases": ["hat"]},
        {"id": "scarf", "name": "Scarf", "price": "32.00", "aliases": []},
        {"id": "belt", "name": "Belt", "price": "38.00", "aliases": []},
    ],
}


def catalog_for(category: str) -> list[dict]:
    return CATALOGS.get(category, CATALOGS["cafe"])


def seed_shops(db: Session) -> None:
    for b in BUILDINGS:
        if db.get(Building, b["id"]) is None:
            db.add(Building(**b))
    db.flush()
    for m in MERCHANTS:
        if db.get(Merchant, m["id"]) is None:
            b = db.get(Building, m["building_id"])
            fields = {k: v for k, v in m.items() if k not in ("dlat", "dlon")}
            db.add(Merchant(**fields, lat=b.lat + m["dlat"], lon=b.lon + m["dlon"], pin_hash=hash_pin(DEMO_PIN)))
    db.commit()


MAX_PLACED_RADIUS_M = 1000.0


def move_building(db: Session, building_id: str, lat: float, lon: float, accuracy: float | None = None) -> Building:
    """Simulation helper: place a demo building (and its shops) where the tester stands.

    `accuracy` comes from the till: a laptop often knows its place only to a few hundred
    meters, so the building grows to cover that, up to a kilometer.
    """
    b = db.get(Building, building_id)
    dlat, dlon = lat - b.lat, lon - b.lon
    b.lat, b.lon = lat, lon
    base = next((x["radius_m"] for x in BUILDINGS if x["id"] == building_id), b.radius_m)
    b.radius_m = base if accuracy is None else max(base, min(accuracy, MAX_PLACED_RADIUS_M))
    for m in db.scalars(select(Merchant).where(Merchant.building_id == building_id)):
        m.lat += dlat
        m.lon += dlon
    db.commit()
    return b


def seed_history(db: Session, user: User, seed: str | None = None) -> int:
    """About 30 synthetic payments over six weeks at the cafe and the bookshop, none at the repair shop.

    Gives the risk engine something to compare against: a coffee or a book is
    routine, a large repair bill at a shop never visited is not.

    The made-up person usually shops around the hour the account was created, so a
    tester (or judge, in any time zone) paying right away is not flagged for an
    unusual time of day. A few classic hours are mixed in for variety.
    """
    db.execute(delete(PaymentHistory).where(PaymentHistory.user_id == user.id, PaymentHistory.synthetic.is_(True)))
    rng = random.Random(seed or user.id)
    tz = ZoneInfo(get_settings().timezone)
    today = now().astimezone(tz).replace(minute=0, second=0, microsecond=0)
    h = today.hour
    usual_hours = [(h + d) % 24 for d in (-2, -1, 0, 0, 0, 1, 1, 2)] + [9, 13, 18]
    count = 0
    for day in range(42, 0, -1):
        if rng.random() < 0.45:
            continue
        hour = rng.choice(usual_hours)
        when = (today - timedelta(days=day)).replace(hour=hour, minute=rng.randrange(60))
        if rng.random() < 0.7:
            shop, cents = "shop_kettle", rng.randrange(380, 900)
        else:
            shop, cents = "shop_lumen", rng.randrange(1200, 3600)
        db.add(PaymentHistory(user_id=user.id, merchant_id=shop, amount_cents=cents, at=when, synthetic=True))
        count += 1
    db.commit()
    return count
