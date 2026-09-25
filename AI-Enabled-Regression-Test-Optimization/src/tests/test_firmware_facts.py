# Copyright (c) 2026 Prajnaana Technologies Pvt. Ltd.
# Original author: Dhanya Shree S
# SPDX-License-Identifier: MIT
#
# Licensed under the MIT License. See LICENSE in the project root.

"""Tests for checking a release note against the build's .config.

The rule these enforce: UNVERIFIABLE is not a failure. Most sentences in a
release note describe behaviour no build artefact records, and reporting
those as warnings would train everyone to ignore warnings -- taking the one
real CONTRADICTED down with them.
"""

from regression.ai_engine.change_spec import extract
from regression.change_detection import firmware_facts as ff

BUILD = {
    "CONFIG_BT_MAX_CONN": "1",
    "CONFIG_BT_L2CAP_TX_MTU": "247",
    "CONFIG_BT_DEVICE_NAME": "Zephyr_Earbuds",
    "CONFIG_MAIN_STACK_SIZE": "4096",
}


def verdicts(note, known=BUILD):
    return [c["verification"]["verdict"] for c in ff.verify(extract(note), known)]


# --------------------------------------------------------------------------
# The three verdicts
# --------------------------------------------------------------------------

def test_a_claim_the_build_agrees_with_is_confirmed():
    assert verdicts("- Increased maximum connections to 1.") == [ff.CONFIRMED]


def test_a_claim_the_build_disagrees_with_is_contradicted():
    """The warning this module exists to raise."""
    checked = ff.verify(
        extract("- Increased BLE packet size to 128 bytes."), BUILD)
    result = checked[0]["verification"]

    assert result["verdict"] == ff.CONTRADICTED
    assert result["claimed"] == 128
    assert result["actual"] == 247


def test_a_claim_with_no_number_is_unverifiable_not_failed():
    assert verdicts("- Improved BLE reconnection handling.") == [ff.UNVERIFIABLE]


def test_a_number_no_build_setting_holds_is_unverifiable():
    """A wrong mapping is worse than a missing one: it sends someone hunting
    a defect that does not exist."""
    assert verdicts("- Reduced boot time to 300 ms.") == [ff.UNVERIFIABLE]


def test_a_setting_absent_from_this_build_is_unverifiable():
    assert verdicts("- Raised the main stack size to 8192 bytes.", {}) == [
        ff.UNVERIFIABLE]


# --------------------------------------------------------------------------
# Reporting
# --------------------------------------------------------------------------

def test_only_contradictions_are_counted_as_flags():
    note = """- Improved BLE reconnection handling.
- Increased maximum connections to 1.
- Increased BLE packet size to 128 bytes.
"""
    checked = ff.verify(extract(note), BUILD)

    assert len(ff.contradictions(checked)) == 1


def test_a_clean_check_says_so_rather_than_staying_silent():
    checked = ff.verify(extract("- Increased maximum connections to 1."), BUILD)

    assert "No release-note change contradicts" in ff.format_report(checked)


def test_the_report_names_the_setting_it_checked():
    checked = ff.verify(
        extract("- Increased BLE packet size to 128 bytes."), BUILD)

    assert "CONFIG_BT_L2CAP_TX_MTU" in ff.format_report(checked)


# --------------------------------------------------------------------------
# Reading facts out of the build
# --------------------------------------------------------------------------

def test_config_values_lose_their_quotes(tmp_path):
    path = tmp_path / ".config"
    path.write_text(
        '# a comment\nCONFIG_BT_DEVICE_NAME="Zephyr_Earbuds"\nCONFIG_BT_MAX_CONN=1\n',
        encoding="utf-8",
    )

    values = ff.read_config(str(path))

    assert values["CONFIG_BT_DEVICE_NAME"] == "Zephyr_Earbuds"
    assert values["CONFIG_BT_MAX_CONN"] == "1"


def test_a_missing_config_is_empty_not_fatal(tmp_path):
    assert ff.read_config(str(tmp_path / "absent")) == {}


def test_strings_come_out_of_a_raw_image(tmp_path):
    """A .bin has no symbol table, so strings are the floor, not the ceiling."""
    path = tmp_path / "firmware.bin"
    path.write_bytes(b"\x00\x01Zephyr_Earbuds\x00\xff\xfeshort\x00")

    found = ff.read_strings(str(path))

    assert "Zephyr_Earbuds" in found


def test_memory_regions_are_read_from_the_map(tmp_path):
    path = tmp_path / "zephyr.map"
    path.write_text(
        "FLASH            0x00000000         0x00100000         xr\n"
        "RAM              0x20000000         0x00040000         xw\n",
        encoding="utf-8",
    )

    assert ff.read_memory(str(path)) == {"FLASH": 1048576, "RAM": 262144}


# --------------------------------------------------------------------------
# Which part of the release note gets checked
# --------------------------------------------------------------------------

ACCUMULATED_NOTE = """# Firmware release notes

## aaaaaaa+11111111

- Increased BLE packet size to 128 bytes.

## bbbbbbb+22222222

- Increased BLE packet size to 247 bytes.
"""


def test_only_this_build_s_section_of_the_note_is_checked(monkeypatch):
    """A release-note file accumulates; today's image answers for today.

    canonical.note_section reads only this build's section on its own side,
    and the --note CLI reads only the matching section too.
    """
    monkeypatch.setenv("HA_BUILD_ID", "bbbbbbb+22222222")

    text = ff.note_text_for_build(ACCUMULATED_NOTE)

    assert "247 bytes" in text
    assert "128 bytes" not in text
    assert verdicts(text) == [ff.CONFIRMED]


def test_a_note_with_no_section_for_this_build_is_read_whole(monkeypatch):
    """A single-entry note, and a note read with no image, must still work."""
    monkeypatch.setenv("HA_BUILD_ID", "no-such-build+deadbeef")

    text = ff.note_text_for_build("- Increased maximum connections to 1.")

    assert "maximum connections" in text


def test_a_report_that_could_check_nothing_does_not_claim_a_clean_bill():
    """With no build tree every claim is UNVERIFIABLE, never a clean bill.

    The verdicts rest on the .config alone.
    """
    checked = ff.verify(extract("- Increased maximum connections to 1."), {})

    report = ff.format_report(checked)

    assert "No release-note change contradicts" not in report
    assert "none was confirmed or contradicted" in report


def test_a_report_that_checked_something_says_how_much():
    checked = ff.verify(
        extract("- Improved BLE reconnection handling.\n"
                "- Increased maximum connections to 1.\n"),
        BUILD)

    report = ff.format_report(checked)

    assert "No release-note change contradicts" in report
    assert "1 of 2 could be checked" in report


# --------------------------------------------------------------------------
# Where the build artefacts are looked for
# --------------------------------------------------------------------------

def _install(tmp_path, monkeypatch, image=b"build v1.0+9e4947d1 here"):
    """A read-only install: the application in one folder, output in another.

    BASE_DIR moves to the writable fallback (LOCALAPPDATA in the real case),
    and the image and the .config stay beside the application in APP_DIR.
    """
    app = tmp_path / "Program Files" / "App"
    firmware = app / "NRF_Firmware"
    build = firmware / "build" / "zephyr"
    build.mkdir(parents=True)

    (firmware / "firmware.bin").write_bytes(image)
    (build / ".config").write_text(
        'CONFIG_BT_MAX_CONN=1\nCONFIG_BT_DEVICE_NAME="Zephyr_Earbuds"\n',
        encoding="utf-8")

    output = tmp_path / "LocalAppData" / "AI_Enabled_Regression_Test_Optimization"
    output.mkdir(parents=True)

    monkeypatch.setattr(ff, "BASE_DIR", str(output))
    monkeypatch.setattr(ff, "APP_DIR", str(app))
    monkeypatch.setattr(ff, "bundle_dir", lambda: str(app))
    monkeypatch.delenv(ff.CONFIG_ENV, raising=False)

    return app


def test_the_image_is_found_beside_the_application_not_only_under_base_dir(
        tmp_path, monkeypatch):
    """A read-only install must read the same build as a writable one.

    Dropped into Program Files, BASE_DIR moves to the writable fallback --
    a folder that has never held a firmware image -- so the image and the
    .config are looked for beside the application as well.
    """
    app = _install(tmp_path, monkeypatch)

    assert ff._first_existing(ff.IMAGE_CANDIDATES) == str(
        app / "NRF_Firmware" / "firmware.bin")
    assert ff._first_existing(ff.CONFIG_CANDIDATES, ff.CONFIG_ENV) == str(
        app / "NRF_Firmware" / "build" / "zephyr" / ".config")

    built = ff.facts()

    assert built["build_id"] == "v1.0+9e4947d1"
    assert built["config"]["CONFIG_BT_MAX_CONN"] == "1"


def test_base_dir_still_wins_when_it_has_the_artefacts(tmp_path, monkeypatch):
    """A writable checkout is unchanged: BASE_DIR is searched first."""
    _install(tmp_path, monkeypatch)

    output = tmp_path / "LocalAppData" / "AI_Enabled_Regression_Test_Optimization"
    beside = output / "NRF_Firmware"
    beside.mkdir(parents=True)
    (beside / "firmware.bin").write_bytes(b"build v9.9+deadbeef here")

    assert ff.facts()["build_id"] == "v9.9+deadbeef"


def test_the_config_environment_override_still_beats_every_root(
        tmp_path, monkeypatch):
    """HA_FIRMWARE_CONFIG names a file outright, wherever it sits."""
    _install(tmp_path, monkeypatch)

    other = tmp_path / "elsewhere.config"
    other.write_text("CONFIG_BT_MAX_CONN=4\n", encoding="utf-8")
    monkeypatch.setenv(ff.CONFIG_ENV, str(other))

    assert ff.read_config()["CONFIG_BT_MAX_CONN"] == "4"
