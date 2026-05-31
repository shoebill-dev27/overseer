"""PatternMatcher の検知ロジックのテスト。"""

import textwrap

from pattern_matcher import PatternMatcher


def _write_patterns(tmp_path, body: str):
    p = tmp_path / "patterns.yaml"
    p.write_text(textwrap.dedent(body))
    return str(p)


def test_matches_approval(tmp_path):
    path = _write_patterns(
        tmp_path,
        """
        patterns:
          - name: approval_proceed
            regex: "Do you want to proceed\\\\?"
            category: APPROVAL
        """,
    )
    m = PatternMatcher(path)
    result = m.match("...\nDo you want to proceed?")
    assert result is not None
    assert result.pattern_name == "approval_proceed"
    assert result.category == "APPROVAL"


def test_no_match_returns_none(tmp_path):
    path = _write_patterns(
        tmp_path,
        """
        patterns:
          - name: approval_proceed
            regex: "Do you want to proceed\\\\?"
            category: APPROVAL
        """,
    )
    m = PatternMatcher(path)
    assert m.match("just running normally") is None


def test_first_match_wins(tmp_path):
    path = _write_patterns(
        tmp_path,
        """
        patterns:
          - name: first
            regex: "ready"
            category: A
          - name: second
            regex: "ready"
            category: B
        """,
    )
    m = PatternMatcher(path)
    assert m.match("ready").pattern_name == "first"


def test_invalid_regex_is_skipped(tmp_path):
    path = _write_patterns(
        tmp_path,
        """
        patterns:
          - name: broken
            regex: "([unterminated"
            category: X
          - name: good
            regex: "valid"
            category: Y
        """,
    )
    m = PatternMatcher(path)
    # 不正な正規表現はスキップされ、正常なものだけ残る
    assert m.match("valid").pattern_name == "good"


def test_real_waiting_patterns_load_and_detect(tmp_path):
    # リポジトリ同梱の本番パターンが読み込め、代表ケースを検知できること
    from pathlib import Path

    real = Path(__file__).resolve().parents[2] / "config" / "waiting_patterns.yaml"
    m = PatternMatcher(str(real))
    assert m.match("Do you want to proceed?") is not None
    assert m.match("Press Enter to continue") is not None
    assert m.match("nothing interesting here") is None
