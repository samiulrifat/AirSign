import base64
import os
import tempfile
from pathlib import Path

_tmp = Path(tempfile.mkdtemp(prefix="airsign-test-"))
os.environ["AIRSIGN_DATABASE_URL"] = f"sqlite:///{_tmp / 'test.db'}"
os.environ["AIRSIGN_SECRET_KEY"] = "test-secret"
os.environ["AIRSIGN_MASTER_KEY"] = base64.b64encode(b"k" * 32).decode()
os.environ["AIRSIGN_PAYPAL_MODE"] = "mock"
os.environ["AIRSIGN_ALLOW_SIMULATION"] = "true"

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from airsign.main import app  # noqa: E402


@pytest.fixture(scope="session")
def app_client():
    with TestClient(app) as c:
        yield c


def new_client() -> TestClient:
    c = TestClient(app)
    c.__enter__()
    return c


@pytest.fixture
def client(app_client):
    """A fresh browser (own cookies) against the shared app."""
    return new_client()
