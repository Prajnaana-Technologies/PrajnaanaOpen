# Copyright (c) 2026 Prajnaana Technologies Pvt. Ltd.
# Original author: Dhanya Shree S
# SPDX-License-Identifier: MIT
#
# Licensed under the MIT License. See LICENSE in the project root.

"""One document describing everything three inputs agree and disagree about.

    requirements document ─┐
    release notes ─────────┼──► canonical document ──► pytest ──► the bench
    firmware image ────────┘

The document is the seam, and it lives in memory: the orchestrator hands
build()'s result straight to the generator, so a run writes no file and can
never pick up a stale one. `--out FILE` writes a copy when you want to read
one. A run never reads that copy back, but the two-step CLI does:
generate_from_canonical takes the file it wrote as its argument.

Everything upstream of the document is analysis -- reading prose, reading
Kconfig, deciding what a change implies. Everything downstream is
execution, and execution should never have to re-read a release note or
guess what a sentence meant. A generator that parses English at the moment
it emits a test is a generator nobody can debug.

It is also where a model can be added safely, and where one is: with
HA_AI=llm, ai_cases.py writes or extends the test list below. It is confined
to this document. Python reads the document and drives the board, so a
hallucinated sentence becomes a malformed step that fails validation, rather
than an unexpected instruction reaching a device.

Each test carries its own setup, steps, expected conditions and cleanup, so
a runner needs no knowledge of what the test is for:

    {"test_id": "BLE-006", "category": "boundary", "priority": "high",
     "setup": ["connect_ble"],
     "steps": [{"action": "send_ble_packet", "size": 128}],
     "expected": [{"condition": "packet_accepted", "value": true}],
     "timeout_ms": 2000, "cleanup": ["disconnect_ble"]}

A step naming an action the bench has no primitive for is kept, marked not
executable, and given the reason. The specification stays complete while the
report stays honest -- when the hardware arrives, the tests are already
written.

    python -m regression.ai_engine.canonical --note NOTE.md --requirements REQ.md
"""

import json
import os
import re
import sys
from datetime import datetime, timezone

from regression.ai_engine import change_spec, requirement_tests, test_spec
from regression.change_detection import firmware_facts
from regression.paths import default_release_note, default_requirements

SCHEMA = "regression.canonical/1"

# Actions the bench can perform, mapped to the RealBLEDevice method that
# performs them. An action absent from here is specified but not runnable,
# and says so rather than being quietly dropped.
ACTIONS = {
    "connect_ble": "connect",
    "disconnect_ble": "disconnect",
    "send_ble_packet": "write",
    "stream_audio": "send_audio_stream",
    "read_build_id": "read_build_id",
    "read_telemetry": "read_telemetry",
    "read_memory": "read_memory",
    "read_battery": "read_battery",
    "read_retry_count": "read_retry_count",
    "pair": "pair",
    "unpair": "unpair",
    "read_bond_count": "read_bond_count",
    "check_encrypted": "is_encrypted",
}

# Named so the reason travels with the test rather than living in a wiki.
NO_PRIMITIVE = {
    "read_temperature": "no temperature interface on this target",
    "measure_power": "needs an external analyzer; the board cannot measure "
                     "its own supply",
    "flash_firmware": "the executor has no flashing step yet",
    "reset_device": "the executor has no reset step yet",
    "read_gpio": "no GPIO harness on this bench",
    "uart_send": "no UART pipeline on this bench",
    "uart_expect": "no UART pipeline on this bench",
}

# Requirements a bench measurement can verify, matched on the requirement's
# own prose the way requirement_tests.CONFIG_FOR matches settings. Each
# names a measurement in regression/measurements.py and the value the
# requirement's limits apply to. The numbers are never taken from here:
# they are read out of the requirement, so editing the requirement moves
# the limit the test asserts.
MEASURED = (
    (("signal-to-noise",), "snr", "snr_db"),
    (("latency",), "latency", "latency_ms"),
    (("gain for quiet input",), "compression", "quiet_over_loud_gain_percent"),
    (("applied gain shall remain",), "gain_range", ("min_gain", "max_gain")),
    (("opposite sign",), "sign_flips", "loud_sign_flip_percent"),
    (("attack", "release"), "compressor_timing", ("attack_ms", "release_ms")),
    (("battery level shall be between",), "battery", "battery_percent"),
    (("stack high-water",), "stack", "stack_kib"),
    (("build identifier",), "build_id", "build_id_characters"),
)


def _value_keys():
    """{measurement: the value names its limits can be stated against}.

    measurements.take() returns a dictionary keyed by value name --
    "stack_kib", not "stack" -- and measurements.check() looks the value up
    by that key. A check naming the measurement instead raises KeyError on
    the board, so a model-written limit resolves the value it applies to
    through here. Derived from MEASURED rather than written out a second
    time, so the two cannot drift apart.
    """
    keys = {}

    for _phrases, measurement, values in MEASURED:
        keys[measurement] = ((values,) if isinstance(values, str)
                             else tuple(values))

    return keys


# Read by ai_cases (to resolve the value a model-written limit applies to)
# and by generate_from_canonical (to emit each check under the step that
# measures it).
VALUE_KEYS = _value_keys()


# The compressor timing is fitted, not read, and the fit recovers a known
# constant to about 0.1 per cent (tests/test_measurements.py). The firmware
# is built exactly at the limit REQ-AUD-010 states, so a limit on a fitted
# value carries a tolerance.
#
# TIMING_VALUES is the set it applies to. The tolerance belongs to the
# measurement, not to the requirement that states a limit on it, so a limit
# on one of these values carries it whoever wrote the case: the rules here,
# or the model in ai_cases.
TIMING_TOLERANCE = 0.02
TIMING_VALUES = ("attack_ms", "release_ms")

# Whether a stated bound excludes the limit itself -- "under 2000 ms" does,
# "at most 2000 ms" does not -- is requirement_tests.strictness, where
# STRICT_UPPER and STRICT_LOWER live beside the patterns that read the
# numbers. It is read from there and not restated, so a sentence cannot
# mean one thing to the checks emitted here and another to the cases
# derived there.

# Requirements nothing on this bench can check, and why, in words an
# operator can act on. The last field says which cases it applies to: one
# connection is checked by every test, so only the cases at and past the
# connection limit need a second central.
UNCHECKABLE = (
    (("heap",),
     "The firmware has no heap -- it never allocates memory at run time -- "
     "so heap growth is always zero. A test here would pass without "
     "measuring anything, so it is skipped rather than counted.",
     "all"),
    (("concurrent",),
     "Needs a second Bluetooth central (another adapter or PC) to try a "
     "second connection while the first is open. This bench has one.",
     "limits"),
)

# How long a step of each category is given before it is a failure. A
# stress case repeating an operation a hundred times cannot share a timeout
# with a single connect.
TIMEOUTS_MS = {
    test_spec.FUNCTIONAL: 10000,
    test_spec.BOUNDARY: 5000,
    test_spec.NEGATIVE: 5000,
    test_spec.REGRESSION: 60000,
    test_spec.STRESS: 600000,
    test_spec.SAFETY: 10000,
}

# A safety case failing is a different event from a boundary case failing.
PRIORITIES = {
    test_spec.SAFETY: "critical",
    test_spec.NEGATIVE: "high",
    test_spec.BOUNDARY: "high",
    test_spec.FUNCTIONAL: "high",
    test_spec.REGRESSION: "medium",
    test_spec.STRESS: "medium",
}

# The value half of a boundary case's title: the "50 dB" of "At the stated
# maximum, 50 dB". A value is always written as a number. See _amount().
AMOUNT = re.compile(r"^[-+]?\d")

# "REQ-BLE-001: the device shall accept packets up to the configured limit"
REQUIREMENT = re.compile(
    r"^\s*(?:[-*]\s*)?((?:REQ|SRS|FR|NFR)[-_][A-Za-z0-9_-]+)\s*[:.\-]\s*(.+)$")

# A requirement id usually names its own component, and the id is the more
# reliable signal: "REQ-BLE-001: the device shall accept an audio packet"
# classifies from its prose as audio. The id says BLE, and the id is what
# the author chose.
ID_COMPONENTS = {
    "BLE": "BLE", "BT": "BLE", "BAT": "battery", "AUD": "audio",
    "DSP": "audio", "MEM": "memory", "PWR": "power", "POW": "power",
    "TLM": "telemetry", "SEN": "sensor", "SYN": "sync", "SYNC": "sync",
    "SEC": "security", "ANC": "noise_cancellation",
}

# A component with no entry in test_spec.DRIVABLE skips with a reason; a
# component wrongly identified as one the bench can drive produces a test
# that cannot fail.


SIZE_IN_TEXT = re.compile(r"(\d+)\s*(?:byte|bytes|b)\b", re.IGNORECASE)
COUNT_IN_TEXT = re.compile(r"(\d+)\s*times", re.IGNORECASE)


def read_requirements(path):
    """Requirements from a requirement document.

    Anything shaped like an identifier followed by a sentence. Lines that
    are not requirements are skipped rather than guessed at -- a heading
    turned into a requirement is a test with nothing behind it.

    An id that appears twice is reported. The parser matches the shape
    anywhere, so a paragraph that quotes an id at the start of a line reads
    as a second requirement of that name: prose explaining why REQ-ANC-003
    behaves as it does would read as a REQ-ANC-003 whose text is the rest of
    the sentence, and derive a case from it. The first wins, and the console
    names the dropped one -- silently keeping both would put a requirement
    nobody wrote into the count of what is untested.
    """
    if not path or not os.path.exists(path):
        return []

    found = []

    try:
        with open(path, encoding="utf-8", errors="replace") as handle:
            text = handle.read()
    except OSError:
        return []

    text = change_spec.COMMENT.sub(" ", text)

    seen = {}

    for line in text.splitlines():
        match = REQUIREMENT.match(line)

        if not match:
            continue

        body = " ".join(match.group(2).split()).strip()

        identifier = match.group(1).upper()

        # One requirement per id. A second is a typo or a paragraph that
        # quoted an id at the start of a line, and both derive cases from
        # text nobody wrote as a requirement.
        if identifier in seen:
            # stderr, like every other diagnostic in this module: --out is
            # optional, and without it the document goes to stdout.
            print("Ignored a repeated requirement id in {}: {} is already "
                  "\"{}\"".format(
                      os.path.basename(path), identifier,
                      seen[identifier][:60]), file=sys.stderr)
            continue

        seen[identifier] = body

        component = ""

        for token in re.split(r"[-_]", identifier):
            if token in ID_COMPONENTS:
                component = ID_COMPONENTS[token]
                break

        found.append({
            "id": identifier,
            "text": body,
            "component": component or change_spec.classify_component(body),
        })

    return found


def requirement_texts(text):
    """{id: the sentence behind it} for a requirements document read as prose.

    read_requirements parses a file into records; ai_cases is handed the
    same document as text, because a model is shown the file rather than
    the records. A model-written limit cites an id, and the strictness and
    the tolerance that limit needs come from the sentence behind that id,
    so the text has to be findable from the prose with the same parser the
    rules use. Duplicates are ignored here rather than reported: the report
    belongs to whoever read the file, and it is made there.
    """
    found = {}

    for line in change_spec.COMMENT.sub(" ", text or "").splitlines():
        match = REQUIREMENT.match(line)

        if not match:
            continue

        identifier = match.group(1).upper()

        if identifier in found:
            continue

        found[identifier] = " ".join(match.group(2).split()).strip()

    return found


def _match_requirement(case, requirements):
    """The requirement a case traces to, or "" when none does.

    Matched on component. Coarse on purpose: a wrong trace is worse than no
    trace, because a coverage figure computed from wrong traces reads as
    reassurance.

    A case whose component is unknown traces to nothing. It would otherwise
    match the first requirement whose component is also unknown, which is
    not a correspondence -- it is two absences meeting.
    """
    if case["component"] == change_spec.UNKNOWN_COMPONENT:
        return ""

    for requirement in requirements:
        if requirement["component"] == case["component"]:
            return requirement["id"]

    return ""


def _steps_for(case):
    """The actions a derived case performs.

    Derived from the case's own text, which is generated from a template, so
    the shapes here are the shapes test_spec emits -- not an attempt to
    parse arbitrary English.
    """
    component = case["component"]
    title = case["test"]

    if component == "BLE":
        size = SIZE_IN_TEXT.search(title)

        if size:
            return [{"action": "send_ble_packet", "size": int(size.group(1))}]

        repeats = COUNT_IN_TEXT.search(title)

        if repeats:
            return [{"action": "connect_ble"},
                    {"action": "disconnect_ble"},
                    {"action": "repeat", "times": int(repeats.group(1))}]

        if case["type"] == test_spec.FUNCTIONAL:
            return [{"action": "connect_ble"}]

        return [{"action": "connect_ble"}, {"action": "read_telemetry"}]

    if component == "audio":
        return [{"action": "stream_audio"}]

    if component == "memory":
        return [{"action": "read_memory"}]

    if component == "battery":
        # Level and temperature are both "battery" to the classifier, and
        # only one of them has a primitive. Choosing by what the case says
        # keeps the temperature cases honestly unexercised while the level
        # cases run.
        if "temperature" in title.lower():
            return [{"action": "read_temperature"}]

        return [{"action": "read_battery"}]

    if component == "power":
        return [{"action": "measure_power"}]

    return [{"action": "read_telemetry"}]


def _expected_for(case):
    if case["type"] == test_spec.NEGATIVE:
        return [{"condition": "rejected", "value": True},
                {"condition": "link_survives", "value": True}]

    if case["type"] == test_spec.STRESS:
        return [{"condition": "no_degradation", "value": True}]

    if case["type"] == test_spec.SAFETY:
        return [{"condition": "protection_active", "value": True}]

    return [{"condition": "accepted", "value": True}]


# Steps that apply a value rather than merely exercising a component. A
# boundary or negative case is about a specific number; if no step carries
# that number, the case is not being tested by anything.
PARAMETERISED = ("send_ble_packet", "repeat")


def _drives_its_own_value(case, steps):
    """Whether the steps actually reach the point this case is about.

    A boundary case named "2001 ms" whose only step streams audio does not
    drive latency to 2001 ms -- it streams audio and passes. That is a test
    that reports success without having tested anything, which is worse than
    no test: it occupies a row in the report and reads as coverage.
    """
    if any(step.get("action") in PARAMETERISED for step in steps):
        return True

    if case["type"] in (test_spec.BOUNDARY, test_spec.NEGATIVE):
        return False

    # A functional case derived from a requirement that states a measurable
    # limit is in the same position: "SNR shall be between 0 and 50 dB"
    # verified by streaming audio and checking something came back is not a
    # verification of anything. The existing hand-written test_audio_dsp
    # measures the ratio.
    if case["type"] == test_spec.FUNCTIONAL:
        return not requirement_tests.limits(case["test"])

    return True


def _unsupported(steps):
    """The first step with no primitive behind it, and why."""
    for step in steps:
        action = step.get("action", "")

        if action in NO_PRIMITIVE:
            return action, NO_PRIMITIVE[action]

        if action not in ACTIONS and action not in ("repeat", "measure"):
            return action, "no primitive named {}".format(action)

    return "", ""


def _matching(text, table):
    """The first row of `table` whose phrases all appear in `text`."""
    lowered = text.lower()

    for row in table:
        if all(phrase in lowered for phrase in row[0]):
            return row

    return None


def _checks_for(text, values):
    """The comparisons a measured requirement makes, limits read from it."""
    if values == TIMING_VALUES:
        checks = []

        for phase, value in (("attack", "attack_ms"), ("release", "release_ms")):
            found = re.search(r"{}\s+within\s+([0-9.]+)\s*ms".format(phase),
                              text, re.IGNORECASE)

            if found:
                checks.append({"condition": "within", "value": value,
                               "max": float(found.group(1)), "unit": "ms",
                               "tolerance": TIMING_TOLERANCE})

        return checks

    bounds = requirement_tests.limits(text)
    min_strict, max_strict = requirement_tests.strictness(text)

    if isinstance(values, tuple):
        # The lowest measured value answers the lower limit, the highest the
        # upper one.
        lowest, highest = values
        checks = []

        if "min" in bounds:
            checks.append({"condition": "within", "value": lowest,
                           "min": bounds["min"], "min_strict": min_strict})

        if "max" in bounds:
            checks.append({"condition": "within", "value": highest,
                           "max": bounds["max"], "max_strict": max_strict})

        return checks

    check = {"condition": "within", "value": values,
             "unit": bounds.get("unit", "")}

    if "min" in bounds:
        check.update({"min": bounds["min"], "min_strict": min_strict})

    if "max" in bounds:
        check.update({"max": bounds["max"], "max_strict": max_strict})

    return [check] if ("min" in check or "max" in check) else []


def _amount(case):
    """The value a boundary case names: "50 dB" from "At the stated maximum, 50 dB".

    Empty when the title carries no value. A case derived from a release-note
    line has a title like "Confirm removed: `CONFIG_BT_GATT_DYNAMIC_DB`" with
    no comma in it, and returning the whole title there would produce a
    sentence such as "cannot make the device produce exactly Confirm
    removed: ...".

    A comma is not enough on its own, because a release-note line can carry
    one of its own: "Confirm removed: Removed the knee branch from
    `wdrc_process`, leaving a fixed gain at `WDRC_GAIN_CEIL`" has a comma
    but no value. A title that does name a value names it as a number,
    which is what
    AMOUNT asks for.
    """
    title = case["test"]

    if "," not in title:
        return ""

    tail = title.split(",", 1)[-1].strip()

    return tail if AMOUNT.match(tail) else ""


def _uncheckable(case, requirement_text):
    """Why nothing on this bench can check this case, or "" if something can."""
    row = _matching(requirement_text, UNCHECKABLE)

    if not row:
        return ""

    if row[2] == "limits" and case["type"] not in (
            test_spec.BOUNDARY, test_spec.NEGATIVE):
        return ""

    return row[1]


def _plain_reason(case, requirement_text):
    """Why a case that cannot run is skipped, for someone reading the report."""
    measured = _matching(requirement_text, MEASURED)

    amount = _amount(case)
    at_value = ("make the device produce exactly {}".format(amount)
                if amount else
                "make the device reach the condition this case names")

    # "at and past the limit" only where there is a limit. A case from a
    # release-note line names a condition rather than a
    # value -- "Confirm removed: CONFIG_BT_GATT_DYNAMIC_DB" has nothing to
    # be at or past -- so the sentence must not speak of a limit.
    what = ("Testing at and past the limit needs firmware that can be told "
            "to misbehave." if amount else
            "Testing it needs firmware that can be told to misbehave.")

    if measured and case["type"] in (test_spec.BOUNDARY, test_spec.NEGATIVE):
        return ("The bench measures this (see the Verify case for {}), but "
                "cannot {}. {}").format(
                    case.get("requirement"), at_value, what)

    if case["type"] in (test_spec.BOUNDARY, test_spec.NEGATIVE):
        return "The bench cannot {}. {}".format(at_value, what)

    return ("The requirement states a number, and nothing on this bench "
            "measures it. Exercising the component and passing would claim "
            "coverage the run did not deliver.")


def to_executable(case, requirements):
    """One derived case in the shape a runner can execute."""
    steps = _steps_for(case)
    expected = _expected_for(case)
    action, reason = _unsupported(steps)

    texts = {r["id"]: r["text"] for r in requirements}
    requirement_text = texts.get(case.get("requirement"), "") \
        if case.get("source") == "requirement" else ""

    # A requirement's own nominal case, where the bench can measure the
    # quantity the requirement names: measure it and compare with the
    # requirement's numbers.
    measured = _matching(requirement_text, MEASURED)

    if measured and case["type"] == test_spec.FUNCTIONAL and not reason \
            and case["executable"]:
        checks = _checks_for(requirement_text, measured[2])

        if checks:
            steps = [{"action": "measure", "measurement": measured[1]}]
            expected = checks

    measures = any(step.get("action") == "measure" for step in steps)

    # A case can be un-runnable for two different reasons: its component has
    # no interface here, or its steps name an action nothing implements.
    # Both are worth distinguishing in the report.
    if not case["executable"]:
        executable, skip_reason = False, case["skip_reason"]
    elif reason:
        executable, skip_reason = False, reason
    elif _uncheckable(case, requirement_text):
        executable, skip_reason = False, _uncheckable(case, requirement_text)
    elif not measures and not _drives_its_own_value(case, steps):
        executable, skip_reason = False, _plain_reason(case, requirement_text)
    else:
        executable, skip_reason = True, ""

    needs_link = case["component"] in ("BLE", "audio", "memory", "telemetry")

    return {
        "test_id": case["id"],
        "source": case.get("source", "release_note"),
        "requirement": case.get("requirement") or _match_requirement(
            case, requirements),
        "description": case["test"],
        "category": case["type"].lower(),
        "priority": PRIORITIES.get(case["type"], "medium"),
        "component": case["component"],
        "setup": ["connect_ble"] if needs_link else [],
        "steps": steps,
        "expected": expected,
        "timeout_ms": TIMEOUTS_MS.get(case["type"], 10000),
        "cleanup": ["disconnect_ble"] if needs_link else [],
        "executable": executable,
        "skip_reason": skip_reason,
        "unsupported_action": action,
    }


def _apply_case_source(derived, requirements_text, note_text, ai_cases):
    """Decide who writes this run's cases, per HA_CASE_SOURCE.

    auto  -- with HA_AI=llm, a model when a credential reaches one and the
             rules otherwise. This is the requested default for a run that
             asked for a model: give it a key and the model writes the
             cases; take the key away and the rules do.
    ai    -- the same, but say so loudly when there is no engine.
    rules -- the rules alone, whatever credentials exist.
    both  -- the rules, plus whatever extra a model proposes.

    Every mode above is gated on HA_AI=llm by ai_cases.reachable_engine and
    ai_cases.propose, so HA_AI alone decides whether anything leaves the
    machine and HA_CASE_SOURCE only decides who writes what when it does.

    Whatever the setting, an empty answer from a model means the derived
    cases stand. A model that cannot answer must never shorten the suite:
    that would turn an outage into silently reduced coverage, which is the
    one failure a regression suite cannot be allowed to have.
    """
    choice = ai_cases.source()

    if choice == ai_cases.RULES_ONLY:
        return derived

    if choice == ai_cases.BOTH:
        extra = ai_cases.propose(requirements_text, note_text, derived)
        seen = {case["test_id"] for case in derived}

        return derived + [c for c in extra if c["test_id"] not in seen]

    engine, reason = ai_cases.reachable_engine()

    if engine is None:
        # The rules are the generator. Said twice as loudly in "ai", where
        # the operator asked for a model specifically. The two modes say it
        # differently, so a reader of the log can tell "ai" from "auto"
        # without knowing which was asked for.
        if choice == ai_cases.AI_ONLY:
            ai_cases.say(
                "[cases] {}={} asked for a model and none is reachable ({}). "
                "The rules generated this suite.".format(
                    ai_cases.SOURCE_ENV, choice, reason))
        elif ai_cases.enabled():
            ai_cases.say("[cases] rules: no model reachable ({})".format(reason))
        else:
            # A default run attempts nothing unless HA_AI=llm. The reason
            # alone is the whole message.
            ai_cases.say("[cases] rules ({})".format(reason))

        return derived

    written = ai_cases.generate_all(requirements_text, note_text, engine)

    if not written:
        ai_cases.say("[cases] rules: {} returned no cases, so the {} derived "
                     "case(s) stand".format(engine.name, len(derived)))

        return derived

    # The comparison, every run. A model writing fewer cases than the rules
    # would have is not necessarily wrong -- it does not emit a boundary
    # case for a requirement it judged untestable -- but it is always worth
    # seeing, and it is invisible unless printed.
    ai_cases.say("[cases] {}: {} case(s) written by the model; the rules "
                 "would have derived {}".format(
                     engine.name, len(written), len(derived)))

    if len(written) < len(derived):
        ai_cases.say(
            "        {} fewer. Set {}=both to keep the derived cases and "
            "add the model's on top.".format(
                len(derived) - len(written), ai_cases.SOURCE_ENV))

    return written


def _requirements_text(path):
    """The requirements document as prose, for a reader that is not a parser.

    read_requirements returns parsed records; a model is shown the file, so
    a sentence the parser skipped is still visible to it.
    """
    if not path or not os.path.exists(path):
        return ""

    try:
        with open(path, encoding="utf-8", errors="replace") as handle:
            return handle.read()
    except OSError:
        return ""


def build(note_text="", requirements_path="", config_path=None):
    """The canonical document for one release."""
    spec = change_spec.extract(note_text)
    known = firmware_facts.read_config(config_path)
    built = firmware_facts.facts(config_path)
    requirements = read_requirements(requirements_path)

    checked = firmware_facts.verify(spec, known)

    # Two generators, two different questions. The release note generates
    # cases about what changed; the requirements generate cases that should
    # run whether or not anything changed. A requirement nobody has touched
    # in six months is still a requirement.
    # One counter for both generators: an id must mean one case.
    counters = {}

    from_note = test_spec.derive(spec, counters)

    for case in from_note:
        case["source"] = "release_note"

    from_requirements = requirement_tests.derive(requirements, known, counters)

    for case in from_requirements:
        case["source"] = "requirement"

    cases = [to_executable(case, requirements)
             for case in from_note + from_requirements]

    # A third source, and the only one a model writes. Off unless HA_AI=llm,
    # and it returns the derived cases rather than raising for any reason at
    # all, so a run with no model reachable builds the document the rules
    # alone build. Applied after to_executable because ai_cases already
    # decides what is runnable -- it validates against the same closed action
    # vocabulary the renderer can express, and marks the rest not executable
    # with a reason.
    from regression.ai_engine import ai_cases

    cases = _apply_case_source(
        cases, _requirements_text(requirements_path), note_text, ai_cases)

    runnable = [c for c in cases if c["executable"]]
    flagged = firmware_facts.contradictions(checked)

    return {
        "schema": SCHEMA,
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "version": spec["version"],
        "firmware": {
            "build_id": built["build_id"],
            "os_version": built["os_version"],
            "device_name": built["device_name"],
            "memory": built["memory"],
            "settings": len(known),
        },
        "requirements": requirements,
        "changes": checked,
        "tests": cases,
        "summary": {
            "requirements": len(requirements),
            "changes": len(checked),
            "unverified_changes": len(flagged),
            "tests": len(cases),
            "tests_from_release_note": len(from_note),
            "tests_from_requirements": len(from_requirements),
            "runnable": len(runnable),
            "not_runnable": len(cases) - len(runnable),
        },
    }


def note_section(text, config_path=None):
    """The part of a release note that describes the build under test.

    A release-note file accumulates. Read whole, a file covering ten
    releases derives ten releases' worth of cases, and the set only grows.
    orchestrator.section_for narrows it to the section whose heading names
    the build. The CLI and the run narrow the note the same way, so one note
    gives one document whichever entry point builds it.

    The build id comes from HA_BUILD_ID when the caller set one, and
    otherwise from the built image the --config tree belongs to. Not the
    same order as orchestrator.resolve_build_id, which asks the board
    second: there is no board on the other end of a CLI invocation, and the
    image is the next best statement of what was built.
    """
    from regression.ai_engine.orchestrator import BUILD_ID_ENV, section_for

    build_id = os.getenv(BUILD_ID_ENV, "") or facts_build_id(config_path)

    return section_for(text, build_id), build_id


def facts_build_id(config_path=None):
    """The build id the image reports, or "" when no image can be read."""
    return firmware_facts.facts(config_path).get("build_id", "")


def main(argv=None):
    import argparse

    parser = argparse.ArgumentParser(
        description="Build the canonical test document")
    # The defaults are the documents a run uses, so the documented
    # `canonical --out canonical.json` builds the document a run builds.
    parser.add_argument("--note", default=default_release_note(),
                        help="release note (default: the one beside the "
                             "application)")
    parser.add_argument("--requirements", default=default_requirements(),
                        help="requirement document (default: the one beside "
                             "the application)")
    parser.add_argument("--config", default=None, help="firmware .config")
    parser.add_argument("--out", default="",
                        help='write JSON here; "-" is stdout, which is also '
                             "where it goes when --out is left out")

    args = parser.parse_args(argv)

    note_text = ""

    if args.note and not os.path.exists(args.note):
        print("Release note not found, ignoring:", args.note, file=sys.stderr)

    if args.requirements and not os.path.exists(args.requirements):
        print("Requirement document not found, ignoring:", args.requirements,
              file=sys.stderr)

    if args.note and os.path.exists(args.note):
        with open(args.note, encoding="utf-8", errors="replace") as handle:
            note_text = handle.read()

        whole = note_text
        note_text, build_id = note_section(note_text, args.config)

        # To stderr, not stdout. Without --out the document itself is
        # printed to stdout, so a progress line there lands in the middle of
        # the JSON and `... > canonical.json` stops parsing.
        if note_text != whole:
            print("Release note narrowed to the section for", build_id,
                  file=sys.stderr)
        elif build_id:
            print("No section in the note names", build_id,
                  "-- reading it whole", file=sys.stderr)

    document = build(note_text, args.requirements, args.config)
    rendered = json.dumps(document, indent=2)

    # "-" means stdout, the same as leaving --out out.
    if args.out and args.out != "-":
        with open(args.out, "w", encoding="utf-8") as handle:
            handle.write(rendered)

        print("Written:", args.out)

        for key, value in document["summary"].items():
            print("  {:<20} {}".format(key, value))
    else:
        print(rendered)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
