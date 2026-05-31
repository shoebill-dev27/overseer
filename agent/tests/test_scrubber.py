"""agent 側 scrubber のテスト（backend と同一の挙動を担保）。"""

from scrubber import scrub


def test_redacts_sk_key():
    assert "[REDACTED]" in scrub("sk-abcdefghij1234567890ABCDEFG")


def test_redacts_aws_access_key_id():
    assert scrub("AKIAIOSFODNN7EXAMPLE") == "[REDACTED]"


def test_redacts_password_assignment():
    assert "[REDACTED]" in scrub("password=hunter2secret")


def test_leaves_normal_text_unchanged():
    text = "Building project... done"
    assert scrub(text) == text
