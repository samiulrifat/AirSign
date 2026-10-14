"""Risk engine (technical report, section 10).

Rule score R_rule is the weighted sum from the report. The learned second opinion
is an isolation forest fitted on the user's own payment history.

Deviation from the report, on purpose: the report rescales the isolation-forest
anomaly score s in (0, 1) as R_ml = 100 s. Ordinary payments already score about
0.5 on that scale, so R = max(R_rule, R_ml) would put almost every payment in
tier 2. We calibrate instead: q is the share of the user's own history that looks
more normal than this payment, and R_ml only rises once q passes 0.9.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime, timedelta

import numpy as np

WEIGHTS = {"amt": 25, "new": 20, "hour": 10, "vel": 15, "loc": 20, "dev": 10}
TIER2_AT = 30
TIER3_AT = 60
DEFAULT_MEDIAN_CENTS = 2500
MIN_HISTORY_FOR_HOUR = 10
MIN_HISTORY_FOR_ML = 20
ML_START_QUANTILE = 0.9


@dataclass
class PastPayment:
    merchant_id: str
    amount_cents: int
    at: datetime  # local time


@dataclass
class RiskInput:
    amount_cents: int
    merchant_id: str
    now: datetime  # local time
    history: list[PastPayment]
    location_verified: bool
    distance_m: float | None
    geofence_m: float
    accuracy_m: float | None
    new_device: bool


@dataclass
class RiskResult:
    score: float
    rule_score: float
    ml_score: float | None
    tier: int
    features: dict
    contributions: dict
    explanation: str
    reasons: list[str] = field(default_factory=list)


def _clip(x: float) -> float:
    return min(1.0, max(0.0, x))


def features(inp: RiskInput) -> tuple[dict, dict]:
    """Feature values in [0, 1] and the context needed to explain them."""
    ctx: dict = {}
    amounts = [p.amount_cents for p in inp.history[-20:]]
    median = float(np.median(amounts)) if amounts else DEFAULT_MEDIAN_CENTS
    ctx["median_cents"] = median
    ctx["amount_ratio"] = inp.amount_cents / max(median, 1)
    f_amt = _clip(math.log2(max(inp.amount_cents, 1) / max(median, 1)) / 3)

    f_new = 0.0 if any(p.merchant_id == inp.merchant_id for p in inp.history) else 1.0

    f_hour = 0.0
    if len(inp.history) >= MIN_HISTORY_FOR_HOUR:
        share = sum(1 for p in inp.history if p.at.hour == inp.now.hour) / len(inp.history)
        f_hour = 1.0 if share < 0.05 else 0.0

    recent = sum(1 for p in inp.history if inp.now - timedelta(hours=1) <= p.at <= inp.now)
    ctx["recent_payments"] = recent
    f_vel = _clip(recent / 4)  # (n - 1) / 4 with n counting this payment

    if inp.location_verified:
        f_loc = 0.0
        ctx["location"] = "verified"
    elif inp.distance_m is None:
        f_loc = 0.5
        ctx["location"] = "unknown"
    else:
        limit = inp.geofence_m + (inp.accuracy_m or 0.0)
        f_loc = _clip(inp.distance_m / max(limit, 1.0)) if inp.distance_m <= limit else 1.0
        ctx["location"] = "far" if f_loc >= 1.0 else "near"

    f_dev = 1.0 if inp.new_device else 0.0
    return {"amt": f_amt, "new": f_new, "hour": f_hour, "vel": f_vel, "loc": f_loc, "dev": f_dev}, ctx


def _ml_vector(amount_cents: int, at: datetime, merchant_visits: int, recent: int) -> list[float]:
    angle = 2 * math.pi * (at.hour + at.minute / 60) / 24
    return [math.log1p(amount_cents / 100), math.sin(angle), math.cos(angle), math.log1p(merchant_visits), recent]


def ml_score(inp: RiskInput) -> float | None:
    if len(inp.history) < MIN_HISTORY_FOR_ML:
        return None
    from sklearn.ensemble import IsolationForest

    hist = sorted(inp.history, key=lambda p: p.at)
    rows = []
    visits: dict[str, int] = {}
    for i, p in enumerate(hist):
        recent = sum(1 for q in hist[:i] if p.at - timedelta(hours=1) <= q.at)
        rows.append(_ml_vector(p.amount_cents, p.at, visits.get(p.merchant_id, 0), recent))
        visits[p.merchant_id] = visits.get(p.merchant_id, 0) + 1
    X = np.array(rows)
    model = IsolationForest(n_estimators=100, random_state=0).fit(X)
    train = model.score_samples(X)  # higher is more normal
    recent_now = sum(1 for p in hist if inp.now - timedelta(hours=1) <= p.at <= inp.now)
    x = np.array([_ml_vector(inp.amount_cents, inp.now, visits.get(inp.merchant_id, 0), recent_now)])
    s = float(model.score_samples(x)[0])
    q = float(np.mean(train > s))  # share of history more normal than this payment
    if q <= ML_START_QUANTILE:
        return 0.0
    return TIER3_AT * (q - ML_START_QUANTILE) / (1 - ML_START_QUANTILE)


def explain(f: dict, ctx: dict, tier: int, ml: float | None, rule: float) -> tuple[str, list[str]]:
    """One plain sentence from templates, built from the largest contributions."""
    phrases = {
        "amt": f"the amount is {ctx['amount_ratio']:.0f} times your usual" if ctx["amount_ratio"] >= 1.5 else "the amount is above your usual",
        "new": "this is a new shop for you",
        "hour": "you rarely pay at this time of day",
        "vel": f"you made {ctx['recent_payments']} payments in the last hour",
        "loc": "we could not confirm you are at the shop" if ctx.get("location") != "far" else "your location does not match the shop",
        "dev": "this is a new device",
    }
    contrib = sorted(((WEIGHTS[k] * v, k) for k, v in f.items() if v > 0), reverse=True)
    reasons = [phrases[k] for c, k in contrib if c >= 5][:2]
    if ml is not None and ml > rule and ml >= TIER2_AT:
        reasons.insert(0, "this payment looks unusual compared with your history")
        reasons = reasons[:2]
    if tier == 1:
        return "Routine payment, one signature is enough.", reasons
    why = " and ".join(reasons) if reasons else "this payment is unusual"
    if tier == 2:
        return f"Stricter check because {why}.", reasons
    return f"Two signatures needed because {why}.", reasons


def assess(inp: RiskInput) -> RiskResult:
    f, ctx = features(inp)
    contributions = {k: WEIGHTS[k] * v for k, v in f.items()}
    rule = float(sum(contributions.values()))
    ml = ml_score(inp)
    score = max(rule, ml or 0.0)
    tier = 1 if score < TIER2_AT else 2 if score < TIER3_AT else 3
    explanation, reasons = explain(f, ctx, tier, ml, rule)
    return RiskResult(
        score=round(score, 1),
        rule_score=round(rule, 1),
        ml_score=None if ml is None else round(ml, 1),
        tier=tier,
        features={k: round(v, 3) for k, v in f.items()},
        contributions={k: round(v, 1) for k, v in contributions.items()},
        explanation=explanation,
        reasons=reasons,
    )
