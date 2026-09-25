# Copyright (c) 2026 Prajnaana Technologies Pvt. Ltd.
# Original author: Dhanya Shree S
# SPDX-License-Identifier: MIT
#
# Licensed under the MIT License. See LICENSE in the project root.

import asyncio

import pytest

from regression.ble.ble_audio import RealBLEDevice


def pytest_configure(config):
    # A frozen build has no pytest.ini, so the marker registered there is
    # unknown here and every categorised test raises a warning.
    config.addinivalue_line(
        "markers",
        "category(name): functional, stress, fault_injection, long_duration, "
        "concurrency or security",
    )


@pytest.fixture(scope="session")
def ble_device():
    """One BLE connection for the whole session.

    Session scope on purpose: reconnecting per test is slow and flaky over
    real BLE.

    The device must be powered on and advertising, or every test errors out.
    """
    device = RealBLEDevice()

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    try:
        loop.run_until_complete(device.connect())
        yield device
    finally:
        loop.run_until_complete(device.disconnect())
        loop.close()
