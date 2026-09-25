# Copyright (c) 2026 Prajnaana Technologies Pvt. Ltd.
# Original author: Dhanya Shree S
# SPDX-License-Identifier: MIT
#
# Licensed under the MIT License. See LICENSE in the project root.

"""Build-to-build comparison and trend detection.

A test run tells you the state today. This tells you the direction, which is
the question release decisions are actually made on.

The failure it catches: every build passes, each one two per cent slower than
the last, and twenty builds later the suite takes twice as long with nobody
able to point at when it changed. Individual runs cannot show that; only a
series can.

Reads the run history that regression.kpi records, so nothing extra has to be
collected.

    python -m regression.analytics.trend
    python -m regression.analytics.trend --test test_audio_latency
"""

from regression import kpi

# A duration change smaller than this is noise on a BLE bench, where timing
# varies with radio conditions run to run.
SLOWDOWN_THRESHOLD_PCT = 25.0

# Below this many runs, a trend line is an illusion.
MIN_RUNS_FOR_TREND = 3

# How much slower one test has to get before it is worth reporting. BLE
# timing varies run to run, so a small rise is noise.
TEST_SLOWDOWN_PCT = 50.0

# Tests quicker than this are dominated by fixed overhead, where a
# percentage means nothing -- 0.01s to 0.03s is 200% and says nothing.
MIN_DURATION_S = 0.5

FAILING = ("FAILED", "ERROR")

# Verdicts that represent a real opinion about the code. Only disagreement
# between these two is flakiness: the test ran twice on one build and
# reached opposite conclusions.
DECIDED = ("PASSED", "FAILED")


def _verdicts(run):
    return run.get("verdicts", {}) or {}


def _durations(run):
    return run.get("durations", {}) or {}


def compare(previous, current):
    """What changed between two runs.

    Returns a dict of lists. The important one is newly_failing: a test that
    passed on the last build and fails on this one points at the change
    between them, which is the whole purpose of regression testing.
    """
    before = _verdicts(previous)
    after = _verdicts(current)

    newly_failing = []
    newly_passing = []
    still_failing = []
    disappeared = []
    added = []

    for name, status in sorted(after.items()):
        was = before.get(name)

        if was is None:
            added.append(name)
            continue

        if status in FAILING and was not in FAILING:
            newly_failing.append(name)
        elif status not in FAILING and was in FAILING:
            newly_passing.append(name)
        elif status in FAILING and was in FAILING:
            still_failing.append(name)

    for name in sorted(before):
        if name not in after:
            disappeared.append(name)

    return {
        "newly_failing": newly_failing,
        "newly_passing": newly_passing,
        "still_failing": still_failing,
        "added": added,
        "disappeared": disappeared,
    }


def duration_trend(runs=None):
    """Total run duration over time, oldest first."""
    runs = kpi.load_runs() if runs is None else runs

    return [
        (run.get("run_dir", "?"), run.get("duration_s", 0.0))
        for run in runs
        if isinstance(run.get("duration_s"), (int, float))
    ]


def slowdown(runs=None, threshold=SLOWDOWN_THRESHOLD_PCT):
    """Percentage change from the first recorded run to the latest.

    Returns None when there is not enough history to say anything. Comparing
    the ends of the series rather than adjacent runs is deliberate: a creep
    of two per cent a build is invisible between neighbours and obvious
    across twenty.
    """
    runs = kpi.load_runs() if runs is None else runs

    series = duration_trend(runs)

    if len(series) < MIN_RUNS_FOR_TREND:
        return None

    # Only tests present in both endpoints. Comparing whole-run totals across
    # suites of different size reports an added test as a slowdown.
    first_durations = _durations(runs[0])
    last_durations = _durations(runs[-1])

    shared = set(first_durations) & set(last_durations)

    if shared:
        first = sum(first_durations[n] for n in shared)
        last = sum(last_durations[n] for n in shared)
    else:
        first = series[0][1]
        last = series[-1][1]

    if first <= 0:
        return None

    change = 100.0 * (last - first) / first

    return {
        "change_pct": round(change, 1),
        "first_s": round(first, 2),
        "last_s": round(last, 2),
        "runs": len(series),
        "regressed": change > threshold,
    }


def _verdicts_by_build(runs):
    """{(build, test): {verdict: how many times}}.

    Counts, not a set. "PASSED/FAILED" tells you a test disagreed with
    itself; "passed 5 times, failed once" tells you whether to suspect the
    test or the bench, which is the question people actually have.
    """
    grouped = {}

    for run in runs:
        build = run.get("build_id", "unversioned")

        for name, status in _verdicts(run).items():
            tally = grouped.setdefault((build, name), {})
            tally[status] = tally.get(status, 0) + 1

    return grouped


def flaky_tests(runs=None):
    """Tests that both PASSED and FAILED on one build.

    Only decided verdicts count. A test that passed once and SKIPPED another
    time did not disagree with itself -- it ran once and declined once, which
    on this bench usually means an instrument was connected for one run and
    not the other. An ERROR is the test failing to run at all, which is a
    harness problem rather than the test being unreliable.

    Counting all three together buries the one signal that matters: a test
    that ran twice on identical firmware and reached opposite conclusions.
    That is what "flaky" means in this project, and this is the only place
    that decides it. kpi.determinism counts a skip against reproducibility,
    which is a different question and is not called flakiness there.
    """
    runs = kpi.load_runs() if runs is None else runs

    flaky = {}

    for (build, name), tally in _verdicts_by_build(runs).items():
        decided = {s: n for s, n in tally.items() if s in DECIDED}

        if len(decided) > 1:
            flaky.setdefault(name, []).append((build, decided))

    return flaky


def inconsistent_tests(runs=None):
    """Tests whose verdict varied without ever contradicting itself.

    Reported separately because the cause is usually environmental -- an
    instrument attached for one run, a device that could not be reached --
    and calling that flakiness sends people hunting a test bug that is not
    there.
    """
    runs = kpi.load_runs() if runs is None else runs

    inconsistent = {}

    for (build, name), tally in _verdicts_by_build(runs).items():
        if len(tally) < 2:
            continue

        if len([s for s in tally if s in DECIDED]) > 1:
            continue        # that is flakiness, reported above

        inconsistent.setdefault(name, []).append((build, dict(tally)))

    return inconsistent


def slower_tests(runs=None, threshold=TEST_SLOWDOWN_PCT):
    """Tests that still pass but have become materially slower.

    This is the gap between "passed" and "fine". A test whose assertion still
    holds while it takes three times as long is a regression, and nothing
    else here reports it: the verdict says PASSED and the run total cannot
    separate a slower test from an added one.

    Each test is compared with its own earlier runs, so adding tests to the
    suite never looks like a slowdown.
    """
    runs = kpi.load_runs() if runs is None else runs

    if len(runs) < 2:
        return {}

    latest = _durations(runs[-1])

    history = {}

    for run in runs[:-1]:
        for name, seconds in _durations(run).items():
            if isinstance(seconds, (int, float)) and seconds > 0:
                history.setdefault(name, []).append(seconds)

    slower = {}

    for name, now in sorted(latest.items()):
        earlier = history.get(name)

        if not earlier or not isinstance(now, (int, float)):
            continue

        # Median, so one bad run does not set the baseline.
        ordered = sorted(earlier)
        baseline = ordered[len(ordered) // 2]

        if baseline < MIN_DURATION_S or now < MIN_DURATION_S:
            continue

        change = 100.0 * (now - baseline) / baseline

        if change >= threshold:
            slower[name] = {
                "was": round(baseline, 2),
                "now": round(now, 2),
                "change_pct": round(change, 1),
                "samples": len(earlier),
            }

    return slower


def test_history(test_name, runs=None):
    """Every recorded verdict for one test, oldest first."""
    runs = kpi.load_runs() if runs is None else runs

    history = []

    for run in runs:
        status = _verdicts(run).get(test_name)

        if status is not None:
            history.append({
                "run": run.get("run_dir", "?"),
                "build_id": run.get("build_id", "unversioned"),
                "status": status,
            })

    return history


def latest_comparison(runs=None):
    """Compare the two most recent runs, or None if there are fewer than two."""
    runs = kpi.load_runs() if runs is None else runs

    if len(runs) < 2:
        return None

    return compare(runs[-2], runs[-1])


# ---------------------------------------------------------------------------
# Report
# ---------------------------------------------------------------------------

def _tally_text(tally):
    """'passed 5 times, failed once' -- readable without a legend."""
    words = {
        "PASSED": "passed", "FAILED": "failed",
        "ERROR": "could not run", "SKIPPED": "skipped",
    }

    parts = []

    for status, count in sorted(tally.items(), key=lambda kv: -kv[1]):
        times = "once" if count == 1 else "{} times".format(count)

        parts.append("{} {}".format(words.get(status, status.lower()), times))

    return ", ".join(parts)

def format_report(runs=None):
    runs = kpi.load_runs() if runs is None else runs

    if not runs:
        return ("No run history yet.\n"
                "Record a run with: python -m regression.kpi record <run_dir>")

    lines = [
        "Trend across {} recorded run(s)".format(len(runs)),
        "=" * 66,
    ]

    change = latest_comparison(runs)

    if change is None:
        lines += ["", "Only one run recorded; nothing to compare against yet."]
    else:
        lines += ["", "Since the previous run:"]

        if change["newly_failing"]:
            lines.append("  REGRESSED  " + ", ".join(change["newly_failing"]))

        if change["newly_passing"]:
            lines.append("  fixed      " + ", ".join(change["newly_passing"]))

        if change["still_failing"]:
            lines.append("  still red  " + ", ".join(change["still_failing"]))

        if change["added"]:
            lines.append("  new        " + ", ".join(change["added"]))

        if change["disappeared"]:
            lines.append("  gone       " + ", ".join(change["disappeared"]))

        if not any(change[key] for key in change):
            lines.append("  no change")

    pace = slowdown(runs)

    lines += ["", "Duration:"]

    if pace is None:
        lines.append("  need {} runs to show a trend, have {}".format(
            MIN_RUNS_FOR_TREND, len(runs)))
    else:
        lines.append("  {:.2f}s -> {:.2f}s across {} runs ({:+.1f}%)".format(
            pace["first_s"], pace["last_s"], pace["runs"], pace["change_pct"]))

        if pace["regressed"]:
            lines.append("  SLOWER by more than {:.0f}% -- this is the creep "
                         "that no single run shows.".format(
                             SLOWDOWN_THRESHOLD_PCT))

    slower = slower_tests(runs)

    lines += ["", "Passed but slower:"]

    if not slower:
        lines.append("  none")
    else:
        lines.append("  Still passing, so the verdict says nothing is wrong.")
        lines.append("  Compared with each test's own earlier runs:")

        for name, info in sorted(slower.items()):
            lines.append("    {} -- {:.2f}s -> {:.2f}s ({:+.0f}%, {} earlier "
                         "run(s))".format(
                             name, info["was"], info["now"],
                             info["change_pct"], info["samples"]))

    flaky = flaky_tests(runs)

    lines += ["", "Flaky tests (passed and failed on one build):"]

    if not flaky:
        lines.append("  none detected")
    else:
        lines.append("  The same firmware was tested more than once and this")
        lines.append("  test gave different answers. Nothing on the board")
        lines.append("  changed between those runs.")

        for name, occurrences in sorted(flaky.items()):
            lines.append("")
            lines.append("    " + name)

            for build, tally in occurrences:
                lines.append("      on firmware {}".format(build))
                lines.append("        {}".format(_tally_text(tally)))

    inconsistent = inconsistent_tests(runs)

    if inconsistent:
        lines += ["", "Ran inconsistently (not flaky):"]
        lines.append("  These varied without ever contradicting themselves.")
        lines.append("  Usually an instrument connected for one run and not")
        lines.append("  the other, or a board that could not be reached.")

        for name, occurrences in sorted(inconsistent.items()):
            lines.append("")
            lines.append("    " + name)

            for build, tally in occurrences:
                lines.append("      on firmware {}".format(build))
                lines.append("        {}".format(_tally_text(tally)))

    return "\n".join(lines)


def main(argv=None):
    import argparse

    parser = argparse.ArgumentParser(description="Build-to-build trend analysis")
    parser.add_argument("--test", default="", help="history for one test")

    args = parser.parse_args(argv)

    if args.test:
        history = test_history(args.test)

        if not history:
            print("No recorded runs include", args.test)
            return 1

        print("History for {}:".format(args.test))

        for entry in history:
            print("  {:<24} {:<16} {}".format(
                entry["run"], entry["build_id"], entry["status"]))

        return 0

    print(format_report())

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
