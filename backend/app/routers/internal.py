"""Internal API for Local Agent communication.

Bound to 127.0.0.1 only (enforced in main.py via a separate server).
Authenticated with HMAC-SHA256 shared secret.
"""

import hashlib
import hmac
import time
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel

import aiosqlite

from ..config import settings
from ..database import get_db
from ..scrubber import scrub
from ..ws_manager import manager as ws_manager

router = APIRouter(prefix="/internal", tags=["internal"])

_MAX_SNAPSHOT_LINES = 100
_MAX_SNAPSHOT_BYTES = 64 * 1024  # 64KB


# ── HMAC verification ─────────────────────────────────────────────────────────


async def verify_agent_auth(request: Request) -> None:
    timestamp_str = request.headers.get("X-Agent-Timestamp", "")
    signature = request.headers.get("X-Agent-Signature", "")

    try:
        ts = int(timestamp_str)
    except ValueError:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Invalid timestamp")

    if abs(time.time() - ts) > 300:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Timestamp too old")

    body = await request.body()
    expected = hmac.new(
        settings.agent_hmac_secret.encode(),
        f"{timestamp_str}:".encode() + body,
        hashlib.sha256,
    ).hexdigest()

    if not hmac.compare_digest(expected, signature):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Invalid signature")


# ── Schemas ───────────────────────────────────────────────────────────────────


class SessionUpdate(BaseModel):
    tmux_name: str
    status: str  # RUNNING | WAITING_FOR_INPUT | ERROR | FINISHED
    waiting_category: str | None = None
    waiting_pattern: str | None = None
    snapshot_lines: list[str] | None = None


class HeartbeatPayload(BaseModel):
    agent_version: str = "unknown"


# ── Endpoints ─────────────────────────────────────────────────────────────────


@router.post("/sessions/update", dependencies=[Depends(verify_agent_auth)])
async def update_session(
    payload: SessionUpdate,
    db: aiosqlite.Connection = Depends(get_db),
) -> dict:
    valid_statuses = {"RUNNING", "WAITING_FOR_INPUT", "ERROR", "FINISHED"}
    if payload.status not in valid_statuses:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Invalid status")

    # Upsert claude_session
    existing = await (
        await db.execute(
            "SELECT id, status FROM claude_sessions WHERE tmux_name = ?",
            (payload.tmux_name,),
        )
    ).fetchone()

    now_iso = datetime.now(timezone.utc).isoformat()

    if existing is None:
        await db.execute(
            """
            INSERT INTO claude_sessions
                (tmux_name, status, waiting_category, waiting_pattern, started_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                payload.tmux_name,
                payload.status,
                payload.waiting_category,
                payload.waiting_pattern,
                now_iso,
            ),
        )
        await db.commit()
        session_row = await (
            await db.execute(
                "SELECT id FROM claude_sessions WHERE tmux_name = ?",
                (payload.tmux_name,),
            )
        ).fetchone()
        session_id = session_row["id"]
        prev_status = None
    else:
        session_id = existing["id"]
        prev_status = existing["status"]
        await db.execute(
            """
            UPDATE claude_sessions SET
                status = ?, waiting_category = ?, waiting_pattern = ?,
                last_updated_at = ?
            WHERE id = ?
            """,
            (
                payload.status,
                payload.waiting_category,
                payload.waiting_pattern,
                now_iso,
                session_id,
            ),
        )
        if payload.status == "FINISHED":
            await db.execute(
                "UPDATE claude_sessions SET finished_at = ? WHERE id = ?",
                (now_iso, session_id),
            )
        await db.commit()

    # Upsert snapshot (scrub + truncate)
    if payload.snapshot_lines is not None:
        lines = payload.snapshot_lines[-_MAX_SNAPSHOT_LINES:]
        scrubbed = [scrub(line) for line in lines]
        content = "\n".join(scrubbed)
        truncated = len(content.encode()) > _MAX_SNAPSHOT_BYTES
        if truncated:
            content = content.encode()[:_MAX_SNAPSHOT_BYTES].decode(errors="replace")

        await db.execute(
            """
            INSERT OR REPLACE INTO session_snapshots
                (session_id, content, line_count, truncated, captured_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (session_id, content, len(scrubbed), truncated, now_iso),
        )
        await db.commit()

    # Broadcast to WebSocket clients
    await ws_manager.broadcast(
        {
            "type": "session_update",
            "session_id": session_id,
            "tmux_name": payload.tmux_name,
            "status": payload.status,
            "waiting_category": payload.waiting_category,
            "prev_status": prev_status,
        }
    )

    return {"ok": True, "session_id": session_id, "prev_status": prev_status}


@router.post("/heartbeat", dependencies=[Depends(verify_agent_auth)])
async def heartbeat(
    payload: HeartbeatPayload,
    db: aiosqlite.Connection = Depends(get_db),
) -> dict:
    now_iso = datetime.now(timezone.utc).isoformat()
    await db.execute(
        """
        INSERT INTO agent_status (id, last_seen_at, agent_version, status)
        VALUES (1, ?, ?, 'ONLINE')
        ON CONFLICT(id) DO UPDATE SET
            last_seen_at = excluded.last_seen_at,
            agent_version = excluded.agent_version,
            status = 'ONLINE'
        """,
        (now_iso, payload.agent_version),
    )
    await db.commit()
    return {"ok": True}
