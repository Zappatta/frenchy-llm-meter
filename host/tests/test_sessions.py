import json
import os
import subprocess
from datetime import datetime, timedelta, timezone

from frenchy_llm_meter import sessions as sessions_module
from frenchy_llm_meter.plan import PlanMeter
from frenchy_llm_meter.protocol import FLAG_NO_USAGE
from frenchy_llm_meter.transcripts import SessionState, TranscriptReader

NOW = datetime(2026, 8, 15, 12, 0, tzinfo=timezone.utc)


def _dead_pid() -> int:
    """A pid that has definitely exited.

    Hardcoding a large number is not portable — Linux allows pid_max well past
    anything macOS issues — so a real process is started and reaped instead.
    """
    proc = subprocess.Popen(["true"])
    proc.wait()
    return proc.pid


def _registry(tmp_path, monkeypatch):
    directory = tmp_path / "sessions"
    directory.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("FRENCHY_SESSIONS_DIR", str(directory))
    return directory


def _session_file(directory, pid, session_id, **overrides):
    data = {
        "pid": pid,
        "sessionId": session_id,
        "cwd": "/Users/x/Code",
        "name": "code-e4",
        "nameSource": "derived",
        "status": "idle",
        "kind": "interactive",
        "updatedAt": 1786797096280,
    }
    data.update(overrides)
    (directory / f"{pid}.json").write_text(json.dumps(data))


def _assistant(ts, req, msg, stop="end_turn"):
    return {
        "type": "assistant",
        "requestId": req,
        "timestamp": ts.isoformat().replace("+00:00", "Z"),
        "cwd": "/Users/x/Code/jigso_mvp",
        "isSidechain": False,
        "message": {
            "id": msg,
            "role": "assistant",
            "model": "claude-opus-4-8",
            "stop_reason": stop,
            "usage": {
                "input_tokens": 10,
                "output_tokens": 100,
                "cache_read_input_tokens": 1000,
                "cache_creation_input_tokens": 500,
            },
        },
    }


def _write(path, records):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(r) + "\n" for r in records))


def test_only_sessions_with_a_running_process_are_live(tmp_path, monkeypatch):
    directory = _registry(tmp_path, monkeypatch)
    _session_file(directory, os.getpid(), "alive-1")
    _session_file(directory, _dead_pid(), "gone-1")

    live = sessions_module.read()

    assert set(live) == {"alive-1"}


def test_a_stale_updated_at_does_not_make_a_session_dead(tmp_path, monkeypatch):
    """updatedAt moves on status change, not on a heartbeat.

    An idle session can sit untouched for days with its process very much
    alive, so freshness of the file must never gate liveness.
    """
    directory = _registry(tmp_path, monkeypatch)
    _session_file(directory, os.getpid(), "alive-1", updatedAt=1, status="idle")

    assert "alive-1" in sessions_module.read()


def test_malformed_and_incomplete_files_are_skipped(tmp_path, monkeypatch):
    directory = _registry(tmp_path, monkeypatch)
    (directory / "broken.json").write_text("{not json")
    (directory / "nosession.json").write_text(json.dumps({"pid": os.getpid()}))
    (directory / "nopid.json").write_text(json.dumps({"sessionId": "x"}))
    _session_file(directory, os.getpid(), "good-1")

    assert set(sessions_module.read()) == {"good-1"}


def test_derived_names_are_not_treated_as_user_chosen(tmp_path, monkeypatch):
    directory = _registry(tmp_path, monkeypatch)
    _session_file(directory, os.getpid(), "s-1", name="code-e4", nameSource="derived")

    assert sessions_module.read()["s-1"].named_by_user is False


def test_busy_status_overrides_a_finished_turn(tmp_path, monkeypatch):
    """The process says it is working, so it is working.

    The transcript's last record says the turn ended, which is what a session
    looks like in the instant between a user pressing enter and the first
    assistant record landing.
    """
    directory = _registry(tmp_path, monkeypatch)
    _session_file(directory, os.getpid(), "sess-a", status="busy")
    _write(
        tmp_path / "-Users-x-Code" / "sess-a.jsonl",
        [_assistant(NOW - timedelta(minutes=1), "r1", "m1", stop="end_turn")],
    )

    frame = PlanMeter().snapshot(TranscriptReader(tmp_path).poll(NOW), NOW)

    assert frame.sessions[0].state == int(SessionState.WORKING)


def test_idle_process_downgrades_a_dangling_tool_call_to_waiting(tmp_path, monkeypatch):
    """A session killed mid-tool-call leaves a trailing ``tool_use``.

    On the transcript alone that reads as WORKING forever. The process knows
    better.
    """
    directory = _registry(tmp_path, monkeypatch)
    _session_file(directory, os.getpid(), "sess-a", status="idle")
    _write(
        tmp_path / "-Users-x-Code" / "sess-a.jsonl",
        [_assistant(NOW - timedelta(minutes=1), "r1", "m1", stop="tool_use")],
    )

    frame = PlanMeter().snapshot(TranscriptReader(tmp_path).poll(NOW), NOW)

    assert frame.sessions[0].state == int(SessionState.WAITING)


def test_a_long_quiet_session_still_reads_as_waiting(tmp_path, monkeypatch):
    """The "your turn" signal must not expire.

    Transcript state decays to IDLE after IDLE_AFTER, which used to drop the
    ring to grey and the LED to its dim ember — twenty minutes after Claude
    finished, which is precisely when someone who walked away needs telling.
    """
    directory = _registry(tmp_path, monkeypatch)
    _session_file(directory, os.getpid(), "sess-a", status="idle")
    _write(
        tmp_path / "-Users-x-Code" / "sess-a.jsonl",
        [_assistant(NOW - timedelta(hours=3), "r1", "m1", stop="end_turn")],
    )

    frame = PlanMeter().snapshot(TranscriptReader(tmp_path).poll(NOW), NOW)

    assert frame.sessions[0].state == int(SessionState.WAITING)


def test_a_closed_session_loses_its_ring_immediately(tmp_path, monkeypatch):
    """Closing a terminal must remove its ring at once.

    Transcript activity used to stand in for liveness, which kept a shut
    session on screen for the whole of IDLE_AFTER — twenty minutes of rings for
    terminals that were already gone.
    """
    directory = _registry(tmp_path, monkeypatch)
    _session_file(directory, os.getpid(), "sess-open", status="idle")

    busy = NOW - timedelta(minutes=1)
    _write(tmp_path / "-Users-x-Code" / "sess-open.jsonl", [_assistant(busy, "r1", "m1")])
    # Was working seconds ago, but its process is gone.
    _write(tmp_path / "-Users-x-Code" / "sess-shut.jsonl", [_assistant(busy, "r2", "m2")])

    frame = PlanMeter().snapshot(TranscriptReader(tmp_path).poll(NOW), NOW)

    assert len(frame.sessions) == 1


def test_an_empty_registry_means_nothing_is_open(tmp_path, monkeypatch):
    """An empty registry is an answer, not an absence of one."""
    _registry(tmp_path, monkeypatch)
    _write(
        tmp_path / "-Users-x-Code" / "sess-a.jsonl",
        [_assistant(NOW - timedelta(minutes=1), "r1", "m1")],
    )

    frame = PlanMeter().snapshot(TranscriptReader(tmp_path).poll(NOW), NOW)

    assert frame.sessions == []


def test_without_a_registry_liveness_falls_back_to_transcripts(tmp_path, monkeypatch):
    """Older Claude Code publishes no session files; that path still works."""
    monkeypatch.setenv("FRENCHY_SESSIONS_DIR", str(tmp_path / "does-not-exist"))
    _write(
        tmp_path / "-Users-x-Code" / "sess-a.jsonl",
        [_assistant(NOW - timedelta(minutes=1), "r1", "m1")],
    )

    frame = PlanMeter().snapshot(TranscriptReader(tmp_path).poll(NOW), NOW)

    assert len(frame.sessions) == 1


def test_open_sessions_get_rings_with_no_statusline_capture(tmp_path, monkeypatch):
    """The zero-configuration path: no shim installed, rings still correct.

    Both sessions have been quiet long enough to read as idle and both have
    tokens in the window, so without the registry the fallback would draw a
    ring for each. Only one has a running process.
    """
    directory = _registry(tmp_path, monkeypatch)
    _session_file(directory, os.getpid(), "sess-a", status="idle")

    quiet = NOW - timedelta(hours=2)
    _write(tmp_path / "-Users-x-Code" / "sess-a.jsonl", [_assistant(quiet, "r1", "m1")])
    _write(tmp_path / "-Users-x-Code" / "sess-b.jsonl", [_assistant(quiet, "r2", "m2")])

    frame = PlanMeter().snapshot(TranscriptReader(tmp_path).poll(NOW), NOW)

    assert len(frame.sessions) == 1
    assert frame.flags & FLAG_NO_USAGE  # no plan figures, but the rings work
    assert frame.sessions[0].ctx_pct > 0
