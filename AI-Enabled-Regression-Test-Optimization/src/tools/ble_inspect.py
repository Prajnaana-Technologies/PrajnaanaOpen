# Copyright (c) 2026 Prajnaana Technologies Pvt. Ltd.
# Original author: Dhanya Shree S
# SPDX-License-Identifier: MIT
#
# Licensed under the MIT License. See LICENSE in the project root.

"""Inspect any BLE device -- what it advertises, and what it exposes.

    python tools/ble_inspect.py --list                  # passive scan, no connecting
    python tools/ble_inspect.py --address AA:BB:...     # connect and enumerate

--list only listens for advertisements. Nothing is contacted, nothing is
disturbed.

--address connects. Only point it at hardware you own: most earbuds accept a
single connection at a time, so connecting to someone else's will drop them
from their phone.

Reads the things any well-behaved BLE device should expose:
  0x180F  Battery Service     -> charge level
  0x180A  Device Information  -> maker, model, firmware version
plus the full GATT table.
"""

import argparse
import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _bleak():
    """Import bleak on first use, with a clear message if it is missing.

    The same lazy import ble_audio.py uses, for the same reason. Importing
    it at module level made --help crash on a host without bleak, which is
    the host most likely to be reading the help.
    """
    try:
        from bleak import BleakClient, BleakScanner
    except ImportError as exc:  # pragma: no cover - depends on the host
        raise RuntimeError(
            "This project needs the 'bleak' package to talk to the device: "
            "pip install -r requirements.txt"
        ) from exc

    return BleakClient, BleakScanner


BATTERY_LEVEL = "00002a19-0000-1000-8000-00805f9b34fb"

# Device Information Service characteristics, all optional.
DEVICE_INFO = {
    "00002a29-0000-1000-8000-00805f9b34fb": "Manufacturer",
    "00002a24-0000-1000-8000-00805f9b34fb": "Model",
    "00002a25-0000-1000-8000-00805f9b34fb": "Serial",
    "00002a26-0000-1000-8000-00805f9b34fb": "Firmware",
    "00002a27-0000-1000-8000-00805f9b34fb": "Hardware",
    "00002a28-0000-1000-8000-00805f9b34fb": "Software",
}

WELL_KNOWN = {
    "1800": "Generic Access",
    "1801": "Generic Attribute",
    "180a": "Device Information",
    "180f": "Battery Service",
    "1812": "HID",
    "fd6f": "Exposure Notification",
    "fe2c": "Google Fast Pair",
}


def service_name(uuid):
    short = uuid[4:8].lower()
    return WELL_KNOWN.get(short, "")


async def scan(timeout):
    _, BleakScanner = _bleak()

    print("Scanning {:.0f}s (passive -- nothing is contacted)...\n".format(timeout))

    found = await BleakScanner.discover(timeout=timeout, return_adv=True)

    rows = []
    for address, (device, adv) in found.items():
        rows.append((device.name or "", address, adv.rssi,
                     len(adv.service_uuids or [])))

    rows.sort(key=lambda r: (r[0] == "", r[0].lower()))

    print("{:<26} {:<20} {:>5}  {}".format("NAME", "ADDRESS", "RSSI", "SERVICES"))
    print("-" * 68)

    for name, address, rssi, services in rows:
        print("{:<26} {:<20} {:>5}  {}".format(
            name or "(unnamed)", address, rssi, services or ""))

    named = sum(1 for r in rows if r[0])
    print("\n{} devices, {} with a name".format(len(rows), named))
    print("\nTo inspect one:  python tools/ble_inspect.py --address <ADDRESS>")
    print("Only do that for hardware you own.")


async def read_text(client, uuid):
    try:
        raw = await client.read_gatt_char(uuid)
        return bytes(raw).decode("utf-8", "replace").strip("\x00").strip()
    except Exception as exc:
        return "unreadable ({})".format(type(exc).__name__)


async def inspect(address, timeout):
    BleakClient, BleakScanner = _bleak()

    print("Looking for {}...".format(address))

    device = await BleakScanner.find_device_by_address(address, timeout=timeout)

    if device is None:
        raise SystemExit("not found -- is it on, in range, and advertising?")

    print("Found {}, connecting...".format(device.name or "(unnamed)"))

    try:
        client = BleakClient(device)
        await client.connect(timeout=15.0)
    except Exception as exc:
        raise SystemExit("connect failed: {}: {}\n"
                         "Often means it is already connected to a phone."
                         .format(type(exc).__name__, exc))

    try:
        print("Connected. MTU = {}\n".format(getattr(client, "mtu_size", "?")))

        # ---- GATT table ----
        print("--- GATT TABLE ---")
        services = 0
        characteristics = 0

        for service in client.services:
            services += 1
            label = service_name(service.uuid)
            print("Service {} {}".format(service.uuid, "(" + label + ")" if label else ""))

            for char in service.characteristics:
                characteristics += 1
                print("    {}  [{}]".format(char.uuid, ", ".join(char.properties)))

        print("\n{} services, {} characteristics".format(services, characteristics))

        # ---- Battery ----
        print("\n--- BATTERY ---")
        try:
            raw = await client.read_gatt_char(BATTERY_LEVEL)
            print("  {}%".format(int(raw[0])))
        except Exception as exc:
            print("  not available ({})".format(type(exc).__name__))

        # ---- Device Information ----
        print("\n--- DEVICE INFORMATION ---")
        any_info = False

        for uuid, label in DEVICE_INFO.items():
            present = any(c.uuid.lower() == uuid
                          for s in client.services for c in s.characteristics)
            if present:
                any_info = True
                print("  {:<13} {}".format(label + ":", await read_text(client, uuid)))

        if not any_info:
            print("  device exposes no Device Information service")

    finally:
        await client.disconnect()
        print("\nDisconnected.")


def main():
    parser = argparse.ArgumentParser(description="Inspect a BLE device")
    parser.add_argument("--list", action="store_true", help="passive scan only")
    parser.add_argument("--address", help="device to connect to and inspect")
    parser.add_argument("--timeout", type=float, default=10.0, help="scan seconds")

    args = parser.parse_args()

    if args.address:
        asyncio.run(inspect(args.address, args.timeout))
    else:
        asyncio.run(scan(args.timeout))


if __name__ == "__main__":
    main()
