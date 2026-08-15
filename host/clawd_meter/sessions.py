"""Live session registry, read straight from Claude Code's own state files.

Every running Claude Code process maintains ``~/.claude/sessions/<pid>.json``::

    {"pid": 17066, "sessionId": "d6e2a7d2-...", "cwd": "/Users/x/Code",
     "name": "code-e4", "nameSource": "derived", "status": "busy",
     "kind": "interactive", "updatedAt": 1786797096280, ...}

This costs nothing to read and needs no configuration, which is what makes the
statusline shim optional: session liveness, status and labels come from here,
and only the 5h/7d plan percentages still require the shim.

Two things about these files are load-bearing:

1. ``updatedAt`` is written when the status *changes*, not on a heartbeat. An
   idle session that nobody has touched for three days keeps a three-day-old
   ``updatedAt`` while its process is very much alive. Liveness therefore comes
   from the pid, never from the timestamp.

2. ``status`` is Claude Code's own view of whether it is processing right now,
   which beats inferring it from a transcript that may end mid-tool-call.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path

DEFAULT_DIR = Path.home() / ".claude" / "sessions"

STATUS_BUSY = "busy"
STATUS_IDLE = "idle"


@dataclass
class LiveSession:
    pid: int
    session_id: str
    name: str = ""
    name_source: str = ""
    cwd: str = ""
    status: str = ""
    kind: str = ""

    @property
    def busy(self) -> bool:
        return self.status == STATUS_BUSY

    @property
    def named_by_user(self) -> bool:
        """True when the name came from ``--name`` or ``/rename``.

        A derived name is something like ``code-e4``, which is worse than the
        transcript's AI-generated title. A name the user chose is better than
        both, so the two cases have to be told apart.
        """
        return bool(self.name) and self.name_source not in ("", "derived")


def _alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        # Alive, owned by somebody else. Not one of ours, but not a corpse.
        return True
    except OSError:
        return False
    return True


def _parse(path: Path) -> LiveSession | None:
    try:
        data = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(data, dict):
        return None

    session_id = data.get("sessionId")
    pid = data.get("pid")
    if not isinstance(session_id, str) or not session_id:
        return None
    if not isinstance(pid, int):
        return None

    def _str(key: str) -> str:
        value = data.get(key)
        return value if isinstance(value, str) else ""

    return LiveSession(
        pid=pid,
        session_id=session_id,
        name=_str("name"),
        name_source=_str("nameSource"),
        cwd=_str("cwd"),
        status=_str("status"),
        kind=_str("kind"),
    )


def read(directory: Path | None = None) -> dict[str, LiveSession]:
    """Every session whose process is still running, keyed by session id.

    A pid that has been recycled by an unrelated process would show up as a
    phantom session. The window for that is small and the cost is one stale
    ring, which is cheaper than shelling out to ``ps`` every few seconds.
    """
    directory = directory or resolve_dir()
    if not directory.is_dir():
        return {}

    live: dict[str, LiveSession] = {}
    for path in directory.glob("*.json"):
        session = _parse(path)
        if session is None or not _alive(session.pid):
            continue
        # Two files can name the same session if a pid was reused; the last
        # one wins, which matches "most recently written file is most true".
        live[session.session_id] = session
    return live


def available(directory: Path | None = None) -> bool:
    """Whether the registry exists at all.

    This has to be told apart from "the registry is empty": an empty registry
    is a real answer meaning nothing is open, while a missing one means this
    Claude Code does not publish session files and liveness has to be guessed
    from transcripts instead.
    """
    return (directory or resolve_dir()).is_dir()


def resolve_dir() -> Path:
    override = os.environ.get("CLAWD_SESSIONS_DIR")
    return Path(override).expanduser() if override else DEFAULT_DIR
