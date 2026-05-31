"""HTTP session management (cookie-based, server-side)."""

import secrets
from datetime import datetime, timedelta, timezone

import aiosqlite

SESSION_TTL_HOURS = 24
COOKIE_NAME = "overseer_session"


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _expires_at() -> datetime:
    return _now() + timedelta(hours=SESSION_TTL_HOURS)


async def create_session(
    db: aiosqlite.Connection,
    user_id: int,
    ip_address: str | None,
    user_agent: str | None,
) -> str:
    token = secrets.token_urlsafe(32)
    await db.execute(
        """
        INSERT INTO http_sessions (token, user_id, expires_at, ip_address, user_agent)
        VALUES (?, ?, ?, ?, ?)
        """,
        (token, user_id, _expires_at().isoformat(), ip_address, user_agent),
    )
    await db.commit()
    return token


async def get_session_user(
    db: aiosqlite.Connection,
    token: str,
) -> aiosqlite.Row | None:
    """Return user row for a valid, non-expired, non-revoked session."""
    row = await (
        await db.execute(
            """
            SELECT u.id, u.github_id, u.github_login, u.role, u.is_active,
                   s.expires_at, s.revoked
            FROM http_sessions s
            JOIN users u ON u.id = s.user_id
            WHERE s.token = ?
            """,
            (token,),
        )
    ).fetchone()

    if row is None:
        return None
    if row["revoked"]:
        return None
    if not row["is_active"]:
        return None

    expires_at = datetime.fromisoformat(row["expires_at"])
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)
    if _now() > expires_at:
        return None

    return row


async def revoke_session(db: aiosqlite.Connection, token: str) -> None:
    await db.execute(
        "UPDATE http_sessions SET revoked = TRUE WHERE token = ?", (token,)
    )
    await db.commit()
