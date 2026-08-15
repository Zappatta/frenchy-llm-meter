# clawd-meter

Claude Code plan usage on a 1.28" round display, mounted in a 3D-printed Clawd.

A daemon on the Mac reads its own Claude Code transcripts, works out how much of
the Max plan's 5-hour and 7-day windows have been consumed, and pushes a
108-byte frame to an ESP32-S3 over Bluetooth LE every few seconds. The crab
draws it. All the judgement lives on the host; the firmware is a display.

BLE rather than WiFi so it works on any network — home, office, tethered,
whatever — with no credentials on the device and nothing to reconfigure when
the network changes.

```
Mac                                    ESP32-S3 Super Mini
├─ captures Claude Code's statusline   ├─ GC9A01 240x240 round TFT
├─ reads ~/.claude/projects/**.jsonl   ├─ onboard WS2812 status LED
└─ BLE GATT write ───────────────────► └─ renders concentric rings
```

## What it shows

**Rings** — one per open session, nested, **least context left on the outside**.
Each ring's fill is how much of that session's context window is still free, so
a ring drains as the session fills up and a nearly-empty ring is a session about
to compact. Ring colour is session state — amber working, green waiting for
input, grey idle — except below 15% left, where it turns red regardless:
running out of context outranks whatever the session is doing.

**Hub** — context left, big, for whichever session will run out first. Plan
usage is demoted to a footer line (`5h 2%  7d 14%`), because it moves slowly
and the context figure is the one that changes what you do next.

A session counts as open if its statusline rendered in the last five minutes.
That is a better liveness signal than transcript activity, which goes quiet the
moment you stop typing even though the terminal is still sitting there.

**LED** — the onboard WS2812 is **off by default**. Anything moving on a desk
all day turns into nagging rather than signalling, which is also why the rings
do not pulse. Set `STATUS_LED_ENABLED` in `firmware/src/config.h` to get it
back; the scheme is intact:

| State | Colour |
|---|---|
| No sessions | dim blue |
| Any session working, none waiting | amber, slow breathe |
| Any session waiting for input | green, pulsing |
| Near a plan limit, or host unreachable | red |

Waiting outranks working: if one session is churning and another wants you, the
actionable one wins.

## Where the plan percentages come from

Claude Code already knows them, and hands them to any statusline script on
stdin:

```json
"rate_limits": {
  "five_hour": { "used_percentage": 23.5, "resets_at": 1738425600 },
  "seven_day": { "used_percentage": 41.2, "resets_at": 1738857600 }
}
```

These are the same figures `/usage` reports. No API call, no OAuth token,
nothing to calibrate — and `resets_at` gives a countdown to the window rolling
over for free.

`statusline-hook.sh` is a shim that captures that blob to
`~/.local/state/clawd-meter/usage.json` and then hands the untouched payload to
your existing statusline, so what you see in the terminal does not change.

### Installing the shim

`python3 install.py --with-usage` does it, or answer yes when the installer
offers. It wraps whatever `statusLine.command` you already have rather than
replacing it, backs up `settings.json` first, and `--uninstall` puts the
original back.

It needs `jq` (`brew install jq`); without it the capture silently does
nothing, so the installer refuses rather than leaving you with a meter that
looks broken.

Everything the shim does is guarded and its stderr discarded — a failure to
record usage can never break your statusline. Without it the rings still work;
the hub shows the live session count and the daemon logs `plan usage off`.

The capture is only as fresh as the last statusline render, which stops when you
close every session. A reading older than 15 minutes is flagged stale and shown
greyed rather than passed off as current.

### What the other two sources are for

`~/.claude/sessions/<pid>.json` is maintained by every running Claude Code
process and needs no setup at all. It gives session liveness, `status`
(busy/idle), cwd and a name. A running process is the definitive answer to
"is this session open", and `status` beats guessing from a transcript that may
end mid-tool-call.

Two traps in those files. `updatedAt` moves when the status *changes*, not on a
heartbeat — an idle session can sit for days with a stale timestamp and a very
live process — so liveness comes from the pid, never the timestamp. And a
`derived` name like `code-e4` is worse than the transcript's AI-generated
title, so only a name the user actually chose is preferred over it.

Transcripts supply the rest: `message.stop_reason` for working vs
waiting-on-input, per-session token history, and a context estimate for any
session with no statusline capture.

An earlier version weighted tokens per model into "Opus-equivalent tokens" to
compute plan percentages. Both consumers of that went away — the statusline
reports the real percentages, and the rings became context rather than usage
share — so the rate table and cache multipliers were deleted rather than left
sitting there looking load-bearing.

## Host setup

```sh
python3 install.py
```

That builds the virtualenv, runs the daemon once in the foreground — which is
where macOS raises its Bluetooth permission prompt, since a launchd job has no
window to prompt from — then installs and starts the login service. It ends by
offering the optional plan-usage hook described above.

```sh
python3 install.py --doctor      # why isn't it updating?
python3 install.py --with-usage  # add plan percentages later
python3 install.py --uninstall   # stop the service, restore your statusline
python3 install.py --dry-run     # print what would change, touch nothing
```

Running it by hand instead:

```sh
cd host
python3 -m venv .venv
.venv/bin/pip install -e '.[dev]'

.venv/bin/python -m clawd_meter --dry-run     # print to stdout, no BLE
.venv/bin/python -m clawd_meter               # connect and push
```

| Variable | Purpose |
|---|---|
| `CLAWD_PROJECTS_DIR` | override `~/.claude/projects` |
| `CLAWD_SESSIONS_DIR` | override `~/.claude/sessions` |
| `CLAWD_STATE_DIR` | override where the usage capture is written and read |
| `CLAWD_INNER_STATUSLINE` | the statusline script the shim wraps |

```sh
.venv/bin/python -m pytest tests/ -q
```

## Firmware

```sh
cd firmware
pio run              # build
pio run -t upload    # flash over USB-C
pio device monitor   # 115200
```

### If the colours look wrong

GC9A01 modules ship wired either BGR or RGB, and the panel cannot be asked
which it is, so it is a build-time choice. The default is BGR.

Flash the default first and look at a session that is working. The ring should
be **amber**. If it is **blue**, you have the other variant:

```sh
pio run -e clawd-meter-rgb -t upload
```

Green is unaffected by the swap — it sits in the middle channel — so a wrong
setting looks plausible until you notice that `COL_ALERT` renders blue and your
warnings stop looking like warnings.

### Wiring

All five display lines sit on the right-hand header in one run, so the ribbon
does not cross itself. GP48 is skipped deliberately — the onboard WS2812 uses it.

| GC9A01 pin | Signal | ESP32-S3 |
|---|---|---|
| 1 | VCC | 3V3 |
| 2 | GND | GND |
| 3 | SCL | GPIO12 |
| 4 | SDA | GPIO11 |
| 5 | DC | GPIO10 |
| 6 | CS | GPIO9 |
| 7 | RST | GPIO8 |

The module regulates VCC on-board (XC6206P332MR), so 3V3 or 5V both work.

## Known hardware constraints

- **The backlight cannot be dimmed or switched off.** LEDA/LEDK only exist on
  the 12-pin FPC pads and LEDA is tied through R6 to +3.3V; nothing on the
  7-pin header controls it. The UI is dark-on-black to compensate. Turning the
  display off means cutting power or modifying the board.
- **No touch.** This module has no touch controller — the 7-pin header is
  power and SPI only. Cycling between sessions would need a capacitive pad on
  one of GPIO1–14 (the ESP32-S3 has a touch peripheral) or a physical button.
  Not needed at four sessions, which all fit on screen at once.
- **Write-only SPI.** No MISO is broken out, so the panel cannot be read back.
- **Four rings is the practical ceiling** before the arcs get too thin to read.

## Layout

```
clawd-meter/
├── docs/protocol.md          wire format, shared by both ends
├── host/
│   ├── statusline-hook.sh    captures Claude Code's real usage figures
│   └── clawd_meter/
│       ├── usage.py          reads the capture
│       ├── transcripts.py    parse ~/.claude, dedupe, derive state
│       ├── models.py         per-model context window sizes
│       ├── plan.py           assembles the frame
│       ├── protocol.py       frame encoder
│       ├── ble.py            BLE central, reconnect loop
│       └── daemon.py         poll loop and CLI
└── firmware/src/
    ├── config.h              pins, palette, geometry, UUIDs
    ├── protocol.h            frame decoder (mirrors protocol.py)
    ├── display.cpp           LovyanGFX rings and hub
    ├── status_led.cpp        WS2812 state colours
    ├── ble_link.cpp          NimBLE peripheral
    └── main.cpp
```

The enclosure is **not** in this repo — the Fusion model is owned separately.

## Two things worth knowing about the transcript format

Both were found by reading real transcripts, and either one silently corrupts
the numbers if missed:

1. **A single API response is written as several `assistant` lines** — thinking,
   text, and tool_use each get their own record, all repeating the same
   `requestId`, `message.id`, and `usage` object. Summing naively inflates
   token counts by 2–3x. Records are deduplicated on that pair.

2. **`message.stop_reason` is the state signal.** `tool_use` means mid-turn
   (working); `end_turn` means Claude has finished and is waiting on you. File
   modification times alone cannot tell those apart.

Subagent transcripts under `<session>/subagents/` roll their tokens up into the
parent session, but deliberately do not set the parent's state — a subagent
finishing is not the parent asking for input.
