# Copyright (c) 2026 Prajnaana Technologies Pvt. Ltd.
# Original author: Dhanya Shree S
# SPDX-License-Identifier: MIT
#
# Licensed under the MIT License. See LICENSE in the project root.

"""End-to-end pipeline: read metrics -> score risk -> generate -> run -> escalate.

The device must be powered on and advertising over BLE.

    python -m regression.ai_engine.orchestrator

With a model choosing the plan instead of the threshold rules
(HA_ENGINE picks which -- anthropic by default, or gemini):

    HA_AI=llm python -m regression.ai_engine.orchestrator

To skip the device read and supply metrics by hand:

    python -m regression.ai_engine.orchestrator \\
        --metrics '{"power": 40, "memory": 3.5, "sync": 10, "retry": 2}'
"""

import argparse
import asyncio
import json
import os
import re
import sys

import pytest

from regression.ai_engine.adaptive_engine import adaptive_escalation
from regression.ai_engine.generate_tests import GENERATED_TEST_PATH, generate_tests
from regression.ble.ble_audio import RealBLEDevice
from regression.reporting import (
    RunReport,
    emit_results,
    new_run_dir,
    parse_junit,
    write_summary,
    write_test_logs,
)
from regression.change_detection.risk_engine import select_regression_slice
from regression.instrumentation import power_analyzer
from regression import governance, kpi
from regression.paths import CANONICAL_TEST_PATH

# Used only when nothing can be derived and nothing was supplied. A typed
# description is an override, not the normal path: asking a person to
# describe the change so the planner can key off their words is choosing
# the tests by hand, which is the opposite of change-aware selection.
DEFAULT_CHANGE = "unknown change"

# Two runs sharing a build id are two runs of identical code, which is
# what makes the determinism KPI measurable.
BUILD_ID_ENV = "HA_BUILD_ID"

# A release note says what a person meant to change. The image says what
# actually changed. When a note is given it supersedes the image comparison:
# the scenarios follow the note alone, and the comparison is only printed
# beside it -- see from_release_note and derive_change.
RELEASE_NOTE_ENV = "HA_RELEASE_NOTE"

# The requirement document. Requirements generate cases that should run
# whether or not anything changed this release, so they belong in the same
# run as the change-driven ones rather than in a separate command nobody
# remembers to invoke.
REQUIREMENTS_ENV = "HA_REQUIREMENTS"


def requirements_path():
    """The requirement document to test against, or "" for none.

    Unset means "nobody said", so the document beside the application is
    used. Set but empty means "leave requirements out of this run", which
    is what the dashboard's Clear button sends.

    The two are kept apart deliberately: `os.getenv(...) or
    default_requirements()` would treat an empty value as absent, so Clear
    would empty the field while the run loaded the default anyway and the
    console still printed a requirements path. HA_RELEASE_NOTE honours
    empty-means-none as well, so the pair behave alike.
    """
    from regression.paths import default_requirements

    if REQUIREMENTS_ENV in os.environ:
        return os.environ[REQUIREMENTS_ENV].strip()

    return default_requirements()


def read_live_metrics():
    """Read the device metrics, measuring retry over this run only."""
    device = RealBLEDevice()

    # Start a fresh window so retry counts drops during this run rather than
    # since the board was powered on.
    device.reset_link_counters()

    async def _read():
        await device.connect()
        try:
            return await device.read_metrics()
        finally:
            await device.disconnect()

    return asyncio.run(_read())


def strip_comments(text):
    """Drop markdown comments from a release note.

    The whole note is read as the change description, so anything left in it
    picks scenarios. Notes carry guidance for whoever writes the next one --
    and guidance contains examples, and examples contain exactly the words
    that trigger selection. Left in, a sentence explaining how to write the
    file would choose tests on every release, forever.

    A comment is the one place text can sit in the file without being part
    of what the release changed, so it is the one place that guidance can
    live without voting.
    """
    return re.sub(r"<!--.*?-->", " ", text, flags=re.DOTALL)


def section_for(text, build_id):
    """The part of a note describing one build.

    A release-note file accumulates. Read whole, a file covering ten
    releases selects the union of ten releases' scenarios, and the set only
    ever grows -- so the tenth release runs everything the first nine
    needed, whether or not anything related changed.

    When a heading names the build under test, only that section is read.
    Headings are matched on the build id appearing in them, so "## 99b1-dirty
    +d8a2" and "## Build 99b1-dirty+d8a2 (current)" both work. When no
    heading matches, the whole text is used: a note written without build
    headings is still a note, and silently selecting nothing would be worse
    than selecting too much.

    A build id is "<git describe>+<content hash>", and only the hash
    identifies the code: the first half moves with every commit. Matching
    the whole id therefore fails after any commit -- the note would fall
    back to "use everything", and every release's changes would be selected
    as though they were this build's. So the hash is tried on its own when
    the full id finds nothing.
    """
    if not build_id:
        return text

    # "3b0d68c5-dirty+4117746a" -> also try "+4117746a".
    content_hash = ""

    if "+" in build_id:
        content_hash = "+" + build_id.rsplit("+", 1)[1]

    for wanted in (build_id, content_hash):
        if not wanted:
            continue

        found = _section_matching(text, wanted)

        if found is not None:
            return found

    return text


def _section_matching(text, wanted):
    """The section whose heading contains `wanted`, or None."""
    lines = text.splitlines()
    start = None
    depth = 0

    for index, line in enumerate(lines):
        stripped = line.lstrip()

        if not stripped.startswith("#"):
            continue

        level = len(stripped) - len(stripped.lstrip("#"))

        if start is None:
            if wanted in line:
                start = index
                depth = level

            continue

        # Only a heading at the same level or shallower ends the section. A
        # deeper one is part of it: "### Audio" under "## <build id>" is the
        # content, and ending there drops everything the note actually says.
        if level <= depth:
            return "\n".join(lines[start:index])

    if start is None:
        return None

    return "\n".join(lines[start:])


def read_release_note():
    """The release note's text, or "" when none was given or it is unreadable."""
    path = os.getenv(RELEASE_NOTE_ENV, "")

    if not path:
        return ""

    if not os.path.exists(path):
        print("Release note not found, ignoring:", path)

        return ""

    try:
        with open(path, encoding="utf-8", errors="replace") as handle:
            text = strip_comments(handle.read())
    except OSError as exc:
        print("Release note unreadable, ignoring:", exc)

        return ""

    return section_for(text, os.getenv(BUILD_ID_ENV, ""))


def from_release_note(note, evidence):
    """Selection driven by the release note alone.

    The note supersedes the image comparison rather than adding to it. The
    note is the statement of what this release changed, and the scenarios
    follow it.

    The consequence to hold on to: a change the note leaves out is not
    selected for, however plainly the image shows it. The note is the
    contract now, so it has to be complete. The image comparison is still
    printed beside it, so a disagreement is visible rather than silent.
    """
    from regression.ai_engine.planner import triggered_by_change

    selected = triggered_by_change(note)

    print("Change taken from the release note:", os.getenv(RELEASE_NOTE_ENV))
    print("   image comparison said:", evidence)

    if selected:
        print("   note selects:", ", ".join(selected))
    else:
        print("   note selects no scenario -- nothing in it names a subsystem")
        print("   the image is NOT consulted, so nothing is selected from it")

    return note


def derive_change():
    """Work out what changed.

    A release note supersedes everything else. It is the statement of what
    this release changed, and when one is given the scenarios follow it and
    nothing else.

    With no note, evidence is preferred over description, in order:

      1. The built images. Comparing this firmware's symbols with the last
         tested build says what actually changed in the thing on the bench.
         It works on a dirty build and needs no repository.

      2. The source tree. Names the files, which is more legible, but
         describes what the developer has been editing -- not necessarily
         what is flashed.

      3. Nothing. Reported as an unknown change, which changes no
         decision. The risk engine never sees it: classify_metrics,
         calculate_risk_score and select_regression_slice each take
         metrics and nothing else. The planner does see it and does
         nothing with it -- "unknown change" matches no word in
         CHANGE_TRIGGERS, so it selects no scenario, and decide_intensity
         keys on "bluetooth" and on the metrics, so it raises no
         intensity. The plan rests on the measurements alone. It is
         reported as unknown rather than as "nothing changed", because
         those are not the same claim.
    """
    from regression.change_detection import firmware_diff, git_changes

    # Must be the same id the run is recorded under, or the fingerprint is
    # filed under one name and looked up under another.
    build_id = os.getenv(BUILD_ID_ENV, "") or "unversioned"

    # Runs first and unconditionally. Besides describing the change it files
    # this build's fingerprint, and the next run compares against that. It
    # has to happen whether or not its words are the ones selection uses.
    description, why = firmware_diff.describe_against_last_tested(build_id)

    note = read_release_note()

    if note.strip():
        return from_release_note(note, description or why)

    if description:
        print("Change derived by comparing firmware images:", description)
        print("  ", why)

        return description

    # describe_against_last_tested returns no description in two quite
    # different cases, reported separately: there is nothing to compare
    # against, or the comparison ran and found only constants changed -- no
    # symbol moved -- which is deliberately handed on to git rather than
    # reported as a firmware change.
    print("No change derived from the firmware image:", why)

    description, why = git_changes.describe_since_last_run()

    if description and description != git_changes.UNKNOWN_CHANGE:
        print("Change derived from the source tree:", description)
        print("  ", why)

        return description

    print("Change could not be derived:", why)
    print("   Recorded as an unknown change. It selects nothing and "
          "raises nothing; the plan rests on the measurements alone.")

    return DEFAULT_CHANGE


# Set by main() once it has resolved the build id: that it did, and what
# it got. run_pipeline then neither announces the same id again in
# different words nor asks the board a second time. A board that reports no
# id leaves HA_BUILD_ID unset, which is why the fact of the answer is
# carried separately from the answer.
_announced_by_main = False
_build_id_from_main = ""


def read_build_id():
    """The build id the device reports, or None if it does not expose one."""
    device = RealBLEDevice()

    async def _read():
        await device.connect()
        try:
            return await device.read_build_id()
        finally:
            await device.disconnect()

    try:
        return asyncio.run(_read())
    except Exception as exc:
        print("Build id read skipped:", exc)
        return None


def resolve_build_id(announce=True):
    """HA_BUILD_ID if the caller set one, otherwise what the board reports.

    The caller wins. Every caller short-circuits on the environment
    variable before asking the device, and main() stores whatever it
    resolves back into the environment so the later readers agree -- so a
    supplied id is never overridden.

    Set it for a run where no device answers, or where a release label
    matters more than what the board reports. Leave it unset and the board
    identifies itself.
    """
    supplied = os.getenv(BUILD_ID_ENV, "")

    if supplied:
        if announce:
            print("Build id from {} (overrides the device): {}".format(
                BUILD_ID_ENV, supplied))

        return supplied

    from_device = read_build_id()

    if from_device and announce:
        print("Build id from device:", from_device)

    return from_device


def read_battery():
    """Battery level percent, or None if the device does not expose it."""
    device = RealBLEDevice()

    async def _read():
        await device.connect()
        try:
            return await device.read_battery()
        finally:
            await device.disconnect()

    try:
        return asyncio.run(_read())
    except Exception as exc:
        print("Battery read skipped:", exc)
        return None


def _module_bytes(path):
    """The module's current text, or None when there is nothing to keep."""
    try:
        with open(path, encoding="utf-8") as handle:
            return handle.read()
    except OSError:
        return None


def _restore_module(path, text):
    """Put a saved module back. True when it is written."""
    if text is None:
        return False

    try:
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(text)
    except OSError as exc:
        print("Could not restore the module that ran:", exc)

        return False

    return True


def _drop_stale_canonical(why):
    """Delete a previous run's requirement suite and return "".

    Returning "" alone left the file on disk, where governance and the
    dashboard still found it.
    """
    if os.path.exists(CANONICAL_TEST_PATH):
        os.remove(CANONICAL_TEST_PATH)
        print("Canonical suite removed:", why)

    return ""


def build_canonical_suite():
    """Write the requirement-and-note driven suite, or return "" if there is none.

    Kept beside the change-driven suite and run in the same pytest
    invocation, so one run reports both and the dashboard lists them
    together. Generating them into a file the pipeline never runs is the
    same as not generating them: the cases exist and nobody sees them.

    When there is nothing to generate the old module is deleted, not left
    behind. Governance and the dashboard list the modules that are present,
    so a leftover from a previous run -- after Requirements is Cleared, say
    -- would be validated, approved and reported although no run executes it.
    """
    from regression.ai_engine import canonical, generate_from_canonical

    requirements = requirements_path()
    note_path = os.getenv(RELEASE_NOTE_ENV, "")
    note_text = read_release_note()

    if not requirements and not note_text:
        return _drop_stale_canonical("no requirements and no release note")

    document = canonical.build(note_text, requirements)
    cases = document["tests"]

    if not cases:
        return _drop_stale_canonical("the inputs yielded no cases")

    path = generate_from_canonical.generate(document)

    summary = document["summary"]
    print("Canonical suite: {} case(s) from {} requirement(s) and {} "
          "change(s); {} runnable".format(
              summary["tests"], summary["requirements"],
              summary["changes"], summary["runnable"]))

    if requirements:
        print("   requirements:", requirements)

    if note_path:
        print("   release note:", note_path)

    flagged = summary.get("unverified_changes", 0)

    if flagged:
        # The build's own settings, which is what verify_change reads: the
        # .config beside the image, not the image. Without a build
        # directory nothing is contradicted, because nothing is checked.
        print("   WARNING: {} release-note change(s) contradict the "
              "firmware's build configuration".format(flagged))

    return path


def generated_modules():
    """Every module this run will execute: planner-selected, then canonical.

    One list, so governance checks exactly what pytest runs.
    """
    modules = [GENERATED_TEST_PATH]

    canonical_suite = build_canonical_suite()

    if canonical_suite:
        modules.append(canonical_suite)

    return modules


def govern(modules):
    """Apply the acceptance rules, and the approval gate, to every module.

    Both generated modules are machine-written and both are executed, so
    both are checked -- and the escalated re-run calls this again on the
    module it has just rewritten, because an approval is of one set of
    bytes and must not be read as covering a different set.
    """
    states = []

    for path in modules:
        states.append(governance.enforce(path))

    return states[0] if len(states) == 1 else ", ".join(states)


def run_suite(run_dir=None, targets=None):
    """Run the generated modules and report per test.

    Returns (status, RunReport). Every test gets its own log file under
    <run_dir>/tests/, so a failure can be attached to a bug report without
    digging through the console transcript.

    `targets` is the list of modules to run. The pipeline passes it, having
    built and governed them first: the canonical suite is generated before
    this call, so governance checks it. Generating it in here would bring it
    into existence after governance has run, and it would execute without
    ever being checked.
    """
    if run_dir is None:
        run_dir = new_run_dir()

    xml_path = os.path.join(run_dir, "results.xml")

    if targets is None:
        targets = generated_modules()

    targets = list(targets)

    pytest.main(targets + [
        "-v",
        "--junitxml=" + xml_path,
        "-o", "junit_logging=all",
        "-p", "no:cacheprovider",
    ])

    report = RunReport(run_dir=run_dir, records=parse_junit(xml_path))

    write_test_logs(report.records, run_dir)
    write_summary(report)
    emit_results(report)

    return report.status, report


def run_pipeline(metrics=None, change_info=DEFAULT_CHANGE):
    print("\n=== AI REGRESSION PIPELINE ===")

    # Ask the board what it is running before anything else touches it --
    # unless the caller named a build, which resolve_build_id honours, or
    # main() has already asked.
    #
    # main() reads the board, prints "Build id from device: X" and writes X
    # into HA_BUILD_ID. This call reuses main()'s answer.
    if _announced_by_main:
        build_id = os.getenv(BUILD_ID_ENV, "") or _build_id_from_main
    else:
        build_id = resolve_build_id()

    # STEP 1: metrics
    if metrics is None:
        metrics = read_live_metrics()

    # An external instrument is the only source for supply current; the
    # board cannot measure its own. Without one, power stays unmeasured.
    power_analyzer.merge_into(metrics)

    print("\nMetrics:", metrics)

    # STEP 2: risk -> regression slice
    prioritized_tests, risk_score = select_regression_slice(metrics)

    # STEP 3: generate. The planner gets the risk engine's own score and the
    # recent run history, so it plans from the same evidence the run records.
    history = kpi.planner_history()

    plan = generate_tests(
        metrics, change_info, prioritized_tests,
        risk_score=risk_score, history=history,
    )

    # STEP 3b: governance. Acceptance rules always run; approval gates
    # the run only when HA_REQUIRE_APPROVAL=1, so a bench stays
    # frictionless and anything quoted does not. Both generated modules are
    # built first so that both are checked before either is executed.
    modules = generated_modules()

    governance_state = govern(modules)

    # STEP 4: run
    print("\nRunning generated suite...\n")

    battery_start = metrics.get("battery")

    status, report = run_suite(targets=modules)

    print("\nResult:", status)

    # Battery drain across the run is a second power signal, separate from
    # read_power(), which returns the power field of the telemetry snapshot
    # (RealBLEDevice.read_power in ble/ble_audio.py).
    battery_end = read_battery() if battery_start is not None else None
    drain = None

    if battery_start is not None and battery_end is not None:
        drain = battery_start - battery_end
        print("Battery: {}% -> {}%  (drain {}%)".format(
            battery_start, battery_end, drain
        ))

    # STEP 5: escalate on failure and re-run once
    escalated = adaptive_escalation(status, plan.intensity)

    if escalated != plan.intensity:
        print("\nEscalating {} -> {} and re-running...\n".format(
            plan.intensity, escalated
        ))

        # Re-plan with the escalated intensity as a floor, so the re-run
        # cannot repeat the intensity that already failed.
        #
        # The new plan is held separately, not assigned to `plan`, until the
        # re-run is allowed to happen: the KPI must record the plan that
        # actually ran.
        #
        # `ran` keeps the bytes that executed, so a refused escalation can
        # be undone. generate_tests writes the escalated module over the one
        # that ran.
        ran = _module_bytes(GENERATED_TEST_PATH)

        escalated_plan = generate_tests(
            metrics, change_info, prioritized_tests,
            risk_score=risk_score, history=history, min_intensity=escalated,
        )

        # The re-run executes freshly generated bytes, so it is governed
        # again. An approval is of content: the one granted above covered
        # the module this call has just overwritten.
        #
        # The same module list, not generated_modules(): that call rebuilds
        # the requirement suite from the canonical document, which under
        # HA_AI=llm is a second paid model call writing a different set of
        # cases. generate_tests above rewrites only the planner-selected
        # module, and the requirement suite on disk is the one just run.
        try:
            governance_state = govern(modules)
        except RuntimeError as refusal:
            # Under HA_REQUIRE_APPROVAL=1 the regenerated module has no
            # approval -- it cannot have one, since it did not exist when a
            # person last looked. Refusing the escalation is right;
            # abandoning the run is not. The failure that triggered the
            # escalation is the result worth keeping, and it is recorded
            # below.
            # One message, and no advice that cannot work: the reason
            # without the command that comes with it. approve() refuses a
            # module that breaks an acceptance rule, and the escalated
            # module is put back below anyway.
            print("\nEscalation refused: {}".format(
                getattr(refusal, "reason", refusal)))
            print("The first run's result stands, at intensity {}, and is "
                  "recorded below.".format(plan.intensity))

            # The advice that message carries is about a module somebody
            # means to keep. This one is not, so that advice is left
            # unprinted: put the bytes that actually ran back.
            if _restore_module(GENERATED_TEST_PATH, ran):
                print("The escalated module has been replaced by the one "
                      "that ran.")
        else:
            # Only now does the escalated plan describe what ran.
            plan = escalated_plan

            status, report = run_suite(new_run_dir(), targets=modules)

            print("\nResult after escalation:", status)

    # STEP 6: record for the KPIs. Determinism compares two runs sharing a
    # build id. Leave HA_BUILD_ID blank or unset and the id is the board's
    # own, which is the right one for repeat runs on one bench; set it to
    # label a run with something else, such as a release tag.
    kpi.record_run(
        report,
        build_id=build_id,
        plan=plan,
        risk_score=risk_score,
    )

    # A failing run is a defect caught before integration, and this is the
    # only witness to it. Recording it here is what lets early_detection
    # measure without anyone typing the event in afterwards.
    caught = kpi.record_pipeline_escapes(report, build_id=build_id)

    if caught:
        print("Defects caught by this run and recorded: {}".format(
            ", ".join(caught)))

    return {
        "metrics": metrics,
        "risk_score": risk_score,
        "governance": governance_state,
        "prioritized_tests": prioritized_tests,
        "intensity": plan.intensity,
        "scenarios": plan.scenarios,
        "planner": plan.source,
        "selection": plan.selection,
        "status": status,
        "battery_start": battery_start,
        "battery_end": battery_end,
        "battery_drain": drain,
        "run_dir": report.run_dir,
        "tests": [(r.name, r.status) for r in report.records],
    }


def _resilient_output():
    """Stop an unprintable character from ending a run.

    The pipeline is launched from a console whose encoding it does not
    choose -- cp1252 on a default Windows shell -- and a model's reply is
    not restricted to what that console can represent. Replacing the
    character costs a smudge in the log; raising costs the run.
    """
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(errors="replace")
        except (AttributeError, ValueError, OSError):
            # A windowed build can have no console at all.
            pass


def main():
    _resilient_output()

    parser = argparse.ArgumentParser(description="AI regression pipeline")
    parser.add_argument(
        "--change",
        default=None,
        help="override the change description. Omit it and the change is "
             "derived, in order, from the release note, then the firmware "
             "image comparison, then git, which is the intended path",
    )
    parser.add_argument(
        "--release-note",
        default=None,
        help="path to a release note describing the firmware changes. When "
             "given it is the only thing selection reads -- the image "
             "comparison still files its fingerprint but does not choose "
             "scenarios, so the note must name everything that changed",
    )
    parser.add_argument(
        "--metrics",
        help="JSON metrics instead of reading the device",
    )
    parser.add_argument(
        "--build-id",
        default=None,
        help="identifier for the firmware under test; repeat runs "
             "sharing one are compared for the determinism KPI",
    )

    args = parser.parse_args()

    global _announced_by_main, _build_id_from_main

    metrics = json.loads(args.metrics) if args.metrics else None

    if args.release_note:
        os.environ[RELEASE_NOTE_ENV] = args.release_note

    if args.build_id:
        os.environ[BUILD_ID_ENV] = args.build_id

    # Resolve the build id first: it keys the firmware fingerprint store,
    # labels the run, and is what the determinism KPI compares on. Resolving
    # it once here, and writing it back, keeps all three consistent -- and
    # means the board is asked once rather than per reader.
    resolved = resolve_build_id()

    _announced_by_main = True
    _build_id_from_main = resolved or ""

    if resolved:
        os.environ[BUILD_ID_ENV] = resolved

    change = args.change

    if change:
        print("Change supplied on the command line:", change)
    else:
        change = derive_change()

    run_pipeline(metrics=metrics, change_info=change)


if __name__ == "__main__":
    main()
