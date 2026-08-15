import pytest

from frenchy_llm_meter import usage as usage_module


@pytest.fixture(autouse=True)
def isolated_state(tmp_path_factory, monkeypatch):
    """Keep tests off the developer's own live Claude Code state.

    Both sources default to real paths under ``~/.claude``, so without this a
    test run would pick up whatever sessions happen to be open on the machine
    and pass or fail accordingly. Tests that want either source opt in by
    pointing it somewhere they control.
    """
    empty = tmp_path_factory.mktemp("claude-state")
    monkeypatch.setenv("FRENCHY_SESSIONS_DIR", str(empty / "sessions"))
    monkeypatch.setattr(usage_module, "USAGE_FILE", empty / "usage.json")
