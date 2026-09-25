# Copyright (c) 2026 Prajnaana Technologies Pvt. Ltd.
# Original author: Dhanya Shree S
# SPDX-License-Identifier: MIT
#
# Licensed under the MIT License. See LICENSE in the project root.

"""The test source each scenario emits.

One entry per name in planner.KNOWN_SCENARIOS. A scenario in the vocabulary
that emits nothing is worse than a missing one, because the plan then claims
coverage the run never delivers -- tests/test_scenarios.py holds the two
lists in step so that cannot happen quietly.

Kept apart from generate_tests.py because these are test *source*, not
generator logic, and mixing the two makes both harder to read.

The category mark is what regression/catalog.py counts. An untagged test is
functional; the other five categories exist because the expensive bugs are
not functional ones:

    stress           where the limit is, and whether crossing it is graceful
    fault_injection  whether recovery actually runs
    long_duration    leaks, counter overflow, drift
    concurrency      conflicts when subsystems compete for the radio
    security         pairing, bonding and what an unpaired peer can reach
"""

RECONNECT_STRESS = '''
@pytest.mark.category("fault_injection")
def test_reconnect(ble_device):
    # Recovery code is usually the least exercised code in a system, because
    # the fault it handles does not occur on a bench by itself. Cause it.
    loop = get_loop()

    for _ in range(RECONNECT_CYCLES):
        loop.run_until_complete(ble_device.disconnect())
        loop.run_until_complete(asyncio.sleep(SETTLE))
        loop.run_until_complete(ble_device.connect())
        loop.run_until_complete(asyncio.sleep(SETTLE))

    assert ble_device.is_connected, (
        "device did not come back after {} disconnect/reconnect "
        "cycles".format(RECONNECT_CYCLES)
    )
'''

FAULT_LINK_DROP = '''
@pytest.mark.category("fault_injection")
def test_link_drop_mid_stream(ble_device):
    # Drop the link while audio is in flight, then check the device recovers
    # and still processes audio. A clean disconnect between tests never
    # exercises the half-finished-transfer path.
    loop = get_loop()
    require_loopback(ble_device, loop)

    audio = tone(0.5)

    loop.run_until_complete(ble_device.send_audio_stream(audio[:200]))
    loop.run_until_complete(ble_device.disconnect())
    loop.run_until_complete(asyncio.sleep(SETTLE))
    loop.run_until_complete(ble_device.connect())
    loop.run_until_complete(asyncio.sleep(SETTLE))

    assert ble_device.is_connected, (
        "device did not re-advertise after a mid-stream drop"
    )

    recovered = loop.run_until_complete(ble_device.send_audio_stream(audio))

    assert recovered is not None, (
        "device reconnected but no longer returns audio -- the stream path "
        "did not recover from the interrupted transfer"
    )
'''

STRESS_STABILITY = '''
@pytest.mark.category("stress")
def test_burst_escalation(ble_device):
    # Push harder until something gives, and record where. The device is
    # allowed to drop audio under load; it is not allowed to stop responding.
    loop = get_loop()
    require_loopback(ble_device, loop)

    limit = None

    for samples in (250, 500, 1000, 2000):
        sent = tone(0.6, samples=samples)
        got = stream(ble_device, loop, sent)

        kept = len(got) / float(len(sent)) if len(sent) else 0.0

        print("burst {:>5} samples -> {:.0%} returned".format(samples, kept))

        if kept < 0.99 and limit is None:
            limit = samples

    assert ble_device.is_connected, (
        "device stopped responding under burst escalation"
    )

    if limit:
        print("first loss at {} samples per burst".format(limit))
'''

MEMORY_PRESSURE = '''
@pytest.mark.category("stress")
def test_memory_under_repeated_streams(ble_device):
    # Memory that climbs and never returns is the signature of a leak. One
    # stream cannot show it; several back to back can.
    #
    # The metric is system work-queue stack high-water in KiB, not heap:
    # this firmware never allocates at run time. That bounds it by the 4 KiB
    # stack, so a limit above 4 KiB could never fire: it sits past the
    # largest value the device can physically report. Both limits
    # below sit inside the window: growth is measured against this test's own
    # `before` reading, and the ceiling is the one the risk engine already
    # scores against.
    loop = get_loop()
    require_loopback(ble_device, loop)

    before = loop.run_until_complete(ble_device.read_metrics())

    if "memory" not in before:
        pytest.skip(
            "this build does not report work-queue stack use, so a leak "
            "cannot be measured"
        )

    for _ in range(STRESS_STREAMS):
        stream(ble_device, loop, tone(0.5))

    after = loop.run_until_complete(ble_device.read_metrics())

    if "memory" not in after:
        pytest.fail(
            "the device reported stack use before the streams and not after"
        )

    growth = after["memory"] - before["memory"]

    print("work-queue stack {:.2f} KiB -> {:.2f} KiB (growth {:+.2f}; "
          "limit {:.2f} of {:.2f} KiB)".format(
              before["memory"], after["memory"], growth,
              MAX_STACK_KB, WORKQUEUE_STACK_KB))

    assert growth <= MAX_STACK_GROWTH_KB, (
        "work-queue stack grew {:.2f} KiB across {} streams, more than the "
        "{:.2f} KiB that counts as steady state. The calibration recorded a "
        "flat high-water across 24 samples, so growth is a change in "
        "behaviour -- and this stack is only {:.2f} KiB deep".format(
            growth, STRESS_STREAMS, MAX_STACK_GROWTH_KB, WORKQUEUE_STACK_KB)
    )

    assert after["memory"] <= MAX_STACK_KB, (
        "work-queue stack reached {:.2f} KiB of {:.2f} KiB after {} streams, "
        "past the {:.2f} KiB limit; {:.2f} KiB of margin left before "
        "overflow".format(
            after["memory"], WORKQUEUE_STACK_KB, STRESS_STREAMS, MAX_STACK_KB,
            WORKQUEUE_STACK_KB - after["memory"])
    )
'''

POWER_STRESS = '''
@pytest.mark.category("stress")
def test_power_under_sustained_stream(ble_device):
    # A firmware change can pass every functional test and still halve
    # battery life. Nothing else in the suite would notice.
    loop = get_loop()
    require_loopback(ble_device, loop)

    before = loop.run_until_complete(ble_device.read_metrics())

    if "power" not in before:
        pytest.skip(
            "no power reading -- the board cannot measure its own supply "
            "current. Connect a PPK II and set HA_POWER_MA or HA_POWER_CSV."
        )

    for _ in range(POWER_STREAMS):
        stream(ble_device, loop, tone(0.7))

    after = loop.run_until_complete(ble_device.read_metrics())

    drawn = after.get("power", before["power"])

    print("power {:.1f} mA -> {:.1f} mA".format(before["power"], drawn))

    assert drawn < MAX_STREAM_CURRENT_MA, (
        "sustained streaming draws {:.1f} mA, budget is {:.1f}".format(
            drawn, MAX_STREAM_CURRENT_MA)
    )
'''

LONG_DURATION_STABILITY = '''
@pytest.mark.category("long_duration")
def test_long_duration_stability(ble_device):
    # Leaks, counter overflow and drift only appear with time. The default is
    # a short proof the loop works; set HA_LONG_MINUTES for a real soak.
    loop = get_loop()
    require_loopback(ble_device, loop)

    minutes = float(os.getenv("HA_LONG_MINUTES", "1"))
    deadline = time.time() + minutes * 60.0

    first = loop.run_until_complete(ble_device.read_metrics())

    cycles = 0
    empty = 0

    while time.time() < deadline:
        got = stream(ble_device, loop, tone(0.5))

        cycles += 1

        if len(got) == 0:
            empty += 1

    last = loop.run_until_complete(ble_device.read_metrics())

    print("{} cycles over {:.1f} min, {} empty".format(cycles, minutes, empty))

    if "memory" in first and "memory" in last:
        print("work-queue stack {:.2f} -> {:.2f} KiB (limit {:.2f})".format(
            first["memory"], last["memory"], MAX_STACK_KB))

        # A soak that only reports the number cannot fail on drift, which is
        # the thing a soak exists to find.
        assert last["memory"] <= MAX_STACK_KB, (
            "work-queue stack reached {:.2f} KiB of {:.2f} KiB over {:.1f} "
            "minutes, past the {:.2f} KiB limit".format(
                last["memory"], WORKQUEUE_STACK_KB, minutes, MAX_STACK_KB)
        )

    assert cycles > 0, "no streaming cycle completed"

    assert empty == 0, (
        "{} of {} cycles returned no audio -- stability degrades with "
        "time".format(empty, cycles)
    )

    assert ble_device.is_connected, "link did not survive the soak"
'''

CONCURRENCY_STREAM_AND_READ = '''
@pytest.mark.category("concurrency")
def test_metrics_read_during_stream(ble_device):
    # Each subsystem works alone; they conflict only when competing for the
    # same radio time. Read telemetry while audio is in flight and check both
    # survive -- the interaction class isolated tests cannot see.
    loop = get_loop()
    require_loopback(ble_device, loop)

    audio = tone(0.5)

    async def both():
        streaming = asyncio.ensure_future(ble_device.send_audio_stream(audio))
        await asyncio.sleep(0.05)
        reading = asyncio.ensure_future(ble_device.read_metrics())

        return await asyncio.gather(streaming, reading, return_exceptions=True)

    returned, metrics = loop.run_until_complete(both())

    assert not isinstance(returned, Exception), (
        "streaming failed while telemetry was being read: {}".format(returned)
    )

    assert not isinstance(metrics, Exception), (
        "telemetry read failed while audio was streaming: {}".format(metrics)
    )

    assert returned is not None, (
        "a concurrent telemetry read starved the audio path -- neither works "
        "when they overlap, though both pass alone"
    )

    print("concurrent stream and metrics read both completed")
'''


PAIRING_BONDING = '''
@pytest.mark.category("security")
def test_encryption_is_actually_enforced(ble_device):
    # Proves the permission does something. A pairing test that only checks
    # pair() returned proves the call succeeded, not that anything is
    # protected -- so read a characteristic that demands encryption while
    # unpaired and require refusal.
    loop = get_loop()

    loop.run_until_complete(ble_device.unpair())
    loop.run_until_complete(ble_device.disconnect())
    loop.run_until_complete(asyncio.sleep(SETTLE))
    loop.run_until_complete(ble_device.connect())

    refused = loop.run_until_complete(ble_device.read_bond_count())

    assert refused is None, (
        "the encryption-required characteristic was readable on an unpaired "
        "link -- BT_GATT_PERM_READ_ENCRYPT is not being enforced"
    )


@pytest.mark.category("security")
def test_pairing_grants_access(ble_device):
    # The positive half: after pairing, the same read must succeed.
    loop = get_loop()

    assert loop.run_until_complete(ble_device.pair()), "pairing failed"

    try:
        bonds = loop.run_until_complete(ble_device.read_bond_count())

        assert bonds is not None, (
            "pairing reported success but the encrypted read is still "
            "refused -- the link did not actually reach an encrypted state"
        )

        assert bonds >= 1, (
            "link is encrypted but the device stored no bond ({}), so "
            "nothing will survive a reconnect".format(bonds)
        )

        assert loop.run_until_complete(ble_device.is_encrypted()), (
            "device reports security level 1 on a paired link"
        )

        print("paired; device holds {} bond(s)".format(bonds))
    finally:
        # The OS owns the bond and it outlives this process. Leaving it
        # behind means the next run starts from a state it did not set up.
        loop.run_until_complete(ble_device.unpair())


@pytest.mark.category("security")
def test_bond_survives_a_reconnect(ble_device):
    # Bonding is the difference between pairing once and pairing every time.
    # Without persistence the device re-pairs silently on each connection and
    # the previous test would still pass.
    loop = get_loop()

    assert loop.run_until_complete(ble_device.pair()), "pairing failed"

    try:
        loop.run_until_complete(ble_device.disconnect())
        loop.run_until_complete(asyncio.sleep(SETTLE))
        loop.run_until_complete(ble_device.connect())
        loop.run_until_complete(asyncio.sleep(SETTLE))

        bonds = loop.run_until_complete(ble_device.read_bond_count())

        assert bonds is not None, (
            "the encrypted read was refused after reconnecting to a bonded "
            "device -- the bond did not persist, so every connection pairs "
            "again"
        )

        print("bond survived the reconnect; {} stored".format(bonds))
    finally:
        loop.run_until_complete(ble_device.unpair())
'''


SCENARIO_TESTS = {
    "reconnect_stress": RECONNECT_STRESS,
    "fault_link_drop": FAULT_LINK_DROP,
    "stress_stability": STRESS_STABILITY,
    "memory_pressure": MEMORY_PRESSURE,
    "power_stress": POWER_STRESS,
    "long_duration_stability": LONG_DURATION_STABILITY,
    "concurrency_stream_and_read": CONCURRENCY_STREAM_AND_READ,
    "pairing_bonding": PAIRING_BONDING,
}
