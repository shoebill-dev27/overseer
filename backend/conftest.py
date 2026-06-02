"""Shared pytest configuration.

Set the required secrets and a temporary DB into environment variables before
importing the app. (Order matters because config.py / database.py read env vars at
import time.)
"""

import hashlib
import hmac
import os
import sqlite3
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

_BACKEND_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(_BACKEND_DIR))

os.environ.setdefault("SECRET_KEY", "s" * 64)
os.environ.setdefault("AGENT_HMAC_SECRET", "h" * 64)
os.environ.setdefault("APP_BASE_URL", "http://127.0.0.1:8000")

_TEST_DB = _BACKEND_DIR / "tests" / "_test.db"
os.environ["DATABASE_PATH"] = str(_TEST_DB)

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

AGENT_HMAC_SECRET = os.environ["AGENT_HMAC_SECRET"]


def _clear_db() -> None:
    for suffix in ("", "-wal", "-shm"):
        p = Path(str(_TEST_DB) + suffix)
        if p.exists():
            p.unlink()


@pytest.fixture
def client():
    """Return a TestClient started from an empty DB for each test."""
    _clear_db()
    from app.main import app  # lifespan runs init_db to create the schema
    from app.routers.internal import require_loopback

    # The TestClient's source is fixed to "testclient" and fails the loopback check,
    # so disable only the /internal loopback restriction (HMAC verification stays real).
    app.dependency_overrides[require_loopback] = lambda: None
    try:
        with TestClient(app) as c:
            yield c
    finally:
        app.dependency_overrides.pop(require_loopback, None)
    _clear_db()


@pytest.fixture
def make_user():
    """Create a user + a valid http_session directly in the DB and return the session token."""

    def _make(
        role: str = "VIEWER", github_id: str = "1001", github_login: str = "tester"
    ) -> str:
        token = f"test-token-{role}-{github_id}"
        expires = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
        con = sqlite3.connect(os.environ["DATABASE_PATH"])
        con.execute(
            "INSERT INTO users (github_id, github_login, role) VALUES (?, ?, ?)",
            (github_id, github_login, role),
        )
        uid = con.execute(
            "SELECT id FROM users WHERE github_id = ?", (github_id,)
        ).fetchone()[0]
        con.execute(
            "INSERT INTO http_sessions (token, user_id, expires_at) VALUES (?, ?, ?)",
            (token, uid, expires),
        )
        con.commit()
        con.close()
        return token

    return _make


def sign_agent(body: bytes, timestamp: str | None = None) -> dict[str, str]:
    """Generate HMAC headers for the Internal API (same scheme as agent/client.py)."""
    ts = timestamp or str(int(time.time()))
    sig = hmac.new(
        AGENT_HMAC_SECRET.encode(),
        f"{ts}:".encode() + body,
        hashlib.sha256,
    ).hexdigest()
    return {
        "Content-Type": "application/json",
        "X-Agent-Timestamp": ts,
        "X-Agent-Signature": sig,
    }
