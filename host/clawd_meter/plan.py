"""Assemble the frame the crab draws.

Three sources, in descending order of how much they cost the user to set up:

``sessions.py``
    ``~/.claude/sessions/<pid>.json``, which every running Claude Code process
    maintains. Liveness, status and labels, for free and with no configuration.

``transcripts.py``
    Per-session token history, turn boundaries, and a context estimate.
    Also free.

``usage.py``
    The 5-hour and 7-day plan percentages, captured from the payload Claude
    Code hands its statusline command. These are the same numbers ``/usage``
    reports, and the statusline is the only place they are exposed — not in
    hooks, not in OpenTelemetry, not in any transcript or state file. That is
    why the shim exists, and why it is the one optional part of the install:
    without it the rings still work, and only the plan footer goes dark.

Earlier revisions estimated the ceiling by treating the largest window ever
observed as the limit. That is gone: on a fresh install the first sample
becomes the peak, so the gauge pinned at 100% and the warning LED stuck on red
permanently. It is not worth reviving now that the real number is free.
"""

from __future__ import annotations

from datetime import datetime, timezone

from . import sessions as sessions_source
from . import usage as usage_source
from .protocol import (
    FLAG_LIMIT_WARN,
    FLAG_NO_USAGE,
    FLAG_STALE,
    MAX_SESSIONS,
    SessionFrame,
    StateFrame,
)
from .sessions import LiveSession
from .transcripts import WINDOW_5H, Session, SessionState

# Fraction of a window at which the LED turns red.
WARN_AT = 85.0


def _state_for(
    session: Session, live: LiveSession | None, now: datetime
) -> SessionState:
    """Claude Code's own status, corrected against the transcript.

    ``status`` says whether the process is working right now, which a
    transcript cannot: a session killed mid-tool-call leaves a trailing
    ``tool_use`` stop reason and reads as WORKING forever.
    """
    state = session.state(now)
    if live is None:
        return state
    if live.busy:
        return SessionState.WORKING
    if state is SessionState.WORKING:
        # Not processing, so the turn is over whatever the transcript implied.
        return SessionState.WAITING
    return state


def _label_for(session: Session, live: LiveSession | None) -> str:
    """A name the user chose beats one anybody generated."""
    if live is not None and live.named_by_user:
        return live.name
    return session.label()


class PlanMeter:
    def __init__(self) -> None:
        self._warned_missing = False

    def snapshot(
        self, sessions: list[Session], now: datetime | None = None
    ) -> StateFrame:
        now = now or datetime.now(timezone.utc)

        reading = usage_source.read()
        live = sessions_source.read()
        frame = StateFrame(sessions=self._rings(sessions, now, reading, live))

        if reading is None:
            # No capture yet: the shim is not installed, or no Claude Code
            # session has rendered a statusline since it was. The rings still
            # work; the hub says so rather than inventing a percentage.
            frame.flags |= FLAG_NO_USAGE
            return frame

        five, seven = reading.five_hour, reading.seven_day
        if five is not None:
            frame.pct_5h_x10 = round(five.used_percentage * 10)
            frame.resets_5h_min = min(five.resets_in_minutes(now), 0xFFFF)
        if seven is not None:
            frame.pct_7d_x10 = round(seven.used_percentage * 10)
            frame.resets_7d_min = min(seven.resets_in_minutes(now), 0xFFFF)

        if reading.is_stale(now):
            # Every session has been closed for a while. Show the last known
            # figure, but flag it so the display can mark it as not current.
            frame.flags |= FLAG_STALE
        elif (five and five.used_percentage >= WARN_AT) or (
            seven and seven.used_percentage >= WARN_AT
        ):
            frame.flags |= FLAG_LIMIT_WARN

        return frame

    def _rings(
        self,
        sessions: list[Session],
        now: datetime,
        reading: "usage_source.Usage | None",
        live: dict[str, LiveSession],
    ) -> list[SessionFrame]:
        """One ring per live session, least context on the outside.

        Ring fill is the percentage of that session's context window still
        free, so a ring drains as the session fills up and an almost-empty ring
        is a session about to compact. The figure comes from the statusline
        capture where available — it is the same number Claude Code shows as
        ``ctx:NN%`` — and falls back to a transcript estimate otherwise.
        """
        if sessions_source.available():
            # The registry is definitive in both directions. An open terminal
            # gets a ring however long its transcript has been quiet, and a
            # session with no running process does not get one however recently
            # it was busy.
            #
            # Transcript activity used to stand in for liveness here, and it
            # kept closed sessions on screen for the whole of IDLE_AFTER — 20
            # minutes of rings for terminals that were already shut. An empty
            # registry is a real answer meaning nothing is open, so there is
            # deliberately no fallback on this path.
            active = [s for s in sessions if s.session_id in live]
        else:
            # No registry: this Claude Code does not publish session files, so
            # liveness has to be inferred as it was before they existed.
            open_ids = reading.open_sessions(now) if reading else set()
            active = [
                s
                for s in sessions
                if s.session_id in open_ids or s.state(now) is not SessionState.IDLE
            ]
            if not active:
                active = [s for s in sessions if s.window_tokens(now, WINDOW_5H) > 0]

        frames: list[SessionFrame] = []
        for session in active:
            ctx = reading.context_for(session.session_id) if reading else None
            if ctx is None:
                ctx = session.estimated_context_remaining()
            if ctx is None:
                continue
            info = live.get(session.session_id)
            frames.append(
                SessionFrame(
                    state=int(_state_for(session, info, now)),
                    ctx_pct=round(ctx),
                    tokens=session.window_tokens(now, WINDOW_5H),
                    label=_label_for(session, info),
                )
            )

        # Least headroom on the outside: the ring that runs out first is the
        # one worth seeing first.
        frames.sort(key=lambda f: f.ctx_pct)
        return frames[:MAX_SESSIONS]
