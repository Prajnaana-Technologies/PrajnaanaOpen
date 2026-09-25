# Copyright (c) 2026 Prajnaana Technologies Pvt. Ltd.
# Original author: Dhanya Shree S
# SPDX-License-Identifier: MIT
#
# Licensed under the MIT License. See LICENSE in the project root.

"""Score device metrics and pick the regression slice to run.

The rule that matters here: an unmeasured metric is not a healthy metric.
Reading a missing value as 0 makes a device nobody can see look perfect, and
substituting a constant makes every build score alike. Both are treated as
risk instead, and reported separately from a real threshold breach so the
console says which of the two put the run into full regression.

Metrics arrive from regression/ble/ble_audio.py read_metrics(), which omits
what the device did not report and lists those names under "unmeasured".
"""

# ------------------------------------------------
# RISK ENGINE (Metric-Based)
# ------------------------------------------------

# Why a scored metric can be missing on this bench. The telemetry service is
# present on current firmware; power and sync are missing because nothing
# can measure them here, not because the service is absent.
UNMEASURED_WHY = {
    "power": "the board cannot measure its own supply current, so telemetry "
             "reports it as unmeasured. Connect a PPK II and set HA_POWER_MA "
             "or HA_POWER_CSV",
    "sync": "binaural sync is the timing error between a left and a right "
            "device. This bench has one board, so telemetry reports it as "
            "unmeasured",
    "memory": "the device did not report it; its firmware may predate the "
              "telemetry service",
    "retry": "the device did not report it; its firmware may predate the "
             "telemetry service",
}

# Threshold values for system metrics.
#
#   power   supply current, mA           (telemetry current_ma)
#   memory  work-queue stack, KiB        (telemetry mem_used / 1024)
#   sync    binaural sync error, ms      (telemetry sync_latency_us / 1000)
#   retry   unexpected link drops in the run (telemetry reconnects, less
#           the disconnects the host asked for)
#
# CALIBRATION STATUS, per metric. Capture a fresh baseline with
# tools/capture_baseline.py after any firmware change that touches the audio
# or BLE path.
#
#   memory  CALIBRATED against fw-2026-09-11-telemetry-v2, 24 samples idle and
#           under streaming load. Work-queue stack high-water sat flat at
#           3.53 KiB of a 4.00 KiB stack in every sample. The limit is placed
#           halfway into what is left: it fires ~240 bytes before the stack is
#           exhausted, and cannot fire on the observed plateau.
#
#           Bounded metrics need the limit inside the remaining window, not
#           above the worst case.
#
#   retry   A per-run count of UNEXPECTED link drops, not the device's
#           since-boot total. The host subtracts the disconnects it asked for
#           itself, so what remains is drops the device caused during this
#           run. That makes an absolute limit meaningful: a healthy board
#           reports 0. The value below is a first guess; confirm it once a
#           few clean runs have been recorded.
#
#   power   UNCALIBRATED. No instrument on this bench, so the metric is
#           unmeasured and the threshold is never consulted. Re-derive once a
#           PPK II is attached.
#
#   sync    UNCALIBRATED. Needs a second board; reported as unmeasured.
#
# ONE NUMBER PER METRIC. This table is the only place a limit is written.
# planner.py reads it and quotes it in the prompt it sends to the model, and
# generate_tests.THRESHOLDS derives the generated tests' pass/fail bounds
# from it, so the risk score, both planners and the verdicts all apply the
# same figures.
#
# A second copy of a limit drifts apart from this one quietly, so
# test_one_limit_per_metric asserts there cannot be one.
#
# Where a requirement states the number, the requirement is the source: it
# is the one figure a person decided rather than a default nobody revisits.
#
# power and sync are still UNCALIBRATED, and one provisional number is
# better than three. When an instrument arrives, re-derive them here; a
# deliberate warn-before-fail split can be added in one place.
METRIC_THRESHOLDS = {
    "power": 60,      # mA, uncalibrated -- REQ-PWR-001
    "memory": 3.76,   # KiB of work-queue stack, calibrated 2026-09-11
    "sync": 50,       # ms, uncalibrated -- REQ-SYN-001
    "retry": 5,       # unexpected drops per run; healthy is 0
}

# What the memory metric is bounded by, so a future calibration can tell a
# plateau from a ceiling. Matches CONFIG_SYSTEM_WORKQUEUE_STACK_SIZE.
WORKQUEUE_STACK_KB = 4.0

# Contribution to the risk score when a metric breaches, or cannot be read.
METRIC_WEIGHTS = {
    "power": 2,
    "memory": 2,
    "sync": 1,
    "retry": 1,
}

# Tests flagged by each metric.
METRIC_TESTS = {
    "power": ["battery", "power_stability"],
    "memory": ["memory_leak", "stability"],
    "sync": ["connection", "sync"],
    "retry": ["retry", "reconnect"],
}

# Score at or above which the whole suite runs, and at or above which a
# targeted slice runs.
FULL_REGRESSION_SCORE = 4
TARGETED_REGRESSION_SCORE = 2


# ------------------------------------------------
# Classify the scored metrics
# ------------------------------------------------

def classify_metrics(metrics):
    """Split the scored metrics into (breached, unknown, within_threshold).

    A metric is unknown when it is absent, None, or named in the caller's
    "unmeasured" list. Unknown metrics are returned separately so the caller
    can say why a run escalated instead of implying the device misbehaved.
    """
    declared_unknown = set(metrics.get("unmeasured") or ())

    breached = []
    unknown = []
    within = []

    for name, limit in METRIC_THRESHOLDS.items():
        value = metrics.get(name)

        if value is None or name in declared_unknown:
            unknown.append(name)
        elif value > limit:
            breached.append(name)
        else:
            within.append(name)

    return breached, unknown, within


# ------------------------------------------------
# Detect risk tests based on metrics
# ------------------------------------------------

def detect_risk_from_metrics(metrics):
    """Tests flagged by a breached metric, or by one that could not be read.

    Sorted rather than set-ordered: the same metrics must always produce the
    same list, or two runs of one build generate different suites.
    """
    breached, unknown, _ = classify_metrics(metrics)

    risk_tests = []

    for name in breached + unknown:
        risk_tests.extend(METRIC_TESTS[name])

    return sorted(set(risk_tests))


# ------------------------------------------------
# Calculate risk score from metrics
# ------------------------------------------------

def calculate_risk_score(metrics):
    """Weighted risk score. An unreadable metric weighs the same as a breach.

    Deliberately conservative: not knowing whether power is within budget is
    not evidence that it is.
    """
    breached, unknown, _ = classify_metrics(metrics)

    return sum(METRIC_WEIGHTS[name] for name in breached + unknown)


# ------------------------------------------------
# Select regression slice
# ------------------------------------------------

def select_regression_slice(metrics):
    breached, unknown, within = classify_metrics(metrics)

    risk_tests = detect_risk_from_metrics(metrics)
    risk_score = calculate_risk_score(metrics)

    print("\nWithin threshold:", ", ".join(sorted(within)) or "none")
    print("Over threshold:  ", ", ".join(sorted(breached)) or "none")

    if unknown:
        print("Not measured:    ", ", ".join(sorted(unknown)))
        print("  Scored as risk, not as healthy:")

        for name in sorted(unknown):
            print("    {}: {}".format(
                name, UNMEASURED_WHY.get(name, "the device did not report it")))

    print("\nDetected Risk Tests:", risk_tests)
    print("Calculated Risk Score:", risk_score)

    if risk_score >= FULL_REGRESSION_SCORE:

        if breached:
            print("High Risk Detected -> Running Full Regression")
        else:
            print("Too little measured to target -> Running Full Regression")

        prioritized_tests = None

    elif risk_score >= TARGETED_REGRESSION_SCORE:

        print("Medium Risk -> Running Targeted Regression")

        prioritized_tests = risk_tests

    else:

        print("Low Risk -> Running Minimal Sanity Tests")

        prioritized_tests = ["connection"]

    return prioritized_tests, risk_score
