"""SQLite database initialization and connection management."""

import aiosqlite
import asyncio
import os

DB_PATH = os.getenv("DATABASE_PATH", "overseer.db")

SCHEMA = """
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;

-- Schema version management
CREATE TABLE IF NOT EXISTS schema_version (
    version    INTEGER NOT NULL,
    applied_at DATETIME DEFAULT CURRENT_TIMESTAMP
);

-- Users (created via GitHub OAuth)
-- github_id is the GitHub User ID (numeric string, immutable)
-- github_login is the username (display only, not used for auth)
CREATE TABLE IF NOT EXISTS users (
    id             INTEGER  PRIMARY KEY AUTOINCREMENT,
    github_id      TEXT     UNIQUE NOT NULL,
    github_login   TEXT     NOT NULL,
    role           TEXT     NOT NULL CHECK(role IN ('VIEWER','OPERATOR','ADMIN')),
    is_active      BOOLEAN  NOT NULL DEFAULT TRUE,
    created_at     DATETIME DEFAULT CURRENT_TIMESTAMP,
    last_login_at  DATETIME
);

-- HTTP sessions (login state management)
CREATE TABLE IF NOT EXISTS http_sessions (
    token       TEXT     PRIMARY KEY,
    user_id     INTEGER  NOT NULL REFERENCES users(id),
    created_at  DATETIME DEFAULT CURRENT_TIMESTAMP,
    expires_at  DATETIME NOT NULL,
    revoked     BOOLEAN  NOT NULL DEFAULT FALSE,
    ip_address  TEXT,
    user_agent  TEXT
);

-- Claude Code sessions (tmux monitoring targets)
CREATE TABLE IF NOT EXISTS claude_sessions (
    id               INTEGER  PRIMARY KEY AUTOINCREMENT,
    tmux_name        TEXT     NOT NULL UNIQUE,
    status           TEXT     NOT NULL CHECK(status IN (
                         'RUNNING','WAITING_FOR_INPUT','ERROR','FINISHED')),
    waiting_category TEXT,
    waiting_pattern  TEXT,
    last_notified_at DATETIME,
    started_at       DATETIME,
    last_updated_at  DATETIME DEFAULT CURRENT_TIMESTAMP,
    finished_at      DATETIME
);

-- Session snapshot (keeps only the latest state, scrubbed)
-- Current state, not history. Overwritten via UPSERT (INSERT OR REPLACE).
CREATE TABLE IF NOT EXISTS session_snapshots (
    session_id   INTEGER  PRIMARY KEY REFERENCES claude_sessions(id),
    captured_at  DATETIME DEFAULT CURRENT_TIMESTAMP,
    content      TEXT     NOT NULL,
    line_count   INTEGER  NOT NULL,
    truncated    BOOLEAN  NOT NULL DEFAULT FALSE
);

-- Actions (Phase 2+)
CREATE TABLE IF NOT EXISTS actions (
    id               INTEGER  PRIMARY KEY AUTOINCREMENT,
    session_id       INTEGER  NOT NULL REFERENCES claude_sessions(id),
    user_id          INTEGER  NOT NULL REFERENCES users(id),
    action_type      TEXT     NOT NULL CHECK(action_type IN (
                         'SEND_Y','SEND_N','SEND_ENTER','STOP','SEND_TEXT')),
    text_payload     TEXT,
    status           TEXT     NOT NULL CHECK(status IN (
                         'PENDING_CONFIRM','CONFIRMED','EXECUTED','FAILED','EXPIRED')),
    idempotency_key  TEXT     UNIQUE NOT NULL,
    created_at       DATETIME DEFAULT CURRENT_TIMESTAMP,
    confirmed_at     DATETIME,
    executed_at      DATETIME,
    failure_reason   TEXT
);

-- Audit log (INSERT ONLY; no UPDATE/DELETE from the app layer)
CREATE TABLE IF NOT EXISTS audit_log (
    id          INTEGER  PRIMARY KEY AUTOINCREMENT,
    occurred_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    user_id     INTEGER  REFERENCES users(id),
    session_id  INTEGER  REFERENCES claude_sessions(id),
    event_type  TEXT     NOT NULL,
    detail      TEXT,
    ip_address  TEXT,
    user_agent  TEXT
);

-- Agent liveness (singleton, fixed id=1)
CREATE TABLE IF NOT EXISTS agent_status (
    id            INTEGER  PRIMARY KEY CHECK(id = 1),
    last_seen_at  DATETIME NOT NULL,
    agent_version TEXT,
    status        TEXT     NOT NULL CHECK(status IN ('ONLINE','OFFLINE'))
                           DEFAULT 'OFFLINE'
);
"""

SCHEMA_VERSION = 1


async def get_db() -> aiosqlite.Connection:
    """For FastAPI dependency injection: returns a DB connection per request."""
    db = await aiosqlite.connect(DB_PATH)
    db.row_factory = aiosqlite.Row
    try:
        await db.execute("PRAGMA foreign_keys=ON")
        yield db
    finally:
        await db.close()


async def init_db() -> None:
    """Create the schema and initialize schema_version."""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.executescript(SCHEMA)

        row = await (await db.execute("SELECT version FROM schema_version")).fetchone()
        if row is None:
            await db.execute(
                "INSERT INTO schema_version (version) VALUES (?)", (SCHEMA_VERSION,)
            )
            await db.commit()
            print(f"[DB] Initialized schema version {SCHEMA_VERSION}")
        else:
            print(f"[DB] Schema version: {row[0]}")


if __name__ == "__main__":
    asyncio.run(init_db())
