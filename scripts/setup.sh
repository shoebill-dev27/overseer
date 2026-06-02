#!/usr/bin/env bash
# One-shot setup for Overseer. Idempotent: safe to re-run.
#
# Automates the local parts (deps, .env scaffolding, secret generation, DB init)
# and then prints the manual steps that cannot be automated (GitHub OAuth App,
# your numeric user ID, role promotion).
set -euo pipefail

cd "$(dirname "$0")/.."  # repo root

echo "==> Installing dependencies (make setup)"
make setup

# Create .env files from templates if missing (never overwrite existing ones).
for d in backend agent; do
  if [ ! -f "$d/.env" ]; then
    cp "$d/.env.example" "$d/.env"
    echo "==> Created $d/.env from template"
  else
    echo "==> $d/.env already exists, leaving as-is"
  fi
done

# Generate and inject secrets. Fills SECRET_KEY / AGENT_HMAC_SECRET only when empty,
# and keeps AGENT_HMAC_SECRET identical across backend/agent.
python3 - <<'PY'
import pathlib, secrets

def get(path, key):
    for ln in pathlib.Path(path).read_text().splitlines():
        if ln.startswith(key + "="):
            return ln[len(key) + 1:]
    return ""

def set_kv(path, key, value):
    p = pathlib.Path(path)
    out = []
    for ln in p.read_text().splitlines():
        out.append(f"{key}={value}" if ln.startswith(key + "=") else ln)
    p.write_text("\n".join(out) + "\n")

# Shared HMAC: reuse backend's if already set, otherwise generate one.
hmac = get("backend/.env", "AGENT_HMAC_SECRET") or secrets.token_urlsafe(32)
set_kv("backend/.env", "AGENT_HMAC_SECRET", hmac)
set_kv("agent/.env", "AGENT_HMAC_SECRET", hmac)

if not get("backend/.env", "SECRET_KEY"):
    set_kv("backend/.env", "SECRET_KEY", secrets.token_urlsafe(32))

print("==> Secrets ready (SECRET_KEY / AGENT_HMAC_SECRET; HMAC synced across backend/agent)")
PY

echo "==> Initializing database (make db-init)"
make db-init

cat <<'EOF'

✅ Local setup complete.

Remaining manual steps (cannot be automated):

  1. Create a GitHub OAuth App: https://github.com/settings/developers
       Authorization callback URL: http://127.0.0.1:8000/auth/github/callback

  2. Fill backend/.env:
       GITHUB_CLIENT_ID, GITHUB_CLIENT_SECRET   (from the OAuth App)
       ALLOWED_GITHUB_USER_IDS                  (your numeric GitHub ID)
       Find your ID: curl https://api.github.com/users/<your-username>

  3. Start it:
       make dev
     Then open http://127.0.0.1:8000 and log in with GitHub.

  4. First login is VIEWER (read-only). To perform actions, promote yourself:
       cd backend && .venv/bin/python -c \
         "import sqlite3,os; c=sqlite3.connect(os.getenv('DATABASE_PATH','overseer.db')); \
          c.execute(\"UPDATE users SET role='OPERATOR' WHERE github_id='<your-id>'\"); c.commit()"
     Then log out and back in.

  5. Monitor a session: start tmux with a 'claude-' prefixed name, e.g.
       tmux new -s claude-myproject

For remote access over HTTPS, see "Remote access" in the README.
EOF
