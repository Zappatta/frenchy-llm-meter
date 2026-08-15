"""Read Claude Code session transcripts and derive per-session usage and state.

Layout under ``~/.claude/projects``::

    <project-slug>/<session-uuid>.jsonl                    top-level session
    <project-slug>/<session-uuid>/subagents/agent-*.jsonl  subagent transcripts

Subagent files roll up into their parent session — their tokens are billed to
the same account and belong on the same ring.

Two things about the format are load-bearing:

1. A single API response is written as several ``assistant`` lines (thinking,
   text, tool_use), each repeating the *same* ``requestId`` and
   ``message.id`` and the same ``usage`` object. Summing naively inflates
   token counts by 2-3x, so records are deduplicated on that pair.

2. ``message.stop_reason`` on the last assistant record is what separates
   working from waiting: ``tool_use`` means mid-turn, ``end_turn`` means the
   turn is finished and Claude is waiting on the user.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from enum import IntEnum
from pathlib import Path

from .models import context_window_for

DEFAULT_ROOT = Path.home() / ".claude" / "projects"

# A session whose transcript has been quiet for longer than this is shown as
# idle rather than waiting — Claude finished, but the user has walked away.
IDLE_AFTER = timedelta(minutes=20)

# The two windows a Max plan is metered over.
WINDOW_5H = timedelta(hours=5)
WINDOW_7D = timedelta(days=7)

# Events are kept for the longer of the two windows.
RETENTION = WINDOW_7D


class SessionState(IntEnum):
    IDLE = 0
    WORKING = 1
    WAITING = 2
    ERROR = 3


@dataclass
class _Event:
    """One deduplicated, billable API response."""

    at: datetime
    model: str
    input_tokens: int
    output_tokens: int
    cache_write_5m: int
    cache_write_1h: int
    cache_read: int

    @property
    def tokens(self) -> int:
        """Raw token count, for display only."""
        return (
            self.input_tokens
            + self.output_tokens
            + self.cache_write_5m
            + self.cache_write_1h
            + self.cache_read
        )


@dataclass
class Session:
    session_id: str
    project: str
    cwd: str = ""
    branch: str = ""
    title: str = ""
    events: list[_Event] = field(default_factory=list)
    last_activity: datetime | None = None
    last_stop_reason: str | None = None
    saw_pending_tool_result: bool = False
    # Prompt size and model of the most recent request on the main thread —
    # enough to estimate how full the context window is.
    last_prompt_tokens: int = 0
    last_model: str = ""

    def label(self) -> str:
        """Short human label for the ring legend."""
        if self.title:
            return self.title
        if self.cwd:
            return Path(self.cwd).name
        return self.project.strip("-").split("-")[-1] or self.session_id[:8]

    def window_events(
        self, now: datetime, window: timedelta = WINDOW_5H
    ) -> list[_Event]:
        cutoff = now - window
        return [e for e in self.events if e.at >= cutoff]

    def window_tokens(self, now: datetime, window: timedelta = WINDOW_5H) -> int:
        return sum(e.tokens for e in self.window_events(now, window))

    def estimated_context_remaining(self) -> float | None:
        """Percentage of the context window still free, from the transcript.

        A fallback for sessions with no statusline capture yet. The last
        request's prompt size is the context in use at that moment, which is
        the same quantity the statusline reports — but the real figure accounts
        for details this cannot see, so it is always preferred when present.
        """
        if self.last_prompt_tokens <= 0:
            return None
        window = context_window_for(self.last_model)
        return max(0.0, min(100.0, 100.0 * (1.0 - self.last_prompt_tokens / window)))

    def state(self, now: datetime) -> SessionState:
        if self.last_activity is None:
            return SessionState.IDLE
        if now - self.last_activity > IDLE_AFTER:
            return SessionState.IDLE
        if self.saw_pending_tool_result or self.last_stop_reason == "tool_use":
            return SessionState.WORKING
        if self.last_stop_reason == "end_turn":
            return SessionState.WAITING
        return SessionState.WORKING


def _parse_ts(raw: str | None) -> datetime | None:
    if not raw:
        return None
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None


class TranscriptReader:
    """Incrementally tails every transcript under ``root``.

    Files are large (megabytes) and re-read every few seconds, so each one is
    read from its previous byte offset. The offset is keyed by inode so a
    rotated or replaced file is re-read from the start rather than seeking
    past the end of a shorter file.
    """

    def __init__(self, root: Path | None = None) -> None:
        self.root = root or DEFAULT_ROOT
        self._offsets: dict[Path, tuple[int, int]] = {}
        self._seen: set[tuple[str, str]] = set()
        self._sessions: dict[str, Session] = {}

    def _session_for(self, path: Path) -> Session:
        """Map a transcript path to its owning session.

        ``<slug>/<sid>.jsonl`` is the session itself;
        ``<slug>/<sid>/subagents/agent-*.jsonl`` belongs to session ``<sid>``.
        """
        rel = path.relative_to(self.root)
        project = rel.parts[0]
        if len(rel.parts) == 2:
            session_id = rel.parts[1].removesuffix(".jsonl")
        else:
            session_id = rel.parts[1]

        session = self._sessions.get(session_id)
        if session is None:
            session = Session(session_id=session_id, project=project)
            self._sessions[session_id] = session
        return session

    def _transcript_paths(self) -> list[Path]:
        if not self.root.is_dir():
            return []
        paths = list(self.root.glob("*/*.jsonl"))
        paths.extend(self.root.glob("*/*/subagents/*.jsonl"))
        return paths

    def _ingest_line(self, session: Session, raw: str, is_subagent: bool) -> None:
        try:
            rec = json.loads(raw)
        except json.JSONDecodeError:
            return
        if not isinstance(rec, dict):
            return

        kind = rec.get("type")

        # Metadata records carry no usage but do carry a nice display label.
        if kind == "ai-title" and not is_subagent:
            title = rec.get("aiTitle")
            if isinstance(title, str) and title:
                session.title = title
            return

        if kind not in ("assistant", "user"):
            return

        ts = _parse_ts(rec.get("timestamp"))
        if ts is not None:
            if session.last_activity is None or ts > session.last_activity:
                session.last_activity = ts

        # Only the main thread's turn boundaries describe the session's state.
        # A subagent finishing does not mean the parent wants input.
        if not is_subagent and not rec.get("isSidechain"):
            if not session.cwd and isinstance(rec.get("cwd"), str):
                session.cwd = rec["cwd"]
            if isinstance(rec.get("gitBranch"), str):
                session.branch = rec["gitBranch"]

            message = rec.get("message") or {}
            if kind == "assistant":
                stop = message.get("stop_reason")
                if stop:
                    session.last_stop_reason = stop
                    session.saw_pending_tool_result = False
            elif kind == "user":
                content = message.get("content")
                if isinstance(content, list) and any(
                    isinstance(b, dict) and b.get("type") == "tool_result"
                    for b in content
                ):
                    # A tool result landed; the model is about to run again.
                    session.saw_pending_tool_result = True

        if kind != "assistant":
            return

        message = rec.get("message") or {}
        usage = message.get("usage")
        if not isinstance(usage, dict):
            return

        # Deduplicate: one API response is written as several assistant lines
        # sharing a requestId/message.id pair and repeating the same usage.
        key = (rec.get("requestId") or "", message.get("id") or "")
        if key != ("", "") and key in self._seen:
            return
        if key != ("", ""):
            self._seen.add(key)

        creation = usage.get("cache_creation") or {}

        # Prompt size for this request — everything the model was sent, which
        # is the context in use. Subagents run their own context, so only the
        # main thread's requests describe the session's window.
        if not is_subagent and not rec.get("isSidechain"):
            session.last_prompt_tokens = (
                int(usage.get("input_tokens") or 0)
                + int(usage.get("cache_read_input_tokens") or 0)
                + int(usage.get("cache_creation_input_tokens") or 0)
            )
            session.last_model = message.get("model") or session.last_model

        session.events.append(
            _Event(
                at=ts or datetime.now(timezone.utc),
                model=message.get("model") or "",
                input_tokens=int(usage.get("input_tokens") or 0),
                output_tokens=int(usage.get("output_tokens") or 0),
                cache_write_5m=int(creation.get("ephemeral_5m_input_tokens") or 0),
                cache_write_1h=int(creation.get("ephemeral_1h_input_tokens") or 0),
                cache_read=int(usage.get("cache_read_input_tokens") or 0),
            )
        )

    def poll(self, now: datetime | None = None) -> list[Session]:
        """Read whatever is new and return sessions active in the window."""
        now = now or datetime.now(timezone.utc)

        for path in self._transcript_paths():
            try:
                st = path.stat()
            except OSError:
                continue

            inode, offset = self._offsets.get(path, (st.st_ino, 0))
            if inode != st.st_ino or st.st_size < offset:
                offset = 0  # replaced or truncated — start over
            if st.st_size == offset:
                continue

            session = self._session_for(path)
            is_subagent = "subagents" in path.parts
            try:
                with path.open("r", encoding="utf-8", errors="replace") as fh:
                    fh.seek(offset)
                    for line in fh:
                        if line.endswith("\n"):
                            self._ingest_line(session, line, is_subagent)
                        else:
                            # Partial trailing line: a writer is mid-flush.
                            # Rewind so it is picked up whole next poll.
                            offset = fh.tell() - len(line.encode("utf-8"))
                            break
                    else:
                        offset = fh.tell()
            except OSError:
                continue

            self._offsets[path] = (st.st_ino, offset)

        self._evict(now)
        return list(self._sessions.values())

    def _evict(self, now: datetime) -> None:
        """Drop events and sessions that have fallen out of the window."""
        cutoff = now - RETENTION
        for session in list(self._sessions.values()):
            session.events = [e for e in session.events if e.at >= cutoff]
            if not session.events and (
                session.last_activity is None or session.last_activity < cutoff
            ):
                del self._sessions[session.session_id]


def resolve_root() -> Path:
    override = os.environ.get("CLAWD_PROJECTS_DIR")
    return Path(override).expanduser() if override else DEFAULT_ROOT
