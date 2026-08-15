import json
from datetime import datetime, timedelta, timezone

import pytest

from clawd_meter import usage as usage_module
from clawd_meter.plan import PlanMeter
from clawd_meter.models import context_window_for
from clawd_meter.protocol import (
    FLAG_LIMIT_WARN,
    FLAG_NO_USAGE,
    FLAG_STALE,
    MAX_PAYLOAD,
    SessionFrame,
    StateFrame,
    decode,
)
from clawd_meter.transcripts import SessionState, TranscriptReader

NOW = datetime(2026, 8, 15, 12, 0, tzinfo=timezone.utc)


def _assistant(ts, req, msg, stop="end_turn", out=100, model="claude-opus-4-8"):
    return {
        "type": "assistant",
        "requestId": req,
        "timestamp": ts.isoformat().replace("+00:00", "Z"),
        "cwd": "/Users/x/Code/jigso_mvp",
        "gitBranch": "main",
        "isSidechain": False,
        "message": {
            "id": msg,
            "role": "assistant",
            "model": model,
            "stop_reason": stop,
            "usage": {
                "input_tokens": 10,
                "output_tokens": out,
                "cache_read_input_tokens": 1000,
                "cache_creation_input_tokens": 500,
                "cache_creation": {
                    "ephemeral_5m_input_tokens": 200,
                    "ephemeral_1h_input_tokens": 300,
                },
            },
        },
    }


def _write(path, records):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w") as fh:
        for rec in records:
            fh.write(json.dumps(rec) + "\n")


def test_duplicate_request_ids_are_counted_once(tmp_path):
    """One API response spans several assistant lines sharing requestId/message.id.

    Summing them naively inflates usage 2-3x, which is the single easiest way
    to get this whole project wrong.
    """
    ts = NOW - timedelta(minutes=1)
    session = tmp_path / "-Users-x-Code" / "sess-a.jsonl"
    _write(
        session,
        [
            _assistant(ts, "req_1", "msg_1"),  # thinking block
            _assistant(ts, "req_1", "msg_1"),  # text block, same response
            _assistant(ts, "req_1", "msg_1"),  # tool_use block, same response
        ],
    )

    reader = TranscriptReader(tmp_path)
    sessions = reader.poll(NOW)
    assert len(sessions) == 1
    # 10 in + 100 out + 200 + 300 + 1000 cached = 1610 raw tokens, counted once.
    assert sessions[0].window_tokens(NOW) == 1610


def test_incremental_read_does_not_double_count(tmp_path):
    ts = NOW - timedelta(minutes=1)
    path = tmp_path / "-Users-x-Code" / "sess-a.jsonl"
    _write(path, [_assistant(ts, "req_1", "msg_1")])

    reader = TranscriptReader(tmp_path)
    reader.poll(NOW)
    first = reader.poll(NOW)[0].window_tokens(NOW)

    with path.open("a") as fh:
        fh.write(json.dumps(_assistant(ts, "req_2", "msg_2")) + "\n")

    second = reader.poll(NOW)[0].window_tokens(NOW)
    assert second == first * 2


def test_partial_trailing_line_is_retried(tmp_path):
    """A half-flushed line must not be consumed and lost."""
    path = tmp_path / "-Users-x-Code" / "sess-a.jsonl"
    path.parent.mkdir(parents=True)
    record = json.dumps(_assistant(NOW - timedelta(minutes=1), "req_1", "msg_1"))
    path.write_text(record[:40])  # writer is mid-flush

    reader = TranscriptReader(tmp_path)
    assert reader.poll(NOW) == [] or reader.poll(NOW)[0].window_tokens(NOW) == 0

    path.write_text(record + "\n")
    sessions = reader.poll(NOW)
    assert sessions and sessions[0].window_tokens(NOW) == 1610


@pytest.mark.parametrize(
    "stop_reason,age,expected",
    [
        ("end_turn", timedelta(minutes=1), SessionState.WAITING),
        ("tool_use", timedelta(minutes=1), SessionState.WORKING),
        ("end_turn", timedelta(hours=2), SessionState.IDLE),
        ("tool_use", timedelta(hours=2), SessionState.IDLE),
    ],
)
def test_state_from_stop_reason_and_age(tmp_path, stop_reason, age, expected):
    path = tmp_path / "-Users-x-Code" / "sess-a.jsonl"
    _write(path, [_assistant(NOW - age, "req_1", "msg_1", stop=stop_reason)])

    reader = TranscriptReader(tmp_path)
    assert reader.poll(NOW)[0].state(NOW) is expected


def test_tool_result_flips_waiting_session_back_to_working(tmp_path):
    ts = NOW - timedelta(minutes=1)
    path = tmp_path / "-Users-x-Code" / "sess-a.jsonl"
    _write(
        path,
        [
            _assistant(ts, "req_1", "msg_1", stop="end_turn"),
            {
                "type": "user",
                "timestamp": ts.isoformat().replace("+00:00", "Z"),
                "isSidechain": False,
                "message": {
                    "role": "user",
                    "content": [{"type": "tool_result", "tool_use_id": "t1"}],
                },
            },
        ],
    )
    reader = TranscriptReader(tmp_path)
    assert reader.poll(NOW)[0].state(NOW) is SessionState.WORKING


def test_subagent_tokens_roll_up_into_parent_session(tmp_path):
    ts = NOW - timedelta(minutes=1)
    _write(tmp_path / "-Users-x-Code" / "sess-a.jsonl", [_assistant(ts, "r1", "m1")])
    _write(
        tmp_path / "-Users-x-Code" / "sess-a" / "subagents" / "agent-x.jsonl",
        [_assistant(ts, "r2", "m2")],
    )

    reader = TranscriptReader(tmp_path)
    sessions = reader.poll(NOW)
    assert len(sessions) == 1
    assert sessions[0].window_tokens(NOW) == 1610 * 2


def test_subagent_does_not_set_parent_state(tmp_path):
    """A subagent finishing is not the parent asking for input."""
    ts = NOW - timedelta(minutes=1)
    _write(
        tmp_path / "-Users-x-Code" / "sess-a.jsonl",
        [_assistant(ts, "r1", "m1", stop="tool_use")],
    )
    _write(
        tmp_path / "-Users-x-Code" / "sess-a" / "subagents" / "agent-x.jsonl",
        [_assistant(ts, "r2", "m2", stop="end_turn")],
    )

    reader = TranscriptReader(tmp_path)
    assert reader.poll(NOW)[0].state(NOW) is SessionState.WORKING


def test_ai_title_is_preferred_as_label(tmp_path):
    ts = NOW - timedelta(minutes=1)
    _write(
        tmp_path / "-Users-x-Code" / "sess-a.jsonl",
        [
            _assistant(ts, "r1", "m1"),
            {"type": "ai-title", "aiTitle": "Fix the login bug", "sessionId": "sess-a"},
        ],
    )
    assert TranscriptReader(tmp_path).poll(NOW)[0].label() == "Fix the login bug"


def test_events_outside_the_window_are_excluded(tmp_path):
    _write(
        tmp_path / "-Users-x-Code" / "sess-a.jsonl",
        [
            _assistant(NOW - timedelta(hours=6), "r1", "m1"),  # outside 5h
            _assistant(NOW - timedelta(minutes=5), "r2", "m2"),  # inside
        ],
    )
    sessions = TranscriptReader(tmp_path).poll(NOW)
    assert sessions[0].window_tokens(NOW) == 1610


def test_context_window_lookup():
    assert context_window_for("claude-opus-4-8") == 1_000_000
    assert context_window_for("claude-haiku-4-5") == 200_000
    assert context_window_for("claude-haiku-4-5-20251001") == 200_000
    assert context_window_for("anthropic.claude-opus-4-8") == 1_000_000
    assert context_window_for("claude-something-new-9") == 1_000_000


def test_payload_round_trips_and_fits_one_mtu():
    frame = StateFrame(
        sessions=[
            SessionFrame(state=1, ctx_pct=62, tokens=1_234_567, label="jigso-mvp"),
            SessionFrame(state=2, ctx_pct=38, tokens=42, label="clawd-meter"),
        ],
        pct_5h_x10=734,
        pct_7d_x10=210,
        resets_5h_min=134,
        resets_7d_min=4321,
        flags=0x05,
    )
    payload = frame.encode()
    assert len(payload) <= MAX_PAYLOAD <= 180  # fits a negotiated BLE MTU

    back = decode(payload)
    assert back.pct_5h_x10 == 734
    assert back.pct_7d_x10 == 210
    assert back.resets_5h_min == 134
    assert back.resets_7d_min == 4321
    assert back.flags == 0x05
    assert [s.label for s in back.sessions] == ["jigso-mvp", "clawd-meter"]
    assert back.sessions[0].tokens == 1_234_567


def test_encode_truncates_to_four_sessions():
    frame = StateFrame(
        sessions=[SessionFrame(0, 10, 1, f"s{i}") for i in range(9)],
    )
    assert len(decode(frame.encode()).sessions) == 4


def test_long_labels_are_truncated_not_rejected():
    frame = StateFrame(sessions=[SessionFrame(1, 50, 1, "a-very-long-session-name")])
    assert len(decode(frame.encode()).sessions[0].label) <= 16


def test_ring_uses_the_captured_context_figure(tmp_path, monkeypatch):
    """The statusline capture is authoritative — it is what Claude Code shows."""
    ts = NOW - timedelta(minutes=1)
    _write(tmp_path / "-Users-x-Code" / "sess-a.jsonl", [_assistant(ts, "r1", "m1")])

    cap = tmp_path / "usage.json"
    cap.write_text(
        json.dumps(
            {
                "captured_at": int(NOW.timestamp()),
                "rate_limits": {"five_hour": {"used_percentage": 5, "resets_at": 1}},
                "sessions": {"sess-a": {"ctx": 37.4, "at": int(NOW.timestamp())}},
            }
        )
    )
    monkeypatch.setattr(usage_module, "USAGE_FILE", cap)

    frame = PlanMeter().snapshot(TranscriptReader(tmp_path).poll(NOW), NOW)
    assert [s.ctx_pct for s in frame.sessions] == [37]


def test_ring_falls_back_to_a_transcript_estimate(tmp_path, monkeypatch):
    """A session that has not rendered a statusline still gets a ring."""
    ts = NOW - timedelta(minutes=1)
    _write(tmp_path / "-Users-x-Code" / "sess-a.jsonl", [_assistant(ts, "r1", "m1")])
    monkeypatch.setattr(usage_module, "USAGE_FILE", tmp_path / "absent.json")

    frame = PlanMeter().snapshot(TranscriptReader(tmp_path).poll(NOW), NOW)
    # 1510 prompt tokens against a 1M window is very nearly empty.
    assert frame.sessions and frame.sessions[0].ctx_pct == 100


def test_smaller_context_window_reads_as_fuller(tmp_path, monkeypatch):
    """The same prompt fills a Haiku window 5x faster than an Opus one."""
    monkeypatch.setattr(usage_module, "USAGE_FILE", tmp_path / "absent.json")

    def remaining(model, prompt_tokens):
        reader = TranscriptReader(tmp_path / model)
        rec = _assistant(NOW - timedelta(minutes=1), "r", "m", model=model)
        rec["message"]["usage"]["cache_read_input_tokens"] = prompt_tokens
        _write(tmp_path / model / "-Users-x-Code" / "s.jsonl", [rec])
        return reader.poll(NOW)[0].estimated_context_remaining()

    opus = remaining("claude-opus-4-8", 100_000)
    haiku = remaining("claude-haiku-4-5", 100_000)
    assert opus > 89 and haiku < 51


def test_sessions_are_ordered_least_context_first(tmp_path, monkeypatch):
    """The ring that runs out first belongs on the outside."""
    ts = NOW - timedelta(minutes=1)
    for name in ("a", "b", "c"):
        _write(
            tmp_path / "-Users-x-Code" / f"sess-{name}.jsonl",
            [_assistant(ts, f"r{name}", f"m{name}")],
        )

    cap = tmp_path / "usage.json"
    at = int(NOW.timestamp())
    cap.write_text(
        json.dumps(
            {
                "captured_at": at,
                "rate_limits": {"five_hour": {"used_percentage": 5, "resets_at": 1}},
                "sessions": {
                    "sess-a": {"ctx": 80, "at": at},
                    "sess-b": {"ctx": 12, "at": at},
                    "sess-c": {"ctx": 45, "at": at},
                },
            }
        )
    )
    monkeypatch.setattr(usage_module, "USAGE_FILE", cap)

    frame = PlanMeter().snapshot(TranscriptReader(tmp_path).poll(NOW), NOW)
    assert [s.ctx_pct for s in frame.sessions] == [12, 45, 80]


# --- plan usage, captured from Claude Code's statusline payload -------------


def _usage_file(tmp_path, five=23.5, seven=41.2, age_s=0, resets_in_s=7200):
    """Write a capture in the shape statusline-hook.sh produces."""
    captured = int(NOW.timestamp()) - age_s
    path = tmp_path / "usage.json"
    path.write_text(
        json.dumps(
            {
                "captured_at": captured,
                "rate_limits": {
                    "five_hour": {
                        "used_percentage": five,
                        "resets_at": int(NOW.timestamp()) + resets_in_s,
                    },
                    "seven_day": {
                        "used_percentage": seven,
                        "resets_at": int(NOW.timestamp()) + resets_in_s * 20,
                    },
                },
            }
        )
    )
    return path


def test_real_percentages_are_read_from_the_capture(tmp_path, monkeypatch):
    _usage_file(tmp_path)
    monkeypatch.setattr(usage_module, "USAGE_FILE", tmp_path / "usage.json")

    frame = PlanMeter().snapshot([], NOW)
    assert frame.pct_5h_x10 == 235  # 23.5%
    assert frame.pct_7d_x10 == 412  # 41.2%
    assert frame.resets_5h_min == 120  # 7200s
    assert not frame.flags & FLAG_NO_USAGE
    assert not frame.flags & FLAG_STALE


def test_missing_capture_reports_no_usage_not_zero_percent(tmp_path, monkeypatch):
    monkeypatch.setattr(usage_module, "USAGE_FILE", tmp_path / "absent.json")
    frame = PlanMeter().snapshot([], NOW)
    assert frame.flags & FLAG_NO_USAGE


def test_old_capture_is_flagged_stale_and_never_warns(tmp_path, monkeypatch):
    """A closed laptop must not leave a confident hour-old number on screen."""
    _usage_file(tmp_path, five=99.0, age_s=3600)
    monkeypatch.setattr(usage_module, "USAGE_FILE", tmp_path / "usage.json")

    frame = PlanMeter().snapshot([], NOW)
    assert frame.flags & FLAG_STALE
    assert not frame.flags & FLAG_LIMIT_WARN  # stale outranks the warning
    assert frame.pct_5h_x10 == 990  # last known figure is still shown


def test_warning_fires_near_the_ceiling(tmp_path, monkeypatch):
    _usage_file(tmp_path, five=91.0)
    monkeypatch.setattr(usage_module, "USAGE_FILE", tmp_path / "usage.json")
    assert PlanMeter().snapshot([], NOW).flags & FLAG_LIMIT_WARN


def test_malformed_capture_is_ignored_rather_than_crashing(tmp_path, monkeypatch):
    path = tmp_path / "usage.json"
    path.write_text("{not json at all")
    monkeypatch.setattr(usage_module, "USAGE_FILE", path)
    assert PlanMeter().snapshot([], NOW).flags & FLAG_NO_USAGE


def test_capture_without_rate_limits_is_ignored(tmp_path, monkeypatch):
    """Plans with no rate limits omit the key; that is not a zero reading."""
    path = tmp_path / "usage.json"
    path.write_text(json.dumps({"captured_at": 1, "rate_limits": None}))
    monkeypatch.setattr(usage_module, "USAGE_FILE", path)
    assert PlanMeter().snapshot([], NOW).flags & FLAG_NO_USAGE
