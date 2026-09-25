# Copyright (c) 2026 Prajnaana Technologies Pvt. Ltd.
# Original author: Dhanya Shree S
# SPDX-License-Identifier: MIT
#
# Licensed under the MIT License. See LICENSE in the project root.

"""What the build says about itself, and whether the note agrees.

A release note is a claim. The build's .config is the evidence. Comparing
the two is what stops a note saying "packet size increased to 128" from
passing against firmware still built with 64 -- which would test the claim
rather than the thing.

This reads facts out of the build and checks each claimed change against
them. Three verdicts, and the third is the important one:

    CONFIRMED     the build's settings carry the value the note claims
    CONTRADICTED  the build's settings carry a different value  <- the warning
    UNVERIFIABLE  nothing readable speaks to this claim

The verdicts rest on the .config alone: verify_change compares the claimed
number with a Kconfig value and reads nothing else, so it is checked against
the build's settings rather than against the image. The practical
consequence is that without a build directory -- a packaged release, or a
source drop with no build/ tree -- read_config returns nothing and EVERY
claim comes back UNVERIFIABLE. That is the honest answer there, not a fault,
though a reader who expected the .bin to be consulted could read it as one.

UNVERIFIABLE is not a failure and must never be reported as one. Most
sentences in a release note describe behaviour no build artefact records --
"improved reconnection handling" leaves no number anywhere. Calling that a
problem would train everyone to ignore the warnings, and then the one real
CONTRADICTED would be ignored with them.

Sources, in order of how much they carry:

    .config      every Kconfig value, and exact: CONFIG_BT_MAX_CONN=1 is a
                 fact. The richest source by far, and the only one a build
                 tree has that a source drop does not.
    .bin/.hex    printable strings only -- no symbol table exists in a raw
                 image, so this is the floor, not the ceiling
    zephyr.map   memory regions

    python -m regression.change_detection.firmware_facts --note NOTE.md
"""

import binascii
import os
import re

from regression.paths import APP_DIR, BASE_DIR, bundle_dir

CONFIRMED = "CONFIRMED"
CONTRADICTED = "CONTRADICTED"
UNVERIFIABLE = "UNVERIFIABLE"

# Where a Zephyr build leaves each artefact, relative to each of the roots
# _first_existing searches -- src/, not the repository root.
CONFIG_CANDIDATES = (
    os.path.join("NRF_Firmware", "build", "NRF_Firmware", "zephyr", ".config"),
    os.path.join("NRF_Firmware", "build", "zephyr", ".config"),
)

MAP_CANDIDATES = (
    os.path.join("NRF_Firmware", "build", "NRF_Firmware", "zephyr",
                 "zephyr_final.map"),
    os.path.join("NRF_Firmware", "build", "NRF_Firmware", "zephyr",
                 "zephyr.map"),
)

# Either form of the image works: read_strings() decodes Intel HEX back to
# the bytes it carries before scanning it.
IMAGE_CANDIDATES = (
    os.path.join("NRF_Firmware", "firmware.bin"),
    os.path.join("NRF_Firmware", "firmware.hex"),
)

CONFIG_ENV = "HA_FIRMWARE_CONFIG"

# What a release note calls a thing -> the Kconfig keys that hold it.
#
# Deliberately small. A wrong mapping is worse than a missing one: a missing
# one reports UNVERIFIABLE, which is true, while a wrong one reports
# CONTRADICTED against an unrelated value. Add a row only when the
# correspondence is exact.
CLAIM_KEYS = (
    (("packet size", "packet length", "mtu", "payload size"),
     ("CONFIG_BT_L2CAP_TX_MTU", "CONFIG_BT_BUF_ACL_TX_SIZE")),
    (("max connection", "maximum connection", "simultaneous connection",
      "concurrent connection"),
     ("CONFIG_BT_MAX_CONN",)),
    (("device name", "advertised name"),
     ("CONFIG_BT_DEVICE_NAME",)),
    (("stack size", "main stack"),
     ("CONFIG_MAIN_STACK_SIZE",)),
    (("heap", "heap size"),
     ("CONFIG_HEAP_MEM_POOL_SIZE",)),
    (("transmit power", "tx power"),
     ("CONFIG_BT_CTLR_TX_PWR_DBM",)),
)

# "<git describe>+<source hash>", as CMakeLists.txt builds it. The four
# shapes this repository's own builds produce:
#
#   3b0d68c5-dirty+4117746a    a bare commit -- what a bench build looks like
#   v1.0-3-g3b0d68c5+4117746a  three commits past a tag
#   v1.0+9e4947d1              exactly at a tag -- what a RELEASE looks like
#   unversioned+a5fb48bd       no repository at all: a zip or a tarball, for
#                              which CMakeLists.txt sets "unversioned"
#
# Not every describe output: a tag is recognised only in the "v<digit>..."
# form this project uses, so "release-1.0+dd586d8d", "1.0.0+dd586d8d" and
# "V2+dd586d8d" all read as no build id. Widening the alternative to any
# tag text would make it match almost any printable run followed by
# "+<8 hex>", and these ids are recovered by scanning a raw image, where a
# false positive is worse than a miss. Change the tag convention and this
# pattern has to change with it.
BUILD_ID = re.compile(
    r"(?:"
    r"[0-9A-Za-z][0-9A-Za-z._\-]*-\d+-g[0-9a-f]{7,10}"
    r"|v[0-9][0-9A-Za-z._]*"
    r"|unversioned"
    r"|[0-9a-f]{7,10}"
    r")(?:-dirty)?\+[0-9a-f]{8}")
ZEPHYR_VERSION = re.compile(r"Zephyr OS v?([0-9][0-9a-zA-Z.\-]*)")
PRINTABLE = re.compile(rb"[ -~]{6,}")

# The advertised name, recovered from the image when no .config is present.
# Anchored on "Zephyr" because that is the name the host scans for -- see
# ble_audio.DEVICE_NAME_MATCH, and the comment in prj.conf that keeps the
# two in step.
DEVICE_NAME = re.compile(r"^Zephyr[A-Za-z0-9_.\-]{0,24}$")
CONFIG_LINE = re.compile(r"^(CONFIG_[A-Z0-9_]+)=(.*)$")
MEMORY_REGION = re.compile(r"^(FLASH|RAM)\s+0x[0-9a-fA-F]+\s+(0x[0-9a-fA-F]+)")


def _roots():
    """Where a build artefact may sit, nearest first, without repeats.

    BASE_DIR alone is right only while the application can write beside
    itself. Dropped into Program Files, BASE_DIR moves to LOCALAPPDATA --
    an empty folder that has never held a firmware image -- so an image
    and a .config sitting beside the application are invisible from there.
    Nothing raises: facts() reports no build id, canonical cannot find the
    note section naming this build, and it reads the whole release note
    instead, deriving cases from every entry the note has ever carried. A
    read-only install would run a different, larger suite than the same
    release unpacked into a user folder.

    APP_DIR is where the inputs live (paths.asset says the same about the
    release note and the sample audio) and bundle_dir is where PyInstaller
    unpacks anything built into the exe.
    """
    roots = []

    for root in (BASE_DIR, APP_DIR, bundle_dir()):
        if root and root not in roots:
            roots.append(root)

    return roots


def _first_existing(candidates, override_env=""):
    if override_env:
        path = os.getenv(override_env, "")

        if path and os.path.exists(path):
            return path

    for candidate in candidates:
        if os.path.isabs(candidate):
            if os.path.exists(candidate):
                return candidate

            continue

        for root in _roots():
            path = os.path.join(root, candidate)

            if os.path.exists(path):
                return path

    return None


def read_config(path=None):
    """Every Kconfig value in the build, as {key: str}.

    Values keep their raw form: "y", "251", '"Zephyr_Earbuds"'. Quotes are
    stripped, because a note says the name is Zephyr_Earbuds, not
    "Zephyr_Earbuds".
    """
    path = path or _first_existing(CONFIG_CANDIDATES, CONFIG_ENV)

    if not path:
        return {}

    values = {}

    try:
        with open(path, encoding="utf-8", errors="replace") as handle:
            for line in handle:
                match = CONFIG_LINE.match(line.strip())

                if match:
                    values[match.group(1)] = match.group(2).strip().strip('"')
    except OSError:
        return {}

    return values


def _intel_hex_bytes(data):
    """The image bytes carried by an Intel HEX file, concatenated.

    A .hex holds the image as ASCII records, so scanning the file itself finds
    record text rather than the strings inside the firmware, and yields an
    empty build id and OS version. Decoding it first is what avoids that.
    Only type-00 data
    records carry image bytes; the addresses are dropped, because the caller
    scans for strings and never needs the layout. A gap between records can
    therefore join two strings, which costs nothing here.

    Returns b"" for anything that does not parse, so the caller can fall back
    to treating the file as raw bytes.
    """
    out = bytearray()

    for line in data.splitlines():
        line = line.strip()

        if not line.startswith(b":") or len(line) < 11:
            continue

        try:
            record = binascii.unhexlify(line[1:])
        except (binascii.Error, ValueError):
            continue

        count = record[0]

        if record[3] != 0x00 or len(record) < 5 + count:
            continue

        out += record[4:4 + count]

    return bytes(out)


def read_strings(path=None, minimum=6):
    """Printable strings in an image.

    The only thing a raw .bin yields -- it has no symbol table -- and still
    enough to recover the build id, the OS version and the device name. An
    Intel HEX image is decoded back to those bytes first.
    """
    path = path or _first_existing(IMAGE_CANDIDATES)

    if not path:
        return []

    try:
        with open(path, "rb") as handle:
            data = handle.read()
    except OSError:
        return []

    if data[:1] == b":":
        decoded = _intel_hex_bytes(data)

        # A .bin is free to start with 0x3A, so only treat it as HEX when the
        # records actually parse.
        if decoded:
            data = decoded

    return [m.decode("ascii", "replace") for m in PRINTABLE.findall(data)
            if len(m) >= minimum]


def read_memory(path=None):
    """Region sizes from the linker map, as {"FLASH": bytes, "RAM": bytes}."""
    path = path or _first_existing(MAP_CANDIDATES)

    if not path:
        return {}

    regions = {}

    try:
        with open(path, encoding="utf-8", errors="replace") as handle:
            for line in handle:
                match = MEMORY_REGION.match(line.strip())

                if match:
                    regions[match.group(1)] = int(match.group(2), 16)

                if len(regions) == 2:
                    break
    except OSError:
        return {}

    return regions


def facts(config_path=None, image_path=None, map_path=None):
    """Everything the build says about itself."""
    config = read_config(config_path)
    strings = read_strings(image_path)

    build_id = ""
    version = ""
    advertised = ""

    for text in strings:
        if not build_id:
            found = BUILD_ID.search(text)

            if found:
                build_id = found.group(0)

        if not version:
            found = ZEPHYR_VERSION.search(text)

            if found:
                version = found.group(1)

        if not advertised and DEVICE_NAME.match(text):
            advertised = text

    # .config first, because it is the exact value; the image second, because
    # a packaged build ships no .config and the name is sitting in the image
    # strings anyway.
    return {
        "build_id": build_id,
        "os_version": version,
        "device_name": config.get("CONFIG_BT_DEVICE_NAME", "") or advertised,
        "config": config,
        "memory": read_memory(map_path),
        "strings": len(strings),
    }


def _keys_for(description):
    lowered = description.lower()

    for phrases, keys in CLAIM_KEYS:
        if any(phrase in lowered for phrase in phrases):
            return keys

    return ()


def verify_change(change, known):
    """One change checked against the build's own values.

    Only a change that states a number can be checked at all: "improved
    reconnection handling" has nothing to compare, and saying so is the
    honest answer rather than a warning nobody can act on.
    """
    values = change.get("values") or {}
    claimed = values.get("to")

    if claimed is None:
        return {
            "verdict": UNVERIFIABLE,
            "detail": "the note states no value to check",
        }

    keys = _keys_for(change.get("description", ""))

    if not keys:
        return {
            "verdict": UNVERIFIABLE,
            "detail": "no build setting is known to hold this value",
        }

    for key in keys:
        if key not in known:
            continue

        raw = known[key]

        try:
            actual = int(raw)
        except (TypeError, ValueError):
            continue

        if actual == claimed:
            return {
                "verdict": CONFIRMED,
                "detail": "{} is {}".format(key, actual),
                "key": key,
                "actual": actual,
                "claimed": claimed,
            }

        return {
            "verdict": CONTRADICTED,
            "detail": "the note claims {} but {} is {}".format(
                claimed, key, actual),
            "key": key,
            "actual": actual,
            "claimed": claimed,
        }

    return {
        "verdict": UNVERIFIABLE,
        "detail": "the build records none of {}".format(", ".join(keys)),
    }


def verify(spec, known=None):
    """Every change in a specification, checked against the build."""
    known = read_config() if known is None else known

    checked = []

    for change in spec.get("changes", []):
        result = dict(change)
        result["verification"] = verify_change(change, known)
        checked.append(result)

    return checked


def contradictions(checked):
    return [c for c in checked
            if c["verification"]["verdict"] == CONTRADICTED]


def format_report(checked):
    lines = []

    for change in checked:
        verdict = change["verification"]["verdict"]
        mark = {CONFIRMED: "OK  ", CONTRADICTED: "WARN", UNVERIFIABLE: "--  "}[verdict]

        lines.append("{} {:<13} {}".format(mark, verdict, change["description"]))
        lines.append("              {}".format(change["verification"]["detail"]))

    flagged = len([c for c in checked
                   if c["verification"]["verdict"] == CONTRADICTED])

    lines.append("")

    # The wording is "the build's settings": every verdict above comes from
    # verify_change, which compares against the .config and nothing else.
    # With no build tree there is no .config and nothing is checkable, so
    # the count is printed beside the verdict line and the third branch
    # below says outright that nothing could be checked.
    checkable = len([c for c in checked
                     if c["verification"]["verdict"] != UNVERIFIABLE])

    if flagged:
        lines.append("{} release-note change(s) contradict the build's "
                     "settings.".format(flagged))
    elif checkable:
        lines.append("No release-note change contradicts the build's "
                     "settings ({} of {} could be checked).".format(
                         checkable, len(checked)))
    else:
        lines.append("No release-note change could be checked against the "
                     "build's settings; none was confirmed or contradicted.")

    return "\n".join(lines)


def note_text_for_build(text, config_path=None):
    """The part of a release note that describes the build being checked.

    A release-note file accumulates. Read whole, every entry it has ever
    carried is checked against today's .config, so a value that moved two
    releases ago is reported as CONTRADICTED for ever -- the note is right
    and the tool says it is wrong, which is the one verdict that has to be
    trustworthy. canonical.note_section already calls reading such a file
    whole a bug on its own side; this is the same bug on this one.

    Falls back to the whole text when no section names the build, which is
    what a single-entry note and a note read without an image both look
    like.
    """
    from regression.ai_engine import canonical

    section, _ = canonical.note_section(text, config_path)

    return section or text


def main(argv=None):
    import argparse

    from regression.ai_engine import change_spec

    parser = argparse.ArgumentParser(
        description="Check a release note against the build's .config")
    parser.add_argument(
        "--note", required=True,
        help="release note to check; its claims are what get a verdict")
    parser.add_argument(
        "--config", default=None,
        help="the build's .config to check against; found under the "
             "NRF_Firmware build tree, or named by " + CONFIG_ENV +
             ", when this is left out")
    parser.add_argument(
        "--whole-note", action="store_true",
        help="check every entry in the note, not just this build's section")

    args = parser.parse_args(argv)

    known = read_config(args.config)
    built = facts(args.config)

    print("Build id   :", built["build_id"] or "not found in the image")
    print("OS version :", built["os_version"] or "not found")
    print("Device name:", built["device_name"] or "not recorded")
    print("Settings   :", len(known))
    print("Memory     :", built["memory"] or "no map file")
    print()

    with open(args.note, encoding="utf-8", errors="replace") as handle:
        text = handle.read()

    if not args.whole_note:
        text = note_text_for_build(text, args.config)

    print(format_report(verify(change_spec.extract(text), known)))

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
