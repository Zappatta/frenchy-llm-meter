"""Diagnose a BLE link that will not come up, by reporting where it dies.

The daemon's log says `connect failed: ` and nothing else, which is true and
useless: a link that fails at the radio and a link that fails three GATT
operations later look identical from there. RSSI does not separate them either
— a connection that completes service discovery has plenty of signal by
definition, however grim the number looks.

What does separate them is how far the connect sequence got. `bleak` already
narrates every step at DEBUG; this captures that narration, maps it onto named
stages, and reports the furthest one reached. The stage is the diagnosis:

The stage names below are exactly the names in STAGES, and must stay that
way: they are what gets printed, so they are what someone greps for.

  connecting                 -> range, or the peer is not accepting
  connected                  -> link layer fine; the peer is closing it
  retrieving_services        -> GATT server, or a stale connection
  services_discovered        -> the service definition serves correctly
  retrieving_characteristics -> the server is complete; look elsewhere

Read-only by default. The write test is opt-in because it exercises the
characteristic the daemon owns, and a probe should not be able to disturb
what it is measuring unless asked.
"""

from __future__ import annotations

import asyncio
import logging

from bleak import BleakClient, BleakScanner
from bleak.exc import BleakError

from .ble import DEVICE_NAME, SERVICE_UUID, STATE_CHAR_UUID

log = logging.getLogger(__name__)

# Ordered worst to best. Each entry is a stage name and the substrings in
# bleak's DEBUG output that prove it was reached. Substrings rather than exact
# lines: these are log messages, not an API, and they get reworded.
STAGES: list[tuple[str, tuple[str, ...]]] = [
    ("connecting", ("Connecting to BLE device",)),
    ("connected", ("didConnectPeripheral",)),
    ("retrieving_services", ("Retrieving services",)),
    ("services_discovered", ("didDiscoverServices", "Services discovered")),
    ("retrieving_characteristics", ("Retrieving characteristics",)),
]

DROP_MARKERS = ("didDisconnectPeripheral", "Peripheral Device disconnected")


def furthest_stage(lines: list[str]) -> str:
    """Name the last stage the captured log proves was reached.

    Pure so it can be tested without a radio: the interesting logic is the
    ordering, and that is exactly what breaks when bleak rewords a message.
    """
    reached = "nothing"
    for name, markers in STAGES:
        if any(m in line for line in lines for m in markers):
            reached = name
    return reached


def peer_dropped(lines: list[str]) -> bool:
    return any(m in line for line in lines for m in DROP_MARKERS)


class _Capture(logging.Handler):
    """Collects bleak's own narration of the connect sequence."""

    def __init__(self) -> None:
        super().__init__(level=logging.DEBUG)
        self.lines: list[str] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.lines.append(record.getMessage())


async def _attempt(device, do_write: bool, hold: float) -> tuple[bool, str, list[str]]:
    cap = _Capture()
    bleak_log = logging.getLogger("bleak")
    prev_level = bleak_log.level
    prev_propagate = bleak_log.propagate
    bleak_log.setLevel(logging.DEBUG)
    # Capture without printing. Raising the level alone sends every line to the
    # root handler too, which buries the probe's own verdict under fifty lines
    # of narration — the exact output this tool exists to summarise.
    bleak_log.propagate = False
    bleak_log.addHandler(cap)
    try:
        client = BleakClient(device, timeout=25.0)
        try:
            await client.connect()
            stage = "connected"
            services = list(client.services)
            if services:
                stage = "services_discovered"
            char = client.services.get_characteristic(STATE_CHAR_UUID)
            if char is not None:
                stage = "characteristics_found"
            if do_write:
                # Deliberately malformed: the firmware validates the header and
                # logs a rejection, so this proves the write path round-trips
                # without putting a fabricated reading on the glass.
                await client.write_gatt_char(STATE_CHAR_UUID, b"\x00", response=True)
                stage = "wrote"
            await asyncio.sleep(hold)
            if not client.is_connected:
                return False, f"{stage}, then dropped while idle", cap.lines
            return True, stage, cap.lines
        finally:
            try:
                await asyncio.wait_for(client.disconnect(), timeout=5.0)
            except (BleakError, OSError, asyncio.TimeoutError):
                pass
    except (BleakError, asyncio.TimeoutError, OSError) as exc:
        stage = furthest_stage(cap.lines)
        detail = f"{stage} -> {type(exc).__name__}: {exc}"
        if peer_dropped(cap.lines):
            detail += " (peer closed the link)"
        return False, detail, cap.lines
    finally:
        bleak_log.removeHandler(cap)
        bleak_log.setLevel(prev_level)
        bleak_log.propagate = prev_propagate


async def run(attempts: int, do_write: bool, hold: float, device_name: str) -> int:
    print(f"probing for {device_name} (service {SERVICE_UUID})")

    seen: list[int] = []

    def match(d, adv) -> bool:
        uuids = [u.lower() for u in (adv.service_uuids or [])]
        if SERVICE_UUID.lower() in uuids or (d.name or "") == device_name:
            if adv.rssi is not None:
                seen.append(adv.rssi)
            return True
        return False

    device = await BleakScanner.find_device_by_filter(match, timeout=15.0)
    if device is None:
        print("  NOT ADVERTISING — the device is off, out of range, or already")
        print("  connected to something (NimBLE does not advertise while connected).")
        return 1

    rssi = f"{seen[-1]} dBm" if seen else "unknown"
    print(f"  advertising: {device.address}  name={device.name!r}  rssi={rssi}")
    print("  (rssi is context, not a verdict — see the stage below)")

    ok = 0
    for i in range(1, attempts + 1):
        good, detail, lines = await _attempt(device, do_write, hold)
        mark = "OK  " if good else "FAIL"
        print(f"  attempt {i}/{attempts}: {mark} {detail}")
        if good:
            ok += 1
        elif lines:
            print(f"           last: {lines[-1][:100]}")
        await asyncio.sleep(2)

    print(f"\n{ok}/{attempts} attempts reached the end of the sequence")
    if ok == attempts:
        print("The link is healthy. If the display disagrees, the fault is not the link.")
    elif ok:
        print("Intermittent. A link that completes sometimes is a state or timing")
        print("problem, not a range problem.")
    else:
        print("Never completed. The stage above says which end to look at:")
        print("  stuck at connecting            -> range, or the peer is not accepting")
        print("  connected then dropped         -> the peer is closing it; check its console")
        print("  services/characteristics stage -> GATT server or a stale connection")
    return 0 if ok else 2
