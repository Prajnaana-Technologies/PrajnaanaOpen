# Copyright (c) 2026 Prajnaana Technologies Pvt. Ltd.
# Original author: Dhanya Shree S
# SPDX-License-Identifier: MIT
#
# Licensed under the MIT License. See LICENSE in the project root.

"""Tests for structured change extraction.

The rule these enforce: a release note is a specification, not a bag of
words. Keyword matching cannot tell a new feature from a bug fix and
cannot see a stated limit at all, which is where the boundary cases live.
"""

import pytest

from regression.ai_engine import change_spec
from regression.ai_engine.change_spec import extract

EXAMPLE = """Version 2.4.0

1. Added battery over-temperature protection.
2. Improved BLE reconnection handling.
3. Fixed memory leak during repeated BLE connections.
4. Increased maximum BLE packet size from 64 to 128 bytes.
"""


# --------------------------------------------------------------------------
# The worked example
# --------------------------------------------------------------------------

def test_the_version_is_read_from_the_note():
    assert extract(EXAMPLE)["version"] == "2.4.0"


def test_every_bullet_becomes_one_change():
    assert len(extract(EXAMPLE)["changes"]) == 4


def test_each_change_is_typed_by_what_the_sentence_says():
    kinds = [c["type"] for c in extract(EXAMPLE)["changes"]]

    assert kinds == [
        change_spec.NEW_FEATURE,
        change_spec.ENHANCEMENT,
        change_spec.BUG_FIX,
        change_spec.CONFIGURATION_CHANGE,
    ]


def test_each_change_names_its_component():
    parts = [c["component"] for c in extract(EXAMPLE)["changes"]]

    assert parts == ["battery", "BLE", "BLE", "BLE"]


# --------------------------------------------------------------------------
# Numbers, which is the part keyword matching cannot reach
# --------------------------------------------------------------------------

def test_a_stated_limit_is_captured_as_values():
    """"from 64 to 128 bytes" is three tests hiding in one sentence."""
    packet = extract(EXAMPLE)["changes"][3]

    assert packet["values"] == {"from": 64, "to": 128, "unit": "bytes"}


def test_a_new_limit_with_no_old_one_is_still_captured():
    spec = extract("- Raised the connection interval to 30 ms.")

    assert spec["changes"][0]["values"] == {"to": 30, "unit": "ms"}


def test_a_sentence_with_no_numbers_has_no_values():
    spec = extract("- Improved BLE reconnection handling.")

    assert "values" not in spec["changes"][0]


# --------------------------------------------------------------------------
# Honest answers when the note says little
# --------------------------------------------------------------------------

def test_a_note_with_no_bullets_describes_nothing():
    """Inventing entries would be worse than reporting none."""
    spec = extract("Some prose with no list in it at all.")

    assert spec["changes"] == []


def test_an_unrecognised_component_is_named_unknown_not_guessed():
    spec = extract("- Fixed the widget frobnicator.")

    assert spec["changes"][0]["component"] == change_spec.UNKNOWN_COMPONENT
    assert spec["changes"][0]["type"] == change_spec.BUG_FIX


def test_a_change_that_does_not_say_how_is_the_least_specific_claim():
    """Something changed -- the note listed it -- but not how."""
    spec = extract("- BLE stack touched up.")

    assert spec["changes"][0]["type"] == change_spec.ENHANCEMENT


def test_guidance_in_a_comment_is_not_a_change():
    spec = extract("<!-- e.g. - Added a thing -->\n- Fixed a leak.\n")

    assert len(spec["changes"]) == 1
    assert spec["changes"][0]["type"] == change_spec.BUG_FIX


# --------------------------------------------------------------------------
# Bullet shapes a person actually writes
# --------------------------------------------------------------------------

def test_dash_star_and_numbered_bullets_all_count():
    spec = extract("- One fixed.\n* Two fixed.\n3. Three fixed.\n4) Four fixed.\n")

    assert len(spec["changes"]) == 4


def test_components_lists_each_one_once_in_order():
    assert change_spec.components(extract(EXAMPLE)) == ["battery", "BLE"]


WRAPPED = """### Bluetooth

- Identity address bumped to C1:F5:B1:81:20:F2. Adding a service changes the
  GATT table, and a host holding a cached database would not re-discover it.

### Build system

- The devicetree overlay is named explicitly in `CMakeLists.txt`.

### Misc

- Tidied things up.
"""


def test_a_wrapped_bullet_is_read_whole():
    # "GATT" is on the second line; reading only the first loses it.
    change = change_spec.extract(WRAPPED)["changes"][0]

    assert change["component"] == "BLE"
    assert change["description"] == "Identity address bumped to C1:F5:B1:81:20:F2"
    assert "GATT table" in change["detail"]


def test_section_heading_names_the_component_when_the_line_does_not():
    change = change_spec.extract("### Battery\n\n- Level is measured, not declared.\n")["changes"][0]

    assert change["component"] == "battery"


def test_unclassified_lines_get_a_specific_reason():
    from regression.ai_engine import test_spec

    cases = test_spec.derive(change_spec.extract(WRAPPED))
    reasons = {c["test"]: c["skip_reason"] for c in cases if not c["executable"]}

    build = next(r for t, r in reasons.items() if "devicetree" in t)
    misc = next(r for t, r in reasons.items() if "Tidied" in t)
    placeholder = next(r for t, r in reasons.items() if "unknown" in t)

    assert build.startswith("Build-system change")
    assert "Tidied things up" in misc and "Recognised parts" in misc
    assert "Not a real test" in placeholder


# --------------------------------------------------------------------------
# Lines about the text of the source rather than about the device
# --------------------------------------------------------------------------

DOCUMENTATION_LINES = [
    "Fixed a typo in a comment in the audio DSP module",
    "Updated licence headers in the Bluetooth sources",
    "Updated license headers in the Bluetooth sources",
    "SPDX and copyright headers added, and comment corrections in prj.conf",
    "Corrected the spelling in the telemetry documentation",
    "Rewrote the file header of dsp_wdrc.c",
]


def test_a_documentation_line_is_typed_as_documentation():
    """"Fixed a typo" is not a bug fix; nothing on the board changed.

    The word the table keys off -- fixed, added, updated -- is in the
    sentence, so none of these must be read as real work.
    """
    for line in DOCUMENTATION_LINES:
        spec = extract("## v1.0\n\n- {}\n".format(line))

        assert spec["changes"][0]["type"] == change_spec.DOCUMENTATION, line


def test_a_documentation_line_derives_no_test_case():
    """The whole point: no bench time, and no green row proving a comment.

    "Fixed a typo in a comment in the audio DSP module" must not derive a
    runnable case that streams audio through the DSP, and "Updated licence
    headers in the Bluetooth sources" must not derive a runnable "Repeat
    the improved path 10 times".
    """
    from regression.ai_engine import test_spec

    for line in DOCUMENTATION_LINES:
        spec = extract("## v1.0\n\n- {}\n".format(line))

        assert test_spec.derive(spec) == [], line


def test_a_documentation_line_is_still_reported_as_a_change():
    """It is left out of the tests, not out of the specification.

    The note said the line was there, and the canonical document's change
    list is what a reader checks the build against.
    """
    spec = extract("## v1.0\n\n- Fixed a typo in a comment in the audio DSP\n")

    assert len(spec["changes"]) == 1
    assert spec["changes"][0]["component"] == "audio"
    assert "typo" in spec["changes"][0]["description"]


def test_a_documentation_line_puts_no_component_under_test():
    """A comment in the DSP names the audio path but does not touch it.

    Each component here becomes a baseline case that runs on the board.
    """
    spec = extract(
        "## v1.0\n\n"
        "- Fixed a typo in a comment in the audio DSP module\n"
        "- Increased maximum BLE packet size from 64 to 128 bytes\n"
    )

    assert change_spec.components(spec) == ["BLE"]


def test_a_stated_limit_outranks_a_documentation_word():
    """A sentence that states a value states a limit, whatever else it says.

    This is the guard that keeps the rule from swallowing real work: the
    boundary cases are the most valuable thing the note yields, and a
    mention of a comment must not cost them.
    """
    from regression.ai_engine import test_spec

    for line in ("Increased the header size from 4 to 8 bytes in the BLE "
                 "transport",
                 "Increased maximum BLE packet size from 64 to 128 bytes, "
                 "and fixed the comment above it"):
        spec = extract("## v1.0\n\n- {}\n".format(line))
        change = spec["changes"][0]

        assert change["type"] == change_spec.CONFIGURATION_CHANGE, line
        assert change["values"]["to"] in (8, 128), line

        kinds = {c["type"] for c in test_spec.derive(spec)}

        assert "Boundary" in kinds and "Negative" in kinds, line


# --------------------------------------------------------------------------
# A documentation WORD is not a documentation LINE
#
# The rule above stops a corrected comment booking bench time. Every line
# below names a comment, a licence or the documentation AND changes the
# device, and every one of them must derive its cases.
#
# A line is documentation only when the documentation word is the OBJECT of
# the change, and a sentence is split on "and" and "," so that each clause
# is judged on its own.
# --------------------------------------------------------------------------

MENTIONS_DOCUMENTATION_BUT_IS_WORK = [
    ("BLE comment characteristic added.",
     change_spec.NEW_FEATURE, "BLE"),
    ("Fixed a crash when the user comment field was empty in the BLE "
     "service.",
     change_spec.BUG_FIX, "BLE"),
    ("Fixed the DSP buffer overflow and updated the comment above it.",
     change_spec.BUG_FIX, "audio"),
    ("Added a BLE characteristic for user comments.",
     change_spec.NEW_FEATURE, "BLE"),
    ("Fixed a license check that let unlicensed audio features run.",
     change_spec.BUG_FIX, "audio"),
    ("Added a new audio feature: documentation mode for the DSP.",
     change_spec.NEW_FEATURE, "audio"),
    ("Fixed the copyright notice display on the battery screen.",
     change_spec.BUG_FIX, "battery"),
    ("Removed dead code and updated comments in the DSP.",
     change_spec.REMOVAL, "audio"),
    # The object is a documentation word and what follows "in" or "of" is
    # the device: the advertised name is on the air, and a host reads it.
    ("Fixed typo in BLE device name string.",
     change_spec.BUG_FIX, "BLE"),
    ("Fixed typo in BLE device name.",
     change_spec.BUG_FIX, "BLE"),
    ("Fixed typo in the advertised BLE name.",
     change_spec.BUG_FIX, "BLE"),
    # "Corrected" makes a change without saying what kind, so the least
    # specific claim stands. The type matters less than the cases surviving.
    ("Corrected the spelling of the BLE device name.",
     change_spec.ENHANCEMENT, "BLE"),
    ("Fixed the license string in the BLE advertising name.",
     change_spec.BUG_FIX, "BLE"),
]

WORK_LINES = [case[0] for case in MENTIONS_DOCUMENTATION_BUT_IS_WORK]


@pytest.mark.parametrize("line,kind,component",
                         MENTIONS_DOCUMENTATION_BUT_IS_WORK,
                         ids=WORK_LINES)
def test_a_mentioned_documentation_word_does_not_retype_the_change(
        line, kind, component):
    """The word is in the sentence; the change is still what it says.

    "BLE comment characteristic added" adds a GATT characteristic whose
    name happens to be "comment". "Fixed a license check that let
    unlicensed audio features run" is a security fix. The object of the
    change decides the type, not the word.
    """
    spec = extract("## v1.0\n\n- {}\n".format(line))
    change = spec["changes"][0]

    assert change["type"] == kind, line
    assert change["component"] == component, line
    assert not change_spec.is_documentation(line), line


@pytest.mark.parametrize("line", WORK_LINES, ids=WORK_LINES)
def test_a_mentioned_documentation_word_still_derives_a_test_case(line):
    """A work line that mentions a documentation word still derives cases.

    components() drops a documentation change, so the type decides whether
    the component reaches the run at all.
    """
    from regression.ai_engine import test_spec

    spec = extract("## v1.0\n\n- {}\n".format(line))

    assert change_spec.components(spec) != [], line
    assert test_spec.derive(spec) != [], line


@pytest.mark.parametrize("line,kind", [
    ("Fixed the DSP buffer overflow and updated the comment above it.",
     change_spec.BUG_FIX),
    ("Removed dead code and updated comments in the DSP.",
     change_spec.REMOVAL),
])
def test_a_sentence_that_does_two_things_keeps_the_one_worth_testing(
        line, kind):
    """Why clauses, and not just an object test.

    The object of the second half genuinely is a comment. A release note
    writes a fix and the comment edit that went with it in one bullet, so
    each half is classified separately and only the documentation half is
    dropped.
    """
    from regression.ai_engine import test_spec

    spec = extract("## v1.0\n\n- {}\n".format(line))

    assert spec["changes"][0]["type"] == kind, line
    assert test_spec.derive(spec) != [], line


def test_a_clause_with_no_verb_keeps_the_verb_of_the_one_before_it():
    """A release note states its verb once and means it for both halves.

    "Fixed readme and dsp gain" splits into "fixed readme", which is
    documentation, and "dsp gain", which has no verb left in it and takes
    the verb of the clause before it.
    """
    assert (change_spec.classify_type("Fixed readme and dsp gain")
            == change_spec.classify_type("Fixed dsp gain")
            == change_spec.BUG_FIX)

    assert (change_spec.classify_type("Removed the changelog and the BLE "
                                      "pairing timeout")
            == change_spec.REMOVAL)


DOCUMENTATION_ONLY_WITH_NO_CASE = [
    "Updated the license headers to 2026.",
    "Updated the docs.",
]


@pytest.mark.parametrize("line", DOCUMENTATION_ONLY_WITH_NO_CASE,
                         ids=DOCUMENTATION_ONLY_WITH_NO_CASE)
def test_a_documentation_only_line_books_no_bench_time(line):
    """The other direction: a line that is only documentation books nothing.

    A number in the sentence is evidence of neither. The clause carrying
    the limit is read on its own, so "increased ... from 64 to 128 bytes,
    and fixed the comment above it" keeps its boundary cases without the
    number deciding anything.

    "Updated the docs" needs nothing so subtle: it turns on "docs" being
    in the word list.
    """
    from regression.ai_engine import test_spec

    spec = extract("## v1.0\n\n- {}\n".format(line))

    assert spec["changes"][0]["type"] == change_spec.DOCUMENTATION, line
    assert change_spec.components(spec) == [], line
    assert test_spec.derive(spec) == [], line


def test_a_bare_header_is_still_the_device_and_not_documentation():
    """An unqualified "header" is the device, not documentation.

    A licence header is documentation; a packet header, a GATT header and
    an L2CAP header are the device. Only the qualified spellings are
    documentation words, so an unqualified "header" keeps its cases.
    """
    for line in ("Increased the header size from 4 to 8 bytes",
                 "Fixed the packet header length field",
                 "Rewrote the GATT header parser"):
        assert not change_spec.is_documentation(line), line

    assert change_spec.is_documentation("Rewrote the file header of dsp.c")
