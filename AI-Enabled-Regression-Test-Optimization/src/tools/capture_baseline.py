# Copyright (c) 2026 Prajnaana Technologies Pvt. Ltd.
# Original author: Dhanya Shree S
# SPDX-License-Identifier: MIT
#
# Licensed under the MIT License. See LICENSE in the project root.

"""Measure what a known-good build actually does, then derive thresholds.

A threshold picked before the device reported anything is a guess. This
samples the device idle and under streaming load, records the distribution,
and proposes a limit from it.

    python tools/capture_baseline.py --cycles 10
    python tools/capture_baseline.py --cycles 10 --apply

--apply writes regression/artifacts/baselines/metrics.json and prints the
METRIC_THRESHOLDS edit to make. It does not edit risk_engine.py itself: a
threshold is a judgement about how much headroom the product needs, and that
belongs in a reviewed commit rather than in a tool's side effect.

One caveat the numbers depend on. The memory metric is stack high-water, and
k_thread_stack_space_get counts bytes never touched since boot. It therefore
only ever rises within a session and resets on reboot. A baseline is not a
spread around a mean -- it is a curve that climbs and plateaus, and the
plateau is what a threshold should sit above.
"""

import argparse
import asyncio
import json
import os
import statistics
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from regression.ble.ble_audio import RealBLEDevice          # noqa: E402
from regression.paths import in_base                        # noqa: E402

BASELINE_PATH = os.path.join(
    "regression", "artifacts", "baselines", "metrics.json")

# Headroom above the observed worst case. A threshold at exactly the plateau
# fires on noise; too far above it never fires at all.
HEADROOM_FACTOR = 1.15

# Metrics with a physical ceiling, and what that ceiling is. Headroom above
# the observed maximum is the wrong rule for these: work-queue stack plateaus
# at 3.53 of 4.00 KiB, and max * 1.15 lands at 4.06 -- above the ceiling, so
# the threshold could never fire. For these the limit is placed inside the
# remaining window instead.
CEILINGS = {
    "memory": 4096 / 1024.0,   # CONFIG_SYSTEM_WORKQUEUE_STACK_SIZE, in KiB
}

# How far into the window between plateau and ceiling to place the limit.
# 0.5 gives equal warning distance and false-positive margin.
CEILING_FRACTION = 0.5

# Metrics this tool reports but proposes no threshold for. retry is now a
# per-run count of unexpected link drops rather than the device's since-boot
# total, so an absolute limit is meaningful -- but the right limit is 0 drops
# on a healthy board, not a number derived from whatever this bench's radio
# did today. risk_engine.METRIC_THRESHOLDS holds it instead.
NO_THRESHOLD = ("retry",)

# Metrics worth a baseline. Anything the device does not report is skipped
# rather than defaulted.
TRACKED = ("memory", "power", "sync", "retry")


def tone(amplitude=0.5, samples=2000, freq=440.0):
    import numpy as np

    t = np.linspace(0, 1, samples, dtype=np.float32)

    return (amplitude * np.sin(2 * np.pi * freq * t)).astype(np.float32)


async def sample(cycles, stream_audio):
    """Read metrics `cycles` times, optionally streaming between reads."""
    device = RealBLEDevice()

    await device.connect()

    readings = []

    try:
        audio = tone() if stream_audio else None

        for index in range(cycles):
            if stream_audio:
                await device.send_audio_stream(audio)

            metrics = await device.read_metrics()

            readings.append(metrics)

            shown = {
                key: metrics[key] for key in TRACKED if key in metrics
            }

            print("  cycle {:>2}: {}".format(index + 1, shown))

    finally:
        await device.disconnect()

    return readings


def summarise(readings):
    """Per-metric statistics across the readings that actually measured it."""
    summary = {}

    for name in TRACKED:
        values = [
            r[name] for r in readings
            if isinstance(r.get(name), (int, float))
        ]

        if not values:
            summary[name] = {"measured": False, "samples": 0}
            continue

        summary[name] = {
            "measured": True,
            "samples": len(values),
            "min": round(min(values), 3),
            "mean": round(statistics.fmean(values), 3),
            "max": round(max(values), 3),
            "first": round(values[0], 3),
            "last": round(values[-1], 3),
            "rising": values[-1] > values[0],
        }

    return summary


def propose(summary):
    """A threshold per measured metric, or None where one cannot be derived."""
    proposed = {}

    for name, stats in summary.items():
        if not stats.get("measured"):
            continue

        if name in NO_THRESHOLD:
            # A bench capture of link drops measures this bench's radio, not
            # the firmware; the limit for these lives in risk_engine.py.
            proposed[name] = None
            continue

        observed = stats["max"]
        ceiling = CEILINGS.get(name)

        if ceiling is None:
            proposed[name] = round(observed * HEADROOM_FACTOR, 1)
            continue

        with_headroom = observed * HEADROOM_FACTOR

        if with_headroom < ceiling:
            proposed[name] = round(with_headroom, 1)
        else:
            # Headroom would overshoot the ceiling. Place the limit partway
            # through what is left instead, so it can actually fire.
            proposed[name] = round(
                observed + (ceiling - observed) * CEILING_FRACTION, 2)

    return proposed


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Capture a metric baseline from a known-good build")
    parser.add_argument("--cycles", type=int, default=10,
                        help="samples per phase")
    parser.add_argument("--build-id", default="",
                        help="firmware identifier this baseline describes")
    parser.add_argument("--apply", action="store_true",
                        help="write the baseline file")

    args = parser.parse_args(argv)

    print("Idle phase ({} samples, no streaming)".format(args.cycles))
    idle = asyncio.run(sample(args.cycles, stream_audio=False))

    print("\nLoaded phase ({} samples, streaming between reads)".format(
        args.cycles))
    loaded = asyncio.run(sample(args.cycles, stream_audio=True))

    idle_summary = summarise(idle)
    loaded_summary = summarise(loaded)

    combined = summarise(idle + loaded)
    proposed = propose(combined)

    print("\n" + "=" * 68)
    print("Baseline across {} samples".format(len(idle) + len(loaded)))
    print("=" * 68)

    for name in TRACKED:
        stats = combined[name]

        if not stats.get("measured"):
            print("\n{:<10} not measured by this device".format(name))
            continue

        print("\n{:<10} min {:>8}   mean {:>8}   max {:>8}".format(
            name, stats["min"], stats["mean"], stats["max"]))
        print("{:<10} idle max {:>8}   loaded max {:>8}".format(
            "", idle_summary[name].get("max", "-"),
            loaded_summary[name].get("max", "-")))
        print("{:<10} {} -> {} across the run{}".format(
            "", stats["first"], stats["last"],
            "  (still rising: sample longer)" if stats["rising"] else
            "  (plateaued)"))
        if proposed[name] is None:
            print("{:<10} no threshold proposed: a bench capture of this"
                  .format(""))
            print("{:<10} measures the bench, not the firmware. See"
                  .format(""))
            print("{:<10} risk_engine.METRIC_THRESHOLDS."
                  .format(""))
        else:
            ceiling = CEILINGS.get(name)

            note = ("  (ceiling {:.2f}, {} bytes of warning)".format(
                ceiling, int((ceiling - proposed[name]) * 1024))
                if ceiling else "")

            print("{:<10} proposed threshold: {}{}".format(
                "", proposed[name], note))

    if not args.apply:
        print("\nNothing written. Re-run with --apply to save the baseline.")
        return 0

    document = {
        "build_id": args.build_id or "unversioned",
        "cycles_per_phase": args.cycles,
        "headroom_factor": HEADROOM_FACTOR,
        "idle": idle_summary,
        "loaded": loaded_summary,
        "combined": combined,
        "proposed_thresholds": proposed,
    }

    path = in_base(BASELINE_PATH)

    with open(path, "w", encoding="utf-8") as handle:
        json.dump(document, handle, indent=2, sort_keys=True)

    print("\nWrote", path)
    print("\nEdit METRIC_THRESHOLDS in "
          "regression/change_detection/risk_engine.py:")

    for name, value in sorted(proposed.items()):
        if value is None:
            print('    # "{}": left as is -- see the note above'.format(name))
        else:
            print('    "{}": {},'.format(name, value))

    return 0


if __name__ == "__main__":
    sys.exit(main())
