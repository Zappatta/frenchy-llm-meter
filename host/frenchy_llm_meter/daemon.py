"""Poll transcripts, compute plan usage, push it to the crab."""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path

from .ble import DEVICE_NAME, Link
from .cost import CostTracker
from .paths import claude_dir, profile_slug
from .plan import MODE_CREDITS, MODE_PLAN, MODES, PlanMeter
from .protocol import (
    FLAG_HOST_ERROR,
    FLAG_LIMIT_WARN,
    FLAG_NO_BASELINE,
    FLAG_NO_USAGE,
    FLAG_STALE,
    StateFrame,
)
from .transcripts import SessionState, TranscriptReader, resolve_root
from .usage import STATE_DIR, USAGE_FILE, hook_installed

log = logging.getLogger("frenchy_llm_meter")

DEFAULT_INTERVAL = 5.0

_STATE_NAMES = {
    SessionState.IDLE: "idle",
    SessionState.WORKING: "working",
    SessionState.WAITING: "waiting",
    SessionState.ERROR: "error",
}


def _hms(minutes: int) -> str:
    return f"{minutes // 60}h{minutes % 60:02d}m"


def _usd(cents: int) -> str:
    return f"${cents / 100:,.2f}"


def _render_credits(frame: StateFrame) -> str:
    head = f"today {_usd(frame.today_cents)}  month {_usd(frame.month_cents)}"
    if frame.flags & FLAG_NO_BASELINE:
        head += "  (no baseline yet)"
    else:
        head += f"  gauge {frame.pct_5h_x10 / 10:.0f}%"
    if frame.flags & FLAG_LIMIT_WARN:
        head += " WARN"
    return head + f"  midnight in {_hms(frame.resets_5h_min)}"


def render(frame: StateFrame) -> str:
    """One-line summary for logs and --dry-run."""
    if frame.credits:
        head = _render_credits(frame)
    elif frame.flags & FLAG_NO_USAGE:
        head = "5h --%  7d --% (statusline shim not installed)"
    else:
        note = ""
        if frame.flags & FLAG_STALE:
            note = " (stale)"
        elif frame.flags & FLAG_LIMIT_WARN:
            note = " WARN"
        head = (
            f"5h {frame.pct_5h_x10 / 10:.1f}% resets {_hms(frame.resets_5h_min)}  "
            f"7d {frame.pct_7d_x10 / 10:.1f}% resets {_hms(frame.resets_7d_min)}{note}"
        )
    if not frame.sessions:
        return head + "  no active sessions"
    parts = [
        f"{s.label}={_STATE_NAMES.get(SessionState(s.state), '?')}"
        f":ctx{s.ctx_pct}%"
        for s in frame.sessions
    ]
    return head + "  " + "  ".join(parts)


def _error_frame(last_good: StateFrame | None) -> StateFrame:
    """Hold the last good figures rather than blanking the meter.

    A bare ``StateFrame(flags=FLAG_HOST_ERROR)`` decodes firmware-side as a
    perfectly valid frame with no sessions and zero usage, so a host-side read
    failure made the crab confidently report that nothing was running. Only the
    LED disagreed. Holding the last reading and flagging it matches what the
    firmware already does when the link itself drops.
    """
    if last_good is None:
        return StateFrame(flags=FLAG_HOST_ERROR)
    return replace(
        last_good, flags=last_good.flags | FLAG_HOST_ERROR | FLAG_STALE
    )


def ledger_path() -> Path:
    """Per profile, so switching profiles never mixes two accounts' spend."""
    return STATE_DIR / f"cost-{profile_slug()}.json"


async def run(
    interval: float,
    dry_run: bool,
    device_name: str,
    mode: str = MODE_PLAN,
    daily_budget: float | None = None,
) -> int:
    cost = CostTracker(ledger_path()) if mode == MODE_CREDITS else None
    reader = TranscriptReader(
        resolve_root(), on_event=cost.add_event if cost is not None else None
    )
    meter = PlanMeter(mode, cost, daily_budget)
    link = None if dry_run else Link(device_name)
    last_good: StateFrame | None = None

    log.info("watching %s in %s mode", claude_dir(), mode)
    if mode == MODE_CREDITS:
        log.info(
            "spend is estimated at list price from transcripts; ledger at %s",
            ledger_path(),
        )
    elif not hook_installed():
        log.info(
            "plan usage off: no statusline capture at %s. Rings, states and "
            "labels work without it; add the 5h/7d figures with "
            "`python3 install.py --with-usage`",
            USAGE_FILE,
        )

    try:
        while True:
            now = datetime.now(timezone.utc)
            try:
                sessions = reader.poll(now)
                frame = meter.snapshot(sessions, now)
                if cost is not None:
                    cost.flush(now)
                last_good = frame
            except Exception:  # a bad transcript must not kill the daemon
                log.exception("failed to read usage")
                frame = _error_frame(last_good)

            log.info("%s", render(frame))

            if link is not None:
                if await link.ensure_connected():
                    await link.send(frame.encode())

            await asyncio.sleep(interval)
    except asyncio.CancelledError:
        pass
    finally:
        if cost is not None:
            cost.flush(datetime.now(timezone.utc), force=True)
        if link is not None:
            await link.close()
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="frenchy-llm-meter", description="Push Claude plan usage to the frenchy-llm-meter."
    )
    parser.add_argument(
        "--interval", type=float, default=DEFAULT_INTERVAL, help="seconds between polls"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="print state to stdout instead of connecting over BLE",
    )
    parser.add_argument("--device", default=DEVICE_NAME, help="BLE device name")
    parser.add_argument(
        "--mode",
        choices=MODES,
        default=os.environ.get("FRENCHY_MODE", MODE_PLAN),
        help="plan: 5h/7d limits of a Pro/Max plan (default). credits: dollars "
        "spent today, for an account billed per use.",
    )
    parser.add_argument(
        "--claude-dir",
        help="Claude Code config dir to watch (default: $CLAUDE_CONFIG_DIR, "
        "else ~/.claude)",
    )
    parser.add_argument(
        "--daily-budget",
        type=float,
        help="credits mode: scale the gauge to this many dollars a day until "
        "a week of history exists to scale it to a normal day instead",
    )
    parser.add_argument(
        "--probe",
        action="store_true",
        help="diagnose a link that will not come up: report how far the connect "
        "sequence gets, then exit. Stop the daemon first — two centrals "
        "competing for one connection slot muddies the result.",
    )
    parser.add_argument(
        "--probe-attempts", type=int, default=3, help="connects to try with --probe"
    )
    parser.add_argument(
        "--probe-write",
        action="store_true",
        help="with --probe, also test the write path. Opt-in: it touches the "
        "characteristic the daemon owns.",
    )
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(message)s",
        datefmt="%H:%M:%S",
    )

    if args.claude_dir:
        # Through the environment rather than threaded through every reader:
        # sessions, transcripts and the profile slug all resolve it the same way.
        os.environ["FRENCHY_CLAUDE_DIR"] = str(Path(args.claude_dir).expanduser())

    try:
        if args.probe:
            from .probe import run as probe_run

            return asyncio.run(
                probe_run(args.probe_attempts, args.probe_write, 3.0, args.device)
            )
        return asyncio.run(
            run(args.interval, args.dry_run, args.device, args.mode, args.daily_budget)
        )
    except KeyboardInterrupt:
        return 0


if __name__ == "__main__":
    sys.exit(main())
