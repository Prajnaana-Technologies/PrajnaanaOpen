# Copyright (c) 2026 Prajnaana Technologies Pvt. Ltd.
# Original author: Dhanya Shree S
# SPDX-License-Identifier: MIT
#
# Licensed under the MIT License. See LICENSE in the project root.

"""Tests for the requirement measurements, against a simulated compressor.

The simulation is a line-for-line port of wdrc_process in
NRF_Firmware/src/dsp_wdrc.c, in float32 like the Cortex-M4F. It lets each
measurement be checked against a known answer: a measurement that cannot
recover the constants of a device whose constants are known cannot be
trusted with one whose constants are not.
"""

import asyncio

import numpy as np
import pytest

from regression import measurements


class SimulatedCompressor:
    """A device running the firmware compressor, reachable like RealBLEDevice."""

    def __init__(self, ceil=0.85, floor=0.55, knee=0.05, ratio=3.0,
                 attack_s=0.005, release_s=0.060, drop=0, loopback=True):
        f = np.float32
        self.loopback = loopback
        self.ceil, self.floor, self.knee = f(ceil), f(floor), f(knee)
        self.exponent = f(1.0 / ratio - 1.0)
        self.attack = f(np.exp(-1.0 / (attack_s * 16000)))
        self.release = f(np.exp(-1.0 / (release_s * 16000)))
        self.envelope = f(0.0)
        self.drop = drop
        self.last_stream_latency_ms = 47.0

    def process(self, samples):
        f = np.float32
        out = np.empty_like(samples)

        for i, s in enumerate(samples):
            x = f(s) / f(32767.0)
            mag = abs(x)
            coef = self.attack if mag > self.envelope else self.release
            self.envelope = coef * self.envelope + (f(1.0) - coef) * mag

            gain = self.ceil

            if self.envelope > self.knee:
                gain = self.ceil * f(np.power(self.envelope / self.knee,
                                              self.exponent))

            gain = min(max(gain, self.floor), self.ceil)
            y = min(max(x * gain, f(-1.0)), f(1.0))
            out[i] = np.int16(np.trunc(y * f(32767.0)))

        return out

    async def send_audio_stream(self, data):
        if not self.loopback:
            return None

        samples = np.frombuffer(data[:4000], dtype=np.int16)
        out = self.process(samples)

        return out[:len(out) - self.drop].tobytes()

    async def supports_audio_loopback(self, force=False):
        """The capability probe RealBLEDevice answers, modelled here too.

        It is what lets the fixture tell the two silences apart, which is
        the distinction measurements.stream draws.
        """
        return self.loopback

    async def read_battery(self):
        return 65

    async def read_memory(self):
        return 2.51

    async def read_build_id(self):
        return "99b1e43e-dirty+4117746a"


@pytest.fixture(autouse=True)
def loop():
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    yield loop
    loop.close()


def test_timing_recovers_the_firmware_constants():
    values = measurements.compressor_timing(SimulatedCompressor())

    assert values["attack_ms"] == pytest.approx(5.0, rel=0.02)
    assert values["release_ms"] == pytest.approx(60.0, rel=0.02)
    assert values["attack_fit_r2"] > measurements.MIN_FIT_R2
    assert values["release_fit_r2"] > measurements.MIN_FIT_R2


def test_timing_tracks_a_changed_constant():
    # The point of measuring: a slower attack must read as slower, not as
    # the value the firmware header happens to say.
    values = measurements.compressor_timing(
        SimulatedCompressor(attack_s=0.008, release_s=0.100))

    assert values["attack_ms"] == pytest.approx(8.0, rel=0.02)
    assert values["release_ms"] == pytest.approx(100.0, rel=0.02)


def test_timing_does_not_depend_on_the_gain_law():
    # Calibrated on the device, so a different knee and ratio still time
    # correctly.
    values = measurements.compressor_timing(
        SimulatedCompressor(knee=0.045, ratio=2.0))

    assert values["attack_ms"] == pytest.approx(5.0, rel=0.03)
    assert values["release_ms"] == pytest.approx(60.0, rel=0.03)


def test_fixed_gain_has_no_timing_to_measure():
    with pytest.raises(measurements.MeasurementError, match="no compression"):
        measurements.compressor_timing(SimulatedCompressor(floor=0.85))


def test_gain_range_brackets_the_clamp():
    values = measurements.gain_range(SimulatedCompressor())

    assert 0.55 <= values["min_gain"] < 0.56
    assert 0.84 < values["max_gain"] <= 0.85


def test_gain_range_catches_a_gain_above_the_ceiling():
    values = measurements.gain_range(SimulatedCompressor(ceil=0.9))

    with pytest.raises(AssertionError, match="above the requirement"):
        measurements.check(values, "max_gain", maximum=0.85)


def test_snr_matches_the_firmware_header():
    # dsp_wdrc.h: SNR = -20 log10(1 - g), g in [0.55, 0.85] -> 6.9 to 16.5 dB.
    snr = measurements.snr(SimulatedCompressor())["snr_db"]

    assert 6.9 <= snr <= 16.5


def test_compression_percent():
    values = measurements.compression(SimulatedCompressor())

    # Quiet sits at the 0.85 ceiling, loud at the 0.55 floor: 0.85 / 0.55.
    assert values["quiet_over_loud_gain_percent"] == pytest.approx(54.5, abs=1)


def test_no_sign_flips_without_overflow():
    values = measurements.sign_flips(SimulatedCompressor())

    assert values["loud_sign_flip_percent"] == 0.0


def test_lost_samples_fail_rather_than_misalign():
    with pytest.raises(measurements.MeasurementError, match="needs every one"):
        measurements.gain_range(SimulatedCompressor(drop=1))


def test_unmeasured_battery_skips():
    device = SimulatedCompressor()

    async def unknown():
        return 0xFF

    device.read_battery = unknown

    with pytest.raises(measurements.Unmeasured):
        measurements.battery(device)


def test_a_missing_loopback_is_unmeasured_rather_than_a_failure():
    """A board that echoes nothing has no DSP result to be wrong about.

    stack(), build_id() and battery() all skip the same way for a quantity
    a build does not report.
    """
    assert not issubclass(measurements.Unmeasured, AssertionError), (
        "Unmeasured must not read as a failure")

    # A board without the feature: the probe says so, and nothing comes
    # back. Both halves matter -- silence alone does not decide it.
    for measurement in ("snr", "latency", "compression", "gain_range",
                        "sign_flips", "compressor_timing"):
        device = SimulatedCompressor(loopback=False)

        with pytest.raises(measurements.Unmeasured, match="loopback"):
            measurements.MEASUREMENTS[measurement](device)


def test_a_loopback_that_stops_replying_fails_rather_than_skips():
    """Silence from a board that HAS the loopback is a failure.

    send_audio_stream returns None on every error and timeout path as well
    as on a board with no echo, so the capability probe is what tells the
    two silences apart.
    """
    for measurement in ("snr", "latency", "compression", "gain_range",
                        "sign_flips", "compressor_timing"):
        device = SimulatedCompressor()

        async def stops_replying(data):
            return None

        device.send_audio_stream = stops_replying

        # An AssertionError, so pytest reports it as a failing test.
        with pytest.raises(measurements.MeasurementError) as caught:
            measurements.MEASUREMENTS[measurement](device)

        assert issubclass(measurements.MeasurementError, AssertionError)
        assert not isinstance(caught.value, measurements.Unmeasured), (
            "{} skipped a board that reports a loopback and stopped "
            "replying".format(measurement))
        assert "not a gap" in str(caught.value)


def test_the_loopback_probe_is_asked_once_per_device():
    """The capability is read once per device and cached for the run.

    The answer decides a skip against a failure, so it must not move while
    the board does not.
    """
    device = SimulatedCompressor()
    device.probes = 0

    async def answers_once(force=False):
        device.probes += 1

        return device.probes == 1

    async def goes_silent(data):
        return None

    device.supports_audio_loopback = answers_once
    device.send_audio_stream = goes_silent

    verdicts = []

    # Two measurements, one device. Unmeasured is not a MeasurementError,
    # so an uncached second probe escapes this block and fails the test.
    for measurement in ("snr", "compression"):
        with pytest.raises(measurements.MeasurementError) as caught:
            measurements.MEASUREMENTS[measurement](device)

        verdicts.append(caught.value)

    assert device.probes == 1, (
        "the capability is read once per device and was asked {} "
        "times".format(device.probes))

    for verdict, measurement in zip(verdicts, ("snr", "compression")):
        assert not isinstance(verdict, measurements.Unmeasured), measurement
        assert "not a gap" in str(verdict), measurement


def test_a_device_that_answers_wrongly_still_fails():
    """Only silence is a skip. A wrong answer is a wrong answer.

    Skipping on a missing loopback must not turn every audio measurement
    into something that cannot fail: a device returning the wrong number
    of samples, or the input untouched, is a result and a bad one.
    """
    with pytest.raises(measurements.MeasurementError, match="needs every one"):
        measurements.gain_range(SimulatedCompressor(drop=1))

    passthrough = SimulatedCompressor()

    async def unchanged(data):
        import numpy as np

        return np.frombuffer(data[:4000], dtype=np.int16).tobytes()

    passthrough.send_audio_stream = unchanged

    with pytest.raises(measurements.MeasurementError, match="unchanged"):
        measurements.snr(passthrough)


def test_check_strict_and_inclusive_limits():
    measurements.check({"v": 50.0}, "v", maximum=50)

    with pytest.raises(AssertionError):
        measurements.check({"v": 50.0}, "v", maximum=50, max_strict=True)

    measurements.check({"v": 5.1}, "v", maximum=5, tolerance=0.05)

    with pytest.raises(AssertionError):
        measurements.check({"v": 5.3}, "v", maximum=5, tolerance=0.05)
