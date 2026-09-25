# Copyright (c) 2026 Prajnaana Technologies Pvt. Ltd.
# Original author: Dhanya Shree S
# SPDX-License-Identifier: MIT
#
# Licensed under the MIT License. See LICENSE in the project root.

"""Derive test cases from requirements, parameterised by the built firmware.

Release notes say what changed. Requirements say what must always be true,
and the two generate different tests: a change generates cases about the
change, a requirement generates cases that should run whether or not
anything changed this release. Without the second, a requirement nobody had
touched in six months would be verified by nothing.

Two sources of numbers, and the second is the interesting one:

    from the requirement   "latency shall be under 2000 ms"
                           -> boundary at 2000, negative beyond it

    from the firmware      REQ-BLE-005 is about concurrent connections,
                           the build says CONFIG_BT_MAX_CONN=1
                           -> 0 connections, 1 connection, 2 connections

The second matters because a requirement written in prose rarely repeats the
number the firmware was actually built with, and the firmware's value is the
one the device will enforce. Reading it from the build means the boundary
cases move when the build moves, instead of quietly testing last year's
limit.

A requirement stating no number generates one functional case and nothing
else. Inventing a boundary around an imagined limit tests the imagination.

    python -m regression.ai_engine.requirement_tests REQUIREMENTS.md
"""

import re

from regression.ai_engine import test_spec
from regression.change_detection import firmware_facts

# Negating a floor states a ceiling, and the negation can carry a verb with
# it: "shall not grow by more than 0.25 KiB" is a ceiling, not a floor. Every
# bare phrase below therefore has its negated mirror on the other side, and
# limits() drops whichever match is buried inside the other.
#
# "never" negates exactly as "not" does and reads more naturally in a
# requirement. Both forms live under this one name, so they cannot drift
# apart.
NOT = r"(?:not|never)"
NEGATED = r"(?:shall |must |does |do |did )?" + NOT + r" (?:\w+ ){0,3}"

# "shall not exceed 60 mA", "shall be under 2000 ms", "at most 32 characters"
UPPER = re.compile(
    r"(?:" + NOT + r" exceed|no more than|no greater than|" + NEGATED +
    r"(?:more than|greater than|above)|"
    r"at most|under|below|less than|fewer than|within|up to)"
    r"\s+([0-9]+(?:\.[0-9]+)?)\s*"
    r"([a-zA-Z%]+)?",
    re.IGNORECASE,
)

# "at least 25 percent", "above 0 dB", "no less than 3"
LOWER = re.compile(
    r"(?:at least|no less than|no fewer than|" + NEGATED +
    r"(?:less than|fewer than|below|under)|"
    r"above|greater than|more than|exceed(?:s)? )\s*"
    r"([0-9]+(?:\.[0-9]+)?)\s*([a-zA-Z%]+)?",
    re.IGNORECASE,
)

# "between 0.55 and 0.85"
RANGE = re.compile(
    r"between\s+([0-9]+(?:\.[0-9]+)?)\s*([a-zA-Z%]+)?\s+and\s+"
    r"([0-9]+(?:\.[0-9]+)?)\s*([a-zA-Z%]+)?",
    re.IGNORECASE,
)

# Requirement text -> the Kconfig key holding the limit the device enforces.
# Reuses the mapping firmware_facts already validates notes against, so the
# two never disagree about which setting means what.
CONFIG_FOR = firmware_facts.CLAIM_KEYS

# A Kconfig key named anywhere in the requirement text.
CONFIG_KEY = re.compile(r"\bCONFIG_[A-Z0-9_]+")

# The units these requirements are written in. The bound patterns capture
# whatever word follows the number, and in prose that word is often not a
# unit at all: the "at" of "between 0.55 and 0.85 at all times". A word that
# is not in this list is not a unit, and the quantity is dimensionless --
# losing a unit reads worse than inventing one, but only slightly, and it
# never puts a word in the report that the requirement did not mean as one.
# Counted things belong here too: "no more than 12 retries" states a unit.
UNITS = (
    "ms", "s", "us", "ns",
    "ma", "ua", "a", "mv", "v", "mw",
    "db", "dba", "dbm",
    "hz", "khz",
    "b", "kb", "mb", "kib", "mib", "byte", "bytes",
    "percent", "%",
    "character", "characters", "chars",
    "connection", "connections", "sample", "samples",
    "retry", "retries", "packet", "packets", "stream", "streams",
)


def _unit(word):
    """The unit a bound was stated in, or "" when the word is not one."""
    return word if word and word.lower() in UNITS else ""


def _amount(value, unit):
    """A number as a requirement states it, with its unit.

    ":g" rather than str(): the negative case adds a hundredth to a
    fractional limit, and 3.76 + 0.01 is 3.7699999999999996 in binary
    floating point. The case title, and the skip reason quoting it, told
    the reader the bench cannot produce "exactly 3.7699999999999996 KiB".
    Whole numbers are left to str(), because ":g" would render a large one
    in exponent form.
    """
    text = "{:g}".format(value) if isinstance(value, float) else str(value)

    return "{} {}".format(text, unit).strip()


def _number(text):
    """A float when the text is fractional, an int when it is whole."""
    value = float(text)

    return int(value) if value == int(value) else value


def _step(value):
    """The smallest meaningful step past a limit.

    One for a whole number, since a packet is 129 bytes and never 128.1.
    A hundredth for a fractional one, which is below any tolerance these
    requirements express and above floating-point noise.
    """
    return 1 if isinstance(value, int) else 0.01


def _inside(inner, outer):
    """Whether one match's span sits entirely within another's."""
    return inner.start() >= outer.start() and inner.end() <= outer.end()


def _first_free(pattern, other, text):
    """First match of `pattern` that is not buried inside a match of `other`.

    Each bound phrase contains the other's when it is negated: "no more than
    12" contains "more than 12", and "no less than 3" contains "less than 3".
    Taken at face value the requirement comes out with a floor and a ceiling
    of the same number, and the derived case claims 11 violates "at most 12",
    or that 4 dB violates "at least 3 dB" -- a test asserting the opposite of
    the requirement.

    The buried match is not a bound; the phrase enclosing it is what the
    author wrote. Skipping rather than giving up keeps the real bound in a
    sentence that states both: in "no less than 3 dB and no more than 12 dB"
    the first upper match is buried, the second is the ceiling.
    """
    enclosing = list(other.finditer(text))

    for match in pattern.finditer(text):
        if not any(_inside(match, span) for span in enclosing):
            return match

    return None


def bound_matches(text):
    """(lower, upper): the two matches that state this requirement's bounds.

    Negated phrases enclose their opposite, so each side skips the matches
    buried in the other -- see _first_free. Either may be None.

    Separated out so limits() and strictness() answer from the SAME pair of
    matches. Taking the first UPPER match in the text regardless would, in
    "Gain shall not fall below 1.5 dB and shall be at most 9 dB", pick the
    "below 1.5 dB" buried inside the negated floor: the 9 dB ceiling that
    limits() keeps would then be reported as strict, and the boundary case
    at exactly 9 dB labelled a violation of a requirement it satisfies. A
    range is not asked here -- RANGE states inclusive ends and both callers
    handle it before they get this far.
    """
    return _first_free(LOWER, UPPER, text), _first_free(UPPER, LOWER, text)


def limits(text):
    """Bounds stated in a requirement, as {"min": x, "max": y, "unit": u}."""
    found = {}

    ranged = RANGE.search(text)

    if ranged:
        found["min"] = _number(ranged.group(1))
        found["max"] = _number(ranged.group(3))
        unit = _unit(ranged.group(4)) or _unit(ranged.group(2))

        if unit:
            found["unit"] = unit

        return found

    lower, upper = bound_matches(text)

    if upper:
        found["max"] = _number(upper.group(1))

        if _unit(upper.group(2)):
            found["unit"] = _unit(upper.group(2))

    if lower:
        found["min"] = _number(lower.group(1))

        if _unit(lower.group(2)) and "unit" not in found:
            found["unit"] = _unit(lower.group(2))

    return found


# Whether the stated limit is itself allowed. "at most 2000 ms" includes
# 2000; "under 2000 ms" does not. These, and strictness() below, are the one
# copy: canonical.py imports this module (it already does, for RANGE and the
# bound patterns), so the checks it emits and the boundary cases derived here
# cannot disagree about whether a value sitting exactly on a limit passes.
STRICT_UPPER = ("under", "below", "less than", "fewer than")
STRICT_LOWER = ("above", "greater than", "more than")


def strictness(text):
    """(min_strict, max_strict): whether each bound excludes the limit itself.

    A range ("between 0.55 and 0.85") states inclusive ends, so neither
    side is strict. Otherwise the phrase that introduced the number decides
    -- the phrase limits() read the number from, which is not always the
    first one in the sentence. See bound_matches.
    """
    if RANGE.search(text):
        return False, False

    lower, upper = bound_matches(text)

    def strict(match, words):
        return bool(match) and match.group(0).lower().startswith(words)

    return strict(lower, STRICT_LOWER), strict(upper, STRICT_UPPER)


def config_limit(text, known):
    """The limit the built firmware enforces for this requirement, if any.

    The requirement says what must be true; the build says what the device
    will actually do. When the build has an opinion, it is the one worth
    testing at -- it moves when the firmware moves.
    """
    lowered = text.lower()

    # A requirement that cites its own setting is the strongest signal there
    # is, and authors cite them often: "(source: CONFIG_BT_MAX_CONN=1)".
    # Matching the key by name beats guessing from prose.
    for key in CONFIG_KEY.findall(text):
        if key in known:
            try:
                return key, int(known[key])
            except (TypeError, ValueError):
                continue

    for phrases, keys in CONFIG_FOR:
        if not any(phrase in lowered for phrase in phrases):
            continue

        for key in keys:
            if key not in known:
                continue

            try:
                return key, int(known[key])
            except (TypeError, ValueError):
                continue

    return "", None


def derive_for(requirement, known=None, counters=None):
    """Test cases for one requirement."""
    known = {} if known is None else known
    counters = {} if counters is None else counters

    component = requirement["component"]
    text = requirement["text"]

    def case(title, kind, expected):
        built = test_spec._case(counters, component, title, kind, expected)
        built["requirement"] = requirement["id"]

        return built

    cases = [case(
        "Verify: {}".format(text.rstrip(".")),
        test_spec.FUNCTIONAL,
        "Requirement holds under nominal conditions",
    )]

    key, enforced = config_limit(text, known)

    if enforced is not None:
        # The build's own limit. Below it, at it, and past it -- the three
        # points that distinguish a limit that works from one that is merely
        # declared.
        if enforced > 0:
            cases.append(case(
                "At {} of {}".format(enforced - 1, enforced),
                test_spec.BOUNDARY, "Accepted"))

        cases.append(case(
            "At the built limit, {} ({}={})".format(enforced, key, enforced),
            test_spec.BOUNDARY, "Accepted"))

        cases.append(case(
            "Beyond the built limit, {}".format(enforced + 1),
            test_spec.NEGATIVE, "Refused, and the device stays healthy"))

        return cases

    bounds = limits(text)

    if not bounds:
        return cases

    unit = bounds.get("unit", "")

    def amount(value):
        return _amount(value, unit)

    # "under 2000 ms" excludes 2000, so the case sitting exactly on the
    # limit is a violation, not a pass, and the expectation has to say so.
    # The title names the
    # value and nothing else, because canonical._amount reads the text
    # after the first comma as the amount the bench would have to produce,
    # and only when that text starts with a number.
    min_strict, max_strict = strictness(text)

    def boundary_expectation(strict):
        if strict:
            return "Violates the requirement (strict limit)"

        return "Within the requirement"

    if "max" in bounds:
        top = bounds["max"]
        cases.append(case("At the stated maximum, {}".format(amount(top)),
                          test_spec.BOUNDARY,
                          boundary_expectation(max_strict)))
        cases.append(case(
            "Beyond the stated maximum, {}".format(amount(top + _step(top))),
            test_spec.NEGATIVE, "Violates the requirement and must be caught"))

    if "min" in bounds:
        bottom = bounds["min"]
        cases.append(case("At the stated minimum, {}".format(amount(bottom)),
                          test_spec.BOUNDARY,
                          boundary_expectation(min_strict)))
        cases.append(case(
            "Below the stated minimum, {}".format(
                amount(bottom - _step(bottom))),
            test_spec.NEGATIVE, "Violates the requirement and must be caught"))

    return cases


def derive(requirements, known=None, counters=None):
    """Test cases for every requirement, ids counted across the whole set.

    `counters` is shared with the change generator when both run, so the two
    cannot mint the same id for different cases.
    """
    known = firmware_facts.read_config() if known is None else known
    counters = {} if counters is None else counters
    cases = []

    for requirement in requirements:
        cases.extend(derive_for(requirement, known, counters))

    return cases


def main(argv=None):
    import argparse

    from regression.ai_engine import canonical

    parser = argparse.ArgumentParser(
        description="Derive test cases from a requirement document")
    parser.add_argument("requirements")
    parser.add_argument("--config", default=None)

    args = parser.parse_args(argv)

    requirements = canonical.read_requirements(args.requirements)
    known = firmware_facts.read_config(args.config)
    cases = derive(requirements, known)

    print("{} requirement(s) -> {} case(s), {} setting(s) read from the "
          "build".format(len(requirements), len(cases), len(known)))
    print()
    print(test_spec.format_table(cases))

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
