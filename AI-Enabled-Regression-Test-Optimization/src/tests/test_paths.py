# Copyright (c) 2026 Prajnaana Technologies Pvt. Ltd.
# Original author: Dhanya Shree S
# SPDX-License-Identifier: MIT
#
# Licensed under the MIT License. See LICENSE in the project root.

"""Tests for path resolution and the one thing this module prints.

Importing regression.paths is enough to print, because BASE_DIR is resolved
at import. Anything it prints therefore lands in front of whatever the entry
point writes, which is why it must not use stdout.
"""

from regression import paths


def test_the_fallback_notice_goes_to_stderr_not_stdout(monkeypatch, capsys):
    """canonical with no --out has to stay valid JSON in a read-only install.

    Left without --out, canonical prints the document to stdout, so the
    notice paths.py prints at import goes to stderr.
    """
    monkeypatch.setattr(paths, "APP_DIR", r"C:\Program Files\App")
    monkeypatch.setattr(paths, "BASE_DIR", r"C:\Users\someone\AppData\Local")

    assert paths.announce_fallback() is True

    captured = capsys.readouterr()

    assert captured.out == "", "nothing may be written to stdout"
    assert "Cannot write beside the application" in captured.err
    assert r"C:\Users\someone\AppData\Local" in captured.err


def test_nothing_is_said_when_output_goes_where_it_should(monkeypatch, capsys):
    """The usual case: a source checkout, or an exe in a writable folder."""
    monkeypatch.setattr(paths, "APP_DIR", r"C:\work\app")
    monkeypatch.setattr(paths, "BASE_DIR", r"C:\work\app")

    assert paths.announce_fallback() is False

    captured = capsys.readouterr()

    assert captured.out == ""
    assert captured.err == ""
