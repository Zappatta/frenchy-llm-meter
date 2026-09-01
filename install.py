#!/usr/bin/env python3
"""Set up frenchy-llm-meter on this Mac. Nothing here needs hand-editing a file.

    python3 install.py              install, then offer the plan-usage hook
    python3 install.py --doctor     diagnose a meter that is not updating
    python3 install.py --uninstall  put everything back

The install comes in two halves, because they cost the user different amounts.

The core half needs no configuration at all. Session liveness, status and
labels come from ``~/.claude/sessions/<pid>.json``, which every running Claude
Code process maintains, and context estimates come from the transcripts. That
is enough to drive the rings.

The optional half is the 5h/7d plan percentages. Those appear in exactly one
place — the JSON payload Claude Code hands its statusline command — and are in
no hook, no OpenTelemetry metric, and no state file on disk. Capturing them
means taking over ``statusLine.command``, so it is offered rather than assumed,
and the previous command is wrapped rather than replaced.

Deliberately stdlib-only: this runs from a fresh clone before any virtualenv
exists, using whatever python3 the machine already has.
"""

from __future__ import annotations

import argparse
import json
import os
import plistlib
import shlex
import shutil
import signal
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

REPO = Path(__file__).resolve().parent
HOST = REPO / "host"
VENV = HOST / ".venv"
VENV_PY = VENV / "bin" / "python"
SHIM = HOST / "statusline-hook.sh"

CLAUDE_DIR = Path.home() / ".claude"
SETTINGS = CLAUDE_DIR / "settings.json"
SESSIONS_DIR = CLAUDE_DIR / "sessions"

LABEL = "io.frenchyllmmeter.daemon"
PLIST = Path.home() / "Library" / "LaunchAgents" / f"{LABEL}.plist"
# One file, not two. Python's logging writes to stderr, so a split StandardOut
# / StandardError leaves the .log everybody looks in permanently empty while
# every line lands in the .err.log nobody thinks to open.
LOG = Path.home() / "Library" / "Logs" / "frenchy-llm-meter.log"

STATE_DIR = Path.home() / ".local" / "state" / "frenchy-llm-meter"
USAGE_FILE = STATE_DIR / "usage.json"
RECEIPT = STATE_DIR / "install.json"

MIN_PYTHON = (3, 11)
POLL_INTERVAL = "5"

# How long to sit in the foreground watching the daemon before handing over to
# launchd. Long enough for a BLE scan to find the crab, or to fail visibly.
FIRST_RUN_SECONDS = 20


# --------------------------------------------------------------------------
# output
# --------------------------------------------------------------------------

_BOLD, _DIM, _RED, _GREEN, _YELLOW, _OFF = (
    ("\033[1m", "\033[2m", "\033[31m", "\033[32m", "\033[33m", "\033[0m")
    if sys.stdout.isatty()
    else ("", "", "", "", "", "")
)


def step(text: str) -> None:
    print(f"\n{_BOLD}{text}{_OFF}")


def ok(text: str) -> None:
    print(f"  {_GREEN}✓{_OFF} {text}")


def warn(text: str) -> None:
    print(f"  {_YELLOW}!{_OFF} {text}")


def bad(text: str) -> None:
    print(f"  {_RED}✗{_OFF} {text}")


def note(text: str) -> None:
    print(f"    {_DIM}{text}{_OFF}")


def die(text: str) -> "NoReturn":  # type: ignore[valid-type]
    bad(text)
    sys.exit(1)


# --------------------------------------------------------------------------
# small helpers
# --------------------------------------------------------------------------


def run(cmd: list[str], check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True, check=check)


def read_json(path: Path) -> object | None:
    try:
        return json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return None


def write_json(path: Path, data: object) -> None:
    """Write via a temp file so an interrupted run cannot truncate the original."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".frenchy-tmp")
    tmp.write_text(json.dumps(data, indent=2) + "\n")
    os.replace(tmp, path)


def statusline_command(inner: str) -> str:
    return (
        f"FRENCHY_INNER_STATUSLINE={shlex.quote(inner)} bash {shlex.quote(str(SHIM))}"
    )


def _expanded(command: str) -> str:
    """Compare paths, not spellings.

    A command wired by hand reads ``$HOME/...`` or ``~/...`` while everything
    here works in absolute paths. Comparing the two literally reports a
    correctly installed hook as belonging to some other checkout.
    """
    return os.path.expandvars(os.path.expanduser(command))


def is_our_shim(command: str | None) -> bool:
    return bool(command) and str(SHIM) in _expanded(command)


def is_some_shim(command: str | None) -> bool:
    """A frenchy-llm-meter wrapper, possibly from a checkout that has since moved."""
    return bool(command) and SHIM.name in _expanded(command)


def wrapped_inner(command: str) -> str | None:
    """The statusline a wrapper is wrapping, so re-pointing preserves it.

    Without this, re-running over an existing wrapper nests one inside the
    other: the inner value stops being a path and the user's real statusline
    silently stops running.
    """
    try:
        parts = shlex.split(command)
    except ValueError:
        return None
    for part in parts:
        if part.startswith("FRENCHY_INNER_STATUSLINE="):
            return part.split("=", 1)[1]
    return None


def ask(question: str, default: bool) -> bool:
    if not sys.stdin.isatty():
        return default
    suffix = "[Y/n]" if default else "[y/N]"
    try:
        answer = input(f"  {question} {suffix} ").strip().lower()
    except EOFError:
        return default
    if not answer:
        return default
    return answer.startswith("y")


# --------------------------------------------------------------------------
# preflight
# --------------------------------------------------------------------------


def preflight() -> None:
    step("Checking this machine")

    if sys.platform != "darwin":
        bad(f"macOS only for now (this is {sys.platform})")
        note("the daemon itself should port cleanly — bleak speaks BlueZ on")
        note("Linux, and nothing above it is Mac-specific. What is missing is")
        note("this installer: a systemd user unit in place of the launchd job,")
        note("XDG paths in place of ~/Library, and somebody with the hardware")
        note("to find out what that turns up.")
        sys.exit(1)
    ok("macOS")

    if sys.version_info < MIN_PYTHON:
        die(f"python {MIN_PYTHON[0]}.{MIN_PYTHON[1]}+ required, this is "
            f"{sys.version_info.major}.{sys.version_info.minor}")
    ok(f"python {sys.version_info.major}.{sys.version_info.minor}")

    if not CLAUDE_DIR.is_dir():
        die(f"no {CLAUDE_DIR} — is Claude Code installed and has it been run once?")
    ok(f"Claude Code state at {CLAUDE_DIR}")

    if not SHIM.is_file():
        die(f"missing {SHIM} — run this from inside the cloned repo")


# --------------------------------------------------------------------------
# core install
# --------------------------------------------------------------------------


def build_venv(dry: bool) -> None:
    step("Installing the daemon")

    if dry:
        note(f"would create {VENV} and pip install -e {HOST}")
        return

    if not VENV_PY.exists():
        run([sys.executable, "-m", "venv", str(VENV)])
        ok(f"virtualenv at {VENV.relative_to(REPO)}")
    else:
        ok(f"virtualenv already at {VENV.relative_to(REPO)}")

    result = run(
        [str(VENV_PY), "-m", "pip", "install", "--quiet", "--upgrade", "pip"],
        check=False,
    )
    result = run([str(VENV_PY), "-m", "pip", "install", "--quiet", "-e", str(HOST)],
                 check=False)
    if result.returncode != 0:
        print(result.stdout, result.stderr)
        die("pip install failed")
    ok("frenchy-llm-meter and its dependencies installed")


def first_run(dry: bool) -> None:
    """Run the daemon in the foreground once, before launchd ever touches it.

    Two jobs. It proves the pipeline end to end while the user is watching, and
    it makes macOS raise its Bluetooth permission prompt against this terminal.
    A launchd job has no window to prompt from, so a background-first install
    can sit there permanently denied with nothing to click.

    Not to be confused with `--probe`, which is the daemon's link diagnostic
    (`python -m frenchy_llm_meter --probe`). This one is part of installing and
    runs the real daemon; that one answers where a failing connect dies. They
    were both called "probe" until 2026-09-01.
    """
    step("Starting the daemon once, in the foreground")

    if dry:
        note(f"would stop any running service, then run the daemon for "
             f"{FIRST_RUN_SECONDS}s to trigger the Bluetooth prompt")
        return

    # Only one central can hold the crab. An already-installed daemon left
    # running through this blocks it, and the first run then reports a device
    # that is sitting right there as missing. `--probe` has the same
    # constraint, for the same reason.
    uid = os.getuid()
    if run(["launchctl", "bootout", f"gui/{uid}/{LABEL}"], check=False).returncode == 0:
        ok("stopped the running service so the first run can reach the meter")

    print("    macOS may ask for Bluetooth permission now — say yes, or the")
    print("    meter can never be reached.\n")

    proc = subprocess.Popen(
        [str(VENV_PY), "-m", "frenchy_llm_meter", "--interval", "3"],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    deadline = time.monotonic() + FIRST_RUN_SECONDS
    saw_connection = False
    try:
        while time.monotonic() < deadline:
            line = proc.stdout.readline() if proc.stdout else ""
            if not line:
                if proc.poll() is not None:
                    break
                continue
            print(f"    {_DIM}{line.rstrip()}{_OFF}")
            if "connected" in line.lower():
                saw_connection = True
    finally:
        # SIGINT, not SIGTERM. SIGTERM kills the interpreter outright, so the
        # daemon's `finally: await link.close()` never runs and the crab is
        # left believing this central is still attached — which makes the
        # launchd job that starts moments later unable to connect at all.
        proc.send_signal(signal.SIGINT)
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()

    print()
    if saw_connection:
        ok("talked to the meter")
    else:
        warn("did not see the meter — is it powered and flashed?")
        note("the daemon keeps scanning, so this fixes itself when it appears")


def install_agent(dry: bool) -> None:
    step("Installing the background service")

    plist = {
        "Label": LABEL,
        "ProgramArguments": [
            str(VENV_PY), "-m", "frenchy_llm_meter", "--interval", POLL_INTERVAL
        ],
        "WorkingDirectory": str(HOST),
        "RunAtLoad": True,
        "KeepAlive": True,
        "ProcessType": "Background",
        "StandardOutPath": str(LOG),
        "StandardErrorPath": str(LOG),
    }

    if dry:
        note(f"would write {PLIST} pointing at {VENV_PY}")
        return

    PLIST.parent.mkdir(parents=True, exist_ok=True)
    with PLIST.open("wb") as fh:
        plistlib.dump(plist, fh)
    ok(f"wrote {PLIST}")

    uid = os.getuid()
    run(["launchctl", "bootout", f"gui/{uid}/{LABEL}"], check=False)
    result = run(["launchctl", "bootstrap", f"gui/{uid}", str(PLIST)], check=False)
    if result.returncode != 0:
        # bootstrap is the modern spelling; fall back for older systems.
        result = run(["launchctl", "load", "-w", str(PLIST)], check=False)
    if result.returncode != 0:
        bad(f"launchctl refused the job: {result.stderr.strip()}")
        note(f"the daemon still runs by hand: {VENV_PY} -m frenchy_llm_meter")
        return
    ok(f"service started, logs at {LOG}")


# --------------------------------------------------------------------------
# optional: plan usage via the statusline
# --------------------------------------------------------------------------


def statusline_state() -> tuple[object | None, str | None]:
    """Current ``statusLine`` block and its command, if any."""
    data = read_json(SETTINGS)
    if not isinstance(data, dict):
        return None, None
    block = data.get("statusLine")
    command = block.get("command") if isinstance(block, dict) else None
    return block, command if isinstance(command, str) else None


def offer_usage(dry: bool, decision: bool | None) -> None:
    step("Optional: 5h and 7d plan usage")

    block, current = statusline_state()

    if is_our_shim(current):
        ok("already wired up")
        return

    # An existing wrapper from a checkout that has since moved: re-point it,
    # but keep the statusline it was wrapping rather than nesting wrappers.
    relocating = is_some_shim(current)
    inner = (wrapped_inner(current) or "") if relocating else (current or "")

    if relocating:
        warn("found a wrapper from another checkout; re-pointing it here")

    print("    Claude Code only exposes plan percentages to its statusline")
    print("    command, so capturing them means pointing statusLine.command at")
    print("    a wrapper. Your existing statusline still runs, untouched, and")
    print("    its output is what you keep seeing.")
    print()
    if inner:
        note(f"your statusline: {inner}")
    else:
        note("you have no statusline configured; the wrapper will print the")
        note("current directory, which is more than you have now")
    print()

    if not shutil.which("jq"):
        warn("jq is not installed, and the capture needs it")
        note("install it with `brew install jq`, then re-run with --with-usage")
        note("everything else works without this; only the plan footer stays dark")
        return

    # An unreadable settings.json would otherwise be treated as an empty one
    # and written back containing nothing but our wrapper — permissions, hooks,
    # env and model silently discarded. There is a backup, but replacing a file
    # we cannot parse is not ours to decide.
    if SETTINGS.exists() and read_json(SETTINGS) is None:
        bad(f"{SETTINGS} is not valid JSON")
        note("fix or move it, then re-run with --with-usage")
        note("refusing to rewrite a settings file that cannot be read first")
        return

    wanted = decision if decision is not None else ask(
        "Wire it up?", default=True
    )
    if not wanted:
        note("skipped — add it later with `python3 install.py --with-usage`")
        return

    if dry:
        note(f"would set statusLine.command to: {statusline_command(inner)}")
        return

    data = read_json(SETTINGS)
    data = data if isinstance(data, dict) else {}

    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    backup = SETTINGS.with_name(f"settings.json.frenchy-backup-{stamp}")
    if SETTINGS.exists():
        shutil.copy2(SETTINGS, backup)
        ok(f"backed up settings to {backup.name}")

    new_block = dict(block) if isinstance(block, dict) else {}
    new_block.setdefault("type", "command")
    new_block["command"] = statusline_command(inner)
    data["statusLine"] = new_block
    write_json(SETTINGS, data)

    # Keep the whole previous block, not just the command string, so uninstall
    # restores padding and any other keys exactly as they were. Re-pointing a
    # relocated wrapper must not overwrite the receipt: the block it recorded
    # is the user's real statusline, and this one is only our own wrapper.
    if not relocating:
        write_json(RECEIPT, {"previous_status_line": block, "backup": str(backup)})
    ok("statusline wrapped")
    note("open a new Claude Code session; percentages appear within a few seconds")


def remove_usage(dry: bool) -> None:
    block, current = statusline_state()
    if not is_some_shim(current):
        ok("statusline was not ours; left alone")
        return

    if dry:
        note("would restore the previous statusLine block")
        return

    data = read_json(SETTINGS)
    if not isinstance(data, dict):
        warn("settings.json is unreadable; leaving it alone")
        return

    receipt = read_json(RECEIPT)
    previous = receipt.get("previous_status_line") if isinstance(receipt, dict) else None

    if isinstance(previous, dict):
        data["statusLine"] = previous
        ok("restored your previous statusline")
    elif (inner := wrapped_inner(current)):
        # No receipt, because the wrapper was put there by hand rather than by
        # this installer. The command still carries what it wraps, so unwrap it
        # — deleting the whole block instead loses a statusline we never owned.
        data["statusLine"] = {"type": "command", "command": inner}
        ok(f"unwrapped your statusline: {inner}")
    else:
        data.pop("statusLine", None)
        ok("removed the statusline we added")
    write_json(SETTINGS, data)


# --------------------------------------------------------------------------
# doctor
# --------------------------------------------------------------------------


def doctor() -> int:
    """Diagnose a meter that is not updating, in pipeline order."""
    step("frenchy-llm-meter doctor")
    problems = 0

    # 1. sessions registry — the zero-config core
    live = list(SESSIONS_DIR.glob("*.json")) if SESSIONS_DIR.is_dir() else []
    if live:
        ok(f"session registry: {len(live)} session file(s) in {SESSIONS_DIR}")
    else:
        warn(f"no session files in {SESSIONS_DIR} — no Claude Code running?")

    # 2. daemon
    uid = os.getuid()
    listed = run(["launchctl", "print", f"gui/{uid}/{LABEL}"], check=False)
    if listed.returncode == 0:
        state = ""
        for line in listed.stdout.splitlines():
            if "state =" in line:
                state = line.split("=", 1)[1].strip()
                break
        ok(f"service loaded, state = {state or 'unknown'}")
        if state and state != "running":
            problems += 1
            note(f"check {LOG}")
        elif LOG.exists():
            # "Running" only means the process exists. A daemon blocked on a
            # BLE connect looks perfectly healthy to launchd while doing
            # nothing at all, and the log going quiet is the only symptom.
            quiet = int(time.time() - LOG.stat().st_mtime)
            if quiet > 60:
                bad(f"process is up but has not logged for {quiet}s — wedged")
                note(f"restart it: launchctl kickstart -k gui/{uid}/{LABEL}")
                note("that clears it but says nothing about why. To find out, "
                     "stop the service and run:")
                note(f"  {VENV_PY} -m frenchy_llm_meter --probe")
                problems += 1
            else:
                ok(f"last logged {quiet}s ago")
    else:
        bad("service not loaded")
        note("run `python3 install.py` to install it")
        problems += 1

    # 3. plan usage (optional)
    _, current = statusline_state()
    if is_our_shim(current):
        ok("statusline wrapper installed")
        if not shutil.which("jq"):
            bad("but jq is missing, so nothing is ever captured")
            note("brew install jq")
            problems += 1
    elif current:
        warn("a statusline is configured but it is not ours")
        note("run `python3 install.py --with-usage` to wrap it")
    else:
        warn("no statusline wrapper: plan percentages are off")
        note("optional — run `python3 install.py --with-usage` to enable")

    # 4. the capture itself
    if USAGE_FILE.exists():
        age = int(time.time() - USAGE_FILE.stat().st_mtime)
        captured = read_json(USAGE_FILE)
        limits = captured.get("rate_limits") if isinstance(captured, dict) else None
        if age > 900:
            warn(f"capture is {age // 60} minutes old — no session has rendered since")
        else:
            ok(f"capture is {age}s old")
        if isinstance(limits, dict) and limits.get("five_hour"):
            pct = limits["five_hour"].get("used_percentage")
            shown = f"{pct:.1f}" if isinstance(pct, (int, float)) else pct
            ok(f"plan usage present: 5h at {shown}%")
        else:
            warn("capture has no rate_limits")
            note("these only exist for Claude.ai Pro/Max accounts, and only")
            note("after the first API response in a session")
    elif is_our_shim(current):
        bad(f"wrapper installed but nothing written to {USAGE_FILE}")
        problems += 1

    # 5. the shim path still resolving
    if is_some_shim(current) and not is_our_shim(current):
        bad("your statusline points at a different frenchy-llm-meter checkout")
        note("the repo was moved or renamed; re-run `python3 install.py --with-usage`")
        problems += 1

    print()
    if problems:
        print(f"  {_RED}{problems} problem(s){_OFF}")
        # Everything above checks the pipeline on this machine. If all of it
        # passes and the glass is still wrong, or the log is full of
        # "connect failed", the remaining question is where the BLE connect
        # dies — which is the one thing doctor cannot see from here.
        print()
        note("if the link itself is suspect, stop the service and run:")
        note(f"  {VENV_PY} -m frenchy_llm_meter --probe")
        note("it reports how far the connect gets, which says whether to look")
        note("at the Mac, the crab, or neither.")
    else:
        print(f"  {_GREEN}all good{_OFF}")
    return 1 if problems else 0


# --------------------------------------------------------------------------
# uninstall
# --------------------------------------------------------------------------


def uninstall(dry: bool) -> int:
    step("Removing frenchy-llm-meter")

    uid = os.getuid()
    if not dry:
        run(["launchctl", "bootout", f"gui/{uid}/{LABEL}"], check=False)
        if PLIST.exists():
            PLIST.unlink()
        ok("service stopped and removed")
    else:
        note(f"would bootout {LABEL} and delete {PLIST}")

    remove_usage(dry)

    print()
    note(f"the virtualenv is left in place; delete it with: rm -rf {VENV}")
    note("the repo itself is untouched")
    return 0


# --------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="install.py", description="Set up frenchy-llm-meter on this Mac."
    )
    parser.add_argument("--doctor", action="store_true",
                        help="diagnose an installed meter and exit")
    parser.add_argument("--uninstall", action="store_true",
                        help="stop the service and restore your statusline")
    parser.add_argument("--dry-run", action="store_true",
                        help="print what would change, touch nothing")
    usage = parser.add_mutually_exclusive_group()
    usage.add_argument("--with-usage", action="store_true",
                       help="wire up plan percentages without asking")
    usage.add_argument("--no-usage", action="store_true",
                       help="skip plan percentages without asking")
    args = parser.parse_args(argv)

    if args.doctor:
        return doctor()
    if args.uninstall:
        return uninstall(args.dry_run)

    decision = True if args.with_usage else False if args.no_usage else None

    preflight()
    build_venv(args.dry_run)

    # The optional half is settled before launchd starts, so the first thing
    # the service does is already the finished configuration.
    offer_usage(args.dry_run, decision)

    first_run(args.dry_run)
    install_agent(args.dry_run)

    step("Done")
    note("check on it any time with: python3 install.py --doctor")
    return 0


if __name__ == "__main__":
    sys.exit(main())
