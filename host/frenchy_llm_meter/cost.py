"""Daily spend for a credits account, and what a normal day looks like.

A Pro/Max plan has a ceiling and Claude Code reports how close to it you are.
A credits account has neither: there is no 5-hour window, and nothing locally
says what the daily allowance is, because there isn't one. So the meter asks a
different question — *is today a heavier day than usual?* — and answers it
from the transcripts alone:

    today          dollars spent since local midnight
    baseline       the average active day over the previous BASELINE_DAYS
    month          dollars spent since the 1st, local time

The baseline self-calibrates. There is no number for anyone to pick, it moves
as habits change, and a day with nothing in it (a weekend) is left out rather
than dragging the average down.

Two things make this harder than summing tokens:

1. **Claude Code deletes old transcripts** — after 30 days by default
   (``cleanupPeriodDays``). Month-to-date on the 31st would lose the 1st. So
   per-day totals are written to a small ledger under the meter's state dir,
   and a day the transcripts no longer cover keeps the figure it had.

2. **Every restart re-reads every transcript from the start.** The ledger
   therefore never *adds* what it reads to what it stored — it takes the
   larger of the two per day. Within one day spend only climbs, and a
   recomputation from complete transcripts can never exceed the truth, so the
   max is both idempotent and right.
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path

from .pricing import cost_usd

log = logging.getLogger(__name__)

# How many days before today feed the baseline.
BASELINE_DAYS = 14

# Fewer active days than this and there is no baseline: an average of one or
# two days says more about those days than about a normal one.
MIN_BASELINE_DAYS = 3

# A fixed --daily-budget, if one was given, scales the gauge until the
# baseline has at least this many days behind it.
BUDGET_UNTIL_DAYS = 7

# A day that spent less than this is "nothing happened", not an active day.
ACTIVE_DAY_USD = 0.01

# Events older than this are not counted live. Nothing on the meter looks
# further back than the month plus the baseline, and the ledger already holds
# whatever it saw of older days.
LIVE_HORIZON = timedelta(days=45)

# The ledger keeps roughly a year, which is plenty for any question the meter
# asks and keeps the file tiny.
LEDGER_KEEP = timedelta(days=400)

# Rewrite the ledger at most this often. It only matters across restarts.
FLUSH_EVERY = timedelta(minutes=1)


@dataclass
class Spend:
    today: float
    month: float
    baseline: float | None  # average active day, None until there is one
    baseline_days: int      # active days the baseline was taken over

    def ratio_to_baseline(self) -> float | None:
        if self.baseline is None or self.baseline <= 0:
            return None
        return self.today / self.baseline


def _local_day(at: datetime) -> date:
    # Local midnight is the reset the user asked for, and astimezone() with no
    # argument follows the machine's zone — including across DST.
    return at.astimezone().date()


class CostTracker:
    def __init__(self, ledger: Path | None) -> None:
        self.ledger = ledger
        self._stored: dict[date, float] = self._load()
        self._live: dict[date, float] = {}
        # Our own dedup, separate from the transcript reader's: that one forgets
        # after seven days, and a transcript rewritten in place (new inode) is
        # re-read from the start, which would otherwise count its older
        # responses a second time.
        self._seen: dict[tuple[str, str], date] = {}
        self._last_flush: datetime | None = None
        self._dirty = False

    # -- feeding -------------------------------------------------------------

    def add(
        self,
        key: tuple[str, str],
        at: datetime,
        model: str,
        *,
        input_tokens: int,
        output_tokens: int,
        cache_write_5m: int,
        cache_write_1h: int,
        cache_read: int,
        fast: bool = False,
        web_searches: int = 0,
        now: datetime | None = None,
    ) -> None:
        now = now or datetime.now(at.tzinfo)
        if at < now - LIVE_HORIZON:
            return
        day = _local_day(at)
        if key != ("", ""):
            if key in self._seen:
                return
            self._seen[key] = day

        dollars = cost_usd(
            model,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cache_write_5m=cache_write_5m,
            cache_write_1h=cache_write_1h,
            cache_read=cache_read,
            fast=fast,
            web_searches=web_searches,
        )
        if dollars <= 0:
            return
        self._live[day] = self._live.get(day, 0.0) + dollars
        self._dirty = True

    def add_event(self, key: tuple[str, str], event) -> None:
        """Adapter for ``TranscriptReader(on_event=...)``."""
        self.add(
            key,
            event.at,
            event.model,
            input_tokens=event.input_tokens,
            output_tokens=event.output_tokens,
            cache_write_5m=event.cache_write_5m,
            cache_write_1h=event.cache_write_1h,
            cache_read=event.cache_read,
            fast=event.fast,
            web_searches=event.web_searches,
        )

    # -- reading -------------------------------------------------------------

    def _day(self, day: date) -> float:
        return max(self._live.get(day, 0.0), self._stored.get(day, 0.0))

    def spend(self, now: datetime) -> Spend:
        today = _local_day(now)

        month = sum(
            self._day(today - timedelta(days=i))
            for i in range(today.day)  # the 1st through today
        )

        history = [
            self._day(today - timedelta(days=i)) for i in range(1, BASELINE_DAYS + 1)
        ]
        active = [d for d in history if d >= ACTIVE_DAY_USD]
        baseline = sum(active) / len(active) if len(active) >= MIN_BASELINE_DAYS else None

        return Spend(
            today=self._day(today),
            month=month,
            baseline=baseline,
            baseline_days=len(active),
        )

    # -- persistence ---------------------------------------------------------

    def _load(self) -> dict[date, float]:
        if self.ledger is None:
            return {}
        try:
            data = json.loads(self.ledger.read_text())
        except (OSError, json.JSONDecodeError):
            return {}
        days = data.get("days") if isinstance(data, dict) else None
        if not isinstance(days, dict):
            return {}
        out: dict[date, float] = {}
        for raw, value in days.items():
            try:
                out[date.fromisoformat(raw)] = float(value)
            except (TypeError, ValueError):
                continue
        return out

    def flush(self, now: datetime, force: bool = False) -> None:
        """Fold what was read into the ledger and write it, at most once a minute."""
        if self.ledger is None or not self._dirty:
            return
        if (
            not force
            and self._last_flush is not None
            and now - self._last_flush < FLUSH_EVERY
        ):
            return

        today = _local_day(now)
        merged = dict(self._stored)
        for day, value in self._live.items():
            merged[day] = max(merged.get(day, 0.0), value)
        merged = {d: v for d, v in merged.items() if d >= today - LEDGER_KEEP}

        payload = {
            "note": "per-day spend at list price, estimated from transcripts",
            "days": {d.isoformat(): round(v, 4) for d, v in sorted(merged.items())},
        }
        try:
            self.ledger.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.ledger.with_suffix(self.ledger.suffix + ".partial")
            tmp.write_text(json.dumps(payload, indent=1) + "\n")
            os.replace(tmp, self.ledger)
        except OSError:
            log.warning("could not write cost ledger %s", self.ledger, exc_info=True)
            return

        self._stored = merged
        self._last_flush = now
        self._dirty = False

        # The dedup keys only need to outlive the live horizon.
        horizon = today - LIVE_HORIZON
        self._seen = {k: d for k, d in self._seen.items() if d >= horizon}
        self._live = {d: v for d, v in self._live.items() if d >= horizon}


def minutes_to_midnight(now: datetime) -> int:
    local = now.astimezone()
    midnight = datetime.combine(
        local.date() + timedelta(days=1), datetime.min.time(), tzinfo=local.tzinfo
    )
    return max(0, int((midnight - local).total_seconds()) // 60)
