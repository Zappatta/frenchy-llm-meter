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
