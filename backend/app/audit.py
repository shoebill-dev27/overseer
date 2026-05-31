"""Audit log helper. INSERT-only — never UPDATE or DELETE audit_log."""

import json
import aiosqlite
from typing import Any


async def log_event(
    db: aiosqlite.Connection,
    event_type: str,
    user_id: int | None = None,
    session_id: int | None = None,
    detail: dict[str, Any] | None = None,
    ip_address: str | None = None,
    user_agent: str | None = None,
) -> None:
    detail_json = json.dumps(detail) if detail is not None else None
    await db.execute(
        """
        INSERT INTO audit_log
            (event_type, user_id, session_id, detail, ip_address, user_agent)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (event_type, user_id, session_id, detail_json, ip_address, user_agent),
    )
    await db.commit()
