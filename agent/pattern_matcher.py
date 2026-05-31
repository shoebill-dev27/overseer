"""YAML-based WAITING state detection with dynamic reload.

Reload interval is controlled by agent.yaml (pattern_reload_seconds).
"""

import re
import time
from dataclasses import dataclass
from pathlib import Path

import yaml


@dataclass
class MatchResult:
    pattern_name: str
    category: str


class PatternMatcher:
    def __init__(self, yaml_path: str, reload_interval: int = 60) -> None:
        self._path = Path(yaml_path)
        self._reload_interval = reload_interval
        self._last_loaded: float = 0.0
        self._compiled: list[tuple[str, str, re.Pattern[str]]] = []
        self._load()

    def _load(self) -> None:
        with open(self._path) as f:
            data = yaml.safe_load(f)

        compiled = []
        for p in data.get("patterns", []):
            try:
                compiled.append(
                    (
                        p["name"],
                        p["category"],
                        re.compile(p["regex"], re.MULTILINE),
                    )
                )
            except re.error as e:
                print(f"[patterns] Invalid regex in '{p['name']}': {e}")

        self._compiled = compiled
        self._last_loaded = time.monotonic()
        print(
            f"[patterns] Loaded {len(self._compiled)} patterns from {self._path.name}"
        )

    def maybe_reload(self) -> None:
        if time.monotonic() - self._last_loaded >= self._reload_interval:
            try:
                self._load()
            except Exception as e:
                print(f"[patterns] Reload failed, keeping previous patterns: {e}")

    def match(self, text: str) -> MatchResult | None:
        """Return the first matching pattern, or None if no match."""
        for name, category, pattern in self._compiled:
            if pattern.search(text):
                return MatchResult(pattern_name=name, category=category)
        return None
