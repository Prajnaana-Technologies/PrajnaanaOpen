# Copyright (c) 2026 Prajnaana Technologies Pvt. Ltd.
# Original author: Dhanya Shree S
# SPDX-License-Identifier: MIT
#
# Licensed under the MIT License. See LICENSE in the project root.

"""The scenario vocabulary, the test catalogue, the engine registry and the
power analyzer.

The contract that matters most here is the first one: a scenario the planner
can choose must emit a test. A scenario that generates nothing lets a plan
claim coverage the run never delivered.
"""

import ast
import inspect
import re

import pytest

from regression import catalog
from regression.ai_engine import engines
from regression.ai_engine.planner import KNOWN_SCENARIOS
from regression.ai_engine.scenario_tests import SCENARIO_TESTS
from regression.instrumentation import power_analyzer


# --------------------------------------------------------------------------
# Every scenario must actually generate something
# --------------------------------------------------------------------------

def test_every_known_scenario_emits_a_test():
    """A scenario that emits nothing lets a plan overstate its coverage."""
    missing = [name for name in KNOWN_SCENARIOS if name not in SCENARIO_TESTS]

    assert missing == [], (
        "these scenarios can be planned but generate no test: {}".format(missing)
    )


def test_no_orphan_scenario_templates():
    """A template the planner can never choose is dead code."""
    orphans = [name for name in SCENARIO_TESTS if name not in KNOWN_SCENARIOS]

    assert orphans == []


@pytest.mark.parametrize("name", sorted(SCENARIO_TESTS))
def test_each_scenario_template_is_valid_python(name):
    ast.parse(SCENARIO_TESTS[name])


@pytest.mark.parametrize("name", sorted(SCENARIO_TESTS))
def test_each_scenario_defines_at_least_one_test(name):
    tree = ast.parse(SCENARIO_TESTS[name])

    tests = [
        node for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name.startswith("test_")
    ]

    assert tests, "{} emits no test function".format(name)


def test_no_two_scenarios_emit_the_same_test_name():
    """Two scenarios in one plan would collide, and pytest runs only the last."""
    seen = {}

    for scenario, source in sorted(SCENARIO_TESTS.items()):
        for node in ast.parse(source).body:
            if not (isinstance(node, ast.FunctionDef)
                    and node.name.startswith("test_")):
                continue

            assert node.name not in seen, (
                "{} and {} both emit {}".format(
                    seen.get(node.name), scenario, node.name)
            )

            seen[node.name] = scenario


@pytest.mark.parametrize("name", sorted(SCENARIO_TESTS))
def test_each_scenario_declares_a_category(name):
    """Untagged tests count as functional, so every scenario declares one."""
    source = SCENARIO_TESTS[name]

    assert "@pytest.mark.category(" in source

    tagged = [
        value for value in catalog.CATEGORIES
        if '"{}"'.format(value) in source
    ]

    assert tagged, "no known category tagged in {}".format(name)


@pytest.mark.parametrize("name", sorted(SCENARIO_TESTS))
def test_each_scenario_passes_the_acceptance_rules(name):
    """Generated code has to satisfy the same rules it is checked against."""
    from regression import governance

    source = "import pytest\nimport asyncio\nimport os\nimport time\n"

    violations = governance.check_source(source + SCENARIO_TESTS[name])

    assert violations == [], [str(v) for v in violations]


def test_the_hard_categories_are_covered_by_scenarios():
    """The categories that find the expensive bugs must each have a scenario.

    Five categories sit beside functional (catalog.CATEGORIES), and all five
    are required here, security included. No other test asserts it.
    """
    covered = set()

    for source in SCENARIO_TESTS.values():
        for value in catalog.CATEGORIES:
            if '"{}"'.format(value) in source:
                covered.add(value)

    for required in (catalog.STRESS, catalog.FAULT_INJECTION,
                     catalog.LONG_DURATION, catalog.CONCURRENCY,
                     catalog.SECURITY):
        assert required in covered, "no scenario produces a {} test".format(
            required)


# --------------------------------------------------------------------------
# Catalogue
# --------------------------------------------------------------------------

def test_the_split_is_a_measurement_not_a_constant():
    entries = [
        catalog.Entry("test_a", catalog.FUNCTIONAL, catalog.AUTOMATED),
        catalog.Entry("test_b", catalog.FUNCTIONAL, catalog.AUTOMATED),
        catalog.Entry("manual_a", catalog.FUNCTIONAL, catalog.TARGETED_MANUAL),
        catalog.Entry("ear_a", catalog.FUNCTIONAL, catalog.SUBJECTIVE),
    ]

    split = catalog.automation_split(entries)

    assert split[catalog.AUTOMATED] == 50.0
    assert split[catalog.TARGETED_MANUAL] == 25.0
    assert split[catalog.SUBJECTIVE] == 25.0


def test_the_split_sums_to_a_hundred():
    split = catalog.automation_split()

    assert abs(sum(split.values()) - 100.0) < 0.5


def test_drift_is_measured_against_the_plan():
    entries = [catalog.Entry("test_a", catalog.FUNCTIONAL, catalog.AUTOMATED)]

    drift = catalog.drift_from_target(entries)

    assert drift[catalog.AUTOMATED] == 35.0
    assert drift[catalog.SUBJECTIVE] == -10.0


def test_an_empty_inventory_does_not_divide_by_zero():
    assert catalog.automation_split([]) == {
        name: 0.0 for name in catalog.AUTOMATION_CLASSES
    }


def test_uncovered_categories_are_named():
    entries = [catalog.Entry("test_a", catalog.FUNCTIONAL, catalog.AUTOMATED)]

    missing = catalog.uncovered_categories(entries)

    assert catalog.CONCURRENCY in missing
    assert catalog.FUNCTIONAL not in missing


def test_an_unknown_category_is_rejected():
    with pytest.raises(ValueError, match="category"):
        catalog.Entry("test_a", "vibes", catalog.AUTOMATED)


def test_an_unknown_automation_class_is_rejected():
    with pytest.raises(ValueError, match="automation"):
        catalog.Entry("test_a", catalog.FUNCTIONAL, "somebody_else")


def test_discovery_reads_the_category_mark(tmp_path):
    module = tmp_path / "test_sample.py"
    module.write_text(
        "import pytest\n\n"
        '@pytest.mark.category("stress")\n'
        "def test_hard():\n    assert True\n\n"
        "def test_plain():\n    assert True\n",
        encoding="utf-8",
    )

    found = catalog.discover(roots=["."], base=str(tmp_path))

    by_name = {entry.name: entry.category for entry in found}

    assert by_name["test_hard"] == catalog.STRESS
    assert by_name["test_plain"] == catalog.FUNCTIONAL


def test_a_generated_suite_covers_every_category(tmp_path):
    """Every category in the catalogue appears in a generated suite.

    Written against a suite generated into a temp directory rather than
    against whatever happens to be on disk: a test that depends on a
    generated artefact passes or fails according to what the last run left
    behind, which is not a property of the code.
    """
    module = tmp_path / "test_generated.py"

    body = "import pytest" + chr(10) + chr(10)

    for source in SCENARIO_TESTS.values():
        body += source

    module.write_text(body, encoding="utf-8")

    found = catalog.discover(roots=["."], base=str(tmp_path))

    assert catalog.uncovered_categories(found + list(catalog.MANUAL_TESTS)) == []


# --------------------------------------------------------------------------
# Engine registry
# --------------------------------------------------------------------------

def test_the_default_engine_is_implemented():
    assert engines.DEFAULT_ENGINE in engines.available_engines()


def test_a_named_but_unwritten_engine_is_refused_clearly():
    """'Not implemented' and 'unknown' are different problems."""
    with pytest.raises(engines.EngineNotAvailable, match="no adapter"):
        engines.get_engine("openai")


def test_an_unknown_engine_names_what_exists():
    with pytest.raises(engines.EngineNotAvailable, match="unknown engine"):
        engines.get_engine("telepathy")


def test_no_engine_is_registered_without_an_adapter():
    """Registering an unwritten adapter turns a known gap into a crash."""
    overlap = set(engines.ENGINES) & set(engines.NOT_IMPLEMENTED)

    assert overlap == set()


def test_the_policy_honours_the_environment(monkeypatch):
    monkeypatch.setenv(engines.ENGINE_ENV, "anthropic")

    assert engines.policy_name() == "anthropic"


def test_the_adapter_satisfies_the_interface():
    engine = engines.get_engine("anthropic")

    for method in ("available", "complete_json", "model_name"):
        assert callable(getattr(engine, method))


def test_availability_never_raises(monkeypatch):
    """The planner falls back on False; an exception would kill the run."""
    from regression.ai_engine import llm_client

    def explode():
        raise RuntimeError("provider down")

    monkeypatch.setattr(llm_client, "availability", explode)

    ok, reason = engines.get_engine("anthropic").available()

    assert ok is False
    assert "provider down" in reason


def test_describe_all_lists_the_unimplemented_too():
    body = " ".join(engines.describe_all())

    assert "anthropic" in body

    for name in engines.NOT_IMPLEMENTED:
        assert name in body


# --------------------------------------------------------------------------
# Power analyzer
# --------------------------------------------------------------------------

def test_no_instrument_means_no_reading(monkeypatch):
    """None is the correct answer, not a plausible default."""
    monkeypatch.delenv(power_analyzer.POWER_MA_ENV, raising=False)
    monkeypatch.delenv(power_analyzer.POWER_CSV_ENV, raising=False)

    assert power_analyzer.read_power_ma() is None


def test_a_fixed_value_is_used(monkeypatch):
    monkeypatch.setenv(power_analyzer.POWER_MA_ENV, "41.5")

    assert power_analyzer.read_power_ma() == 41.5


def test_a_non_numeric_value_is_rejected(monkeypatch):
    monkeypatch.setenv(power_analyzer.POWER_MA_ENV, "quite a lot")

    with pytest.raises(power_analyzer.PowerReadError):
        power_analyzer.read_power_ma()


def test_a_capture_is_averaged(tmp_path, monkeypatch):
    capture = tmp_path / "ppk2.csv"
    capture.write_text(
        "Timestamp(ms),Current(uA)\n0,40000\n1,42000\n2,38000\n",
        encoding="utf-8",
    )

    monkeypatch.delenv(power_analyzer.POWER_MA_ENV, raising=False)
    monkeypatch.setenv(power_analyzer.POWER_CSV_ENV, str(capture))

    assert power_analyzer.read_power_ma() == 40.0


def test_the_noise_floor_is_excluded(tmp_path, monkeypatch):
    """Sub-microamp samples are the instrument, not the device."""
    capture = tmp_path / "ppk2.csv"
    capture.write_text(
        "Timestamp(ms),Current(uA)\n0,40000\n1,0.2\n2,40000\n",
        encoding="utf-8",
    )

    monkeypatch.delenv(power_analyzer.POWER_MA_ENV, raising=False)
    monkeypatch.setenv(power_analyzer.POWER_CSV_ENV, str(capture))

    assert power_analyzer.read_power_ma() == 40.0


def test_a_capture_without_a_current_column_is_rejected(tmp_path, monkeypatch):
    capture = tmp_path / "ppk2.csv"
    capture.write_text("Timestamp(ms),Voltage(V)\n0,3.0\n", encoding="utf-8")

    monkeypatch.delenv(power_analyzer.POWER_MA_ENV, raising=False)
    monkeypatch.setenv(power_analyzer.POWER_CSV_ENV, str(capture))

    with pytest.raises(power_analyzer.PowerReadError, match="current column"):
        power_analyzer.read_power_ma()


def test_a_missing_capture_is_rejected(monkeypatch):
    monkeypatch.delenv(power_analyzer.POWER_MA_ENV, raising=False)
    monkeypatch.setenv(power_analyzer.POWER_CSV_ENV, "no/such/file.csv")

    with pytest.raises(power_analyzer.PowerReadError, match="no such capture"):
        power_analyzer.read_power_ma()


def test_merging_a_reading_clears_the_unmeasured_flag(monkeypatch):
    monkeypatch.setenv(power_analyzer.POWER_MA_ENV, "39.0")

    metrics = {"unmeasured": ["power", "sync"]}

    power_analyzer.merge_into(metrics)

    assert metrics["power"] == 39.0
    assert metrics["unmeasured"] == ["sync"]


def test_merging_without_an_instrument_changes_nothing(monkeypatch):
    monkeypatch.delenv(power_analyzer.POWER_MA_ENV, raising=False)
    monkeypatch.delenv(power_analyzer.POWER_CSV_ENV, raising=False)

    metrics = {"unmeasured": ["power"]}

    power_analyzer.merge_into(metrics)

    assert "power" not in metrics
    assert metrics["unmeasured"] == ["power"]


# --------------------------------------------------------------------------
# Every scenario must be reachable by the DEFAULT planner
# --------------------------------------------------------------------------

# A scenario the rules planner can never select is unreachable in practice:
# rules is the default and the LLM path is opt-in. Being in the vocabulary
# and generating real tests is not enough -- pairing_bonding, fault_link_drop
# and concurrency_stream_and_read are checked here because a scenario nothing
# selects never runs.
REACHABILITY_CASES = (
    ("pairing and bonding rework", {}, [], 0),
    ("bluetooth reconnection fix", {}, ["connection"], 3),
    ("audio scheduling change", {}, ["sync"], 2),
    ("memory leak fix", {}, ["memory_leak"], 2),
    ("buffer overflow fix", {}, ["stability"], 4),
    ("power regression", {"power": 90}, [], 2),
    ("long soak investigation", {}, [], 0),
    ("throughput tuning", {}, [], 0),
)


def _reachable_scenarios():
    from regression.ai_engine.planner import suggest_scenarios

    reached = set()

    for change, metrics, risk_tests, score in REACHABILITY_CASES:
        reached |= set(suggest_scenarios(change, metrics, risk_tests, score))

    return reached


def test_every_scenario_is_reachable_without_the_llm():
    unreachable = sorted(set(KNOWN_SCENARIOS) - _reachable_scenarios())

    assert unreachable == [], (
        "the default planner can never select: {}. A scenario nothing selects "
        "has never run, however good its test is.".format(unreachable)
    )


def test_a_pairing_change_selects_the_pairing_scenario():
    from regression.ai_engine.planner import suggest_scenarios

    for change in ("pairing rework", "bonding storage fix",
                   "encrypt the write characteristic", "SMP enabled"):
        assert "pairing_bonding" in suggest_scenarios(change, {}, [], 0), change


def test_an_unrelated_change_does_not_select_pairing():
    """Triggering on everything is the same as triggering on nothing."""
    from regression.ai_engine.planner import suggest_scenarios

    assert "pairing_bonding" not in suggest_scenarios(
        "readme typo", {"memory": 3.0, "power": 40}, [], 0)


def test_every_trigger_names_a_real_scenario():
    from regression.ai_engine.planner import CHANGE_TRIGGERS

    unknown = sorted(set(CHANGE_TRIGGERS) - set(KNOWN_SCENARIOS))

    assert unknown == []


# --------------------------------------------------------------------------
# The change comes from the repository, not from an operator's typing
# --------------------------------------------------------------------------

def test_paths_map_to_subsystems():
    from regression.change_detection import git_changes

    found = git_changes.subsystems_for([
        "src/NRF_Firmware/src/ble_telemetry_service.c",
        "src/regression/ai_engine/planner.py",
    ])

    assert "bluetooth telemetry" in found
    assert "test planning (host)" in found


def test_host_changes_do_not_reach_the_planner():
    """A change to the test generator cannot alter how the board behaves."""
    from regression.change_detection import git_changes

    paths = [
        "src/regression/ai_engine/planner.py",
        "src/regression/governance.py",
    ]

    assert git_changes.subsystems_for(paths)          # they are recognised
    assert git_changes.device_subsystems(paths) == []  # but not device changes


def test_a_firmware_change_does_reach_the_planner():
    from regression.change_detection import git_changes

    device = git_changes.device_subsystems(
        ["src/NRF_Firmware/src/dsp_wdrc.c"])

    assert device == ["audio dsp"]


def test_noise_paths_are_ignored():
    """Build output and logs change constantly and say nothing about firmware."""
    from regression.change_detection import git_changes

    noisy = [
        "bin/_internal/base_library.zip",
        "src/regression/logs/build_1/tests/test_x.log",
        "src/NRF_Firmware/firmware.hex",
        "src/regression/__pycache__/x.pyc",
    ]

    assert git_changes.subsystems_for(noisy) == []

    for path in noisy:
        assert not git_changes._interesting(path), path


def test_a_real_source_path_is_interesting():
    from regression.change_detection import git_changes

    assert git_changes._interesting("src/regression/ai_engine/planner.py")


def test_every_subsystem_word_can_reach_a_scenario():
    """A subsystem mapping to words no trigger recognises selects nothing."""
    from regression.ai_engine.planner import suggest_scenarios
    from regression.change_detection import git_changes

    silent = []

    for _, subsystem, affects_device in git_changes.PATH_SUBSYSTEMS:
        # Host-side changes legitimately select no device scenario: the host
        # suite covers them, and pulling in hardware stress tests because the
        # test generator changed would be selection by superstition.
        if not affects_device:
            continue

        if not suggest_scenarios(subsystem, {}, [], 0):
            silent.append(subsystem)

    assert silent == [], (
        "these subsystem words select no scenario: {}. A change mapped to one "
        "of them would be tested as though nothing changed.".format(silent)
    )


def test_an_unknown_change_is_named_not_blank():
    from regression.change_detection import git_changes

    description, detail = git_changes.describe(cwd="/definitely/not/a/repo")

    assert description == git_changes.UNKNOWN_CHANGE
    assert detail


# --------------------------------------------------------------------------
# Firmware-to-firmware comparison
# --------------------------------------------------------------------------

def test_a_resized_function_is_detected():
    """The whole point: a function that changed size is a function that changed."""
    from regression.change_detection import firmware_diff

    before = {"wdrc_process": 288, "bt_conn_set_state": 240}
    after = {"wdrc_process": 304, "bt_conn_set_state": 240}

    assert firmware_diff.changed_symbols(before, after) == ["wdrc_process"]


def test_added_and_removed_functions_are_detected():
    from regression.change_detection import firmware_diff

    difference = firmware_diff.compare(
        {"old_only": 32, "shared": 64},
        {"new_only": 48, "shared": 64},
    )

    assert difference["added"] == ["new_only"]
    assert difference["removed"] == ["old_only"]
    assert difference["resized"] == []


def test_matching_symbols_do_not_claim_an_identical_image():
    """Symbols are names and sizes; a changed constant moves neither."""
    from regression.change_detection import firmware_diff

    symbols = {"wdrc_process": 288}

    description, detail = firmware_diff.describe(symbols, dict(symbols))

    assert description is None
    assert "identical" not in detail
    assert "no symbol was added, removed or resized" in detail


def test_the_image_hash_decides_identity(monkeypatch):
    """Same symbols, different bytes: a value inside the code changed."""
    from regression.change_detection import firmware_diff

    symbols = {"wdrc_process": 288}
    earlier = firmware_diff.record(symbols, build_id="fw-1", sha="aaaa")

    monkeypatch.setattr(firmware_diff, "fingerprint", lambda *a, **k: dict(symbols))
    monkeypatch.setattr(firmware_diff, "save", lambda *a, **k: "")
    monkeypatch.setattr(firmware_diff, "previous_build_id", lambda current: "fw-1")
    monkeypatch.setattr(firmware_diff, "load", lambda build: earlier)

    # Same bytes: the claim is supported, and the hash is what supports it.
    monkeypatch.setattr(firmware_diff, "image_hash", lambda *a, **k: "aaaa")

    description, detail = firmware_diff.describe_against_last_tested("fw-2")

    assert description is None and "byte-identical" in detail

    # Different bytes, identical symbols: a constant moved -- neither "no
    # change" nor an answer. Returning the words "unknown change" would give
    # the orchestrator a description it takes as the answer, so the source
    # tree would never get its turn. No description means it falls through to
    # git, the way git's own "unknown change" does.
    monkeypatch.setattr(firmware_diff, "image_hash", lambda *a, **k: "bbbb")

    description, detail = firmware_diff.describe_against_last_tested("fw-2")

    assert description is None
    assert "no symbol moved, but the image differs" in detail


def test_an_old_fingerprint_cannot_confirm_unchanged(monkeypatch):
    # Fingerprints written before hashes existed are a bare {symbol: size}
    # map. They still load, and they say what they cannot answer.
    #
    # One outcome, asserted exactly: a bare map carries no build id either,
    # so nothing supports "a value changed" and nothing supports
    # "unchanged". The detail line has to say the second out loud, because
    # silence after a comparison reads as confirmation. Accepting either
    # outcome would pass whichever branch ran.
    from regression.change_detection import firmware_diff

    symbols = {"wdrc_process": 288}

    monkeypatch.setattr(firmware_diff, "fingerprint", lambda *a, **k: dict(symbols))
    monkeypatch.setattr(firmware_diff, "save", lambda *a, **k: "")
    monkeypatch.setattr(firmware_diff, "image_hash", lambda *a, **k: "bbbb")
    monkeypatch.setattr(firmware_diff, "previous_build_id", lambda current: "fw-1")
    monkeypatch.setattr(firmware_diff, "load", lambda build: dict(symbols))

    description, detail = firmware_diff.describe_against_last_tested("fw-2")

    assert description is None
    assert "no image hash stored for" in detail
    assert "'unchanged' is not confirmed" in detail


def test_the_hash_is_taken_over_the_loadable_image(tmp_path):
    """The bin the board runs, not the ELF.

    The ELF carries debug information, including the absolute path of every
    source file, and no ELF is shipped.
    """
    import hashlib

    from regression.change_detection import firmware_diff

    elf = tmp_path / "zephyr.elf"
    image = tmp_path / "zephyr.bin"

    elf.write_bytes(b"ELF with /home/someone/src baked into it")
    image.write_bytes(b"loadable bytes")

    assert firmware_diff.image_hash(str(elf)) == hashlib.sha256(
        b"loadable bytes").hexdigest()

    # An ELF with no image beside it hashes nothing. Falling back to the
    # next image on the list would pair one build's symbols with another
    # build's bytes and report a change on a pair nobody compared.
    lonely = tmp_path / "other" / "zephyr.elf"
    lonely.parent.mkdir()
    lonely.write_bytes(b"ELF with no bin beside it")

    assert firmware_diff.find_image(str(lonely)) is None
    assert firmware_diff.image_hash(str(lonely)) == ""


def test_the_shipped_baseline_hashes_the_shipped_image():
    """baseline.json must name an image the recipient actually has.

    The release does not ship zephyr.elf, so the bundled fingerprint is
    taken over the image it does ship.
    """
    import hashlib
    import json
    import os

    from regression.change_detection import firmware_diff
    from regression.paths import BASE_DIR, asset

    baseline_path = asset(*firmware_diff.BASELINE_PARTS)
    image_path = os.path.join(BASE_DIR, "NRF_Firmware", "firmware.bin")

    if not (os.path.exists(baseline_path) and os.path.exists(image_path)):
        pytest.skip("no shipped baseline or no shipped image in this tree")

    with open(baseline_path, encoding="utf-8") as handle:
        _build_id, sha = firmware_diff.identity_of(json.load(handle))

    with open(image_path, "rb") as handle:
        assert sha == hashlib.sha256(handle.read()).hexdigest()


def test_symbols_map_to_subsystems():
    from regression.change_detection import firmware_diff

    assert firmware_diff.subsystems_for_symbols(["wdrc_process"]) == ["audio dsp"]
    assert firmware_diff.subsystems_for_symbols(
        ["ha_telemetry_init"]) == ["bluetooth telemetry"]
    assert firmware_diff.subsystems_for_symbols(
        ["smp_pairing_req"]) == ["pairing security"]


# --------------------------------------------------------------------------
# Firmware comparison in a packaged build
# --------------------------------------------------------------------------

def _no_history(monkeypatch, current, baseline):
    """A packaged build: an image to read, but no recorded run history."""
    from regression.change_detection import firmware_diff

    monkeypatch.setattr(firmware_diff, "fingerprint", lambda *a, **k: current)
    monkeypatch.setattr(firmware_diff, "save", lambda *a, **k: None)
    monkeypatch.setattr(firmware_diff, "previous_build_id", lambda _c: None)
    monkeypatch.setattr(firmware_diff, "load_baseline", lambda: baseline)

    return firmware_diff


def test_the_shipped_baseline_gives_a_first_run_something_to_compare(monkeypatch):
    """A packaged first run has a bundled baseline to compare against, so
    it names a subsystem rather than reporting the change as unknown."""
    firmware_diff = _no_history(
        monkeypatch,
        current={"wdrc_process": 304, "main": 100},
        baseline={"wdrc_process": 288, "main": 100},
    )

    description, detail = firmware_diff.describe_against_last_tested("build-1")

    assert description == "audio dsp"
    assert firmware_diff.BASELINE_ID in detail


def test_no_bundled_baseline_still_says_so(monkeypatch):
    firmware_diff = _no_history(
        monkeypatch, current={"wdrc_process": 304}, baseline=None)

    description, detail = firmware_diff.describe_against_last_tested("build-1")

    assert description is None
    assert "nothing earlier" in detail


def test_a_missing_image_and_a_missing_toolchain_read_differently(monkeypatch):
    """A missing image and a missing toolchain get different messages."""
    from regression.change_detection import firmware_diff

    monkeypatch.setattr(firmware_diff, "find_elf", lambda: None)
    monkeypatch.setattr(firmware_diff, "find_nm", lambda: None)
    assert "no built image" in firmware_diff.unavailable_reason()

    monkeypatch.setattr(firmware_diff, "find_elf", lambda: "zephyr.elf")
    assert "nm" in firmware_diff.unavailable_reason()


def test_a_dsp_change_describes_itself_as_audio_dsp():
    """End to end over the mapping: symbols in, planner vocabulary out."""
    from regression.ai_engine.planner import suggest_scenarios
    from regression.change_detection import firmware_diff

    description, _ = firmware_diff.describe(
        {"wdrc_process": 288}, {"wdrc_process": 304})

    assert description == "audio dsp"
    assert suggest_scenarios(description, {}, [], 0)


def test_every_symbol_subsystem_reaches_a_scenario():
    """A subsystem naming words no trigger knows would select nothing."""
    from regression.ai_engine.planner import suggest_scenarios
    from regression.change_detection import firmware_diff

    silent = [
        subsystem for _, subsystem in firmware_diff.SYMBOL_SUBSYSTEMS
        if not suggest_scenarios(subsystem, {}, [], 0)
    ]

    assert silent == []


def test_a_missing_baseline_is_unknown_not_no_change():
    """Nothing to compare against must never read as 'nothing changed'."""
    from regression.change_detection import firmware_diff

    description, detail = firmware_diff.describe(None, {"wdrc_process": 288})

    assert description is None
    assert "no previous" in detail


def test_the_memory_pressure_limits_are_inside_the_metric_range():
    """The limit has to be reachable, or the test cannot fail.

    memory is work-queue stack high-water in a 4 KiB stack
    (risk_engine.WORKQUEUE_STACK_KB), so every limit here stays inside that.
    """
    from regression.ai_engine import generate_tests
    from regression.change_detection.risk_engine import (
        METRIC_THRESHOLDS, WORKQUEUE_STACK_KB)

    source = generate_tests.SCENARIO_TESTS["memory_pressure"]

    assert "MAX_HEAP_GROWTH_KB" not in source
    assert "MAX_STACK_GROWTH_KB" in source and "MAX_STACK_KB" in source

    # The absolute ceiling sits inside what the device can report.
    assert 0 < METRIC_THRESHOLDS["memory"] < WORKQUEUE_STACK_KB

    # And so does the growth limit, which is the half a threshold check
    # alone would miss: a growth limit of 8.0 KiB against a 4 KiB stack can
    # never be reached, so the test built on it can never fail. It is a
    # literal in the generated header rather than an entry in
    # METRIC_THRESHOLDS, so it is read back out of the module source.
    growth = re.search(r"^MAX_STACK_GROWTH_KB = ([0-9.]+)$",
                       inspect.getsource(generate_tests), re.MULTILINE)

    assert growth, "MAX_STACK_GROWTH_KB is no longer written as a literal"
    assert 0 < float(growth.group(1)) < WORKQUEUE_STACK_KB


class _FakeLoop(object):
    """The generated tests drive async calls through a loop object.

    Nothing here is async, so run_until_complete hands back whatever it was
    given.
    """

    @staticmethod
    def run_until_complete(value):
        return value


class _FakeDevice(object):
    """A board that reports the stack readings it was built with, in order."""

    def __init__(self, readings):
        self._readings = list(readings)

    def read_metrics(self):
        reading = self._readings.pop(0)

        return {} if reading is None else {"memory": reading}


def _emitted_constant(name):
    """A limit as the generator writes it into the module it emits.

    Read out of generate_tests rather than restated here: a test carrying
    its own copy of the number passes whatever the emitted code does with it.
    """
    import inspect
    import re

    from regression.ai_engine import generate_tests

    found = re.search(r"^{} = ([0-9.]+)$".format(name),
                      inspect.getsource(generate_tests), re.M)

    assert found, "generate_tests no longer emits {}".format(name)

    return float(found.group(1))


def _run_emitted_memory_test(readings, streams=3):
    """Execute the emitted memory test against a fake device.

    Runs the real assertions from scenario_tests.MEMORY_PRESSURE, with the
    real limits, and returns how it ended: "passed", "failed" or "skipped".
    """
    from regression.ai_engine.scenario_tests import MEMORY_PRESSURE
    from regression.change_detection.risk_engine import (
        METRIC_THRESHOLDS, WORKQUEUE_STACK_KB)

    namespace = {
        "pytest": pytest,
        "get_loop": _FakeLoop,
        "require_loopback": lambda device, loop: None,
        "stream": lambda device, loop, audio: None,
        "tone": lambda amplitude: None,
        "STRESS_STREAMS": streams,
        "WORKQUEUE_STACK_KB": WORKQUEUE_STACK_KB,
        "MAX_STACK_KB": METRIC_THRESHOLDS["memory"],
        "MAX_STACK_GROWTH_KB": _emitted_constant("MAX_STACK_GROWTH_KB"),
    }

    exec(MEMORY_PRESSURE, namespace)

    # pytest.skip and pytest.fail raise outcomes that do not derive from
    # Exception, so they have to be named rather than caught by a bare
    # "except Exception" -- which would let a skip escape and skip this test
    # instead of being reported as one.
    try:
        namespace["test_memory_under_repeated_streams"](_FakeDevice(readings))
    except pytest.skip.Exception:
        return "skipped"
    except (AssertionError, pytest.fail.Exception):
        return "failed"

    return "passed"


def test_the_generated_memory_test_can_fail():
    """Run the emitted assertions, do not restate them.

    3.53 KiB is the flat high-water the bench calibration recorded, and
    3.90 KiB is that plateau plus more than the growth the limit allows.
    """
    from regression.change_detection.risk_engine import (
        METRIC_THRESHOLDS, WORKQUEUE_STACK_KB)

    # Steady: the plateau twice over.
    assert _run_emitted_memory_test([3.53, 3.53]) == "passed"

    # Leaking: growth past MAX_STACK_GROWTH_KB, and past the ceiling too.
    assert _run_emitted_memory_test([3.53, 3.90]) == "failed"

    # One case per assertion, because a reading that breaks both is caught
    # by whichever survives a regression. Growth only: 0.30 KiB of climb,
    # still short of the 3.76 KiB ceiling.
    assert _run_emitted_memory_test([3.20, 3.50]) == "failed"

    # Ceiling only: 0.10 KiB of climb, which the growth limit allows, and
    # the ceiling crossed all the same.
    assert _run_emitted_memory_test([3.70, 3.80]) == "failed"

    # A build that does not report the metric declines rather than passing.
    assert _run_emitted_memory_test([None, None]) == "skipped"

    # Reported before and not after is the device breaking mid-test, which
    # is a failure and not a skip.
    assert _run_emitted_memory_test([3.53, None]) == "failed"

    # Both limits have to sit inside what a 4 KiB stack can report, or
    # neither assertion can fire.
    assert 0 < METRIC_THRESHOLDS["memory"] < WORKQUEUE_STACK_KB
    assert WORKQUEUE_STACK_KB == 4.0


def test_every_source_file_carries_the_licence_header():
    """README and CREDITS both promise this, so it has to be true."""
    import os

    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

    missing = []

    for directory, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames
                       if d not in ("__pycache__", "build", "dist",
                                    ".pytest_cache", "NRF_Firmware")]

        for name in filenames:
            if not name.endswith(".py"):
                continue

            path = os.path.join(directory, name)

            # Generated modules carry the header from their own template.
            if "generated_tests" in path and name.startswith("test_"):
                continue

            with open(path, encoding="utf-8", errors="replace") as handle:
                if "SPDX-License-Identifier" not in handle.read(600):
                    missing.append(os.path.relpath(path, root))

    assert missing == [], missing
