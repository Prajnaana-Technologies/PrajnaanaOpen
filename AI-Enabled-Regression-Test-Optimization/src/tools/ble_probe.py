# Copyright (c) 2026 Prajnaana Technologies Pvt. Ltd.
# Original author: Dhanya Shree S
# SPDX-License-Identifier: MIT
#
# Licensed under the MIT License. See LICENSE in the project root.

"""Diagnose "No DSP data received from device".

    python tools/ble_probe.py
    python tools/ble_probe.py --listen 8

The regression suite takes ~100 seconds to tell you the device sent nothing
back. This tells you why, in about 30, and it does not give up after one
theory. It:

  1. dumps the GATT table and flags the characteristics the suite expects
  2. subscribes to EVERY notify-capable characteristic, not just the one
     configured -- if the firmware answers on a different handle, this finds it
  3. writes a short audio burst, first without response, then with response --
     if the characteristic does not support write-without-response the suite's
     writes are being silently dropped
  4. reports exactly which combination produced bytes back

Whatever line says "GOT n BYTES" tells you the UUID and write mode to put in
regression/ble/ble_audio.py.
"""

import argparse
import asyncio
import collections

import os
import sys

# Running this file directly puts its own directory on sys.path, not the repo
# root, so "import regression" fails. Fix that before importing anything local.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np

from regression.ble.ble_audio import (
    NOTIFY_CHAR_UUID,
    WRITE_CHAR_UUID,
    find_device,
)


def _bleak_client():
    """Import bleak on first use, with a clear message if it is missing.

    The same lazy import ble_audio.py uses, for the same reason. Importing
    it at module level made --help crash on a host without bleak, which is
    the host most likely to be reading the help.
    """
    try:
        from bleak import BleakClient
    except ImportError as exc:  # pragma: no cover - depends on the host
        raise RuntimeError(
            "This project needs the 'bleak' package to talk to the device: "
            "pip install -r requirements.txt"
        ) from exc

    return BleakClient

BURST_BYTES = 400        # short: we only need to know if anything answers
CHUNK = 20
LISTEN_S = 4.0


def make_burst(n_bytes=BURST_BYTES):
    n = n_bytes // 2
    t = np.linspace(0, 1, n, dtype=np.float32)
    tone = 0.6 * np.sin(2 * np.pi * 440 * t)
    return (tone * 32767).astype(np.int16).tobytes()


async def probe(listen_s=LISTEN_S):
    BleakClient = _bleak_client()

    device = await find_device()

    if not device:
        print("No matching device found. Is it powered on and advertising?")
        return 1

    print("Connecting to {} ({})".format(device.name, device.address))

    async with BleakClient(device) as client:
        print("Connected. MTU =", getattr(client, "mtu_size", "unknown"))

        # ---------------- 1. GATT table ----------------
        writable = []
        notifiable = []

        print("\n--- GATT TABLE ---")
        for service in client.services:
            print("Service {}".format(service.uuid))
            for char in service.characteristics:
                props = ", ".join(char.properties)
                print("   {}  [{}]".format(char.uuid, props))

                if {"write", "write-without-response"} & set(char.properties):
                    writable.append(char)
                if "notify" in char.properties or "indicate" in char.properties:
                    notifiable.append(char)

        print("\n--- WHAT THE SUITE EXPECTS ---")
        for label, uuid, pool in (
            ("WRITE_CHAR_UUID ", WRITE_CHAR_UUID, writable),
            ("NOTIFY_CHAR_UUID", NOTIFY_CHAR_UUID, notifiable),
        ):
            match = [c for c in pool if c.uuid.lower() == uuid.lower()]
            print("{}  {}  {}".format(
                label, uuid,
                "OK ({})".format(", ".join(match[0].properties)) if match
                else "NOT FOUND / WRONG PROPERTIES",
            ))

        if not writable:
            print("\nDevice exposes nothing writable. It cannot accept audio.")
            return 1

        # ---------------- 2. listen everywhere ----------------
        received = collections.Counter()

        def on_notify(sender, data):
            uuid = getattr(sender, "uuid", str(sender))
            received[uuid] += len(data)

        print("\n--- SUBSCRIBING TO {} NOTIFY CHARACTERISTIC(S) ---".format(
            len(notifiable)))
        subscribed = []
        for char in notifiable:
            try:
                await client.start_notify(char, on_notify)
                subscribed.append(char)
                print("   listening on", char.uuid)
            except Exception as exc:
                print("   could not subscribe to {}: {}".format(char.uuid, exc))

        if not subscribed:
            print("   nothing to listen on -- the device cannot send data back")

        # ---------------- 3. try each write mode ----------------
        burst = make_burst()

        for target in writable:
            for response in (False, True):
                mode = "with response" if response else "without response"

                needed = "write" if response else "write-without-response"
                if needed not in target.properties:
                    print("\n{} {}: unsupported, skipping".format(
                        target.uuid, mode))
                    continue

                received.clear()
                print("\n--- writing {} bytes to {} {} ---".format(
                    len(burst), target.uuid, mode))

                try:
                    for i in range(0, len(burst), CHUNK):
                        await client.write_gatt_char(
                            target, burst[i:i + CHUNK], response=response
                        )
                        await asyncio.sleep(0.01)
                except Exception as exc:
                    print("   write failed: {}: {}".format(type(exc).__name__, exc))
                    continue

                await asyncio.sleep(listen_s)

                if received:
                    for uuid, count in received.items():
                        print("   >>> GOT {} BYTES on {}".format(count, uuid))
                    print("   ^ use this UUID and write mode in ble_audio.py")
                else:
                    print("   nothing came back")

        for char in subscribed:
            try:
                await client.stop_notify(char)
            except Exception:
                pass

    print("\n--- CONCLUSION ---")
    print("If no line above says GOT n BYTES, this build is not echoing")
    print("processed audio back. The current firmware does echo it, so treat")
    print("a silent run as a fault rather than as a missing feature.")

    return 0


def main(argv=None):
    # An argument parser, so that --help prints help.
    parser = argparse.ArgumentParser(
        description='Diagnose "No DSP data received from device"')
    parser.add_argument("--listen", type=float, default=LISTEN_S,
                        help="seconds to wait for notifications after a write")

    args = parser.parse_args(argv)

    return asyncio.run(probe(args.listen))


if __name__ == "__main__":
    sys.exit(main())
