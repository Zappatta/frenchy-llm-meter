"""Uninstall must never cost someone a statusline it did not create.

This is here because it already happened. The receipt recording what was
wrapped is only written when the installer does the wrapping, so a hook wired
up by hand left no receipt, and uninstall took the "we added this, remove it"
branch on a statusline it never owned.
"""

import importlib.util
import json
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]


@pytest.fixture
def inst(tmp_path, monkeypatch):
    spec = importlib.util.spec_from_file_location("install", REPO / "install.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(module, "SETTINGS", tmp_path / "settings.json")
    monkeypatch.setattr(module, "RECEIPT", tmp_path / "receipt.json")
    return module


def _settings(inst, command, extra=True):
    data = {"statusLine": {"type": "command", "command": command}}
    if extra:
        data["permissions"] = {"allow": ["Bash(ls)"]}
    inst.SETTINGS.write_text(json.dumps(data, indent=2))


def _read(inst):
    return json.loads(inst.SETTINGS.read_text())


ORIGINAL = "bash ~/.claude/statusline-command.sh"


def _wrapped(inst, inner=ORIGINAL):
    return f"FRENCHY_INNER_STATUSLINE='{inner}' bash {inst.SHIM}"


def test_a_receipt_restores_the_previous_block_whole(inst):
    """Not just the command string — padding and any other keys too."""
    _settings(inst, _wrapped(inst))
    inst.RECEIPT.write_text(
        json.dumps({"previous_status_line": {"type": "command", "command": ORIGINAL}})
    )

    inst.remove_usage(dry=False)

    assert _read(inst)["statusLine"]["command"] == ORIGINAL


def test_without_a_receipt_the_wrapper_is_unwrapped(inst):
    """The bug: this branch used to delete the statusline outright.

    A hook installed by hand leaves no receipt, but the command it wrote still
    carries what it wraps, so the value needed was there the whole time.
    """
    _settings(inst, _wrapped(inst))

    inst.remove_usage(dry=False)

    assert _read(inst)["statusLine"]["command"] == ORIGINAL


def test_a_statusline_that_is_not_ours_is_left_alone(inst):
    _settings(inst, "npx ccusage statusline")

    inst.remove_usage(dry=False)

    assert _read(inst)["statusLine"]["command"] == "npx ccusage statusline"


def test_a_wrapper_around_nothing_is_removed(inst):
    """Installed where there was no statusline before, so there is none to keep."""
    _settings(inst, _wrapped(inst, inner=""), extra=False)

    inst.remove_usage(dry=False)

    assert "statusLine" not in _read(inst)


def test_unrelated_settings_survive(inst):
    """The whole file is rewritten, so everything else has to come back."""
    _settings(inst, _wrapped(inst))

    inst.remove_usage(dry=False)

    assert _read(inst)["permissions"] == {"allow": ["Bash(ls)"]}


# -- profile and mode ----------------------------------------------------------


def test_the_service_is_told_everything_it_needs(inst):
    """launchd passes no environment, so the profile has to be an argument."""
    args = inst.daemon_args(
        {"claude_dir": "/Users/x/.claude-work", "mode": "credits", "daily_budget": 40.0}
    )
    assert args == ["--claude-dir", "/Users/x/.claude-work", "--mode", "credits",
                    "--daily-budget", "40"]


def test_no_budget_means_no_budget_argument(inst):
    args = inst.daemon_args({"claude_dir": "/c", "mode": "plan", "daily_budget": None})
    assert "--daily-budget" not in args


def test_profiles_are_found_beside_the_default(inst, tmp_path, monkeypatch):
    home = tmp_path / "home"
    (home / ".claude" / "projects").mkdir(parents=True)
    (home / ".claude-personal" / "projects").mkdir(parents=True)
    (home / ".claude-empty").mkdir()  # never used: not offered
    monkeypatch.setattr(inst.Path, "home", lambda: home)
    monkeypatch.setattr(inst, "DEFAULT_CLAUDE_DIR", home / ".claude")
    monkeypatch.delenv("CLAUDE_CONFIG_DIR", raising=False)

    assert inst.discover_profiles() == [home / ".claude", home / ".claude-personal"]


def test_switching_profile_repoints_settings(inst, tmp_path):
    inst.use_profile(tmp_path / ".claude-work")
    assert inst.SETTINGS == tmp_path / ".claude-work" / "settings.json"
    assert inst.SESSIONS_DIR == tmp_path / ".claude-work" / "sessions"


def test_the_ledger_name_matches_the_daemons(inst):
    from frenchy_llm_meter.paths import profile_slug

    for name in (".claude", ".claude-personal", "weird dir!"):
        assert inst.profile_slug(Path(name)) == profile_slug(Path(name))
