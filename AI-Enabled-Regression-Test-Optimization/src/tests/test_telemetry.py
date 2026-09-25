# Copyright (c) 2026 Prajnaana Technologies Pvt. Ltd.
# Original author: Dhanya Shree S
# SPDX-License-Identifier: MIT
#
# Licensed under the MIT License. See LICENSE in the project root.

"""Tests for the telemetry wire format.

These guard the boundary between NRF_Firmware/src/ble_telemetry_service.h
and regression/ble/telemetry.py. A silent mismatch there produces
plausible-looking but wrong metrics, which is far worse than a visible
failure.

No hardware needed: the device's bytes are constructed here.
"""

import struct

import pytest

from regression.ble import telemetry


def snapshot_bytes(version=3, battery=87, current_ma=42, sync_us=15000,
                   reconnects=3, stream_errors=0, mem_used=3400,
                   mem_free=696, bond_count=0, sec_level=1):
    return struct.pack(
        telemetry.SNAPSHOT_FORMAT,
        version, battery, current_ma, sync_us,
        reconnects, stream_errors, mem_used, mem_free,
        bond_count, sec_level,
    )


# --------------------------------------------------------------------------
# Layout must match the firmware struct
# --------------------------------------------------------------------------

def test_snapshot_fits_one_notification():
    """20 bytes is the usable ATT payload at the default MTU of 23."""
    assert telemetry.SNAPSHOT_SIZE <= 20


def test_snapshot_size_is_what_the_firmware_packs():
    assert telemetry.SNAPSHOT_SIZE == 20


def test_format_is_little_endian():
    """ARM is little-endian and the struct is __packed; no native alignment."""
    assert telemetry.SNAPSHOT_FORMAT.startswith("<")


# --------------------------------------------------------------------------
# Parsing
# --------------------------------------------------------------------------

def test_parses_a_healthy_snapshot():
    parsed = telemetry.parse(snapshot_bytes())

    assert parsed["battery"] == 87
    assert parsed["current_ma"] == 42
    assert parsed["sync_ms"] == 15.0
    assert parsed["reconnects"] == 3
    assert parsed["mem_used"] == 3400
    assert parsed["memory_kb"] == 3.32


def test_microseconds_become_milliseconds():
    """The risk engine's sync threshold is 50 ms, so units must be right."""
    parsed = telemetry.parse(snapshot_bytes(sync_us=20500))

    assert parsed["sync_ms"] == 20.5


def test_unknown_battery_is_none_not_255():
    parsed = telemetry.parse(snapshot_bytes(battery=telemetry.BATTERY_UNKNOWN))

    assert parsed["battery"] is None


def test_missing_current_sensor_is_none_not_zero():
    """0 mA would look like a wonderfully efficient device."""
    parsed = telemetry.parse(snapshot_bytes(current_ma=0))

    assert parsed["current_ma"] is None


def test_unavailable_memory_is_none_not_zero():
    """Zero means the measurement is off, not that nothing is in use."""
    parsed = telemetry.parse(snapshot_bytes(mem_used=0, mem_free=0))

    assert parsed["mem_used"] is None
    assert parsed["memory_kb"] is None


# --------------------------------------------------------------------------
# Rejecting bad input
# --------------------------------------------------------------------------

def test_future_version_is_rejected():
    """Better to fail loudly than to misread relocated fields."""
    with pytest.raises(telemetry.TelemetryError, match="version"):
        telemetry.parse(snapshot_bytes(version=4))


def test_the_v2_wire_format_is_rejected():
    """v2 is 18 bytes; v3 appends bond_count and sec_level to make 20."""
    with pytest.raises(telemetry.TelemetryError, match="version"):
        telemetry.parse(snapshot_bytes(version=2))


def test_truncated_payload_is_rejected():
    with pytest.raises(telemetry.TelemetryError, match="too short"):
        telemetry.parse(snapshot_bytes()[:10])


def test_none_is_rejected():
    with pytest.raises(telemetry.TelemetryError):
        telemetry.parse(None)


def test_longer_payload_is_tolerated():
    """A future firmware appending fields must not break this host."""
    parsed = telemetry.parse(snapshot_bytes() + b"\x00\x00")

    assert parsed["battery"] == 87


# --------------------------------------------------------------------------
# Mapping onto the risk engine's four metrics
# --------------------------------------------------------------------------

def test_metrics_use_measured_values():
    metrics = telemetry.to_metrics(telemetry.parse(snapshot_bytes()))

    assert metrics["power"] == 42
    assert metrics["memory"] == 3.32
    assert metrics["sync"] == 15.0
    assert metrics["retry"] == 3


def test_unmeasured_metrics_are_omitted_so_the_caller_can_fall_back():
    parsed = telemetry.parse(snapshot_bytes(current_ma=0, mem_used=0, mem_free=0))

    metrics = telemetry.to_metrics(parsed)

    assert "power" not in metrics
    assert "memory" not in metrics
    assert metrics["sync"] == 15.0
    assert metrics["retry"] == 3


def test_measured_metrics_reach_the_risk_engine():
    """End to end: device bytes -> metrics -> risk score."""
    from regression.change_detection.risk_engine import (
    METRIC_THRESHOLDS,
    calculate_risk_score,
)

    # memory is work-queue stack against a 4 KiB stack, so a healthy
    # reading is a few KiB, not tens of them.
    healthy = telemetry.to_metrics(telemetry.parse(snapshot_bytes(
        current_ma=38, sync_us=12000, reconnects=1,
        mem_used=3400, mem_free=696,
    )))
    assert healthy["memory"] < 3.76
    assert calculate_risk_score(healthy) == 0

    # Every metric past its limit, so the score is the sum of all weights.
    # Each value is chosen to sit past its entry in METRIC_THRESHOLDS -- sync
    # at 62 ms against the 50 ms REQ-SYN-001 states -- and the loop below
    # reads the limits from that table rather than from frozen literals.
    degraded = telemetry.to_metrics(telemetry.parse(snapshot_bytes(
        current_ma=78, sync_us=62000, reconnects=7,
        mem_used=3960, mem_free=136,
    )))

    for name, limit in METRIC_THRESHOLDS.items():
        assert degraded[name] > limit, name

    assert calculate_risk_score(degraded) == 6


# --------------------------------------------------------------------------
# An unmeasured metric must not look like a healthy one
# --------------------------------------------------------------------------

def test_sync_sentinel_is_none_not_65_milliseconds():
    """0xFFFF means "no second device", not a catastrophic 65 ms error."""
    parsed = telemetry.parse(snapshot_bytes(sync_us=telemetry.SYNC_UNKNOWN))

    assert parsed["sync_ms"] is None


def test_zero_sync_is_none_not_a_perfect_reading():
    """Firmware predating the sentinel leaves the field at 0."""
    parsed = telemetry.parse(snapshot_bytes(sync_us=0))

    assert parsed["sync_ms"] is None


def test_unmeasured_sync_is_omitted_from_metrics():
    parsed = telemetry.parse(snapshot_bytes(sync_us=telemetry.SYNC_UNKNOWN))

    assert "sync" not in telemetry.to_metrics(parsed)


def test_measured_sync_still_reaches_metrics():
    parsed = telemetry.parse(snapshot_bytes(sync_us=15000))

    assert telemetry.to_metrics(parsed)["sync"] == 15.0


def test_unknown_metric_scores_as_risk_not_as_health():
    """A device nobody can read must not score a clean zero."""
    from regression.change_detection import risk_engine

    assert risk_engine.calculate_risk_score({}) == sum(
        risk_engine.METRIC_WEIGHTS.values()
    )


def test_declared_unmeasured_outranks_a_present_value():
    """read_metrics() names what it could not measure; honour that."""
    from regression.change_detection import risk_engine

    metrics = {"power": 10, "memory": 3.0, "sync": 1, "retry": 0,
               "unmeasured": ["power"]}

    breached, unknown, within = risk_engine.classify_metrics(metrics)

    assert unknown == ["power"]
    assert breached == []
    assert sorted(within) == ["memory", "retry", "sync"]


def test_unknown_and_breached_are_reported_apart():
    """The console has to distinguish a bad device from an unreadable one."""
    from regression.change_detection import risk_engine

    breached, unknown, within = risk_engine.classify_metrics(
        {"power": 99, "retry": 0}
    )

    assert breached == ["power"]
    assert sorted(unknown) == ["memory", "sync"]
    assert within == ["retry"]


def test_risk_tests_are_deterministic():
    """Two runs of one build must not generate different suites."""
    from regression.change_detection import risk_engine

    metrics = {"power": 99, "memory": 999, "sync": 99, "retry": 99}

    first = risk_engine.detect_risk_from_metrics(metrics)

    assert first == sorted(first)
    assert first == risk_engine.detect_risk_from_metrics(dict(metrics))


def test_an_unreadable_device_runs_the_full_suite():
    """Not knowing is not the same as knowing it is fine."""
    from regression.change_detection import risk_engine

    prioritized, score = risk_engine.select_regression_slice(
        {"unmeasured": ["power", "memory", "sync", "retry"]}
    )

    assert prioritized is None
    assert score >= risk_engine.FULL_REGRESSION_SCORE


def test_telemetry_is_reprobed_after_a_reconnect(monkeypatch):
    """The absence flag caches a per-connection fact, not a device property.

    Driven through connect() itself, with the transport stubbed -- reading
    the source for the assignment proves only that the line exists.
    """
    import asyncio
    import types

    from regression.ble import ble_audio

    class FakeClient:
        def __init__(self, device):
            self.is_connected = False

        async def connect(self, timeout=None):
            self.is_connected = True

        async def start_notify(self, uuid, handler):
            pass

    async def fake_find_device(timeout=None):
        return types.SimpleNamespace(address="AA:BB:CC:DD:EE:FF",
                                     name="Zephyr_Earbuds")

    async def no_delay(seconds):
        return None

    monkeypatch.setattr(ble_audio, "find_device", fake_find_device)
    monkeypatch.setattr(ble_audio, "_bleak", lambda: (FakeClient, None))

    # connect() waits 2 s for the link to settle; only sleep is used here.
    monkeypatch.setattr(ble_audio, "asyncio", types.SimpleNamespace(sleep=no_delay))

    device = ble_audio.RealBLEDevice()

    # A read failed on the previous link.
    device._telemetry_ok = False

    asyncio.run(device.connect())

    assert device.is_connected
    assert device._telemetry_ok is None, (
        "connect() must clear the telemetry flag, or a single failed read "
        "disables telemetry for the whole session"
    )


# --------------------------------------------------------------------------
# Build identity
# --------------------------------------------------------------------------

def test_the_build_characteristic_has_its_own_uuid():
    """Distinct from the snapshot and the encrypted one."""
    uuids = {
        telemetry.TELEMETRY_CHAR_UUID,
        telemetry.SECURE_CHAR_UUID,
        telemetry.BUILD_CHAR_UUID,
    }

    assert len(uuids) == 3


def test_the_build_characteristic_is_in_the_telemetry_service():
    prefix = telemetry.TELEMETRY_SERVICE_UUID[:-1]

    assert telemetry.BUILD_CHAR_UUID.startswith(prefix)


def test_an_absent_build_characteristic_is_not_an_error():
    """Older firmware simply has no such characteristic."""
    import asyncio

    from regression.ble.ble_audio import RealBLEDevice

    device = RealBLEDevice.__new__(RealBLEDevice)

    class Refusing(object):
        async def read_gatt_char(self, uuid):
            raise RuntimeError("characteristic not found")

    device.client = Refusing()
    type(device).is_connected = property(lambda self: True)

    try:
        assert asyncio.run(RealBLEDevice.read_build_id(device)) is None
    finally:
        del type(device).is_connected


def test_a_reported_build_id_is_decoded_and_stripped():
    import asyncio

    from regression.ble.ble_audio import RealBLEDevice

    device = RealBLEDevice.__new__(RealBLEDevice)

    class Reporting(object):
        async def read_gatt_char(self, uuid):
            return b"99b1e43e-dirty\n"

    device.client = Reporting()
    type(device).is_connected = property(lambda self: True)

    try:
        assert asyncio.run(RealBLEDevice.read_build_id(device)) == "99b1e43e-dirty"
    finally:
        del type(device).is_connected


# --------------------------------------------------------------------------
# retry counts unexpected drops during one run, not the since-boot total
# --------------------------------------------------------------------------

def _device_reporting(reconnects_sequence):
    """A RealBLEDevice whose telemetry returns the given reconnect counts."""
    from regression.ble.ble_audio import RealBLEDevice

    device = RealBLEDevice.__new__(RealBLEDevice)
    device._telemetry_ok = True
    device._retry_baseline = None
    device._expected_disconnects = 0

    counts = list(reconnects_sequence)

    async def read_telemetry():
        return telemetry.parse(snapshot_bytes(reconnects=counts.pop(0)))

    async def read_battery():
        return None

    device.read_telemetry = read_telemetry
    device.read_battery = read_battery

    return device


def test_a_fresh_window_starts_at_zero_however_high_the_counter():
    """A board reconnected 40 times must not fail forever: the window counts
    the drops in this run, not the device's lifetime total."""
    import asyncio

    from regression.ble.ble_audio import RealBLEDevice

    device = _device_reporting([40])

    metrics = asyncio.run(RealBLEDevice.read_metrics(device))

    assert metrics["retry"] == 0
    assert metrics["retry_total"] == 40


def test_a_drop_the_harness_caused_does_not_count():
    import asyncio

    from regression.ble.ble_audio import RealBLEDevice

    device = _device_reporting([10, 11])

    asyncio.run(RealBLEDevice.read_metrics(device))

    # The harness disconnected once, so the device's extra reconnect is ours.
    device._expected_disconnects = 1

    assert asyncio.run(RealBLEDevice.read_metrics(device))["retry"] == 0


def test_a_drop_the_device_caused_does_count():
    import asyncio

    from regression.ble.ble_audio import RealBLEDevice

    device = _device_reporting([10, 12])

    asyncio.run(RealBLEDevice.read_metrics(device))

    device._expected_disconnects = 1   # we caused one, the device caused one

    assert asyncio.run(RealBLEDevice.read_metrics(device))["retry"] == 1


def test_retry_never_goes_negative():
    """More expected disconnects than observed drops is not a healthy -2."""
    import asyncio

    from regression.ble.ble_audio import RealBLEDevice

    device = _device_reporting([10, 10])

    asyncio.run(RealBLEDevice.read_metrics(device))

    device._expected_disconnects = 3

    assert asyncio.run(RealBLEDevice.read_metrics(device))["retry"] == 0


def test_resetting_the_window_rebaselines():
    import asyncio

    from regression.ble.ble_audio import RealBLEDevice

    device = _device_reporting([10, 25])

    asyncio.run(RealBLEDevice.read_metrics(device))

    RealBLEDevice.reset_link_counters(device)

    assert asyncio.run(RealBLEDevice.read_metrics(device))["retry"] == 0


# --------------------------------------------------------------------------
# HA_REQUIRE_TELEMETRY: the gate insists on what this bench can measure
#
# The gate insists on ble_audio.REQUIRED_METRICS, not on all of RISK_METRICS,
# which no DK can satisfy -- the board has no current sensor and no second
# device, so power and sync are unmeasured by design. A gate on the wider set
# raises on every hardware run and stops it before planning, which leaves CI
# no choice but to turn the gate off. These pin the narrowed set so it cannot
# widen.
# --------------------------------------------------------------------------

def _device_reporting_snapshot(has_telemetry=True, **snapshot_fields):
    """A RealBLEDevice whose telemetry is the given snapshot, or absent."""
    from regression.ble.ble_audio import RealBLEDevice

    device = RealBLEDevice.__new__(RealBLEDevice)
    device._telemetry_ok = has_telemetry
    device._retry_baseline = None
    device._expected_disconnects = 0

    async def read_telemetry():
        if not has_telemetry:
            return None

        return telemetry.parse(snapshot_bytes(**snapshot_fields))

    async def read_battery():
        return None

    device.read_telemetry = read_telemetry
    device.read_battery = read_battery

    return device


def test_the_gate_passes_when_only_power_and_sync_are_unmeasured(monkeypatch):
    """The DK case: no current sensor, no second device, everything else read."""
    import asyncio

    from regression.ble.ble_audio import RealBLEDevice

    monkeypatch.setenv(RealBLEDevice.REQUIRE_TELEMETRY_ENV, "1")

    device = _device_reporting_snapshot(
        current_ma=0, sync_us=telemetry.SYNC_UNKNOWN
    )

    metrics = asyncio.run(RealBLEDevice.read_metrics(device))

    assert sorted(metrics["unmeasured"]) == ["power", "sync"]
    assert metrics["memory"] == 3.32
    assert metrics["retry"] == 0


def test_the_gate_raises_when_memory_is_missing(monkeypatch):
    """Firmware that stopped publishing its stack figure must not look clean."""
    import asyncio

    from regression.ble.ble_audio import RealBLEDevice

    monkeypatch.setenv(RealBLEDevice.REQUIRE_TELEMETRY_ENV, "1")

    device = _device_reporting_snapshot(mem_used=0, mem_free=0)

    with pytest.raises(RuntimeError) as raised:
        asyncio.run(RealBLEDevice.read_metrics(device))

    # Only the metric this bench can measure is demanded; power and sync are
    # named as not required, not as failures.
    assert "did not report: memory." in str(raised.value)


def test_the_gate_raises_when_there_is_no_telemetry_service(monkeypatch):
    """An image built without the service is the case the gate exists for."""
    import asyncio

    from regression.ble.ble_audio import RealBLEDevice

    monkeypatch.setenv(RealBLEDevice.REQUIRE_TELEMETRY_ENV, "1")

    device = _device_reporting_snapshot(has_telemetry=False)

    with pytest.raises(RuntimeError) as raised:
        asyncio.run(RealBLEDevice.read_metrics(device))

    assert "did not report: memory, retry." in str(raised.value)
    assert telemetry.TELEMETRY_SERVICE_UUID in str(raised.value)


def test_without_the_variable_a_missing_service_is_a_degraded_run():
    """Unset is the default everywhere except the bench job: degrade, do not stop."""
    import asyncio

    from regression.ble.ble_audio import RealBLEDevice

    device = _device_reporting_snapshot(has_telemetry=False)

    metrics = asyncio.run(RealBLEDevice.read_metrics(device))

    assert sorted(metrics["unmeasured"]) == ["memory", "power", "retry", "sync"]


def test_the_required_set_is_a_subset_of_the_scored_set():
    """Requiring a metric the risk engine does not score would be meaningless."""
    from regression.ble.ble_audio import RealBLEDevice

    assert set(RealBLEDevice.REQUIRED_METRICS) <= set(RealBLEDevice.RISK_METRICS)
    assert "power" not in RealBLEDevice.REQUIRED_METRICS
    assert "sync" not in RealBLEDevice.REQUIRED_METRICS


def test_the_hardware_job_turns_the_gate_on():
    """ble_audio asks for the telemetry gate on anything whose result will
    be quoted; the hardware job is what makes that true of CI.

    Both hardware runs produce quoted results, so both set the same two
    gates: HA_REQUIRE_APPROVAL and HA_REQUIRE_TELEMETRY. The workflow is
    not shipped in the packaged install, hence the skip.
    """
    import pathlib

    workflow = (pathlib.Path(__file__).resolve().parents[2]
                / ".github" / "workflows" / "regression.yml")

    if not workflow.exists():
        pytest.skip("not run from a checkout: .github/workflows is absent")

    text = workflow.read_text(encoding="utf-8")

    # The regression run and the determinism repeat.
    assert text.count('HA_REQUIRE_TELEMETRY: "1"') == 2
    assert (text.count('HA_REQUIRE_TELEMETRY: "1"')
            == text.count('HA_REQUIRE_APPROVAL: "1"'))

    # No comment in the workflow may say the gate is left off pending a
    # narrowing.
    assert "deliberately NOT set" not in text
    assert "Not HA_REQUIRE_TELEMETRY" not in text
