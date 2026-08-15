# Wire protocol

One GATT write is one complete state frame. Little-endian, fixed width, no
framing or fragmentation — the payload is bounded at 108 bytes, which fits
inside a single negotiated MTU (the firmware asks for 247).

Implemented in two places that must stay in step:

- `host/frenchy_llm_meter/protocol.py` — encoder
- `firmware/src/protocol.h` — decoder

## BLE

| | |
|---|---|
| Device name | `frenchy-llm-meter` |
| Service UUID | `6b1d0001-9a3f-4c6e-b0d2-7f2a5c8e41aa` |
| State characteristic | `6b1d0002-9a3f-4c6e-b0d2-7f2a5c8e41aa`, write |

The ESP32-S3 is the peripheral; the Mac is the central. The host writes with
response — one payload every few seconds is nowhere near a throughput concern,
and the acknowledgement is how the host learns the link is genuinely alive
rather than merely believed to be.

## Header — 12 bytes

| Offset | Type | Field | Notes |
|---|---|---|---|
| 0 | `u8` | magic | always `0xC1` |
| 1 | `u8` | version | `1`; a mismatch is rejected, not guessed at |
| 2 | `u8` | flags | see below |
| 3 | `u8` | count | number of session records, 0–4 |
| 4 | `u16` | pct_5h_x10 | tenths of a percent of the 5-hour plan window |
| 6 | `u16` | pct_7d_x10 | tenths of a percent of the 7-day plan window |
| 8 | `u16` | resets_5h_min | whole minutes until the 5-hour window resets |
| 10 | `u16` | resets_7d_min | whole minutes until the 7-day window resets |

Percentages are allowed to exceed `1000` (100.0%) so an over-limit state stays
visible instead of being clamped to a reassuring number.

Both percentages and both reset times come from Claude Code itself, via the
statusline payload — see the host README. They are the same numbers `/usage`
reports, not an estimate.

### Flags

| Bit | Name | Meaning |
|---|---|---|
| 0 | `LIMIT_WARN` | at or above 85% on either window |
| 1 | `HOST_ERROR` | the host could not read transcripts this tick |
| 2 | `STALE` | the last usage capture is old; figures are not current |
| 3 | `NO_USAGE` | no capture at all — the statusline shim is not installed |

`NO_USAGE` means both percentage fields are zero and meaningless; the display
shows the live session count instead of a gauge. `STALE` means the figures are
real but were captured a while ago, because no Claude Code session has rendered
a statusline since — they are shown greyed rather than hidden, since the last
known number still beats nothing.

`LIMIT_WARN` is never set alongside `STALE` or `NO_USAGE`: warning about a
ceiling on the strength of an old or absent reading is how a status light loses
its meaning.

## Session record — 24 bytes, repeated `count` times

| Offset | Type | Field | Notes |
|---|---|---|---|
| 0 | `u8` | state | 0 idle, 1 working, 2 waiting, 3 error |
| 1 | `u8` | ctx_pct | 0–100, percentage of this session's context window still free |
| 2 | `u16` | reserved | zero |
| 4 | `u32` | tokens | raw tokens in the window, display only |
| 8 | `char[16]` | label | ASCII, NUL-padded, truncated not rejected |

Records arrive sorted by context remaining, **least first**, so record 0 owns
the outer ring — the session that runs out first is the one you see first.

`ctx_pct` is per session and the values are independent; they do not sum to
anything meaningful. Plan utilisation is account-level and lives in the header,
which is why it is drawn as a footer line rather than on a ring.

## Sizing

```
header                 12
session record  4 x 24  96
                    -----
maximum               108 bytes
```
