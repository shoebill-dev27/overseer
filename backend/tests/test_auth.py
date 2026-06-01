"""ロール階層判定 _has_role と OAuth リトライ _github_request の単体テスト。"""

import asyncio

import httpx
import pytest

from app.auth import ROLE_ADMIN, ROLE_OPERATOR, ROLE_VIEWER, _has_role
from app.routers import auth as auth_router


def test_same_role_passes():
    assert _has_role(ROLE_VIEWER, ROLE_VIEWER)
    assert _has_role(ROLE_OPERATOR, ROLE_OPERATOR)
    assert _has_role(ROLE_ADMIN, ROLE_ADMIN)


def test_higher_role_passes_lower_requirement():
    assert _has_role(ROLE_ADMIN, ROLE_VIEWER)
    assert _has_role(ROLE_OPERATOR, ROLE_VIEWER)
    assert _has_role(ROLE_ADMIN, ROLE_OPERATOR)


def test_lower_role_fails_higher_requirement():
    assert not _has_role(ROLE_VIEWER, ROLE_OPERATOR)
    assert not _has_role(ROLE_VIEWER, ROLE_ADMIN)
    assert not _has_role(ROLE_OPERATOR, ROLE_ADMIN)


def test_unknown_role_fails():
    assert not _has_role("GUEST", ROLE_VIEWER)


def test_github_request_retries_then_succeeds(monkeypatch):
    """一時的な接続エラーが続いた後に成功すれば、その応答を返す。"""
    calls = {"n": 0}
    sentinel = object()

    async def fake_request(self, method, url, **kwargs):
        calls["n"] += 1
        if calls["n"] < 3:
            raise httpx.ConnectTimeout("boom")
        return sentinel

    monkeypatch.setattr(httpx.AsyncClient, "request", fake_request)
    monkeypatch.setattr(auth_router.asyncio, "sleep", _noop_sleep)

    result = asyncio.run(auth_router._github_request("GET", "https://example"))
    assert result is sentinel
    assert calls["n"] == 3


def test_github_request_raises_502_after_exhausting_retries(monkeypatch):
    """全試行が接続エラーなら 502 を送出する。"""

    async def always_fail(self, method, url, **kwargs):
        raise httpx.ConnectTimeout("boom")

    monkeypatch.setattr(httpx.AsyncClient, "request", always_fail)
    monkeypatch.setattr(auth_router.asyncio, "sleep", _noop_sleep)

    with pytest.raises(Exception) as exc_info:
        asyncio.run(auth_router._github_request("GET", "https://example"))
    assert getattr(exc_info.value, "status_code", None) == 502


async def _noop_sleep(_seconds):
    return None
