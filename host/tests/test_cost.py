"""Credits mode: what a day cost, and whether today is a heavy one."""

import json
from datetime import datetime, timedelta, timezone

import pytest

from frenchy_llm_meter import pricing
from frenchy_llm_meter.cost import CostTracker, minutes_to_midnight
from frenchy_llm_meter.plan import MODE_CREDITS, PlanMeter
from frenchy_llm_meter.protocol import (
    FLAG_CREDITS,
    FLAG_LIMIT_WARN,
    FLAG_NO_BASELINE,
    FLAG_NO_USAGE,
    StateFrame,
    decode,
)

# Midday local time, so no test lands on the wrong side of midnight.
NOW = datetime.now().astimezone().replace(hour=12, minute=0, second=0, microsecond=0)


def _spend(tracker, dollars, at, key=None):
    """Feed one response worth exactly `dollars` (Sonnet 5.5 output tokens)."""
    tracker.add(
        key or (f"req-{at.isoformat()}-{dollars}", "msg"),
        at,
        "claude-sonnet-5-5",
        input_tokens=0,
        output_tokens=round(dollars * 100_000),  # $10/MTok
        cache_write_5m=0,
        cache_write_1h=0,
        cache_read=0,
        now=NOW,
    )


# -- pricing ------------------------------------------------------------------


def test_cache_tiers_are_priced_off_the_input_rate():
    # Opus 5.5: $4 in, $20 out, $0.20 cache read.
    dollars = pricing.cost_usd(
        "claude-opus-5-5",
        input_tokens=1_000_000,
        output_tokens=1_000_000,
        cache_write_5m=1_000_000,
        cache_write_1h=1_000_000,
        cache_read=1_000_000,
    )
    assert dollars == pytest.approx(4 + 20 + 4 * 1.25 + 4 * 2 + 0.20)


def test_dated_snapshot_ids_find_their_row_not_a_shorter_one():
    assert pricing.price_for("claude-opus-4-5-20251101").input == 5.00
    assert pricing.price_for("claude-opus-4-20250514").input == 15.00


def test_fast_mode_doubles_the_bill():
    base = dict(input_tokens=1000, output_tokens=1000, cache_write_5m=0,
                cache_write_1h=0, cache_read=0)
    assert pricing.cost_usd("claude-opus-5-5", fast=True, **base) == pytest.approx(
        2 * pricing.cost_usd("claude-opus-5-5", **base)
    )


def test_unknown_model_of_a_known_family_is_priced_as_its_newest():
    assert pricing.price_for("claude-opus-9-1") == pricing.PRICES["claude-opus-5-5"]


def test_synthetic_messages_cost_nothing():
    assert pricing.price_for("<synthetic>") is None


def test_haiku_long_prompts_use_the_higher_tier():
    short = pricing.cost_usd("claude-haiku-5-5", input_tokens=100_000, output_tokens=0,
                             cache_write_5m=0, cache_write_1h=0, cache_read=0)
    long = pricing.cost_usd("claude-haiku-5-5", input_tokens=100_001, output_tokens=0,
                            cache_write_5m=0, cache_write_1h=0, cache_read=0)
    assert short == pytest.approx(0.01)
    assert long == pytest.approx(100_001 * 0.50 / 1_000_000)


# -- the tracker --------------------------------------------------------------


def test_today_resets_at_local_midnight():
    tracker = CostTracker(None)
    _spend(tracker, 3.0, NOW - timedelta(hours=11))  # 01:00 today
    _spend(tracker, 5.0, NOW - timedelta(hours=13))  # 23:00 yesterday
    assert tracker.spend(NOW).today == pytest.approx(3.0)


def test_one_response_written_as_several_lines_counts_once():
    tracker = CostTracker(None)
    for _ in range(3):
        _spend(tracker, 2.0, NOW - timedelta(hours=1), key=("req-1", "msg-1"))
    assert tracker.spend(NOW).today == pytest.approx(2.0)


def test_no_baseline_until_three_active_days():
    tracker = CostTracker(None)
    for days_ago in (1, 2):
        _spend(tracker, 10.0, NOW - timedelta(days=days_ago))
    assert tracker.spend(NOW).baseline is None

    _spend(tracker, 10.0, NOW - timedelta(days=5))
    assert tracker.spend(NOW).baseline == pytest.approx(10.0)


def test_idle_days_do_not_drag_the_baseline_down():
    """A weekend with nothing in it is not a cheap day."""
    tracker = CostTracker(None)
    for days_ago in (1, 2, 3, 6):
        _spend(tracker, 20.0, NOW - timedelta(days=days_ago))
    spend = tracker.spend(NOW)
    assert spend.baseline == pytest.approx(20.0)
    assert spend.baseline_days == 4


def test_today_is_not_part_of_its_own_baseline():
    tracker = CostTracker(None)
    for days_ago in (1, 2, 3):
        _spend(tracker, 10.0, NOW - timedelta(days=days_ago))
    _spend(tracker, 100.0, NOW - timedelta(hours=1))
    assert tracker.spend(NOW).baseline == pytest.approx(10.0)


def test_month_to_date_starts_on_the_first():
    tracker = CostTracker(None)
    first = NOW.replace(day=1)
    _spend(tracker, 4.0, first)
    _spend(tracker, 6.0, first - timedelta(days=1))  # last month
    _spend(tracker, 1.0, NOW)
    assert tracker.spend(NOW).month == pytest.approx(5.0)


# -- the ledger ---------------------------------------------------------------


def test_restart_does_not_double_count(tmp_path):
    """Every restart re-reads every transcript; the ledger must not add them."""
    ledger = tmp_path / "cost.json"

    first = CostTracker(ledger)
    _spend(first, 7.0, NOW - timedelta(hours=2))
    first.flush(NOW, force=True)

    second = CostTracker(ledger)
    _spend(second, 7.0, NOW - timedelta(hours=2))  # the same response, re-read
    second.flush(NOW, force=True)

    assert CostTracker(ledger).spend(NOW).today == pytest.approx(7.0)


def test_days_whose_transcripts_were_cleaned_up_keep_their_figure(tmp_path):
    ledger = tmp_path / "cost.json"
    day = (NOW - timedelta(days=20)).date().isoformat()
    ledger.write_text(json.dumps({"days": {day: 42.0}}))

    tracker = CostTracker(ledger)  # nothing read for that day this time
    _spend(tracker, 1.0, NOW)
    tracker.flush(NOW, force=True)

    assert json.loads(ledger.read_text())["days"][day] == 42.0


def test_an_unreadable_ledger_starts_empty_rather_than_crashing(tmp_path):
    ledger = tmp_path / "cost.json"
    ledger.write_text("{ not json")
    assert CostTracker(ledger).spend(NOW).today == 0.0


def test_minutes_to_midnight_from_noon():
    assert minutes_to_midnight(NOW) == 12 * 60


# -- the frame ----------------------------------------------------------------


def _meter(tracker, budget=None):
    return PlanMeter(MODE_CREDITS, tracker, budget)


def test_credits_frame_round_trips_as_v2():
    frame = StateFrame(flags=FLAG_CREDITS, today_cents=1240, month_cents=34012,
                       pct_5h_x10=820, resets_5h_min=300)
    payload = frame.encode()
    assert payload[1] == 2
    assert len(payload) == 20
    back = decode(payload)
    assert (back.today_cents, back.month_cents, back.pct_5h_x10) == (1240, 34012, 820)


def test_plan_frame_is_still_v1_byte_for_byte():
    """A crab flashed before credits mode must keep working in plan mode."""
    payload = StateFrame(pct_5h_x10=235).encode()
    assert payload[1] == 1
    assert len(payload) == 12


def test_first_days_send_dollars_with_no_gauge():
    tracker = CostTracker(None)
    _spend(tracker, 3.0, NOW)
    frame = _meter(tracker).snapshot([], NOW)
    assert frame.flags & FLAG_CREDITS
    assert frame.flags & FLAG_NO_BASELINE
    assert not frame.flags & FLAG_NO_USAGE
    assert frame.today_cents == 300
    assert frame.pct_5h_x10 == 0


def test_gauge_is_today_against_a_normal_day():
    tracker = CostTracker(None)
    for days_ago in (1, 2, 3):
        _spend(tracker, 10.0, NOW - timedelta(days=days_ago))
    _spend(tracker, 8.0, NOW)
    frame = _meter(tracker).snapshot([], NOW)
    assert frame.pct_5h_x10 == 800
    assert not frame.flags & FLAG_LIMIT_WARN


def test_half_again_a_normal_day_warns():
    tracker = CostTracker(None)
    for days_ago in (1, 2, 3):
        _spend(tracker, 10.0, NOW - timedelta(days=days_ago))
    _spend(tracker, 15.0, NOW)
    assert _meter(tracker).snapshot([], NOW).flags & FLAG_LIMIT_WARN


def test_a_budget_scales_the_gauge_until_a_week_of_history():
    tracker = CostTracker(None)
    for days_ago in (1, 2, 3):
        _spend(tracker, 10.0, NOW - timedelta(days=days_ago))
    _spend(tracker, 45.0, NOW)

    frame = _meter(tracker, budget=50.0).snapshot([], NOW)
    assert frame.pct_5h_x10 == 900  # 45 of 50
    assert frame.flags & FLAG_LIMIT_WARN

    for days_ago in (4, 5, 6, 7):
        _spend(tracker, 10.0, NOW - timedelta(days=days_ago))
    frame = _meter(tracker, budget=50.0).snapshot([], NOW)
    assert frame.pct_5h_x10 == 4500  # now 4.5x a normal day
