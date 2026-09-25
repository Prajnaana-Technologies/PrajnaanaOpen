# Copyright (c) 2026 Prajnaana Technologies Pvt. Ltd.
# Original author: Dhanya Shree S
# SPDX-License-Identifier: MIT
#
# Licensed under the MIT License. See LICENSE in the project root.

"""Wire format for the device telemetry characteristic.

Mirrors ``struct ha_telemetry`` in NRF_Firmware/src/ble_telemetry_service.h.
Kept in its own module so the parsing is testable without a device attached --
a mismatch between the two sides is exactly the kind of bug that is miserable
to diagnose over BLE.

    Service  12345678-1234-5678-1234-56789abcdee0
      dee1   snapshot, read + notify, 20 bytes little-endian
      dee2   bond count, read, encryption required
      dee3   build id, read, no pairing needed
"""

import struct

TELEMETRY_SERVICE_UUID = "12345678-1234-5678-1234-56789abcdee0"
TELEMETRY_CHAR_UUID = "12345678-1234-5678-1234-56789abcdee1"

# Reading this requires an encrypted link. It is what lets a pairing test
# prove an effect rather than a return code: unpaired reads are refused
# with Insufficient Authentication, paired reads return the bond count.
SECURE_CHAR_UUID = "12345678-1234-5678-1234-56789abcdee2"

# The build the device is running, set at compile time from git. Read
# without pairing, because a run has to be labelled before it does
# anything else.
BUILD_CHAR_UUID = "12345678-1234-5678-1234-56789abcdee3"

# uint8 version, uint8 battery_pct, uint16 current_ma, uint16 sync_latency_us,
# uint16 reconnects, uint16 stream_errors, uint32 mem_used, uint32 mem_free,
# uint8 bond_count, uint8 sec_level
SNAPSHOT_FORMAT = "<BBHHHHIIBB"
SNAPSHOT_SIZE = struct.calcsize(SNAPSHOT_FORMAT)

# v2 renamed heap_used/heap_free to mem_used/mem_free, so a host expecting
# heap bytes refuses a device reporting stack bytes instead of silently
# mislabelling them. v3 appended bond_count and sec_level, taking the
# snapshot from 18 to 20 bytes, which is why a v2 payload is too short.
SUPPORTED_VERSION = 3

BATTERY_UNKNOWN = 0xFF

# Firmware sentinel for "no binaural sync measurement" (HA_SYNC_UNKNOWN).
# Zero counts as unmeasured too: a sync error of exactly 0 us is not physically
# plausible, and older firmware left the field at 0 rather than at the
# sentinel. Same reasoning as current_ma -- see the tests.
SYNC_UNKNOWN = 0xFFFF


class TelemetryError(ValueError):
    """Raised when the device sends something this host cannot interpret."""


def parse(data):
    """Turn the raw characteristic value into a dict.

    Returns keys: version, battery, current_ma, sync_ms, reconnects,
    stream_errors, mem_used, mem_free, memory_kb, bond_count, sec_level and
    encrypted. Values the firmware reports as unavailable come back as None
    rather than as a misleading zero.

    mem_* is system work queue stack, not heap -- see the firmware header.
    """
    if data is None:
        raise TelemetryError("no telemetry data")

    data = bytes(data)

    if len(data) < SNAPSHOT_SIZE:
        raise TelemetryError(
            "telemetry too short: got {} bytes, expected {}".format(
                len(data), SNAPSHOT_SIZE
            )
        )

    fields = struct.unpack(SNAPSHOT_FORMAT, data[:SNAPSHOT_SIZE])

    (version, battery, current_ma, sync_us,
     reconnects, stream_errors, mem_used, mem_free,
     bond_count, sec_level) = fields

    if version != SUPPORTED_VERSION:
        raise TelemetryError(
            "unsupported telemetry version {} (this host understands {})".format(
                version, SUPPORTED_VERSION
            )
        )

    # The firmware signals "not measured" with a sentinel rather than a zero,
    # so a missing sensor never looks like a healthy reading of 0.
    mem_available = (mem_used or mem_free) > 0
    sync_available = sync_us not in (0, SYNC_UNKNOWN)

    return {
        "version": version,
        "battery": None if battery == BATTERY_UNKNOWN else battery,
        "current_ma": current_ma or None,
        "sync_ms": (sync_us / 1000.0) if sync_available else None,
        "reconnects": reconnects,
        "stream_errors": stream_errors,
        "mem_used": mem_used if mem_available else None,
        "mem_free": mem_free if mem_available else None,
        "memory_kb": round(mem_used / 1024.0, 2) if mem_available else None,
        "bond_count": bond_count,
        "sec_level": sec_level,
        "encrypted": sec_level >= 2,
    }


def to_metrics(snapshot):
    """Map a parsed snapshot onto the four keys the risk engine scores.

    Only includes a key when the device actually measured it, so the caller
    can fall back per metric instead of all-or-nothing. An absent key means
    "not measured" -- never substitute a value for it, or the risk engine
    scores a constant and every build looks alike.
    """
    metrics = {}

    if snapshot.get("current_ma") is not None:
        metrics["power"] = snapshot["current_ma"]

    if snapshot.get("memory_kb") is not None:
        metrics["memory"] = snapshot["memory_kb"]

    if snapshot.get("sync_ms") is not None:
        metrics["sync"] = snapshot["sync_ms"]

    # The raw cumulative count. RealBLEDevice.read_metrics turns this into
    # "retry": drops during this run that the harness did not cause.
    metrics["retry"] = snapshot["reconnects"]

    return metrics
