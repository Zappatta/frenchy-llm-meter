"""Wire format for the BLE state payload.

Little-endian, fixed-width, no framing — one GATT write is one whole payload.
Keeping it binary means the firmware needs no JSON parser and every payload
has a known maximum size that fits inside a single negotiated MTU.

    header   12 bytes
    session  24 bytes  x count (max 4)
    -------------------------
    total   108 bytes max

See docs/protocol.md for the field-by-field description.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass, field

MAGIC = 0xC1
VERSION = 1
MAX_SESSIONS = 4
LABEL_LEN = 16

HEADER = struct.Struct("<BBBBHHHH")
SESSION = struct.Struct(f"<BBHI{LABEL_LEN}s")

MAX_PAYLOAD = HEADER.size + SESSION.size * MAX_SESSIONS

FLAG_LIMIT_WARN = 1 << 0  # at or above WARN_AT on either window
FLAG_HOST_ERROR = 1 << 1  # host could not read transcripts this tick
FLAG_STALE = 1 << 2  # last usage capture is old; figures are not current
FLAG_NO_USAGE = 1 << 3  # no capture at all — statusline shim not installed


@dataclass
class SessionFrame:
    state: int
    ctx_pct: int  # 0..100, percentage of this session's context window still free
    tokens: int
    label: str


@dataclass
class StateFrame:
    """One snapshot of plan usage.

    Percentages are tenths of a percent (``625`` -> 62.5%) and are allowed to
    exceed 1000 so an over-limit state is visible rather than clamped. Reset
    times are whole minutes remaining in the window.
    """

    sessions: list[SessionFrame] = field(default_factory=list)
    pct_5h_x10: int = 0
    pct_7d_x10: int = 0
    resets_5h_min: int = 0
    resets_7d_min: int = 0
    flags: int = 0

    def encode(self) -> bytes:
        sessions = self.sessions[:MAX_SESSIONS]
        out = bytearray(
            HEADER.pack(
                MAGIC,
                VERSION,
                self.flags & 0xFF,
                len(sessions),
                min(max(self.pct_5h_x10, 0), 0xFFFF),
                min(max(self.pct_7d_x10, 0), 0xFFFF),
                min(max(self.resets_5h_min, 0), 0xFFFF),
                min(max(self.resets_7d_min, 0), 0xFFFF),
            )
        )
        for s in sessions:
            label = s.label.encode("ascii", "replace")[:LABEL_LEN]
            out += SESSION.pack(
                s.state & 0xFF,
                max(0, min(s.ctx_pct, 100)),
                0,
                min(max(s.tokens, 0), 0xFFFFFFFF),
                label,
            )
        return bytes(out)


def decode(payload: bytes) -> StateFrame:
    """Round-trip helper, used by the tests and by ``--dry-run``."""
    magic, version, flags, count, pct5, pct7, reset5, reset7 = HEADER.unpack_from(
        payload
    )
    if magic != MAGIC:
        raise ValueError(f"bad magic 0x{magic:02X}")
    if version != VERSION:
        raise ValueError(f"unsupported protocol version {version}")

    sessions = []
    off = HEADER.size
    for _ in range(count):
        state, ctx, _res, tokens, label = SESSION.unpack_from(payload, off)
        off += SESSION.size
        sessions.append(
            SessionFrame(
                state=state,
                ctx_pct=ctx,
                tokens=tokens,
                label=label.rstrip(b"\x00").decode("ascii", "replace"),
            )
        )
    return StateFrame(
        sessions=sessions,
        pct_5h_x10=pct5,
        pct_7d_x10=pct7,
        resets_5h_min=reset5,
        resets_7d_min=reset7,
        flags=flags,
    )
