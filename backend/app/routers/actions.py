"""Action API — 操作の作成・確認・状態取得（OPERATOR 以上が必要）。

二段階確認: 作成時は PENDING_CONFIRM、明示的な confirm で CONFIRMED に遷移。
実際の tmux 操作は Local Agent が CONFIRMED を取得して実行する。
"""

from datetime import datetime, timedelta, timezone

import aiosqlite
from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel

from ..audit import log_event
from ..auth import require_operator, require_viewer
from ..database import get_db

router = APIRouter(prefix="/api", tags=["actions"])

# Phase 2 で実行可能なアクション（SEND_TEXT は Phase 3）
PHASE2_ACTIONS = {"SEND_Y", "SEND_N", "SEND_ENTER", "STOP"}

# PENDING_CONFIRM のまま確認されなかった操作の有効期限
ACTION_TTL_SECONDS = 120


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _parse(ts: str) -> datetime:
    dt = datetime.fromisoformat(ts)
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


class CreateAction(BaseModel):
    action_type: str
    idempotency_key: str


def _fmt_action(row: aiosqlite.Row) -> dict:
    return {
        "id": row["id"],
        "session_id": row["session_id"],
        "action_type": row["action_type"],
        "status": row["status"],
        "created_at": row["created_at"],
        "confirmed_at": row["confirmed_at"],
        "executed_at": row["executed_at"],
        "failure_reason": row["failure_reason"],
    }


@router.post("/sessions/{session_id}/actions")
async def create_action(
    session_id: int,
    payload: CreateAction,
    request: Request,
    user: aiosqlite.Row = Depends(require_operator),
    db: aiosqlite.Connection = Depends(get_db),
) -> dict:
    if payload.action_type not in PHASE2_ACTIONS:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            f"Unsupported action type: {payload.action_type}",
        )

    session = await (
        await db.execute("SELECT id FROM claude_sessions WHERE id = ?", (session_id,))
    ).fetchone()
    if session is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Session not found")

    # 冪等性: 同一キーの操作が既にあればそれを返す（重複作成しない）
    existing = await (
        await db.execute(
            "SELECT * FROM actions WHERE idempotency_key = ?",
            (payload.idempotency_key,),
        )
    ).fetchone()
    if existing is not None:
        return _fmt_action(existing)

    now_iso = _now().isoformat()
    await db.execute(
        """
        INSERT INTO actions
            (session_id, user_id, action_type, status, idempotency_key, created_at)
        VALUES (?, ?, ?, 'PENDING_CONFIRM', ?, ?)
        """,
        (session_id, user["id"], payload.action_type, payload.idempotency_key, now_iso),
    )
    await db.commit()

    row = await (
        await db.execute(
            "SELECT * FROM actions WHERE idempotency_key = ?",
            (payload.idempotency_key,),
        )
    ).fetchone()

    ip = request.client.host if request.client else None
    await log_event(
        db,
        "CREATE_ACTION",
        user_id=user["id"],
        session_id=session_id,
        detail={"action_type": payload.action_type, "action_id": row["id"]},
        ip_address=ip,
    )

    return _fmt_action(row)


@router.post("/actions/{action_id}/confirm")
async def confirm_action(
    action_id: int,
    request: Request,
    user: aiosqlite.Row = Depends(require_operator),
    db: aiosqlite.Connection = Depends(get_db),
) -> dict:
    row = await (
        await db.execute("SELECT * FROM actions WHERE id = ?", (action_id,))
    ).fetchone()
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Action not found")

    if row["status"] != "PENDING_CONFIRM":
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"Action is not pending confirmation (status: {row['status']})",
        )

    # 期限切れ判定
    if _now() - _parse(row["created_at"]) > timedelta(seconds=ACTION_TTL_SECONDS):
        await db.execute(
            "UPDATE actions SET status = 'EXPIRED' WHERE id = ?", (action_id,)
        )
        await db.commit()
        raise HTTPException(status.HTTP_409_CONFLICT, "Action expired")

    now_iso = _now().isoformat()
    await db.execute(
        "UPDATE actions SET status = 'CONFIRMED', confirmed_at = ? WHERE id = ?",
        (now_iso, action_id),
    )
    await db.commit()

    ip = request.client.host if request.client else None
    await log_event(
        db,
        "CONFIRM_ACTION",
        user_id=user["id"],
        session_id=row["session_id"],
        detail={"action_type": row["action_type"], "action_id": action_id},
        ip_address=ip,
    )

    return {"id": action_id, "status": "CONFIRMED"}


@router.get("/actions/{action_id}")
async def get_action(
    action_id: int,
    user: aiosqlite.Row = Depends(require_viewer),
    db: aiosqlite.Connection = Depends(get_db),
) -> dict:
    row = await (
        await db.execute("SELECT * FROM actions WHERE id = ?", (action_id,))
    ).fetchone()
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Action not found")
    return _fmt_action(row)
