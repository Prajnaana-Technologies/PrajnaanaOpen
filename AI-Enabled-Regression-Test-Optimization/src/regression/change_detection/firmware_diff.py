# Copyright (c) 2026 Prajnaana Technologies Pvt. Ltd.
# Original author: Dhanya Shree S
# SPDX-License-Identifier: MIT
#
# Licensed under the MIT License. See LICENSE in the project root.

"""Compare two firmware images and say which subsystems changed.

Reading the source tree tells you what the developer has been editing. It does
not tell you what is on the board: flash an image, edit a file, run the tests,
and a source-based comparison describes code the device is not executing.

This reads the built images instead. Each build is reduced to a fingerprint --
every symbol and its size -- and two fingerprints are compared. A function that
appeared, vanished or changed size is a function that changed, whatever the
source tree currently says.

    build A                build B
    wdrc_process  288  ->  wdrc_process  304      -> audio dsp changed
    ha_telemetry_init  72 -> (absent)             -> telemetry removed

Works on a dirty build, where git has no commit to diff against, and needs no
repository at all. What it cannot do is explain *why* something changed -- for
that the git path is better, so the two are complementary rather than rivals.

    python -m regression.change_detection.firmware_diff --save baseline.json
    python -m regression.change_detection.firmware_diff --against baseline.json
    python -m regression.change_detection.firmware_diff --save-baseline
"""

import hashlib
import json
import os
import re
import subprocess

from regression.paths import BASE_DIR, asset, in_base

# Where a built image usually sits, relative to BASE_DIR -- src/ in a
# source run, the folder holding the .exe in a frozen one.
ELF_CANDIDATES = (
    os.path.join("NRF_Firmware", "build", "NRF_Firmware", "zephyr", "zephyr.elf"),
    os.path.join("NRF_Firmware", "build", "zephyr", "zephyr.elf"),
)

# Where the loadable image sits. The ELF is what nm reads; this is what is
# actually flashed, and it is what gets hashed -- see image_hash.
BIN_CANDIDATES = (
    os.path.join("NRF_Firmware", "build", "NRF_Firmware", "zephyr", "zephyr.bin"),
    os.path.join("NRF_Firmware", "build", "zephyr", "zephyr.bin"),
    os.path.join("NRF_Firmware", "firmware.bin"),
)

ELF_ENV = "HA_FIRMWARE_ELF"
BIN_ENV = "HA_FIRMWARE_BIN"
NM_ENV = "HA_NM"

# Where per-build fingerprints are kept, so the next run has something to
# compare against.
FINGERPRINT_DIR = os.path.join("regression", "artifacts", "firmware")

# A fingerprint of the image this release was built against, bundled
# read-only inside the build: paths.asset resolves it under
# bin/_internal/regression/artifacts/ in the onedir exe, NOT beside the
# executable. A packaged build has no NRF_Firmware tree and no run
# history. Generated at build time by --save-baseline.
BASELINE_PARTS = ("regression", "artifacts", "firmware", "baseline.json")
BASELINE_ID = "shipped baseline"

# Stored fingerprints carry the symbols plus what identifies the image, so a
# later run can tell "no symbol moved" from "the same image". A file in the
# earlier schema is a bare {symbol: size} map, and still loads.
RECORD_SCHEMA = "regression.firmware_fingerprint/2"

# Symbol name fragment -> the subsystem it belongs to. The words on the right
# are the planner's vocabulary.
SYMBOL_SUBSYSTEMS = (
    ("wdrc", "audio dsp"),
    ("ha_telemetry", "bluetooth telemetry"),
    ("telemetry_", "bluetooth telemetry"),
    ("snapshot", "bluetooth telemetry"),
    # audio_ring before audio_: first match wins, so the specific fragment
    # sits above the general one.
    ("audio_ring", "audio buffer overflow"),
    ("audio_", "audio"),
    ("smp_", "pairing security"),
    ("bond", "pairing security"),
    ("keys_", "pairing security"),
    ("bt_conn", "bluetooth link"),
    ("bt_le_adv", "bluetooth link"),
    ("bt_hci", "bluetooth link"),
    ("adv_", "bluetooth link"),
    ("bt_gatt", "bluetooth link"),
    ("k_work", "workqueue scheduling"),
    ("ring_buf", "audio buffer overflow"),
    ("settings_", "bluetooth configuration"),
    ("main", "bluetooth lifecycle"),
)

# Symbols below this size are usually compiler bookkeeping, and they churn for
# reasons that say nothing about the firmware.
MIN_SYMBOL_SIZE = 8

# A size change smaller than this is not worth reporting on its own.
MIN_SIZE_DELTA = 1


def find_nm():
    """The toolchain's nm, or None."""
    override = os.getenv(NM_ENV)

    if override and os.path.exists(override):
        return override

    roots = (
        os.path.join("C:", os.sep, "ncs", "toolchains"),
        os.path.join(os.sep, "opt", "zephyr-sdk"),
        os.path.expanduser("~/zephyr-sdk"),
    )

    for root in roots:
        if not os.path.isdir(root):
            continue

        for directory, _dirnames, filenames in os.walk(root):
            for name in filenames:
                if name in ("arm-zephyr-eabi-nm.exe", "arm-zephyr-eabi-nm"):
                    return os.path.join(directory, name)

    return None


def find_elf():
    """The built image, or None when the build tree is not present."""
    override = os.getenv(ELF_ENV)

    if override:
        return override if os.path.exists(override) else None

    for candidate in ELF_CANDIDATES:
        path = os.path.join(BASE_DIR, candidate)

        if os.path.exists(path):
            return path

    return None


def find_image(elf_path=None):
    """The loadable image to hash, or None when no build is present.

    The .bin beside the ELF being read, so the hash and the symbols describe
    the same build. When there is an ELF but no image beside it, the answer
    is None rather than the next image on the list: pairing one build's
    symbols with another build's bytes would report a change on a pair that
    was never compared, which is the failure this function exists to avoid.

    The candidate list is for the case with no ELF at all -- a packaged
    build, which ships the image and no build tree.
    """
    override = os.getenv(BIN_ENV)

    if override:
        return override if os.path.exists(override) else None

    elf_path = elf_path or os.getenv(ELF_ENV) or find_elf()

    if elf_path:
        beside = os.path.splitext(elf_path)[0] + ".bin"

        return beside if os.path.exists(beside) else None

    for candidate in BIN_CANDIDATES:
        path = os.path.join(BASE_DIR, candidate)

        if os.path.exists(path):
            return path

    return None


def unavailable_reason(elf_path=None, nm_path=None):
    """Which missing piece stops a fingerprint being taken.

    Three causes, three messages. A packaged build with no firmware tree and
    a source build with no toolchain are different problems, and one shared
    "no built image found" would be true of only one of them.
    """
    elf_path = elf_path or find_elf()
    nm_path = nm_path or find_nm()

    if not elf_path:
        return ("no built image found -- point {} at a zephyr.elf "
                "(a packaged build ships no firmware tree)".format(ELF_ENV))

    if not nm_path:
        return ("image found, but no arm-zephyr-eabi-nm to read it -- point "
                "{} at the toolchain's nm".format(NM_ENV))

    return "the image was found but no symbols could be read from it"


def fingerprint(elf_path=None, nm_path=None):
    """{symbol: size} for one image, or None when it cannot be read.

    Sizes rather than addresses: addresses shift when anything ahead of a
    symbol changes, so an address-based comparison reports the whole image as
    different for a one-line edit.
    """
    elf_path = elf_path or find_elf()
    nm_path = nm_path or find_nm()

    if not elf_path or not nm_path:
        return None

    try:
        result = subprocess.run(
            [nm_path, "--print-size", "--defined-only", elf_path],
            capture_output=True, text=True, timeout=60,
        )
    except (OSError, subprocess.SubprocessError):
        return None

    if result.returncode != 0:
        return None

    symbols = {}

    for line in result.stdout.splitlines():
        # "address size type name"
        parts = line.split()

        if len(parts) != 4:
            continue

        _address, size_hex, _kind, name = parts

        try:
            size = int(size_hex, 16)
        except ValueError:
            continue

        if size >= MIN_SYMBOL_SIZE:
            symbols[name] = size

    return symbols or None


def compare(old, new):
    """What differs between two fingerprints."""
    old = old or {}
    new = new or {}

    added = sorted(set(new) - set(old))
    removed = sorted(set(old) - set(new))

    resized = sorted(
        name for name in set(old) & set(new)
        if abs(new[name] - old[name]) >= MIN_SIZE_DELTA
    )

    return {"added": added, "removed": removed, "resized": resized}


def changed_symbols(old, new):
    difference = compare(old, new)

    return difference["added"] + difference["removed"] + difference["resized"]


def subsystems_for_symbols(names):
    """Subsystem words for a list of symbol names, most specific first."""
    found = []

    for name in names:
        lowered = name.lower()

        for fragment, subsystem in SYMBOL_SUBSYSTEMS:
            if fragment in lowered:
                if subsystem not in found:
                    found.append(subsystem)
                break

    return found


def describe(old, new):
    """(description, detail) from two fingerprints.

    A fingerprint is symbol names and sizes, so "nothing differs" means no
    function was added, removed or resized. It does not mean the image is
    unchanged: editing a constant -- WDRC_GAIN_CEIL from 0.85 to 0.90, a
    threshold, a timing value -- changes what the device does while every
    symbol stays exactly where it was. Calling that case "byte-identical"
    would claim more than the evidence supports and read as a reason to test
    less, so identity is decided by the image hash and the build id instead;
    see describe_against_last_tested.
    """
    if not old or not new:
        return None, "no previous firmware fingerprint to compare against"

    difference = compare(old, new)
    names = changed_symbols(old, new)

    if not names:
        return None, ("no symbol was added, removed or resized -- a change "
                      "inside a function of the same size would not show here")

    subsystems = subsystems_for_symbols(names)

    detail = "{} symbol(s) differ ({} new, {} gone, {} resized)".format(
        len(names), len(difference["added"]),
        len(difference["removed"]), len(difference["resized"]))

    interesting = [n for n in names if subsystems_for_symbols([n])][:5]

    if interesting:
        detail += ": " + ", ".join(interesting)

    if not subsystems:
        return None, detail + " -- none map to a known subsystem"

    return ", ".join(subsystems), detail


# ---------------------------------------------------------------------------
# Storage: keep a fingerprint per build so the next run can compare
# ---------------------------------------------------------------------------

def fingerprint_path(build_id):
    safe = re.sub(r"[^A-Za-z0-9._-]", "_", build_id or "unversioned")

    return in_base(os.path.join(FINGERPRINT_DIR, safe + ".json"))


def image_hash(elf_path=None):
    """SHA-256 of the loadable image, or "" when it cannot be read.

    The symbol fingerprint cannot see a change inside a function that keeps
    its size. This can: any edit at all changes the bytes. It is what makes
    "unchanged" a claim worth printing.

    The bin, not the ELF. An ELF hash covers the debug information, which
    records the absolute path of every source file, so identical sources
    built in two directories would hash differently and the comparison would
    report a change that has not happened. The release also ships the image
    and no ELF, so an ELF hash would leave baseline.json carrying a hash
    nobody can check against the delivered image. `elf_path` picks the
    build; the image beside it is what is hashed.
    """
    path = find_image(elf_path)

    if not path or not os.path.exists(path):
        return ""

    digest = hashlib.sha256()

    try:
        with open(path, "rb") as handle:
            for chunk in iter(lambda: handle.read(65536), b""):
                digest.update(chunk)
    except OSError:
        return ""

    return digest.hexdigest()


def record(symbols, build_id="", sha=""):
    """One stored fingerprint: the symbols, plus what identifies the image."""
    return {
        "schema": RECORD_SCHEMA,
        "build_id": build_id,
        "image_sha256": sha,
        "symbols": symbols or {},
    }


def symbols_of(stored):
    """The symbol map from a stored fingerprint, old format or new.

    Fingerprints written before the image hash existed are a bare
    {symbol: size} map. They still load; they just cannot answer the
    identity question, and identity_of reports that rather than guessing.
    """
    if not stored:
        return None

    if isinstance(stored, dict) and stored.get("schema") == RECORD_SCHEMA:
        return stored.get("symbols") or None

    return stored or None


def identity_of(stored):
    """(build_id, image_sha256) from a stored fingerprint; "" when unknown."""
    if isinstance(stored, dict) and stored.get("schema") == RECORD_SCHEMA:
        return stored.get("build_id", ""), stored.get("image_sha256", "")

    return "", ""


def save(build_id, symbols, sha=""):
    path = fingerprint_path(build_id)

    with open(path, "w", encoding="utf-8") as handle:
        json.dump(record(symbols, build_id, sha), handle, sort_keys=True)

    return path


def load(build_id):
    path = fingerprint_path(build_id)

    if not os.path.exists(path):
        return None

    try:
        with open(path, encoding="utf-8") as handle:
            return json.load(handle)
    except (OSError, ValueError):
        return None


def load_baseline():
    """The fingerprint bundled with the release, or None when none was."""
    path = asset(*BASELINE_PARTS)

    if not os.path.exists(path):
        return None

    try:
        with open(path, encoding="utf-8") as handle:
            return json.load(handle)
    except (OSError, ValueError):
        return None


def save_baseline(symbols, build_id="", sha=""):
    """Write the shipped baseline into the source tree, for the build to bundle.

    Carries the build id and image hash of the image it was taken from, so a
    packaged build can tell "the board runs what we shipped" from "no symbol
    moved" -- and so a baseline left behind by an older release is visible
    rather than silently treated as current.
    """
    path = in_base(os.path.join(*BASELINE_PARTS))

    with open(path, "w", encoding="utf-8") as handle:
        json.dump(record(symbols, build_id, sha), handle, sort_keys=True)

    return path


def previous_build_id(current):
    """The most recent recorded build that is not the one under test."""
    try:
        from regression import kpi

        runs = kpi.load_runs()
    except Exception:  # noqa: BLE001 - a missing history is not an error
        return None

    for run in reversed(runs):
        build = run.get("build_id", "")

        if build and build != "unversioned" and build != current:
            return build

    return None


def describe_against_last_tested(build_id, elf_path=None):
    """Compare the image on the bench with the one tested before it.

    Returns (description, detail). A None description does NOT only mean
    "no comparison was possible". The detail is what tells the cases apart,
    and there are these:

      * this image could not be fingerprinted (no nm, no image, no symbols)
      * nothing to compare against: no stored fingerprint and no baseline
      * the stored fingerprint carries no symbols, so there is no other side
      * the two images are byte-identical
      * the bytes differ but no symbol moved -- a constant changed
      * no symbol moved, no image hash to settle it, and the build ids differ
      * no symbol moved, no image hash to settle it, and nothing else to say
      * symbols did move, but none of them maps to a known subsystem

    Only the first three failed to compare. The rest did, and three of them
    even found a difference. They still answer None deliberately: the
    caller takes any description as the final word and stops asking, so
    naming a subsystem here would cut the source tree out of the decision.
    "Unknown" is the safe reading for every one of them -- never
    "no change".
    """
    current = fingerprint(elf_path)

    if current is None:
        return None, unavailable_reason(elf_path)

    current_sha = image_hash(elf_path)

    # Record this build either way, so the next run has a baseline even if
    # this one had nothing to compare against.
    save(build_id, current, current_sha)

    earlier_id = previous_build_id(build_id)
    earlier = load(earlier_id) if earlier_id else None

    if earlier is None:
        # No recorded history, which is the normal state of a packaged build:
        # the run history it would read lives in a source tree. The release
        # bundles a fingerprint of its own image so the first comparison has
        # something on the other side, rather than reporting the change as
        # unknown. Named in the detail line, so nobody mistakes the shipped
        # image for the last image tested on this bench.
        earlier = load_baseline()
        earlier_id = BASELINE_ID

    if earlier is None:
        return None, "first build recorded; nothing earlier to compare with"

    earlier_build, earlier_sha = identity_of(earlier)

    # Identity first. The symbol fingerprint cannot see a changed constant,
    # so only the image hash -- or the build id, which is a content hash of
    # the sources, the config and the build options -- can support the claim
    # that nothing changed.
    if current_sha and earlier_sha:
        if current_sha == earlier_sha:
            return None, "{} vs {}: the image is byte-identical".format(
                earlier_id, build_id)

        if not changed_symbols(symbols_of(earlier), current):
            # Same functions, same sizes, different bytes: a constant, a
            # threshold or a timing value moved.
            #
            # No description, so the caller falls through to git, exactly as
            # it does for git's own "unknown change". The caller takes any
            # description as the answer and stops asking, and this branch is
            # common, because the image embeds `git describe`, so the bytes
            # differ after every commit.
            return None, (
                "{} vs {}: no symbol moved, but the image differs -- a value "
                "inside the code changed".format(earlier_id, build_id))

    description, detail = describe(symbols_of(earlier), current)

    if description is None and not changed_symbols(symbols_of(earlier), current):
        # Symbols match, and no hash on one side or the other to settle it.
        # Say so: a fingerprint written before hashes existed cannot support
        # "unchanged".
        if earlier_build and build_id and earlier_build != build_id:
            # No description here either, for the reason above: the source
            # tree gets its turn instead of being cut off by a word that
            # names no subsystem.
            return None, (
                "{} vs {}: no symbol moved, but the build ids differ -- a "
                "value inside the code may have changed".format(
                    earlier_id, build_id))

        detail += "; no image hash stored for {}, so 'unchanged' is not " \
                  "confirmed".format(earlier_id)

    return description, "{} vs {}: {}".format(earlier_id, build_id, detail)


def main():
    import argparse

    parser = argparse.ArgumentParser(
        description="Compare firmware images by their symbols")
    parser.add_argument("--elf", default=None, help="image to fingerprint")
    parser.add_argument("--save", default=None, help="write the fingerprint here")
    parser.add_argument("--against", default=None,
                        help="compare with a saved fingerprint")
    parser.add_argument("--save-baseline", action="store_true",
                        help="write the baseline the packaged build bundles")

    args = parser.parse_args()

    elf = args.elf or find_elf()
    nm = find_nm()

    print("image:", elf or "not found")
    print("nm:   ", nm or "not found")

    if not elf or not nm:
        print(unavailable_reason(elf, nm))
        return 1

    current = fingerprint(elf, nm)

    if current is None:
        print("could not read symbols")
        return 1

    sha = image_hash(elf)
    build_id = os.getenv("HA_BUILD_ID", "")

    print("symbols:", len(current))
    print("hash:   ", sha[:12] or "not hashed")

    if args.save:
        with open(args.save, "w", encoding="utf-8") as handle:
            json.dump(record(current, build_id, sha), handle, sort_keys=True)

        print("saved to", args.save)

    if args.save_baseline:
        print("baseline written to", save_baseline(current, build_id, sha))

    if args.against:
        with open(args.against, encoding="utf-8") as handle:
            earlier = json.load(handle)

        earlier_build, earlier_sha = identity_of(earlier)

        if sha and earlier_sha and sha == earlier_sha:
            print()
            print("change: none -- the image is byte-identical")
            return 0

        description, detail = describe(symbols_of(earlier), current)

        print()
        print("change:", description or "not identified -- see why")
        print("why:   ", detail)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
