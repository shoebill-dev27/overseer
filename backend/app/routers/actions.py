"""Action API — create/confirm actions and query status (requires OPERATOR or higher).

Two-step confirmation: created as PENDING_CONFIRM, transitions to CONFIRMED on an
explicit confirm. The actual tmux operation is fetched and executed by the Local Agent.
"""

from datetime import datetime, timedelta, timezone

import aiosqlite
from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel

from ..audit import log_event
from ..auth import require_operator, require_viewer
from ..database import get_db

router = APIRouter(prefix="/api", tags=["actions"])

# Actions that can be created. Only SEND_TEXT carries a text_payload.
SUPPORTED_ACTIONS = {"SEND_Y", "SEND_N", "SEND_ENTER", "STOP", "SEND_TEXT"}

# Max text length for SEND_TEXT
MAX_TEXT_LENGTH = 1000

# TTL for actions left unconfirmed in PENDING_CONFIRM
ACTION_TTL_SECONDS = 120


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _parse(ts: str) -> datetime:
    dt = datetime.fromisoformat(ts)
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


class CreateAction(BaseModel):
    action_type: str
    idempotency_key: str
    text_payload: str | None = None


def _validate_payload(action_type: str, text_payload: str | None) -> str | None:
    """Validate and normalize text_payload per action type.

    SEND_TEXT requires non-empty text (newlines are forbidden to prevent multi-command
    injection). Other action types are not allowed to carry a text_payload.
    """
    if action_type == "SEND_TEXT":
        if text_payload is None or text_payload.strip() == "":
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_ENTITY,
                "SEND_TEXT requires a non-empty text_payload",
            )
        if len(text_payload) > MAX_TEXT_LENGTH:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_ENTITY,
                f"text_payload exceeds {MAX_TEXT_LENGTH} characters",
            )
        if "\n" in text_payload or "\r" in text_payload:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_ENTITY,
                "text_payload must not contain newlines",
            )
        return text_payload
    if text_payload is not None:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            f"{action_type} does not accept a text_payload",
        )
    return None


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
    if payload.action_type not in SUPPORTED_ACTIONS:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            f"Unsupported action type: {payload.action_type}",
        )

    text_payload = _validate_payload(payload.action_type, payload.text_payload)

    session = await (
        await db.execute("SELECT id FROM claude_sessions WHERE id = ?", (session_id,))
    ).fetchone()
    if session is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Session not found")

    # Idempotency: if an action with the same key exists, return it (no duplicate)
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
            (session_id, user_id, action_type, text_payload, status,
             idempotency_key, created_at)
        VALUES (?, ?, ?, ?, 'PENDING_CONFIRM', ?, ?)
        """,
        (
            session_id,
            user["id"],
            payload.action_type,
            text_payload,
            payload.idempotency_key,
            now_iso,
        ),
    )
    await db.commit()

    row = await (
        await db.execute(
            "SELECT * FROM actions WHERE idempotency_key = ?",
            (payload.idempotency_key,),
        )
    ).fetchone()

    ip = request.client.host if request.client else None
    # Do not keep raw text in the audit log (length only, to avoid widening secret exposure).
    detail = {"action_type": payload.action_type, "action_id": row["id"]}
    if text_payload is not None:
        detail["text_length"] = len(text_payload)
    await log_event(
        db,
        "CREATE_ACTION",
        user_id=user["id"],
        session_id=session_id,
        detail=detail,
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

    # Expiry check
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
