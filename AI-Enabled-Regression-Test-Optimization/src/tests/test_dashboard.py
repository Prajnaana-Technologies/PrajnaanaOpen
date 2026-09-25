# Copyright (c) 2026 Prajnaana Technologies Pvt. Ltd.
# Original author: Dhanya Shree S
# SPDX-License-Identifier: MIT
#
# Licensed under the MIT License. See LICENSE in the project root.

"""Tests for the Tkinter dashboard.

Skipped automatically where there is no display (headless CI), so they never
turn CI red on a machine that simply cannot open a window.
"""

import pytest

tk = pytest.importorskip("tkinter")

from regression.dashboard import PATTERNS, SLICE_LABEL, Dashboard  # noqa: E402


@pytest.fixture(scope="session")
def tk_root():
    """One Tk interpreter for the whole session.

    Creating and destroying Tk() repeatedly is unreliable on Windows -- the
    third instance tends to fail -- so share one and clear its children
    between tests instead.
    """
    try:
        window = tk.Tk()
    except tk.TclError:
        pytest.skip("no display available")

    window.withdraw()
    yield window
    window.destroy()


@pytest.fixture
def root(tk_root):
    yield tk_root

    for child in tk_root.winfo_children():
        child.destroy()


# --------------------------------------------------------------------------
# Output parsing -- the summary row depends on these matching the orchestrator
# --------------------------------------------------------------------------

ORCHESTRATOR_OUTPUT = """
=== AI REGRESSION PIPELINE ===
Metrics: {'power': 80.36, 'memory': 3.81, 'sync': 26.88, 'retry': 7}

Detected Risk Tests: ['sync', 'battery']
Calculated Risk Score: 6
High Risk Detected -> Running Full Regression
Generating tests...

Planner: rules
Selected intensity: high (80 packets)
Result: pass
"""


@pytest.mark.parametrize(
    "key,expected",
    [
        ("score", "6"),
        ("slice", "Full Regression"),
        ("planner", "rules"),
        ("intensity", "high"),
        ("result", "pass"),
    ],
)
def test_orchestrator_output_is_parsed(key, expected):
    found = None

    for line in ORCHESTRATOR_OUTPUT.splitlines():
        match = PATTERNS[key].search(line.strip())
        if match:
            found = match.group(1)

    assert found == expected


def test_metrics_line_is_parsed():
    line = "Metrics: {'power': 80.36, 'memory': 3.81, 'sync': 26.88, 'retry': 7}"

    match = PATTERNS["metrics"].match(line)

    assert match
    assert "80.36" in match.group(1)


def test_every_slice_has_a_label():
    for phrase in ("Full Regression", "Targeted Regression", "Minimal Sanity"):
        assert phrase in SLICE_LABEL


@pytest.mark.parametrize(
    "line,name,status",
    [
        ("regression/generated_tests/test_ai_generated.py::test_connection PASSED  [ 12%]",
         "test_connection", "PASSED"),
        ("regression/generated_tests/test_ai_generated.py::test_audio_dsp SKIPPED  [ 50%]",
         "test_audio_dsp", "SKIPPED"),
        ("tests/test_planner.py::test_rule_intensity[metrics0-readme update-low] PASSED [ 5%]",
         "test_rule_intensity[metrics0-readme update-low]", "PASSED"),
    ],
)
def test_live_pytest_line_is_parsed(line, name, status):
    """This is what makes results appear as each test finishes."""
    match = PATTERNS["live"].search(line)

    assert match, "no live result parsed from: " + line
    assert match.group("name") == name
    assert match.group("status") == status


def test_non_result_lines_do_not_create_rows():
    for line in ("Running generated suite...", "Metrics: {'power': 40}",
                 "  passed 6   failed 0   skipped 2"):
        assert PATTERNS["live"].search(line) is None, line


def test_live_row_is_enriched_by_the_final_marker(root):
    """Live rows have no duration; the TEST_RESULT line fills it in."""
    dash = Dashboard(root)

    dash._add_or_update("test_connection", "PASSED")
    item = dash.rows["test_connection"]
    assert dash.tree.item(item, "values")[1] == "..."

    dash._add_or_update("test_connection", "PASSED", 3.29, "some/path.log")

    assert len(dash.rows) == 1, "must update the row, not add a second one"
    assert dash.tree.item(item, "values")[1] == "3.29s"
    assert dash.log_paths[item] == "some/path.log"


def test_fallback_line_is_recognised():
    line = "[planner] HA_AI=llm requested but no credentials -- using rules"

    assert PATTERNS["fallback"].match(line)


# --------------------------------------------------------------------------
# Widget behaviour
# --------------------------------------------------------------------------

def test_dashboard_builds(root):
    dash = Dashboard(root)
    root.update()

    assert dash.planner.get() == "rules", "must default to the no-API path"
    assert dash.rows == {}, "results table starts empty"


class _StubEngine(object):
    """An engine whose availability the test decides."""

    name = "anthropic"

    def __init__(self, ok, reason):
        self._ok = ok
        self._reason = reason

    def available(self):
        return self._ok, self._reason

    @staticmethod
    def model_name():
        return "claude-opus-5"


def test_planner_status_explains_missing_credentials(root, monkeypatch):
    """With API and no key: say it is unusable AND what happens instead.

    The engine is stubbed rather than trusted to be unavailable: conftest
    clears the key variables, but an `ant auth login` profile on the
    machine would still resolve one.
    """
    from regression.ai_engine import engines

    monkeypatch.setattr(
        engines, "get_engine",
        lambda *a, **k: _StubEngine(
            False, "no Anthropic credentials found (set ANTHROPIC_API_KEY)"))

    dash = Dashboard(root)

    dash.planner.set("llm")
    dash._on_planner_change()

    text = dash.api_status.cget("text")

    assert "Unavailable" in text
    assert "ANTHROPIC_API_KEY" in text, "say which credential is missing"
    assert "fall back to rules" in text, "say what the run will do instead"


def test_planner_status_says_the_model_writes_the_test_cases(
        root, monkeypatch):
    """With API is not only a planner.

    Under HA_AI=llm the model writes test cases as well, and how many is
    HA_CASE_SOURCE's answer: "auto" (the default) and "ai" give it the
    requirement and release-note cases, "both" has it add extras to the
    rule-written ones, "rules" leaves them all to the rules. The line names
    the variable, because naming any one outcome would be false under the
    others. Wherever the model does write cases, the requirement document
    and this build's release-note section leave the machine, which is what
    SECURITY.md describes.
    """
    from regression.ai_engine import engines

    monkeypatch.setattr(engines, "get_engine",
                        lambda *a, **k: _StubEngine(True, "ready"))

    dash = Dashboard(root)

    dash.planner.set("llm")
    dash._on_planner_change()

    text = dash.api_status.cget("text")

    assert "intensity" in text and "scenarios" in text
    assert "test cases" in text
    assert "HA_CASE_SOURCE" in text, "name the variable that decides how many"
    assert "as HA_CASE_SOURCE sets" in text, (
        "the line promised the model writes the cases, which is false under "
        "HA_CASE_SOURCE=rules")

    assert "requirement cases" not in text, (
        "false under HA_CASE_SOURCE=rules and understated under both")


def test_the_key_hint_says_the_environment_is_a_place_to_put_a_key(root):
    """The window is not the only place a key can come from.

    Runs are launched with dict(os.environ), so a key already exported is
    used as it stands, with nothing typed into the window. The hint has to
    offer both routes.
    """
    dash = Dashboard(root)

    dash.planner.set("llm")
    dash._on_planner_change()

    hint = dash.key_hint.cget("text")

    assert "in the environment" in hint
    assert "before starting" in hint


def test_the_recording_checkbox_is_hidden_in_a_frozen_build(root, monkeypatch):
    """Recording runs tools/dsp_listen.py, which the exe does not ship.

    _start_recording launches `sys.executable -u tools/dsp_listen.py`, and
    in the frozen build sys.executable IS the application, so the box is
    hidden there.
    """
    import regression.dashboard as dashboard

    monkeypatch.setattr(dashboard, "is_frozen", lambda: True)

    dash = Dashboard(root)

    assert dash.record_check is None
    assert dash.record_audio.get() is False


def test_the_recording_checkbox_is_offered_from_a_source_checkout(
        root, monkeypatch):
    import regression.dashboard as dashboard

    monkeypatch.setattr(dashboard, "is_frozen", lambda: False)

    dash = Dashboard(root)

    assert dash.record_check is not None
    assert dash.record_check.winfo_manager() == "pack"


def test_an_engine_with_no_adapter_is_labelled_with_one_space(root):
    """The User Guide quotes the dropdown entry, so the spacing is part of
    the interface: "openai (no adapter)", not two spaces."""
    dash = Dashboard(root)

    labels = list(dash._engine_choices())

    assert "openai (no adapter)" in labels
    assert not any("  " in label for label in labels)


def test_summary_reports_a_finished_run(root):
    dash = Dashboard(root)

    for line in ORCHESTRATOR_OUTPUT.splitlines():
        dash._parse(line.strip())

    dash._finished()

    summary = dash.summary.cget("text")

    assert "PASS" in summary
    assert "FULL" in summary
    assert "rules" in summary
