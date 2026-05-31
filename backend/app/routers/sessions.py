"""Viewer API — session list, detail, and snapshot (VIEWER role required)."""

from datetime import datetime, timedelta, timezone

import aiosqlite
from fastapi import APIRouter, Depends, HTTPException, Request, status

from ..audit import log_event
from ..auth import require_viewer
from ..database import get_db

router = APIRouter(prefix="/api", tags=["sessions"])

_AGENT_OFFLINE_THRESHOLD_MINUTES = 5


@router.get("/sessions")
async def list_sessions(
    user: aiosqlite.Row = Depends(require_viewer),
    db: aiosqlite.Connection = Depends(get_db),
) -> dict:
    rows = await (
        await db.execute(
            """
            SELECT id, tmux_name, status, waiting_category,
                   started_at, last_updated_at, finished_at
            FROM claude_sessions
            ORDER BY last_updated_at DESC
            """
        )
    ).fetchall()

    agent_status = await _get_agent_status(db)

    return {
        "sessions": [_fmt_session(r) for r in rows],
        "agent": agent_status,
    }


@router.get("/sessions/{session_id}")
async def get_session(
    session_id: int,
    request: Request,
    user: aiosqlite.Row = Depends(require_viewer),
    db: aiosqlite.Connection = Depends(get_db),
) -> dict:
    row = await (
        await db.execute(
            "SELECT * FROM claude_sessions WHERE id = ?", (session_id,)
        )
    ).fetchone()

    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Session not found")

    ip = request.client.host if request.client else None
    await log_event(
        db, "VIEW_SESSION", user_id=user["id"], session_id=session_id, ip_address=ip
    )

    return _fmt_session(row)


@router.get("/sessions/{session_id}/snapshot")
async def get_snapshot(
    session_id: int,
    request: Request,
    user: aiosqlite.Row = Depends(require_viewer),
    db: aiosqlite.Connection = Depends(get_db),
) -> dict:
    session = await (
        await db.execute(
            "SELECT id FROM claude_sessions WHERE id = ?", (session_id,)
        )
    ).fetchone()

    if session is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Session not found")

    snap = await (
        await db.execute(
            "SELECT content, line_count, truncated, captured_at FROM session_snapshots WHERE session_id = ?",
            (session_id,),
        )
    ).fetchone()

    ip = request.client.host if request.client else None
    await log_event(
        db, "VIEW_SNAPSHOT", user_id=user["id"], session_id=session_id, ip_address=ip
    )

    if snap is None:
        return {"content": "", "line_count": 0, "truncated": False, "captured_at": None}

    return {
        "content": snap["content"],
        "line_count": snap["line_count"],
        "truncated": snap["truncated"],
        "captured_at": snap["captured_at"],
    }


async def _get_agent_status(db: aiosqlite.Connection) -> dict:
    row = await (
        await db.execute(
            "SELECT last_seen_at, agent_version, status FROM agent_status WHERE id = 1"
        )
    ).fetchone()

    if row is None:
        return {"status": "NEVER_CONNECTED", "last_seen_at": None, "agent_version": None}

    last_seen = datetime.fromisoformat(row["last_seen_at"])
    if last_seen.tzinfo is None:
        last_seen = last_seen.replace(tzinfo=timezone.utc)

    threshold = datetime.now(timezone.utc) - timedelta(minutes=_AGENT_OFFLINE_THRESHOLD_MINUTES)
    is_offline = last_seen < threshold

    return {
        "status": "OFFLINE" if is_offline else "ONLINE",
        "last_seen_at": row["last_seen_at"],
        "agent_version": row["agent_version"],
    }


def _fmt_session(row: aiosqlite.Row) -> dict:
    return {
        "id": row["id"],
        "tmux_name": row["tmux_name"],
        "status": row["status"],
        "waiting_category": row["waiting_category"],
        "started_at": row["started_at"],
        "last_updated_at": row["last_updated_at"],
        "finished_at": row["finished_at"],
    }
