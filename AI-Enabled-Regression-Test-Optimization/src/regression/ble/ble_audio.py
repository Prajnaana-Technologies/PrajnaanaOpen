# Copyright (c) 2026 Prajnaana Technologies Pvt. Ltd.
# Original author: Dhanya Shree S
# SPDX-License-Identifier: MIT
#
# Licensed under the MIT License. See LICENSE in the project root.

import asyncio
import os
import time

import numpy as np

from regression.ble import telemetry


def _bleak():
    """Import bleak on first use, with a clear message if it is missing."""
    try:
        from bleak import BleakClient, BleakScanner
    except ImportError as exc:  # pragma: no cover - depends on the host
        raise RuntimeError(
            "This project needs the 'bleak' package to talk to the device: "
            "pip install -r requirements.txt"
        ) from exc

    return BleakClient, BleakScanner


# REAL UUIDs (from your device)
WRITE_CHAR_UUID = "12345678-1234-5678-1234-56789abcdef1"
NOTIFY_CHAR_UUID = "12345678-1234-5678-1234-56789abcdef2"

# Standard Battery Service. Confirmed present on the bench device by
# tools/ble_probe.py: 0x2A19 [read, notify].
BATTERY_LEVEL_UUID = "00002a19-0000-1000-8000-00805f9b34fb"

# What a characteristic must be able to do to carry processed audio back.
# A device publishing NOTIFY_CHAR_UUID without either property cannot
# notify anything, whatever else it says.
NOTIFY_PROPERTIES = frozenset(("notify", "indicate"))

# How many times one buffer is streamed per test.
STREAM_REPEATS = 1

# The device negotiates MTU 247 and answers in ~47 ms, so a write carries a
# full chunk and the next follows a few milliseconds behind it. Streaming
# then runs faster than realtime and the test waits on the device rather
# than on the host. Both values stay well inside what the link sustains: a
# larger chunk risks the MTU, a shorter gap risks the controller's queue.
MAX_CHUNK = 128          # bytes per write, capped well under the MTU
WRITE_PACING = 0.005     # seconds between writes

# How much audio one send_audio_stream call carries: 2000 int16 samples,
# 0.125 s at 16 kHz. The device answers a burst of this size and no more.
MAX_BURST_BYTES = 4000

# Instead of a fixed wait, stop once nothing new has arrived for this long.
QUIET_PERIOD = 0.25
MAX_DSP_WAIT = 2.5


# Advertised name must contain this. Change it if your board differs.
DEVICE_NAME_MATCH = "Zephyr"

# Pin the bench to one specific device instead of "first thing named
# Zephyr". Set by the dashboard's device picker; useful when several
# boards are powered on at once.
DEVICE_ADDRESS_ENV = "HA_DEVICE_ADDRESS"

# Per-packet logging. Off by default: a 10 s recording produces ~1300
# notifications, which buries everything else. Set HA_VERBOSE=1 when
# debugging the transport itself.
VERBOSE_ENV = "HA_VERBOSE"

SCAN_TIMEOUT = 10.0


class LoopbackProbeError(RuntimeError):
    """The device could not be asked whether it implements the loopback.

    Distinct from an answer of "no". Every False the capability probe
    returns turns a failing audio case into a skipped one, so a read that
    did not happen must not be reported as a feature that is not there:
    that is a run which proved nothing and said it passed.
    """


# ---------------- FIND DEVICE ----------------
async def find_device(timeout=SCAN_TIMEOUT):
    """Return the BLEDevice object, not its address.

    Connecting by address string fails on Windows: the WinRT backend needs the
    BLEDevice handle produced by the scan, and a bare address raises
    "Device with address ... was not found" even when the scan just saw it.
    """
    _, BleakScanner = _bleak()

    wanted = os.getenv(DEVICE_ADDRESS_ENV, "").strip()

    if wanted:
        device = await BleakScanner.find_device_by_address(wanted, timeout=timeout)

        if device is None:
            print("Device", wanted, "not found")
    else:
        device = await BleakScanner.find_device_by_filter(
            lambda d, ad: bool(d.name and DEVICE_NAME_MATCH in d.name),
            timeout=timeout,
        )

    if device:
        print("Found device:", device.name, device.address)

    return device


# ---------------- BLE DEVICE CLASS ----------------
class RealBLEDevice:

    def __init__(self):
        self.client = None
        self.processed_audio = bytearray()

        # None = not yet probed. Cached for the life of the object so a
        # session-scoped fixture pays for the probe once.
        self._loopback = None

        # None = not yet read. False once the device is known not to
        # expose the telemetry characteristic, so we stop retrying.
        self._telemetry_ok = None

        # The device counts link drops since boot and never resets, so the
        # raw number is useless as a threshold. These two turn it into drops
        # during THIS run that the harness did not cause.
        self._retry_baseline = None
        self._expected_disconnects = 0

        # Set when a stream starts / when its first byte comes back, so
        # round-trip latency can be measured without timing the whole
        # transfer.
        self._stream_started_at = None
        self._first_notify_at = None

        # Packets in the current stream, for the summary line.
        self._notify_packets = 0

    @property
    def is_connected(self):
        return self.client and self.client.is_connected

    # ---------------- CONNECT ----------------
    async def connect(self):

        if self.is_connected:
            return

        # Re-probe telemetry on every fresh link. The flag caches a
        # per-connection fact, not a permanent property of the device.
        self._telemetry_ok = None

        for attempt in range(5):

            device = await find_device()

            if device:
                try:
                    print(f"Connecting to {device.address} (attempt {attempt+1})")

                    BleakClient, _ = _bleak()

                    # Pass the BLEDevice, never the address string.
                    self.client = BleakClient(device)
                    await self.client.connect(timeout=10.0)

                    if self.client.is_connected:
                        print("Connected")

                        # Enable notifications. Firmware that does not
                        # implement the loopback has no such characteristic
                        # to subscribe to, and that is a capability gap, not
                        # a failed connection. supports_audio_loopback reads
                        # the GATT table and reports the gap; the link is
                        # fine, and every characteristic read still works.
                        try:
                            await self.client.start_notify(
                                NOTIFY_CHAR_UUID,
                                self.notification_handler
                            )
                        except Exception as exc:
                            print("Could not subscribe to {}: {}".format(
                                NOTIFY_CHAR_UUID, exc))

                        # stabilisation delay
                        await asyncio.sleep(2)
                        return

                except Exception as e:
                    print("Connect failed:", e)

            # Peripherals need a moment to resume advertising after a
            # dropped link; back off progressively rather than hammering.
            backoff = 2 + 2 * attempt
            print(f"Retry {attempt+1}/5 in {backoff}s...")
            await asyncio.sleep(backoff)

        raise Exception("Device not found after retries")

    # ---------------- DISCONNECT ----------------
    async def disconnect(self):
        if self.is_connected:
            try:
                await self.client.stop_notify(NOTIFY_CHAR_UUID)
            except Exception:
                pass
            await self.client.disconnect()

            # The device cannot tell a deliberate disconnect from a fault, so
            # the host has to. Every drop we asked for is subtracted, leaving
            # only the ones the device caused.
            self._expected_disconnects += 1

            print("Disconnected")

    def reset_link_counters(self):
        """Start a fresh measurement window for the retry metric.

        Called at the start of a run. Without it a long-lived device object
        keeps widening the window, and retry creeps up across runs exactly as
        the raw counter does.
        """
        self._retry_baseline = None
        self._expected_disconnects = 0

    # ---------------- STREAM AUDIO ----------------
    async def send_audio_stream(self, audio_data, params=None):

        try:
            if not self.is_connected:
                await self.connect()

            self.processed_audio = bytearray()

            # Ensure mono + PCM16
            if isinstance(audio_data, np.ndarray):
                audio = audio_data.flatten()
                audio = (audio * 32767).astype(np.int16)
                data = audio.tobytes()
            else:
                data = audio_data

            # One burst, 2000 int16 samples of it: the device's return
            # buffer is this size, so a longer write is not answered in full.
            # This truncates, it does not enlarge.
            #
            # Callers wanting more than 0.125 s send several bursts -- see
            # stream() in the generated suite. Handing a whole selection to
            # one call gets 0.125 s of it silently, which is how
            # HA_TEST_SECONDS comes to be ignored unless the caller loops.
            data = data[:MAX_BURST_BYTES]

            self._first_notify_at = None
            self._notify_packets = 0
            self._stream_started_at = time.monotonic()

            print("Streaming bytes:", len(data))

            # Use as much of the negotiated MTU as is safe. Falls back to
            # 20 bytes, the default-MTU payload, if it cannot be read.
            mtu = getattr(self.client, "mtu_size", 23) or 23
            CHUNK_SIZE = max(20, min(MAX_CHUNK, (mtu - 3))) & ~1

            # One pass (STREAM_REPEATS is 1). With a working loopback a
            # repeat sends the audio back that many times over.
            for _ in range(STREAM_REPEATS):

                for i in range(0, len(data), CHUNK_SIZE):

                    if not self.is_connected:
                        print("Disconnected mid-stream")
                        return None

                    chunk = data[i:i + CHUNK_SIZE]

                    try:
                        await self.client.write_gatt_char(
                            WRITE_CHAR_UUID,
                            chunk,
                            response=False
                        )
                    except Exception as e:
                        print("Write failed:", e)
                        return None

                    await asyncio.sleep(WRITE_PACING)

            # Wait only until the device stops sending, rather than a flat
            # 2.5 s. Round trip measures ~47 ms, so this usually returns
            # in well under half a second.
            waited = 0.0
            last = -1
            quiet = 0.0

            while waited < MAX_DSP_WAIT:
                await asyncio.sleep(0.05)
                waited += 0.05

                if len(self.processed_audio) == last:
                    quiet += 0.05
                    if last > 0 and quiet >= QUIET_PERIOD:
                        break
                else:
                    last = len(self.processed_audio)
                    quiet = 0.0

            if len(self.processed_audio) == 0:
                print("No DSP data received from device")
                return None

            print("DSP data received: {} bytes in {} packets".format(
                len(self.processed_audio), self._notify_packets))
            return bytes(self.processed_audio)

        except Exception as e:
            print("Streaming error:", e)
            return None

    # ---------------- RAW WRITE ----------------
    async def write(self, data):
        if not self.is_connected:
            await self.connect()

        await self.client.write_gatt_char(WRITE_CHAR_UUID, data, response=False)

        return len(data)

    # ---------------- NOTIFICATION HANDLER ----------------
    def notification_handler(self, sender, data):
        if self._first_notify_at is None:
            self._first_notify_at = time.monotonic()

        self._notify_packets += 1
        self.processed_audio.extend(data)

        if os.getenv(VERBOSE_ENV):
            print(f"DSP OUT: {len(data)} bytes")

    @property
    def last_stream_latency_ms(self):
        """Time from the first write to the first byte back, or None."""
        if self._stream_started_at is None or self._first_notify_at is None:
            return None

        return (self._first_notify_at - self._stream_started_at) * 1000.0

    # ---------------- CAPABILITY PROBE ----------------
    def loopback_characteristic(self):
        """The processed-audio notify characteristic, or None if absent.

        Read from the GATT table the link discovered. Raises
        LoopbackProbeError when the table cannot be read at all, which is
        not the same answer as "this firmware does not publish it".
        """
        try:
            services = self.client.services
        except Exception as exc:
            raise LoopbackProbeError(
                "could not read the GATT table, so whether this firmware "
                "implements the audio loopback is unknown: {}".format(exc)
            ) from exc

        if services is None:
            raise LoopbackProbeError(
                "the client reported no GATT table, so whether this "
                "firmware implements the audio loopback is unknown")

        # bleak's service collection indexes by UUID; anything else that
        # answers like one is walked instead.
        getter = getattr(services, "get_characteristic", None)

        if getter is not None:
            return getter(NOTIFY_CHAR_UUID)

        wanted = NOTIFY_CHAR_UUID.lower()

        for service in services:
            for characteristic in service.characteristics:
                if str(characteristic.uuid).lower() == wanted:
                    return characteristic

        return None

    async def supports_audio_loopback(self, force=False):
        """Does this firmware implement the audio loopback at all?

        Answered from the device's GATT table: either it publishes the
        processed-audio notify characteristic or it does not, and a board
        publishes what it publishes whatever its DSP is doing.

        That distinction is the only thing this decides, and it decides a
        skip against a failure, so it must not be read from anything the
        failure can break. Asking by writing audio and waiting for an echo
        does exactly that: a DSP hung from the very first write and a GATT
        write error during the probe both answer "this firmware has no
        audio loopback", every audio case then skips as a missing feature,
        and the run reports pass on firmware that is not working -- the
        defect the probe exists to prevent. A hung DSP cannot withdraw a
        characteristic.

        Raises LoopbackProbeError when the table cannot be read. Unknown is
        not "no": answering False on a failed read would put every audio
        case back behind a firmware-gap message on a board nobody asked.

        Cached for the life of the object -- the table cannot change
        without new firmware -- and force=True reads it again.
        """
        if self._loopback is not None and not force:
            return self._loopback

        if not self.is_connected:
            await self.connect()

        characteristic = self.loopback_characteristic()

        if characteristic is None:
            print("Audio loopback supported: False ({} is not in the GATT "
                  "table)".format(NOTIFY_CHAR_UUID))

            self._loopback = False

            return False

        # A characteristic that can neither notify nor indicate cannot
        # carry processed audio back, whatever its UUID says. Backends that
        # do not publish a property list leave this to presence alone.
        properties = getattr(characteristic, "properties", None)

        if properties is not None and not (
                NOTIFY_PROPERTIES & set(properties)):
            print("Audio loopback supported: False ({} is present but "
                  "cannot notify: {})".format(
                      NOTIFY_CHAR_UUID, ", ".join(sorted(properties))))

            self._loopback = False

            return False

        print("Audio loopback supported: True")

        self._loopback = True

        return True

    # ---------------- METRICS ----------------
    async def read_battery(self):
        """Battery level percent from the standard Battery Service, or None.

        Read independently of the telemetry service, so it still works
        against firmware that predates it.
        """
        if not self.is_connected:
            await self.connect()

        try:
            data = await self.client.read_gatt_char(BATTERY_LEVEL_UUID)
        except Exception as exc:
            print("Battery read failed:", exc)
            return None

        return int(data[0]) if data else None

    async def read_telemetry(self):
        """Parsed telemetry snapshot, or None if the device does not expose it.

        Firmware side: NRF_Firmware/src/ble_telemetry_service.c. A device
        without it still runs -- the scored metrics are reported as
        unmeasured rather than invented.
        """
        if self._telemetry_ok is False:
            return None

        if not self.is_connected:
            await self.connect()

        try:
            raw = await self.client.read_gatt_char(telemetry.TELEMETRY_CHAR_UUID)
        except Exception:
            if self._telemetry_ok is None:
                print("Telemetry characteristic not present; "
                      "power/memory/sync/retry will be reported unmeasured")
            self._telemetry_ok = False
            return None

        try:
            snapshot = telemetry.parse(raw)
        except telemetry.TelemetryError as exc:
            print("Telemetry unreadable:", exc)
            self._telemetry_ok = False
            return None

        self._telemetry_ok = True

        return snapshot

    async def read_build_id(self):
        """What the device says it is running, or None.

        Asking the board beats asking the operator: a typed label can be
        stale or wrong, and the determinism KPI compares runs sharing one.
        Two runs of genuinely different firmware labelled the same would be
        reported as non-determinism that nobody could reproduce.
        """
        if not self.is_connected:
            await self.connect()

        try:
            raw = await self.client.read_gatt_char(telemetry.BUILD_CHAR_UUID)
        except Exception:
            # Older firmware has no such characteristic. Not an error: the
            # caller falls back to whatever the operator supplied.
            return None

        if not raw:
            return None

        return bytes(raw).decode("utf-8", errors="replace").strip() or None

    # ---------------- PAIRING AND BONDING ----------------
    async def pair(self):
        """Bond with the device. True on success.

        The OS owns the bond, not this process: on Windows it survives the
        script exiting and shows up in Bluetooth settings. Always unpair in
        teardown, or the next run starts from a state the test did not set up.
        """
        if not self.is_connected:
            await self.connect()

        try:
            await self.client.pair()
            return True
        except Exception as exc:
            print("Pairing failed:", exc)
            return False

    async def unpair(self):
        """Remove the bond from the host side. True on success."""
        try:
            await self.client.unpair()
            return True
        except Exception as exc:
            print("Unpair failed:", exc)
            return False

    async def read_bond_count(self):
        """Bonds stored on the device, or None when the read is refused.

        Refusal is the interesting case: the characteristic requires an
        encrypted link, so None on an unpaired connection is the correct
        result and is what proves encryption is enforced.
        """
        if not self.is_connected:
            await self.connect()

        try:
            raw = await self.client.read_gatt_char(telemetry.SECURE_CHAR_UUID)
        except Exception as exc:
            print("Secure read refused:", str(exc)[:90])
            return None

        return int(raw[0]) if raw else None

    async def is_encrypted(self):
        """True when the device reports the link at security level 2 or above."""
        snapshot = await self.read_telemetry()

        return bool(snapshot and snapshot.get("encrypted"))

    # The metrics the risk engine scores. A metric the device does not report
    # is left out of the returned dict and named in "unmeasured" -- it is
    # never replaced with a literal. Do not add stub values here.
    RISK_METRICS = ("power", "memory", "sync", "retry")

    # Set to "1" to make a missing telemetry reading an error instead of a
    # degraded run. The hardware job in .github/workflows/regression.yml
    # sets it on both runs, as should anything producing a result that
    # will be quoted.
    REQUIRE_TELEMETRY_ENV = "HA_REQUIRE_TELEMETRY"

    # What the telemetry service on this board can actually deliver, and
    # therefore what HA_REQUIRE_TELEMETRY=1 may insist on. It is a subset of
    # RISK_METRICS, because no DK can satisfy all four: there is no current
    # sensor and no second device, so power and sync are reported unmeasured
    # by design (ble_telemetry_service.c sets current_ma to 0 and
    # sync_latency_us to HA_SYNC_UNKNOWN).
    REQUIRED_METRICS = ("memory", "retry")

    async def read_power(self):
        return (await self.read_metrics()).get("power")

    async def read_memory(self):
        return (await self.read_metrics()).get("memory")

    async def measure_sync_latency(self):
        return (await self.read_metrics()).get("sync")

    async def read_retry_count(self):
        return (await self.read_metrics()).get("retry")

    async def read_metrics(self):
        """The metrics the risk engine scores, measured where possible.

        Returns only what the device actually reported, plus "unmeasured":
        the names of the scored metrics it did not. Falls back per metric
        rather than all-or-nothing, so a device that reports stack high-water
        but has no current sensor still yields a real memory reading.

        Raises RuntimeError when HA_REQUIRE_TELEMETRY=1 and one of
        REQUIRED_METRICS is missing. Without that, firmware that silently
        stops publishing telemetry produces a run that looks clean.
        """
        metrics = {}

        snapshot = await self.read_telemetry()

        if snapshot:
            metrics.update(telemetry.to_metrics(snapshot))

            total = snapshot["reconnects"]

            # First reading of the window is the zero point.
            if self._retry_baseline is None:
                self._retry_baseline = total

            unexpected = (total - self._retry_baseline) - self._expected_disconnects

            metrics["retry"] = max(0, unexpected)
            metrics["retry_total"] = total

            metrics["stream_errors"] = snapshot["stream_errors"]

            if snapshot["mem_free"] is not None:
                metrics["mem_free"] = snapshot["mem_free"]

        battery = await self.read_battery()

        if battery is None and snapshot:
            battery = snapshot["battery"]

        if battery is not None:
            metrics["battery"] = battery

        unmeasured = [m for m in self.RISK_METRICS if m not in metrics]

        required = [m for m in self.REQUIRED_METRICS if m in unmeasured]

        if required and os.getenv(self.REQUIRE_TELEMETRY_ENV) == "1":
            raise RuntimeError(
                "{}=1 but the device did not report: {}. The telemetry "
                "service ({}) publishes these on any build that has it, so "
                "either it is absent from this image or the link dropped "
                "before it was read. Unset the variable to allow a degraded "
                "run. Metrics this bench cannot measure at all -- {} -- are "
                "not required.".format(
                    self.REQUIRE_TELEMETRY_ENV,
                    ", ".join(required),
                    telemetry.TELEMETRY_SERVICE_UUID,
                    ", ".join(m for m in self.RISK_METRICS
                              if m not in self.REQUIRED_METRICS),
                )
            )

        metrics["unmeasured"] = unmeasured

        return metrics
