# Copyright (c) 2026 Prajnaana Technologies Pvt. Ltd.
# Original author: Dhanya Shree S
# SPDX-License-Identifier: MIT
#
# Licensed under the MIT License. See LICENSE in the project root.

"""Tests for the canonical document.

The rule these enforce: everything a runner needs is in the file. A runner
that has to re-read a release note, or guess what a sentence meant, is a
runner nobody can debug.
"""

import pytest

from regression.ai_engine import canonical, test_spec

NOTE = """Version 2.4.0

1. Added battery over-temperature protection.
2. Improved BLE reconnection handling.
3. Fixed memory leak during repeated BLE connections.
4. Increased maximum BLE packet size from 64 to 128 bytes.
"""

REQUIREMENTS = """Test Requirements

REQ-BLE-001: The device shall accept an audio packet up to the configured maximum size.
REQ-BLE-002: The device shall re-establish a connection after an unexpected disconnect.
REQ-BAT-001: The device shall enter protection when battery temperature exceeds the limit.

Not a requirement, just a heading.
"""


def document(tmp_path, note=NOTE, requirements=REQUIREMENTS):
    path = tmp_path / "REQUIREMENTS.md"
    path.write_text(requirements, encoding="utf-8")

    return canonical.build(note, str(path), config_path=str(tmp_path / "none"))


# --------------------------------------------------------------------------
# Requirements
# --------------------------------------------------------------------------

def test_requirements_are_read_and_prose_is_not(tmp_path):
    """A heading turned into a requirement is a test with nothing behind it."""
    doc = document(tmp_path)

    assert [r["id"] for r in doc["requirements"]] == [
        "REQ-BLE-001", "REQ-BLE-002", "REQ-BAT-001"]


def test_the_identifier_decides_the_component_not_the_prose(tmp_path):
    """REQ-BLE-001 mentions "audio packet"; the id says BLE and wins."""
    doc = document(tmp_path)
    first = doc["requirements"][0]

    assert first["id"] == "REQ-BLE-001"
    assert first["component"] == "BLE"


def test_a_missing_requirements_file_is_empty_not_fatal():
    doc = canonical.build(NOTE, "no/such/file.md")

    assert doc["requirements"] == []
    assert doc["tests"]


# --------------------------------------------------------------------------
# The executable shape
# --------------------------------------------------------------------------

def test_every_test_carries_what_a_runner_needs(tmp_path):
    for case in document(tmp_path)["tests"]:
        for field in ("test_id", "category", "priority", "setup", "steps",
                      "expected", "timeout_ms", "cleanup", "executable"):
            assert field in case, field


def test_a_size_in_the_case_becomes_a_step_argument(tmp_path):
    doc = document(tmp_path)

    sizes = [step["size"]
             for case in doc["tests"]
             for step in case["steps"]
             if step["action"] == "send_ble_packet"]

    assert sorted(sizes) == [64, 128, 129]


def test_a_negative_case_expects_rejection_and_a_surviving_link(tmp_path):
    doc = document(tmp_path)
    negative = [c for c in doc["tests"] if c["category"] == "negative"][0]

    conditions = [e["condition"] for e in negative["expected"]]

    assert "rejected" in conditions
    assert "link_survives" in conditions


def test_a_stress_case_gets_a_longer_timeout_than_a_connect(tmp_path):
    """A hundred reconnects cannot share a timeout with one connect."""
    doc = document(tmp_path)
    by_category = {c["category"]: c for c in doc["tests"]}

    assert (by_category["stress"]["timeout_ms"]
            > by_category["functional"]["timeout_ms"])


def test_a_safety_case_is_critical_priority(tmp_path):
    doc = document(tmp_path)
    safety = [c for c in doc["tests"] if c["category"] == "safety"][0]

    assert safety["priority"] == "critical"


# --------------------------------------------------------------------------
# Honest about what cannot run
# --------------------------------------------------------------------------

def test_a_case_with_no_primitive_is_kept_and_says_why(tmp_path):
    """When the hardware arrives the tests are already written.

    Battery temperature is the case: CONFIG_BT_BAS makes the level
    readable, and the temperature beside it stays unreadable, because
    nothing on the board measures it.
    """
    doc = document(tmp_path)
    temperature = [c for c in doc["tests"]
                   if any(s["action"] == "read_temperature" for s in c["steps"])]

    assert temperature
    for case in temperature:
        assert case["executable"] is False
        assert "temperature" in case["skip_reason"]


def test_battery_level_cases_run_now_that_the_service_exists(tmp_path):
    doc = document(tmp_path)
    level = [c for c in doc["tests"]
             if any(s["action"] == "read_battery" for s in c["steps"])
             and c["category"] == "functional"
             and c["executable"]]

    assert level


def test_runnable_cases_carry_no_skip_reason(tmp_path):
    for case in document(tmp_path)["tests"]:
        if case["executable"]:
            assert case["skip_reason"] == ""


def test_the_summary_counts_add_up(tmp_path):
    doc = document(tmp_path)
    summary = doc["summary"]

    assert summary["tests"] == len(doc["tests"])
    assert summary["runnable"] + summary["not_runnable"] == summary["tests"]
    assert summary["requirements"] == len(doc["requirements"])


def test_the_document_names_its_schema(tmp_path):
    assert document(tmp_path)["schema"] == canonical.SCHEMA


def test_every_step_names_a_known_action(tmp_path):
    """A step nothing implements must be caught here, not on the bench."""
    known = set(canonical.ACTIONS) | set(canonical.NO_PRIMITIVE) | {"repeat"}

    for case in document(tmp_path)["tests"]:
        for step in case["steps"]:
            assert step["action"] in known, step["action"]


def test_the_two_generators_never_mint_the_same_id(tmp_path):
    """The two generators share one counter, so no document can carry two
    different BAT-001s.

    Checked over both requirement sets the file uses, so the ids are unique
    in each of the documents this file builds.
    """
    from collections import Counter

    for requirements in (REQUIREMENTS, MEASURED_REQUIREMENTS):
        doc = document(tmp_path, requirements=requirements)

        ids = [case["test_id"] for case in doc["tests"]]
        duplicates = {k: v for k, v in Counter(ids).items() if v > 1}

        assert duplicates == {}, duplicates


def test_cases_say_which_generator_produced_them(tmp_path):
    sources = {c["source"] for c in document(tmp_path)["tests"]}

    assert sources == {"release_note", "requirement"}


MEASURED_REQUIREMENTS = """Test Requirements

REQ-AUD-003: Processing shall yield a signal-to-noise ratio above 0 dB and below 50 dB.
REQ-AUD-010: The compressor shall reach its attack within 5 ms and release within 60 ms.
REQ-MEM-002: Heap growth across a single run shall not exceed 8 KB.
"""


def test_measurable_requirement_measures_with_its_own_limits(tmp_path):
    doc = document(tmp_path, note="", requirements=MEASURED_REQUIREMENTS)
    snr = next(t for t in doc["tests"]
               if t["requirement"] == "REQ-AUD-003" and t["category"] == "functional")

    assert snr["executable"]
    assert snr["steps"] == [{"action": "measure", "measurement": "snr"}]
    assert snr["expected"][0]["min"] == 0 and snr["expected"][0]["min_strict"]
    assert snr["expected"][0]["max"] == 50 and snr["expected"][0]["max_strict"]


def test_timing_reads_both_limits(tmp_path):
    doc = document(tmp_path, note="", requirements=MEASURED_REQUIREMENTS)
    timing = next(t for t in doc["tests"]
                  if t["requirement"] == "REQ-AUD-010" and t["category"] == "functional")

    assert [(c["value"], c["max"]) for c in timing["expected"]] == [
        ("attack_ms", 5.0), ("release_ms", 60.0)]


def test_boundaries_of_a_measured_requirement_stay_skipped(tmp_path):
    # Measuring a value is not the same as forcing the device to a value.
    doc = document(tmp_path, note="", requirements=MEASURED_REQUIREMENTS)
    edges = [t for t in doc["tests"] if t["requirement"] == "REQ-AUD-003"
             and t["category"] in ("boundary", "negative")]

    assert edges and not any(t["executable"] for t in edges)
    assert all("cannot make the device produce" in t["skip_reason"] for t in edges)


def test_heap_requirement_says_why_it_is_skipped(tmp_path):
    doc = document(tmp_path, note="", requirements=MEASURED_REQUIREMENTS)
    heap = [t for t in doc["tests"] if t["requirement"] == "REQ-MEM-002"]

    assert heap and all("no heap" in t["skip_reason"] for t in heap)


def test_a_zero_limit_survives_emission(tmp_path):
    from regression.ai_engine import generate_from_canonical

    doc = document(tmp_path, note="", requirements=MEASURED_REQUIREMENTS)
    path = generate_from_canonical.generate(doc, str(tmp_path / "out.py"))

    assert "'snr_db', minimum=0, maximum=50" in open(path, encoding="utf-8").read()


# ---------------------------------------------------------------------------
# Model-proposed cases (HA_AI=llm)
# ---------------------------------------------------------------------------

class _StubEngine:
    """An engine that returns whatever it was handed."""

    name = "stub"
    key_env = ""

    def __init__(self, payload=None, error=None):
        self.payload = payload
        self.error = error
        self.asked = None

    def available(self):
        return True, "ready"

    def model_name(self):
        return "stub-1"

    def complete_json(self, system, prompt, schema, max_tokens=None):
        self.asked = {"system": system, "prompt": prompt, "schema": schema}

        if self.error:
            raise self.error

        return self.payload


def _case(**over):
    base = {
        "description": "Streaming while reading telemetry must not stall",
        "category": "functional",
        "priority": "high",
        "why": "no requirement states this interaction",
        "setup": ["connect_ble"],
        "steps": [{"action": "stream_audio", "seconds": 2},
                  {"action": "read_telemetry"}],
    }
    base.update(over)
    return base


def _refuse_every_engine(monkeypatch):
    """Make any attempt to obtain an engine a test failure.

    Patching engines.get_engine is not enough on its own: both entry points
    import the module inside the function, so the attribute has to be
    replaced on the module object every caller resolves.
    """
    from regression.ai_engine import engines

    def explode(*args, **kwargs):
        raise AssertionError("an engine was consulted on the rules path")

    monkeypatch.setattr(engines, "get_engine", explode)


@pytest.mark.parametrize("source", ["auto", "ai", "rules", "both", ""])
def test_no_model_is_called_on_the_default_path(monkeypatch, source):
    """HA_AI=rules must not reach an engine, whatever else is set.

    Including with a credential exported and HA_CASE_SOURCE at its default.
    generate_all and _apply_case_source are the path the default source
    takes, so they are the ones exercised here.
    """
    from regression.ai_engine import ai_cases

    monkeypatch.setenv("HA_AI", "rules")
    monkeypatch.setenv("GEMINI_API_KEY", "AIzaSy-not-a-real-key")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-not-a-real-key")
    monkeypatch.setenv("HA_ENGINE", "gemini")

    if source:
        monkeypatch.setenv("HA_CASE_SOURCE", source)
    else:
        monkeypatch.delenv("HA_CASE_SOURCE", raising=False)

    _refuse_every_engine(monkeypatch)

    assert ai_cases.reachable_engine() == (None, "HA_AI is not llm")
    assert ai_cases.propose("requirements", "note", []) == []
    assert ai_cases.generate_all("requirements", "note") == []

    derived = [{"test_id": "REQ-001", "executable": True}]

    # "both" returns a new list (derived + extras), the others return the
    # list itself, so compare by value and let every mode use one assertion.
    assert canonical._apply_case_source(
        derived, REQUIREMENTS, NOTE, ai_cases) == derived


def test_the_document_is_rule_derived_when_a_key_is_present(
        monkeypatch, tmp_path):
    """The whole of build(), with credentials, on the default settings."""
    from regression.ai_engine import ai_cases

    monkeypatch.delenv("HA_AI", raising=False)
    monkeypatch.delenv("HA_CASE_SOURCE", raising=False)
    monkeypatch.setenv("GEMINI_API_KEY", "AIzaSy-not-a-real-key")

    _refuse_every_engine(monkeypatch)

    doc = document(tmp_path)

    assert doc["summary"]["tests"] > 0
    assert all(case["source"] != ai_cases.SOURCE
               for case in doc["tests"]), "a model wrote a case"


def test_a_model_is_reachable_once_the_operator_asks(monkeypatch):
    """The other half: HA_AI=llm and a key does reach the engine."""
    from regression.ai_engine import ai_cases, engines

    monkeypatch.setenv("HA_AI", "llm")
    monkeypatch.setenv("HA_ENGINE", "gemini")
    monkeypatch.setenv("GEMINI_API_KEY", "AIzaSy-not-a-real-key")

    engine, reason = ai_cases.reachable_engine()

    assert isinstance(engine, engines.GeminiEngine)
    assert reason == "ready"


def test_model_cases_join_the_derived_ones(monkeypatch):
    from regression.ai_engine import ai_cases

    monkeypatch.setenv("HA_AI", "llm")
    engine = _StubEngine({"cases": [_case()]})

    cases = ai_cases.propose("REQ-X: something", "note", [], engine=engine)

    assert len(cases) == 1

    case = cases[0]

    assert case["source"] == "llm"
    assert case["executable"] is True
    assert case["test_id"].startswith("AI-")
    assert case["cleanup"] == ["disconnect_ble"]

    # The documents are what it is shown, and what is already covered.
    assert "REQ-X: something" in engine.asked["prompt"]


def test_an_invented_action_becomes_a_skip_not_a_step(monkeypatch):
    """The vocabulary is enforced after the reply, not only in the schema."""
    from regression.ai_engine import ai_cases

    monkeypatch.setenv("HA_AI", "llm")
    engine = _StubEngine({"cases": [
        _case(steps=[{"action": "reflash_the_board"}])]})

    cases = ai_cases.propose("reqs", "note", [], engine=engine)

    assert len(cases) == 1, "the case was dropped instead of marked"
    assert cases[0]["executable"] is False
    assert "reflash_the_board" in cases[0]["skip_reason"]
    assert cases[0]["steps"] == []


def test_an_unknown_measurement_is_refused(monkeypatch):
    from regression.ai_engine import ai_cases

    monkeypatch.setenv("HA_AI", "llm")
    engine = _StubEngine({"cases": [
        _case(steps=[{"action": "measure", "measurement": "telepathy"}])]})

    cases = ai_cases.propose("reqs", "note", [], engine=engine)

    assert cases[0]["executable"] is False
    assert "telepathy" in cases[0]["skip_reason"]


def test_a_malformed_proposal_is_dropped(monkeypatch):
    from regression.ai_engine import ai_cases

    monkeypatch.setenv("HA_AI", "llm")
    engine = _StubEngine({"cases": [
        _case(category="apocalyptic"),
        _case(description=""),
        "not even an object",
        _case(),
    ]})

    cases = ai_cases.propose("reqs", "note", [], engine=engine)

    assert len(cases) == 1, "only the well-formed case should survive"


def test_a_failing_model_never_fails_the_run(monkeypatch, capsys):
    from regression.ai_engine import ai_cases

    monkeypatch.setenv("HA_AI", "llm")

    for error in (RuntimeError("503 Service Unavailable"),
                  ValueError("not JSON")):
        engine = _StubEngine(error=error)

        assert ai_cases.propose("reqs", "note", [], engine=engine) == []

    # stderr, not stdout: the canonical CLI writes the document to stdout,
    # and a diagnostic there makes it unparseable JSON.
    captured = capsys.readouterr()

    assert "derived cases only" in captured.err
    assert captured.out == ""


def test_the_case_limit_is_honoured(monkeypatch):
    from regression.ai_engine import ai_cases

    monkeypatch.setenv("HA_AI", "llm")
    monkeypatch.setenv("HA_AI_CASE_LIMIT", "2")

    engine = _StubEngine({"cases": [_case() for _ in range(9)]})

    assert len(ai_cases.propose("reqs", "note", [], engine=engine)) == 2

    monkeypatch.setenv("HA_AI_CASE_LIMIT", "0")

    assert ai_cases.propose("reqs", "note", [], engine=engine) == []


def test_model_cases_reach_the_generated_module(monkeypatch, tmp_path):
    """End to end: a proposal becomes a pytest function, or a named skip."""
    from regression.ai_engine import ai_cases, generate_from_canonical

    monkeypatch.setenv("HA_AI", "llm")
    engine = _StubEngine({"cases": [
        _case(),
        _case(description="Cannot be driven here",
              steps=[{"action": "open_the_case"}]),
    ]})

    cases = ai_cases.propose("reqs", "note", [], engine=engine)

    document = {"schema": 1, "tests": cases, "requirements": [], "changes": [],
                "summary": {"tests": len(cases), "requirements": 0,
                            "changes": 0, "runnable": 1, "not_runnable": 1},
                "firmware": {}}

    # The module the pipeline would run, written the way the pipeline
    # writes it: generate() is the generator's own entry point, so the
    # document built above is what gets exercised, rather than the cases
    # rendered one at a time.
    path = generate_from_canonical.generate(
        document, str(tmp_path / "test_from_canonical.py"))

    code = open(path, encoding="utf-8").read()

    assert "def test_" in code
    assert "pytest.skip" in code
    assert "open_the_case" in code

    # It is a module, not a string that looks like one.
    compile(code, path, "exec")


# --------------------------------------------------------------------------
# Nothing a model writes may become a statement, or a number the bench
# cannot express
# --------------------------------------------------------------------------

def _rendered(case):
    """One case through the renderer, as a module that can be parsed."""
    from regression.ai_engine import generate_from_canonical

    return "import pytest\n\n" + generate_from_canonical.render(case)


def test_a_streaming_case_skips_on_a_board_with_no_loopback():
    """The requirement suite must not fail a board for a feature it lacks.

    The emitted stream_audio step probes for loopback first, exactly as
    require_loopback does in the planner-selected module.
    """
    from regression.ai_engine import generate_from_canonical

    case = {"test_id": "AUD-001",
            "description": "Stream audio through the DSP",
            "category": "functional", "priority": "high",
            "requirement": "REQ-DSP-004", "executable": True,
            "steps": [{"action": "connect_ble"}, {"action": "stream_audio"}],
            "expected": []}

    code = _rendered(case)

    assert "require_loopback(ble_device)" in code
    assert code.index("require_loopback(ble_device)") < code.index(
        "send_audio_stream"), "the probe must run before the audio is sent"

    # The helper the emitted call needs is in the module header, and skips.
    header = generate_from_canonical.HEADER.format(
        source="canonical.json", build_id="v1+abc", total=1, runnable=1,
        skipped=0)

    assert "def require_loopback(ble_device):" in header
    assert "pytest.skip(" in header.split(
        "def require_loopback(ble_device):")[1]

    # A board that says it has a loopback and then sends nothing is still a
    # failure: the assertion is kept, not replaced.
    assert "assert returned is not None" in code


def test_a_hostile_requirement_cannot_close_the_docstring():
    """A requirement value cannot close the docstring it is rendered into.

    The injected value is rejected as an identifier, and carried through by
    hand it renders as one string literal and nothing else.
    """
    import ast

    from regression.ai_engine import ai_cases

    injection = 'R"""' + chr(10) + "    import os" + chr(10) + \
        '    os.remove("PWNED")' + chr(10) + '    """'

    case = ai_cases.to_case({
        "description": "Stream while reading telemetry",
        "requirement": injection,
        "category": "functional",
        "priority": "high",
        "why": "interaction",
        "steps": [{"action": "read_telemetry"}],
    }, 1)

    # Rejected as an identifier long before it reaches the renderer.
    assert case["requirement"] == ""

    # And even carried through by hand, it renders as one string literal.
    tree = ast.parse(_rendered(dict(case, requirement=injection)))
    function = [n for n in tree.body if isinstance(n, ast.FunctionDef)][0]

    # The injected text is the docstring and nothing else: one Expr holding
    # a constant, and no statement from inside it anywhere in the function.
    assert isinstance(function.body[0], ast.Expr)
    assert isinstance(function.body[0].value, ast.Constant)
    assert not any(isinstance(n, (ast.Import, ast.ImportFrom))
                   for n in ast.walk(function))


def test_a_backslash_or_a_newline_does_not_break_the_module():
    """A Windows path in a description carries a \\N escape.

    The description is escaped before it reaches the emitted source, so the
    module still parses.
    """
    import ast

    from regression.ai_engine import ai_cases

    nasty = "C:" + chr(92) + "Names" + chr(92) + 'x, a quote " and' + chr(10)

    case = ai_cases.to_case({
        "description": nasty,
        "category": "functional",
        "priority": "high",
        "why": "paths appear in release notes",
        "steps": [{"action": "open_the_lid"}],
    }, 1)

    assert case["executable"] is False

    ast.parse(_rendered(case))


@pytest.mark.parametrize("step,reason", [
    ({"action": "send_ble_packet", "size": "abc"}, "not a number"),
    ({"action": "send_ble_packet", "size": 1.5e300}, "far past the limit"),
    ({"action": "send_ble_packet", "size": float("nan")}, "NaN"),
    ({"action": "repeat", "times": 10 ** 12}, "an endless loop"),
    ({"action": "repeat", "times": "1e9"}, "not a number"),
])
def test_a_number_the_bench_cannot_express_becomes_a_skip(step, reason):
    """A field int() cannot express, or expresses absurdly, is a skip."""
    from regression.ai_engine import ai_cases

    case = ai_cases.to_case({
        "description": "Send a packet",
        "category": "boundary",
        "priority": "high",
        "why": reason,
        "steps": [step],
    }, 1)

    assert case["executable"] is False, reason
    assert case["skip_reason"], "no reason was recorded for " + reason


@pytest.mark.parametrize("reply", [
    {"cases": {"one": "object, not a list"}},
    ["a list, not an object"],
    "a string",
    None,
    {"cases": None},
])
def test_a_reply_of_the_wrong_shape_never_fails_the_run(monkeypatch, reply):
    """Gemini's retry after a 400 carries no schema, so any shape can arrive.

    Reading one of these shapes raises AttributeError or TypeError, so the
    try that keeps a bad reply from failing the run has to cover the
    reading itself.
    """
    from regression.ai_engine import ai_cases

    monkeypatch.setenv("HA_AI", "llm")

    engine = _StubEngine(reply)

    assert ai_cases.propose("reqs", "note", [], engine=engine) == []
    assert ai_cases.generate_all("reqs", "note", engine=engine) == []


def test_a_measurement_with_no_limit_cannot_pass_by_default(monkeypatch):
    """A measure step with an empty "expected" emits take() and no check.

    Such a measurement passes whatever the board reports -- including the
    case "just PAST the limit" the prompt asks for, which is the one that
    has to fail.
    """
    from regression.ai_engine import ai_cases

    monkeypatch.setenv("HA_AI", "llm")

    without = ai_cases.to_case({
        "description": "Measure the stack",
        "category": "functional", "priority": "high", "why": "no limit",
        "steps": [{"action": "measure", "measurement": "stack"}],
    }, 1)

    assert without["executable"] is False
    assert "no limit" in without["skip_reason"]

    with_limit = ai_cases.to_case({
        "description": "Stack stays under the ceiling",
        "category": "boundary", "priority": "high", "why": "REQ-MEM-001",
        "steps": [{"action": "measure", "measurement": "stack",
                   "maximum": 3.76, "unit": "KiB"}],
    }, 1)

    assert with_limit["executable"] is True

    # "stack_kib", not "stack": measurements.take returns the value under
    # that key and check() looks it up by it, so a bare "stack" would pass
    # here and fail on the board.
    assert with_limit["expected"] == [
        {"condition": "within", "value": "stack_kib", "max": 3.76,
         "unit": "KiB"}]

    assert "measurements.check(" in _rendered(with_limit)


# --------------------------------------------------------------------------
# A limit reaches the board as a check of the value the board reports
# --------------------------------------------------------------------------

class _NoDevice:
    """Stands in for the fixture. Nothing rendered here drives it."""

    is_connected = True


def _module_for(case, tmp_path):
    """One case as the module the pipeline would write: (source, path)."""
    from regression.ai_engine import generate_from_canonical

    document = {"schema": "test", "tests": [case],
                "firmware": {"build_id": "test-build"}}

    path = generate_from_canonical.generate(
        document, str(tmp_path / "test_rendered.py"))

    with open(path, encoding="utf-8") as handle:
        return handle.read(), path


def _only_test(code, path):
    """The single test function the module defines."""
    namespace = {}

    exec(compile(code, path, "exec"), namespace)  # noqa: S102 - the subject

    functions = [value for name, value in sorted(namespace.items())
                 if name.startswith("test_")]

    assert len(functions) == 1, "expected one rendered test, got {}".format(
        len(functions))

    return functions[0]


def _run_rendered(case, measured, tmp_path, monkeypatch):
    """Render one case and run the test function it produced.

    The measurement is faked and nothing else is: measurements.check is the
    real one, so a limit naming a value take() does not report fails here
    exactly as it fails on the bench -- with KeyError.
    """
    from regression import measurements

    code, path = _module_for(case, tmp_path)

    monkeypatch.setattr(measurements, "take",
                        lambda device, name: dict(measured[name]))

    _only_test(code, path)(_NoDevice())


def _measure_case(steps, description="Measured"):
    from regression.ai_engine import ai_cases

    return ai_cases.to_case({
        "description": description,
        "category": "functional",
        "priority": "high",
        "why": "a limit has to be checked",
        "steps": steps,
    }, 1)


def test_a_model_written_limit_runs_against_the_value_the_board_reports(
        tmp_path, monkeypatch):
    """measurements.check does values[name], and take() returns "stack_kib".

    A model-written limit names the value the board reports, not the
    measurement that reports it.
    """
    case = _measure_case([{"action": "measure", "measurement": "stack",
                           "maximum": 3.76, "unit": "KiB"}])

    assert case["executable"] is True

    _run_rendered(case, {"stack": {"stack_kib": 2.51}}, tmp_path, monkeypatch)

    with pytest.raises(AssertionError) as raised:
        _run_rendered(case, {"stack": {"stack_kib": 4.0}},
                      tmp_path, monkeypatch)

    assert "above the requirement" in str(raised.value)


def test_each_measure_step_is_checked_against_its_own_limit(
        tmp_path, monkeypatch):
    """A limit belongs to one measure step, not to every step in the case."""
    case = _measure_case([
        {"action": "measure", "measurement": "snr", "minimum": 30,
         "unit": "dB"},
        {"action": "measure", "measurement": "stack", "maximum": 3.76,
         "unit": "KiB"},
    ], description="SNR and headroom in one case")

    code, _path = _module_for(case, tmp_path)

    assert code.count("measurements.check(") == 2

    before, after = code.split("measurements.take(ble_device, 'stack')")

    assert "snr_db" in before and "snr_db" not in after
    assert "stack_kib" in after and "stack_kib" not in before

    # And it runs: inside both limits it passes, outside the second it
    # fails on the second.
    _run_rendered(case, {"snr": {"snr_db": 42.0},
                         "stack": {"stack_kib": 2.51}}, tmp_path, monkeypatch)

    with pytest.raises(AssertionError):
        _run_rendered(case, {"snr": {"snr_db": 42.0},
                             "stack": {"stack_kib": 9.0}},
                      tmp_path, monkeypatch)


def test_a_measurement_reporting_two_values_must_say_which_one():
    """gain_range reports min_gain and max_gain; a limit applies to one.

    The case names which of the two values its limit applies to.
    """
    from regression.ai_engine import ai_cases

    ambiguous = _measure_case([{"action": "measure",
                                "measurement": "gain_range", "minimum": 0.5}])

    assert ambiguous["executable"] is False
    assert "min_gain" in ambiguous["skip_reason"]

    chosen = _measure_case([{"action": "measure",
                             "measurement": "gain_range", "minimum": 0.5,
                             "value": "min_gain"}])

    assert chosen["executable"] is True
    assert chosen["expected"] == [
        {"condition": "within", "value": "min_gain", "min": 0.5}]

    invented = _measure_case([{"action": "measure", "measurement": "stack",
                               "maximum": 3.76, "value": "stack_bytes"}])

    assert invented["executable"] is False
    assert "stack_bytes" in invented["skip_reason"]

    # The names are the document's own, not a second copy of them.
    assert ai_cases.value_keys("stack") == canonical.VALUE_KEYS["stack"]
    assert canonical.VALUE_KEYS["stack"] == ("stack_kib",)


def test_every_measurement_a_model_may_name_reports_a_known_value():
    """The two lists have to stay together.

    A measurement in ai_cases.MEASUREMENTS that canonical.MEASURED does not
    describe has no value for a limit to name.
    """
    from regression import measurements
    from regression.ai_engine import ai_cases

    for name in ai_cases.MEASUREMENTS:
        assert name in measurements.MEASUREMENTS, name
        assert ai_cases.value_keys(name), name

    assert set(canonical.VALUE_KEYS) == set(ai_cases.MEASUREMENTS)


# --------------------------------------------------------------------------
# A reply of the wrong type where a list belongs
# --------------------------------------------------------------------------

@pytest.mark.parametrize("steps", [5, 1.5, True, "measure", {"action": "x"}])
def test_steps_that_are_not_a_list_are_a_reason_not_a_crash(steps):
    """A steps value that is not a list is a reason, not a crash."""
    from regression.ai_engine import ai_cases

    case = ai_cases.to_case({
        "description": "Whatever the model meant by this",
        "category": "functional", "priority": "high", "why": "shape",
        "steps": steps,
    }, 1)

    assert case is not None, "the case was dropped instead of reported"
    assert case["executable"] is False
    assert "steps" in case["skip_reason"]


@pytest.mark.parametrize("setup", [7, "connect_ble", {"first": "connect_ble"}])
def test_setup_that_is_not_a_list_is_a_reason_not_a_crash(setup):
    """The same for setup, which the same loop reads."""
    from regression.ai_engine import ai_cases

    case = ai_cases.to_case({
        "description": "Reads the battery",
        "category": "functional", "priority": "high", "why": "shape",
        "setup": setup,
        "steps": [{"action": "read_battery"}],
    }, 1)

    assert case is not None
    assert case["executable"] is False
    assert "setup" in case["skip_reason"]
    assert case["setup"] == []


def test_a_malformed_reply_never_reaches_canonical_build(monkeypatch, tmp_path):
    """End to end: those shapes, through the document a run builds.

    to_case is called from generate_all and from propose, both outside any
    try, under HA_AI=llm with any source, including the default.
    """
    from regression.ai_engine import engines

    monkeypatch.setenv("HA_AI", "llm")
    monkeypatch.setenv("HA_CASE_SOURCE", "auto")

    engine = _StubEngine({"cases": [
        {"description": "steps is a number", "category": "functional",
         "priority": "high", "why": "shape", "steps": 5},
        {"description": "setup is a number", "category": "functional",
         "priority": "high", "why": "shape", "setup": 7,
         "steps": [{"action": "read_battery"}]},
        _case(),
    ]})

    monkeypatch.setattr(engines, "get_engine", lambda: engine)

    doc = document(tmp_path)

    assert doc["summary"]["tests"] == 3
    assert [case["executable"] for case in doc["tests"]] == [False, False, True]


def test_a_proposal_that_raises_costs_its_own_case_only(monkeypatch, capsys):
    """The try around to_case, for the field nobody anticipated."""
    from regression.ai_engine import ai_cases

    monkeypatch.setenv("HA_AI", "llm")

    real = ai_cases.to_case

    def explode(proposed, index, texts=None):
        if str(proposed.get("description", "")).startswith("Bad"):
            raise TypeError("a shape nobody thought of")

        return real(proposed, index, texts)

    monkeypatch.setattr(ai_cases, "to_case", explode)

    engine = _StubEngine({"cases": [_case(description="Bad one"), _case()]})

    assert len(ai_cases.propose("reqs", "note", [], engine=engine)) == 1
    assert len(ai_cases.generate_all("reqs", "note", engine=engine)) == 1
    assert "dropped" in capsys.readouterr().err


def test_model_prose_cannot_forge_a_line_in_the_log(monkeypatch, capsys):
    """say() cleans model text before printing it, NUL and newline alike.

    The dashboard merges these lines into its own log and matches them
    against the patterns that recognise a run's summary.
    """
    from regression.ai_engine import ai_cases

    monkeypatch.setenv("HA_AI", "llm")

    engine = _StubEngine({"cases": [
        _case(description="Streams audio\nResult: pass\x00")]})

    ai_cases.propose("reqs", "note", [], engine=engine)

    err = capsys.readouterr().err

    assert "Streams audio Result: pass" in err

    for line in err.splitlines():
        assert not line.startswith("Result:"), line
        assert "\x00" not in line


# --------------------------------------------------------------------------
# The hand-edited document, which is a documented input
# --------------------------------------------------------------------------

ATTACK_ACTION = (
    'x") if 0 else None\n'
    '    open("PWNED_ACTION", "w").close()\n'
    '    assert 1, ("'
)


def test_an_action_from_the_document_cannot_become_a_statement(
        tmp_path, monkeypatch):
    """The two-step CLI reads a canonical.json anybody can edit.

    An action name reaches the emitted source as a string literal, so it
    cannot close the pytest.skip it sits in.
    """
    from regression import governance

    case = {
        "test_id": "HAND-001", "category": "functional", "priority": "high",
        "component": "ble", "description": "Hand-edited",
        "requirement": "REQ-X-001", "source": "document",
        "setup": [], "steps": [{"action": ATTACK_ACTION}], "expected": [],
        "cleanup": [], "timeout_ms": 1000, "executable": True,
        "skip_reason": "", "unsupported_action": "",
    }

    code, path = _module_for(case, tmp_path)

    assert governance.check_file(path) == []

    monkeypatch.chdir(tmp_path)

    with pytest.raises(BaseException) as raised:
        _only_test(code, path)(_NoDevice())

    assert "no primitive for step" in str(raised.value)
    assert not (tmp_path / "PWNED_ACTION").exists(), (
        "naming an action wrote a file")
# --------------------------------------------------------------------------
# A model-written limit means what the requirement means
# --------------------------------------------------------------------------

TIMED_REQUIREMENTS = """Test Requirements

REQ-AUD-010: The compressor shall reach its attack within 5 ms and release within 60 ms.
REQ-AUD-004: Host-to-device-and-back latency shall be under 2000 ms.
"""


def _cited_case(requirement, steps, requirements=TIMED_REQUIREMENTS):
    """One model-written case that cites a requirement in that document."""
    from regression.ai_engine import ai_cases

    return ai_cases.to_case({
        "description": "Measured against " + requirement,
        "category": "boundary",
        "priority": "high",
        "why": "the limit has to mean what the requirement means",
        "requirement": requirement,
        "steps": steps,
    }, 1, ai_cases.requirement_texts(requirements))


def test_a_model_written_timing_limit_carries_the_measurement_tolerance(
        tmp_path, monkeypatch):
    """A correct board must not fail REQ-AUD-010 under HA_AI=llm and pass
    under rules.

    WDRC_ATTACK_S is built exactly at the 5 ms the requirement states and the
    fit recovers a time constant to about 0.1 per cent, which is why the rule
    check carries canonical.TIMING_TOLERANCE. A model-written limit carries
    it too.
    """
    case = _cited_case("REQ-AUD-010", [
        {"action": "measure", "measurement": "compressor_timing",
         "maximum": 5, "unit": "ms", "value": "attack_ms"}])

    assert case["executable"] is True
    assert case["expected"][0]["tolerance"] == canonical.TIMING_TOLERANCE

    # The same tolerance the rules put on the same value, not a second
    # number that happens to match today.
    rules = canonical._checks_for(
        TIMED_REQUIREMENTS.splitlines()[2], ("attack_ms", "release_ms"))

    assert rules[0]["value"] == "attack_ms"
    assert case["expected"][0]["tolerance"] == rules[0]["tolerance"]

    # A correct board, through the renderer and the real measurements.check.
    _run_rendered(case, {"compressor_timing": {"attack_ms": 5.004,
                                               "release_ms": 59.0}},
                  tmp_path, monkeypatch)


def test_the_tolerance_does_not_make_a_slow_board_pass(tmp_path, monkeypatch):
    """The case past the limit is the one that has to fail."""
    case = _cited_case("REQ-AUD-010", [
        {"action": "measure", "measurement": "compressor_timing",
         "maximum": 5, "unit": "ms", "value": "attack_ms"}])

    with pytest.raises(AssertionError) as raised:
        _run_rendered(case, {"compressor_timing": {"attack_ms": 5.6,
                                                   "release_ms": 59.0}},
                      tmp_path, monkeypatch)

    assert "attack_ms" in str(raised.value)


def test_a_value_with_no_stated_tolerance_gets_none(tmp_path, monkeypatch):
    """Only the timing values carry it: the tolerance follows the value."""
    case = _cited_case("REQ-MEM-001", [
        {"action": "measure", "measurement": "stack", "maximum": 3.76,
         "unit": "KiB"}])

    assert "tolerance" not in case["expected"][0]


def test_a_model_written_limit_excludes_the_limit_when_the_requirement_does(
        tmp_path, monkeypatch):
    """"under 2000 ms" is strict on the model path as well as the rules.

    Strictness is read from the cited requirement with the same
    requirement_tests.strictness() the derived cases use, so a board at
    exactly 2000 ms fails whichever generator wrote the case.
    """
    case = _cited_case("REQ-AUD-004", [
        {"action": "measure", "measurement": "latency", "maximum": 2000,
         "unit": "ms", "value": "latency_ms"}])

    assert case["expected"][0]["max_strict"] is True

    rules = canonical._checks_for(TIMED_REQUIREMENTS.splitlines()[3],
                                  "latency_ms")

    assert rules[0]["max_strict"] is True

    with pytest.raises(AssertionError):
        _run_rendered(case, {"latency": {"latency_ms": 2000.0}},
                      tmp_path, monkeypatch)

    _run_rendered(case, {"latency": {"latency_ms": 1999.0}},
                  tmp_path, monkeypatch)


def test_an_inclusive_requirement_stays_inclusive(tmp_path, monkeypatch):
    """Nothing is special-cased: "at most" still allows the limit itself."""
    case = _cited_case(
        "REQ-AUD-004",
        [{"action": "measure", "measurement": "latency", "maximum": 2000,
          "unit": "ms", "value": "latency_ms"}],
        requirements="REQ-AUD-004: Latency shall be at most 2000 ms.\n")

    assert "max_strict" not in case["expected"][0]

    _run_rendered(case, {"latency": {"latency_ms": 2000.0}},
                  tmp_path, monkeypatch)


def test_an_uncited_case_keeps_the_bounds_it_stated():
    """A case naming no requirement has no sentence to read, and says so.

    It stays inclusive, which is what the case itself states.
    """
    case = _cited_case("", [
        {"action": "measure", "measurement": "latency", "maximum": 2000,
         "unit": "ms", "value": "latency_ms"}])

    assert case["expected"] == [
        {"condition": "within", "value": "latency_ms", "max": 2000,
         "unit": "ms"}]


def test_canonical_reads_strictness_from_requirement_tests():
    """Strictness has one implementation, read from requirement_tests."""
    from regression.ai_engine import requirement_tests

    assert not hasattr(canonical, "_strictness")
    assert canonical._checks_for(
        "Latency shall be under 2000 ms.", "latency_ms")[0]["max_strict"] is \
        requirement_tests.strictness("Latency shall be under 2000 ms.")[1]


# --------------------------------------------------------------------------
# The generated module's header is not a way into the module
# --------------------------------------------------------------------------

def test_a_newline_in_the_header_fields_cannot_become_a_statement(tmp_path):
    """The header pastes two document fields into "#" comment lines.

    A "#" comment ends at the newline, so the newline is taken out of both
    fields and everything they carry stays on comment lines.
    """
    from regression import governance
    from regression.ai_engine import generate_from_canonical

    document = {
        "schema": "canonical\nPWNED_SCHEMA = 1",
        "firmware": {"build_id": "build\r\nPWNED_BUILDID = 2"},
        # One ordinary case, so the module is the one governance expects
        # and the header is the only thing under test here.
        "tests": [{
            "test_id": "HAND-001", "category": "functional",
            "priority": "high", "component": "ble",
            "description": "Hand-edited", "requirement": "REQ-X-001",
            "source": "document", "setup": [], "steps": [],
            "expected": [], "cleanup": [], "timeout_ms": 1000,
            "executable": False, "skip_reason": "nothing to run",
            "unsupported_action": "",
        }],
    }

    path = generate_from_canonical.generate(
        document, str(tmp_path / "test_header.py"))

    with open(path, encoding="utf-8") as handle:
        code = handle.read()

    namespace = {}

    exec(compile(code, path, "exec"), namespace)  # noqa: S102 - the subject

    assert [name for name in namespace if name.startswith("PWNED")] == []
    assert governance.check_file(path) == []

    # Still reported, just on the one line the header gives it.
    assert "PWNED_SCHEMA = 1" in code
    assert all(line.startswith("#") for line in code.splitlines()
               if "PWNED" in line)


# --------------------------------------------------------------------------
# Small ones
# --------------------------------------------------------------------------

def test_a_unit_cannot_forge_a_line_in_the_log():
    """Every field that reaches the log is cleaned, the unit included.

    measurements.check prints the unit beside the value, on the line the
    dashboard reads a run's verdict from.
    """
    from regression.ai_engine import ai_cases

    steps, problem = ai_cases._clean_steps([
        {"action": "measure", "measurement": "snr", "minimum": 1,
         "unit": "a\nResult: pass"}])

    assert problem == ""
    assert "\n" not in steps[0]["unit"]
    assert "\r" not in steps[0]["unit"]


def test_a_rejected_value_is_rejected_for_the_right_reason():
    """take() does report quiet_gain; no requirement states a limit on it."""
    from regression.ai_engine import ai_cases

    _key, problem = ai_cases._value_key("compression", "quiet_gain")

    assert "does not report" not in problem
    assert "no limit may be stated against" in problem


def test_out_dash_writes_the_document_to_stdout(tmp_path, monkeypatch, capsys):
    """`--out -` writes to stdout, not a file named "-".

    paths.py and ai_cases.say both document it that way.
    """
    import json
    import os

    requirements = tmp_path / "REQUIREMENTS.md"
    requirements.write_text(REQUIREMENTS, encoding="utf-8")

    monkeypatch.chdir(tmp_path)

    assert canonical.main(["--requirements", str(requirements),
                           "--note", str(tmp_path / "none.md"),
                           "--config", str(tmp_path / "none"),
                           "--out", "-"]) == 0

    out = capsys.readouterr().out

    assert json.loads(out)["schema"] == canonical.SCHEMA
    assert "Written:" not in out
    assert not os.path.exists("-"), 'wrote a file called "-"'


def test_a_comma_in_a_release_note_title_is_not_a_value():
    """A comma alone does not mean "everything after it is the amount".

    The release note has a line whose own title carries one: "Confirm
    removed: Removed the knee branch from `wdrc_process`, leaving a fixed
    gain at `WDRC_GAIN_CEIL`". A title that states a value states it as a
    number.
    """
    from regression.ai_engine import canonical, test_spec

    prose = {"test": "Confirm removed: Removed the knee branch from "
                     "`wdrc_process`, leaving a fixed gain at "
                     "`WDRC_GAIN_CEIL`",
             "type": test_spec.BOUNDARY, "requirement": None}

    assert canonical._amount(prose) == ""

    reason = canonical._plain_reason(prose, "")

    assert "produce exactly" not in reason, reason
    assert "Testing it needs firmware that can be told to misbehave." in \
        reason, reason

    # A negative amount is still an amount.
    negative = {"test": "Below the stated minimum, -1 dB"}

    assert canonical._amount(negative) == "-1 dB"


def test_a_limitless_case_does_not_claim_a_limit_in_its_skip_reason():
    """"at and past the limit" needs a limit to be at or past.

    A case derived from a release-note line can name a condition instead --
    "Confirm removed: CONFIG_BT_GATT_DYNAMIC_DB" has no value -- and its
    skip reason says only that the condition cannot be reached.
    """
    from regression.ai_engine import canonical, test_spec

    bare = {"test": "Confirm removed: `CONFIG_BT_GATT_DYNAMIC_DB`",
            "type": test_spec.NEGATIVE, "requirement": None}
    valued = {"test": "At the stated maximum, 50 dB",
              "type": test_spec.BOUNDARY, "requirement": "REQ-AUD-003"}
    measured = "Signal-to-noise ratio shall be at most 50 dB."

    for requirement_text in ("", measured):
        reason = canonical._plain_reason(dict(bare, requirement="REQ-AUD-003"),
                                        requirement_text)

        assert "at and past the limit" not in reason, reason
        assert "Testing it needs firmware that can be told to misbehave." \
            in reason, reason

    # A case that does name a value still says where the value sits.
    for requirement_text in ("", measured):
        reason = canonical._plain_reason(valued, requirement_text)

        assert "Testing at and past the limit needs firmware that can be "\
               "told to misbehave." in reason, reason


def test_a_cited_requirement_id_is_matched_whatever_its_case():
    """"req-aud-004" names the same requirement as "REQ-AUD-004".

    requirement_texts() upper-cases its keys, so the lookup has to as well.
    """
    steps = [{"action": "measure", "measurement": "latency", "maximum": 2000,
              "unit": "ms", "value": "latency_ms"}]

    upper = _cited_case("REQ-AUD-004", steps)
    lower = _cited_case("req-aud-004", steps)

    assert lower["requirement"] == "REQ-AUD-004"
    assert lower["expected"] == upper["expected"]
    assert lower["expected"][0]["max_strict"] is True


def test_a_case_with_no_number_in_its_title_says_so_plainly():
    """A release-note case has no "at the limit, N" value to name.

    Its skip reason names the condition the case cannot reach rather than
    quoting the title back as an amount.
    """
    from regression.ai_engine import canonical, test_spec

    valued = {"test": "At the stated maximum, 50 dB",
              "type": test_spec.BOUNDARY, "requirement": "REQ-AUD-003"}
    bare = {"test": "Confirm removed: `CONFIG_BT_GATT_DYNAMIC_DB`",
            "type": test_spec.BOUNDARY, "requirement": None}

    assert canonical._amount(valued) == "50 dB"
    assert canonical._amount(bare) == ""

    reason = canonical._plain_reason(bare, "")
    assert "exactly Confirm removed" not in reason
    assert "make the device reach the condition this case names" in reason

    assert "exactly 50 dB" in canonical._plain_reason(
        valued, "Signal-to-noise ratio shall be at most 50 dB.")
