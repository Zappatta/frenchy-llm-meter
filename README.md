# clawd-meter

Claude Code plan usage on a 1.28" round display, mounted in a 3D-printed Clawd.

A daemon on the Mac reads its own Claude Code state, works out how much of the
plan's 5-hour and 7-day windows have been consumed and how full each session's
context window is, then pushes a 108-byte frame to an ESP32-S3 over Bluetooth LE
every few seconds. The crab draws it. All the judgement lives on the host; the
firmware is a display.

BLE rather than WiFi so it works on any network — home, office, tethered — with
no credentials on the device and nothing to reconfigure when the network
changes.

```
Mac                                    ESP32-S3 Super Mini
├─ captures Claude Code's statusline   ├─ GC9A01 240x240 round TFT
├─ reads ~/.claude/sessions/*.json     ├─ onboard WS2812 (off by default)
├─ reads ~/.claude/projects/**.jsonl   │
└─ BLE GATT write ───────────────────► └─ renders concentric rings
```

<!-- TODO: photo of the assembled crab -->

---

# Build one

Two evenings, most of which is printing. You need a soldering iron only if your
display module ships without a header.

## 1. Parts

| Part | What to look for | Notes |
|---|---|---|
| **ESP32-S3 Super Mini** | USB-C, onboard WS2812 on GP48 | See the flash-size note below |
| **GC9A01 1.28" round TFT** | 240×240 IPS, **7-pin SPI** header | Not the 12-pin FPC variant |
| 7 × jumper wires | female–female, or thin silicone wire | ~10 cm is plenty |
| USB-C cable | data, not charge-only | For flashing and power |
| 3D printed enclosure | see below | |

**The flash-size trap.** These boards are sold as 8MB and many are actually
4MB. `platformio.ini` pins 4MB, which is correct for the boards this was built
against. If yours genuinely has 8MB and you want the space, change
`board_upload.flash_size` and `board_build.flash_size` together. Get this wrong
in the other direction and the board bootloops with:

```
Detected size(4096k) smaller than the size in the binary image header(8192k)
```

**Get the 7-pin module.** The 12-pin FPC version exposes the backlight pins and
a different connector; everything here assumes the 7-pin SPI header.

## 2. Print the enclosure

> **TODO — STLs not yet published.**
>
> The Fusion model is maintained outside this repo. When it lands, this section
> gets:
>
> - `enclosure/*.stl` — body, and whatever else it splits into
> - print settings: material, layer height, supports, orientation
> - any hardware (screws, magnets, heat-set inserts)
>
> Until then: the display is a 32.4 mm active circle in a ~37 mm module, and
> the board is an ESP32-S3 Super Mini. Any case that holds both and lets a
> USB-C cable in will do.

## 3. Wire it up

Seven wires. All five display signals sit on the right-hand header in one run,
so the loom does not cross itself. GP48 is skipped deliberately — the onboard
WS2812 uses it.

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

**If the display ends up upside down in your case, leave it.** That is handled
in software — `SCREEN_ROTATION` in `firmware/src/config.h` is set to 2 for
exactly this reason. Change it to 0 if yours sits the right way up.

## 4. Flash the firmware

Needs [PlatformIO](https://platformio.org/install/cli):

```sh
cd firmware
pio run -t upload      # build and flash over USB-C
pio device monitor     # 115200, to watch it boot
```

On first boot you should see `NO LINK` in red with a red dot — the device has
never been fed. That is correct; the Mac side comes next.

## 5. Install on the Mac

macOS only for now — launchd and the BLE central are both Mac-specific.

```sh
python3 install.py
```

That builds the virtualenv, runs the daemon once in the foreground, then
installs and starts the login service. The foreground run is deliberate: it is
where macOS raises its **Bluetooth permission prompt**, and a launchd job has no
window to prompt from. Say yes, or the meter can never be reached.

Within a few seconds the crab should show rings and a green dot.

```sh
python3 install.py --doctor      # why isn't it updating?
python3 install.py --uninstall   # stop the service, restore your statusline
python3 install.py --dry-run     # print what would change, touch nothing
```

## 6. Optional: plan usage

Everything above works with no configuration at all — rings, session states,
labels. The **5-hour and 7-day plan percentages** are the one part that needs
setup, because Claude Code only ever exposes them to a statusline command.

```sh
brew install jq
python3 install.py --with-usage
```

This wraps whatever `statusLine.command` you already have rather than replacing
it, backs up `settings.json` first, and `--uninstall` puts the original back.
Your terminal statusline keeps rendering exactly as before.

Without it, rings and states still work; the plan arc stays empty and the hub
footer reads `no plan`.

> Plan percentages only exist for Claude.ai **Pro/Max** accounts, and only after
> the first API response in a session. On an API-key, Team or Enterprise account
> they never appear, and that is not a fault in the meter.

## Troubleshooting

**`python3 install.py --doctor` first.** It walks the pipeline in order —
session registry, service, statusline wrapper, capture freshness, whether the
capture actually contains rate limits — and tells you which link is broken.

**The colours look wrong.** GC9A01 modules ship wired either BGR or RGB and the
panel cannot be asked which it is, so it is a build-time choice. The default is
BGR. Flash the default, then look at a session that is working: the ring should
be **amber**. If it is **blue**, you have the other variant:

```sh
pio run -e clawd-meter-rgb -t upload
```

Green is unaffected by the swap — it sits in the middle channel — so a wrong
setting looks plausible until you notice `COL_ALERT` rendering blue and your
warnings no longer looking like warnings.

**It says NO LINK.** The device has never received a frame. Check the service
is running (`--doctor`), and that you granted Bluetooth permission.

**The figures are frozen and the dot is red.** The link dropped; the last known
figures are being held deliberately rather than blanking the screen. If the Mac
slept, the device drops the stale connection and re-advertises after 60 seconds,
and the daemon reconnects on its next scan.

**Nothing on screen ever moves.** By design. Both the ring pulse and the status
LED were tried and removed — anything animating on a desk all day becomes
nagging rather than signalling. `STATUS_LED_ENABLED` in `firmware/src/config.h`
turns the LED back on if you disagree.

---

# What it shows

**Outer arc** — the 5-hour plan window, filling clockwise as you consume it.
Turns red past 85%. Empty track means no plan reading.

**Rings** — one per open session, nested, **least context left on the outside**.
Each ring's fill is how much of that session's context window is still free, so
a ring drains as the session fills up and a nearly-empty ring is a session about
to compact. Colour is session state — amber working, green waiting for input —
except below 15% left, where it turns red regardless: running out of context
outranks whatever the session is doing.

**Hub** — context left, big, for whichever session runs out first, plus the
7-day figure on a compact line beneath.

**Dot** — green when the host is talking to the device, red when it has gone
quiet. When it is red the figures on screen are the last ones received, held
deliberately: stale numbers with a warning beat a blank screen.

A session gets a ring if its process is running, full stop. Closing a terminal
removes its ring within one poll.

**LED** — the onboard WS2812 is **off by default**. Set `STATUS_LED_ENABLED` in
`firmware/src/config.h` to get it back; the scheme is intact:

| State | Colour |
|---|---|
| No sessions | dim blue |
| Any session working, none waiting | amber, slow breathe |
| Any session waiting for input | green, pulsing |
| Near a plan limit, or host unreachable | red |

Waiting outranks working: if one session is churning and another wants you, the
actionable one wins.

---

# How it works

Three sources, ordered by what they cost you to set up.

## `~/.claude/sessions/<pid>.json` — free

Maintained by every running Claude Code process. Gives session liveness,
`status` (busy/idle), cwd and a name, with no configuration at all. A running
process is the definitive answer to "is this session open", and `status` beats
guessing from a transcript that may end mid-tool-call.

Two traps in those files. `updatedAt` moves when the status *changes*, not on a
heartbeat — an idle session can sit for days with a stale timestamp and a very
live process — so liveness comes from the pid, never the timestamp. And a
`derived` name like `code-e4` is worse than the transcript's AI-generated title,
so only a name you actually chose is preferred over it.

## `~/.claude/projects/**.jsonl` — free

Per-session token history, turn boundaries, and a context estimate for any
session with no statusline capture.

Two things about the format, both found by reading real transcripts, either of
which silently corrupts the numbers if missed:

1. **A single API response is written as several `assistant` lines** — thinking,
   text and tool_use each get their own record, all repeating the same
   `requestId`, `message.id` and `usage` object. Summing naively inflates token
   counts by 2–3×. Records are deduplicated on that pair.

2. **`message.stop_reason` is the state signal.** `tool_use` means mid-turn;
   `end_turn` means Claude has finished and is waiting on you. File modification
   times alone cannot tell those apart.

Subagent transcripts under `<session>/subagents/` roll their tokens up into the
parent session but deliberately do not set the parent's state — a subagent
finishing is not the parent asking for input.

## The statusline payload — needs the shim

Claude Code hands its statusline command the real figures on stdin:

```json
"rate_limits": {
  "five_hour": { "used_percentage": 23.5, "resets_at": 1738425600 },
  "seven_day": { "used_percentage": 41.2, "resets_at": 1738857600 }
}
```

The same numbers `/usage` reports. No API call, no OAuth token, nothing to
calibrate — and `resets_at` gives the countdown for free.

This is the **only** place they are exposed. Not in hooks, not in OpenTelemetry,
not in any transcript or state file on disk. That is why the shim exists, and
why it is the one optional part of the install.

`statusline-hook.sh` captures the blob to
`~/.local/state/clawd-meter/usage.json` and hands the untouched payload to your
existing statusline. Everything it does is guarded and its stderr discarded — a
failure to record usage can never break your statusline. The capture is only as
fresh as the last render, which stops when you close every session; a reading
older than 15 minutes is flagged stale and dimmed rather than passed off as
current.

## Running it by hand

```sh
cd host
python3 -m venv .venv
.venv/bin/pip install -e '.[dev]'

.venv/bin/python -m clawd_meter --dry-run     # print to stdout, no BLE
.venv/bin/python -m clawd_meter               # connect and push
.venv/bin/python -m pytest tests/ -q
```

| Variable | Purpose |
|---|---|
| `CLAWD_PROJECTS_DIR` | override `~/.claude/projects` |
| `CLAWD_SESSIONS_DIR` | override `~/.claude/sessions` |
| `CLAWD_STATE_DIR` | override where the usage capture is written and read |
| `CLAWD_INNER_STATUSLINE` | the statusline command the shim wraps |

---

# Known hardware constraints

- **The backlight cannot be dimmed or switched off.** LEDA/LEDK only exist on
  the 12-pin FPC pads and LEDA is tied through R6 to +3.3V; nothing on the
  7-pin header controls it. The UI is dark-on-black to compensate. Turning the
  display off means cutting power or modifying the board.
- **No touch.** This module has no touch controller — the 7-pin header is power
  and SPI only. Cycling between sessions would need a capacitive pad on one of
  GPIO1–14 (the ESP32-S3 has a touch peripheral) or a physical button. Not
  needed at four sessions, which all fit on screen at once.
- **Write-only SPI.** No MISO is broken out, so the panel cannot be read back.
- **Four rings is the practical ceiling** before the arcs get too thin to read.
- **macOS does not hang up when it sleeps.** It keeps the BLE link alive in the
  controller while the owning process goes away, so the device never sees a
  disconnect. The firmware drops a connection that has been silent for 60
  seconds and re-advertises; without that it stays deaf until power-cycled.

# Layout

```
clawd-meter/
├── install.py                one-shot macOS setup, --doctor, --uninstall
├── docs/protocol.md          wire format, shared by both ends
├── host/
│   ├── statusline-hook.sh    captures Claude Code's real usage figures
│   └── clawd_meter/
│       ├── sessions.py       the live session registry
│       ├── transcripts.py    parse ~/.claude, dedupe, derive state
│       ├── usage.py          reads the statusline capture
│       ├── models.py         per-model context window sizes
│       ├── plan.py           assembles the frame
│       ├── protocol.py       frame encoder
│       ├── ble.py            BLE central, reconnect loop
│       └── daemon.py         poll loop and CLI
└── firmware/src/
    ├── config.h              pins, palette, geometry, UUIDs
    ├── protocol.h            frame decoder (mirrors protocol.py)
    ├── display.cpp           LovyanGFX arc, rings and hub
    ├── ble_link.cpp          NimBLE peripheral and the silence watchdog
    ├── status_led.cpp        WS2812 state colours (disabled by default)
    ├── anim.h                shared pulse maths
    └── main.cpp
```

The enclosure is **not** in this repo — the Fusion model is owned separately.
