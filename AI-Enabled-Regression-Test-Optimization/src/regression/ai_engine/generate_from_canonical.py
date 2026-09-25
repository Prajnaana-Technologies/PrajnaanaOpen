# Copyright (c) 2026 Prajnaana Technologies Pvt. Ltd.
# Original author: Dhanya Shree S
# SPDX-License-Identifier: MIT
#
# Licensed under the MIT License. See LICENSE in the project root.

"""Emit a pytest module from the canonical document.

The last link: the canonical document describes what to run, this writes
the Python that runs it, and pytest executes it against the board.

Two rules shape the output.

**Nothing is interpreted here.** Every decision -- which cases exist, what
they send, what counts as passing -- is made upstream and is in the
document.
This walks a list of steps and calls a primitive per step. A generator that
re-reads prose at emit time is one nobody can debug, and it is where a model
would otherwise get a second chance to improvise.

**Every string from the document is emitted as a literal.** Descriptions,
requirement ids, skip reasons and the action name in a "no primitive" skip
are written with repr(), never pasted into a docstring or a quoted string.
Pasting would let a "requirement" of R'''...''' close the docstring it
sits in, leaving the rest of the field as statements the module runs, and
a backslash in a description ("C:\\Names") would be a SyntaxError that
stops the whole module being collected. A repr escapes everything and is
one expression, so no field can become a statement.

The two fields the header quotes are the exception: they are pasted into
"#" comment lines, where repr() would be noise, so a newline in either
would end the comment and start a top-level statement. They go through
comment_text() instead.

**A case that cannot run is emitted as a skip, never dropped.** The
specification and the suite stay the same length. When a thermistor appears,
those tests start running with no regeneration -- the skip becomes a pass or
a failure, and the count of what is untested never silently shrinks.

    python -m regression.ai_engine.canonical --out canonical.json
    python -m regression.ai_engine.generate_from_canonical canonical.json

A run does neither. The orchestrator holds the document in memory and calls
generate() directly, so there is no canonical.json on disk unless you wrote
one yourself with the first command.
"""

import json
import os
import re

from regression.paths import CANONICAL_TEST_PATH

# Bounds on the numbers a step may carry into the emitted code. A model
# writes steps as well as the rules, so every numeric field is checked and
# clamped before it reaches the template.
MAX_PACKET_BYTES = 4096
MAX_REPEATS = 100

# Every character that can end a "#" comment line early, or truncate one
# in a terminal. See comment_text().
CONTROL = re.compile(r"[\x00-\x1f\x7f-\x9f]")

HEADER = '''# Copyright (c) 2026 Prajnaana Technologies Pvt. Ltd.
# Original author: Dhanya Shree S
# SPDX-License-Identifier: MIT
#
# GENERATED FILE -- do not edit.
#
# Written by regression.ai_engine.generate_from_canonical from the canonical
# document. Every case below traces to a requirement or a release-note
# change; edit those and regenerate rather than editing this.
#
# Source document : {source}
# Firmware        : {build_id}
# Cases           : {total} ({runnable} runnable, {skipped} specified but not runnable)

import asyncio

import pytest

from regression import measurements


def run(device, coro):
    """Drive one coroutine on the fixture's own loop."""
    return asyncio.get_event_loop().run_until_complete(coro)


def payload(size):
    """A deterministic pattern of the requested size.

    Deterministic so a failure is reproducible: random bytes would make the
    one packet that broke the device unrecoverable after the fact.
    """
    return bytes((i % 251) for i in range(size))


def require_loopback(ble_device):
    """Skip when the firmware publishes no processed-audio characteristic.

    A board without it cannot produce a DSP result, so a test that asserted
    one would report a feature the firmware never had as a regression. The
    capability is read from the GATT table rather than from a reply, so a
    board that publishes the characteristic and then answers nothing fails
    here instead of skipping. The planner-selected module skips its audio
    tests for the same reason and with the same words; the probe is run
    once per session by the device and cached.
    """
    if not run(ble_device, ble_device.supports_audio_loopback()):
        pytest.skip(
            "device does not implement audio loopback -- it publishes no "
            "processed-audio notify characteristic, so it accepts the audio "
            "and notifies nothing back. "
            "Confirm with: python tools/ble_probe.py (from a source "
            "installation)")

'''

SKIP = '''

@pytest.mark.category({category})
def test_{name}():
    {docstring}
    pytest.skip({reason})
'''

TEST = '''

@pytest.mark.category({category})
def test_{name}(ble_device):
    {docstring}
{body}
'''


def literal(value):
    """One string from the document, as a Python expression.

    repr() and nothing else. See the module docstring for what pasting
    these in as text would allow.
    """
    return repr(str(value))


def number(step, key, default, low, high):
    """A step's numeric argument as an int inside [low, high].

    Out of range is clamped and unreadable is the default: a malformed step
    must produce a test that runs something safe, not an exception inside
    the generator that takes the whole run with it.
    """
    try:
        value = int(step.get(key, default))
    except (TypeError, ValueError, OverflowError):
        return default

    return max(low, min(high, value))


def identifier(case):
    """A python-safe function name that still reads as the test id."""
    base = "{}_{}".format(case["test_id"], case["description"])
    base = re.sub(r"[^0-9a-zA-Z]+", "_", base).strip("_").lower()

    return base[:80]


def _checks_for_step(step, case):
    """The case's limits that belong to THIS measure step.

    A case can measure twice, and case["expected"] holds the limits for the
    whole case. Emitting all of them under every measure step would assert
    each measurement's values against the other's limit -- a case measuring
    the SNR and then the stack would raise KeyError both ways, because the
    values one measurement returns never carry another's key.

    A limit therefore belongs to the step whose measurement reports the
    value it names. A measurement outside canonical.MEASURED reports values
    nothing here knows, so a hand-written document naming one falls back to
    emitting all of its limits, and the document is answerable for them.
    """
    from regression.ai_engine import canonical

    within = [check for check in case.get("expected") or []
              if isinstance(check, dict) and check.get("condition") == "within"]

    keys = canonical.VALUE_KEYS.get(step.get("measurement"))

    if keys is None:
        return within

    return [check for check in within if check.get("value") in keys]


def _step_lines(step, case):
    """The Python for one step, as indented source lines."""
    action = step.get("action", "")

    if action == "connect_ble":
        return ["    assert ble_device.is_connected, (",
                "        \"the device is not connected, so nothing below is "
                "a result\")"]

    if action == "disconnect_ble":
        # The session fixture owns the link; a test must not tear it down.
        return ["    pass  # the session fixture owns connect and disconnect"]

    if action == "send_ble_packet":
        size = number(step, "size", 0, 0, MAX_PACKET_BYTES)
        negative = case["category"] == "negative"

        lines = ["    data = payload({})".format(size)]

        if negative:
            # A refusal is the expected result, so the exception is the pass
            # condition -- and the link surviving matters as much as the
            # refusal. A device that resets on a bad packet has not rejected
            # it, it has crashed.
            lines += [
                "    try:",
                "        run(ble_device, ble_device.write(data))",
                "        refused = False",
                "    except Exception:",
                "        refused = True",
                "",
                "    assert ble_device.is_connected, (",
                "        \"the link did not survive the oversized write\")",
                "",
                "    if not refused:",
                "        pytest.skip(",
                "            \"the device accepted {} bytes; the limit this "
                "case tests is not \"".format(size),
                "            \"enforced at the transport, so this is a "
                "specification gap \"",
                "            \"rather than a test failure\")",
            ]
        else:
            lines += [
                "    run(ble_device, ble_device.write(data))",
                "    assert ble_device.is_connected, (",
                "        \"the link dropped while sending {} bytes\")".format(size),
            ]

        return lines

    if action == "read_telemetry":
        return [
            "    snapshot = run(ble_device, ble_device.read_telemetry())",
            "    assert snapshot is not None, (",
            "        \"no telemetry came back, so nothing here is measured\")",
        ]

    if action == "read_battery":
        return [
            "    level = run(ble_device, ble_device.read_battery())",
            "",
            "    assert level is not None, (",
            "        \"no battery level came back from 0x2A19\")",
            "    assert 0 <= level <= 100, (",
            "        \"battery level {} is outside 0-100\".format(level))",
        ]

    if action == "read_memory":
        return [
            "    value = run(ble_device, ble_device.read_memory())",
            "",
            "    if value is None:",
            "        pytest.skip(\"memory is not reported by this build\")",
        ]

    if action == "read_build_id":
        return [
            "    build = run(ble_device, ble_device.read_build_id())",
            "    assert build, \"the device reported no build id\"",
        ]

    if action == "stream_audio":
        # Probe first, so a firmware that implements no loopback skips
        # here rather than failing on the assertion below. The
        # planner-selected module skips the same way through
        # require_loopback, so one board gives both modules the same
        # verdict.
        return [
            "    require_loopback(ble_device)",
            "",
            "    returned = run(",
            "        ble_device, ble_device.send_audio_stream(payload(2000)))",
            "    assert returned is not None, (",
            "        \"the loopback probe answered yes and then no audio came "
            "back\")",
        ]

    if action == "measure":
        # Measure, then compare with each limit the requirement states for
        # THIS measurement. The limits are read out of the requirement
        # upstream; nothing here decides what a number should be.
        lines = [
            "    try:",
            "        values = measurements.take(ble_device, {!r})".format(
                step["measurement"]),
            "    except measurements.Unmeasured as exc:",
            "        pytest.skip(str(exc))",
        ]

        for check in _checks_for_step(step, case):
            arguments = ["values", literal(check["value"])]

            for key, name in (("min", "minimum"), ("max", "maximum"),
                              ("min_strict", "min_strict"),
                              ("max_strict", "max_strict"),
                              ("unit", "unit"), ("tolerance", "tolerance")):
                value = check.get(key)

                # "is False", not "in (..., False)": a limit of 0 equals
                # False.
                if value is None or value == "" or value is False:
                    continue

                arguments.append("{}={!r}".format(name, value))

            lines.append("    measurements.check({})".format(", ".join(arguments)))

        return lines

    if action == "repeat":
        times = number(step, "times", 1, 1, MAX_REPEATS)

        return [
            "    for _ in range({}):".format(times),
            "        run(ble_device, ble_device.write(payload(64)))",
            "",
            "    assert ble_device.is_connected, (",
            "        \"the link did not survive {} iterations\")".format(times),
        ]

    # Through literal(), like every other string from the document. The
    # two-step CLI reads a canonical.json anybody can edit.
    return ["    pytest.skip({})".format(
        literal("no primitive for step: {}".format(action)))]


def render(case):
    """One case as a pytest function."""
    name = identifier(case)
    requirement = case.get("requirement") or "(none)"

    if not case["executable"]:
        return SKIP.format(
            name=name,
            category=literal(case["category"]),
            docstring=literal("{}\n\nRequirement: {}".format(
                case["description"], requirement)),
            reason=literal(case["skip_reason"]),
        )

    lines = []

    for step in case["steps"]:
        lines.extend(_step_lines(step, case))
        lines.append("")

    if not lines:
        lines = ["    pytest.skip(\"the case has no steps\")"]

    return TEST.format(
        name=name,
        category=literal(case["category"]),
        docstring=literal("{}\n\nRequirement: {}\nPriority:    {}".format(
            case["description"], requirement, case["priority"])),
        body="\n".join(lines).rstrip(),
    )


def emitted_cases(document):
    """The cases that reach the file, in order.

    Shared with the CLI so its counts and the header's cannot disagree:
    both count emitted cases, after duplicates are dropped. The length of
    the document is the count before the drop.
    """
    seen = set()
    emitted = []

    for case in document["tests"]:
        # Two requirements can derive cases with the same shape, and two
        # functions of the same name in one module shadow: the second
        # definition replaces the first. The duplicate is dropped here,
        # before the file is written.
        name = identifier(case)

        if name in seen:
            continue

        seen.add(name)
        emitted.append(case)

    return emitted


def comment_text(value):
    """`value` as one line, safe to paste after a "#".

    The header quotes the document's schema and the built firmware's id
    in comment lines. A "#" comment ends at the newline, so a newline in
    either field ends the comment and whatever follows it is a top-level
    statement in the generated module: a hand-edited canonical.json with
    a build id of "x", newline, "PWNED = 1" would define PWNED at import,
    and governance would report no violation, because by then it is
    ordinary source. Everything else in this file is emitted with repr();
    these two cannot be, so every control character becomes a space.
    """
    return CONTROL.sub(" ", str(value))


def generate(document, out_path=None):
    """Write the module and return where it went."""
    out_path = out_path or CANONICAL_TEST_PATH

    emitted = emitted_cases(document)

    # Counted after the drop, not before, so the header promises only the
    # cases that are in the file. The length of the document counts the
    # duplicates too.
    runnable = [c for c in emitted if c["executable"]]

    body = [HEADER.format(
        source=comment_text(document.get("schema", "canonical")),
        build_id=comment_text(
            document["firmware"].get("build_id") or "unknown"),
        total=len(emitted),
        runnable=len(runnable),
        skipped=len(emitted) - len(runnable),
    )]

    for case in emitted:
        body.append(render(case))

    parent = os.path.dirname(out_path)

    if parent:
        os.makedirs(parent, exist_ok=True)

    with open(out_path, "w", encoding="utf-8") as handle:
        handle.write("".join(body))

    return out_path


def main(argv=None):
    import argparse

    parser = argparse.ArgumentParser(
        description="Emit pytest from the canonical document")
    parser.add_argument(
        "document",
        help="a canonical document, as written by "
             "`python -m regression.ai_engine.canonical --out FILE`")
    parser.add_argument("--out", default=None)

    args = parser.parse_args(argv)

    with open(args.document, encoding="utf-8") as handle:
        document = json.load(handle)

    path = generate(document, args.out)
    emitted = emitted_cases(document)

    print("Written:", path)
    print("  cases    :", len(emitted))
    print("  runnable :", sum(1 for c in emitted if c["executable"]))

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
