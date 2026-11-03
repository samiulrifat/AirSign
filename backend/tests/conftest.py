import base64
import os
import tempfile
from pathlib import Path

_tmp = Path(tempfile.mkdtemp(prefix="airsign-test-"))
# AIRSIGN_TEST_DATABASE_URL runs the suite against another database, e.g. Postgres.
os.environ["AIRSIGN_DATABASE_URL"] = os.environ.get("AIRSIGN_TEST_DATABASE_URL", f"sqlite:///{_tmp / 'test.db'}")
os.environ["AIRSIGN_SECRET_KEY"] = "test-secret"
os.environ["AIRSIGN_MASTER_KEY"] = base64.b64encode(b"k" * 32).decode()
os.environ["AIRSIGN_PAYPAL_MODE"] = "mock"
os.environ["AIRSIGN_ALLOW_SIMULATION"] = "true"
os.environ["AIRSIGN_LAB_CODE"] = "test-lab"
os.environ["AIRSIGN_RATE_LIMITS"] = "false"  # test_ratelimit.py turns them on
os.environ["AIRSIGN_GEMINI_API_KEY"] = ""  # never call Gemini from tests, even with a key in .env

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from airsign.main import app  # noqa: E402


@pytest.fixture(scope="session")
def app_client():
    with TestClient(app) as c:
        yield c


@pytest.fixture(autouse=True)
def clean_counter(app_client):
    """Open bills and nearby customers from earlier tests would change who and what later ones find."""
    from airsign.db import SessionLocal
    from airsign.models import Invoice, Presence

    with SessionLocal() as db:
        db.query(Invoice).filter(Invoice.status == "OPEN").update({Invoice.status: "CANCELLED"})
        db.query(Presence).delete()
        db.commit()


def new_client() -> TestClient:
    c = TestClient(app)
    c.__enter__()
    return c


@pytest.fixture
def client(app_client):
    """A fresh browser (own cookies) against the shared app."""
    return new_client()
