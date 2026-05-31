"""ロール階層判定 _has_role の単体テスト。"""

from app.auth import ROLE_ADMIN, ROLE_OPERATOR, ROLE_VIEWER, _has_role


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
