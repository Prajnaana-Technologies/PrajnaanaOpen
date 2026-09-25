# Copyright (c) 2026 Prajnaana Technologies Pvt. Ltd.
# Original author: Dhanya Shree S
# SPDX-License-Identifier: MIT
#
# Licensed under the MIT License. See LICENSE in the project root.

"""Per-test results and per-test log files.

pytest's console output is one wall of text. For a regression bench you want
to know, per test, whether it passed and why it did not -- and you want that
on disk afterwards, one file per test, so a failure can be attached to a bug
report without hunting through a transcript.

The orchestrator runs pytest with a JUnit XML report, this module turns that
into records, writes one log file per test, and prints a machine-readable line
per test that the dashboard parses.

Layout produced::

    regression/logs/build_20260909_143000/
        results.xml
        summary.txt
        tests/
            test_connection.log
            test_audio_dsp.log
            ...
"""

import os
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from datetime import datetime

# Printed once per test so the dashboard can build its table without needing
# to find or parse the XML itself. Fields are pipe separated.
RESULT_MARKER = "TEST_RESULT"

PASSED = "PASSED"
FAILED = "FAILED"
ERROR = "ERROR"
SKIPPED = "SKIPPED"


@dataclass
class TestRecord:
    name: str
    status: str
    duration: float = 0.0
    message: str = ""
    detail: str = ""
    output: str = ""
    log_path: str = ""

    @property
    def ok(self):
        return self.status in (PASSED, SKIPPED)


@dataclass
class RunReport:
    run_dir: str
    records: list = field(default_factory=list)

    @property
    def passed(self):
        return sum(1 for r in self.records if r.status == PASSED)

    @property
    def failed(self):
        return sum(1 for r in self.records if r.status in (FAILED, ERROR))

    @property
    def skipped(self):
        return sum(1 for r in self.records if r.status == SKIPPED)

    @property
    def status(self):
        return "pass" if self.failed == 0 and self.records else "fail"


def new_run_dir(root=None):
    """Create regression/logs/build_<timestamp>/ and return its path."""
    if root is None:
        from regression.paths import LOGS_DIR

        root = LOGS_DIR

    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    path = os.path.join(root, "build_" + stamp)

    os.makedirs(os.path.join(path, "tests"), exist_ok=True)

    return path


def _text(node):
    return (node.text or "").strip() if node is not None else ""


def parse_junit(xml_path):
    """Turn pytest's JUnit XML into TestRecord objects."""
    records = []

    if not os.path.exists(xml_path):
        return records

    root = ET.parse(xml_path).getroot()

    # pytest emits <testsuites><testsuite>...; older versions emit <testsuite>.
    suites = root.iter("testsuite")

    for suite in suites:
        for case in suite.iter("testcase"):

            failure = case.find("failure")
            error = case.find("error")
            skipped = case.find("skipped")

            if error is not None:
                status, node = ERROR, error
            elif failure is not None:
                status, node = FAILED, failure
            elif skipped is not None:
                status, node = SKIPPED, skipped
            else:
                status, node = PASSED, None

            captured = "\n".join(
                _text(case.find(tag))
                for tag in ("system-out", "system-err")
                if _text(case.find(tag))
            )

            records.append(TestRecord(
                name=case.get("name", "?"),
                status=status,
                duration=float(case.get("time", 0.0) or 0.0),
                message=(node.get("message", "") if node is not None else ""),
                detail=_text(node),
                output=captured,
            ))

    return records


def write_test_logs(records, run_dir):
    """Write one log file per test. Returns the records, with log_path set."""
    tests_dir = os.path.join(run_dir, "tests")
    os.makedirs(tests_dir, exist_ok=True)

    for record in records:
        safe = "".join(c if c.isalnum() or c in "._-" else "_" for c in record.name)
        path = os.path.join(tests_dir, safe + ".log")

        lines = [
            "test:     " + record.name,
            "result:   " + record.status,
            "duration: {:.3f}s".format(record.duration),
            "when:     " + datetime.now().isoformat(timespec="seconds"),
            "",
        ]

        if record.message:
            lines += ["--- reason ---", record.message, ""]

        if record.detail:
            lines += ["--- traceback ---", record.detail, ""]

        if record.output:
            lines += ["--- captured output ---", record.output, ""]

        if not (record.message or record.detail or record.output):
            lines += ["(no output captured)", ""]

        with open(path, "w", encoding="utf-8") as handle:
            handle.write("\n".join(lines))

        record.log_path = path

    return records


def write_summary(report):
    """Write summary.txt and return its path."""
    path = os.path.join(report.run_dir, "summary.txt")

    width = max([len(r.name) for r in report.records] + [4])

    lines = [
        "Regression run {}".format(os.path.basename(report.run_dir)),
        datetime.now().isoformat(timespec="seconds"),
        "",
        "{}  {}  {}".format("TEST".ljust(width), "RESULT ", "TIME"),
        "-" * (width + 18),
    ]

    for record in report.records:
        lines.append("{}  {}  {:>6.2f}s".format(
            record.name.ljust(width), record.status.ljust(7), record.duration
        ))

    lines += [
        "",
        "passed {}   failed {}   skipped {}".format(
            report.passed, report.failed, report.skipped
        ),
        "overall: " + report.status.upper(),
    ]

    with open(path, "w", encoding="utf-8") as handle:
        handle.write("\n".join(lines))

    return path


def emit_results(report):
    """Print one parseable line per test, then a human-readable table."""
    for record in report.records:
        print("{}|{}|{}|{:.3f}|{}".format(
            RESULT_MARKER, record.name, record.status,
            record.duration, record.log_path,
        ))

    width = max([len(r.name) for r in report.records] + [4])

    print("\n--- TEST RESULTS ---")
    for record in report.records:
        print("  {}  {}  {:>6.2f}s".format(
            record.name.ljust(width), record.status.ljust(7), record.duration
        ))

    print("\n  passed {}   failed {}   skipped {}".format(
        report.passed, report.failed, report.skipped
    ))
    print("  logs: {}".format(os.path.join(report.run_dir, "tests")))


def parse_result_line(line):
    """Inverse of emit_results, for the dashboard. Returns a dict or None."""
    line = line.strip()

    if not line.startswith(RESULT_MARKER + "|"):
        return None

    parts = line.split("|")

    if len(parts) < 5:
        return None

    try:
        duration = float(parts[3])
    except ValueError:
        duration = 0.0

    return {
        "name": parts[1],
        "status": parts[2],
        "duration": duration,
        "log_path": parts[4],
    }
