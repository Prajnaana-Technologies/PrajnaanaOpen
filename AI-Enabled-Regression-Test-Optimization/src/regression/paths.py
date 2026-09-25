# Copyright (c) 2026 Prajnaana Technologies Pvt. Ltd.
# Original author: Dhanya Shree S
# SPDX-License-Identifier: MIT
#
# Licensed under the MIT License. See LICENSE in the project root.

"""Where the project reads and writes, whether run from source or as an .exe.

A path relative to the current working directory only works when the suite
is launched from src/, and not at all from a frozen build, so every path the
project reads or writes is resolved here once, against the application
itself.

    source run   BASE_DIR = src/, the folder holding the regression package
    frozen .exe  BASE_DIR = the folder containing the .exe

BASE_DIR is where output is written, so it has to be writable. When the
folder above is not -- an .exe unzipped into Program Files, say -- base_dir()
falls back to LOCALAPPDATA/AI_Enabled_Regression_Test_Optimization instead,
and announce_fallback() says so on stderr.

Read-only assets bundled into a PyInstaller build live in sys._MEIPASS, so
asset lookups check there first, then beside the application, then the
writable root.
"""

import os
import sys


def is_frozen():
    return getattr(sys, "frozen", False)


# Where run output goes when the application cannot write beside itself.
FALLBACK_NAME = "AI_Enabled_Regression_Test_Optimization"


def app_dir():
    """Where the application lives. May be read-only."""
    if is_frozen():
        return os.path.dirname(os.path.abspath(sys.executable))

    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _writable(path):
    """Whether a directory can be created under `path` and written to.

    Writes and removes a probe file. BASE_DIR is resolved at import, so
    importing this module touches the application folder once -- including
    for a --help run that goes on to write nothing.
    """
    probe = os.path.join(path, ".ha_write_probe")

    try:
        os.makedirs(path, exist_ok=True)

        with open(probe, "w", encoding="utf-8") as handle:
            handle.write("")

        os.remove(probe)
    except OSError:
        return False

    return True


def fallback_dir():
    root = os.getenv("LOCALAPPDATA") or os.path.expanduser("~")

    return os.path.join(root, FALLBACK_NAME)


def base_dir():
    """Writable root: logs, generated tests, artefacts.

    Beside the application when that is writable, which is the case for a
    source checkout and for an .exe unzipped into a user folder.

    It is not the case everywhere. Dropped into Program Files, or anywhere
    else Windows protects, the first thing the pipeline does -- create
    regression/generated_tests -- fails with WinError 5 and takes the whole
    run down before a single test is chosen. The application is not broken
    there; it simply has nowhere to put its output, which is a thing to
    solve rather than to crash over.
    """
    beside = app_dir()

    if _writable(beside):
        return beside

    return fallback_dir()


def bundle_dir():
    """Read-only root for bundled assets. Same as app_dir when not frozen."""
    meipass = getattr(sys, "_MEIPASS", None)

    return meipass if meipass else app_dir()


APP_DIR = app_dir()


BASE_DIR = base_dir()


def announce_fallback(stream=None):
    """Say once, loudly, that output is not where the user will look for it.

    On stderr, never stdout. Importing this module is enough to print it,
    and entry points write their real answer to stdout: run
    `python -m regression.ai_engine.canonical` with no --out and the JSON
    document goes to stdout. From a read-only install (Program Files, say)
    these two lines would otherwise land in front of it, and
    `canonical ... > x.json` would produce a file that is not valid JSON.
    Where the output went is diagnostic output, which is what stderr is
    for.

    (--out takes a filename. --out - also writes to stdout.)

    Returns whether anything was printed, so a test can tell the two cases
    apart without reading the stream.
    """
    if BASE_DIR == APP_DIR:
        return False

    out = sys.stderr if stream is None else stream

    print("Cannot write beside the application:", APP_DIR, file=out)
    print("Run output goes to:", BASE_DIR, file=out)

    return True


announce_fallback()


def in_base(*parts):
    """Absolute path under the writable root, creating parent dirs."""
    path = os.path.join(BASE_DIR, *parts)

    parent = os.path.dirname(path)
    if parent:
        os.makedirs(parent, exist_ok=True)

    return path


def asset(*parts):
    """Absolute path to a read-only asset, preferring the bundled copy.

    Falls back to the application folder before the writable root: the
    inputs a user edits -- the release note, the sample audio -- sit beside
    the executable, and that is not the same place as the output once the
    application is somewhere it cannot write.
    """
    for root in (bundle_dir(), APP_DIR, BASE_DIR):
        candidate = os.path.join(root, *parts)

        if os.path.exists(candidate):
            return candidate

    return os.path.join(APP_DIR, *parts)


# Paths the pipeline uses.
GENERATED_TEST_PATH = os.path.join(
    BASE_DIR, "regression", "generated_tests", "test_ai_generated.py"
)

# The requirement-and-release-note suite. Generated like the one above and
# executed in the same pytest run, so governance and the dashboard have to
# be able to name it rather than only the planner's module.
CANONICAL_TEST_PATH = os.path.join(
    BASE_DIR, "regression", "generated_tests", "test_from_canonical.py"
)

LOGS_DIR = os.path.join(BASE_DIR, "regression", "logs")

# Audio fed to the device. Overridable so the dashboard can offer a file
# picker without the pipeline needing to know where the GUI got it from.
SIGNAL_ENV = "HA_TEST_SIGNAL"

# Audio the user may drop beside the executable, so the packaged build has an
# obvious input to replace. Nothing ships under this name, so in practice
# default_signal falls back to the bundled stimulus.
SAMPLE_SOUND = "sample_sound.wav"


def default_signal():
    beside = os.path.join(APP_DIR, SAMPLE_SOUND)

    if os.path.exists(beside):
        return beside

    return asset("regression", "audio", "test_signal.wav")


DEFAULT_TEST_SIGNAL = default_signal()


# The release note describing what this firmware changed. Kept beside the
# firmware it describes, so the two travel together. Offered as the default
# when it exists and left blank when it does not -- an empty field means the
# pipeline compares images instead, which is the better path when it can.
RELEASE_NOTE_NAME = os.path.join("NRF_Firmware", "RELEASE_NOTES.md")


def default_release_note():
    """The release note shipped beside the firmware, or "" if there is none."""
    for candidate in (os.path.join(APP_DIR, RELEASE_NOTE_NAME),
                      os.path.join(APP_DIR, "RELEASE_NOTES.md"),
                      os.path.join(BASE_DIR, RELEASE_NOTE_NAME)):
        if os.path.exists(candidate):
            return candidate

    return ""


# The requirement document. Like the release note, an input the user edits,
# so it lives beside the application rather than inside the bundle.
REQUIREMENTS_NAME = "REQUIREMENTS.md"


def default_requirements():
    """The requirement document beside the application, or "" if absent."""
    for candidate in (os.path.join(APP_DIR, REQUIREMENTS_NAME),
                      os.path.join(BASE_DIR, REQUIREMENTS_NAME)):
        if os.path.exists(candidate):
            return candidate

    return ""


def test_signal():
    """The WAV to stream, honouring HA_TEST_SIGNAL if set."""
    override = os.getenv(SIGNAL_ENV)

    if override:
        return os.path.abspath(override)

    return DEFAULT_TEST_SIGNAL


TEST_SIGNAL = test_signal()
