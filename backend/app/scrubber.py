"""Secret scrubbing for logs and snapshots.

Applied before any data is written to the DB or sent in notifications.
Replaces matched secrets with [REDACTED].
"""

import re

_PATTERNS: list[re.Pattern[str]] = [
    # OpenAI / Anthropic / generic sk- keys
    re.compile(r"sk-[A-Za-z0-9]{20,}", re.ASCII),
    # Bearer tokens
    re.compile(r"Bearer\s+[A-Za-z0-9._\-]{10,}", re.IGNORECASE),
    # AWS access key IDs
    re.compile(r"AKIA[0-9A-Z]{16}", re.ASCII),
    # AWS secret keys (common prefix patterns)
    re.compile(r"aws_secret[_\s]*=\s*\S{8,}", re.IGNORECASE),
    # Generic .env-style assignments with long values
    re.compile(r"(?<![A-Z_])[A-Z][A-Z0-9_]{3,}=\S{8,}", re.ASCII),
    # password / passwd / pwd assignments
    re.compile(r"(?:password|passwd|pwd)\s*[:=]\s*\S+", re.IGNORECASE),
    # GitHub tokens
    re.compile(r"gh[pousr]_[A-Za-z0-9]{36,}", re.ASCII),
    # Generic token= patterns
    re.compile(r"token\s*[:=]\s*[A-Za-z0-9._\-]{10,}", re.IGNORECASE),
]

_REPLACEMENT = "[REDACTED]"


def scrub(text: str) -> str:
    """Return text with secrets replaced by [REDACTED]."""
    for pattern in _PATTERNS:
        text = pattern.sub(_REPLACEMENT, text)
    return text


def scrub_lines(lines: list[str]) -> list[str]:
    return [scrub(line) for line in lines]
