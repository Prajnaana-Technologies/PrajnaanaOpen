# Copyright (c) 2026 Prajnaana Technologies Pvt. Ltd.
# Original author: Dhanya Shree S
# SPDX-License-Identifier: MIT
#
# Licensed under the MIT License. See LICENSE in the project root.

"""Let a model propose extra cases from the requirements and release note.

canonical.py names this as the one place a model can be added safely, and
this is that addition. What the model does depends on
HA_CASE_SOURCE: in "both" it only appends what the rules could not see -- a
feature described in prose that no pattern matches, an interaction between
two requirements, a failure mode the wording implies -- and in "auto" and
"ai" it writes the requirement cases itself, with the derived cases kept as
the fallback for an empty answer.

Why it is safe to let a model write cases here, when it is not safe to let
it write test code:

  * A case is data, not code. generate_from_canonical renders it through a
    fixed set of templates, one per action in ACTIONS, and emits every
    string from the case with repr(). The one field that reaches Python
    as text rather than as a repr() is "requirement", which is rendered
    into a triple-quoted docstring: a value that closed that docstring
    would turn the rest of itself into statements the module runs. Ids are
    checked against REQUIREMENT_ID here and quoted there.
  * The vocabulary is closed and enforced twice. The request is schema
    constrained to ACTIONS and MEASUREMENTS, and every proposal is checked
    again here after it arrives, because a schema is a request, not a
    guarantee.
  * A case naming something the bench cannot do is not dropped and not run.
    It is marked not executable with a reason, exactly like a rule-derived
    case the bench cannot drive, so it appears in the count of what is
    specified but untested.
  * A limit names the value it applies to. measurements.take() returns
    {"stack_kib": 2.5}, so a check has to say "stack_kib" and not
    "stack" -- a check naming the measurement instead reaches the board and
    fails with KeyError. The value is resolved through
    canonical.VALUE_KEYS, and a measurement returning more than one value
    (gain_range, compressor_timing) has to be told which one in "value".
  * Nothing here can fail a run. Any error -- no engine, no credential, a
    refusal, malformed JSON, a reply of the wrong shape entirely -- leaves
    the document exactly as the rules built it and says why on stderr.
    Every number is read through _bounded, because a model writes these
    fields and int() raises on "abc" and accepts 10**12 as a repeat count,
    and every field that must be a list is checked for being one, because
    "steps": 5 and "setup": 7 would raise TypeError out of to_case and take
    the whole run with them. to_case is called inside a try as well: the claim
    in this sentence is worth no more than the weakest field nobody
    anticipated.

Off unless asked: HA_AI=llm selects it, the same switch that selects the
LLM planner, and it gates every mode including "auto". HA_AI=rules, the
default, never calls a model whatever HA_CASE_SOURCE says and whatever
credentials the machine happens to have. The gate is checked for every
source rather than in propose() alone, because a gate only "both" honoured
would leave "auto" -- the default source -- sending the requirements
document to a vendor on the strength of an exported key, on a run the
operator asked to keep offline.
"""

import os
import re
import sys

MODE_ENV = "HA_AI"
LIMIT_ENV = "HA_AI_CASE_LIMIT"
SOURCE_ENV = "HA_CASE_SOURCE"

AUTO = "auto"
AI_ONLY = "ai"
RULES_ONLY = "rules"
BOTH = "both"

SOURCES = (AUTO, AI_ONLY, RULES_ONLY, BOTH)

DEFAULT_LIMIT = 12

SOURCE = "llm"

# What the renderer can express, and what a requirement id may look like.
# Both are enforced here rather than trusted from the reply: a schema is a
# request, and the loose retry in gemini_client sends no schema at all.
MAX_PACKET_BYTES = 4096
MAX_REPEATS = 100
MAX_SECONDS = 60

REQUIREMENT_ID = re.compile(r"^[A-Za-z][A-Za-z0-9_.-]{0,40}$")

# The actions generate_from_canonical._step_lines can render. Anything else
# becomes a skip naming the missing primitive, so the vocabulary is closed
# here to keep the model inside what the bench can actually do.
ACTIONS = (
    "connect_ble",
    "disconnect_ble",
    "send_ble_packet",
    "read_telemetry",
    "read_battery",
    "read_memory",
    "read_build_id",
    "stream_audio",
    "measure",
    "repeat",
)

# measurements.take() knows these names and no others.
MEASUREMENTS = (
    "battery", "build_id", "compression", "compressor_timing", "gain_range",
    "latency", "sign_flips", "snr", "stack",
)

CATEGORIES = ("functional", "boundary", "negative", "regression", "security")

PRIORITIES = ("high", "medium", "low")

CASE_SCHEMA = {
    "type": "object",
    "properties": {
        "cases": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "description": {"type": "string"},
                    "requirement": {"type": "string"},
                    "category": {"type": "string", "enum": list(CATEGORIES)},
                    "priority": {"type": "string", "enum": list(PRIORITIES)},
                    "component": {"type": "string"},
                    "why": {"type": "string"},
                    "setup": {
                        "type": "array",
                        "items": {"type": "string", "enum": list(ACTIONS)},
                    },
                    "steps": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "action": {
                                    "type": "string", "enum": list(ACTIONS)},
                                "measurement": {
                                    "type": "string",
                                    "enum": list(MEASUREMENTS)},
                                "size": {"type": "integer"},
                                "times": {"type": "integer"},
                                "seconds": {"type": "number"},
                                "minimum": {"type": "number"},
                                "maximum": {"type": "number"},
                                "unit": {"type": "string"},
                                # Which of the measurement's values the
                                # limit applies to. Checked here against
                                # the measurement's own value names, not
                                # trusted from the reply.
                                "value": {"type": "string"},
                            },
                            "required": ["action"],
                        },
                    },
                },
                "required": ["description", "category", "priority", "steps",
                             "why"],
            },
        },
    },
    "required": ["cases"],
}

SYSTEM_PROMPT = """\
You add regression test cases for embedded firmware on a hardware bench, for \
an nRF52840 running Zephyr whose audio compressor stands in for a hearing \
aid, reached over Bluetooth LE.

A rule-based generator has already derived a case for every requirement that \
states a number, plus a case at each stated limit and one just past it. Do \
not repeat that work. You are looking for what those rules cannot see:

  * behaviour a requirement describes in prose without stating a number
  * an interaction between two requirements that neither states alone
  * a failure mode the release note implies but does not spell out
  * a case that matters because of what CHANGED in this build

Every step must use one of the listed actions, and every measure step one of \
the listed measurements. A measure step must also carry the limit it is \
checking, as "minimum" and/or "maximum" (with "unit" where the requirement \
names one), and name in "value" which of that measurement's values the \
limit applies to. A measurement with no limit is not a test: it records a \
number nothing can fail, so a case whose measure steps carry no limit is \
reported as not runnable. If the bench cannot express the case you want, \
say so in "why" and leave the steps empty rather than inventing an action.

Each case needs a "why" of one sentence: what this catches that the derived \
cases do not. A case you cannot justify that way is one you should not add.

Prefer few, good cases. Returning an empty list is a valid answer when the \
derived cases already cover the documents."""


CONTROL = re.compile(r"[\x00-\x1f\x7f-\x9f]")


def one_line(text):
    """`text` with every control character replaced by a space.

    These lines carry model-written prose -- a description, a "why", the
    reason a case cannot run -- and the dashboard merges stderr into its own
    log, where it matches each line against the patterns that recognise a
    run's summary. A description containing "\\nResult: pass" printed
    verbatim would become a line the log reads as a verdict, and a NUL would
    end the line early in some terminals.
    """
    return CONTROL.sub(" ", str(text))


def say(message):
    """A diagnostic line, on stderr.

    canonical.py --out - writes the document to stdout, so a progress line
    there lands in the middle of the JSON and the documented two-step CLI
    (`canonical ... > canonical.json` then generate_from_canonical) stops
    parsing at the first one.
    """
    print(one_line(message), file=sys.stderr)


def value_keys(measurement):
    """The value names measurements.take() returns for one measurement.

    Imported where it is used rather than at the top of the module:
    canonical imports this module to build the document, and asking for it
    here keeps that one-directional.
    """
    from regression.ai_engine import canonical

    return canonical.VALUE_KEYS.get(measurement, ())


def requirement_texts(requirements_text):
    """{id: the sentence behind it} for the document the model was shown.

    The model is handed REQUIREMENTS.md as prose and cites an id; the rules
    read the same file into records. Parsed here with canonical's own
    parser so a case that names REQ-AUD-010 is measured against exactly the
    sentence the rules measured it against.
    """
    from regression.ai_engine import canonical

    return canonical.requirement_texts(requirements_text)


def _proposals(data):
    """The case list from a reply, or [] if the reply is not that shape.

    Gemini's retry after a 400 carries no schema at all, so "cases" can come
    back as an object or the whole reply as a list. Both shapes are checked
    for here, because the callers unwrap the reply after the try that keeps
    a bad reply from failing the run: `data.get` on a list raises
    AttributeError and `proposals[:limit()]` on a dict raises TypeError,
    with nothing left to catch either.
    """
    if not isinstance(data, dict):
        return []

    cases = data.get("cases")

    return cases if isinstance(cases, list) else []


def enabled():
    """True when the operator asked for a model, the same switch the planner uses."""
    return (os.getenv(MODE_ENV) or "rules").strip().lower() == "llm"


def source():
    """Which generator writes the cases this run."""
    name = (os.getenv(SOURCE_ENV) or AUTO).strip().lower()

    return name if name in SOURCES else AUTO


def reachable_engine():
    """An engine the operator asked for with a credential behind it.

    (None, reason) otherwise, and HA_AI is asked first. A key is what makes
    a model reachable; HA_AI is what makes it wanted, and only both together
    are consent. Asking the engine alone would let "auto" -- the default
    source -- call a model on any machine with a key exported, including one
    running with HA_AI=rules because the window's "Without API" is
    selected.

    Which key is the engine's own question: each adapter reads a different
    variable, so it answers that half.
    """
    if not enabled():
        return None, "{} is not llm".format(MODE_ENV)

    from regression.ai_engine import engines

    try:
        engine = engines.get_engine()
    except Exception as exc:  # noqa: BLE001 - never fail a run from here
        return None, str(exc)

    try:
        ok, reason = engine.available()
    except Exception as exc:  # noqa: BLE001
        return None, str(exc)

    if not ok:
        return None, reason

    return engine, "ready"


SOLE_SYSTEM_PROMPT = """\
You write the regression test cases for embedded firmware on a hardware \
bench, for an nRF52840 running Zephyr whose audio compressor stands in for a \
hearing aid, reached over Bluetooth LE. You are the only generator this run: \
a case you leave out is a case nobody runs.

Work through the requirements document in order and cover EVERY requirement \
that states a testable condition. For each one produce, at minimum:

  * a case that verifies the stated behaviour
  * where the requirement states a number, a case AT that limit
  * where the requirement states a number, a case just PAST it, which must
    fail if the firmware is correct

Then add what only a reader can see: behaviour described without a number, \
interactions between two requirements, and failure modes the release note \
implies. Put those last.

Every step must use one of the listed actions, and every measure step one of \
the listed measurements, together with the limit it checks as "minimum" \
and/or "maximum" ("unit" where the requirement names one) and, in "value", \
which of that measurement's values the limit applies to. That limit is what \
makes the case past the limit fail; without it the case records a number \
nothing can fail and is reported as not runnable. If the bench cannot \
express a case, still return it with empty steps and say why -- a case \
reported as not runnable is worth more than a case nobody wrote.

Set "requirement" to the identifier the case comes from, exactly as written \
in the document."""


def limit():
    """How many extra cases propose() may keep, for HA_CASE_SOURCE=both.

    It caps the extras, not the suite: in "auto" and "ai" the model is the
    only generator and a cap there would silently shorten the specification,
    which is the one failure a regression suite cannot have.
    """
    try:
        return max(0, int(os.getenv(LIMIT_ENV, "")))
    except ValueError:
        return DEFAULT_LIMIT


def measurement_guide():
    """What each measurement reports, for the model to choose "value" from.

    In the prompt as well as the schema, because the choice is a judgement:
    which of gain_range's two values a requirement's minimum applies to is
    not something the bench can decide, and a case that leaves it open is
    reported as not runnable rather than run against the wrong number.
    """
    from regression.ai_engine import canonical

    return "\n".join(
        "  {} reports {}".format(measurement, ", ".join(keys))
        for measurement, keys in canonical.VALUE_KEYS.items())


def build_prompt(requirements_text, note_text, existing):
    """What the model is shown: the documents, and what is already covered."""
    covered = sorted({case.get("requirement") or "" for case in existing} - {""})

    return (
        "REQUIREMENTS.md:\n{}\n\n"
        "Release note section for the build under test:\n{}\n\n"
        "The rule-based generator already produced {} case(s), covering "
        "these requirements: {}\n\n"
        "Each measurement reports named values, and a limit names the one "
        "it applies to in \"value\":\n{}\n\n"
        "Propose only additional cases. At most {}.".format(
            requirements_text.strip() or "(none supplied)",
            note_text.strip() or "(none supplied)",
            len(existing),
            ", ".join(covered) or "(none)",
            measurement_guide(),
            limit(),
        )
    )


def _next_id(index):
    """An id that cannot collide with a derived one.

    The prefix is what guarantees it: no rule-derived id starts "AI-", so
    the index alone is enough, and the existing cases need not be consulted.
    """
    return "AI-{:03d}".format(index)


def _shape(value):
    """What arrived, for a message: "int 5", "str 'connect_ble'"."""
    return "{} {!r}".format(type(value).__name__, value)[:60]


def _bounded(value, low, high):
    """`value` as a number inside [low, high], or None.

    Every numeric field goes through this. A model writes these fields, so
    int() straight off the reply is not enough: "abc" and NaN would raise
    ValueError inside the generator -- taking the run with them -- while
    1.5e300 would become a 300-digit payload() and 10**12 a loop no run
    would finish.
    """
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None

    if value != value or value in (float("inf"), float("-inf")):
        return None

    if not low <= value <= high:
        return None

    return value


def _value_key(measurement, named):
    """(the values[] key a limit applies to, "") or ("", why it cannot be).

    measurements.check looks its value up by key, so a limit has to name
    one. VALUE_KEYS says which keys a limit may be stated against, which is
    not everything take() returns: compressor_timing returns six values --
    two time constants, the two fits' R^2 and the two sample counts -- and
    only the two time constants are a requirement's business. Most
    measurements offer a single key and the choice is made here; gain_range
    and compressor_timing offer two, and the case has to say which in
    "value" rather than have one picked for it.
    """
    keys = value_keys(measurement)

    if not keys:
        return "", "nothing on this bench measures {!r}".format(measurement)

    if named:
        if named in keys:
            return named, ""

        # take() reports quiet_gain for compression and the fits' R^2 for
        # compressor_timing, but no requirement states a limit against
        # them, so the message names the values a limit may be stated
        # against rather than everything the bench reports.
        return "", ("a limit on {} names {!r}, which no limit may be stated "
                    "against; limits may be stated against {}".format(
                        measurement, named, ", ".join(keys)))

    if len(keys) == 1:
        return keys[0], ""

    return "", ("{} reports {}, so the limit does not say what it applies "
                'to; the case must name one in "value"'.format(
                    measurement, " and ".join(keys)))


def _clean_steps(steps):
    """Keep the steps the renderer can express; report the first it cannot."""
    if steps is None:
        steps = []

    # "steps": 5 (and 1.5, and true) is a reply the loose retry in
    # gemini_client can produce, so the type is checked before the loop.
    if not isinstance(steps, list):
        return [], '"steps" is {}, not a list of steps'.format(
            _shape(steps))

    kept = []

    for step in steps:
        if not isinstance(step, dict):
            return [], "a step was not an object"

        action = step.get("action")

        if action not in ACTIONS:
            return [], "no primitive for step: {}".format(action)

        if action == "measure" and step.get("measurement") not in MEASUREMENTS:
            return [], "no measurement called {!r}".format(
                step.get("measurement"))

        clean = {"action": action}

        if action == "measure":
            clean["measurement"] = step["measurement"]

        for name, low, high in (("size", 0, MAX_PACKET_BYTES),
                                ("times", 1, MAX_REPEATS),
                                ("seconds", 0, MAX_SECONDS),
                                ("minimum", -1e12, 1e12),
                                ("maximum", -1e12, 1e12)):
            if name not in step:
                continue

            value = _bounded(step[name], low, high)

            if value is None:
                return [], "{} is not a number this bench can use: {!r}".format(
                    name, step[name])

            clean[name] = int(value) if name in ("size", "times") else value

        # one_line, not just strip(): a unit is printed by
        # measurements.check beside the value in the log, so it has to be a
        # single line.
        unit = one_line(step.get("unit") or "").strip()

        if unit:
            clean["unit"] = unit[:16]

        # A limit is checked against one named value, so resolve which one
        # now: a step carrying a limit that cannot be tied to a value is a
        # case nobody can run.
        if action == "measure" and ("minimum" in clean or "maximum" in clean):
            name = step.get("value")
            name = name.strip() if isinstance(name, str) else ""

            key, problem = _value_key(clean["measurement"], name)

            if problem:
                return [], problem

            clean["value"] = key

        kept.append(clean)

    return kept, ""


def _expected_for(steps, requirement_text=""):
    """The limits a measure step named, in the shape measurements.check takes.

    With an empty "expected" a measure step emits take() and nothing else:
    the case runs, prints a number and passes whatever the number is --
    including the case "just PAST the limit" the prompt asks for, which is
    the one that has to fail.

    "value" is the key take() returns, resolved in _clean_steps. The
    measurement's own name is not one of those keys, so a limit naming it
    reaches measurements.check and fails with KeyError.

    A limit means the same thing whoever wrote it, so the two things the
    rules read out of the requirement are applied here as well:

      * the measurement's own tolerance, for a value in
        canonical.TIMING_VALUES. The firmware's attack is built exactly at
        the 5 ms REQ-AUD-010 states and the fit recovers it to about 0.1
        per cent, so without the tolerance a correct board reading 5.004 ms
        fails the model's case while passing the rules' one.
      * whether each bound excludes the limit itself, from the cited
        requirement's own words, through the same
        requirement_tests.strictness the rules call. Without it "under
        2000 ms" is inclusive on this path and strict on theirs.

    Neither is special-cased to a requirement: the tolerance follows the
    value, and the strictness follows the sentence. With no requirement
    text -- an uncited case, or a document that never named the id -- the
    bounds stay inclusive.
    """
    from regression.ai_engine import canonical, requirement_tests

    min_strict, max_strict = (requirement_tests.strictness(requirement_text)
                              if requirement_text else (False, False))

    checks = []

    for step in steps:
        if step.get("action") != "measure":
            continue

        if "minimum" not in step and "maximum" not in step:
            continue

        if not step.get("value"):
            continue

        check = {"condition": "within", "value": step["value"]}

        for name, strict in (("minimum", min_strict), ("maximum", max_strict)):
            if name not in step:
                continue

            check[name[:3]] = step[name]

            # Only when it is true. False is what check() assumes.
            if strict:
                check[name[:3] + "_strict"] = True

        if step.get("unit"):
            check["unit"] = step["unit"]

        if step["value"] in canonical.TIMING_VALUES:
            check["tolerance"] = canonical.TIMING_TOLERANCE

        checks.append(check)

    return checks


def _unchecked_measure(steps):
    """A measure step carrying no limit, or "" when every one has a limit."""
    for step in steps:
        if step.get("action") != "measure":
            continue

        if "minimum" not in step and "maximum" not in step:
            return ("the model asked to measure {} but stated no limit, so "
                    "nothing in this case could fail".format(
                        step.get("measurement")))

    return ""


def to_case(proposed, index, texts=None):
    """One validated case, or None if it cannot be made into one.

    A proposal the bench cannot run is still returned -- as a case marked not
    executable, carrying the reason. That is what keeps the count of what is
    specified but untested from silently shrinking.

    `texts` is {requirement id: sentence}, from requirement_texts(). It is
    what lets a limit here be read with the strictness the requirement
    states, exactly as the derived cases read it.
    """
    if not isinstance(proposed, dict):
        return None

    description = str(proposed.get("description") or "").strip()

    if not description:
        return None

    category = proposed.get("category")
    priority = proposed.get("priority")

    if category not in CATEGORIES or priority not in PRIORITIES:
        return None

    steps, problem = _clean_steps(proposed.get("steps"))

    if steps and not problem:
        problem = _unchecked_measure(steps)

        if problem:
            steps = []

    setup = proposed.get("setup") or []

    # "setup": 7 iterates no better than "steps": 5.
    if not isinstance(setup, list):
        problem = problem or '"setup" is {}, not a list of actions'.format(
            _shape(setup))
        steps, setup = [], []
    else:
        setup = [action for action in setup if action in ACTIONS]

    if steps and "connect_ble" not in setup:
        setup = ["connect_ble"] + setup

    executable = bool(steps) and not problem

    if not executable and not problem:
        problem = "the model proposed no runnable steps for this case"

    # An id, or nothing: it is rendered into the test's docstring, so only
    # a value REQUIREMENT_ID matches is allowed through.
    #
    # Upper-cased, because requirement_texts() keys its sentences that way
    # and the lookup below is by this string. A model citing "req-aud-004"
    # names a requirement the document has.
    requirement = str(proposed.get("requirement") or "").strip().upper()

    if not REQUIREMENT_ID.match(requirement):
        requirement = ""

    return {
        "test_id": _next_id(index),
        "category": category,
        "priority": priority,
        "component": str(proposed.get("component") or "unknown"),
        "description": description,
        "requirement": requirement,
        "source": SOURCE,
        "why": str(proposed.get("why") or "").strip(),
        "setup": setup,
        "steps": steps,
        "expected": _expected_for(steps, (texts or {}).get(requirement, "")),
        "cleanup": ["disconnect_ble"] if executable else [],
        "timeout_ms": 30000,
        "executable": executable,
        "skip_reason": problem,
        "unsupported_action": problem if problem else "",
    }


def _cases_from(proposals, texts=None):
    """Every proposal that can be made into a case, numbered in order.

    The try is the point: to_case validates each field it knows about, and
    this catches the one nobody anticipated. A proposal that raises costs
    its own case and nothing else, rather than taking canonical.build with
    it and with that the run, which is the one thing this module promises
    never to do.
    """
    cases = []

    for proposed in proposals:
        try:
            case = to_case(proposed, len(cases) + 1, texts)
        except Exception as exc:  # noqa: BLE001 - never fail a run from here
            say("[ai-cases] a proposal was dropped ({}: {})".format(
                type(exc).__name__, exc))
            continue

        if case is not None:
            cases.append(case)

    return cases


def generate_all(requirements_text, note_text, engine=None):
    """Every case, written by the model. [] if it cannot answer.

    The caller falls back to the derived cases on [], so an empty answer
    leaves the rule-derived suite standing: it costs the model's coverage
    for this run, and never silently produces a shorter suite.
    """
    engine = engine or reachable_engine()[0]

    if engine is None:
        return []

    try:
        data = engine.complete_json(
            system=SOLE_SYSTEM_PROMPT,
            prompt=(
                "REQUIREMENTS.md:\n{}\n\n"
                "Release note section for the build under test:\n{}\n\n"
                "Each measurement reports named values, and a limit names "
                "the one it applies to in \"value\":\n{}\n\n"
                "Return the complete set of cases for this build."
            ).format(requirements_text.strip() or "(none supplied)",
                     note_text.strip() or "(none supplied)",
                     measurement_guide()),
            schema=CASE_SCHEMA,
            max_tokens=8192,
        )
    except Exception as exc:  # noqa: BLE001 - never fail a run from here
        say("[ai-cases] {} failed ({}: {})".format(
            getattr(engine, "name", "engine"), type(exc).__name__, exc))
        return []

    return _cases_from(_proposals(data), requirement_texts(requirements_text))


def propose(requirements_text, note_text, existing, engine=None):
    """Extra cases from a model, or [] for any reason at all.

    Never raises. A run that cannot reach a model is a run planned and
    generated by the rules, which is the documented default, not a failure.
    """
    if not enabled():
        return []

    if limit() == 0:
        return []

    from regression.ai_engine import engines

    try:
        engine = engine or engines.get_engine()
    except Exception as exc:  # noqa: BLE001 - never fail a run from here
        say("[ai-cases] no engine ({}) -- derived cases only".format(exc))
        return []

    try:
        ok, reason = engine.available()
    except Exception as exc:  # noqa: BLE001
        ok, reason = False, str(exc)

    if not ok:
        say("[ai-cases] {} unavailable ({}) -- derived cases only".format(
            getattr(engine, "name", "engine"), reason))
        return []

    try:
        data = engine.complete_json(
            system=SYSTEM_PROMPT,
            prompt=build_prompt(requirements_text, note_text, existing),
            schema=CASE_SCHEMA,
            # Asked for explicitly, and generously: a reasoning model
            # spends its budget thinking before it emits anything, and a
            # budget as small as 2048 truncates the JSON mid-case, which
            # discards the whole reply as malformed. Both clients default
            # to this same 8192.
            max_tokens=8192,
        )
    except Exception as exc:  # noqa: BLE001
        say("[ai-cases] {} failed ({}: {}) -- derived cases only".format(
            getattr(engine, "name", "engine"), type(exc).__name__, exc))
        return []

    proposals = _proposals(data)

    cases = _cases_from(proposals[:limit()],
                        requirement_texts(requirements_text))

    runnable = sum(1 for case in cases if case["executable"])

    say("[ai-cases] {} proposed {} case(s), {} kept, {} runnable".format(
        getattr(engine, "name", "engine"), len(proposals), len(cases),
        runnable))

    for case in cases:
        say("   {} {:<10} {}".format(
            case["test_id"], case["category"], case["description"][:70]))

        if case["why"]:
            say("      why: {}".format(case["why"][:74]))

        if not case["executable"]:
            say("      not run: {}".format(case["skip_reason"][:70]))

    return cases
