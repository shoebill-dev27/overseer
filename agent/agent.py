"""Overseer Local Agent — tmux session monitor."""

import asyncio
import os
import subprocess
import sys
from pathlib import Path

import yaml

from client import AGENT_VERSION, check_backend_reachable, push_session_update, send_heartbeat
from pattern_matcher import PatternMatcher
from scrubber import scrub
from tmux_monitor import capture_pane, list_sessions

_BASE = Path(__file__).parent.parent
_AGENT_CONFIG = _BASE / "config" / "agent.yaml"
_PATTERNS_CONFIG = _BASE / "config" / "waiting_patterns.yaml"


# ── Config ────────────────────────────────────────────────────────────────────


def load_config() -> dict:
    with open(_AGENT_CONFIG) as f:
        return yaml.safe_load(f)


# ── Startup check ─────────────────────────────────────────────────────────────


def startup_check() -> str:
    """Print per-item results and return HEALTHY | DEGRADED | FAILED."""
    results: dict[str, bool] = {}

    try:
        subprocess.run(["tmux", "-V"], capture_output=True, check=True)
        results["tmux"] = True
    except (FileNotFoundError, subprocess.CalledProcessError):
        results["tmux"] = False

    results["AGENT_HMAC_SECRET"] = bool(os.getenv("AGENT_HMAC_SECRET"))
    results["waiting_patterns.yaml"] = _PATTERNS_CONFIG.exists()
    results["agent.yaml"] = _AGENT_CONFIG.exists()

    for name, ok in results.items():
        print(f"  [startup] {'OK  ' if ok else 'FAIL'} {name}")

    if not results["tmux"] or not results["AGENT_HMAC_SECRET"]:
        return "FAILED"
    if not all(results.values()):
        return "DEGRADED"
    return "HEALTHY"


# ── Main loop ─────────────────────────────────────────────────────────────────


async def main() -> None:
    print(f"[agent] Overseer Local Agent v{AGENT_VERSION}")

    health = startup_check()
    print(f"[startup] Status: {health}")
    if health == "FAILED":
        sys.exit(1)

    config = load_config()
    monitoring = config["monitoring"]
    backend_cfg = config["backend"]

    prefix: str = monitoring["session_prefix"]
    poll_interval: int = monitoring["poll_interval_seconds"]
    capture_lines: int = monitoring["capture_lines"]
    pattern_reload: int = monitoring["pattern_reload_seconds"]
    heartbeat_interval: int = backend_cfg["heartbeat_interval_seconds"]

    # Check backend reachability (warning only — agent still starts)
    if await check_backend_reachable():
        print("[agent] Backend reachable ✓")
    else:
        print("[agent] WARNING: Backend not reachable at startup. Will retry each poll.")

    matcher = PatternMatcher(str(_PATTERNS_CONFIG), reload_interval=pattern_reload)

    # State tracking for FINISHED detection and notification dedup
    known_sessions: set[str] = set()

    # last_status[tmux_name] — tracks previous status for transition detection
    last_status: dict[str, str] = {}

    last_heartbeat: float = 0.0

    while True:
        loop_start = asyncio.get_event_loop().time()

        # ── Heartbeat ──────────────────────────────────────────────────────
        if loop_start - last_heartbeat >= heartbeat_interval:
            try:
                await send_heartbeat()
                last_heartbeat = loop_start
            except Exception as e:
                print(f"[agent] Heartbeat error: {e}")

        # ── Pattern reload ─────────────────────────────────────────────────
        matcher.maybe_reload()

        # ── Session discovery ──────────────────────────────────────────────
        current_sessions = set(list_sessions(prefix))

        # Detect sessions that disappeared since last poll → FINISHED
        for gone in known_sessions - current_sessions:
            prev = last_status.get(gone)
            if prev != "FINISHED":
                print(f"[agent] {gone}: FINISHED (session gone)")
                try:
                    await push_session_update(tmux_name=gone, status="FINISHED")
                except Exception as e:
                    print(f"[agent] Push error for {gone}: {e}")
                last_status[gone] = "FINISHED"

        # ── Poll each session ──────────────────────────────────────────────
        for session_name in sorted(current_sessions):
            lines = capture_pane(session_name, capture_lines)
            text = "\n".join(lines)

            match = matcher.match(text)
            if match:
                status = "WAITING_FOR_INPUT"
                waiting_category = match.category
                waiting_pattern = match.pattern_name
            else:
                status = "RUNNING"
                waiting_category = None
                waiting_pattern = None

            prev = last_status.get(session_name)
            if status != prev:
                print(f"[agent] {session_name}: {prev} → {status}"
                      + (f" ({waiting_pattern})" if waiting_pattern else ""))

            # Scrub before sending
            scrubbed = [scrub(line) for line in lines]

            try:
                await push_session_update(
                    tmux_name=session_name,
                    status=status,
                    waiting_category=waiting_category,
                    waiting_pattern=waiting_pattern,
                    snapshot_lines=scrubbed,
                )
            except Exception as e:
                print(f"[agent] Push error for {session_name}: {e}")

            last_status[session_name] = status

        known_sessions = current_sessions

        # ── Sleep until next poll ──────────────────────────────────────────
        elapsed = asyncio.get_event_loop().time() - loop_start
        sleep_time = max(0.0, poll_interval - elapsed)
        await asyncio.sleep(sleep_time)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n[agent] Stopped.")
