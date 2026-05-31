"""FastAPI application entry point."""

import subprocess
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from .config import settings
from .database import init_db
from .routers import actions, auth, internal, sessions, ws

# ── Startup self-check ────────────────────────────────────────────────────────

CONFIG_FILES = [
    Path("../config/waiting_patterns.yaml"),
    Path("../config/agent.yaml"),
]


def _check_tmux() -> bool:
    try:
        subprocess.run(["tmux", "-V"], capture_output=True, check=True)
        return True
    except (FileNotFoundError, subprocess.CalledProcessError):
        return False


def _check_config_files() -> bool:
    return all(p.exists() for p in CONFIG_FILES)


async def _startup_check() -> str:
    """Run all checks and return HEALTHY | DEGRADED | FAILED."""
    results: dict[str, bool] = {}

    results["tmux"] = _check_tmux()
    results["config_files"] = _check_config_files()

    env_checks = settings.check()
    results.update(env_checks)

    for name, ok in results.items():
        print(f"  [startup] {'OK  ' if ok else 'FAIL'} {name}")

    # SECRET_KEY and AGENT_HMAC_SECRET are required to run safely
    if not results.get("SECRET_KEY") or not results.get("AGENT_HMAC_SECRET"):
        return "FAILED"

    if all(results.values()):
        return "HEALTHY"

    return "DEGRADED"


# ── Lifespan ──────────────────────────────────────────────────────────────────


@asynccontextmanager
async def lifespan(app: FastAPI):
    print("[startup] Initializing database...")
    await init_db()

    print("[startup] Running self-check...")
    health = await _startup_check()
    print(f"[startup] Status: {health}")

    if health == "FAILED":
        raise RuntimeError(
            "Startup failed: SECRET_KEY and AGENT_HMAC_SECRET must be set in .env"
        )

    yield


# ── App ───────────────────────────────────────────────────────────────────────

app = FastAPI(
    title="Overseer",
    description="Claude Code Remote Operations Console",
    version="0.1.0",
    lifespan=lifespan,
    # Disable docs in future; fine for MVP
    docs_url="/docs",
    redoc_url=None,
)

# ── Security headers middleware ───────────────────────────────────────────────


@app.middleware("http")
async def add_security_headers(request: Request, call_next) -> Response:
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'"
    )
    return response


# ── CORS (Tailscale-only, no public origin needed) ───────────────────────────
# Restricted to same origin. Adjust APP_BASE_URL if the frontend is on a
# different port during development.

app.add_middleware(
    CORSMiddleware,
    allow_origins=[settings.app_base_url],
    allow_credentials=True,
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)

# ── Routers ───────────────────────────────────────────────────────────────────

app.include_router(auth.router)
app.include_router(sessions.router)
app.include_router(actions.router)
app.include_router(ws.router)
# Internal router is mounted separately (127.0.0.1 only) — see __main__ block
app.include_router(internal.router)


@app.get("/health")
async def health() -> dict:
    return {"status": "ok"}


# ── Static frontend ───────────────────────────────────────────────────────────
# 素の JS/HTML/CSS をバックエンドから配信（CSP: script-src 'self' に適合）。
# API ルータの後にマウントするため、/api · /auth · /ws · /internal が優先される。
# html=True により "/" は index.html を返す。

_FRONTEND_DIR = Path(__file__).resolve().parent.parent.parent / "frontend"
if _FRONTEND_DIR.is_dir():
    app.mount("/", StaticFiles(directory=_FRONTEND_DIR, html=True), name="frontend")
else:
    print(f"[startup] WARNING: frontend directory not found at {_FRONTEND_DIR}")
