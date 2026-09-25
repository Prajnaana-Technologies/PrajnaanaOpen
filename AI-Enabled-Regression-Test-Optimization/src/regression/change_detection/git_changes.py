# Copyright (c) 2026 Prajnaana Technologies Pvt. Ltd.
# Original author: Dhanya Shree S
# SPDX-License-Identifier: MIT
#
# Licensed under the MIT License. See LICENSE in the project root.

"""Work out what changed from the repository, not from an operator's typing.

The point of change-aware regression is that the framework reads the change
and decides what to test. Asking a person to describe the change so the
planner can key off their words is choosing the tests by hand with extra
steps: whoever types it already decided the answer.

So this derives the description from the code:

    files changed  ->  subsystems touched  ->  a description the planner reads

The board reports the git revision it was built from, and the run history
records what was tested last. The gap between those two is exactly the change
under test, and no one has to remember it.

    python -m regression.change_detection.git_changes
    python -m regression.change_detection.git_changes --since 99b1e43
"""

import os
import subprocess

from regression.paths import BASE_DIR

# Which subsystem a path belongs to. Checked in order, first match wins, so
# put the specific patterns above the general ones.
#
# The words on the right are what the planner keys on -- see
# planner.CHANGE_TRIGGERS. Keeping the vocabulary shared is deliberate.
# Each entry is (path fragment, description, affects the device).
#
# Every fragment names a file that exists, and that is a requirement, not
# a coincidence: NRF_Firmware/src holds ble_telemetry_service.c, dsp_wdrc.c
# and main.c and nothing else.
#
# The third field matters. A change to planner.py alters how tests are chosen
# but cannot change how the board behaves, so it must not pull in hardware
# stress scenarios -- the host suite covers it. Only device-affecting changes
# feed the planner; host-only ones are reported and then set aside.
PATH_SUBSYSTEMS = (
    ("ble_telemetry_service", "bluetooth telemetry", True),
    ("dsp_wdrc", "audio dsp", True),
    ("main.c", "bluetooth lifecycle", True),
    ("prj.conf", "bluetooth configuration", True),

    # NRF_Firmware/CMakeLists.txt decides whether the audio write
    # characteristic demands an encrypted link (HA_OPEN_WRITE), and it feeds
    # the build id. Both are device behaviour, so it has to come before the
    # host "CMakeLists" row below, which matches the same basename.
    ("NRF_Firmware/CMakeLists", "pairing security", True),

    # Host-side. Real changes, but not ones the board can feel. None of
    # these descriptions may contain a word from planner.CHANGE_TRIGGERS:
    # host-only changes are set aside rather than planned on.
    # tests/test_planner.py holds the two apart.
    #
    # ble_audio.py is the host's GATT client: Python that runs on the laptop.
    # The device side is ble_telemetry_service.c, so no firmware file matches
    # that fragment.
    ("ble_audio", "host BLE client (host)", False),
    ("telemetry.py", "telemetry decoding (host)", False),
    ("audio_prep", "WAV conversion (host)", False),
    ("CMakeLists", "build (host)", False),
    ("risk_engine", "risk scoring (host)", False),
    ("planner", "test planning (host)", False),
    ("generate_tests", "test generation (host)", False),
    ("scenario_tests", "test generation (host)", False),
    ("governance", "governance (host)", False),
    ("kpi", "metrics (host)", False),
)

# Paths that change constantly and say nothing about the firmware under test.
IGNORED = (
    ".git/",
    "bin/",
    "dist/",
    "build/",
    "__pycache__",
    "regression/logs/",
    "regression/artifacts/",

    # Both generated suites, not just the planner's one. A run writes
    # test_ai_generated.py and test_from_canonical.py, governance checks
    # both and pytest runs both. .gitignore names both.
    "regression/generated_tests/test_ai_generated.py",
    "regression/generated_tests/test_from_canonical.py",

    ".hex",
    ".bin",
    ".elf",
    ".pyc",
)

# Used when git says nothing changed, or there is no git at all. Honest rather
# than empty: an unknown change is not a small change.
UNKNOWN_CHANGE = "unknown change"


def _git(args, cwd=None):
    """Run git and return stdout, or None when git cannot answer."""
    try:
        result = subprocess.run(
            ["git"] + list(args),
            cwd=cwd or BASE_DIR,
            capture_output=True,
            text=True,
            timeout=15,
        )
    except (OSError, subprocess.SubprocessError):
        return None

    if result.returncode != 0:
        return None

    return result.stdout


def is_repository(cwd=None):
    return _git(["rev-parse", "--git-dir"], cwd) is not None


def current_revision(cwd=None):
    out = _git(["describe", "--always", "--dirty", "--abbrev=8"], cwd)

    return out.strip() if out else None


def _interesting(path):
    return path and not any(skip in path for skip in IGNORED)


def changed_files(since=None, cwd=None):
    """Paths that differ from `since`, plus anything uncommitted.

    With no `since`, compares against the previous commit. Uncommitted work is
    always included: the board is usually flashed from a dirty tree, and the
    build id says "-dirty" precisely because of it.
    """
    paths = []

    if since:
        out = _git(["diff", "--name-only", "{}..HEAD".format(since)], cwd)
    else:
        out = _git(["diff", "--name-only", "HEAD~1..HEAD"], cwd)

    if out:
        paths.extend(out.splitlines())

    uncommitted = _git(["status", "--porcelain"], cwd)

    if uncommitted:
        for line in uncommitted.splitlines():
            # "XY path" or "XY old -> new"
            entry = line[3:].strip()

            if " -> " in entry:
                entry = entry.split(" -> ", 1)[1]

            paths.append(entry)

    seen = []

    for path in paths:
        path = path.strip().strip('"')

        if _interesting(path) and path not in seen:
            seen.append(path)

    return seen


def subsystems_for(paths, device_only=False):
    """The subsystem words the changed paths map to, in order of appearance.

    device_only drops the host-side ones, which is what the planner should
    see: a change to the test generator cannot alter how the board behaves.
    """
    found = []

    for path in paths:
        name = os.path.basename(path)

        for pattern, subsystem, affects_device in PATH_SUBSYSTEMS:
            if pattern in name or pattern in path:
                if device_only and not affects_device:
                    break

                if subsystem not in found:
                    found.append(subsystem)

                break

    return found


def device_subsystems(paths):
    return subsystems_for(paths, device_only=True)


def describe(since=None, cwd=None):
    """A change description derived from the repository.

    Returns (description, detail) where detail explains where it came from, so
    the GUI and the log can show why these tests were selected rather than
    presenting the choice as a black box.
    """
    if not is_repository(cwd):
        return UNKNOWN_CHANGE, "not a git repository; nothing to derive from"

    paths = changed_files(since, cwd)

    if not paths:
        reference = since or "the previous commit"

        return UNKNOWN_CHANGE, "no tracked changes since {}".format(reference)

    device = device_subsystems(paths)
    everything = subsystems_for(paths)

    if not device:
        host_only = ", ".join(everything) if everything else "nothing mapped"

        return (
            UNKNOWN_CHANGE,
            "{} file(s) changed but none touch the device ({}); "
            "the run will be chosen from device metrics alone".format(
                len(paths), host_only),
        )

    description = ", ".join(device)

    detail = "{} file(s) changed -> {}".format(
        len(paths), ", ".join(os.path.basename(p) for p in paths[:5])
    )

    if len(paths) > 5:
        detail += " and {} more".format(len(paths) - 5)

    host = [s for s in everything if s not in device]

    if host:
        detail += "; host-side too: " + ", ".join(host)

    return description, detail


def describe_since_last_run():
    """Derive the change from what was last tested, using the run history.

    The board reports the revision it was built from and the KPI history
    records the revision last tested. The difference between them is the
    change under test.
    """
    try:
        from regression import kpi

        runs = kpi.load_runs()
    except Exception:  # noqa: BLE001 - a missing history is not an error
        runs = []

    previous = None

    for run in reversed(runs):
        build = run.get("build_id", "")

        if not build:
            continue

        # Keep only the part git can diff from. A build id is
        # "<git describe>[-dirty]+<content hash>", and neither the "-dirty"
        # marker nor the "+hash" is a revision, so both come off. `git
        # diff` fails on a whole id such as 3b0d68c5+4117746a, clean or
        # not.
        revision = build.split("-dirty")[0].split("+")[0]

        # "unversioned" is CMakeLists.txt's answer when the firmware is
        # built from a source drop with no repository, and it is not a
        # revision either. The test is against the stripped revision rather
        # than the whole id: every id ends in "+<hash>", so no id equals
        # "unversioned".
        if revision == "unversioned":
            continue

        previous = revision
        break

    if not previous:
        return describe()

    description, detail = describe(since=previous)

    if description != UNKNOWN_CHANGE:
        detail += " (since {})".format(previous)

    return description, detail


def main():
    import argparse

    parser = argparse.ArgumentParser(
        description="Derive the change description from the repository")
    parser.add_argument("--since", default=None,
                        help="revision to compare against")
    parser.add_argument("--last-run", action="store_true",
                        help="compare against the last tested build")

    args = parser.parse_args()

    if args.last_run:
        description, detail = describe_since_last_run()
    else:
        description, detail = describe(since=args.since)

    print("change:", description)
    print("why:   ", detail)

    # An unknown change is not scored at all: risk_engine.py scores the
    # metrics and nothing else, and "unknown change" matches no word in
    # planner.CHANGE_TRIGGERS, so it selects nothing and raises nothing.
    # The plan rests on the measurements, which is all this says.
    if description == UNKNOWN_CHANGE:
        print()
        print("Nothing was derived, so the planner will fall back to metrics")
        print("alone. That is correct, not a failure: the change is reported")
        print("as unknown, and the plan rests on the measurements alone.")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
