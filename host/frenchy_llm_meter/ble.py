"""BLE central: find the crab, stay connected, push state to it.

macOS CoreBluetooth is BLE-only and the ESP32-S3 is BLE-only (no Bluetooth
Classic), so the two line up. `bleak` wraps CoreBluetooth on this platform.

The link is deliberately dumb: reconnect forever, write the whole state on
every tick, never expect a reply. A dropped packet costs one stale frame on
a screen that refreshes every few seconds.
"""

from __future__ import annotations

import asyncio
import logging

from bleak import BleakClient, BleakScanner
from bleak.exc import BleakError

log = logging.getLogger(__name__)

DEVICE_NAME = "frenchy-llm-meter"
SERVICE_UUID = "6b1d0001-9a3f-4c6e-b0d2-7f2a5c8e41aa"
STATE_CHAR_UUID = "6b1d0002-9a3f-4c6e-b0d2-7f2a5c8e41aa"

SCAN_TIMEOUT = 10.0
RECONNECT_DELAY = 3.0
MAX_RECONNECT_DELAY = 30.0

# CoreBluetooth's connectPeripheral has no timeout of its own — it waits
# forever. If the crab still believes an earlier central is attached, an
# unbounded connect wedges the daemon permanently: no log line, no reconnect,
# a process that looks healthy and does nothing.
CONNECT_TIMEOUT = 20.0

# The same trap as CONNECT_TIMEOUT, one call along. A response=True write waits
# for the peripheral to acknowledge, and macOS does not report a link that died
# in the controller — so the ack never arrives, the await never returns, and the
# daemon wedges with the process alive at 0% CPU and not one line in the log.
# Observed exactly that: connected 15:47:09, then two hours of silence.
#
# Generous rather than tight: one payload every few seconds is not a throughput
# problem, and a slow ack is worth waiting for. It is an upper bound on being
# stuck, not a latency target.
WRITE_TIMEOUT = 10.0

# Disconnect is an await on the same stack and hangs for the same reason. It is
# on the recovery path, so hanging here strands the daemon in the one place it
# was trying to get itself out of trouble.
DISCONNECT_TIMEOUT = 5.0


class Link:
    """Maintains a connection to the device, reconnecting as needed."""

    def __init__(self, device_name: str = DEVICE_NAME) -> None:
        self.device_name = device_name
        self._client: BleakClient | None = None
        self._backoff = RECONNECT_DELAY

    @property
    def connected(self) -> bool:
        return self._client is not None and self._client.is_connected

    async def _discover(self):
        log.debug("scanning for %s", self.device_name)
        # Match on the advertised service UUID rather than the name alone:
        # macOS caches names aggressively and a renamed device would be missed.
        device = await BleakScanner.find_device_by_filter(
            lambda d, adv: (
                (d.name or "") == self.device_name
                or SERVICE_UUID.lower() in [u.lower() for u in adv.service_uuids]
            ),
            timeout=SCAN_TIMEOUT,
        )
        return device

    async def ensure_connected(self) -> bool:
        if self.connected:
            return True

        await self.close()

        device = await self._discover()
        if device is None:
            log.info("device not found; retrying in %.0fs", self._backoff)
            await asyncio.sleep(self._backoff)
            self._backoff = min(self._backoff * 2, MAX_RECONNECT_DELAY)
            return False

        client = BleakClient(device, disconnected_callback=self._on_disconnect)
        try:
            await asyncio.wait_for(client.connect(), timeout=CONNECT_TIMEOUT)
            self._client = client
            self._backoff = RECONNECT_DELAY
            log.info("connected to %s", device.address)
            return True
        except (BleakError, asyncio.TimeoutError, OSError) as exc:
            log.warning("connect failed: %s", exc)
            self._client = None
            # A timed-out connect can still complete underneath us, leaving a
            # link nobody owns and the crab refusing the next attempt.
            try:
                await asyncio.wait_for(client.disconnect(), timeout=DISCONNECT_TIMEOUT)
            except (BleakError, OSError, asyncio.TimeoutError):
                pass
            await asyncio.sleep(self._backoff)
            self._backoff = min(self._backoff * 2, MAX_RECONNECT_DELAY)
            return False

    def _on_disconnect(self, _client: BleakClient) -> None:
        log.info("disconnected")
        self._client = None

    async def send(self, payload: bytes) -> bool:
        if not self.connected:
            return False
        try:
            # Write with response: one payload every few seconds is nowhere
            # near a throughput problem, and the ack tells us the link is
            # genuinely alive rather than merely believed to be.
            await asyncio.wait_for(
                self._client.write_gatt_char(STATE_CHAR_UUID, payload, response=True),
                timeout=WRITE_TIMEOUT,
            )
            return True
        except (BleakError, asyncio.TimeoutError, OSError) as exc:
            log.warning("write failed: %s", exc)
            await self.close()
            return False

    async def close(self) -> None:
        client, self._client = self._client, None
        if client is None:
            return
        try:
            await asyncio.wait_for(client.disconnect(), timeout=DISCONNECT_TIMEOUT)
        except (BleakError, OSError, asyncio.TimeoutError):
            pass
