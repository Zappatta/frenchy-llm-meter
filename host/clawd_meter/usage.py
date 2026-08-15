"""Authoritative plan usage, captured from Claude Code's statusline payload.

Claude Code hands its statusline command a JSON blob on stdin containing the
real figures::

    rate_limits.five_hour.used_percentage   0..100
    rate_limits.five_hour.resets_at         unix epoch seconds
    rate_limits.seven_day.used_percentage
    rate_limits.seven_day.resets_at

These are the same numbers ``/usage`` reports. ``statusline-hook.sh`` captures
them to a state file; this module reads it.

The reading is only as fresh as the last statusline render, which stops when
no Claude Code session is open. A stale reading is reported as stale rather
than as current — a meter confidently showing an hour-old number is worse than
one admitting it does not know.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

STATE_DIR = Path(
    os.environ.get("CLAWD_STATE_DIR", Path.home() / ".local" / "state" / "clawd-meter")
)
USAGE_FILE = STATE_DIR / "usage.json"

# How old a capture may be before it stops being treated as current. The
# statusline re-renders often while a session is open, so anything older than
# this means every session has been closed for a while.
STALE_AFTER_S = 15 * 60

# A session whose statusline rendered within this window is treated as open.
# Renders are event-driven plus a refreshInterval timer, so a genuinely open
# session touches its entry far more often than this.
OPEN_WITHIN_S = 5 * 60


@dataclass
class Window:
    used_percentage: float
    resets_at: int | None = None

    def resets_in_minutes(self, now: datetime) -> int:
        if not self.resets_at:
            return 0
        remaining = self.resets_at - int(now.timestamp())
        return max(0, remaining // 60)


@dataclass
class Usage:
    five_hour: Window | None
    seven_day: Window | None
    captured_at: int
    # session_id -> (context remaining %, when that session last rendered).
    # Per session, unlike the account-level rate limits, so it is keyed rather
    # than merged. The timestamp doubles as a liveness signal: a session that
    # rendered a statusline recently is open, whatever its transcript says.
    context: dict[str, tuple[float, int]] = field(default_factory=dict)

    def age_seconds(self, now: datetime) -> int:
        return max(0, int(now.timestamp()) - self.captured_at)

    def is_stale(self, now: datetime) -> bool:
        return self.age_seconds(now) > STALE_AFTER_S

    def context_for(self, session_id: str) -> float | None:
        entry = self.context.get(session_id)
        return entry[0] if entry else None

    def open_sessions(self, now: datetime, within_s: int = OPEN_WITHIN_S) -> set[str]:
        """Sessions that rendered a statusline recently, so are still open."""
        cutoff = int(now.timestamp()) - within_s
        return {sid for sid, (_, at) in self.context.items() if at >= cutoff}


def _window(raw: object) -> Window | None:
    if not isinstance(raw, dict):
        return None
    pct = raw.get("used_percentage")
    if not isinstance(pct, (int, float)):
        return None
    resets = raw.get("resets_at")
    return Window(
        used_percentage=float(pct),
        resets_at=int(resets) if isinstance(resets, (int, float)) else None,
    )


def read(path: Path | None = None) -> Usage | None:
    """Load the last captured reading, or None if there isn't a usable one."""
    path = path or USAGE_FILE
    try:
        data = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(data, dict):
        return None

    limits = data.get("rate_limits")
    five = _window(limits.get("five_hour")) if isinstance(limits, dict) else None
    seven = _window(limits.get("seven_day")) if isinstance(limits, dict) else None

    context: dict[str, tuple[float, int]] = {}
    sessions = data.get("sessions")
    if isinstance(sessions, dict):
        for session_id, entry in sessions.items():
            if not isinstance(entry, dict):
                continue
            ctx = entry.get("ctx")
            at = entry.get("at")
            if isinstance(ctx, (int, float)):
                context[str(session_id)] = (
                    float(ctx),
                    int(at) if isinstance(at, (int, float)) else 0,
                )

    if five is None and seven is None and not context:
        return None

    captured = data.get("captured_at")
    return Usage(
        five_hour=five,
        seven_day=seven,
        captured_at=int(captured) if isinstance(captured, (int, float)) else 0,
        context=context,
    )


def hook_installed(path: Path | None = None) -> bool:
    return (path or USAGE_FILE).exists()
