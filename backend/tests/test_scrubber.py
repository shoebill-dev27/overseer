"""Secret-removal tests for scrubber.scrub."""

from app.scrubber import scrub


def test_redacts_sk_key():
    assert "[REDACTED]" in scrub("key: sk-abcdefghij1234567890ABCDEFG")


def test_redacts_aws_access_key_id():
    assert scrub("AKIAIOSFODNN7EXAMPLE") == "[REDACTED]"


def test_redacts_github_token():
    out = scrub("ghp_" + "a" * 40)
    assert "[REDACTED]" in out
    assert "ghp_" not in out


def test_redacts_password_assignment():
    assert "[REDACTED]" in scrub("password: hunter2secret")


def test_redacts_bearer_token():
    assert "[REDACTED]" in scrub("Authorization: Bearer abcdef123456.token")


def test_redacts_env_style_assignment():
    assert "[REDACTED]" in scrub("API_KEY=supersecretvalue123")


def test_leaves_normal_text_unchanged():
    text = "Running tests... 3 passed in 0.12s"
    assert scrub(text) == text


def test_multiple_secrets_in_one_line():
    out = scrub("token=abcdefghij12345 and AKIAIOSFODNN7EXAMPLE here")
    assert out.count("[REDACTED]") >= 2
