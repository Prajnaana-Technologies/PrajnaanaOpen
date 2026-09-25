# Copyright (c) 2026 Prajnaana Technologies Pvt. Ltd.
# Original author: Dhanya Shree S
# SPDX-License-Identifier: MIT
#
# Licensed under the MIT License. See LICENSE in the project root.

"""Tests for the KPI scorecard.

The rule these enforce: a KPI with too little data reports INSUFFICIENT DATA.
A fabricated number is worse than a missing one, because a missing one gets
chased and a fabricated one gets quoted.
"""

import pytest

from regression import kpi
from regression.analytics import trend


def run(build_id, verdicts, duration=10.0, run_dir="build_1"):
    return {
        "run_dir": run_dir,
        "build_id": build_id,
        "verdicts": verdicts,
        "duration_s": duration,
        "status": "pass",
        "passed": len(verdicts),
        "failed": 0,
        "skipped": 0,
    }


# --------------------------------------------------------------------------
# Nothing measured means nothing claimed
# --------------------------------------------------------------------------

def test_every_kpi_is_unmeasured_with_no_data():
    results = [
        kpi.determinism([]),
        kpi.cycle_predictability([]),
        kpi.early_detection([]),
        kpi.escape_rate([]),
        kpi.test_reduction([]),
        kpi.subjective_stability([]),
    ]

    for result in results:
        assert not result.measured, result.name
        assert result.needs


def test_maintenance_effort_is_honest_about_not_being_derivable():
    """Nothing in a test result knows how long a person spent on the suite."""
    result = kpi.maintenance_effort([])

    assert not result.measured
    assert "log effort" in result.detail


def test_maintenance_effort_measures_once_two_releases_are_logged():
    """Two logged releases are enough to report a trend, so the KPI is
    measurable rather than a stub no amount of logged effort could feed."""
    effort = [
        {"release": "v0.1", "days": 1.0},
        {"release": "v0.1", "days": 0.5},
        {"release": "v0.2", "days": 2.0},
    ]

    result = kpi.maintenance_effort(effort)

    assert result.measured
    assert result.value == 0.5          # 2.0 against 1.5
    assert result.meets_target is False  # growing, and the target is flat


def test_effort_for_one_release_totals_the_pieces():
    """Maintenance arrives in pieces; a release total is their sum."""
    effort = [
        {"release": "v0.1", "days": 1.0},
        {"release": "v0.1", "days": 0.5},
    ]

    assert kpi.effort_by_release(effort) == [("v0.1", 1.5)]


def test_effort_without_a_release_is_not_counted():
    effort = [{"release": "", "days": 3.0}, {"release": "v0.1", "days": 1.0}]

    assert kpi.effort_by_release(effort) == [("v0.1", 1.0)]


def test_falling_effort_meets_the_target():
    effort = [{"release": "v0.1", "days": 4.0}, {"release": "v0.2", "days": 1.0}]

    result = kpi.maintenance_effort(effort)

    assert result.value == -3.0
    assert result.meets_target is True


def test_an_escape_cannot_be_recorded_without_a_release():
    """Unlabelled escapes are counted by nothing and explain nothing."""
    with pytest.raises(SystemExit):
        kpi.main(["escape", "--id", "BUG-1"])


# --------------------------------------------------------------------------
# Defects the pipeline records by watching itself
# --------------------------------------------------------------------------

def report_with(**statuses):
    """A RunReport carrying one record per name=status pair."""
    from regression.reporting import RunReport, TestRecord

    return RunReport(
        run_dir="build_x",
        records=[TestRecord(name=n, status=s) for n, s in statuses.items()],
    )


def test_a_failing_run_records_the_defect_it_caught(tmp_path, monkeypatch):
    """A failing run is the only witness to a defect the suite caught. Unless
    it writes one down the KPI reads INSUFFICIENT DATA while the suite is
    catching things."""
    monkeypatch.setattr(kpi, "load_escapes", lambda: [])
    saved = {}
    monkeypatch.setattr(kpi, "_save", lambda path, data: saved.update(rows=data))

    added = kpi.record_pipeline_escapes(
        report_with(test_compression_curve="FAILED", test_connection="PASSED"),
        build_id="fw-1",
    )

    assert added == ["AUTO-test_compression_curve"]

    entry = saved["rows"][0]
    assert entry["phase"] == "pre_integration"
    assert entry["source"] == kpi.AUTO_SOURCE
    assert "fw-1" in entry["note"]


def test_a_passing_run_records_nothing():
    assert kpi.record_pipeline_escapes(report_with(test_a="PASSED")) == []


def test_the_same_defect_twice_is_one_defect(monkeypatch):
    """Counting five runs of one bug as five defects would inflate the very
    number this is meant to report honestly."""
    rows = [{
        "defect_id": "AUTO-test_x", "phase": "pre_integration",
        "source": kpi.AUTO_SOURCE, "occurrences": 1,
    }]
    monkeypatch.setattr(kpi, "load_escapes", lambda: rows)
    monkeypatch.setattr(kpi, "_save", lambda path, data: None)

    added = kpi.record_pipeline_escapes(report_with(test_x="FAILED"))

    assert added == []
    assert rows[0]["occurrences"] == 2


def test_a_person_s_record_is_never_overwritten(monkeypatch):
    rows = [{"defect_id": "AUTO-test_x", "phase": "post_signoff",
             "severity": "critical"}]
    monkeypatch.setattr(kpi, "load_escapes", lambda: rows)
    monkeypatch.setattr(kpi, "_save", lambda path, data: None)

    kpi.record_pipeline_escapes(report_with(test_x="FAILED"))

    assert rows[0]["phase"] == "post_signoff"
    assert rows[0]["severity"] == "critical"
    assert "occurrences" not in rows[0]


def test_an_error_counts_as_a_defect_like_a_failure(monkeypatch):
    monkeypatch.setattr(kpi, "load_escapes", lambda: [])
    monkeypatch.setattr(kpi, "_save", lambda path, data: None)

    assert kpi.record_pipeline_escapes(report_with(test_x="ERROR")) == [
        "AUTO-test_x"]


def test_early_detection_says_how_much_it_recorded_itself():
    """Pipeline defects are pre-integration by construction, so they can only
    push this number up. The reader is told how many."""
    escapes = [
        {"phase": "pre_integration", "source": kpi.AUTO_SOURCE},
        {"phase": "post_signoff"},
    ]

    result = kpi.early_detection(escapes)

    assert result.value == 50.0
    assert "1 recorded by the pipeline itself" in result.detail


def test_the_report_says_insufficient_rather_than_estimating():
    body = kpi.format_report([kpi.determinism([]), kpi.cycle_predictability([])])

    assert "INSUFFICIENT DATA" in body
    assert "not estimated" in body


# --------------------------------------------------------------------------
# Determinism
# --------------------------------------------------------------------------

def test_one_run_of_a_build_cannot_measure_determinism():
    result = kpi.determinism([run("abc", {"test_a": "PASSED"})])

    assert not result.measured
    assert "twice" in result.detail


def test_two_identical_runs_are_fully_deterministic():
    runs = [
        run("abc", {"test_a": "PASSED", "test_b": "PASSED"}),
        run("abc", {"test_a": "PASSED", "test_b": "PASSED"}),
    ]

    assert kpi.determinism(runs).value == 100.0


def test_a_disagreeing_verdict_lowers_determinism():
    runs = [
        run("abc", {"test_a": "PASSED", "test_b": "PASSED"}),
        run("abc", {"test_a": "PASSED", "test_b": "FAILED"}),
    ]

    assert kpi.determinism(runs).value == 50.0


def test_a_skip_disagreeing_with_a_pass_is_not_reproducible():
    """Two runs of one build, two different reports: not deterministic.

    Not "flaky", though. That word means one thing in this project --
    analytics.trend.flaky_tests, a test that PASSED and FAILED on the same
    build -- and a skip disagreeing with a pass is not that.
    """
    runs = [
        run("abc", {"test_a": "PASSED"}),
        run("abc", {"test_a": "SKIPPED"}),
    ]

    assert kpi.determinism(runs).value == 0.0


def test_different_builds_are_not_compared():
    """Two builds disagreeing is a regression, not non-determinism."""
    runs = [
        run("abc", {"test_a": "PASSED"}),
        run("def", {"test_a": "FAILED"}),
    ]

    assert not kpi.determinism(runs).measured


def test_determinism_target_is_checked():
    runs = [
        run("abc", {"test_a": "PASSED"}),
        run("abc", {"test_a": "PASSED"}),
    ]

    assert kpi.determinism(runs).meets_target is True


# --------------------------------------------------------------------------
# Cycle predictability
# --------------------------------------------------------------------------

def test_two_runs_are_not_enough_for_variance():
    runs = [run("a", {}, duration=10.0), run("b", {}, duration=11.0)]

    assert not kpi.cycle_predictability(runs).measured


def test_steady_durations_are_predictable():
    runs = [run(str(i), {}, duration=10.0) for i in range(4)]

    result = kpi.cycle_predictability(runs)

    assert result.value == 0.0
    assert result.meets_target is True


def test_a_wild_run_breaks_predictability():
    runs = [run("a", {}, duration=10.0), run("b", {}, duration=10.0),
            run("c", {}, duration=40.0)]

    result = kpi.cycle_predictability(runs)

    assert result.value > kpi.TARGETS["cycle_predictability"]["target"]
    assert result.meets_target is False


def test_a_grown_suite_is_not_called_unpredictable():
    """Deviation is measured over the tests two runs share.

    Two steady runs, then one with a test added. On whole-run totals the
    addition alone reads as a 67% deviation; on the shared tests it is zero.
    """
    runs = [
        timed("a", {"t1": 10.0, "t2": 10.0}),
        timed("b", {"t1": 10.0, "t2": 10.0}),
        timed("c", {"t1": 10.0, "t2": 10.0, "t3": 30.0}),
    ]

    result = kpi.cycle_predictability(runs)

    assert result.value == 0.0
    assert result.meets_target is True


def test_a_shared_test_that_slows_is_still_caught():
    """Ignoring added tests must not mean ignoring real drift."""
    runs = [
        timed("a", {"t1": 10.0}),
        timed("b", {"t1": 10.0}),
        timed("c", {"t1": 40.0, "t2": 5.0}),
    ]

    result = kpi.cycle_predictability(runs)

    assert result.meets_target is False


def test_runs_without_per_test_times_fall_back_and_say_so():
    """History recorded without per-test times cannot use that basis."""
    runs = [run(str(i), {}, duration=10.0) for i in range(3)]

    result = kpi.cycle_predictability(runs)

    assert result.measured
    assert "whole-run totals" in result.detail


# --------------------------------------------------------------------------
# Defect-based KPIs
# --------------------------------------------------------------------------

def test_early_detection_counts_pre_integration_finds():
    escapes = [
        {"phase": "pre_integration"},
        {"phase": "pre_integration"},
        {"phase": "post_signoff"},
        {"phase": "post_integration"},
    ]

    assert kpi.early_detection(escapes).value == 50.0


def test_escape_rate_needs_two_releases():
    escapes = [{"phase": "post_signoff", "release": "1.0"}]

    assert not kpi.escape_rate(escapes).measured


def test_escape_rate_measures_the_reduction():
    escapes = (
        [{"phase": "post_signoff", "release": "1.0"}] * 10
        + [{"phase": "post_signoff", "release": "2.0"}] * 6
    )

    assert kpi.escape_rate(escapes).value == 40.0


def test_subjective_stability_reports_a_direction_not_a_pass():
    escapes = [
        {"phase": "post_signoff", "category": "subjective", "release": "1.0"},
        {"phase": "post_signoff", "category": "subjective", "release": "2.0"},
        {"phase": "post_signoff", "category": "subjective", "release": "2.0"},
    ]

    result = kpi.subjective_stability(escapes)

    assert result.value == 1.0
    assert result.target is None
    assert result.meets_target is None


# --------------------------------------------------------------------------
# Trend analysis
# --------------------------------------------------------------------------

def test_a_newly_failing_test_is_the_headline():
    previous = run("a", {"test_x": "PASSED", "test_y": "PASSED"})
    current = run("b", {"test_x": "PASSED", "test_y": "FAILED"})

    change = trend.compare(previous, current)

    assert change["newly_failing"] == ["test_y"]
    assert change["newly_passing"] == []


def test_a_fixed_test_is_reported_separately():
    previous = run("a", {"test_x": "FAILED"})
    current = run("b", {"test_x": "PASSED"})

    assert trend.compare(previous, current)["newly_passing"] == ["test_x"]


def test_added_and_removed_tests_are_not_regressions():
    previous = run("a", {"test_old": "PASSED"})
    current = run("b", {"test_new": "PASSED"})

    change = trend.compare(previous, current)

    assert change["added"] == ["test_new"]
    assert change["disappeared"] == ["test_old"]
    assert change["newly_failing"] == []


def test_slow_creep_is_visible_across_the_series():
    """No adjacent pair shows it; the ends of the series do."""
    runs = [run(str(i), {}, duration=10.0 + i) for i in range(12)]

    pace = trend.slowdown(runs)

    assert pace["regressed"] is True
    assert pace["change_pct"] > 100.0 - 1


def test_a_steady_suite_is_not_flagged():
    runs = [run(str(i), {}, duration=10.0) for i in range(5)]

    assert trend.slowdown(runs)["regressed"] is False


def test_flaky_tests_are_named():
    runs = [
        run("abc", {"test_a": "PASSED"}),
        run("abc", {"test_a": "FAILED"}),
    ]

    flaky = trend.flaky_tests(runs)

    assert "test_a" in flaky


def test_a_consistent_test_is_not_flaky():
    runs = [
        run("abc", {"test_a": "PASSED"}),
        run("abc", {"test_a": "PASSED"}),
    ]

    assert trend.flaky_tests(runs) == {}


# --------------------------------------------------------------------------
# Passed but slower
# --------------------------------------------------------------------------

def timed(build, durations, status="PASSED"):
    return {
        "run_dir": "build_" + build,
        "build_id": build,
        "verdicts": {name: status for name in durations},
        "durations": dict(durations),
        "duration_s": sum(durations.values()),
        "status": "pass",
        "passed": len(durations),
        "failed": 0,
        "skipped": 0,
    }


def test_a_test_that_still_passes_but_tripled_is_reported():
    """The gap between 'passed' and 'fine'."""
    runs = [
        timed("a", {"test_x": 2.0}),
        timed("b", {"test_x": 2.1}),
        timed("c", {"test_x": 6.5}),
    ]

    slower = trend.slower_tests(runs)

    assert "test_x" in slower
    assert slower["test_x"]["change_pct"] > 100


def test_a_steady_test_is_not_reported():
    runs = [timed(b, {"test_x": 2.0}) for b in ("a", "b", "c")]

    assert trend.slower_tests(runs) == {}


def test_a_new_test_is_not_a_slowdown():
    """Adding tests must never look like a regression."""
    runs = [
        timed("a", {"test_x": 2.0}),
        timed("b", {"test_x": 2.0}),
        timed("c", {"test_x": 2.0, "test_new": 30.0}),
    ]

    assert trend.slower_tests(runs) == {}


def test_very_quick_tests_are_ignored():
    """0.01s to 0.03s is +200% and means nothing."""
    runs = [
        timed("a", {"test_x": 0.01}),
        timed("b", {"test_x": 0.01}),
        timed("c", {"test_x": 0.03}),
    ]

    assert trend.slower_tests(runs) == {}


def test_one_bad_run_does_not_set_the_baseline():
    """The median ignores a single outlier in the history."""
    runs = [
        timed("a", {"test_x": 2.0}),
        timed("b", {"test_x": 20.0}),      # one bad run
        timed("c", {"test_x": 2.0}),
        timed("d", {"test_x": 2.2}),
    ]

    assert trend.slower_tests(runs) == {}


def test_the_total_ignores_tests_absent_from_one_end():
    """Only the tests both ends of the comparison ran are totalled."""
    runs = [
        timed("a", {"test_x": 10.0}),
        timed("b", {"test_x": 10.0}),
        timed("c", {"test_x": 10.0, "test_new": 500.0}),
    ]

    pace = trend.slowdown(runs)

    assert pace["regressed"] is False


# --------------------------------------------------------------------------
# test_reduction -- the optimisation itself
# --------------------------------------------------------------------------

def test_reduction_needs_selection_counts():
    assert not kpi.test_reduction([run("b1", {"test_a": "passed"})]).measured


def test_reduction_is_the_share_left_out():
    runs = [
        dict(run("b1", {"test_a": "passed"}),
             selection={"selected": 15, "full": 20, "left_out": ["test_x"]}),
        dict(run("b2", {"test_a": "passed"}),
             selection={"selected": 20, "full": 20, "left_out": []}),
    ]

    result = kpi.test_reduction(runs)

    assert result.value == 12.5
    assert result.sample == 2


def test_time_saved_is_borrowed_from_runs_that_did_include_the_test():
    runs = [
        dict(run("b1", {"test_x": "passed"}), durations={"test_x": 30.0},
             selection={"selected": 20, "full": 20, "left_out": []}),
        dict(run("b2", {"test_a": "passed"}),
             selection={"selected": 19, "full": 20, "left_out": ["test_x"]}),
        dict(run("b3", {"test_a": "passed"}),
             selection={"selected": 19, "full": 20, "left_out": ["test_y"]}),
    ]

    detail = kpi.test_reduction(runs).detail

    assert "about 30s" in detail
    assert "1 left-out test(s) never ran" in detail


def test_planner_history_keeps_what_failed_and_drops_timings():
    runs = [dict(run("b{}".format(i), {"test_a": "passed", "test_b": "FAILED"}),
                 durations={"test_a": 1.0}, intensity="low", scenarios=[])
            for i in range(7)]

    history = kpi.planner_history(runs)

    assert len(history) == 5
    assert history[-1]["build_id"] == "b6"
    assert history[-1]["failed_tests"] == ["test_b"]
    assert "durations" not in history[-1]
