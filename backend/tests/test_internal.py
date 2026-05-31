"""Internal API（HMAC 認証つきの Agent 連携）のテスト。"""

import json

from conftest import sign_agent


def _update_body(**overrides) -> bytes:
    payload = {
        "tmux_name": "claude-demo",
        "status": "RUNNING",
        "waiting_category": None,
        "waiting_pattern": None,
        "snapshot_lines": None,
    }
    payload.update(overrides)
    return json.dumps(payload).encode()


def test_session_update_valid_signature(client):
    body = _update_body()
    resp = client.post(
        "/internal/sessions/update", content=body, headers=sign_agent(body)
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is True
    assert isinstance(data["session_id"], int)
    assert data["prev_status"] is None  # 新規作成


def test_session_update_is_upsert(client):
    body = _update_body()
    first = client.post(
        "/internal/sessions/update", content=body, headers=sign_agent(body)
    ).json()

    body2 = _update_body(status="WAITING_FOR_INPUT", waiting_category="APPROVAL")
    second = client.post(
        "/internal/sessions/update", content=body2, headers=sign_agent(body2)
    ).json()

    assert second["session_id"] == first["session_id"]  # 同一セッションを更新
    assert second["prev_status"] == "RUNNING"


def test_invalid_signature_rejected(client):
    body = _update_body()
    headers = sign_agent(body)
    headers["X-Agent-Signature"] = "deadbeef"
    resp = client.post("/internal/sessions/update", content=body, headers=headers)
    assert resp.status_code == 403


def test_old_timestamp_rejected(client):
    body = _update_body()
    headers = sign_agent(body, timestamp="1000000000")  # 2001 年 = 5 分超過
    resp = client.post("/internal/sessions/update", content=body, headers=headers)
    assert resp.status_code == 403


def test_invalid_status_rejected(client):
    body = _update_body(status="BOGUS")
    resp = client.post(
        "/internal/sessions/update", content=body, headers=sign_agent(body)
    )
    assert resp.status_code == 422


def test_snapshot_is_scrubbed(client, make_user):
    secret_line = "export API_KEY=supersecretvalue123"
    body = _update_body(snapshot_lines=["normal output", secret_line])
    update = client.post(
        "/internal/sessions/update", content=body, headers=sign_agent(body)
    ).json()
    session_id = update["session_id"]

    # 閲覧 API でスナップショットを取得し、シークレットが除去されていることを確認
    token = make_user(role="VIEWER")
    client.cookies.set("overseer_session", token)
    snap = client.get(f"/api/sessions/{session_id}/snapshot").json()

    assert "supersecretvalue123" not in snap["content"]
    assert "[REDACTED]" in snap["content"]
    assert "normal output" in snap["content"]


def test_heartbeat(client):
    body = json.dumps({"agent_version": "0.1.0"}).encode()
    resp = client.post("/internal/heartbeat", content=body, headers=sign_agent(body))
    assert resp.status_code == 200
    assert resp.json()["ok"] is True


def test_non_loopback_client_rejected():
    """署名が正しくても、ループバック以外の送信元からの /internal は 403 で拒否する。

    require_loopback を無効化しない素の TestClient を使う。TestClient の送信元は
    "testclient"（非ループバック）固定なので、ここでは実際の制限が働く。
    """
    from fastapi.testclient import TestClient

    from app.main import app

    body = _update_body()
    with TestClient(app) as external:
        resp = external.post(
            "/internal/sessions/update", content=body, headers=sign_agent(body)
        )
    assert resp.status_code == 403
