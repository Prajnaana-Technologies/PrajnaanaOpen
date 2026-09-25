# Copyright (c) 2026 Prajnaana Technologies Pvt. Ltd.
# Original author: Dhanya Shree S
# SPDX-License-Identifier: MIT
#
# Licensed under the MIT License. See LICENSE in the project root.

"""Tests for the test-planning layer.

None of these touch the network. The LLM path is exercised through a stubbed
client so the contract -- schema, fallback, safe limits -- is covered without
spending money or requiring a key.
"""

import sys

import io
import urllib.error
import json
import os

import pytest

from regression.ai_engine import llm_client, planner
from regression.ai_engine.planner import (
    KNOWN_SCENARIOS,
    RegressionPlan,
    plan_regression,
)
from regression.ai_engine.scenario_tests import SCENARIO_TESTS

NOTE_WITH_GUIDANCE = """<!-- e.g. "removed the knee from the compressor" -->
## build-1
- Reworked pairing key storage.
"""

# memory is work-queue stack high-water in KiB; the risk engine's limit is 3.76.
# DEGRADED breaches power (60), memory (3.76) and retry (5). Its sync of 26 ms
# is inside the 50 ms limit, which is deliberate: sync does not drive the
# intensity, so a breach there would prove nothing these tests assert.
HEALTHY = {"power": 38, "memory": 3.1, "sync": 12, "retry": 1}
DEGRADED = {"power": 78, "memory": 3.9, "sync": 26, "retry": 7}


# --------------------------------------------------------------------------
# Mode selection -- rules is the default, the LLM is opt-in
# --------------------------------------------------------------------------

def test_rules_is_the_default_mode(monkeypatch):
    monkeypatch.delenv(planner.MODE_ENV, raising=False)

    assert planner.get_mode() == planner.RULES
    assert plan_regression(HEALTHY, "docs tweak").source == planner.RULES


def test_llm_mode_is_opt_in(monkeypatch):
    monkeypatch.setenv(planner.MODE_ENV, "llm")

    assert planner.get_mode() == planner.LLM


def test_unknown_mode_is_rejected(monkeypatch):
    monkeypatch.setenv(planner.MODE_ENV, "telepathy")

    with pytest.raises(ValueError, match="telepathy"):
        planner.get_mode()


def test_rules_path_never_calls_the_llm(monkeypatch):
    """The default must not touch the network, even by accident."""
    monkeypatch.delenv(planner.MODE_ENV, raising=False)

    def explode(*args, **kwargs):
        raise AssertionError("rules mode must not call the LLM")

    monkeypatch.setattr(llm_client, "complete_json", explode)
    monkeypatch.setattr(llm_client, "availability", explode)

    assert plan_regression(DEGRADED, "bluetooth update").source == planner.RULES


# --------------------------------------------------------------------------
# Rule-based decisions
# --------------------------------------------------------------------------

@pytest.mark.parametrize(
    "metrics,change,expected",
    [
        (HEALTHY, "readme update", "low"),
        (HEALTHY, "bluetooth update", "high"),
        ({"power": 95, "memory": 3.9}, "readme", "high"),
        ({"power": 10, "memory": 3.9}, "readme", "medium"),
    ],
)
def test_rule_intensity(metrics, change, expected):
    assert planner.decide_intensity(metrics, change) == expected


def test_rule_scenarios_track_the_metrics():
    scenarios = planner.suggest_scenarios("readme", DEGRADED, ["memory_leak"], 4)

    assert "memory_pressure" in scenarios
    assert "power_stress" in scenarios
    assert "long_duration_stability" in scenarios
    assert "reconnect_stress" in scenarios


# --------------------------------------------------------------------------
# Plan invariants -- these hold whoever produced the plan
# --------------------------------------------------------------------------

def test_packets_are_capped():
    for intensity in planner.INTENSITIES:
        plan = RegressionPlan(intensity, [], "", planner.RULES)
        assert plan.packets <= planner.MAX_PACKETS


def test_unknown_scenarios_are_dropped():
    plan = RegressionPlan("low", ["memory_pressure", "launch_the_missiles"], "", "llm")

    assert plan.scenarios == ["memory_pressure"]


def test_bad_intensity_is_rejected():
    with pytest.raises(ValueError):
        RegressionPlan("catastrophic", [], "", planner.RULES)


def test_schema_matches_the_generator_vocabulary():
    """If these drift, the LLM emits scenarios that generate nothing."""
    schema_scenarios = planner.PLAN_SCHEMA["properties"]["scenarios"]["items"]["enum"]

    assert set(schema_scenarios) == set(KNOWN_SCENARIOS)
    assert planner.PLAN_SCHEMA["properties"]["intensity"]["enum"] == list(
        planner.INTENSITIES
    )
    assert planner.PLAN_SCHEMA["additionalProperties"] is False


# --------------------------------------------------------------------------
# LLM path, stubbed
# --------------------------------------------------------------------------

def test_llm_plan_is_used_when_available(monkeypatch):
    monkeypatch.setenv(planner.MODE_ENV, "llm")
    monkeypatch.setattr(llm_client, "availability", lambda: (True, "ready"))
    monkeypatch.setattr(llm_client, "model_name", lambda: "stub-model")
    monkeypatch.setattr(
        llm_client, "complete_json",
        lambda **kwargs: {
            "intensity": "high",
            "scenarios": ["power_stress"],
            "reasoning": "power draw is up sharply",
        },
    )

    plan = plan_regression(DEGRADED, "power management rewrite")

    assert plan.source == planner.LLM
    assert plan.intensity == "high"
    assert plan.scenarios == ["power_stress"]
    assert plan.packets == 80


def test_llm_failure_falls_back_to_rules(monkeypatch):
    monkeypatch.setenv(planner.MODE_ENV, "llm")
    monkeypatch.setattr(llm_client, "availability", lambda: (True, "ready"))
    monkeypatch.setattr(llm_client, "model_name", lambda: "stub-model")

    def boom(**kwargs):
        raise RuntimeError("API is down")

    monkeypatch.setattr(llm_client, "complete_json", boom)

    plan = plan_regression(HEALTHY, "bluetooth update")

    assert plan.source == planner.RULES
    assert plan.intensity == "high"


def test_missing_credentials_fall_back_to_rules(monkeypatch):
    monkeypatch.setenv(planner.MODE_ENV, "llm")
    monkeypatch.setattr(llm_client, "availability", lambda: (False, "no credentials"))

    assert plan_regression(HEALTHY, "readme").source == planner.RULES


def test_llm_garbage_falls_back_to_rules(monkeypatch):
    """A plan that violates the invariants must not reach the generator."""
    monkeypatch.setenv(planner.MODE_ENV, "llm")
    monkeypatch.setattr(llm_client, "availability", lambda: (True, "ready"))
    monkeypatch.setattr(llm_client, "model_name", lambda: "stub-model")
    monkeypatch.setattr(
        llm_client, "complete_json",
        lambda **kwargs: {"intensity": "nuclear", "scenarios": [], "reasoning": ""},
    )

    assert plan_regression(HEALTHY, "readme").source == planner.RULES


# --------------------------------------------------------------------------
# Client configuration
# --------------------------------------------------------------------------

def test_default_model_is_current():
    assert llm_client.DEFAULT_MODEL == "claude-opus-5"


def test_model_is_overridable(monkeypatch):
    monkeypatch.setenv("HA_LLM_MODEL", "claude-sonnet-5")

    assert llm_client.model_name() == "claude-sonnet-5"


def test_availability_reports_a_reason_when_sdk_is_missing(monkeypatch):
    monkeypatch.setitem(sys.modules, "anthropic", None)

    ok, reason = llm_client.availability()

    assert ok is False
    assert "anthropic" in reason


# --------------------------------------------------------------------------
# Unmeasured metrics must not lower the intensity
# --------------------------------------------------------------------------

def test_unmeasured_power_escalates_rather_than_reading_as_zero():
    """One step, not to the top: the risk engine has already scored it."""
    metrics = dict(HEALTHY, unmeasured=["power"])

    assert planner.decide_intensity(metrics, "readme update") == "medium"


def test_unmeasured_memory_escalates():
    metrics = dict(HEALTHY, unmeasured=["memory"])

    assert planner.decide_intensity(metrics, "readme update") == "medium"


def test_a_fully_measured_healthy_device_stays_low():
    metrics = dict(HEALTHY, unmeasured=[])

    assert planner.decide_intensity(metrics, "readme update") == "low"


def test_unmeasured_metrics_add_their_scenarios():
    scenarios = planner.suggest_scenarios(
        "readme", dict(HEALTHY, unmeasured=["power", "memory"])
    )

    assert "memory_pressure" in scenarios
    assert "power_stress" in scenarios


def test_the_llm_prompt_says_unmeasured_is_not_good():
    """The model must not read a missing metric as a passing one either."""
    prompt = planner.build_prompt(
        dict(HEALTHY, unmeasured=["power"]), "bluetooth update"
    )

    assert "unmeasured" in prompt
    assert "never as good" in prompt


# --------------------------------------------------------------------------
# Release notes as an input to selection
# --------------------------------------------------------------------------

def test_a_release_note_in_plain_words_selects_scenarios():
    """The vocabulary covers the way a person describes a change, not only
    the phrases firmware_diff emits."""
    from regression.ai_engine.planner import triggered_by_change

    assert triggered_by_change(
        "removed the knee branch from the WDRC compressor gain curve"
    ) == ["concurrency_stream_and_read"]


def test_a_word_inside_another_word_is_not_a_match():
    """Matching is on whole words: a substring search would read 'during' as
    'ring' and 'belong' as 'long'."""
    from regression.ai_engine.planner import triggered_by_change

    assert "stress_stability" not in triggered_by_change("dropped during the run")
    assert "long_duration_stability" not in triggered_by_change(
        "the compressor is no longer applying a knee")


def test_a_prefix_still_matches_the_whole_word():
    from regression.ai_engine.planner import triggered_by_change

    assert "concurrency_stream_and_read" in triggered_by_change(
        "reworked the workqueue scheduling")
    assert "fault_link_drop" in triggered_by_change("advertising restarts now")


def test_the_note_replaces_the_image_evidence(tmp_path, monkeypatch):
    """The note is the statement of what the release changed, so selection
    follows it and nothing else."""
    from regression.ai_engine import orchestrator
    from regression.ai_engine.planner import triggered_by_change

    note = tmp_path / "RELEASE_NOTES.md"
    note.write_text("Reworked pairing and bonding key storage.", encoding="utf-8")
    monkeypatch.setenv(orchestrator.RELEASE_NOTE_ENV, str(note))

    selected = triggered_by_change(
        orchestrator.from_release_note(note.read_text(encoding="utf-8"), "audio dsp")
    )

    assert selected == ["pairing_bonding"]
    assert "concurrency_stream_and_read" not in selected   # the image said so


def test_a_note_that_names_nothing_selects_nothing(tmp_path, monkeypatch):
    """The cost of note-only selection: an incomplete note is an incomplete
    run, and the image is not consulted to make up the difference."""
    from regression.ai_engine import orchestrator
    from regression.ai_engine.planner import triggered_by_change

    note = tmp_path / "RELEASE_NOTES.md"
    note.write_text("Tidied up some comments.", encoding="utf-8")
    monkeypatch.setenv(orchestrator.RELEASE_NOTE_ENV, str(note))

    assert triggered_by_change(
        orchestrator.from_release_note(note.read_text(encoding="utf-8"), "audio dsp")
    ) == []


def test_no_release_note_means_the_evidence_path_is_used(monkeypatch):
    from regression.ai_engine import orchestrator

    monkeypatch.delenv(orchestrator.RELEASE_NOTE_ENV, raising=False)

    assert orchestrator.read_release_note() == ""


def test_a_missing_release_note_is_ignored_not_fatal(monkeypatch, tmp_path):
    """A path that does not exist must not take the run down with it."""
    from regression.ai_engine import orchestrator

    monkeypatch.setenv(orchestrator.RELEASE_NOTE_ENV, str(tmp_path / "absent.md"))

    assert orchestrator.read_release_note() == ""


def test_guidance_in_a_comment_does_not_select_anything(tmp_path, monkeypatch):
    """A note carries advice for whoever writes the next one, and advice
    contains examples, and examples contain the words that trigger
    selection, so the advice is stripped before selection reads it."""
    from regression.ai_engine import orchestrator
    from regression.ai_engine.planner import triggered_by_change

    note = tmp_path / "RELEASE_NOTES.md"
    note.write_text(NOTE_WITH_GUIDANCE, encoding="utf-8")
    monkeypatch.setenv(orchestrator.RELEASE_NOTE_ENV, str(note))

    assert triggered_by_change(orchestrator.read_release_note()) == [
        "pairing_bonding"]


def test_the_shipped_release_note_selects_from_its_entries_only(monkeypatch):
    """The real file, read the way the pipeline reads it."""
    import os

    from regression.ai_engine import orchestrator
    from regression.ai_engine.planner import triggered_by_change

    monkeypatch.setenv(
        orchestrator.RELEASE_NOTE_ENV,
        os.path.join("NRF_Firmware", "RELEASE_NOTES.md"),
    )

    note = orchestrator.read_release_note()

    if not note:
        # Skipped, not returned: a bare return reports the test as
        # PASSED although it asserted nothing.
        pytest.skip("no NRF_Firmware/RELEASE_NOTES.md in this checkout "
                    "(the path is relative to src/)")

    head, _, entries = note.partition("## ")

    assert triggered_by_change(head) == []
    assert triggered_by_change(entries)


# --------------------------------------------------------------------------
# One section per build
# --------------------------------------------------------------------------

NOTE_WITH_HISTORY = """# Firmware release notes

## build-new

### Audio

- Restored the compressor knee.

## build-old

### Bluetooth

- Reworked pairing and bonding key storage.
"""


def test_only_the_section_for_the_build_under_test_is_read(monkeypatch, tmp_path):
    """Selection reads the section for the build under test, not the whole
    note."""
    from regression.ai_engine import orchestrator
    from regression.ai_engine.planner import triggered_by_change

    note = tmp_path / "RELEASE_NOTES.md"
    note.write_text(NOTE_WITH_HISTORY, encoding="utf-8")
    monkeypatch.setenv(orchestrator.RELEASE_NOTE_ENV, str(note))
    monkeypatch.setenv(orchestrator.BUILD_ID_ENV, "build-new")

    selected = triggered_by_change(orchestrator.read_release_note())

    assert "concurrency_stream_and_read" in selected   # from build-new
    assert "pairing_bonding" not in selected           # history, not selected


def test_a_section_keeps_its_own_subheadings(monkeypatch, tmp_path):
    """A section runs to the next heading of its own level: "### Audio"
    under "## <build>" is part of that build's content."""
    from regression.ai_engine import orchestrator

    note = tmp_path / "RELEASE_NOTES.md"
    note.write_text(NOTE_WITH_HISTORY, encoding="utf-8")
    monkeypatch.setenv(orchestrator.RELEASE_NOTE_ENV, str(note))
    monkeypatch.setenv(orchestrator.BUILD_ID_ENV, "build-new")

    section = orchestrator.read_release_note()

    assert "### Audio" in section
    assert "Restored the compressor knee" in section
    assert "pairing" not in section


def test_a_note_without_build_headings_is_read_whole(monkeypatch, tmp_path):
    """Selecting nothing would be worse than selecting too much."""
    from regression.ai_engine import orchestrator
    from regression.ai_engine.planner import triggered_by_change

    note = tmp_path / "RELEASE_NOTES.md"
    note.write_text("- Reworked pairing key storage.", encoding="utf-8")
    monkeypatch.setenv(orchestrator.RELEASE_NOTE_ENV, str(note))
    monkeypatch.setenv(orchestrator.BUILD_ID_ENV, "some-build-nobody-wrote-about")

    assert triggered_by_change(orchestrator.read_release_note()) == [
        "pairing_bonding"]


# --------------------------------------------------------------------------
# One set of limits, and the evidence the planner is given
# --------------------------------------------------------------------------

def test_the_rule_planner_uses_the_risk_engine_limits():
    from regression.change_detection.risk_engine import METRIC_THRESHOLDS

    limit = METRIC_THRESHOLDS["memory"]

    assert planner.decide_intensity(dict(HEALTHY, memory=limit), "readme") == "low"
    assert planner.decide_intensity(dict(HEALTHY, memory=limit + 0.1), "readme") == "medium"
    assert planner.decide_intensity(
        dict(HEALTHY, power=METRIC_THRESHOLDS["power"] + 1), "readme") == "high"


def test_the_prompt_quotes_the_risk_engine_limits():
    from regression.change_detection.risk_engine import METRIC_THRESHOLDS

    for name, limit in METRIC_THRESHOLDS.items():
        assert "over {} ".format(limit) in planner.SYSTEM_PROMPT, name

    assert "stack" in planner.SYSTEM_PROMPT
    assert "not heap" in planner.SYSTEM_PROMPT


def test_history_reaches_the_prompt():
    history = [{"build_id": "b1", "status": "fail", "failed_tests": ["test_reconnect"]}]

    prompt = planner.build_prompt(HEALTHY, "readme", ["connection"], 1, history)

    assert "test_reconnect" in prompt
    assert "score 1" in prompt


def _generate(monkeypatch, tmp_path, **kwargs):
    from regression.ai_engine import generate_tests as gen

    monkeypatch.delenv(planner.MODE_ENV, raising=False)
    monkeypatch.setattr(gen, "GENERATED_TEST_PATH", str(tmp_path / "test_ai_generated.py"))

    seen = {}
    real = gen.plan_regression

    def spy(*args, **kw):
        seen.update(kw)
        return real(*args, **kw)

    monkeypatch.setattr(gen, "plan_regression", spy)

    plan = gen.generate_tests(**kwargs)

    return plan, seen, (tmp_path / "test_ai_generated.py").read_text(encoding="utf-8")


def test_the_planner_gets_the_risk_score_not_the_flag_count(monkeypatch, tmp_path):
    """prioritized_tests=None must not be read as "nothing to run".

    A full regression passes None rather than a list. None means every risk
    test, which is what
    test_the_prompt_and_the_rules_agree_on_a_full_regression pins.
    """
    _, seen, _ = _generate(monkeypatch, tmp_path, metrics=DEGRADED,
                           change_info="readme", prioritized_tests=None,
                           risk_score=6, history=[{"build_id": "b0"}])

    assert seen["risk_score"] == 6
    assert seen["history"] == [{"build_id": "b0"}]


def test_the_score_is_computed_when_the_caller_has_none(monkeypatch, tmp_path):
    from regression.change_detection.risk_engine import calculate_risk_score

    _, seen, _ = _generate(monkeypatch, tmp_path, metrics=DEGRADED,
                           change_info="readme", prioritized_tests=None)

    assert seen["risk_score"] == calculate_risk_score(DEGRADED)


def test_the_intensity_reaches_the_generated_tests(monkeypatch, tmp_path):
    plan, _, source = _generate(monkeypatch, tmp_path, metrics=HEALTHY,
                                change_info="readme", prioritized_tests=["connection"])

    assert plan.intensity == "low"
    assert 'INTENSITY = "low"' in source
    assert "PACKETS = 20" in source


def test_escalation_raises_the_intensity(monkeypatch, tmp_path):
    plan, _, source = _generate(monkeypatch, tmp_path, metrics=HEALTHY,
                                change_info="readme", prioritized_tests=["connection"],
                                min_intensity="high")

    assert plan.intensity == "high"
    assert 'INTENSITY = "high"' in source
    assert "after a failure" in plan.reasoning


def test_a_floor_never_lowers_the_plan(monkeypatch, tmp_path):
    plan, _, _ = _generate(monkeypatch, tmp_path, metrics=DEGRADED,
                           change_info="bluetooth", prioritized_tests=None,
                           min_intensity="low")

    assert plan.intensity == "high"


def test_the_selection_is_counted_against_the_full_vocabulary(monkeypatch, tmp_path):
    from regression.ai_engine.generate_tests import defined_tests

    plan, _, source = _generate(monkeypatch, tmp_path, metrics=HEALTHY,
                                change_info="readme", prioritized_tests=["connection"])

    every_scenario = sum(len(defined_tests(SCENARIO_TESTS[s])) for s in KNOWN_SCENARIOS)
    chosen = sum(len(defined_tests(SCENARIO_TESTS[s])) for s in plan.scenarios)

    assert plan.selection["selected"] == len(defined_tests(source))
    assert plan.selection["full"] == plan.selection["selected"] - chosen + every_scenario
    assert len(plan.selection["left_out"]) == every_scenario - chosen


def test_the_memory_test_can_fail_on_a_real_stack():
    """A bound a 4 KiB stack cannot reach is a test that cannot fail."""
    from regression.ai_engine.generate_tests import THRESHOLDS
    from regression.change_detection.risk_engine import (
        METRIC_THRESHOLDS,
        WORKQUEUE_STACK_KB,
    )

    assert THRESHOLDS["memory"]["max"] == METRIC_THRESHOLDS["memory"]
    assert THRESHOLDS["memory"]["max"] < WORKQUEUE_STACK_KB


def test_the_generated_memory_bound_is_the_calibrated_limit(monkeypatch, tmp_path):
    from regression.change_detection.risk_engine import METRIC_THRESHOLDS

    _, _, source = _generate(monkeypatch, tmp_path, metrics=HEALTHY,
                             change_info="readme", prioritized_tests=["connection"])

    namespace = {}
    line = next(l for l in source.splitlines() if l.startswith("THRESHOLDS = "))
    exec(line, namespace)

    assert namespace["THRESHOLDS"]["memory"]["max"] == METRIC_THRESHOLDS["memory"]


def test_full_regression_never_runs_less_than_targeted():
    """None means "nothing was narrowed away", so it cannot mean "none".

    The risk engine passes None for a full regression and the flagged list
    for a targeted one.
    """
    from regression.change_detection.risk_engine import (
        detect_risk_from_metrics, select_regression_slice)

    worst = {"unmeasured": ["power", "memory", "sync", "retry"]}
    # memory over its limit scores 2 on its own; everything else measured
    # and healthy, so the slice is targeted rather than full.
    medium = {"power": 10, "memory": 3.9, "sync": 5, "retry": 0}

    full_tests, full_score = select_regression_slice(worst)
    targeted_tests, targeted_score = select_regression_slice(medium)

    assert full_tests is None and full_score >= 4
    assert targeted_tests and 2 <= targeted_score < 4

    full = planner.suggest_scenarios("unknown change", worst,
                                     full_tests, full_score)
    targeted = planner.suggest_scenarios("unknown change", medium,
                                         targeted_tests, targeted_score)

    assert set(targeted) <= set(full), (
        "full regression {} misses scenarios a targeted run has: {}".format(
            full, sorted(set(targeted) - set(full))))
    assert len(full) >= len(targeted)

    # Every scenario the flagged-test map can reach is in a full run.
    for name in ("long_duration_stability", "stress_stability",
                 "reconnect_stress", "fault_link_drop",
                 "concurrency_stream_and_read"):
        assert name in full, name


def test_an_empty_flagged_list_still_means_none():
    # Only None means "unrestricted"; [] is a caller saying nothing flagged.
    healthy = {"power": 10, "memory": 2.5, "sync": 5, "retry": 0}

    assert planner.suggest_scenarios("readme tidy", healthy, [], 0) == []


def test_the_llm_request_matches_the_installed_sdk_signature():
    """Every argument the request sends must be one the SDK accepts.

    The SDK's create() has no **kwargs, and the planner catches TypeError
    and falls back to the rules, so every keyword the request sends is
    checked against the installed signature here.
    """
    import inspect

    anthropic = pytest.importorskip("anthropic")

    from regression.ai_engine import llm_client

    sent = {}

    class Messages:
        def create(self, **kwargs):
            sent.update(kwargs)
            raise RuntimeError("stop before the network")

    class Beta:
        messages = Messages()

    class Client:
        beta = Beta()
        messages = Messages()

    with pytest.raises(RuntimeError):
        llm_client._create(
            Client(),
            model="claude-opus-5",
            max_tokens=16,
            system="s",
            thinking={"type": "adaptive"},
            output_config={"effort": "low"},
            messages=[{"role": "user", "content": "hello"}],
        )

    assert "fallbacks" not in sent, "fallbacks must travel in extra_body"
    assert sent["extra_body"] == {"fallbacks": "default"}

    # The real SDK signature has to accept exactly these arguments.
    signature = inspect.signature(
        anthropic.Anthropic(api_key="not-used").beta.messages.create)

    signature.bind(**sent)


def test_an_old_sdk_reports_unavailable_instead_of_failing_later(monkeypatch):
    from regression.ai_engine import llm_client

    anthropic = pytest.importorskip("anthropic")

    monkeypatch.setattr(anthropic, "__version__", "0.40.0", raising=False)

    ok, reason = llm_client.availability()

    assert not ok and "too old" in reason


def test_the_selected_engine_plans_the_run(monkeypatch):
    """HA_ENGINE decides who plans, through the registry."""
    from regression.ai_engine import engines

    asked = {}

    class FakeEngine:
        name = "fake"

        def available(self):
            return True, "ready"

        def model_name(self):
            return "fake-1"

        def complete_json(self, system, prompt, schema, max_tokens=None):
            asked["called"] = True
            return {"intensity": "high", "scenarios": ["reconnect_stress"],
                    "reasoning": "because"}

    monkeypatch.setenv("HA_AI", "llm")
    monkeypatch.setattr(engines, "get_engine", lambda name=None: FakeEngine())

    plan = planner.plan_regression({"power": 10, "memory": 2.5, "sync": 5,
                                    "retry": 0}, "readme tidy", [], 0)

    assert asked.get("called"), "the selected engine was not asked to plan"
    assert plan.source == planner.LLM and plan.intensity == "high"


def test_an_engine_without_an_adapter_is_refused_not_substituted(monkeypatch):
    monkeypatch.setenv("HA_AI", "llm")
    monkeypatch.setenv("HA_ENGINE", "openai")

    plan = planner.plan_regression({"power": 10, "memory": 2.5, "sync": 5,
                                    "retry": 0}, "readme tidy", [], 0)

    # Refused, and the run continues on the rules rather than on a model
    # nobody selected.
    assert plan.source == planner.RULES


def test_editing_the_host_ble_client_does_not_force_high_intensity():
    """ble_audio.py is the host's GATT client, not firmware."""
    from regression.change_detection import git_changes

    healthy = {"power": 10, "memory": 2.5, "sync": 5, "retry": 0}

    assert git_changes.device_subsystems(["regression/ble/ble_audio.py"]) == []

    # Firmware files still are device-affecting.
    assert git_changes.device_subsystems(
        ["NRF_Firmware/src/ble_telemetry_service.c"]) == ["bluetooth telemetry"]

    description = dict(
        (fragment, text)
        for fragment, text, affects in git_changes.PATH_SUBSYSTEMS
    )["ble_audio"]

    assert planner.decide_intensity(healthy, description) == "low"


def test_no_host_only_label_contains_a_planner_trigger_word():
    """A host label that says "bluetooth" selects Bluetooth scenarios.

    Host-only changes are set aside rather than planned on, so this is a
    second line of defence -- and the one that catches a host label such as
    "bluetooth audio link" for the host's GATT client.
    """
    from regression.change_detection import git_changes

    healthy = {"power": 10, "memory": 2.5, "sync": 5, "retry": 0}
    words = sorted({w for ws in planner.CHANGE_TRIGGERS.values() for w in ws})

    offenders = {}

    for _fragment, text, affects_device in git_changes.PATH_SUBSYSTEMS:
        if affects_device:
            continue

        # planner._mentions, not a plain substring: "risk scoring" contains
        # "ring" and does not mention it.
        hits = [word for word in words
                if planner._mentions(text.lower(), word)]

        if hits or planner.decide_intensity(healthy, text) != "low":
            offenders[text] = hits or ["raises intensity"]

    assert offenders == {}, offenders


def test_one_limit_per_metric():
    """One limit per metric, written in exactly one place."""
    from regression.ai_engine.generate_tests import THRESHOLDS
    from regression.change_detection.risk_engine import METRIC_THRESHOLDS

    assert set(THRESHOLDS) == set(METRIC_THRESHOLDS)

    for name, limit in METRIC_THRESHOLDS.items():
        assert THRESHOLDS[name]["max"] == limit, name


def test_the_requirements_document_quotes_the_code_limits():
    """REQUIREMENTS.md states the numbers a run actually applies."""
    import os
    import re

    from regression.change_detection.risk_engine import METRIC_THRESHOLDS

    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    path = os.path.join(root, "REQUIREMENTS.md")

    if not os.path.exists(path):
        pytest.skip("no requirement document beside the source tree")

    with open(path, encoding="utf-8") as handle:
        text = handle.read()

    stated = {
        "power": (r"REQ-PWR-001:.*?([0-9.]+)\s*mA", METRIC_THRESHOLDS["power"]),
        "sync": (r"REQ-SYN-001:.*?([0-9.]+)\s*ms", METRIC_THRESHOLDS["sync"]),
        "memory": (r"REQ-MEM-001:.*?([0-9.]+)\s*KiB", METRIC_THRESHOLDS["memory"]),
    }

    for name, (pattern, limit) in stated.items():
        found = re.search(pattern, text)

        assert found, "REQUIREMENTS.md states no limit for {}".format(name)
        assert float(found.group(1)) == float(limit), (
            "{}: document says {}, code applies {}".format(
                name, found.group(1), limit))


def test_the_prompt_and_the_rules_agree_on_a_full_regression():
    """Both planners must read None as every flagged test."""
    metrics = {"memory": 2.5, "retry": 0, "unmeasured": ["power", "sync"]}

    prompt = planner.build_prompt(metrics, "unknown change", None, 6)
    line = next(l for l in prompt.splitlines() if l.startswith("Risk engine"))

    assert "none" not in line
    for name in planner.ALL_RISK_TESTS:
        assert name in line, name

    # An empty list still means nothing was flagged.
    empty = planner.build_prompt(metrics, "unknown change", [], 2)
    assert "flagged tests none" in empty


def test_governance_commands_cover_every_generated_module(monkeypatch, tmp_path):
    from regression import governance

    one = tmp_path / "test_ai_generated.py"
    two = tmp_path / "test_from_canonical.py"

    for path in (one, two):
        path.write_text("def test_x():\n    assert True\n", encoding="utf-8")

    monkeypatch.setattr(governance, "GENERATED_TEST_PATH", str(one))
    monkeypatch.setattr(governance, "CANONICAL_TEST_PATH", str(two))

    assert governance.generated_modules() == [str(one), str(two)]


def test_the_governance_commands_act_on_both_modules(monkeypatch, tmp_path,
                                                     capsys):
    """validate, approve and status, not just the list they act on."""
    from regression import governance

    one = tmp_path / "test_ai_generated.py"
    two = tmp_path / "test_from_canonical.py"

    for path in (one, two):
        path.write_text(
            "def test_x():\n    assert 1 == 1\n", encoding="utf-8")

    monkeypatch.setattr(governance, "GENERATED_TEST_PATH", str(one))
    monkeypatch.setattr(governance, "CANONICAL_TEST_PATH", str(two))
    monkeypatch.setattr(governance, "RECORD_PATH",
                        str(tmp_path / "approvals.json"))

    assert governance.main(["validate"]) == 0
    assert governance.main(["approve", "--by", "a tester"]) == 0
    assert governance.main(["status"]) == 0

    printed = capsys.readouterr().out

    for path in (one, two):
        assert path.name in printed, path.name

    # Approving the suite has to leave BOTH modules approved.
    for path in (one, two):
        assert governance.approval_for(str(path)), path.name


def test_a_wrong_path_is_a_sentence_not_a_traceback(capsys):
    from regression import governance

    assert governance.main(["--path", "no/such/module.py", "status"]) == 1
    assert "No module at" in capsys.readouterr().out


def test_section_for_finds_the_shipped_build(tmp_path):
    """The real id, not a synthetic one.

    The shipped id carries a git describe prefix that moves with every
    commit, and the note's heading is written with the content hash.
    """
    import json

    from regression.ai_engine import orchestrator
    from regression.paths import BASE_DIR

    baseline = os.path.join(BASE_DIR, "regression", "artifacts", "firmware",
                            "baseline.json")

    with io.open(baseline, encoding="utf-8") as handle:
        build_id = json.load(handle)["build_id"]

    note = os.path.join(BASE_DIR, "NRF_Firmware", "RELEASE_NOTES.md")

    with io.open(note, encoding="utf-8", errors="replace") as handle:
        text = handle.read()

    section = orchestrator.section_for(text, build_id)

    assert section is not None, "no heading matched {}".format(build_id)
    assert 0 < len(section) < len(text), (
        "the whole note came back, so nothing was selected")

    # The hash alone is what matches: the describe prefix moves.
    assert orchestrator.section_for(text, "ffffffff+" + build_id.rsplit(
        "+", 1)[1]) == section

    # An id no heading mentions falls back to the whole note, which is the
    # documented behaviour -- a note written without build headings is still
    # a note.
    assert orchestrator.section_for(text, "0000000+deadbeef") == text


# ---------------------------------------------------------------------------
# Engine transport helpers
# ---------------------------------------------------------------------------

class _FakeResponse:
    """Stands in for urlopen's context manager."""

    def __init__(self, payload):
        self._body = json.dumps(payload).encode("utf-8")

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def _http_error(code, body=b"{}"):
    return urllib.error.HTTPError(
        "https://example.invalid/v1", code, "Bad Request", {},
        io.BytesIO(body))


# ---------------------------------------------------------------------------
# Gemini engine
# ---------------------------------------------------------------------------

def _gemini_payload(text, finish="STOP"):
    return {"candidates": [{"finishReason": finish,
                            "content": {"parts": [{"text": text}]}}]}


def _gemini(monkeypatch, responses):
    from regression.ai_engine import gemini_client

    sent = []

    def fake_urlopen(request, timeout=None):
        sent.append((request.full_url,
                     json.loads(request.data.decode("utf-8"))))
        outcome = responses.pop(0)

        if isinstance(outcome, Exception):
            raise outcome

        return _FakeResponse(outcome)

    monkeypatch.setattr(gemini_client.urllib.request, "urlopen", fake_urlopen)
    monkeypatch.setenv(gemini_client.KEY_ENV, "AIza_test_not_a_real_key")

    return sent


def test_gemini_is_a_registered_engine():
    from regression.ai_engine import engines

    assert "gemini" in engines.available_engines()

    engine = engines.get_engine("gemini")

    assert engine.name == "gemini"
    assert engine.vendor == "Google"


def test_gemini_accepts_either_credential_variable(monkeypatch):
    from regression.ai_engine import gemini_client

    monkeypatch.delenv(gemini_client.KEY_ENV, raising=False)
    monkeypatch.delenv(gemini_client.ALT_KEY_ENV, raising=False)

    assert gemini_client.availability()[0] is False

    monkeypatch.setenv(gemini_client.ALT_KEY_ENV, "AIza_from_the_google_name")

    assert gemini_client.availability() == (True, "ready")


def test_gemini_translates_the_schema_it_cannot_send(monkeypatch):
    """responseSchema is OpenAPI, not JSON Schema: additionalProperties is a 400."""
    from regression.ai_engine import gemini_client, planner

    assert planner.PLAN_SCHEMA["additionalProperties"] is False

    converted = gemini_client._to_response_schema(planner.PLAN_SCHEMA)

    assert "additionalProperties" not in converted

    # The parts that constrain the answer must survive the translation.
    assert converted["required"] == ["intensity", "scenarios", "reasoning"]
    assert set(converted["properties"]["intensity"]["enum"]) == set(
        planner.INTENSITIES)
    assert set(converted["properties"]["scenarios"]["items"]["enum"]) == set(
        planner.KNOWN_SCENARIOS)

    sent = _gemini(monkeypatch, [_gemini_payload(json.dumps(
        {"intensity": "high", "scenarios": [], "reasoning": "why"}))])

    gemini_client.complete_json("system", "prompt", planner.PLAN_SCHEMA)

    url, body = sent[0]
    config = body["generationConfig"]

    assert "additionalProperties" not in json.dumps(config["responseSchema"])
    assert config["responseMimeType"] == "application/json"
    assert body["systemInstruction"]["parts"][0]["text"] == "system"
    assert gemini_client.model_name() in url


def test_gemini_downgrades_to_json_mode_on_a_refused_schema(monkeypatch):
    from regression.ai_engine import gemini_client, planner

    sent = _gemini(monkeypatch, [
        _http_error(400, b'{"error":{"message":"invalid responseSchema"}}'),
        _gemini_payload(json.dumps(
            {"intensity": "low", "scenarios": [], "reasoning": "quiet"})),
    ])

    data = gemini_client.complete_json(
        "system", "prompt", planner.PLAN_SCHEMA)

    assert data["intensity"] == "low"
    assert len(sent) == 2

    _, retry = sent[1]

    assert "responseSchema" not in retry["generationConfig"]
    assert "JSON Schema" in retry["systemInstruction"]["parts"][0]["text"]


def test_gemini_does_not_retry_a_refusal_or_a_rate_limit(monkeypatch):
    from regression.ai_engine import gemini_client, planner

    for code in (403, 429):
        sent = _gemini(monkeypatch, [_http_error(code, b'{"error":{}}')])

        with pytest.raises(RuntimeError) as caught:
            gemini_client.complete_json(
                "system", "prompt", planner.PLAN_SCHEMA)

        assert str(code) in str(caught.value)
        assert len(sent) == 1, "a {} was retried".format(code)


def test_gemini_reports_a_blocked_prompt(monkeypatch):
    from regression.ai_engine import gemini_client, planner

    _gemini(monkeypatch, [{"promptFeedback": {"blockReason": "SAFETY"}}])

    with pytest.raises(RuntimeError) as caught:
        gemini_client.complete_json("system", "prompt", planner.PLAN_SCHEMA)

    assert "blocked" in str(caught.value)
    assert "SAFETY" in str(caught.value)


def test_gemini_reports_a_truncated_reply(monkeypatch):
    from regression.ai_engine import gemini_client, planner

    _gemini(monkeypatch, [_gemini_payload('{"intensity"', finish="MAX_TOKENS")])

    with pytest.raises(RuntimeError) as caught:
        gemini_client.complete_json("system", "prompt", planner.PLAN_SCHEMA)

    assert "token limit" in str(caught.value)


def test_a_gemini_plan_is_clamped_like_any_other(monkeypatch):
    """Dropping additionalProperties is safe because nothing trusts extra keys."""
    from regression.ai_engine import engines, planner

    _gemini(monkeypatch, [_gemini_payload(json.dumps(
        {"intensity": "medium",
         "scenarios": ["power_stress", "not_a_scenario"],
         "reasoning": "invented one",
         "extra_field": "ignored"}))])

    plan = planner.plan_with_llm(
        {"memory": 2.0, "unmeasured": []}, "power",
        engine=engines.get_engine("gemini"))

    assert plan.scenarios == ["power_stress"]
    assert not hasattr(plan, "extra_field")


def test_a_built_client_is_not_proof_of_a_credential(monkeypatch):
    """Anthropic() builds happily with nothing and complains at call time.

    What settles it is whether the client resolved a credential.
    """
    # The assertions below are about which credential availability()
    # found, a question it only reaches once the SDK is importable. CI
    # installs requirements.txt alone, so without this the host job would
    # fail on the package message rather than skipping.
    pytest.importorskip("anthropic")

    from regression.ai_engine import llm_client

    class _Empty:
        api_key = None
        auth_token = None

    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_AUTH_TOKEN", raising=False)
    monkeypatch.setattr(llm_client, "_client", lambda: _Empty())

    ok, reason = llm_client.availability()

    assert ok is False, "a client with no credential was reported ready"
    assert "ANTHROPIC_API_KEY" in reason

    # A client that did resolve one is available, env var or not.
    class _Resolved(_Empty):
        api_key = "sk-ant-from-a-profile"

    monkeypatch.setattr(llm_client, "_client", lambda: _Resolved())

    assert llm_client.availability() == (True, "ready")


def test_gemini_retries_a_busy_model_but_not_a_quota(monkeypatch):
    """503 says "try again later"; 429 says the allowance is spent."""
    from regression.ai_engine import gemini_client, planner

    monkeypatch.setattr(gemini_client.time, "sleep", lambda _s: None)

    # Busy twice, then answers: the run should get its plan.
    sent = _gemini(monkeypatch, [
        _http_error(503, b'{"error":{"message":"high demand"}}'),
        _http_error(503, b'{"error":{"message":"high demand"}}'),
        _gemini_payload(json.dumps(
            {"intensity": "low", "scenarios": [], "reasoning": "quiet"})),
    ])

    data = gemini_client.complete_json(
        "system", "prompt", planner.PLAN_SCHEMA)

    assert data["intensity"] == "low"
    assert len(sent) == 3, "it gave up before the model recovered"

    # Busy every time: it gives up rather than holding the run open.
    sent = _gemini(monkeypatch, [
        _http_error(503, b"{}") for _ in range(gemini_client.BUSY_ATTEMPTS)])

    with pytest.raises(RuntimeError) as caught:
        gemini_client.complete_json("system", "prompt", planner.PLAN_SCHEMA)

    assert "503" in str(caught.value)
    assert len(sent) == gemini_client.BUSY_ATTEMPTS, "it retried past the cap"

    # A quota is not retried at all.
    sent = _gemini(monkeypatch, [_http_error(429, b'{"error":{}}')])

    with pytest.raises(RuntimeError):
        gemini_client.complete_json("system", "prompt", planner.PLAN_SCHEMA)

    assert len(sent) == 1, "a 429 was retried; asking again makes no quota"
