"""Tests for the action create/confirm/result-reporting flow."""

import json
import sqlite3
import os
from datetime import datetime, timedelta, timezone

from conftest import sign_agent


def _create_session(client, tmux_name="claude-act"):
    body = json.dumps({"tmux_name": tmux_name, "status": "WAITING_FOR_INPUT"}).encode()
    resp = client.post(
        "/internal/sessions/update", content=body, headers=sign_agent(body)
    )
    return resp.json()["session_id"]


def _create_action(client, session_id, action_type="SEND_Y", key="k1", text=None):
    body = {"action_type": action_type, "idempotency_key": key}
    if text is not None:
        body["text_payload"] = text
    return client.post(f"/api/sessions/{session_id}/actions", json=body)


def test_create_requires_operator(client, make_user):
    session_id = _create_session(client)
    client.cookies.set("overseer_session", make_user(role="VIEWER"))
    resp = _create_action(client, session_id)
    assert resp.status_code == 403


def test_operator_creates_pending(client, make_user):
    session_id = _create_session(client)
    client.cookies.set("overseer_session", make_user(role="OPERATOR"))
    resp = _create_action(client, session_id)
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "PENDING_CONFIRM"
    assert data["action_type"] == "SEND_Y"


def test_unsupported_action_type_rejected(client, make_user):
    session_id = _create_session(client)
    client.cookies.set("overseer_session", make_user(role="OPERATOR"))
    resp = _create_action(client, session_id, action_type="REBOOT")
    assert resp.status_code == 422


def test_send_text_creates_pending(client, make_user):
    session_id = _create_session(client)
    client.cookies.set("overseer_session", make_user(role="OPERATOR"))
    resp = _create_action(client, session_id, action_type="SEND_TEXT", text="hello")
    assert resp.status_code == 200
    assert resp.json()["status"] == "PENDING_CONFIRM"


def test_send_text_requires_payload(client, make_user):
    session_id = _create_session(client)
    client.cookies.set("overseer_session", make_user(role="OPERATOR"))
    resp = _create_action(client, session_id, action_type="SEND_TEXT")
    assert resp.status_code == 422


def test_send_text_rejects_newline(client, make_user):
    session_id = _create_session(client)
    client.cookies.set("overseer_session", make_user(role="OPERATOR"))
    resp = _create_action(
        client, session_id, action_type="SEND_TEXT", text="a\nrm -rf /"
    )
    assert resp.status_code == 422


def test_text_payload_rejected_for_non_text_action(client, make_user):
    session_id = _create_session(client)
    client.cookies.set("overseer_session", make_user(role="OPERATOR"))
    resp = _create_action(client, session_id, action_type="SEND_Y", text="oops")
    assert resp.status_code == 422


def test_send_text_payload_reaches_pending(client, make_user):
    """SEND_TEXT's text_payload is carried through to the Agent's pending list."""
    session_id = _create_session(client)
    client.cookies.set("overseer_session", make_user(role="OPERATOR"))
    action = _create_action(
        client, session_id, action_type="SEND_TEXT", text="git status"
    ).json()
    client.post(f"/api/actions/{action['id']}/confirm")

    pending = client.get("/internal/actions/pending", headers=sign_agent(b"")).json()
    target = next(a for a in pending["actions"] if a["id"] == action["id"])
    assert target["action_type"] == "SEND_TEXT"
    assert target["text_payload"] == "git status"


def test_create_on_missing_session_404(client, make_user):
    client.cookies.set("overseer_session", make_user(role="OPERATOR"))
    resp = _create_action(client, 9999)
    assert resp.status_code == 404


def test_idempotency_key_returns_same_action(client, make_user):
    session_id = _create_session(client)
    client.cookies.set("overseer_session", make_user(role="OPERATOR"))
    first = _create_action(client, session_id, key="dup").json()
    second = _create_action(client, session_id, key="dup").json()
    assert first["id"] == second["id"]


def test_confirm_transitions_to_confirmed(client, make_user):
    session_id = _create_session(client)
    client.cookies.set("overseer_session", make_user(role="OPERATOR"))
    action = _create_action(client, session_id).json()
    resp = client.post(f"/api/actions/{action['id']}/confirm")
    assert resp.status_code == 200
    assert resp.json()["status"] == "CONFIRMED"


def test_double_confirm_conflict(client, make_user):
    session_id = _create_session(client)
    client.cookies.set("overseer_session", make_user(role="OPERATOR"))
    action = _create_action(client, session_id).json()
    client.post(f"/api/actions/{action['id']}/confirm")
    resp = client.post(f"/api/actions/{action['id']}/confirm")
    assert resp.status_code == 409


def test_full_flow_execute(client, make_user):
    """create → confirm → agent fetches pending → reports result → reflected as EXECUTED."""
    session_id = _create_session(client)
    client.cookies.set("overseer_session", make_user(role="OPERATOR"))
    action = _create_action(client, session_id, action_type="SEND_Y").json()
    client.post(f"/api/actions/{action['id']}/confirm")

    # Agent: fetch the pending list (HMAC, no body)
    pending = client.get("/internal/actions/pending", headers=sign_agent(b"")).json()
    ids = [a["id"] for a in pending["actions"]]
    assert action["id"] in ids
    target = next(a for a in pending["actions"] if a["id"] == action["id"])
    assert target["tmux_name"] == "claude-act"
    assert target["action_type"] == "SEND_Y"

    # Agent: report the execution result
    body = json.dumps({"status": "EXECUTED", "failure_reason": None}).encode()
    result = client.post(
        f"/internal/actions/{action['id']}/result",
        content=body,
        headers=sign_agent(body),
    )
    assert result.status_code == 200

    # Status should be EXECUTED
    final = client.get(f"/api/actions/{action['id']}").json()
    assert final["status"] == "EXECUTED"


def test_pending_only_returns_confirmed(client, make_user):
    session_id = _create_session(client)
    client.cookies.set("overseer_session", make_user(role="OPERATOR"))
    # Unconfirmed actions (still PENDING_CONFIRM) do not appear in pending
    _create_action(client, session_id, key="unconfirmed")
    pending = client.get("/internal/actions/pending", headers=sign_agent(b"")).json()
    assert pending["actions"] == []


def test_expired_action_dropped_in_pending(client, make_user):
    session_id = _create_session(client)
    client.cookies.set("overseer_session", make_user(role="OPERATOR"))
    action = _create_action(client, session_id, key="old").json()

    # Rewrite created_at to before the TTL to create an expired state
    old = (datetime.now(timezone.utc) - timedelta(seconds=300)).isoformat()
    con = sqlite3.connect(os.environ["DATABASE_PATH"])
    con.execute("UPDATE actions SET created_at = ? WHERE id = ?", (old, action["id"]))
    con.commit()
    con.close()

    client.get(
        "/internal/actions/pending", headers=sign_agent(b"")
    )  # trigger lazy expiry
    final = client.get(f"/api/actions/{action['id']}").json()
    assert final["status"] == "EXPIRED"
