"""Location and shop detection (technical report, section 11).

Addition to the report: an `open_invoice` feature. Shops in one building share
almost the same coordinates, and a first-time visitor has no history, so without
it every shop ties and the popup can never make a suggestion. A shop where the
cashier has just opened an invoice is by far the most likely one.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime

WEIGHTS = {"distance": 1.0, "floor": 1.0, "history": 0.8, "hours": 1.0, "time_of_day": 0.3, "open_invoice": 3.0}
CLOSED_PENALTY = -3.0
SUGGEST_MIN_P = 0.6
SUGGEST_MIN_LEAD = 0.2
MAX_ACCURACY_M = 100.0
EARTH_R = 6_371_000.0
FLOOR_HEIGHT_M = 3.5
FLOOR_SIGMA_M = 2.5


def haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * EARTH_R * math.asin(math.sqrt(a))


@dataclass
class ShopCandidate:
    id: str
    name: str
    floor: int
    lat: float
    lon: float
    radius_m: float
    open_hour: int
    close_hour: int
    visits: int
    hour_share: float  # user's share of visits to this shop at the current hour
    open_invoice: bool


def is_open(open_hour: int, close_hour: int, hour: int) -> bool:
    if open_hour <= close_hour:
        return open_hour <= hour < close_hour
    return hour >= open_hour or hour < close_hour  # past midnight


def shop_features(
    s: ShopCandidate,
    lat: float,
    lon: float,
    accuracy_m: float,
    hour: int,
    altitude_m: float | None = None,
    altitude_accuracy_m: float | None = None,
    ground_altitude_m: float | None = None,
) -> dict:
    d = haversine_m(lat, lon, s.lat, s.lon)
    f = {"distance": -(d**2) / (2 * (accuracy_m**2 + s.radius_m**2))}
    if (
        altitude_m is not None
        and altitude_accuracy_m is not None
        and altitude_accuracy_m < 5
        and ground_altitude_m is not None
    ):
        h = altitude_m - ground_altitude_m
        f["floor"] = -((h - s.floor * FLOOR_HEIGHT_M) ** 2) / (2 * FLOOR_SIGMA_M**2)
    else:
        f["floor"] = 0.0
    f["history"] = math.log1p(s.visits)
    f["hours"] = 0.0 if is_open(s.open_hour, s.close_hour, hour) else CLOSED_PENALTY
    f["time_of_day"] = math.log(max(s.hour_share, 0.01)) if s.visits else 0.0
    f["open_invoice"] = 1.0 if s.open_invoice else 0.0
    return f


def rank(shops: list[ShopCandidate], lat: float, lon: float, accuracy_m: float, now_local: datetime, **alt) -> dict:
    scored = []
    for s in shops:
        f = shop_features(s, lat, lon, accuracy_m, now_local.hour, **alt)
        scored.append((sum(WEIGHTS[k] * v for k, v in f.items()), s, f))
    if not scored:
        return {"ranked": [], "suggestion": None}
    top = max(x[0] for x in scored)
    exps = [math.exp(x[0] - top) for x in scored]
    total = sum(exps)
    ranked = sorted(
        (
            {"shop_id": s.id, "probability": e / total, "features": {k: round(v, 3) for k, v in f.items()}}
            for (sc, s, f), e in zip(scored, exps)
        ),
        key=lambda r: -r["probability"],
    )
    suggestion = None
    p1 = ranked[0]["probability"]
    p2 = ranked[1]["probability"] if len(ranked) > 1 else 0.0
    if p1 >= SUGGEST_MIN_P and p1 - p2 >= SUGGEST_MIN_LEAD:
        suggestion = ranked[0]["shop_id"]
    return {"ranked": ranked, "suggestion": suggestion}
