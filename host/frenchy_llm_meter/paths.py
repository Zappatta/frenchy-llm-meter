"""Which Claude Code profile the meter reads.

Claude Code keeps all of its state under one config directory — ``~/.claude``
by default, or whatever ``CLAUDE_CONFIG_DIR`` points at. People who run two
accounts side by side (a personal Max plan and a work account on credits, say)
have two of these, and the meter has to be told which one it is watching.

Resolution order, first match wins:

``FRENCHY_CLAUDE_DIR``
    Set by the daemon from ``--claude-dir``, which is what the installer writes
    into the launchd job. A launchd job inherits no shell environment, so this
    is the only spelling that reliably reaches the service.

``CLAUDE_CONFIG_DIR``
    Claude Code's own override, honoured so a hand-run ``--dry-run`` from a
    shell that already selects a profile watches that profile.

``~/.claude``
    Claude Code's default.
"""

from __future__ import annotations

import os
from pathlib import Path

DEFAULT_CLAUDE_DIR = Path.home() / ".claude"


def claude_dir() -> Path:
    for var in ("FRENCHY_CLAUDE_DIR", "CLAUDE_CONFIG_DIR"):
        value = os.environ.get(var)
        if value:
            return Path(value).expanduser()
    return DEFAULT_CLAUDE_DIR


def profile_slug(directory: Path | None = None) -> str:
    """A filesystem-safe name for a profile, for per-profile state files.

    ``~/.claude`` becomes ``claude`` and ``~/.claude-work`` becomes
    ``claude-work``. Only the last path component is used: two profiles with
    the same directory name in different places is not a setup worth the
    longer, uglier names it would take to tell apart.
    """
    name = (directory or claude_dir()).name.lstrip(".")
    return "".join(c if c.isalnum() or c in "-_" else "_" for c in name) or "claude"
