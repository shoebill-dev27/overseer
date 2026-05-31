"""Overseer Local Agent — tmux session monitor."""

import asyncio
import os
import subprocess
import sys
from pathlib import Path

import yaml

from client import (
    AGENT_VERSION,
    check_backend_reachable,
    fetch_pending_actions,
    push_session_update,
    report_action_result,
    send_heartbeat,
)
from pattern_matcher import PatternMatcher
from scrubber import scrub
from tmux_monitor import capture_pane, list_sessions, send_keys

_BASE = Path(__file__).parent.parent
_AGENT_CONFIG = _BASE / "config" / "agent.yaml"
_PATTERNS_CONFIG = _BASE / "config" / "waiting_patterns.yaml"

# アクション種別 → tmux send-keys のトークン列
# STOP は Claude Code の中断キー（Escape）。
_ACTION_KEYS: dict[str, list[str]] = {
    "SEND_Y": ["y", "Enter"],
    "SEND_N": ["n", "Enter"],
    "SEND_ENTER": ["Enter"],
    "STOP": ["Escape"],
}


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


# ── Action execution ────────────────────────────────────────────────────────


async def execute_pending_actions(action_flags: dict[str, bool]) -> None:
    """確認済み操作を取得し、設定で許可されたものを tmux に送信して結果を報告する。

    action_flags は agent.yaml の actions セクション。Agent 側を最終ゲートとして、
    無効化された種別は実行せず FAILED を報告する。
    """
    try:
        actions = await fetch_pending_actions()
    except Exception as e:
        print(f"[agent] Fetch actions error: {e}")
        return

    for action in actions:
        action_id = action["id"]
        atype = action["action_type"]
        tmux_name = action["tmux_name"]

        if not action_flags.get(atype, False):
            print(f"[agent] action {action_id} ({atype}): disabled in config → FAILED")
            await _report(action_id, "FAILED", "action type disabled in agent config")
            continue

        keys = _ACTION_KEYS.get(atype)
        if keys is None:
            await _report(action_id, "FAILED", f"unknown action type: {atype}")
            continue

        ok = send_keys(tmux_name, keys)
        print(
            f"[agent] action {action_id} ({atype}) → {tmux_name}: "
            f"{'EXECUTED' if ok else 'FAILED'}"
        )
        await _report(
            action_id,
            "EXECUTED" if ok else "FAILED",
            None if ok else "tmux send-keys failed",
        )


async def _report(action_id: int, status: str, reason: str | None) -> None:
    try:
        await report_action_result(action_id, status, reason)
    except Exception as e:
        print(f"[agent] Report result error for action {action_id}: {e}")


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
    action_flags: dict[str, bool] = config.get("actions", {})

    # Check backend reachability (warning only — agent still starts)
    if await check_backend_reachable():
        print("[agent] Backend reachable ✓")
    else:
        print(
            "[agent] WARNING: Backend not reachable at startup. Will retry each poll."
        )

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
                print(
                    f"[agent] {session_name}: {prev} → {status}"
                    + (f" ({waiting_pattern})" if waiting_pattern else "")
                )

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

        # ── Execute confirmed actions ──────────────────────────────────────
        await execute_pending_actions(action_flags)

        # ── Sleep until next poll ──────────────────────────────────────────
        elapsed = asyncio.get_event_loop().time() - loop_start
        sleep_time = max(0.0, poll_interval - elapsed)
        await asyncio.sleep(sleep_time)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n[agent] Stopped.")
