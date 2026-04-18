"""
Shared fixtures for the AURA test suite.

The session-scoped `app_client` fixture replaces the module-level database
with an isolated test DB so tests never touch data/aura.db.
The generation loop runs normally but against the test DB.
"""

import json
import os
import sys
import tempfile
from pathlib import Path

import pytest

# Ensure project root is importable
ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

_SETTINGS_FILE = ROOT / "data" / "settings.json"


@pytest.fixture(scope="session", autouse=True)
def _preserve_settings():
    """Snapshot data/settings.json before tests and restore it after.
    Prevents test PATCH /api/settings/* calls from permanently changing
    tick_interval_seconds, jwt_secret, etc. in the real settings file."""
    snapshot = None
    if _SETTINGS_FILE.exists():
        snapshot = _SETTINGS_FILE.read_text()
    yield
    if snapshot is not None:
        _SETTINGS_FILE.write_text(snapshot)
    elif _SETTINGS_FILE.exists():
        _SETTINGS_FILE.unlink()  # file didn't exist before — remove it


# ---------------------------------------------------------------------------
# Isolated database fixture
# ---------------------------------------------------------------------------

@pytest.fixture(scope="session")
def test_db_path(tmp_path_factory):
    return str(tmp_path_factory.mktemp("db") / "test_aura.db")


@pytest.fixture(scope="session")
def test_db(test_db_path):
    from app.database import Database
    return Database(test_db_path)


# ---------------------------------------------------------------------------
# App / HTTP client
# ---------------------------------------------------------------------------

@pytest.fixture(scope="session")
def app_client(test_db):
    """TestClient wired to an isolated test database."""
    import main as m
    from fastapi.testclient import TestClient

    # Swap to test DB so no production data is touched
    original_db = m.db
    m.db = test_db

    with TestClient(m.app, raise_server_exceptions=False) as client:
        yield client

    m.db = original_db


@pytest.fixture(scope="session")
def auth_token(app_client):
    """Valid JWT for protected endpoints (uses default 'admin' password)."""
    r = app_client.post("/api/auth/login", json={"password": "admin"})
    assert r.status_code == 200, f"Login failed: {r.text}"
    return r.json()["token"]


@pytest.fixture(scope="session")
def auth_headers(auth_token):
    return {"Authorization": f"Bearer {auth_token}"}


# ---------------------------------------------------------------------------
# Seed data helper
# ---------------------------------------------------------------------------

@pytest.fixture(scope="session")
def seeded_db(test_db):
    """Insert a few rows so read-path tests have something to query."""
    from app import constants
    from datetime import datetime

    ts = datetime.now().isoformat()
    reading = {p: (lo + hi) / 2 for p, (lo, hi) in constants.PARAMETER_NOMINAL_RANGES.items()}

    for loc in constants.LOCATIONS[:3]:
        row_id = test_db.insert_data(reading, loc, ts)
        test_db.insert_label(row_id, 1, None)

    return test_db
