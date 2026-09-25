# Copyright (c) 2026 Prajnaana Technologies Pvt. Ltd.
# Original author: Dhanya Shree S
# SPDX-License-Identifier: MIT
#
# Licensed under the MIT License. See LICENSE in the project root.

"""Turn a release note into a structured change specification.

The planner reads a release note as a bag of words: if "pair" appears
anywhere, the pairing scenario runs (planner.triggered_by_change, which
orchestrator.from_release_note still calls). That is enough to pick scenarios,
but it cannot tell a new feature from a bug fix, cannot say which component
moved, and -- most costly -- cannot see the numbers. "Increased maximum packet
size from 64 to 128 bytes" is three tests hiding in one sentence (64 accepted,
128 accepted, 129 rejected), and keyword matching finds none of them.

This reads the same sentence into:

    {"type": "configuration_change", "component": "BLE",
     "description": "...", "values": {"from": 64, "to": 128, "unit": "bytes"}}

which is enough to derive boundary and negative cases rather than guess at
them. It is the difference between asking a model to "generate test cases for
this firmware" and handing it a specification. This feeds the requirement
suite; scenario selection still runs on the keywords.

Deterministic on purpose. This needs no API key, no network and no cost,
and the same note always gives the same specification -- which the
determinism KPI requires of anything in the selection path. Nothing here
changes with HA_AI. A model's role is elsewhere: planner.py chooses how hard
to test, and ai_cases.py can write requirement cases, both only under
HA_AI=llm. A model-backed reader for the prose these rules cannot parse
would have to emit this same shape; none is written.

    python -m regression.ai_engine.change_spec NRF_Firmware/RELEASE_NOTES.md
"""

import json
import re

# The change types, in the vocabulary the test deriver keys off.
NEW_FEATURE = "new_feature"
ENHANCEMENT = "enhancement"
BUG_FIX = "bug_fix"
CONFIGURATION_CHANGE = "configuration_change"
REMOVAL = "removal"

# A line about the text of the source, not about what the device does. It is
# a change -- the note says so, and it stays in the specification and in the
# report -- but there is nothing on the board to exercise, so the deriver
# has no builder for it and it contributes no case.
#
# It needs its own type because the table below reads these lines as real
# work: "Fixed a typo in a comment in the audio DSP module" is a bug fix by
# the word "fixed", and "Updated licence headers in the Bluetooth sources"
# is an enhancement by nothing at all.
DOCUMENTATION = "documentation"

CHANGE_TYPES = (
    NEW_FEATURE, ENHANCEMENT, BUG_FIX, CONFIGURATION_CHANGE, REMOVAL,
    DOCUMENTATION,
)

# Ordered most specific first: "increased the buffer to fix a leak" is a
# configuration change with a reason, not a bug fix, and the first match wins.
TYPE_WORDS = (
    ("increased", CONFIGURATION_CHANGE),
    ("decreased", CONFIGURATION_CHANGE),
    ("raised", CONFIGURATION_CHANGE),
    ("lowered", CONFIGURATION_CHANGE),
    ("changed", CONFIGURATION_CHANGE),
    ("configured", CONFIGURATION_CHANGE),
    ("removed", REMOVAL),
    ("dropped", REMOVAL),
    ("deleted", REMOVAL),
    ("fixed", BUG_FIX),
    ("fix", BUG_FIX),
    ("bug", BUG_FIX),
    ("leak", BUG_FIX),
    ("regression", BUG_FIX),
    ("crash", BUG_FIX),
    ("improved", ENHANCEMENT),
    ("improve", ENHANCEMENT),
    ("optimised", ENHANCEMENT),
    ("optimized", ENHANCEMENT),
    ("faster", ENHANCEMENT),
    ("reduced", ENHANCEMENT),
    ("added", NEW_FEATURE),
    ("add", NEW_FEATURE),
    ("new", NEW_FEATURE),
    ("introduced", NEW_FEATURE),
    ("support for", NEW_FEATURE),
)

# Words that say a line is about the text of the source. Read before
# TYPE_WORDS, because these sentences say "fixed", "added" or "updated" too,
# and the first match there wins.
#
# Mentioning one of these is NOT enough on its own. Every one of them turns
# up inside real work just as often: "BLE comment characteristic added"
# changes the GATT table, "fixed a license check that let unlicensed audio
# features run" is a security fix, and "fixed the copyright notice display
# on the battery screen" draws pixels on the device. A line is
# documentation only when one of these words is the OBJECT of the change --
# see _is_doc_object.
DOC_WORDS = (
    "comment", "typo", "misspell", "spelling", "punctuation",
    "spdx", "copyright", "licence", "license",
    "documentation", "docstring", "docs", "readme", "changelog",
)

# "header" is deliberately not in the list above. A licence header is
# documentation; a packet header, a GATT header and an L2CAP header are the
# device, and "increased the header size from 4 to 8 bytes" must keep its
# boundary cases. The qualified spellings are safe, so they are named.
DOC_PHRASES = (
    "file header", "header comment", "source header",
)

# A DOC_PHRASE is folded to this one token before the object is read, so
# "the file header" is a single documentation noun while "the file header
# size" still has "size" in it and stays a change.
DOC_PHRASE_TOKEN = "__docphrase__"

# Words allowed to stand beside a documentation word in the object without
# making it something on the board: determiners, the nouns a documentation
# word takes ("licence headers", "copyright notices", "comment
# corrections"), and the adjectives a note puts in front of them.
#
# Filler alone is never documentation -- one real DOC_WORD has to be
# present -- which is what keeps "removed the HA_DISCONNECT_TEST block" and
# "rewrote the packet header" out of this.
DOC_FILLER = frozenset("""
a an the its their our all every each some any no this these those
header headers notice notices block blocks line lines text texts wording
entry entries correction corrections string strings year years note notes
new old stale outdated missing minor various several few many incorrect
wrong bad broken duplicate leftover extra inline internal only
""".split())

# Where an object noun phrase ends. Everything from here on qualifies the
# object or starts a new thought: "a comment in the audio DSP module" has
# to read as "a comment" rather than as something about the DSP, and "a
# crash when the user comment field was empty" as "a crash".
OBJECT_END = frozenset("""
in of on for to from at with above below before after into across under
over during per via until unless that which who whose when where while so
because and or but
""".split())

# The two object-end words that say where the changed text sits. "a typo in
# the audio DSP module" and "a typo in the BLE device name" are the same
# shape, and what follows decides which one it is: a file, or something the
# device puts on the air.
OBJECT_SCOPE = frozenset(("in", "of"))

# Nouns for something the device exposes rather than for the source it is
# written in. The advertised name is on the air and a host reads it, so a
# typo in it changes what the board does and has cases to run. A component
# name is not enough on its own: a comment lives in the audio DSP module and
# a licence header lives in the Bluetooth sources, and both are still only
# text.
DEVICE_NOUNS = ("name", "string", "uuid", "advertis")

# The verbs a release note uses for making a change, and only verbs.
# "crash", "leak" and "bug" type a sentence but never take an object: the
# object of "fixed a crash when the user comment field was empty" is
# "a crash".
CHANGE_VERBS = (
    "added", "add", "removed", "remove", "deleted", "delete", "dropped",
    "drop", "fixed", "fix", "updated", "update", "corrected", "correct",
    "increased", "decreased", "raised", "lowered", "changed", "configured",
    "improved", "improve", "optimised", "optimized", "reduced", "introduced",
    "rewrote", "rewritten", "rewrite", "tidied", "cleaned", "clarified",
    "reworded", "refreshed", "regenerated", "bumped", "replaced", "renamed",
    "moved",
)

# One sentence can do two things. "Fixed the DSP buffer overflow and
# updated the comment above it" is a bug fix AND a documentation edit. Each
# part is classified on its own and only the documentation parts are
# dropped, so the fix survives its own release note.
CLAUSE_SPLIT = re.compile(r",|;|\band\b|\bbut\b")

WORD_RE = re.compile(r"[a-z0-9_]+")

# Component names, in the words a release note actually uses. The right-hand
# side is the label the test deriver groups by and builds test ids from.
COMPONENT_WORDS = (
    ("over-temperature", "battery"),
    ("temperature", "battery"),
    ("batter", "battery"),
    ("charg", "battery"),
    ("ble", "BLE"),
    ("bluetooth", "BLE"),
    ("gatt", "BLE"),
    ("advertis", "BLE"),
    ("pair", "BLE"),
    ("bond", "BLE"),
    ("reconnect", "BLE"),
    ("connection", "BLE"),
    ("wdrc", "audio"),
    ("compress", "audio"),
    ("codec", "audio"),
    ("audio", "audio"),
    ("dsp", "audio"),
    ("memory", "memory"),
    ("heap", "memory"),
    ("stack", "memory"),
    ("alloc", "memory"),
    ("power", "power"),
    ("current", "power"),
    ("sleep", "power"),
    ("telemetry", "telemetry"),
    ("uart", "uart"),
    ("sensor", "sensor"),
)

UNKNOWN_COMPONENT = "unknown"

# A bullet in a release note: "- text", "* text", "1. text", "1) text".
BULLET = re.compile(r"^\s*(?:[-*•]|\d+[.)])\s+(.*)$")

# "Version 2.4.0", "## v2.4.0", "Release 2.4".
VERSION = re.compile(
    r"(?:version|release|v)\s*[:\-]?\s*(\d+(?:\.\d+){1,3}[0-9a-z.+_-]*)",
    re.IGNORECASE,
)

# "from 64 to 128 bytes" -- the shape that makes boundary cases derivable.
FROM_TO = re.compile(
    r"from\s+(\d+)\s*(?:\w+\s+)?to\s+(\d+)\s*([a-z]+)?", re.IGNORECASE)

# "to 128 bytes", "maximum 128 bytes" -- a new limit with no stated old one.
TO_ONLY = re.compile(
    r"(?:to|at|of|max(?:imum)?)\s+(\d+)\s*([a-z]+)?", re.IGNORECASE)

COMMENT = re.compile(r"<!--.*?-->", re.DOTALL)

# Up to the first full stop followed by a space, or all of it. "0x2A19" and
# "v2.4" contain full stops that do not end a sentence.
FIRST_SENTENCE = re.compile(r"(.*?\.)(?:\s|$)", re.DOTALL)
HEADING = re.compile(r"^\s{0,3}#{1,6}\s+", re.MULTILINE)


def is_documentation(text):
    """True when every part of a sentence is about the source text.

    Two rules, and a sentence has to satisfy both to be dropped.

    First, a documentation word has to be the OBJECT of the change -- "a
    comment", "comments in the DSP", "licence headers", "the docs" -- not
    merely somewhere in the sentence. A mention is worth nothing: "BLE
    comment characteristic added" adds a GATT characteristic and "fixed a
    license check that let unlicensed audio features run" is a security
    fix, and neither must be dropped from the run for naming a comment or a
    licence in passing.

    Second, the sentence is split on "and" and "," and each clause is read
    on its own, so "fixed the DSP buffer overflow and updated the comment
    above it" keeps the fix and drops only the comment. Splitting is the
    stronger of the two and the reason it is here: an object test alone
    still loses the whole line whenever a real change and a documentation
    edit share one sentence, which is how release notes are written.

    A number in the sentence decides nothing. The temptation is to read one
    as proof of a real change -- a sentence stating a value usually states a
    limit, and a limit is a boundary to test at -- but the clause carrying
    the limit already stands on its own here, with no documentation word as
    its object, so "increased maximum BLE packet size from 64 to 128 bytes,
    and fixed the comment above it" keeps its boundary cases for the right
    reason. Reading the number instead costs the other direction: "updated
    the licence headers to 2026" would count as a change and spend bench
    time proving a year.
    """
    clauses = _clauses(text)

    return bool(clauses) and all(_is_doc_clause(part) for part in clauses)


def _clauses(text):
    """A sentence split into separately-classifiable parts."""
    parts = []

    for part in CLAUSE_SPLIT.split(text.lower()):
        part = part.strip(" \t.:-\u2013\u2014")

        if WORD_RE.search(part):
            parts.append(part)

    return parts


def _is_doc_clause(clause):
    """True when a clause changes the text of the source and nothing else."""
    for phrase in DOC_PHRASES:
        clause = clause.replace(phrase, DOC_PHRASE_TOKEN)

    return any(_is_doc_object(part) for part in _object_phrases(clause))


def _object_phrases(clause):
    """The candidate objects of the change in one clause.

    A note puts the object after the verb ("updated the comment") or before
    it ("copyright headers added"), and a fragment with no verb at all
    ("comment corrections in prj.conf") is all object.
    """
    found = _verb_span(clause)

    if found is None:
        return [clause]

    start, end = found

    return [clause[end:], clause[:start]]


def _verb_span(clause):
    """Where the earliest change verb sits, or None when there is none."""
    best = None

    for verb in CHANGE_VERBS:
        start = 0

        while True:
            found = clause.find(verb, start)

            if found < 0:
                break

            end = found + len(verb)

            if ((found == 0 or clause[found - 1] not in WORD_CHARS)
                    and (end >= len(clause)
                         or clause[end] not in WORD_CHARS)):
                if best is None or found < best[0]:
                    best = (found, end)

                break

            start = found + 1

    return best


def _is_doc_object(phrase):
    """True when a noun phrase names the text of the source.

    The phrase is cut at the first OBJECT_END word, so only the head of the
    object is judged: "a comment in the audio DSP module" is "a comment",
    while "a BLE characteristic for user comments" is "a BLE
    characteristic", which is the device.

    The cut costs one distinction, which is why the rest is kept. "in" and
    "of" say where the text sits, and a documentation head followed by a
    DEVICE_NOUN names something the board puts on the air: "a typo in the
    BLE device name" is the advertised name, which a host reads and a case
    can check, so the clause is work rather than documentation.
    """
    tokens = []
    rest = []
    words = WORD_RE.findall(phrase)

    for index, token in enumerate(words):
        if token in OBJECT_END:
            if token in OBJECT_SCOPE:
                rest = words[index + 1:]

            break

        tokens.append(token)

    documentation = False

    for token in tokens:
        if token == DOC_PHRASE_TOKEN or any(
                token.startswith(word) for word in DOC_WORDS):
            documentation = True
        elif token not in DOC_FILLER:
            return False

    if documentation and any(
            token.startswith(noun) for token in rest for noun in DEVICE_NOUNS):
        return False

    return documentation


def classify_type(text):
    """The change type a sentence describes.

    Read from the parts of the sentence that are not documentation, so a
    fix announced beside a comment edit is still a fix.

    A clause with no verb of its own is read with the verb of the clause
    before it. "Fixed readme and dsp gain" states "fixed" once and means it
    for both halves, so the split must not cost the second half its type.
    """
    clauses = _clauses(text)
    working = []
    verb = ""

    for part in clauses:
        found = _verb_span(part)

        if found is not None:
            verb = part[found[0]:found[1]]

        # The documentation test reads the clause as written; only the type
        # words are read with the inherited verb.
        if _is_doc_clause(part):
            continue

        working.append(part if found is not None or not verb
                       else verb + " " + part)

    if clauses and not working:
        return DOCUMENTATION

    lowered = " ".join(working) if working else text.lower()

    for word, kind in TYPE_WORDS:
        if _mentions(lowered, word):
            return kind

    # Something changed -- the note said so by listing it -- but the sentence
    # does not say how. Enhancement is the least specific claim that is still
    # true, and it selects a regression run rather than a boundary sweep.
    return ENHANCEMENT


def classify_component(text):
    """The component a sentence is about."""
    lowered = text.lower()

    for word, component in COMPONENT_WORDS:
        if _mentions(lowered, word):
            return component

    return UNKNOWN_COMPONENT


def _mentions(text, word):
    """True when `word` starts a word in `text`.

    Same boundary rule as the planner, kept as its own copy: neither module
    imports the other, so each stays usable on its own. A plain substring
    search would read "during" as "ring".
    """
    start = 0

    while True:
        found = text.find(word, start)

        if found < 0:
            return False

        if found == 0 or text[found - 1] not in WORD_CHARS:
            return True

        start = found + 1


WORD_CHARS = "abcdefghijklmnopqrstuvwxyz0123456789_"


def extract_values(text):
    """Numeric bounds in a sentence, or {} when it states none.

    This is the part keyword matching cannot do and the part worth the most.
    A stated limit is a boundary to test at; an unstated one is a guess.

    From a "from X to Y" pair test_spec derives the OLD limit, the NEW
    limit, and one step past the new one. Nothing emits a case just under
    the new limit.
    """
    match = FROM_TO.search(text)

    if match:
        values = {"from": int(match.group(1)), "to": int(match.group(2))}

        if match.group(3):
            values["unit"] = match.group(3).lower()

        return values

    match = TO_ONLY.search(text)

    if match:
        values = {"to": int(match.group(1))}

        if match.group(2):
            values["unit"] = match.group(2).lower()

        return values

    return {}


def describe(text):
    """The sentence, trimmed to a description worth putting in a report."""
    cleaned = " ".join(text.split()).strip()

    return cleaned.rstrip(".")


def extract(note):
    """A structured change specification from release-note text.

    Returns {"version": str, "changes": [...]}. An empty changes list is an
    honest answer: a note with no bullet points describes nothing this can
    key off, and inventing entries would be worse than reporting none.
    """
    note = COMMENT.sub(" ", note or "")

    version = ""
    found = VERSION.search(note)

    if found:
        version = found.group(1)

    # A bullet wrapped over several lines is one change, so all of its lines
    # are read.
    bullets = []
    section = ""

    for line in note.splitlines():
        if HEADING.match(line):
            section = HEADING.sub("", line).strip()
            continue

        bullet = BULLET.match(line)

        if bullet:
            bullets.append([bullet.group(1).strip(), section])
        elif bullets and line.strip() and line[:1].isspace():
            bullets[-1][0] += " " + line.strip()
        elif not line.strip():
            continue
        else:
            # Unindented prose ends the bullet: it belongs to the section.
            bullets.append(None)

    changes = []

    for item in bullets:
        if not item or not item[0]:
            continue

        text, section = item

        component = classify_component(text)

        # A bullet under "### Bluetooth" is about Bluetooth even when it
        # never says so; the heading is the author's own classification.
        if component == UNKNOWN_COMPONENT and section:
            component = classify_component(section)

        # The component can be named anywhere in the bullet, so it is read
        # from all of it. The type and any limit are read from the first
        # sentence, which is where a note says what it did; the rest is
        # explanation, and "fixed" or "to 0" in an explanation is not a
        # change of that kind.
        sentence = FIRST_SENTENCE.match(text)
        first = sentence.group(1) if sentence else text

        entry = {
            "type": classify_type(first),
            "component": component,
            "description": describe(first),
            "detail": describe(text),
        }

        if section:
            entry["section"] = section

        values = extract_values(first)

        if values:
            entry["values"] = values

        changes.append(entry)

    return {"version": version, "changes": changes}


def components(spec):
    """Every component the specification puts under test, in first-seen order.

    A documentation line names a component -- "a comment in the audio DSP
    module" says audio -- but it does not touch it, and the deriver turns
    each component here into a baseline case that runs on the board. A note
    whose only mention of the audio path is a corrected comment earns no
    audio test, so those lines are left out.
    """
    seen = []

    for change in spec.get("changes", []):
        if change.get("type") == DOCUMENTATION:
            continue

        component = change.get("component", UNKNOWN_COMPONENT)

        if component not in seen:
            seen.append(component)

    return seen


def main(argv=None):
    import argparse

    parser = argparse.ArgumentParser(
        description="Extract a structured change specification from a note")
    parser.add_argument("note", help="path to the release note")

    args = parser.parse_args(argv)

    with open(args.note, encoding="utf-8", errors="replace") as handle:
        spec = extract(handle.read())

    print(json.dumps(spec, indent=2))

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
