# Copyright (c) 2026 Prajnaana Technologies Pvt. Ltd.
# Original author: Dhanya Shree S
# SPDX-License-Identifier: MIT
#
# Licensed under the MIT License. See LICENSE in the project root.

"""Derive test cases from a structured change specification.

The difference this module exists to make:

    "Generate test cases for this firmware"     -> whatever the model felt like
    change_spec -> derive()                     -> the same cases every time

Each change type except documentation implies a shape of test, and the
shapes are not interchangeable. A documentation change implies none, and
derives no case. A configuration change stating a limit as "from X to Y"
implies three cases: the old limit, the new limit, and one past the new one;
a bare "to Y" implies the last two, because there is no old limit to name. A
memory bug fix implies repetition, because a leak is invisible on the first
iteration and obvious on the hundredth. A protection feature implies a case
on the far side of the threshold, which is the one nobody writes by hand
because it is the unpleasant one.

What this does NOT do is claim the bench can run them. A case whose
component has no interface on this hardware is emitted with executable=False
and the reason, so the specification stays complete while the report stays
honest. That is the same rule test_power and test_sync already follow: an
unmeasured thing is skipped and says why, never quietly passed.

    python -m regression.ai_engine.test_spec NRF_Firmware/RELEASE_NOTES.md
"""

import json

from regression.ai_engine import change_spec

# Test case kinds, in the vocabulary a report groups by.
FUNCTIONAL = "Functional"
REGRESSION = "Regression"
STRESS = "Stress"
BOUNDARY = "Boundary"
NEGATIVE = "Negative"
SAFETY = "Safety"

# Short prefixes for readable ids. A component with no prefix here uses its
# first three letters upper-cased, which is good enough and never collides
# in practice because the counter is per prefix.
ID_PREFIXES = {
    "BLE": "BLE",
    "battery": "BAT",
    "audio": "AUD",
    "memory": "MEM",
    "power": "PWR",
    "telemetry": "TLM",
    "sensor": "SEN",
    change_spec.UNKNOWN_COMPONENT: "GEN",
}

# What this bench can actually drive. Everything else is specified and
# skipped -- the DK exposes no thermistor, so a battery temperature case can
# be written but not run.
DRIVABLE = {
    "BLE": "",
    "audio": "",
    "memory": "",
    "telemetry": "",
    "power": "needs an external analyzer; the board cannot measure its own supply",
    # Level is readable over the Battery Service (CONFIG_BT_BAS).
    # Temperature is not -- there is no thermistor -- and a case needing one
    # is caught at the step, not here.
    "battery": "",
    "sensor": "no sensor attached to this bench",
    "sync": "binaural sync needs two devices; this bench has one",
    "security": "",
    change_spec.UNKNOWN_COMPONENT: "the note does not say which component this touches",
}

# Repetitions for the two kinds of repeat test. The regression count proves
# the operation still works; the stress count is where a leak shows up.
REGRESSION_REPEATS = 10
STRESS_REPEATS = 100

# Words that mark a change as protective rather than merely additive. A
# protection feature is the case where the far side of the threshold is the
# point of the feature, so it earns a safety case.
PROTECTION_WORDS = (
    "protection", "protect", "limit", "cutoff", "cut-off", "shutdown",
    "safety", "over-temperature", "overtemperature", "overcurrent",
    "overvoltage", "watchdog",
)

REPEAT_WORDS = ("leak", "repeated", "repeat", "cycle", "long", "soak")


def _prefix(component):
    return ID_PREFIXES.get(component) or component[:3].upper()


def _case(counters, component, title, kind, expected):
    prefix = _prefix(component)
    counters[prefix] = counters.get(prefix, 0) + 1

    reason = DRIVABLE.get(component, "no interface for this component on this bench")

    return {
        "id": "{}-{:03d}".format(prefix, counters[prefix]),
        "test": title,
        "type": kind,
        "expected": expected,
        "component": component,
        "executable": not reason,
        "skip_reason": reason,
    }


def _is_protection(description):
    lowered = description.lower()

    return any(word in lowered for word in PROTECTION_WORDS)


def _wants_repetition(description):
    lowered = description.lower()

    return any(word in lowered for word in REPEAT_WORDS)


def _baseline(counters, component):
    """The case that proves the component works at all.

    Everything else in the list assumes it does, so a failure here explains
    the rest instead of leaving nine unrelated-looking failures.
    """
    titles = {
        "BLE": ("Connect BLE device", "Connection successful"),
        "audio": ("Stream audio through the DSP", "Audio returned unaltered in shape"),
        "memory": ("Read memory telemetry", "Reported and within budget"),
        "battery": ("Read battery level over 0x2A19",
                    "Reported, 0 to 100 percent"),
        "power": ("Measure supply current", "Reported"),
        "telemetry": ("Read telemetry snapshot", "Reported and version accepted"),
    }

    title, expected = titles.get(
        component, ("Exercise {} nominally".format(component), "Normal operation"))

    return _case(counters, component, title, FUNCTIONAL, expected)


def _from_configuration_change(counters, change):
    """Boundary and negative cases around a stated limit.

    A limit the note states is a fact to test at. A limit it does not state
    is a guess, so nothing is emitted -- a boundary case invented around an
    imagined number tests the imagination.
    """
    component = change["component"]
    values = change.get("values") or {}
    unit = values.get("unit", "")
    upper = values.get("to")
    cases = []

    if upper is None:
        return [_case(counters, component,
                      change["description"], REGRESSION,
                      "Behaviour unchanged apart from the new setting")]

    def amount(number):
        return "{} {}".format(number, unit).strip()

    lower = values.get("from")

    if lower is not None:
        cases.append(_case(
            counters, component,
            "Exercise the previous limit, {}".format(amount(lower)),
            BOUNDARY, "PASS -- the old limit must keep working"))

    cases.append(_case(
        counters, component,
        "Exercise the new limit, {}".format(amount(upper)),
        BOUNDARY, "PASS"))

    cases.append(_case(
        counters, component,
        "Exceed the new limit, {}".format(amount(upper + 1)),
        NEGATIVE, "Rejected, and the link survives the rejection"))

    return cases


def _from_bug_fix(counters, change):
    component = change["component"]
    cases = [_case(
        counters, component,
        "Reproduce the conditions of: {}".format(change["description"]),
        REGRESSION, "Defect does not recur")]

    # A leak is invisible once and obvious a hundred times.
    if _wants_repetition(change["description"]):
        cases.append(_case(
            counters, component,
            "Repeat {} times".format(STRESS_REPEATS),
            STRESS, "No degradation across the run"))

    return cases


def _from_enhancement(counters, change):
    return [_case(
        counters, change["component"],
        "Repeat the improved path {} times: {}".format(
            REGRESSION_REPEATS, change["description"]),
        REGRESSION, "All iterations succeed")]


def _from_new_feature(counters, change):
    component = change["component"]
    cases = [_case(
        counters, component,
        "{} -- nominal conditions".format(change["description"]),
        FUNCTIONAL, "Normal operation")]

    if _is_protection(change["description"]):
        cases.append(_case(
            counters, component,
            "At the protection threshold",
            BOUNDARY, "Protection behaviour is defined and repeatable"))

        # The case nobody writes by hand, because it is the unpleasant one.
        cases.append(_case(
            counters, component,
            "Beyond the protection threshold",
            SAFETY, "Protection activates and the device stays safe"))

    return cases


def _from_removal(counters, change):
    return [_case(
        counters, change["component"],
        "Confirm removed: {}".format(change["description"]),
        NEGATIVE, "No longer present, and nothing depends on it")]


BY_TYPE = {
    change_spec.CONFIGURATION_CHANGE: _from_configuration_change,
    change_spec.BUG_FIX: _from_bug_fix,
    change_spec.ENHANCEMENT: _from_enhancement,
    change_spec.NEW_FEATURE: _from_new_feature,
    change_spec.REMOVAL: _from_removal,
}


def derive(spec, counters=None):
    """Test cases for a structured change specification.

    One baseline case per component touched, then the cases each change
    implies. Deterministic: the same specification always derives the same
    list, in the same order, with the same ids.

    `counters` is shared with the requirement generator when both run, so a
    case id means one case. With a counter each, both start at 001 for the
    same prefix and the document carries two different BAT-001s -- which
    reads as a traceability matrix right up until someone follows an id.
    """
    counters = {} if counters is None else counters
    cases = []
    placeholder = None

    for component in change_spec.components(spec):
        baseline = _baseline(counters, component)
        cases.append(baseline)

        if component == change_spec.UNKNOWN_COMPONENT:
            placeholder = baseline

    unexplained = []

    for change in spec.get("changes", []):
        builder = BY_TYPE.get(change.get("type"))

        if builder is None:
            continue

        built = builder(counters, change)

        # "No component" is not a reason anyone can act on. Say which line
        # it was and what kind of change it is.
        if change.get("component") == change_spec.UNKNOWN_COMPONENT:
            for case in built:
                case["skip_reason"] = unknown_reason(change)
                unexplained.append(case["id"])

        cases.extend(built)

    if placeholder is not None:
        placeholder["skip_reason"] = (
            "Not a real test: 'unknown' is not a part of the device. This row "
            "stands for the release-note line(s) that name no part the bench "
            "can test ({}); each of those says why it is skipped.".format(
                ", ".join(unexplained)))

    return cases


# Words that mark a change to how the image is built rather than to what the
# device does.
BUILD_WORDS = (
    "cmake", "overlay", "devicetree", "kconfig", "prj.conf", "build id",
    "build-identity", "build identity", "toolchain", "linker", "makefile",
    "compiler",
)

# What the component classifier recognises, for telling an author what to
# write.
RECOGNISED = ("battery", "Bluetooth / BLE / GATT", "audio / DSP / compressor",
              "memory / stack", "power / current", "telemetry", "sensor")


def _quote(text, limit=70):
    return text if len(text) <= limit else text[:limit - 3].rstrip() + "..."


def unknown_reason(change):
    """Why a release-note change with no component has no test, specifically."""
    line = _quote(change["description"])
    section = change.get("section", "")
    detail = change.get("detail", change["description"]).lower()

    where = " under '{}'".format(section) if section else ""

    if "build" in section.lower() or any(w in detail for w in BUILD_WORDS):
        return ("Build-system change: '{}'{}. It changes how the firmware "
                "image is put together, not what the device does, so it has "
                "no behaviour of its own to test. Any effect it has shows up "
                "in the other tests, which all run on the image it "
                "produced.".format(line, where))

    return ("The release-note line '{}'{} does not name a part of the device "
            "this bench can test, and its section heading does not either. "
            "Recognised parts: {}. Name the part in the line and a test is "
            "generated for it.".format(line, where, ", ".join(RECOGNISED)))


def format_table(cases):
    """The cases as a table, for a report or a review."""
    head = ("ID", "TEST", "TYPE", "EXPECTED")
    rows = [(c["id"], c["test"], c["type"],
             c["expected"] if c["executable"]
             else "SKIPPED -- " + c["skip_reason"])
            for c in cases]

    widths = [max(len(str(r[i])) for r in (head,) + tuple(rows))
              for i in range(4)]

    def line(row):
        return "  ".join(str(row[i]).ljust(widths[i]) for i in range(4)).rstrip()

    out = [line(head), "  ".join("-" * w for w in widths)]
    out += [line(row) for row in rows]

    return "\n".join(out)


def main(argv=None):
    import argparse

    parser = argparse.ArgumentParser(
        description="Derive test cases from a release note")
    parser.add_argument("note", help="path to the release note")
    parser.add_argument("--json", action="store_true", help="emit JSON")

    args = parser.parse_args(argv)

    with open(args.note, encoding="utf-8", errors="replace") as handle:
        spec = change_spec.extract(handle.read())

    cases = derive(spec)

    if args.json:
        print(json.dumps({"spec": spec, "cases": cases}, indent=2))
    else:
        print("Version:", spec["version"] or "unstated")
        print()
        print(format_table(cases))
        print()
        runnable = sum(1 for c in cases if c["executable"])
        print("{} case(s) derived, {} runnable on this bench, {} specified "
              "but not runnable".format(len(cases), runnable, len(cases) - runnable))

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
