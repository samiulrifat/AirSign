"""Brute-force protection on the cashier PIN and the six-digit check-in code."""

import pytest

from airsign.ratelimit import limiter

from .conftest import new_client


@pytest.fixture
def limits_on():
    limiter.enabled = True
    limiter.reset()
    yield
    limiter.enabled = None
    limiter.reset()


def test_pos_pin_brute_force_is_limited(limits_on):
    c = new_client()
    codes = [c.post("/api/pos/login", json={"merchant_id": "shop_lumen", "pin": f"{i:04d}"}).status_code for i in range(12)]
    assert codes[:10] == [401] * 10
    assert codes[10:] == [429, 429]
    r = c.post("/api/pos/login", json={"merchant_id": "shop_lumen", "pin": "1234"})
    assert r.status_code == 429 and int(r.headers["Retry-After"]) > 0
    assert r.json()["code"] == "rate_limited"


def test_checkin_code_guessing_is_limited(limits_on):
    c = new_client()
    c.post("/api/session", json={"display_name": "Eve", "consent": True})
    codes = [c.post("/api/checkin", json={"code": f"{i:06d}"}).status_code for i in range(22)]
    assert 429 in codes and codes.index(429) == 20
