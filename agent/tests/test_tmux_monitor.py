"""Tests for tmux_monitor (mock subprocess to be tmux-independent)."""

import subprocess
import types

import tmux_monitor


def _fake_run(returncode: int, stdout: str):
    def _run(*args, **kwargs):
        return types.SimpleNamespace(returncode=returncode, stdout=stdout, stderr="")

    return _run


def test_list_sessions_filters_by_prefix(monkeypatch):
    monkeypatch.setattr(subprocess, "run", _fake_run(0, "claude-a\nclaude-b\nother\n"))
    assert tmux_monitor.list_sessions("claude-") == ["claude-a", "claude-b"]


def test_list_sessions_empty_when_tmux_fails(monkeypatch):
    monkeypatch.setattr(subprocess, "run", _fake_run(1, ""))
    assert tmux_monitor.list_sessions("claude-") == []


def test_capture_pane_returns_lines(monkeypatch):
    monkeypatch.setattr(subprocess, "run", _fake_run(0, "line1\nline2\n"))
    assert tmux_monitor.capture_pane("claude-a") == ["line1", "line2"]


def test_capture_pane_empty_on_failure(monkeypatch):
    monkeypatch.setattr(subprocess, "run", _fake_run(1, ""))
    assert tmux_monitor.capture_pane("claude-a") == []
