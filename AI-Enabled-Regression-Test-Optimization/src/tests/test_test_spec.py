# Copyright (c) 2026 Prajnaana Technologies Pvt. Ltd.
# Original author: Dhanya Shree S
# SPDX-License-Identifier: MIT
#
# Licensed under the MIT License. See LICENSE in the project root.

"""Tests for deriving test cases from a change specification.

The rule these enforce: the same specification derives the same cases, every
time, with the same ids. A generator that produces a different list each run
cannot be reviewed, cannot be diffed, and makes the determinism KPI
meaningless for everything downstream of it.
"""

import pytest

from regression.ai_engine import test_spec
from regression.ai_engine.change_spec import extract
from regression.ai_engine.test_spec import derive

EXAMPLE = """Version 2.4.0

1. Added battery over-temperature protection.
2. Improved BLE reconnection handling.
3. Fixed memory leak during repeated BLE connections.
4. Increased maximum BLE packet size from 64 to 128 bytes.
"""


def cases_for(note):
    return derive(extract(note))


def kinds(cases):
    return [c["type"] for c in cases]


# --------------------------------------------------------------------------
# Determinism
# --------------------------------------------------------------------------

def test_the_same_note_derives_the_same_cases():
    first = cases_for(EXAMPLE)
    second = cases_for(EXAMPLE)

    assert first == second


def test_every_id_is_unique():
    ids = [c["id"] for c in cases_for(EXAMPLE)]

    assert len(ids) == len(set(ids))


# --------------------------------------------------------------------------
# A stated limit becomes boundary and negative cases
# --------------------------------------------------------------------------

def test_a_stated_limit_derives_both_bounds_and_one_past_it():
    """"from 64 to 128" is the old limit, the new limit, and one over."""
    cases = cases_for("- Increased maximum BLE packet size from 64 to 128 bytes.")

    boundaries = [c for c in cases if c["type"] == test_spec.BOUNDARY]
    negatives = [c for c in cases if c["type"] == test_spec.NEGATIVE]

    assert len(boundaries) == 2
    assert "64 bytes" in boundaries[0]["test"]
    assert "128 bytes" in boundaries[1]["test"]

    assert len(negatives) == 1
    assert "129 bytes" in negatives[0]["test"]


def test_a_limit_with_no_stated_number_invents_no_boundary():
    """A boundary around an imagined number tests the imagination."""
    cases = cases_for("- Changed the BLE buffering strategy.")

    assert test_spec.BOUNDARY not in kinds(cases)
    assert test_spec.NEGATIVE not in kinds(cases)


# --------------------------------------------------------------------------
# Each change type implies its own shape
# --------------------------------------------------------------------------

def test_a_leak_fix_earns_a_repeat_case():
    """A leak is invisible once and obvious a hundred times."""
    cases = cases_for("- Fixed memory leak during repeated BLE connections.")

    stress = [c for c in cases if c["type"] == test_spec.STRESS]

    assert len(stress) == 1
    assert str(test_spec.STRESS_REPEATS) in stress[0]["test"]


def test_a_bug_fix_without_repetition_words_gets_no_stress_case():
    cases = cases_for("- Fixed the advertising restart on disconnect.")

    assert test_spec.STRESS not in kinds(cases)


def test_a_protection_feature_earns_a_safety_case():
    """The case nobody writes by hand, because it is the unpleasant one."""
    cases = cases_for("- Added battery over-temperature protection.")

    safety = [c for c in cases if c["type"] == test_spec.SAFETY]

    assert len(safety) == 1
    assert "Beyond" in safety[0]["test"]


def test_an_ordinary_feature_earns_no_safety_case():
    cases = cases_for("- Added a BLE characteristic for the envelope.")

    assert test_spec.SAFETY not in kinds(cases)


def test_a_removal_is_checked_for_absence():
    cases = cases_for("- Removed the legacy UART command shell.")

    negatives = [c for c in cases if c["type"] == test_spec.NEGATIVE]

    assert len(negatives) == 1


# --------------------------------------------------------------------------
# Specified but not runnable
# --------------------------------------------------------------------------

def test_a_component_this_bench_cannot_drive_is_specified_and_skipped():
    """The specification stays complete; the report stays honest."""
    cases = cases_for("- Lowered idle supply current to 8 uA.")
    power = [c for c in cases if c["component"] == "power"]

    assert power
    for case in power:
        assert case["executable"] is False
        assert "analyzer" in case["skip_reason"]


def test_battery_became_drivable_when_the_service_was_added():
    """Battery level is readable over 0x2A19 with CONFIG_BT_BAS set.
    Temperature is a different question and has no instrument."""
    assert test_spec.DRIVABLE["battery"] == ""
    assert test_spec.DRIVABLE["sync"]


def test_ble_cases_are_runnable_here():
    cases = [c for c in cases_for(EXAMPLE) if c["component"] == "BLE"]

    assert cases
    assert all(c["executable"] for c in cases)
    assert all(c["skip_reason"] == "" for c in cases)


def test_the_worked_example_covers_every_case_in_the_specification():
    cases = cases_for(EXAMPLE)

    assert [c for c in cases if c["type"] == test_spec.FUNCTIONAL]
    assert [c for c in cases if c["type"] == test_spec.REGRESSION]
    assert [c for c in cases if c["type"] == test_spec.STRESS]
    assert [c for c in cases if c["type"] == test_spec.BOUNDARY]
    assert [c for c in cases if c["type"] == test_spec.NEGATIVE]
    assert [c for c in cases if c["type"] == test_spec.SAFETY]


def test_each_component_gets_one_baseline_case():
    """Everything else assumes the component works at all."""
    cases = cases_for(EXAMPLE)

    for component in ("BLE", "battery"):
        first = [c for c in cases if c["component"] == component][0]

        assert first["type"] == test_spec.FUNCTIONAL
        assert first["id"].endswith("-001")


# --------------------------------------------------------------------------
# Deriving from requirements
# --------------------------------------------------------------------------

def test_a_lower_bound_inside_an_upper_bound_is_not_a_lower_bound():
    """"no more than 12" contains "more than 12". Read as both, the derived
    case asserts the opposite of the requirement."""
    from regression.ai_engine.requirement_tests import limits

    assert limits("shall be no more than 12 retries") == {
        "max": 12, "unit": "retries"}


def test_an_upper_bound_inside_a_lower_bound_is_not_an_upper_bound():
    """The mirror of the case above: "no less than 3" states a floor and no
    ceiling, although it contains "less than 3"."""
    from regression.ai_engine.requirement_tests import limits

    assert limits("Signal-to-noise ratio shall be no less than 3 dB.") == {
        "min": 3, "unit": "dB"}


@pytest.mark.parametrize(
    "text,expected",
    [
        # Ceilings.
        ("shall be no more than 12 retries", {"max": 12, "unit": "retries"}),
        ("shall be at most 12 retries", {"max": 12, "unit": "retries"}),
        ("shall be below 5 ms", {"max": 5, "unit": "ms"}),
        ("shall be under 2000 ms", {"max": 2000, "unit": "ms"}),
        ("shall not exceed 60 mA", {"max": 60, "unit": "mA"}),
        ("shall not be above 5 ms", {"max": 5, "unit": "ms"}),
        ("shall be no greater than 3 dB", {"max": 3, "unit": "dB"}),
        ("shall be fewer than 5 retries", {"max": 5, "unit": "retries"}),
        # A negation that carries a verb still states a ceiling.
        ("shall not add more than 8 ms of latency",
         {"max": 8, "unit": "ms"}),
        ("shall not grow by more than 0.25 KiB",
         {"max": 0.25, "unit": "KiB"}),
        # Floors.
        ("shall be no less than 3 dB", {"min": 3, "unit": "dB"}),
        ("shall be not less than 3 dB", {"min": 3, "unit": "dB"}),
        ("shall be at least 3 dB", {"min": 3, "unit": "dB"}),
        ("shall be above 0 dB", {"min": 0, "unit": "dB"}),
        ("shall be greater than 3 dB", {"min": 3, "unit": "dB"}),
        ("shall be no fewer than 5 retries", {"min": 5, "unit": "retries"}),
        ("shall not fall below 3 dB", {"min": 3, "unit": "dB"}),
    ],
)
def test_every_bound_phrase_points_the_way_it_reads(text, expected):
    """One bound, in the direction the sentence states, and no second one.

    Every phrase in the vocabulary contains its opposite once it is negated,
    so each pairing is pinned here: a requirement that states a floor must
    never also produce a ceiling, and the other way round.
    """
    from regression.ai_engine.requirement_tests import limits

    assert limits(text) == expected


def test_a_genuine_pair_of_bounds_is_still_read_as_two():
    from regression.ai_engine.requirement_tests import limits

    bounds = limits("shall be at least 3 and no more than 12 retries")

    assert bounds["min"] == 3
    assert bounds["max"] == 12


def test_a_pair_written_with_both_negations_is_still_read_as_two():
    """Skipping the buried match must not skip the real one behind it."""
    from regression.ai_engine.requirement_tests import limits

    bounds = limits("shall be no less than 3 dB and no more than 12 dB")

    assert bounds["min"] == 3
    assert bounds["max"] == 12


def test_the_shipped_requirements_state_no_contradictory_bound():
    """A floor and a ceiling of the same number is always a parsing error."""
    import io
    import os

    from regression.ai_engine.requirement_tests import limits
    from regression.paths import asset

    path = asset("REQUIREMENTS.md")

    if not os.path.exists(path):
        pytest.skip("no REQUIREMENTS.md in this tree")

    with io.open(path, encoding="utf-8") as handle:
        for line in handle:
            bounds = limits(line)

            if "min" in bounds and "max" in bounds:
                assert bounds["min"] < bounds["max"], line.strip()


def test_a_requirement_with_no_number_derives_one_functional_case():
    """A boundary invented around an imagined limit tests the imagination."""
    from regression.ai_engine.requirement_tests import derive_for

    cases = derive_for(
        {"id": "REQ-AUD-101", "component": "audio",
         "text": "The device shall mute the output when the input is silent."},
        {}, {})

    assert len(cases) == 1
    assert cases[0]["type"] == test_spec.FUNCTIONAL


def test_a_cited_setting_parameterises_from_the_build_not_the_prose():
    """The build's value is the one the device enforces."""
    from regression.ai_engine.requirement_tests import derive_for

    cases = derive_for(
        {"id": "REQ-BLE-102", "component": "BLE",
         "text": "The MTU shall stay at CONFIG_BT_L2CAP_TX_MTU."},
        {"CONFIG_BT_L2CAP_TX_MTU": "247"}, {})

    titles = " ".join(c["test"] for c in cases)

    assert "247" in titles
    assert "248" in titles


# --------------------------------------------------------------------------
# Strict limits -- "under 2000 ms" excludes 2000
# --------------------------------------------------------------------------

def _boundary(cases, word):
    for case in cases:
        if case["type"] == test_spec.BOUNDARY and word in case["test"]:
            return case

    raise AssertionError("no boundary case naming {!r}".format(word))


@pytest.mark.parametrize(
    "text,value",
    [
        ("Audio latency shall be under 2000 ms.", "2000 ms"),
        ("Noise floor shall be below 50 dB.", "50 dB"),
        ("Retries shall be fewer than 5 retries.", "5 retries"),
    ],
)
def test_the_boundary_of_a_strict_ceiling_is_a_violation(text, value):
    """A strict ceiling excludes its own limit, so 2000 ms breaches
    "under 2000 ms" rather than sitting within it.

    REQ-AUD-004 and REQ-AUD-003 are both phrased this way. canonical.py
    says the same thing about "under" in its own STRICT_UPPER, so the two
    agree.
    """
    from regression.ai_engine.requirement_tests import derive_for

    cases = derive_for(
        {"id": "REQ-AUD-201", "component": "audio", "text": text}, {}, {})

    case = _boundary(cases, value)

    assert case["expected"] == "Violates the requirement (strict limit)"


def test_the_boundary_of_an_inclusive_ceiling_still_passes():
    """"at most" and "shall not exceed" include the limit; nothing changes."""
    from regression.ai_engine.requirement_tests import derive_for

    for text in ("Supply current shall not exceed 60 mA.",
                 "Retries shall be at most 12 retries."):
        cases = derive_for(
            {"id": "REQ-PWR-201", "component": "power", "text": text}, {}, {})

        assert _boundary(cases, "stated maximum")["expected"] == (
            "Within the requirement")


def test_the_boundary_of_a_strict_floor_is_a_violation():
    from regression.ai_engine.requirement_tests import derive_for

    cases = derive_for(
        {"id": "REQ-AUD-202", "component": "audio",
         "text": "Signal-to-noise ratio shall be greater than 3 dB."}, {}, {})

    assert _boundary(cases, "stated minimum")["expected"] == (
        "Violates the requirement (strict limit)")


def test_the_ends_of_a_stated_range_are_inclusive():
    """"between 0.55 and 0.85" states both ends as allowed values."""
    from regression.ai_engine.requirement_tests import derive_for, strictness

    text = "Compression ratio shall be between 0.55 and 0.85."

    assert strictness(text) == (False, False)

    cases = derive_for(
        {"id": "REQ-AUD-203", "component": "audio", "text": text}, {}, {})

    for word in ("stated maximum", "stated minimum"):
        assert _boundary(cases, word)["expected"] == "Within the requirement"


def test_never_negates_a_bound_exactly_as_not_does():
    """"must never go below 3 dB" is a floor of 3, not a ceiling of 3."""
    from regression.ai_engine.requirement_tests import limits

    assert limits("Level must never go below 3 dB") == {
        "min": 3, "unit": "dB"}
    assert limits("Level must never fall below 3 dB") == {
        "min": 3, "unit": "dB"}
    assert limits("Supply current shall never exceed 60 mA") == {
        "max": 60, "unit": "mA"}
    assert limits("Latency shall never be more than 8 ms") == {
        "max": 8, "unit": "ms"}


def test_strictness_reads_the_bound_that_limits_kept():
    """A sentence stating both bounds must not read the wrong one as strict.

    "shall not fall below 1.5 dB" is a floor, and the bare "below 1.5 dB"
    inside it is not a bound at all -- limits() skips it and takes the 9 dB
    ceiling. strictness() answers from the same pair of matches, so it
    reads the ceiling limits() kept and not the buried "below".
    """
    from regression.ai_engine.requirement_tests import (
        bound_matches, derive_for, limits, strictness,
    )

    text = "Gain shall not fall below 1.5 dB and shall be at most 9 dB"

    assert limits(text) == {"min": 1.5, "max": 9, "unit": "dB"}
    assert strictness(text) == (False, False)

    lower, upper = bound_matches(text)

    assert upper.group(0).lower().startswith("at most")
    assert "not fall below" in lower.group(0).lower()

    cases = derive_for(
        {"id": "REQ-AUD-204", "component": "audio", "text": text}, {}, {})

    assert _boundary(cases, "9 dB")["expected"] == "Within the requirement"
    assert _boundary(cases, "1.5 dB")["expected"] == "Within the requirement"

