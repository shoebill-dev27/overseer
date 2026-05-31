"""tmux session monitoring via capture-pane."""

import subprocess


def list_sessions(prefix: str) -> list[str]:
    """Return tmux session names that start with the given prefix."""
    result = subprocess.run(
        ["tmux", "ls", "-F", "#{session_name}"],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        # tmux server not running or no sessions
        return []
    return [s for s in result.stdout.strip().splitlines() if s.startswith(prefix)]


def capture_pane(session_name: str, lines: int = 300) -> list[str]:
    """Return the last N lines of a tmux pane as a list of strings."""
    result = subprocess.run(
        ["tmux", "capture-pane", "-pt", session_name, "-S", f"-{lines}"],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        return []
    return result.stdout.splitlines()


def send_keys(session_name: str, keys: list[str]) -> bool:
    """Send key sequences to a tmux session. Returns True on success.

    keys は tmux send-keys にそのまま渡すトークン列。
    （例: ["y", "Enter"] / ["Enter"] / ["Escape"]）
    """
    result = subprocess.run(
        ["tmux", "send-keys", "-t", session_name, *keys],
        capture_output=True,
        text=True,
    )
    return result.returncode == 0
