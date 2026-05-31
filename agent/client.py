"""HMAC-signed HTTP client for communicating with the backend Internal API."""

import hashlib
import hmac
import json
import os
import time

import httpx
from dotenv import find_dotenv, load_dotenv

load_dotenv(find_dotenv())

_BACKEND_URL = os.getenv("BACKEND_URL", "http://127.0.0.1:8000")
_HMAC_SECRET = os.getenv("AGENT_HMAC_SECRET", "")
_TIMEOUT = float(os.getenv("AGENT_REQUEST_TIMEOUT", "5"))

AGENT_VERSION = "0.1.0"


def _sign(body: bytes) -> tuple[str, str]:
    """Return (timestamp, hmac_hex) for the given body."""
    timestamp = str(int(time.time()))
    sig = hmac.new(
        _HMAC_SECRET.encode(),
        f"{timestamp}:".encode() + body,
        hashlib.sha256,
    ).hexdigest()
    return timestamp, sig


def _headers(body: bytes) -> dict[str, str]:
    ts, sig = _sign(body)
    return {
        "Content-Type": "application/json",
        "X-Agent-Timestamp": ts,
        "X-Agent-Signature": sig,
    }


async def push_session_update(
    tmux_name: str,
    status: str,
    waiting_category: str | None = None,
    waiting_pattern: str | None = None,
    snapshot_lines: list[str] | None = None,
) -> bool:
    payload = {
        "tmux_name": tmux_name,
        "status": status,
        "waiting_category": waiting_category,
        "waiting_pattern": waiting_pattern,
        "snapshot_lines": snapshot_lines,
    }
    body = json.dumps(payload).encode()
    async with httpx.AsyncClient(timeout=_TIMEOUT) as c:
        resp = await c.post(
            f"{_BACKEND_URL}/internal/sessions/update",
            content=body,
            headers=_headers(body),
        )
    return resp.status_code == 200


async def send_heartbeat() -> bool:
    payload = {"agent_version": AGENT_VERSION}
    body = json.dumps(payload).encode()
    async with httpx.AsyncClient(timeout=_TIMEOUT) as c:
        resp = await c.post(
            f"{_BACKEND_URL}/internal/heartbeat",
            content=body,
            headers=_headers(body),
        )
    return resp.status_code == 200


async def fetch_pending_actions() -> list[dict]:
    """実行待ち（CONFIRMED）の操作一覧を取得する。失敗時は空リスト。"""
    body = b""  # GET なのでボディは空。HMAC は空ボディに対して署名する。
    async with httpx.AsyncClient(timeout=_TIMEOUT) as c:
        resp = await c.get(
            f"{_BACKEND_URL}/internal/actions/pending",
            headers=_headers(body),
        )
    if resp.status_code != 200:
        return []
    return resp.json().get("actions", [])


async def report_action_result(
    action_id: int,
    status: str,
    failure_reason: str | None = None,
) -> bool:
    payload = {"status": status, "failure_reason": failure_reason}
    body = json.dumps(payload).encode()
    async with httpx.AsyncClient(timeout=_TIMEOUT) as c:
        resp = await c.post(
            f"{_BACKEND_URL}/internal/actions/{action_id}/result",
            content=body,
            headers=_headers(body),
        )
    return resp.status_code == 200


async def check_backend_reachable() -> bool:
    try:
        async with httpx.AsyncClient(timeout=3) as c:
            resp = await c.get(f"{_BACKEND_URL}/health")
        return resp.status_code == 200
    except Exception:
        return False
