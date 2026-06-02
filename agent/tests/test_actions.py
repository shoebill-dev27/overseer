"""Tests for the agent's action execution logic, execute_pending_actions.

Network/process calls in client / tmux are replaced with stubs for verification.
(The agent venv has no pytest-asyncio, so we run via asyncio.run.)
"""

import asyncio

import agent


def _one_action(action_type="SEND_Y", tmux_name="claude-a", text_payload=None):
    return [
        {
            "id": 1,
            "action_type": action_type,
            "tmux_name": tmux_name,
            "text_payload": text_payload,
        }
    ]


def _patch(monkeypatch, actions, send_ok=True):
    sent, texts, reports = [], [], []

    async def fake_fetch():
        return actions

    def fake_send(name, keys):
        sent.append((name, keys))
        return send_ok

    def fake_send_text(name, text):
        texts.append((name, text))
        return send_ok

    async def fake_report(action_id, status, reason):
        reports.append((action_id, status, reason))
        return True

    monkeypatch.setattr(agent, "fetch_pending_actions", fake_fetch)
    monkeypatch.setattr(agent, "send_keys", fake_send)
    monkeypatch.setattr(agent, "send_text", fake_send_text)
    monkeypatch.setattr(agent, "report_action_result", fake_report)
    return sent, texts, reports


def test_enabled_action_is_sent_and_reported(monkeypatch):
    sent, _texts, reports = _patch(monkeypatch, _one_action("SEND_Y"))
    asyncio.run(agent.execute_pending_actions({"SEND_Y": True}))
    assert sent == [("claude-a", ["y", "Enter"])]
    assert reports == [(1, "EXECUTED", None)]


def test_stop_maps_to_escape(monkeypatch):
    sent, _texts, reports = _patch(monkeypatch, _one_action("STOP"))
    asyncio.run(agent.execute_pending_actions({"STOP": True}))
    assert sent == [("claude-a", ["Escape"])]
    assert reports == [(1, "EXECUTED", None)]


def test_disabled_action_not_sent(monkeypatch):
    sent, _texts, reports = _patch(monkeypatch, _one_action("SEND_Y"))
    asyncio.run(agent.execute_pending_actions({"SEND_Y": False}))
    assert sent == []
    assert reports[0][1] == "FAILED"


def test_send_failure_reports_failed(monkeypatch):
    sent, _texts, reports = _patch(monkeypatch, _one_action("SEND_N"), send_ok=False)
    asyncio.run(agent.execute_pending_actions({"SEND_N": True}))
    assert sent == [("claude-a", ["n", "Enter"])]
    assert reports[0][1] == "FAILED"


def test_send_text_sends_literal_and_reports(monkeypatch):
    _sent, texts, reports = _patch(
        monkeypatch, _one_action("SEND_TEXT", text_payload="git status")
    )
    asyncio.run(agent.execute_pending_actions({"SEND_TEXT": True}))
    assert texts == [("claude-a", "git status")]
    assert reports == [(1, "EXECUTED", None)]


def test_send_text_without_payload_fails(monkeypatch):
    _sent, texts, reports = _patch(
        monkeypatch, _one_action("SEND_TEXT", text_payload=None)
    )
    asyncio.run(agent.execute_pending_actions({"SEND_TEXT": True}))
    assert texts == []
    assert reports[0][1] == "FAILED"
