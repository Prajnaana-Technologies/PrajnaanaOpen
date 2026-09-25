# Copyright (c) 2026 Prajnaana Technologies Pvt. Ltd.
# Original author: Dhanya Shree S
# SPDX-License-Identifier: MIT
#
# Licensed under the MIT License. See LICENSE in the project root.

"""Inventory of what is tested, by category and by who has to run it.

The 65/25/10 automation target is a planning number: 65 per cent of regression
automated, 25 per cent targeted manual, 10 per cent subjective. It cannot be
measured without an inventory saying which bucket each test falls in, so this
is that inventory.

Read the automated share with its scope in mind: it counts the host unit
tests under src/tests as well as the tests that run against the board, and
there are several hundred of them. They are automated regression by any
honest reading -- they are what catches a change to the framework -- but
they are not device coverage, and a split they dominate is saying more
about the host suite than about the bench. format_report prints the two
counts separately for that reason.

Automated tests are *discovered*, not declared -- the split is parsed out of
the test files themselves, so it cannot drift from the code the way a
hand-maintained list does. Manual and subjective tests have no code to
discover, so those are declared below and have to be kept current by hand.

    python -m regression.catalog

prints the split and the distance from target.
"""

import ast
import os

from regression.paths import BASE_DIR

# ---------------------------------------------------------------------------
# Vocabulary
# ---------------------------------------------------------------------------

# What kind of failure the test is designed to find. A suite made only of
# FUNCTIONAL tests finds only the bugs that break a feature outright; the
# other five categories exist because the expensive bugs are elsewhere.
FUNCTIONAL = "functional"
STRESS = "stress"
FAULT_INJECTION = "fault_injection"
LONG_DURATION = "long_duration"
CONCURRENCY = "concurrency"
SECURITY = "security"

CATEGORIES = (
    FUNCTIONAL,
    STRESS,
    FAULT_INJECTION,
    LONG_DURATION,
    CONCURRENCY,
    SECURITY,
)

CATEGORY_PURPOSE = {
    FUNCTIONAL: "does the feature work at all",
    STRESS: "where is the limit, and does crossing it degrade gracefully",
    FAULT_INJECTION: "does recovery actually run when something breaks",
    LONG_DURATION: "leaks, counter overflow and drift over hours",
    CONCURRENCY: "conflicts when subsystems compete for CPU, buffers or radio",
    SECURITY: "pairing, bonding and whether encryption is actually enforced",
}

# Who runs it.
AUTOMATED = "automated"
TARGETED_MANUAL = "targeted_manual"
SUBJECTIVE = "subjective"

AUTOMATION_CLASSES = (AUTOMATED, TARGETED_MANUAL, SUBJECTIVE)

# The plan from the proposal. Percentages of the total inventory.
TARGET_SPLIT = {
    AUTOMATED: 65.0,
    TARGETED_MANUAL: 25.0,
    SUBJECTIVE: 10.0,
}

# How far from target counts as on-plan. The target is a direction, not a
# contract, so this is deliberately loose.
TARGET_TOLERANCE_PCT = 10.0


# ---------------------------------------------------------------------------
# Declared tests: the ones with no code to discover
# ---------------------------------------------------------------------------

class Entry(object):
    """One test in the inventory."""

    def __init__(self, name, category, automation, note=""):
        if category not in CATEGORIES:
            raise ValueError("unknown category: {}".format(category))

        if automation not in AUTOMATION_CLASSES:
            raise ValueError("unknown automation class: {}".format(automation))

        self.name = name
        self.category = category
        self.automation = automation
        self.note = note

    def __repr__(self):
        return "Entry({!r}, {!r}, {!r})".format(
            self.name, self.category, self.automation
        )


# Work a person does, that no amount of automation replaces. Keep this list
# honest: padding it moves the measured split without changing reality.
MANUAL_TESTS = [
    Entry("bench_bring_up", FUNCTIONAL, TARGETED_MANUAL,
          "first power-on of new hardware, before anything is trusted"),
    Entry("flash_and_recover", FAULT_INJECTION, TARGETED_MANUAL,
          "reflash a bricked board; the thing that bricked it cannot do this"),
    Entry("rf_interoperability", FUNCTIONAL, TARGETED_MANUAL,
          "pairing against phones and stacks not present on the bench"),
    Entry("exploratory_new_feature", FUNCTIONAL, TARGETED_MANUAL,
          "unscripted probing of a feature before its tests exist"),
    Entry("range_and_obstruction", STRESS, TARGETED_MANUAL,
          "walk the link to its edge; needs a room, not a rig"),
    Entry("pairing_on_real_phones", SECURITY, TARGETED_MANUAL,
          "pairing UX against iOS and Android, which no bench rig covers"),
]

SUBJECTIVE_TESTS = [
    Entry("listening_quality", FUNCTIONAL, SUBJECTIVE,
          "does processed audio sound right to a person"),
    Entry("artefact_detection", FUNCTIONAL, SUBJECTIVE,
          "clicks, pumping, distortion -- audible long before a metric moves"),
    Entry("comfort_over_time", LONG_DURATION, SUBJECTIVE,
          "fatigue across a long session"),
]


# ---------------------------------------------------------------------------
# Discovery: parse the automated tests out of the source
# ---------------------------------------------------------------------------

# Where to look for automated tests, relative to BASE_DIR -- src/ in a
# source run, the folder holding the .exe in a frozen one.
#
# "tests" is the host unit suite -- the framework testing itself. It is
# counted, and the report says so.
DEVICE_TEST_ROOT = os.path.join("regression", "generated_tests")
HOST_TEST_ROOT = "tests"

TEST_ROOTS = (
    DEVICE_TEST_ROOT,
    HOST_TEST_ROOT,
)


def _category_from_decorators(node):
    """Read @pytest.mark.category("stress") off a test function.

    Untagged tests count as functional -- the common case, and the one that
    needs no ceremony. Only the other five categories have to be declared.
    """
    for decorator in node.decorator_list:
        if not isinstance(decorator, ast.Call):
            continue

        target = decorator.func

        if not isinstance(target, ast.Attribute) or target.attr != "category":
            continue

        for arg in decorator.args:
            if isinstance(arg, ast.Constant) and arg.value in CATEGORIES:
                return arg.value

    return FUNCTIONAL


def discover(roots=None, base=None):
    """Every automated test found under the test roots.

    Parses rather than imports: discovery must work with no hardware
    attached, no device fixture and no optional dependency installed.
    """
    base = base or BASE_DIR
    roots = roots or TEST_ROOTS

    found = []

    for root in roots:
        directory = os.path.join(base, root)

        if not os.path.isdir(directory):
            continue

        for entry in sorted(os.listdir(directory)):
            if not (entry.startswith("test_") and entry.endswith(".py")):
                continue

            path = os.path.join(directory, entry)

            try:
                with open(path, encoding="utf-8") as handle:
                    tree = ast.parse(handle.read(), filename=path)
            except (OSError, SyntaxError):
                # A malformed test file is a problem, but not this module's
                # problem -- pytest reports it far more clearly than a
                # half-parsed inventory would.
                continue

            for node in ast.walk(tree):
                if not isinstance(node, ast.FunctionDef):
                    continue

                if not node.name.startswith("test_"):
                    continue

                found.append(Entry(
                    node.name,
                    _category_from_decorators(node),
                    AUTOMATED,
                    note=os.path.relpath(path, base),
                ))

    return found


def inventory(base=None):
    """Discovered automated tests plus the declared manual and subjective."""
    return discover(base=base) + list(MANUAL_TESTS) + list(SUBJECTIVE_TESTS)


# ---------------------------------------------------------------------------
# The measurement
# ---------------------------------------------------------------------------

def automation_split(entries=None):
    """Percentage of the inventory in each automation class."""
    entries = inventory() if entries is None else entries

    total = len(entries)

    if not total:
        return {name: 0.0 for name in AUTOMATION_CLASSES}

    counts = {name: 0 for name in AUTOMATION_CLASSES}

    for entry in entries:
        counts[entry.automation] += 1

    return {
        name: round(100.0 * count / total, 1)
        for name, count in counts.items()
    }


def drift_from_target(entries=None):
    """Percentage points away from TARGET_SPLIT, per class. Positive is over."""
    split = automation_split(entries)

    return {
        name: round(split[name] - TARGET_SPLIT[name], 1)
        for name in AUTOMATION_CLASSES
    }


def on_target(entries=None):
    """True when every class is within TARGET_TOLERANCE_PCT of plan."""
    drift = drift_from_target(entries)

    return all(abs(value) <= TARGET_TOLERANCE_PCT for value in drift.values())


def category_counts(entries=None):
    """How many tests exist per category, including the empty ones.

    Categories with a count of zero are the point of this function: an
    uncovered category is invisible in a list of what exists.
    """
    entries = inventory() if entries is None else entries

    counts = {name: 0 for name in CATEGORIES}

    for entry in entries:
        counts[entry.category] += 1

    return counts


def uncovered_categories(entries=None):
    """Categories with no test at all."""
    counts = category_counts(entries)

    return [name for name in CATEGORIES if counts[name] == 0]


# ---------------------------------------------------------------------------
# Report
# ---------------------------------------------------------------------------

def format_report(entries=None):
    entries = inventory() if entries is None else entries

    split = automation_split(entries)
    drift = drift_from_target(entries)
    counts = category_counts(entries)

    host = len([e for e in entries
                if e.automation == AUTOMATED
                and (e.note or "").replace("\\", "/").startswith(
                    HOST_TEST_ROOT + "/")])

    lines = [
        "Test inventory: {} tests".format(len(entries)),
        "  of which {} are host unit tests ({}/), which test the framework"
        .format(host, HOST_TEST_ROOT),
        "  rather than the board. They count as automated below.",
        "",
        "Automation split          actual   target   drift",
        "-" * 50,
    ]

    for name in AUTOMATION_CLASSES:
        lines.append("  {:<22} {:>5.1f}%   {:>5.1f}%   {:>+5.1f}".format(
            name, split[name], TARGET_SPLIT[name], drift[name]
        ))

    lines += [
        "",
        "  on target: {}".format("yes" if on_target(entries) else "no"),
        "",
        "Coverage by category",
        "-" * 50,
    ]

    for name in CATEGORIES:
        marker = "   " if counts[name] else "  !"
        lines.append("{} {:<18} {:>4}   {}".format(
            marker, name, counts[name], CATEGORY_PURPOSE[name]
        ))

    missing = uncovered_categories(entries)

    if missing:
        lines += [
            "",
            "  No test exercises: {}".format(", ".join(missing)),
            "  A category with no tests is a class of bug nothing looks for.",
        ]

    return "\n".join(lines)


def main(argv=None):
    # An argument parser, so that --help prints help.
    import argparse

    parser = argparse.ArgumentParser(
        description="Print the test inventory and its distance from the "
                    "65/25/10 automation target")

    parser.parse_args(argv)

    print(format_report())

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
