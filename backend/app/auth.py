"""FastAPI dependencies for authentication and role-based access control."""

import aiosqlite
from fastapi import Depends, HTTPException, Request, status

from .database import get_db
from .sessions import COOKIE_NAME, get_session_user

# ── Role constants ────────────────────────────────────────────────────────────

ROLE_VIEWER = "VIEWER"
ROLE_OPERATOR = "OPERATOR"
ROLE_ADMIN = "ADMIN"

_ROLE_RANK = {ROLE_VIEWER: 1, ROLE_OPERATOR: 2, ROLE_ADMIN: 3}


def _has_role(user_role: str, required_role: str) -> bool:
    return _ROLE_RANK.get(user_role, 0) >= _ROLE_RANK.get(required_role, 99)


# ── Session extraction ────────────────────────────────────────────────────────


async def _get_current_user(
    request: Request,
    db: aiosqlite.Connection = Depends(get_db),
) -> aiosqlite.Row:
    token = request.cookies.get(COOKIE_NAME)
    if not token:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Not authenticated")

    user = await get_session_user(db, token)
    if user is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid or expired session")

    return user


# ── Role-based dependencies ───────────────────────────────────────────────────


async def require_viewer(
    user: aiosqlite.Row = Depends(_get_current_user),
) -> aiosqlite.Row:
    if not _has_role(user["role"], ROLE_VIEWER):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Insufficient permissions")
    return user


async def require_operator(
    user: aiosqlite.Row = Depends(_get_current_user),
) -> aiosqlite.Row:
    if not _has_role(user["role"], ROLE_OPERATOR):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Insufficient permissions")
    return user


async def require_admin(
    user: aiosqlite.Row = Depends(_get_current_user),
) -> aiosqlite.Row:
    if not _has_role(user["role"], ROLE_ADMIN):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Insufficient permissions")
    return user
