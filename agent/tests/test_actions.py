"""agent のアクション実行ロジック execute_pending_actions のテスト。

client / tmux のネットワーク・プロセス呼び出しをスタブに差し替えて検証する。
（agent venv に pytest-asyncio が無いため asyncio.run で実行）
"""

import asyncio

import agent


def _one_action(action_type="SEND_Y", tmux_name="claude-a"):
    return [
        {
            "id": 1,
            "action_type": action_type,
            "tmux_name": tmux_name,
            "text_payload": None,
        }
    ]


def _patch(monkeypatch, actions, send_ok=True):
    sent, reports = [], []

    async def fake_fetch():
        return actions

    def fake_send(name, keys):
        sent.append((name, keys))
        return send_ok

    async def fake_report(action_id, status, reason):
        reports.append((action_id, status, reason))
        return True

    monkeypatch.setattr(agent, "fetch_pending_actions", fake_fetch)
    monkeypatch.setattr(agent, "send_keys", fake_send)
    monkeypatch.setattr(agent, "report_action_result", fake_report)
    return sent, reports


def test_enabled_action_is_sent_and_reported(monkeypatch):
    sent, reports = _patch(monkeypatch, _one_action("SEND_Y"))
    asyncio.run(agent.execute_pending_actions({"SEND_Y": True}))
    assert sent == [("claude-a", ["y", "Enter"])]
    assert reports == [(1, "EXECUTED", None)]


def test_stop_maps_to_escape(monkeypatch):
    sent, reports = _patch(monkeypatch, _one_action("STOP"))
    asyncio.run(agent.execute_pending_actions({"STOP": True}))
    assert sent == [("claude-a", ["Escape"])]
    assert reports == [(1, "EXECUTED", None)]


def test_disabled_action_not_sent(monkeypatch):
    sent, reports = _patch(monkeypatch, _one_action("SEND_Y"))
    asyncio.run(agent.execute_pending_actions({"SEND_Y": False}))
    assert sent == []
    assert reports[0][1] == "FAILED"


def test_send_failure_reports_failed(monkeypatch):
    sent, reports = _patch(monkeypatch, _one_action("SEND_N"), send_ok=False)
    asyncio.run(agent.execute_pending_actions({"SEND_N": True}))
    assert sent == [("claude-a", ["n", "Enter"])]
    assert reports[0][1] == "FAILED"
