# Copyright (c) 2026 Prajnaana Technologies Pvt. Ltd.
# Original author: Dhanya Shree S
# SPDX-License-Identifier: MIT
#
# Licensed under the MIT License. See LICENSE in the project root.

"""The numbers that say whether the framework works, not whether the product does.

Seven KPIs. Five carry a numeric target; test_reduction (the measure of the
optimisation itself) and subjective_stability (a direction of travel, not a
number) are tracked without one. The rule throughout: a KPI with too little data
reports INSUFFICIENT DATA and says what it needs. It never reports a number it
cannot support -- a fabricated KPI is worse than a missing one, because a
missing one gets chased.

Determinism comes first on purpose. If two runs of one build disagree, no
other measurement here means anything, because every later number is computed
over verdicts that are themselves unreliable.

    python -m regression.kpi record   <run_dir>   # after a run
    python -m regression.kpi report                # the scorecard
    python -m regression.kpi escape --id BUG-41 --severity major --release v0.3
    python -m regression.kpi effort --release v0.3 --days 1.5
"""

import json
import os
import statistics
from datetime import datetime, timezone

from regression.paths import in_base

HISTORY_PATH = os.path.join("regression", "artifacts", "kpi", "runs.json")
ESCAPES_PATH = os.path.join("regression", "artifacts", "kpi", "escapes.json")
EFFORT_PATH = os.path.join("regression", "artifacts", "kpi", "effort.json")

# ---------------------------------------------------------------------------
# Targets, from the proposal
# ---------------------------------------------------------------------------

TARGETS = {
    "determinism": {
        "target": 98.0,
        "unit": "%",
        "question": "run the same build twice -- same result?",
        "direction": "higher",
        "needs": "two runs of one identical build",
    },
    "early_detection": {
        "target": 70.0,
        "unit": "%",
        "question": "what share of defects was caught before integration?",
        "direction": "higher",
        "needs": "escape records tagged pre- and post-integration",
    },
    "cycle_predictability": {
        "target": 10.0,
        "unit": "% deviation",
        "question": "does a regression run take a consistent time?",
        "direction": "lower",
        "needs": "at least 3 recorded runs",
    },
    "escape_rate": {
        "target": 30.0,
        "unit": "% reduction",
        "question": "how many defects were found after sign-off?",
        "direction": "higher",
        "needs": "a baseline period and a current period of escape records",
    },
    "maintenance_effort": {
        "target": 0.0,
        "unit": "person-days/release",
        "question": "does keeping the suite alive grow with feature count?",
        "direction": "lower",
        "needs": "maintenance effort logged per release",
    },
    "test_reduction": {
        "target": None,
        "unit": "% of tests not run",
        "question": "how much of the full suite did the planner leave out?",
        "direction": "higher",
        "needs": "runs recorded with the planner's selection counts",
    },
    "subjective_stability": {
        "target": None,
        "unit": "trend",
        "question": "is perceptual rework increasing?",
        "direction": "lower",
        "needs": "escape records with category=subjective across 2+ releases",
    },
}

MIN_RUNS_FOR_VARIANCE = 3

# Defects the pipeline recorded by watching itself, rather than a person
# recording them. Kept distinguishable on purpose: they are machine
# observations, and early_detection says how many of its inputs came from
# here so the number can be weighed rather than taken at face value.
AUTO_SOURCE = "pipeline"
AUTO_PREFIX = "AUTO-"

# reporting.RunReport counts both of these as a failure.
FAILING_STATUSES = ("FAILED", "ERROR")


# ---------------------------------------------------------------------------
# Storage
# ---------------------------------------------------------------------------

def _load(rel_path):
    path = in_base(rel_path)

    if not os.path.exists(path):
        return []

    try:
        with open(path, encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, ValueError):
        return []

    return data if isinstance(data, list) else []


def _save(rel_path, data):
    path = in_base(rel_path)

    with open(path, "w", encoding="utf-8") as handle:
        json.dump(data, handle, indent=2, sort_keys=True)

    return path


def load_runs():
    return _load(HISTORY_PATH)


def load_escapes():
    return _load(ESCAPES_PATH)


def load_effort():
    return _load(EFFORT_PATH)


# ---------------------------------------------------------------------------
# Recording
# ---------------------------------------------------------------------------

def record_run(report, build_id="", plan=None, risk_score=None):
    """Append one run to the KPI history.

    build_id is what makes determinism measurable: two runs sharing a build_id
    are two runs of identical code, and are expected to agree.
    """
    verdicts = {record.name: record.status for record in report.records}

    # Per test, not just the total. A test that still passes but takes three
    # times as long is a regression nothing else here would notice, and a
    # total is useless for spotting it: adding a test raises the total the
    # same way slowing one down does.
    durations = {
        record.name: round(record.duration, 3) for record in report.records
    }

    entry = {
        "run_dir": os.path.basename(report.run_dir),
        "recorded_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "build_id": build_id or "unversioned",
        "status": report.status,
        "passed": report.passed,
        "failed": report.failed,
        "skipped": report.skipped,
        "duration_s": round(sum(r.duration for r in report.records), 3),
        "verdicts": verdicts,
        "durations": durations,
        "risk_score": risk_score,
        "intensity": getattr(plan, "intensity", None),
        "scenarios": list(getattr(plan, "scenarios", []) or []),
        "planner": getattr(plan, "source", None),
        "selection": dict(getattr(plan, "selection", {}) or {}),
    }

    runs = load_runs()
    runs.append(entry)
    _save(HISTORY_PATH, runs)

    return entry


def record_escape(defect_id, severity="unknown", phase="post_signoff",
                  category="functional", release="", note=""):
    """Record a defect that regression did not catch.

    phase is what makes early detection measurable:
      pre_integration  -- found by regression before integration (a success)
      post_integration -- found later in the cycle
      post_signoff     -- found after release (an escape)
    """
    entry = {
        "defect_id": defect_id,
        "severity": severity,
        "phase": phase,
        "category": category,
        "release": release,
        "note": note,
        "recorded_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }

    escapes = load_escapes()
    escapes.append(entry)
    _save(ESCAPES_PATH, escapes)

    return entry


def record_pipeline_escapes(report, build_id=""):
    """Record each failing test as a defect this run caught before integration.

    early_detection asks what share of defects was found before integration.
    A failing pipeline run is exactly that event, and the pipeline is the
    only witness to it, so the pipeline records it. Unrecorded, the KPI reads
    INSUFFICIENT DATA while the suite is demonstrably catching regressions,
    and asking a person to type in what the tool just watched happen is a
    chore that does not get done.

    Deduplicated by test name. The same defect failing on five consecutive
    runs is one defect, not five, and counting it five times would inflate
    the very number this exists to report honestly.

    These are observations, not judgements: a failure can be a flaky test or
    a bad fixture rather than a defect. Each entry carries its source so a
    person can reclassify or delete it, and a record a person wrote already
    is never overwritten by one of these.
    """
    failed = [
        record.name for record in report.records
        if record.status in FAILING_STATUSES
    ]

    if not failed:
        return []

    escapes = load_escapes()
    by_id = {e.get("defect_id"): e for e in escapes}
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    added = []

    for name in failed:
        defect_id = AUTO_PREFIX + name
        existing = by_id.get(defect_id)

        if existing is not None:
            # A person's record of the same defect wins; theirs carries a
            # severity and a release that this cannot know.
            if existing.get("source") == AUTO_SOURCE:
                existing["occurrences"] = int(existing.get("occurrences", 1)) + 1
                existing["last_seen"] = now
                existing["build_id"] = build_id or existing.get("build_id", "")

            continue

        entry = {
            "defect_id": defect_id,
            "severity": "unknown",
            "phase": "pre_integration",
            "category": "functional",
            # No release: the pipeline knows the build it ran, not the
            # release it will ship in. escape_rate and subjective_stability
            # skip records without one, which is correct -- neither counts
            # pre-integration catches anyway.
            "release": "",
            "note": "caught by {} on build {}".format(
                name, build_id or "unversioned"),
            "recorded_at": now,
            "last_seen": now,
            "occurrences": 1,
            "source": AUTO_SOURCE,
            "build_id": build_id,
        }

        escapes.append(entry)
        by_id[defect_id] = entry
        added.append(defect_id)

    _save(ESCAPES_PATH, escapes)

    return added


def record_effort(release, days, note=""):
    """Log person-days spent keeping the suite alive, for one release.

    Appends rather than replaces. Maintenance arrives in pieces -- an hour
    here, an afternoon there -- and a release total that can only be written
    once would be guessed at the end instead of recorded as it happens.

    This is the input maintenance_effort reads: without it the KPI would
    describe what to log with nothing able to log it.
    """
    entry = {
        "release": release,
        "days": float(days),
        "note": note,
        "recorded_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }

    effort = load_effort()
    effort.append(entry)
    _save(EFFORT_PATH, effort)

    return entry


# ---------------------------------------------------------------------------
# Measurement
# ---------------------------------------------------------------------------

class Result(object):
    """One KPI: either a value, or an honest statement that there is none."""

    def __init__(self, name, value=None, detail="", sample=0):
        self.name = name
        self.value = value
        self.detail = detail
        self.sample = sample

        spec = TARGETS[name]

        self.target = spec["target"]
        self.unit = spec["unit"]
        self.question = spec["question"]
        self.direction = spec["direction"]
        self.needs = spec["needs"]

    @property
    def measured(self):
        return self.value is not None

    @property
    def meets_target(self):
        if not self.measured or self.target is None:
            return None

        if self.direction == "higher":
            return self.value >= self.target

        return self.value <= self.target

    def __repr__(self):
        return "Result({!r}, {!r})".format(self.name, self.value)


def determinism(runs=None):
    """Percentage of verdicts that agreed across repeat runs of one build.

    For each build_id, compares the first recorded run with each of the
    others. A test that is SKIPPED in one run and PASSED in another counts
    as disagreement: the same code was run twice and the report came back
    different, which is what "deterministic" denies.

    That is reproducibility, not flakiness: "flaky" means one thing in this
    project and analytics.trend.flaky_tests owns the word -- a test that
    PASSED and FAILED on one build. A skip is the bench
    declining to answer, usually because an instrument was connected for one
    run and not the other, and it says nothing about the test being
    unreliable. It does lower this number, and it should.
    """
    runs = load_runs() if runs is None else runs

    by_build = {}

    for run in runs:
        by_build.setdefault(run.get("build_id", "unversioned"), []).append(run)

    agreements = 0
    comparisons = 0
    builds_compared = 0

    for build, group in by_build.items():
        if len(group) < 2:
            continue

        builds_compared += 1

        first = group[0].get("verdicts", {})

        for other in group[1:]:
            verdicts = other.get("verdicts", {})

            for name in set(first) | set(verdicts):
                comparisons += 1

                if first.get(name) == verdicts.get(name):
                    agreements += 1

    if not comparisons:
        return Result(
            "determinism",
            detail="no build has been run twice",
        )

    return Result(
        "determinism",
        value=round(100.0 * agreements / comparisons, 1),
        detail="{} build(s) run more than once, {} verdict "
               "comparisons".format(builds_compared, comparisons),
        sample=comparisons,
    )


def _test_durations(run):
    """Per-test times for one run, {} when the run did not record them.

    Runs recorded before per-test timing existed carry only a total.
    """
    durations = run.get("durations") or {}

    return {
        name: value for name, value in durations.items()
        if isinstance(value, (int, float)) and value > 0
    }


def cycle_predictability(runs=None):
    """Worst deviation from the mean run duration, as a percentage.

    Measured over the tests every run has in common, not over whole-run
    totals. A total cannot separate a slower test from an added one: when
    this suite grew from 15 tests to 18, the total alone counted the three
    new tests as the suite becoming unpredictable. analytics.trend.slowdown
    uses the same shared-tests basis, for the same reason.

    A run with no per-test breakdown cannot take part in that comparison.
    When too few runs have one, this falls back to totals and says so in the
    detail line -- a fallback nobody can see is a fabricated number.
    """
    runs = load_runs() if runs is None else runs

    usable = [
        run for run in runs
        if isinstance(run.get("duration_s"), (int, float))
        and run["duration_s"] > 0
    ]

    if len(usable) < MIN_RUNS_FOR_VARIANCE:
        return Result(
            "cycle_predictability",
            detail="{} run(s) recorded, need {}".format(
                len(usable), MIN_RUNS_FOR_VARIANCE),
        )

    per_test = [_test_durations(run) for run in usable]
    detailed = [per for per in per_test if per]
    shared = set()

    if len(detailed) >= MIN_RUNS_FOR_VARIANCE:
        shared = set(detailed[0]).intersection(*detailed[1:])

    if shared:
        durations = [sum(per[name] for name in shared) for per in detailed]
        basis = "{} test(s) common to all of them".format(len(shared))
        sample = len(detailed)
    else:
        durations = [run["duration_s"] for run in usable]
        basis = ("whole-run totals -- {} of {} runs record per-test times, "
                 "so a larger suite still reads as deviation".format(
                     len(detailed), len(usable)))
        sample = len(usable)

    mean = statistics.fmean(durations)

    if mean <= 0:
        return Result("cycle_predictability", detail="durations are all zero")

    worst = max(abs(value - mean) / mean for value in durations)

    return Result(
        "cycle_predictability",
        value=round(100.0 * worst, 1),
        detail="mean {:.1f}s across {} runs, {}".format(mean, sample, basis),
        sample=sample,
    )


def early_detection(escapes=None):
    """Share of defects caught before integration."""
    escapes = load_escapes() if escapes is None else escapes

    if not escapes:
        return Result("early_detection", detail="no defects recorded")

    early = sum(1 for e in escapes if e.get("phase") == "pre_integration")
    auto = sum(1 for e in escapes if e.get("source") == AUTO_SOURCE)

    detail = "{} of {} defects found pre-integration".format(early, len(escapes))

    if auto:
        # Pipeline-recorded defects are pre_integration by construction, so
        # they can only push this number up. Saying how many keeps a high
        # score from reading as more than it is.
        detail += " ({} recorded by the pipeline itself)".format(auto)

    return Result(
        "early_detection",
        value=round(100.0 * early / len(escapes), 1),
        detail=detail,
        sample=len(escapes),
    )


def escape_rate(escapes=None, baseline_release="", current_release=""):
    """Reduction in post-sign-off defects, current release versus baseline."""
    escapes = load_escapes() if escapes is None else escapes

    post = [e for e in escapes if e.get("phase") == "post_signoff"]

    releases = sorted({e.get("release", "") for e in post if e.get("release")})

    if len(releases) < 2:
        return Result(
            "escape_rate",
            detail="escapes recorded for {} release(s), need 2".format(
                len(releases)),
        )

    baseline_release = baseline_release or releases[0]
    current_release = current_release or releases[-1]

    baseline = sum(1 for e in post if e.get("release") == baseline_release)
    current = sum(1 for e in post if e.get("release") == current_release)

    if not baseline:
        return Result(
            "escape_rate",
            detail="baseline release {} has no escapes to improve "
                   "on".format(baseline_release),
        )

    reduction = 100.0 * (baseline - current) / baseline

    return Result(
        "escape_rate",
        value=round(reduction, 1),
        detail="{} ({}) -> {} ({})".format(
            baseline, baseline_release, current, current_release),
        sample=len(post),
    )


def effort_by_release(effort=None):
    """Total person-days per release, oldest first.

    Releases are ordered by name, the same way escape_rate and
    subjective_stability order theirs. Name order is not date order for
    v0.10 against v0.9, which is a wart the three share and should be fixed
    in one place rather than three different ways.
    """
    effort = load_effort() if effort is None else effort

    totals = {}

    for entry in effort:
        release = entry.get("release", "")
        days = entry.get("days")

        if not release or not isinstance(days, (int, float)) or days < 0:
            continue

        totals[release] = totals.get(release, 0.0) + float(days)

    return [(release, totals[release]) for release in sorted(totals)]


def maintenance_effort(effort=None):
    """Change in person-days spent maintaining the suite, across releases.

    The absolute figure says little: a large suite costs more than a small
    one, and should. The question in the target is whether that cost grows
    with the feature count, so this reports the change from the earliest
    logged release to the latest and the target of zero means flat or
    falling -- the same shape as subjective_stability.

    Nothing in a test result knows how long a person spent, so the input is
    logged by hand with 'kpi effort'. Until two releases are logged this
    stays unmeasured, and says what it is waiting for.
    """
    by_release = effort_by_release(effort)

    if len(by_release) < 2:
        return Result(
            "maintenance_effort",
            detail="effort logged for {} release(s), need 2 -- log effort "
                   "with 'kpi effort --release <name> --days <n>'".format(
                       len(by_release)),
        )

    first_release, first_days = by_release[0]
    last_release, last_days = by_release[-1]

    return Result(
        "maintenance_effort",
        value=round(last_days - first_days, 2),
        detail="{:.1f} ({}) -> {:.1f} ({}); target is flat or falling".format(
            first_days, first_release, last_days, last_release),
        sample=len(by_release),
    )


def subjective_stability(escapes=None):
    """Trend in perceptual rework across releases."""
    escapes = load_escapes() if escapes is None else escapes

    subjective = [e for e in escapes if e.get("category") == "subjective"]

    releases = sorted({e.get("release", "") for e in subjective if e.get("release")})

    if len(releases) < 2:
        return Result(
            "subjective_stability",
            detail="subjective defects recorded for {} release(s), "
                   "need 2".format(len(releases)),
        )

    first = sum(1 for e in subjective if e.get("release") == releases[0])
    last = sum(1 for e in subjective if e.get("release") == releases[-1])

    return Result(
        "subjective_stability",
        value=float(last - first),
        detail="{} ({}) -> {} ({}); target is a flat or falling trend".format(
            first, releases[0], last, releases[-1]),
        sample=len(subjective),
    )


def _mean_durations(runs):
    """Mean recorded duration per test name, over every run that timed it."""
    samples = {}

    for run in runs:
        for name, value in _test_durations(run).items():
            samples.setdefault(name, []).append(value)

    return {name: statistics.fmean(values) for name, values in samples.items()}


def test_reduction(runs=None):
    """Share of the full planner suite that runs left out, and the time saved.

    The full suite is the planner module with every scenario in the
    vocabulary included -- what the planner could have chosen, so the figure
    cannot be inflated by comparing against a suite that never existed.

    Bench time saved is estimated from how long each left-out test took in
    the runs that did include it. A test that has never run has no duration
    to borrow, and the detail line says how many of those there were rather
    than guessing one.
    """
    runs = load_runs() if runs is None else runs

    usable = [
        run for run in runs
        if (run.get("selection") or {}).get("full")
    ]

    if not usable:
        return Result("test_reduction", detail="no run records selection counts yet")

    shares = [
        100.0 * (sel["full"] - sel["selected"]) / sel["full"]
        for sel in (run["selection"] for run in usable)
    ]

    means = _mean_durations(runs)
    saved = 0.0
    untimed = set()

    for run in usable:
        for name in run["selection"].get("left_out") or []:
            if name in means:
                saved += means[name]
            else:
                untimed.add(name)

    detail = "mean over {} run(s); about {:.0f}s of bench time saved in total".format(
        len(usable), saved)

    if untimed:
        detail += "; {} left-out test(s) never ran, so their time is not counted".format(
            len(untimed))

    return Result(
        "test_reduction",
        value=round(statistics.fmean(shares), 1),
        detail=detail,
        sample=len(usable),
    )


def planner_history(runs=None, limit=5):
    """The last few runs, reduced to what a planner can use.

    Verdict maps and durations are dropped: the planner needs to know what
    failed and how hard each run pushed, not every test's timing, and a
    smaller prompt is a cheaper and more focused one.
    """
    runs = load_runs() if runs is None else runs

    return [
        {
            "build_id": run.get("build_id"),
            "status": run.get("status"),
            "intensity": run.get("intensity"),
            "scenarios": run.get("scenarios") or [],
            "failed_tests": sorted(
                name for name, status in (run.get("verdicts") or {}).items()
                if status in FAILING_STATUSES
            ),
        }
        for run in runs[-limit:]
    ]


def all_results():
    """Every KPI, measured or honestly unmeasured."""
    return [
        determinism(),
        early_detection(),
        cycle_predictability(),
        escape_rate(),
        maintenance_effort(),
        test_reduction(),
        subjective_stability(),
    ]


# ---------------------------------------------------------------------------
# Report
# ---------------------------------------------------------------------------

def format_report(results=None):
    results = all_results() if results is None else results

    lines = [
        "Framework KPIs",
        "=" * 72,
    ]

    for result in results:
        if result.measured:
            if result.meets_target is True:
                verdict = "MEETS TARGET"
            elif result.meets_target is False:
                verdict = "BELOW TARGET"
            else:
                verdict = "TRACKED"

            value = "{:.1f} {}".format(result.value, result.unit)
        else:
            verdict = "INSUFFICIENT DATA"
            value = "--"

        target = ("target {} {}".format(result.target, result.unit)
                  if result.target is not None else "no numeric target")

        lines += [
            "",
            "{:<24} {:>18}   {}".format(result.name, value, verdict),
            "  {}".format(result.question),
            "  {}".format(target),
            "  {}".format(result.detail or result.needs),
        ]

        if not result.measured:
            lines.append("  needs: {}".format(result.needs))

    measured = sum(1 for r in results if r.measured)

    lines += [
        "",
        "=" * 72,
        "{} of {} KPIs measurable from the data on hand.".format(
            measured, len(results)),
    ]

    if measured < len(results):
        lines.append(
            "The rest are not estimated. A KPI nobody can compute is a gap to "
            "close, not a number to invent."
        )

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main(argv=None):
    import argparse

    parser = argparse.ArgumentParser(description="Framework KPI scorecard")
    sub = parser.add_subparsers(dest="command")

    sub.add_parser("report", help="print the scorecard")

    record_parser = sub.add_parser("record", help="record a finished run")
    record_parser.add_argument("run_dir", help="the run directory to ingest")
    record_parser.add_argument("--build-id", default="", help="build identifier")

    escape_parser = sub.add_parser("escape", help="record a defect")
    escape_parser.add_argument("--id", required=True, dest="defect_id")
    escape_parser.add_argument("--severity", default="unknown")
    escape_parser.add_argument(
        "--phase", default="post_signoff",
        choices=("pre_integration", "post_integration", "post_signoff"))
    escape_parser.add_argument("--category", default="functional")
    # Required, not optional. escape_rate and subjective_stability both skip
    # records with no release.
    escape_parser.add_argument("--release", required=True,
                               help="release the defect was found against")
    escape_parser.add_argument("--note", default="")

    effort_parser = sub.add_parser(
        "effort", help="log person-days spent maintaining the suite")
    effort_parser.add_argument("--release", required=True)
    effort_parser.add_argument("--days", required=True, type=float)
    effort_parser.add_argument("--note", default="")

    args = parser.parse_args(argv)

    if args.command == "record":
        from regression.reporting import RunReport, parse_junit

        xml_path = os.path.join(args.run_dir, "results.xml")

        if not os.path.exists(xml_path):
            print("No results.xml in", args.run_dir)
            return 1

        report = RunReport(run_dir=args.run_dir, records=parse_junit(xml_path))
        entry = record_run(report, build_id=args.build_id)

        print("Recorded {} ({} passed, {} failed)".format(
            entry["run_dir"], entry["passed"], entry["failed"]))

        return 0

    if args.command == "escape":
        entry = record_escape(
            args.defect_id, severity=args.severity, phase=args.phase,
            category=args.category, release=args.release, note=args.note,
        )

        print("Recorded {} ({}, {})".format(
            entry["defect_id"], entry["phase"], entry["severity"]))

        return 0

    if args.command == "effort":
        entry = record_effort(args.release, args.days, note=args.note)

        print("Logged {} person-day(s) against {}".format(
            entry["days"], entry["release"]))

        return 0

    print(format_report())

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
