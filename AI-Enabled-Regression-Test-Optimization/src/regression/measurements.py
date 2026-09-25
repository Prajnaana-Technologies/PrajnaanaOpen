# Copyright (c) 2026 Prajnaana Technologies Pvt. Ltd.
# Original author: Dhanya Shree S
# SPDX-License-Identifier: MIT
#
# Licensed under the MIT License. See LICENSE in the project root.

"""Measurements the requirement suite compares against requirement limits.

A requirement that states a number ("latency under 2000 ms") needs a
measurement to compare with; these provide them. Exercising a component --
stream audio, read memory -- shows only that it responds, which leaves such
a requirement nothing to compare with and nothing to do but skip.

Each returns numbers, never a verdict. The limit comes from the requirement
and the comparison is made by check(), which prints the measured value next
to the limit, so a report shows how close a pass was rather than only that
it passed.

What these can and cannot do: they measure what the device does under
normal stimulus. They cannot make the device produce a chosen value -- a
latency of exactly 2001 ms, a battery level of 101 per cent -- so the
boundary and negative cases derived from the same requirements stay
skipped. That needs firmware that can be told to misbehave.

    take(device, "snr")                -> {"snr_db": 12.4}
    check(values, "snr_db", max=50, max_strict=True, unit="dB")
"""

import asyncio
import weakref

import numpy as np

# REQ-AUD-009. Converts sample counts to time for the compressor timing.
SAMPLE_RATE = 16000

# ble_audio.send_audio_stream sends at most 4000 bytes per call.
BLOCK = 2000

FULL_SCALE = 32767

# Per-sample gain is out / in. Below this input magnitude the 16-bit
# truncation of the output is too coarse for the ratio to mean anything.
MIN_GAIN_SAMPLE = 256

# Compressor timing. The envelope is driven by DC steps between two levels
# either side of the range where gain varies, and read back through a gain
# curve calibrated on the same device.
REST_LEVEL = 0.03
STEP_LEVEL = 0.10
CALIBRATION_LEVELS = tuple(np.linspace(0.052, 0.094, 12))

# Blocks at REST_LEVEL before a timing step. The release constant is the
# slow one: six blocks is 12000 samples, about 12 release constants, which
# settles the envelope from full scale to within 0.001 of the rest level.
REST_BLOCKS = 6

# A one-pole envelope decays as a straight line in log space. A fit worse
# than this is not a time constant, and quoting one would be inventing it.
MIN_FIT_R2 = 0.995


class MeasurementError(AssertionError):
    """The device did something that makes the measurement impossible.

    An AssertionError so pytest reports it as a failure with this message:
    a device that returns the wrong number of samples has failed, it has not
    been skipped.
    """


class Unmeasured(Exception):
    """The device does not report this quantity; the test skips with why.

    The difference from MeasurementError is the difference between a device
    that got something wrong and a device that never offered it. A build
    with no work-queue stack characteristic, no build identifier, no audio
    loopback, or a battery level it reports as unmeasured (0xFF), has not
    failed anything: there is nothing to compare with a limit, so the test
    skips and says which feature is absent.
    """


# Every audio measurement sends PCM and reads back what the DSP returned,
# so silence is the one answer these cannot interpret on their own: it is
# either firmware that never implemented the echo or firmware whose echo
# has stopped, and those have opposite verdicts. The device is asked which
# -- ble_audio.supports_audio_loopback reads its GATT table -- because any
# question put by sending audio is one a hung DSP answers wrongly.
NO_LOOPBACK = (
    "this firmware does not implement the audio loopback: it publishes no "
    "processed-audio notify characteristic, so it accepts the audio, "
    "notifies nothing back, and there is no processed audio to measure. "
    "Confirm with: python tools/ble_probe.py (from a source "
    "installation)"
)

# The other reason nothing came back, and the opposite verdict. A board
# that publishes the characteristic has the feature, so silence is the
# DSP stopping, a write failing or the reply timing out -- a result, and a
# bad one. send_audio_stream returns None for all of those as well as for a
# board with no loopback, so the reply alone cannot tell them apart.
NO_REPLY = (
    "the device reports an audio loopback but notified nothing back for "
    "this burst: the DSP stopped, a write failed or the reply timed out. "
    "The feature is present, so this is a failure and not a gap"
)

# The capability, per device. It is a fact about the firmware and cannot
# change inside a run, so it is asked once; RealBLEDevice caches it too,
# and a fixture's stand-in need not.
_LOOPBACK = weakref.WeakKeyDictionary()


def supports_loopback(device):
    """True when this firmware implements the processed-audio echo.

    A device that cannot be asked counts as supporting it. The only thing
    this decides is whether silence is a missing feature, and the only
    answer that makes silence a skip is a capability that came back False.

    A probe that raises is left to propagate: it is a question that did not
    get an answer, and treating it as "no" would report a board whose GATT
    read failed as a board with no loopback -- every audio case skipped and
    the run green on firmware that is not working.
    """
    probe = getattr(device, "supports_audio_loopback", None)

    if probe is None:
        return True

    try:
        return _LOOPBACK[device]
    except (KeyError, TypeError):
        pass

    supported = bool(run(device, probe()))

    try:
        _LOOPBACK[device] = supported
    except TypeError:
        pass

    return supported


def run(device, coro):
    """Drive one coroutine on the fixture's loop, where notifications land."""
    return asyncio.get_event_loop().run_until_complete(coro)


# ---------------------------------------------------------------- stimulus

def tone(amplitude, samples=BLOCK, freq=440.0):
    """A sine as PCM16."""
    t = np.arange(samples) / float(SAMPLE_RATE)

    return np.round(amplitude * FULL_SCALE
                    * np.sin(2 * np.pi * freq * t)).astype(np.int16)


def dc(level, samples=BLOCK):
    """A constant as PCM16. The envelope of a constant is the constant."""
    return np.full(samples, int(round(level * FULL_SCALE)), dtype=np.int16)


def stream(device, pcm):
    """Send PCM16, return PCM16 back, sample for sample.

    Sent as bytes, not floats: the float path in send_audio_stream scales
    and truncates, which can move a sample by one count, and every
    measurement here divides by the sample that was sent.
    """
    returned = []

    for start in range(0, len(pcm), BLOCK):
        block = pcm[start:start + BLOCK]

        raw = run(device, device.send_audio_stream(block.tobytes()))

        # Nothing came back, which means one of two opposite things, and
        # the capability is what tells them apart. Firmware without the
        # loopback notifies nothing for every audio write, and that is a
        # missing feature: Unmeasured, so the case skips and says which
        # feature is absent, the same verdict require_loopback reaches in
        # the other generated module. A board that publishes the
        # characteristic and then goes quiet has failed -- send_audio_stream
        # also returns None when the DSP hangs, a write fails or the reply
        # times out.
        if raw is None:
            if not supports_loopback(device):
                raise Unmeasured(NO_LOOPBACK)

            raise MeasurementError(NO_REPLY)

        got = np.frombuffer(bytes(raw)[:len(raw) & ~1], dtype=np.int16)

        # Gain and timing compare sample n out with sample n in. One lost
        # or extra sample shifts every comparison after it.
        if len(got) != len(block):
            raise MeasurementError(
                "device returned {} samples for {} sent; sample-by-sample "
                "measurement needs every one".format(len(got), len(block)))

        returned.append(got)

    return np.concatenate(returned)


def sample_gains(sent, got):
    """Per-sample gain, for samples large enough to carry one.

    The device truncates its output toward zero, so the true gain of a
    sample lies in [|out| / |in|, (|out| + 1) / |in|). Returns both ends.
    """
    sent = sent.astype(np.float64)
    got = got.astype(np.float64)

    usable = (np.abs(sent) >= MIN_GAIN_SAMPLE) & (np.sign(sent) == np.sign(got))

    low = np.abs(got[usable]) / np.abs(sent[usable])
    high = (np.abs(got[usable]) + 1.0) / np.abs(sent[usable])

    return low, high


def rms(x):
    return float(np.sqrt(np.mean(np.square(x.astype(np.float64)))))


# ------------------------------------------------------------ measurements

def snr(device):
    """Signal-to-noise ratio of processed audio against what was sent.

    Quiet and loud tone, so both ends of the compressor contribute.
    """
    sent = np.concatenate([tone(0.05), tone(0.9)])
    got = stream(device, sent)

    signal = np.sum(np.square(sent.astype(np.float64)))
    noise = np.sum(np.square(sent.astype(np.float64) - got))

    if noise == 0:
        raise MeasurementError("device returned the input unchanged; "
                               "the signal-to-noise ratio is infinite")

    return {"snr_db": 10.0 * np.log10(signal / noise)}


def latency(device):
    """First byte sent to first byte back, over the BLE link."""
    stream(device, tone(0.5))

    ms = device.last_stream_latency_ms

    if ms is None:
        raise MeasurementError("no notification arrived to time")

    return {"latency_ms": float(ms)}


def compression(device):
    """How much more gain quiet input gets than loud input, in per cent.

    Two warm-up blocks per level: the release constant is 60 ms, so
    recovering from loud audio takes longer than one block, and a quiet
    reading taken too soon still carries the loud gain.
    """
    gains = {}

    for label, amplitude in (("quiet", 0.05), ("loud", 0.90)):
        sent = tone(amplitude)

        for _ in range(2):
            stream(device, sent)

        got = stream(device, sent)
        tail = slice(len(sent) // 2, len(sent))

        gains[label] = rms(got[tail]) / rms(sent[tail])

    return {
        "quiet_over_loud_gain_percent":
            (gains["quiet"] / gains["loud"] - 1.0) * 100.0,
        "quiet_gain": gains["quiet"],
        "loud_gain": gains["loud"],
    }


def gain_range(device):
    """Lowest and highest gain applied, across quiet to near full scale.

    Reported as the tightest bounds the 16-bit output allows: min_gain is
    the highest the lowest gain could have been, max_gain the lowest the
    highest could have been. A value outside the requirement is therefore
    certainly outside it, not a rounding artefact.
    """
    lows, highs = [], []

    for amplitude in (0.02, 0.05, 0.1, 0.3, 0.6, 0.99):
        sent = tone(amplitude)
        low, high = sample_gains(sent, stream(device, sent))
        lows.append(low)
        highs.append(high)

    low = np.concatenate(lows)
    high = np.concatenate(highs)

    if not len(low):
        raise MeasurementError("no sample was large enough to measure gain")

    return {"min_gain": float(np.min(high)), "max_gain": float(np.max(low))}


def sign_flips(device):
    """Per cent of loud samples that came back with the opposite sign.

    The signature of 16-bit overflow: a loud positive sample wraps negative.
    """
    sent = tone(0.99)
    got = stream(device, sent)

    loud = np.abs(sent.astype(np.int32)) > FULL_SCALE // 2
    flipped = np.sign(sent[loud]) != np.sign(got[loud])

    return {"loud_sign_flip_percent": 100.0 * float(np.mean(flipped))}


def _fit_time_constant(envelope, target):
    """Time constant, in ms, of an envelope approaching `target`.

    A one-pole follower gives |target - envelope| = A * c**n, a straight
    line in log space with slope ln(c) = -1 / (tau * fs).
    """
    distance = np.abs(target - envelope)
    n = np.nonzero(distance > 0)[0]

    if len(n) < 10:
        raise MeasurementError(
            "only {} samples fell inside the calibrated gain range; too "
            "few to fit a time constant".format(len(n)))

    y = np.log(distance[n])
    slope, intercept = np.polyfit(n, y, 1)

    fitted = slope * n + intercept
    r2 = 1.0 - np.sum((y - fitted) ** 2) / np.sum((y - np.mean(y)) ** 2)

    if slope >= 0:
        raise MeasurementError("the envelope moved away from the new level")

    return -1000.0 / (slope * SAMPLE_RATE), float(r2), len(n)


def compressor_timing(device):
    """Attack and release time constants of the compressor, in ms.

    The envelope cannot be read directly, only the gain it produces. So:

      1. Calibrate. A constant input settles the envelope at exactly that
         level, so the steady gain at each of several levels gives the gain
         curve point by point -- measured on this device, not taken from
         the firmware's constants.
      2. Step. Jump from a level below the curve to one above it (attack),
         then back (release), and turn each sample's gain into an envelope
         value through the calibrated curve.
      3. Fit. A one-pole envelope approaches its new level exponentially;
         the slope of the log distance is the time constant. The fit's R^2
         is checked, because a curve that is not exponential has no time
         constant to report.
    """
    rest = dc(REST_LEVEL)

    for _ in range(REST_BLOCKS):
        stream(device, rest)

    # 1. Calibrate, rising, so each block only needs the fast attack to
    #    settle.
    levels, gains = [], []

    for level in CALIBRATION_LEVELS:
        sent = dc(level)
        got = stream(device, sent)
        tail = slice(len(sent) // 2, len(sent))

        levels.append(sent[0] / float(FULL_SCALE))
        gains.append(float(np.median(got[tail] / sent[tail].astype(np.float64))))

    levels = np.array(levels)
    gains = np.array(gains)

    if not np.all(np.diff(gains) < 0):
        raise MeasurementError(
            "gain does not fall as the level rises between {:.3f} and {:.3f} "
            "(gains {}); there is no compression here to time".format(
                levels[0], levels[-1], np.round(gains, 4).tolist()))

    def envelope(sent, got):
        gain = got / sent.astype(np.float64)
        inside = (gain < gains[0]) & (gain > gains[-1])

        # The curve is close to a power law, so interpolate in log-log.
        env = np.full(len(gain), np.nan)
        env[inside] = np.exp(np.interp(
            np.log(gain[inside]), np.log(gains[::-1]), np.log(levels[::-1])))

        return env

    for _ in range(REST_BLOCKS):
        stream(device, rest)

    # 2. Attack: rest -> step. Release: step -> rest. The step block is long
    #    enough (25 attack constants) to settle before the release starts.
    step = dc(STEP_LEVEL)
    attack_env = envelope(step, stream(device, step))
    release_env = envelope(rest, stream(device, rest))

    # 3. Fit only the samples whose gain fell inside the calibrated range.
    #    Samples outside it are set to the target, which gives them zero
    #    distance, and _fit_time_constant leaves zero distances out.
    def fit(env, target):
        return _fit_time_constant(np.where(np.isnan(env), target, env), target)

    attack_ms, attack_r2, attack_n = fit(attack_env, step[0] / float(FULL_SCALE))
    release_ms, release_r2, release_n = fit(release_env, REST_LEVEL)

    for name, r2 in (("attack", attack_r2), ("release", release_r2)):
        if r2 < MIN_FIT_R2:
            raise MeasurementError(
                "the {} envelope is not exponential (fit R^2 {:.4f}, needs "
                "{}); a time constant would be invented, not measured".format(
                    name, r2, MIN_FIT_R2))

    return {
        "attack_ms": attack_ms,
        "release_ms": release_ms,
        "attack_fit_r2": attack_r2,
        "release_fit_r2": release_r2,
        "attack_samples": attack_n,
        "release_samples": release_n,
    }


def battery(device):
    level = run(device, device.read_battery())

    if level is None:
        raise MeasurementError("no battery level came back from 0x2A19")

    if level == 0xFF:
        raise Unmeasured("the device reports battery as unmeasured (0xFF)")

    return {"battery_percent": level}


def stack(device):
    value = run(device, device.read_memory())

    if value is None:
        raise Unmeasured("this build does not report work-queue stack use")

    return {"stack_kib": float(value)}


def build_id(device):
    value = run(device, device.read_build_id())

    if value is None:
        raise Unmeasured("this build has no build identifier characteristic")

    return {"build_id_characters": len(value), "build_id": value}


MEASUREMENTS = {
    "snr": snr,
    "latency": latency,
    "compression": compression,
    "gain_range": gain_range,
    "sign_flips": sign_flips,
    "compressor_timing": compressor_timing,
    "battery": battery,
    "stack": stack,
    "build_id": build_id,
}


def take(device, name):
    """Run one measurement and print everything it found."""
    values = MEASUREMENTS[name](device)

    for key, value in values.items():
        print("measured {} = {}".format(
            key, round(value, 4) if isinstance(value, float) else value))

    return values


def check(values, name, minimum=None, maximum=None, min_strict=False,
          max_strict=False, unit="", tolerance=0.0):
    """Assert one measured value against a requirement's limits.

    `tolerance` is a fraction of the limit, for a measurement whose own
    uncertainty is known. It is printed with the verdict so a pass that
    needed it is visible as one.
    """
    value = values[name]
    unit = (" " + unit) if unit else ""

    limits = []

    if minimum is not None:
        limits.append("{} {}{}".format(">" if min_strict else ">=", minimum, unit))

    if maximum is not None:
        limits.append("{} {}{}".format("<" if max_strict else "<=", maximum, unit))

    allowed = " and ".join(limits)

    if tolerance:
        allowed += " (measurement tolerance {:.0%})".format(tolerance)

    print("requirement: {} {}; measured {:.4g}{}".format(
        name, allowed, value, unit))

    if minimum is not None:
        floor = minimum - abs(minimum) * tolerance
        low = value <= floor if min_strict else value < floor

        assert not low, "{} = {:.4g}{} is below the requirement: {}".format(
            name, value, unit, allowed)

    if maximum is not None:
        ceiling = maximum + abs(maximum) * tolerance
        high = value >= ceiling if max_strict else value > ceiling

        assert not high, "{} = {:.4g}{} is above the requirement: {}".format(
            name, value, unit, allowed)
