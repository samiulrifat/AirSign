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


def move_building(db: Session, building_id: str, lat: float, lon: float) -> Building:
    """Simulation helper: place a demo building (and its shops) where the tester stands."""
    b = db.get(Building, building_id)
    dlat, dlon = lat - b.lat, lon - b.lon
    b.lat, b.lon = lat, lon
    for m in db.scalars(select(Merchant).where(Merchant.building_id == building_id)):
        m.lat += dlat
        m.lon += dlon
    db.commit()
    return b


def seed_history(db: Session, user: User, seed: str | None = None) -> int:
    """About 30 synthetic payments over six weeks at the cafe and the bookshop, none at the repair shop.

    Gives the risk engine something to compare against: a coffee or a book is
    routine, a large repair bill at a shop never visited is not.
    """
    db.execute(delete(PaymentHistory).where(PaymentHistory.user_id == user.id, PaymentHistory.synthetic.is_(True)))
    rng = random.Random(seed or user.id)
    tz = ZoneInfo(get_settings().timezone)
    today = now().astimezone(tz).replace(minute=0, second=0, microsecond=0)
    count = 0
    for day in range(42, 0, -1):
        if rng.random() < 0.45:
            continue
        hour = rng.choice([8, 8, 9, 12, 13, 17, 18])
        when = (today - timedelta(days=day)).replace(hour=hour, minute=rng.randrange(60))
        if rng.random() < 0.7:
            shop, cents = "shop_kettle", rng.randrange(380, 900)
        else:
            shop, cents = "shop_lumen", rng.randrange(1200, 3600)
        db.add(PaymentHistory(user_id=user.id, merchant_id=shop, amount_cents=cents, at=when, synthetic=True))
        count += 1
    db.commit()
    return count
