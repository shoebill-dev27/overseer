"""Secret scrubbing — mirrors backend/app/scrubber.py.

Applied to snapshot content before sending to backend.
"""

import re

_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"sk-[A-Za-z0-9]{20,}", re.ASCII),
    re.compile(r"Bearer\s+[A-Za-z0-9._\-]{10,}", re.IGNORECASE),
    re.compile(r"AKIA[0-9A-Z]{16}", re.ASCII),
    re.compile(r"aws_secret[_\s]*=\s*\S{8,}", re.IGNORECASE),
    re.compile(r"(?<![A-Z_])[A-Z][A-Z0-9_]{3,}=\S{8,}", re.ASCII),
    re.compile(r"(?:password|passwd|pwd)\s*[:=]\s*\S+", re.IGNORECASE),
    re.compile(r"gh[pousr]_[A-Za-z0-9]{36,}", re.ASCII),
    re.compile(r"token\s*[:=]\s*[A-Za-z0-9._\-]{10,}", re.IGNORECASE),
]

_REPLACEMENT = "[REDACTED]"


def scrub(text: str) -> str:
    for pattern in _PATTERNS:
        text = pattern.sub(_REPLACEMENT, text)
    return text
