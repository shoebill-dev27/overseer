"""Tests for the read API (auth/roles) and heartbeat reflection."""

import json

from conftest import sign_agent


def test_sessions_requires_auth(client):
    resp = client.get("/api/sessions")
    assert resp.status_code == 401


def test_sessions_empty_for_viewer(client, make_user):
    token = make_user(role="VIEWER")
    client.cookies.set("overseer_session", token)
    resp = client.get("/api/sessions")
    assert resp.status_code == 200
    data = resp.json()
    assert data["sessions"] == []
    assert data["agent"]["status"] == "NEVER_CONNECTED"


def test_sessions_lists_after_agent_update(client, make_user):
    body = json.dumps({"tmux_name": "claude-x", "status": "RUNNING"}).encode()
    client.post("/internal/sessions/update", content=body, headers=sign_agent(body))

    token = make_user(role="VIEWER")
    client.cookies.set("overseer_session", token)
    data = client.get("/api/sessions").json()
    names = [s["tmux_name"] for s in data["sessions"]]
    assert "claude-x" in names


def test_finished_sessions_excluded_from_list(client, make_user):
    running = json.dumps({"tmux_name": "claude-live", "status": "RUNNING"}).encode()
    client.post("/internal/sessions/update", content=running, headers=sign_agent(running))
    done = json.dumps({"tmux_name": "claude-demo", "status": "FINISHED"}).encode()
    client.post("/internal/sessions/update", content=done, headers=sign_agent(done))

    token = make_user(role="VIEWER")
    client.cookies.set("overseer_session", token)
    names = [s["tmux_name"] for s in client.get("/api/sessions").json()["sessions"]]
    assert "claude-live" in names
    assert "claude-demo" not in names


def test_get_missing_session_404(client, make_user):
    token = make_user(role="VIEWER")
    client.cookies.set("overseer_session", token)
    resp = client.get("/api/sessions/9999")
    assert resp.status_code == 404


def test_agent_online_after_heartbeat(client, make_user):
    body = json.dumps({"agent_version": "0.1.0"}).encode()
    client.post("/internal/heartbeat", content=body, headers=sign_agent(body))

    token = make_user(role="VIEWER")
    client.cookies.set("overseer_session", token)
    data = client.get("/api/sessions").json()
    assert data["agent"]["status"] == "ONLINE"
    assert data["agent"]["agent_version"] == "0.1.0"


def test_invalid_cookie_unauthorized(client):
    client.cookies.set("overseer_session", "nonexistent-token")
    resp = client.get("/api/sessions")
    assert resp.status_code == 401
