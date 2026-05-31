"""GitHub OAuth 2.0 authentication endpoints."""

import secrets
import time

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from fastapi.responses import RedirectResponse

import aiosqlite

from ..audit import log_event
from ..config import settings
from ..database import get_db
from ..sessions import COOKIE_NAME, create_session, revoke_session
from ..auth import require_viewer

router = APIRouter(prefix="/auth", tags=["auth"])

GITHUB_AUTHORIZE_URL = "https://github.com/login/oauth/authorize"
GITHUB_TOKEN_URL = "https://github.com/login/oauth/access_token"
GITHUB_USER_URL = "https://api.github.com/user"

# In-memory OAuth state store: state -> created_at (Unix timestamp)
# Single-process only; ephemeral (restart clears pending logins, which is fine)
_pending_states: dict[str, float] = {}
_STATE_TTL = 600  # 10 minutes


def _clean_states() -> None:
    cutoff = time.time() - _STATE_TTL
    expired = [s for s, ts in _pending_states.items() if ts < cutoff]
    for s in expired:
        del _pending_states[s]


@router.get("/github")
async def github_login() -> RedirectResponse:
    _clean_states()
    state = secrets.token_urlsafe(16)
    _pending_states[state] = time.time()

    params = {
        "client_id": settings.github_client_id,
        "redirect_uri": settings.github_callback_url,
        "scope": "read:user",
        "state": state,
    }
    query = "&".join(f"{k}={v}" for k, v in params.items())
    return RedirectResponse(f"{GITHUB_AUTHORIZE_URL}?{query}")


@router.get("/github/callback")
async def github_callback(
    request: Request,
    response: Response,
    code: str = "",
    state: str = "",
    db: aiosqlite.Connection = Depends(get_db),
) -> RedirectResponse:
    # Verify state
    _clean_states()
    if not state or state not in _pending_states:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Invalid state parameter")
    del _pending_states[state]

    if not code:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Missing code parameter")

    # Exchange code for access token
    async with httpx.AsyncClient() as client:
        token_resp = await client.post(
            GITHUB_TOKEN_URL,
            data={
                "client_id": settings.github_client_id,
                "client_secret": settings.github_client_secret,
                "code": code,
                "redirect_uri": settings.github_callback_url,
            },
            headers={"Accept": "application/json"},
        )

    if token_resp.status_code != 200:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, "GitHub token exchange failed")

    token_data = token_resp.json()
    access_token = token_data.get("access_token")
    if not access_token:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, "No access token in response")

    # Get GitHub user info
    async with httpx.AsyncClient() as client:
        user_resp = await client.get(
            GITHUB_USER_URL,
            headers={"Authorization": f"Bearer {access_token}"},
        )

    if user_resp.status_code != 200:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, "GitHub user fetch failed")

    gh_user = user_resp.json()
    github_id = str(gh_user["id"])  # numeric ID, immutable
    github_login = gh_user["login"]

    # Whitelist check — fail with 403, not 404, to avoid leaking valid user info
    if github_id not in settings.allowed_github_user_ids:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Access denied")

    # Upsert user (first login creates with VIEWER role by default)
    await db.execute(
        """
        INSERT INTO users (github_id, github_login, role)
        VALUES (?, ?, 'VIEWER')
        ON CONFLICT(github_id) DO UPDATE SET
            github_login = excluded.github_login,
            last_login_at = CURRENT_TIMESTAMP
        """,
        (github_id, github_login),
    )
    await db.commit()

    user_row = await (
        await db.execute("SELECT id, role FROM users WHERE github_id = ?", (github_id,))
    ).fetchone()

    ip = request.client.host if request.client else None
    ua = request.headers.get("user-agent")

    session_token = await create_session(db, user_row["id"], ip, ua)
    await log_event(db, "LOGIN", user_id=user_row["id"], ip_address=ip, user_agent=ua)

    redirect = RedirectResponse(url="/", status_code=status.HTTP_302_FOUND)
    redirect.set_cookie(
        key=COOKIE_NAME,
        value=session_token,
        httponly=True,
        samesite="strict",
        max_age=86400,
    )
    return redirect


@router.post("/logout")
async def logout(
    request: Request,
    user: aiosqlite.Row = Depends(require_viewer),
    db: aiosqlite.Connection = Depends(get_db),
) -> dict:
    token = request.cookies.get(COOKIE_NAME, "")
    if token:
        await revoke_session(db, token)

    ip = request.client.host if request.client else None
    await log_event(db, "LOGOUT", user_id=user["id"], ip_address=ip)

    response = Response()
    response.delete_cookie(COOKIE_NAME)
    return {"ok": True}


@router.get("/me")
async def me(user: aiosqlite.Row = Depends(require_viewer)) -> dict:
    return {
        "id": user["id"],
        "github_login": user["github_login"],
        "role": user["role"],
    }
