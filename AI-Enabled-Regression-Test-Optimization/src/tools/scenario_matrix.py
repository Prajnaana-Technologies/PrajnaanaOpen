# Copyright (c) 2026 Prajnaana Technologies Pvt. Ltd.
# Original author: Dhanya Shree S
# SPDX-License-Identifier: MIT
#
# Licensed under the MIT License. See LICENSE in the project root.

"""Generate pipeline inputs that between them exercise every scenario.

Two jobs:

  * Show which inputs reach which scenarios, with randomised device metrics,
    so the selection rules are exercised across a range of risk rather than
    the one state a bench happens to be in.

  * Prove every scenario is reachable. A scenario nothing selects never
    runs, however good its test is, and nothing else in the suite says so.

    python tools/scenario_matrix.py                 # random sweep
    python tools/scenario_matrix.py --seed 7        # same sweep every time
    python tools/scenario_matrix.py --cover         # a small covering set
    python tools/scenario_matrix.py --cover --commands

--commands prints ready-to-paste orchestrator invocations.

Nothing here touches hardware: it only asks the planner what it would choose.
"""

import argparse
import contextlib
import io
import os
import random
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from regression.ai_engine.planner import (          # noqa: E402
    KNOWN_SCENARIOS,
    suggest_scenarios,
)
from regression.change_detection.risk_engine import (  # noqa: E402
    METRIC_THRESHOLDS,
    detect_risk_from_metrics,
    select_regression_slice,
)

# Change descriptions a real commit might carry. Deliberately a mix: some hit
# a keyword trigger, some hit none, because a planner that selects everything
# for every change is no better than one that selects nothing.
CHANGE_POOL = (
    "pairing and bonding rework",
    "encrypt the write characteristic",
    "bluetooth reconnection fix",
    "suspend and resume handling",
    "ring buffer overflow fix",
    "throughput tuning for the notify path",
    "memory leak in the audio work handler",
    "long soak investigation",
    "workqueue scheduling change",
    "audio DSP gain curve tweak",
    "readme typo",
    "build script cleanup",
)

# Metric ranges to draw from. Values straddle the thresholds so a sweep sees
# healthy, borderline and breaching devices rather than one fixed state.
#
# Every range therefore has to reach past its limit. Each upper bound
# derives from the limit itself, so the ranges move with the thresholds.
METRIC_RANGES = {
    "memory": (2.0, 4.0),                              # KiB, 4.00 ceiling
    "power": (20.0, METRIC_THRESHOLDS["power"] * 1.6),   # mA
    "sync": (0.0, METRIC_THRESHOLDS["sync"] * 1.6),      # ms
    "retry": (0, 9),                                   # unexpected drops
}

# How often a metric comes back unmeasured. Not rare: on this bench power and
# sync are always unmeasured, and unmeasured is scored as risk.
UNMEASURED_CHANCE = 0.35


def random_metrics(rng):
    """A device state, with some metrics randomly unreported."""
    metrics = {}
    unmeasured = []

    for name, (low, high) in METRIC_RANGES.items():
        if rng.random() < UNMEASURED_CHANCE:
            unmeasured.append(name)
            continue

        value = rng.uniform(low, high)

        metrics[name] = int(round(value)) if name == "retry" else round(value, 2)

    metrics["unmeasured"] = unmeasured

    return metrics


def evaluate(change, metrics):
    """What the risk engine and planner make of one input.

    The slice comes from select_regression_slice, which is what the pipeline
    hands the planner: None above the full-regression score, ["connection"]
    below the targeted one, and the flagged metrics only in between. This
    passed the flagged list every time, so for a score of 4 or more, or
    under 2, it printed scenarios the pipeline would not have chosen -- with
    a docstring promising it only asks the planner what it would choose.

    select_regression_slice narrates its reasoning on stdout, which is worth
    reading in a run and unreadable across four thousand candidates, so it
    is silenced here.
    """
    risk_tests = detect_risk_from_metrics(metrics)

    with contextlib.redirect_stdout(io.StringIO()):
        prioritized, risk_score = select_regression_slice(metrics)

    scenarios = suggest_scenarios(change, metrics, prioritized, risk_score)

    return {
        "change": change,
        "metrics": metrics,
        "risk_tests": risk_tests,
        "prioritized": prioritized,
        "risk_score": risk_score,
        "scenarios": scenarios,
    }


def sweep(rng, count):
    return [
        evaluate(rng.choice(CHANGE_POOL), random_metrics(rng))
        for _ in range(count)
    ]


def covering_set(rng, attempts=4000):
    """A small set of inputs that between them reach every scenario.

    Greedy: repeatedly take the candidate adding the most uncovered
    scenarios. That is not guaranteed minimal, only short -- short enough
    to actually run, which is all this is for.
    """
    candidates = [
        evaluate(change, random_metrics(rng))
        for _ in range(attempts // len(CHANGE_POOL))
        for change in CHANGE_POOL
    ]

    remaining = set(KNOWN_SCENARIOS)
    chosen = []

    while remaining:
        best = None
        best_key = None

        for candidate in candidates:
            gain = len(remaining & set(candidate["scenarios"]))

            if not gain:
                continue

            # Prefer the candidate that covers most, then the one measuring
            # most. A device reporting nothing flags every metric as risk and
            # so "covers" everything in one input -- true, but it exercises
            # the unmeasured shortcut rather than the selection rules, and
            # tells you nothing about what a working board would trigger.
            unmeasured = len(candidate["metrics"].get("unmeasured") or [])

            key = (gain, -unmeasured)

            if best_key is None or key > best_key:
                best, best_key = candidate, key

        if best is None:
            break

        chosen.append(best)
        remaining -= set(best["scenarios"])

    return chosen, remaining


def describe(entry, index=None):
    metrics = entry["metrics"]

    measured = ", ".join(
        "{}={}".format(k, v) for k, v in sorted(metrics.items())
        if k != "unmeasured"
    ) or "nothing measured"

    unmeasured = ", ".join(metrics.get("unmeasured") or []) or "none"

    label = "" if index is None else "[{}] ".format(index)

    # The flagged metrics and the slice the planner is handed are two
    # different lists: above the full-regression score the slice is None,
    # below the targeted score it is ["connection"], and neither resembles
    # what the metrics flagged. Both are shown, so the line cannot mislead.
    prioritized = entry["prioritized"]

    if prioritized is None:
        given = "everything (full regression)"
    else:
        given = ", ".join(prioritized) or "none"

    lines = [
        '{}change: "{}"'.format(label, entry["change"]),
        "    measured:   " + measured,
        "    unmeasured: " + unmeasured,
        "    risk score: {}  flagged: {}".format(
            entry["risk_score"], ", ".join(entry["risk_tests"]) or "none"),
        "    planner got: " + given,
        "    scenarios:  " + (", ".join(entry["scenarios"]) or "none"),
    ]

    return "\n".join(lines)


def as_command(entry):
    import json

    metrics = {
        k: v for k, v in entry["metrics"].items() if k != "unmeasured"
    }
    metrics["unmeasured"] = entry["metrics"].get("unmeasured", [])

    return (
        'python -m regression.ai_engine.orchestrator '
        '--change "{}" --metrics \'{}\''.format(
            entry["change"], json.dumps(metrics))
    )


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Generate planner inputs covering every scenario")
    parser.add_argument("--count", type=int, default=8,
                        help="inputs in a random sweep")
    parser.add_argument("--seed", type=int, default=None,
                        help="fix the randomness so a sweep repeats")
    parser.add_argument("--cover", action="store_true",
                        help="print a small set reaching every scenario")
    parser.add_argument("--commands", action="store_true",
                        help="print runnable orchestrator commands")

    args = parser.parse_args(argv)

    rng = random.Random(args.seed)

    if args.cover:
        entries, missing = covering_set(rng)

        print("Greedy set reaching every scenario: {} input(s)".format(
            len(entries)))
        print("=" * 70)

        for index, entry in enumerate(entries, start=1):
            print()
            print(describe(entry, index))

        reached = set()

        for entry in entries:
            reached |= set(entry["scenarios"])

        print()
        print("=" * 70)
        print("covered {} of {} scenarios".format(
            len(reached), len(KNOWN_SCENARIOS)))

        if missing:
            print("NOT REACHABLE:", ", ".join(sorted(missing)))
            print("A scenario nothing selects has never run.")

        if args.commands:
            print()
            print("Run them with:")
            print()

            for entry in entries:
                print("  " + as_command(entry))
                print()

        return 1 if missing else 0

    entries = sweep(rng, args.count)

    print("Random sweep of {} input(s){}".format(
        args.count, "" if args.seed is None else " (seed {})".format(args.seed)))
    print("=" * 70)

    for index, entry in enumerate(entries, start=1):
        print()
        print(describe(entry, index))

    reached = set()

    for entry in entries:
        reached |= set(entry["scenarios"])

    missing = sorted(set(KNOWN_SCENARIOS) - reached)

    print()
    print("=" * 70)
    print("this sweep reached {} of {} scenarios".format(
        len(reached), len(KNOWN_SCENARIOS)))

    if missing:
        print("not reached here: " + ", ".join(missing))
        print("Use --cover for a set that reaches all of them.")

    return 0


if __name__ == "__main__":
    sys.exit(main())
