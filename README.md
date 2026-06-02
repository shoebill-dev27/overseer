# Overseer

**Claude Code Remote Operations Console** — a tool to remotely "watch" multiple
Claude Code (tmux) sessions running locally, and send approval actions
(Y/N/Enter/STOP) when a session is waiting for input.

The goal is to be able to check, from anywhere or another device over
Tailscale / localhost, "is that session stuck waiting for approval?" and, if
needed, push it forward with a single tap.

---

## What it solves

When you run Claude Code on `tmux` for long stretches, it frequently stops for
your approval (`Do you want to proceed?`, etc.). Nothing moves unless you are
glued to the terminal. Overseer takes over the following:

- Periodically peeks at the monitored tmux sessions and classifies them as
  **RUNNING / WAITING_FOR_INPUT / FINISHED**.
- Shows the screen contents (snapshot) in a web UI, **with secrets redacted**.
- Sends **Y / N / Enter / STOP** (and arbitrary text) from the browser, with
  two-step confirmation.

---

## Architecture

Three independent components. The Agent and Backend are assumed to communicate
over **localhost on the same host**.

```
   ┌──────────────────────────────────────────────────────────────┐
   │  Dev machine (host where Claude Code runs)                     │
   │                                                                │
   │   tmux: claude-foo ┐                                           │
   │   tmux: claude-bar ┤  capture-pane / send-keys                 │
   │                    ▼                                           │
   │            ┌──────────────┐   HMAC-signed HTTP (/internal)     │
   │            │ Local Agent  │ ───────────────────────────┐       │
   │            │ (agent/)     │                            ▼       │
   │            └──────────────┘                   ┌──────────────┐ │
   │                                               │  FastAPI     │ │
   │                                               │  Backend     │ │
   │   Browser ◀── WebSocket / REST ───────────────│  (backend/)  │ │
   │   (frontend/) GitHub OAuth role authz          │  + SQLite    │ │
   │                                               └──────────────┘ │
   └──────────────────────────────────────────────────────────────┘
```

### 1. Local Agent (`agent/`)
- Watches `tmux` sessions (by default, names starting with `claude-`) every
  `poll_interval_seconds` (default 10s).
- Captures the screen with `tmux capture-pane`, then detects "waiting for input"
  via the regexes in `config/waiting_patterns.yaml`.
- Scrubs the captured text before sending (replaces API keys, tokens, passwords,
  etc. with `[REDACTED]`).
- Pushes status and snapshots to the Backend's Internal API. Reports liveness via
  `heartbeat`.
- Fetches **CONFIRMED** actions from the Backend and executes only the ones
  allowed in `config/agent.yaml` via `tmux send-keys` (the Agent is the final
  gate).

### 2. Backend (`backend/`)
- FastAPI + SQLite (aiosqlite, WAL mode). Also serves the frontend statically.
- **GitHub OAuth 2.0** login + user-ID whitelist + role authorization.
- Stores session state and broadcasts changes to all clients over **WebSocket**.
- Two-step confirmation of actions (create → confirm) and an audit log
  (INSERT-only).

### 3. Frontend (`frontend/`)
- Plain HTML/CSS/JS (no build step). Served by the Backend at `/`.
- Session list on the left; detail snapshot and action bar on the right.
  Real-time updates over WebSocket.

---

## Data flow (status and actions)

**Status flow (monitoring)**
```
tmux screen → capture-pane → pattern match → scrub → POST /internal/sessions/update
            → store in SQLite → WebSocket broadcast → browser update
```

**Action flow (operation, two-step confirmation)**
```
Browser: POST /api/sessions/{id}/actions          → status=PENDING_CONFIRM
Browser: POST /api/actions/{id}/confirm (within 120s) → status=CONFIRMED
Agent:   GET  /internal/actions/pending           → fetch CONFIRMED
Agent:   run tmux send-keys                        → POST /internal/actions/{id}/result
                                                   → status=EXECUTED or FAILED
```
An action that is not confirmed within 120s (`ACTION_TTL_SECONDS`) drops to
**EXPIRED**. Actions with the same `idempotency_key` are not created twice.

---

## Roles and permissions

Roles are ranked (higher includes lower). On first login a user is created as
**VIEWER**; promotion is done by updating the DB directly.

| Role      | Can do                                            |
|-----------|---------------------------------------------------|
| VIEWER    | View session list / detail / snapshot             |
| OPERATOR  | Above + create/confirm actions (Y/N/Enter/STOP, arbitrary text) |
| ADMIN     | All of the above (no ADMIN-only endpoints yet)    |

---

## Setup

### Prerequisites
- Python 3.10+
- `tmux` (used by the Agent for monitoring and operations)
- Start the Claude Code you want to monitor in a tmux session named
  `claude-<name>` (the prefix is configurable via `session_prefix` in
  `config/agent.yaml`)
- A GitHub OAuth App (for login)

### Quick start (one-shot)

Run the setup script. It installs dependencies, scaffolds `backend/.env` and
`agent/.env`, generates secrets, and initializes the DB, then prints the manual
steps (GitHub OAuth App, your user ID, role promotion). Existing `.env` files are
never overwritten, so it is safe to re-run.

```bash
make bootstrap   # or: ./scripts/setup.sh
```

After it finishes, follow the printed steps, then `make dev`.

### Steps (manual)
```bash
# 1. Install dependencies (creates a venv for backend / agent each)
make setup

# 2. Prepare environment variables (copy from templates and fill in real values)
cp backend/.env.example backend/.env
cp agent/.env.example   agent/.env
#  - Generate SECRET_KEY and AGENT_HMAC_SECRET and set both
#    (AGENT_HMAC_SECRET must be identical in backend/agent)
#  - Fill in the GitHub OAuth values and ALLOWED_GITHUB_USER_IDS

# 3. Initialize the DB (create the SQLite schema)
make db-init
```

Example of generating `SECRET_KEY` / `AGENT_HMAC_SECRET`:
```bash
python3 -c "import secrets; print(secrets.token_urlsafe(32))"
```

---

## Running

```bash
make run-backend   # Start FastAPI + UI on 0.0.0.0:8000
make run-agent     # Start the Local Agent (in another terminal)
# or
make dev           # Start backend and agent together
```

After starting, open `http://127.0.0.1:8000` in a browser and "Log in with
GitHub". The startup self-check prints **HEALTHY / DEGRADED / FAILED** to stdout
(without `SECRET_KEY` and `AGENT_HMAC_SECRET` it stops with FAILED).

---

## Preparing sessions to monitor

The Agent only watches **tmux sessions whose name starts with `claude-`** (the
prefix is `session_prefix` in `config/agent.yaml`). The check is a name prefix
match only; it does not care what program is running inside.

```bash
# Create a new one and start Claude Code inside it
tmux new -s claude-myproject

# To target an existing session, rename it
tmux rename-session -t existing-session-name claude-myproject
```

A few seconds later (default poll interval is 10s), `claude-myproject` appears in
the web UI's session list.

---

## Remote access (HTTPS via Tailscale Serve)

To use it on the go or from a phone, the easiest approach is to serve it over
**HTTPS on Tailscale** without exposing it publicly.

```bash
# Bind the backend to localhost only (do not expose it directly)
cd backend && .venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8000

# Proxy 443 → backend with Tailscale Serve (cert auto-issued, tailnet-only)
tailscale serve --bg 8000
```

This makes it reachable at `https://<machine>.<tailnet>.ts.net` (no port number,
valid certificate) from every device in the tailnet (including phones). Also
**align all of the following to the same URL**:

- `APP_BASE_URL` and `GITHUB_CALLBACK_URL` in `backend/.env`
- The Authorization callback URL of the GitHub OAuth App
  (`https://<machine>.<tailnet>.ts.net/auth/github/callback`)

If the three disagree, login fails with an OAuth `redirect_uri` mismatch, so be
careful.

> `tailscale serve` requires root/operator privileges. Running
> `sudo tailscale set --operator=$USER` once lets you run it without sudo
> afterward, and the Serve config persists in tailscaled across restarts.

---

## Configuration files

| File                            | Role                                                          |
|---------------------------------|---------------------------------------------------------------|
| `backend/.env`                  | Backend secrets, OAuth, whitelist (template: `.env.example`)  |
| `agent/.env`                    | Agent HMAC secret, connection target (template: `.env.example`) |
| `config/agent.yaml`             | Poll interval, target prefix, allowed action types, etc.      |
| `config/waiting_patterns.yaml`  | Regexes for waiting-for-input detection. **Hot-reloaded every 60s without restarting the Agent** |

> Note: the connection URL uses `BACKEND_URL` in `agent/.env` (`backend.url` in
> `agent.yaml` is for meta settings such as the poll interval).

---

## API overview

| Method   | Path                                | Authz       | Purpose                  |
|----------|-------------------------------------|-------------|--------------------------|
| GET      | `/auth/github`                      | —           | Start OAuth              |
| GET      | `/auth/github/callback`             | —           | OAuth callback           |
| POST     | `/auth/logout`                      | VIEWER      | Log out                  |
| GET      | `/auth/me`                          | VIEWER      | Own info                 |
| GET      | `/api/sessions`                     | VIEWER      | Session list + Agent status |
| GET      | `/api/sessions/{id}`                | VIEWER      | Session detail           |
| GET      | `/api/sessions/{id}/snapshot`       | VIEWER      | Screen snapshot          |
| POST     | `/api/sessions/{id}/actions`        | OPERATOR    | Create action (needs confirm) |
| POST     | `/api/actions/{id}/confirm`         | OPERATOR    | Confirm action           |
| GET      | `/api/actions/{id}`                 | VIEWER      | Get action status        |
| WS       | `/ws/sessions`                      | Cookie auth | Real-time status feed    |
| GET      | `/health`                           | —           | Health check             |
| `/internal/*`                      | HMAC sig    | Agent-only (see below)   |

`/internal/*` (`sessions/update`, `heartbeat`, `actions/pending`,
`actions/{id}/result`) uses HMAC-SHA256 authentication via `X-Agent-Timestamp` /
`X-Agent-Signature` (timestamp ±300s).

---

## Security model

- **Assumes it is not exposed to a public network.** Access is expected over
  Tailscale or localhost.
- CORS allows `APP_BASE_URL` only. Security headers (CSP / X-Frame-Options, etc.)
  are added.
- Whitelist authorization by GitHub **numeric user ID** (username is for display
  only).
- HTTP sessions are random tokens stored server-side (24h TTL, revocable).
- The session cookie is `HttpOnly` / `SameSite=Strict` / `Secure` (not sent in
  cleartext when served over HTTPS).
- Agent ↔ Backend is authenticated with an HMAC signature + timestamp. In
  addition, `/internal` restricts the client IP to **loopback (127.0.0.1 / ::1)**
  and rejects external sources with 403 before HMAC.
- Snapshots and logs are **secret-scrubbed** before being stored/sent
  (`scrubber.py`).

### Known constraints / caveats (to improve)
- The loopback restriction on `/internal` depends on the source IP, so if you put
  a reverse proxy in front that forwards `/internal` to localhost, block
  `/internal` at the proxy (via the proxy the source appears as 127.0.0.1).
- `SECRET_KEY` is only existence-checked at startup; it is not used for signing in
  the current code (reserved for future use).
- The OAuth state is held in process memory (assumes a single process; an
  in-progress login is lost on restart).
- `SEND_TEXT` (arbitrary text) is implemented but **disabled by default** in
  `config/agent.yaml` for safety. Enabling it lets the browser send arbitrary
  strings to tmux, so set `SEND_TEXT: true` explicitly as an operational decision
  (OPERATOR or higher + two-step confirmation are always required).

---

## Development

```bash
make lint    # ruff check + ruff format --check (backend / agent)
make test    # pytest (backend 43 + agent 19 = 62 tests)
```

### Project layout
```
overseer/
├── backend/
│   ├── app/
│   │   ├── main.py          # FastAPI startup, self-check, static serving
│   │   ├── config.py        # Environment variable loading
│   │   ├── database.py      # SQLite schema and connection
│   │   ├── auth.py          # Role authorization dependencies
│   │   ├── sessions.py      # HTTP session (cookie) management
│   │   ├── audit.py         # Audit log
│   │   ├── scrubber.py      # Secret scrubbing
│   │   ├── ws_manager.py    # WebSocket connection management
│   │   └── routers/         # auth / sessions / actions / internal / ws
│   └── tests/
├── agent/
│   ├── agent.py             # Monitoring main loop
│   ├── client.py            # HMAC-signed Internal API client
│   ├── tmux_monitor.py      # capture-pane / send-keys / list-sessions
│   ├── pattern_matcher.py   # Waiting-pattern matching (hot reload)
│   ├── scrubber.py          # Secret scrubbing
│   └── tests/
├── frontend/                # Plain HTML / CSS / JS
├── config/                  # agent.yaml / waiting_patterns.yaml
└── Makefile
```

---

## Status

- **Phase 1** (read-only web UI) — done
- **Phase 2** (actions: SEND_Y/N/ENTER/STOP, two-step confirmation, HMAC, audit log) — done
- **Phase 3** (`SEND_TEXT`: arbitrary text; disabled by default) — done

`make lint` green / `make test` all 62 tests pass.
