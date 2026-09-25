# Copyright (c) 2026 Prajnaana Technologies Pvt. Ltd.
# Original author: Dhanya Shree S
# SPDX-License-Identifier: MIT
#
# Licensed under the MIT License. See LICENSE in the project root.

import ast
import os
import shutil

from regression.ai_engine.planner import (
    INTENSITIES,
    INTENSITY_PACKETS,
    KNOWN_SCENARIOS,
    MAX_PACKETS,
    RegressionPlan,
    decide_intensity,
    plan_regression,
    suggest_scenarios,
)

from regression.ai_engine.scenario_tests import SCENARIO_TESTS
from regression.change_detection.risk_engine import (
    METRIC_THRESHOLDS,
    UNMEASURED_WHY,
    WORKQUEUE_STACK_KB,
    calculate_risk_score,
)
from regression.paths import GENERATED_TEST_PATH, asset, test_signal

CONFTEST = "conftest.py"


def ensure_conftest():
    """Put the ble_device fixture beside the module pytest is about to run.

    pytest resolves conftest.py from the filesystem. A frozen build has no
    pytest.ini, so rootdir collapses to the directory holding the generated
    module and only a conftest.py sitting there is loaded -- and PyInstaller
    places bundled data under _internal/, which is not that directory.
    Without this copy every hardware test errors with
    "fixture 'ble_device' not found".
    """
    target = os.path.join(os.path.dirname(GENERATED_TEST_PATH), CONFTEST)

    if os.path.exists(target):
        return target

    source = asset("regression", "generated_tests", CONFTEST)

    if os.path.exists(source) and os.path.abspath(source) != os.path.abspath(target):
        shutil.copyfile(source, target)

    return target

# Pass/fail bounds for the generated metric tests, every one of them derived
# from the risk engine's table so a metric cannot be scored against one limit
# and judged against another. See risk_engine.METRIC_THRESHOLDS for the
# numbers and what is still uncalibrated.
THRESHOLDS = {
    name: {"min": 0, "max": limit}
    for name, limit in METRIC_THRESHOLDS.items()
}

# decide_intensity and suggest_scenarios live in planner.py; they are
# re-exported above so either module can be imported for them.
INTENSITY_MAP = {k: {"packets": v} for k, v in INTENSITY_PACKETS.items()}


def enforce_safe_limits(params):
    """Cap packets so a generated test can never overload the device."""
    params = dict(params)  # copy: INTENSITY_MAP must not be mutated in place
    params["packets"] = min(params["packets"], MAX_PACKETS)
    return params


# ---------------------------------------------------
# MAIN GENERATOR
# ---------------------------------------------------

def defined_tests(source):
    """Names of the test functions defined in a piece of generated source."""
    return [
        node.name for node in ast.walk(ast.parse(source))
        if isinstance(node, ast.FunctionDef) and node.name.startswith("test_")
    ]


def raise_to(plan, floor):
    """The plan, at no less than `floor` intensity.

    A floor, rather than a re-plan in the hope the new plan comes out
    harder: a planner that chose medium before a failure chooses medium
    again from the same evidence, and a run labelled "escalated" would
    repeat itself. The floor makes the step up a fact.
    """
    if not floor or INTENSITIES.index(floor) <= INTENSITIES.index(plan.intensity):
        return plan

    return RegressionPlan(
        intensity=floor,
        scenarios=plan.scenarios,
        reasoning="{} Raised to {} after a failure.".format(
            plan.reasoning.rstrip(), floor),
        source=plan.source,
    )


def generate_tests(metrics, change_info, prioritized_tests=None,
                   risk_score=None, history=None, min_intensity=None):
    """Plan the run and write the planner-selected PyTest module.

    risk_score is the risk engine's score for these metrics. It is computed
    here when the caller has none, and never inferred from the length of the
    flagged list: that is 0 for a full regression, the riskiest case there is.
    """

    print("Generating tests...\n")

    os.makedirs(os.path.dirname(GENERATED_TEST_PATH), exist_ok=True)

    ensure_conftest()

    if risk_score is None:
        risk_score = calculate_risk_score(metrics)

    plan = plan_regression(
        metrics,
        change_info,
        risk_tests=prioritized_tests,
        risk_score=risk_score,
        history=history,
    )

    plan = raise_to(plan, min_intensity)

    intensity = plan.intensity
    scenarios = plan.scenarios
    params = enforce_safe_limits(plan.params)

    print("Planner:", plan.source)
    print("Selected intensity:", intensity, "({} packets)".format(params["packets"]))
    print("Scenarios:", scenarios or "none")
    print("Reason:", plan.reasoning)

    # ------------------------------------------------
    # BASE TEST CODE
    # ------------------------------------------------

    signal = test_signal()

    # Interpolated into the module below, so the generated file records the
    # limits the run applied rather than referring to code it cannot see.
    stack = WORKQUEUE_STACK_KB
    memory = METRIC_THRESHOLDS["memory"]
    power = METRIC_THRESHOLDS["power"]

    test_code = f"""
# Copyright (c) 2026 Prajnaana Technologies Pvt. Ltd.
# Original author: Dhanya Shree S
# SPDX-License-Identifier: MIT

import pytest
import asyncio
import os
import time
import numpy as np
import wave

# Real BLE needs time to settle after a reconnect.
SETTLE = 2.0

# Round-trip budget for the first byte back over BLE.
MAX_LATENCY_MS = 2000.0

# The firmware compressor clamps its gain to this range
# (WDRC_GAIN_FLOOR / WDRC_GAIN_CEIL in NRF_Firmware/src/dsp_wdrc.h), with
# a little slack for measurement noise.
GAIN_MIN = 0.45
GAIN_MAX = 0.95

# One BLE burst carries this many samples.
BLOCK = 2000

# How much of the input file the audio tests use. The host writes MTU-sized
# chunks a few milliseconds apart (see ble_audio.py), so a burst of this much
# audio is written in a fraction of a second and the test then waits only for
# the device to fall quiet. Override with HA_TEST_SECONDS.
USE_SAMPLES = int(float(os.getenv("HA_TEST_SECONDS", "0.125")) * 16000)

# Quiet input must receive at least this much more gain than loud input.
# The firmware range is 0.85 / 0.55 = 1.55, so 1.25 leaves margin while
# still failing a compressor that does nothing.
MIN_COMPRESSION_RATIO = 1.25

# Warm-up bursts before measuring, so the compressor reaches steady state.
SETTLE_BURSTS = 2

# The memory metric is system work-queue stack high-water, not heap: this
# firmware never allocates at run time. It is therefore bounded by the stack
# it lives in, so any limit must sit below that bound to be able to fire.
#
# The two limits below are independent. Growth is measured against this
# run's own first reading, and {memory} KiB is an absolute ceiling checked
# separately. The ceiling is calibrated against a high-water reading of
# 3.53 KiB, while the metric samples the system work queue explicitly and
# reads about 0.53 KiB, so the ceiling sits well above anything a current
# run produces and awaits recalibration.
WORKQUEUE_STACK_KB = {stack}
MAX_STACK_KB = {memory}
MAX_STACK_GROWTH_KB = 0.25

# Supply current budget while streaming, taken from
# risk_engine.METRIC_THRESHOLDS["power"], like MAX_STACK_KB above. Not every
# limit here comes from that table: WORKQUEUE_STACK_KB is
# risk_engine.WORKQUEUE_STACK_KB, a separate constant, and
# MAX_STACK_GROWTH_KB is the literal 0.25, with no entry in the table at all
# -- as are the latency, gain-range and compression-ratio limits written
# further down.
#
# Only checked when an external power analyzer supplied a reading; the
# board cannot measure its own supply.
MAX_STREAM_CURRENT_MA = {power}

TEST_SIGNAL = r"{signal}"

METRICS = {metrics}
THRESHOLDS = {THRESHOLDS}

# The intensity the planner chose. It sets how hard the stress scenarios
# push, through the arithmetic below: medium gives 12 stress streams,
# 6 power streams and 3 reconnect cycles.
INTENSITY = "{intensity}"
PACKETS = {params["packets"]}
STRESS_STREAMS = PACKETS // 4                   # low 5, medium 12, high 20
POWER_STREAMS = max(3, PACKETS // 8)            # low 3, medium 6, high 10
RECONNECT_CYCLES = max(2, PACKETS // 16)        # low 2, medium 3, high 5


def load_wav(file_path):
    # Converts to 16 kHz mono, so a 44.1 kHz stereo file works too.
    try:
        from regression.audio_prep import load_wav as prepare

        return prepare(file_path)
    except Exception as exc:
        print("WAV unusable ({{}}) -> synthetic audio".format(exc))
        t = np.linspace(0, 1, 16000)
        return 0.5 * np.sin(2 * np.pi * 440 * t)


def tone(amplitude, samples=2000, freq=440.0):
    t = np.linspace(0, 1, samples, dtype=np.float32)
    return (amplitude * np.sin(2 * np.pi * freq * t)).astype(np.float32)


def stream(ble_device, loop, audio):
    # Send audio and return what came back, as float samples in [-1, 1].
    #
    # One send_audio_stream call carries at most BLOCK samples, so longer
    # audio is sent as several bursts and stitched back together. That is
    # what lets a real audio file be used, not just a 0.125 s fragment.
    chunks = []

    for start in range(0, len(audio), BLOCK):
        block = audio[start:start + BLOCK]

        result = loop.run_until_complete(ble_device.send_audio_stream(block))

        assert result is not None, (
            "device returned no audio: the write was accepted but nothing "
            "was notified back. The device publishes the processed-audio "
            "characteristic, so this is the firmware failing to answer, not "
            "a missing capability. "
            "Run: python tools/ble_probe.py (from a source installation)"
        )

        if len(result) % 2:
            result = result[:-1]

        chunks.append(np.frombuffer(result, dtype=np.int16)
                      .astype(np.float32) / 32767.0)

    return np.concatenate(chunks) if chunks else np.array([], dtype=np.float32)


def rms(x):
    return float(np.sqrt(np.mean(x ** 2)))


def get_loop():
    # The fixture connected and subscribed on its own event loop, and bleak
    # delivers notifications to the loop that started them. Reuse that one.
    return asyncio.get_event_loop()


# Skip audio tests on firmware that publishes no processed-audio notify
# characteristic. The capability is read from the GATT table, so a working
# board with a stuck DSP still fails here rather than skipping. Probed once
# per session and cached; firmware that gains the characteristic runs these
# tests with no change needed here.
def require_loopback(ble_device, loop):
    if not loop.run_until_complete(ble_device.supports_audio_loopback()):
        pytest.skip(
            "device does not implement audio loopback -- it publishes no "
            "processed-audio notify characteristic, so it accepts the audio "
            "and notifies nothing back. "
            "Confirm with: python tools/ble_probe.py (from a source installation)"
        )


# ---------------- CONNECTION ----------------
def test_connection(ble_device):
    assert ble_device is not None
"""

    # ------------------------------------------------
    # SCENARIO TESTS
    # ------------------------------------------------
    # Sorted so one plan always produces byte-identical output. The
    # determinism KPI compares verdicts run to run, and a suite whose test
    # order moves is not comparable with itself.
    for scenario in sorted(scenarios):
        block = SCENARIO_TESTS.get(scenario)

        if block is None:
            # planner.KNOWN_SCENARIOS and SCENARIO_TESTS are held in step by
            # tests/test_scenarios.py, so reaching this means one was edited
            # without the other.
            print("  no test defined for scenario:", scenario)
            continue

        test_code += block

    # ------------------------------------------------
    # AUDIO STREAM TEST
    # ------------------------------------------------
    test_code += """
def test_audio_stream_basic(ble_device):

    loop = get_loop()

    require_loopback(ble_device, loop)

    audio = load_wav(TEST_SIGNAL)[:USE_SAMPLES]

    print("using {} samples ({:.2f}s) of the input file".format(
        len(audio), len(audio) / 16000.0))

    # First burst only, so the retries below have something small to repeat.
    result = loop.run_until_complete(
        ble_device.send_audio_stream(audio[:BLOCK])
    )

    if result is None:
        # Retry on the existing link first. The fixture is session scoped.
        print("No data back; retrying on the same connection")

        loop.run_until_complete(asyncio.sleep(SETTLE))

        result = loop.run_until_complete(
            ble_device.send_audio_stream(audio[:1000])
        )

    if result is None and not ble_device.is_connected:
        print("Link dropped; reconnecting once")

        loop.run_until_complete(ble_device.connect())
        loop.run_until_complete(asyncio.sleep(SETTLE))

        result = loop.run_until_complete(
            ble_device.send_audio_stream(audio[:1000])
        )

    assert result is not None, (
        "Device accepted audio but sent none back, although the device "
        "publishes the processed-audio characteristic. The firmware stopped "
        "answering partway. "
        "Run: python tools/ble_probe.py (from a source installation)"
    )

    # The link answers. Now send the rest of what HA_TEST_SECONDS asked for.
    # One send_audio_stream call carries at most BLOCK samples, so stream()
    # is what sends a longer selection: it splits the audio into bursts and
    # stitches the replies together.
    if len(audio) > BLOCK:
        stream(ble_device, loop, audio[BLOCK:])

    print("Audio streaming completed")

"""

    # ------------------------------------------------
    # DSP TEST
    # ------------------------------------------------
    test_code += """
def test_audio_dsp(ble_device):

    loop = get_loop()

    require_loopback(ble_device, loop)

    clean = load_wav(TEST_SIGNAL)[:USE_SAMPLES]

    # stream(), not one send_audio_stream call: a single call carries at most
    # BLOCK samples.
    processed = stream(ble_device, loop, clean)

    assert len(processed), (
        "No audio came back from the device, so the DSP cannot be checked. "
        "The device publishes the processed-audio characteristic, so this "
        "is the firmware failing to answer, not a missing capability. "
        "Run: python tools/ble_probe.py (from a source installation)"
    )

    # stream() has already dropped any odd trailing byte and scaled the
    # samples into [-1, 1], so neither is repeated here.
    assert len(processed) > 50, (
        "Device returned only {} samples, too few to analyse".format(len(processed))
    )

    # Align both ways. The device may return more or less than was sent.
    n = min(len(clean), len(processed))
    clean = clean[:n]
    processed = processed[:n]

    # DSP must modify signal
    diff = np.mean(np.abs(clean - processed))
    print("Signal difference:", diff)

    assert diff > 0.001, (
        "Device echoed audio unchanged (diff={:.6f}). The DSP ran but had "
        "no effect on the signal.".format(diff)
    )

    # SNR validation
    noise = clean - processed

    signal_power = np.mean(clean**2)
    noise_power = np.mean(noise**2)

    assert noise_power > 1e-10, "Invalid DSP"

    snr = 10 * np.log10(signal_power / noise_power)

    print("DSP SNR:", snr)

    assert 0 < snr < 50, "Suspicious DSP result"

"""

    test_code += """

# ---------------- DELAY ----------------
def test_audio_latency(ble_device):
    # How long from sending the first byte to getting the first byte back.
    #
    # This is the BLE round trip, not the hearing aid's own audio latency --
    # a real device processes locally. It is still worth tracking: a jump from
    # ~100 ms to ~900 ms means something changed in the link or the firmware.
    loop = get_loop()
    require_loopback(ble_device, loop)

    stream(ble_device, loop, tone(0.5))

    ms = ble_device.last_stream_latency_ms

    assert ms is not None, "no audio came back, so latency cannot be measured"

    print("Round-trip latency ms:", round(ms, 1))

    assert ms < MAX_LATENCY_MS, (
        "round trip took {:.0f} ms, budget is {:.0f} ms".format(ms, MAX_LATENCY_MS)
    )


# ---------------- VOLUME BEHAVIOUR ----------------
def test_compression_curve(ble_device):
    # A hearing aid must apply LESS gain to loud sound than to quiet sound.
    # test_audio_dsp only proves the signal changed; this proves it changed
    # the way a compressor should. A plain volume control would fail here.
    loop = get_loop()
    require_loopback(ble_device, loop)

    gains = {}

    for label, amplitude in (("quiet", 0.05), ("loud", 0.90)):
        sent = tone(amplitude)

        # Let the compressor settle before measuring. Its release constant is
        # 60 ms, so recovering from loud audio takes ~3250 samples -- more
        # than the 2000 one burst carries.
        for _ in range(SETTLE_BURSTS):
            stream(ble_device, loop, sent)

        got = stream(ble_device, loop, sent)

        n = min(len(sent), len(got))

        # Measure only the settled tail of the final burst.
        start = n // 2

        gains[label] = (rms(got[start:n])
                        / max(rms(sent[start:n]), 1e-9))

        print("{} input gain: {:.3f}".format(label, gains[label]))

    # A real difference, not a rounding artefact.
    assert gains["quiet"] > gains["loud"] * MIN_COMPRESSION_RATIO, (
        "quiet gain {:.3f} is not meaningfully above loud gain {:.3f} -- "
        "the compressor is applying a fixed gain, not compressing".format(
            gains["quiet"], gains["loud"])
    )

    for label, gain in gains.items():
        assert GAIN_MIN <= gain <= GAIN_MAX, (
            "{} gain {:.3f} outside the expected range "
            "{:.2f}-{:.2f}".format(label, gain, GAIN_MIN, GAIN_MAX)
        )


# ---------------- NOTHING LOST ----------------
def test_no_samples_lost(ble_device):
    # Every sample sent must come back. Dropped audio is a real defect.
    loop = get_loop()
    require_loopback(ble_device, loop)

    # Uses the chosen input file, so loss is measured on real material.
    sent = load_wav(TEST_SIGNAL)[:USE_SAMPLES]
    got = stream(ble_device, loop, sent)

    print("sent {} samples, received {}".format(len(sent), len(got)))

    assert len(got) == len(sent), (
        "device returned {} of {} samples ({} missing)".format(
            len(got), len(sent), len(sent) - len(got))
    )


# ---------------- VERY LOUD SOUND ----------------
def test_full_scale_no_clipping(ble_device):
    # Near maximum volume, 16-bit maths can overflow and wrap: a loud positive
    # sample comes back negative, which sounds catastrophic. Sign flips on
    # loud samples are the signature.
    loop = get_loop()
    require_loopback(ble_device, loop)

    sent = tone(0.99)
    got = stream(ble_device, loop, sent)

    n = min(len(sent), len(got))
    sent, got = sent[:n], got[:n]

    loud = np.abs(sent) > 0.5
    flipped = int(np.sum(np.sign(sent[loud]) != np.sign(got[loud])))
    ratio = flipped / max(1, int(loud.sum()))

    print("sign flips on loud samples: {}/{}".format(flipped, int(loud.sum())))

    assert ratio < 0.01, (
        "{:.1%} of loud samples came back with the opposite sign -- "
        "the DSP is overflowing at high volume".format(ratio)
    )
"""

    # ------------------------------------------------
    # METRIC TESTS
    # ------------------------------------------------
    for m in THRESHOLDS:
        why = "{} not measured: {}".format(
            m, UNMEASURED_WHY.get(m, "the device did not report it"))

        test_code += f"""
@pytest.mark.category("functional")
def test_{m}():
    # A metric the device does not report is absent from METRICS. Skipping
    # is the honest outcome.
    if "{m}" not in METRICS:
        pytest.skip({why!r})

    v = METRICS["{m}"]
    assert THRESHOLDS["{m}"]["min"] <= v <= THRESHOLDS["{m}"]["max"]
"""

    # ------------------------------------------------
    # VALIDATE + WRITE
    # ------------------------------------------------
    ast.parse(test_code)

    with open(GENERATED_TEST_PATH, "w", encoding="utf-8") as f:
        f.write(test_code)

    print("Tests generated:", GENERATED_TEST_PATH)

    # What this plan left out. The full suite is this module with every
    # scenario in the vocabulary added, so the comparison is against what the
    # planner could have chosen, not against an imagined larger suite.
    selected = defined_tests(test_code)
    left_out = sorted(
        name
        for scenario in KNOWN_SCENARIOS if scenario not in scenarios
        for name in defined_tests(SCENARIO_TESTS[scenario])
    )

    plan.selection = {
        "selected": len(selected),
        "full": len(selected) + len(left_out),
        "left_out": left_out,
    }

    print("Selected {} of {} tests; left out: {}".format(
        plan.selection["selected"], plan.selection["full"],
        ", ".join(left_out) or "none"))

    return plan
