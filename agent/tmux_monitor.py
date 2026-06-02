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

    keys is a token sequence passed directly to tmux send-keys.
    (e.g. ["y", "Enter"] / ["Enter"] / ["Escape"])
    """
    result = subprocess.run(
        ["tmux", "send-keys", "-t", session_name, *keys],
        capture_output=True,
        text=True,
    )
    return result.returncode == 0


# Dedicated tmux buffer name for SEND_TEXT (avoids clashing with the user's copy buffer)
_SEND_BUFFER = "overseer-send"


def send_text(session_name: str, text: str) -> bool:
    """Type literal text into a tmux session, then submit with Enter.

    Passing text directly into argv as `send-keys -l <text>` risks strings starting with
    `-R` etc. being misinterpreted as send-keys options (send-keys does not treat `--` as
    an end-of-options marker). To avoid this, load it into a tmux buffer via stdin and
    paste it with paste-buffer.
    """
    load = subprocess.run(
        ["tmux", "load-buffer", "-b", _SEND_BUFFER, "-"],
        input=text,
        capture_output=True,
        text=True,
    )
    if load.returncode != 0:
        return False
    # -d discards the buffer after pasting.
    paste = subprocess.run(
        ["tmux", "paste-buffer", "-d", "-b", _SEND_BUFFER, "-t", session_name],
        capture_output=True,
        text=True,
    )
    if paste.returncode != 0:
        return False
    enter = subprocess.run(
        ["tmux", "send-keys", "-t", session_name, "Enter"],
        capture_output=True,
        text=True,
    )
    return enter.returncode == 0
