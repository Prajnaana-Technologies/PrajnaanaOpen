# Copyright (c) 2026 Prajnaana Technologies Pvt. Ltd.
# Original author: Dhanya Shree S
# SPDX-License-Identifier: MIT
#
# Licensed under the MIT License. See LICENSE in the project root.

"""Tkinter control panel for the regression pipeline.

    python -m regression.dashboard

Pick the planner (threshold rules, or a model), press Start. The pipeline runs
as a subprocess so the window stays responsive and Stop actually works; its
output is streamed into the log pane and parsed for the summary row.

The device must be powered on and advertising over BLE before you press Start.

Tkinter ships with Python, so this adds no dependency.
"""

import os
import queue
import re
import subprocess
import sys
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, simpledialog, ttk

from regression.ble.ble_audio import DEVICE_ADDRESS_ENV, DEVICE_NAME_MATCH, SCAN_TIMEOUT
from regression.paths import (
    BASE_DIR,
    CANONICAL_TEST_PATH,
    DEFAULT_TEST_SIGNAL,
    GENERATED_TEST_PATH,
    SIGNAL_ENV,
    default_release_note,
    default_requirements,
    is_frozen,
)
from regression.reporting import parse_result_line

REPO_ROOT = BASE_DIR

# A frozen build has no python interpreter to call, and
# "sys.executable -m regression.ai_engine.orchestrator" is meaningless
# inside a bundle. The exe re-invokes itself with this flag instead.
PIPELINE_FLAG = "--run-pipeline"

# A windowed build has no console, so every child process Windows starts
# gets one of its own -- which flashes up as a black window while a run
# finishes. CREATE_NO_WINDOW suppresses it. Harmless elsewhere: the flag
# only exists on Windows, so the dict is empty on every other platform.
NO_CONSOLE = (
    {"creationflags": getattr(subprocess, "CREATE_NO_WINDOW", 0)}
    if sys.platform.startswith("win") else {}
)

# Lines the orchestrator prints that feed the summary row.
PATTERNS = {
    "metrics": re.compile(r"^Metrics:\s*(\{.*\})"),
    "score": re.compile(r"^Calculated Risk Score:\s*(\d+)"),
    "slice": re.compile(r"Running (Full Regression|Targeted Regression|Minimal Sanity)"),
    "planner": re.compile(r"^Planner:\s*(\w+)"),
    "intensity": re.compile(r"^Selected intensity:\s*(\w+)"),
    "result": re.compile(r"^Result(?: after escalation)?:\s*(pass|fail)"),
    "fallback": re.compile(r"^\[planner\].*-- using rules"),
    # pytest -v prints this as each test completes, which is what lets the
    # table fill in live instead of all at once when the run ends.
    "live": re.compile(
        r"::(?P<name>.+?)\s+(?P<status>PASSED|FAILED|SKIPPED|ERROR|XFAIL|XPASS)\b"
    ),
}

# The device list starts with this placeholder rather than an "Auto"
# entry. Choosing the board explicitly is required, so that a run can
# never silently target whichever board happened to answer first.
SELECT_DEVICE = "-- select a device --"

# Audio captured after a run, so the DSP can be listened to rather than
# only measured. Written by tools/dsp_listen.py into BASE_DIR -- src/ in a
# source run, the folder holding the .exe in a frozen build.
BEFORE_WAV = os.path.join(BASE_DIR, "dsp_before.wav")
AFTER_WAV = os.path.join(BASE_DIR, "dsp_after.wav")

# Short by default: long enough to hear the DSP without making every run
# wait on the capture.
RECORD_SECONDS = 10.0

SLICE_LABEL = {
    "Full Regression": "FULL",
    "Targeted Regression": "TARGETED",
    "Minimal Sanity": "SANITY",
}


class Dashboard(ttk.Frame):

    def __init__(self, master):
        super().__init__(master, padding=10)
        self.grid(sticky="nsew")

        master.columnconfigure(0, weight=1)
        master.rowconfigure(0, weight=1)
        self.columnconfigure(0, weight=1)
        self.rowconfigure(2, weight=3)
        self.rowconfigure(3, weight=2)

        self.proc = None
        self.lines = queue.Queue()

        self.planner = tk.StringVar(value="rules")
        self.engine = tk.StringVar(value="anthropic")
        self.engine_label = tk.StringVar(value="anthropic")
        # Held in this process only. It reaches a run through the child
        # process environment, and goes when the window does: there is no
        # store, so nothing has to be cleared and nothing can be left
        # behind on a shared machine.
        self.api_key = tk.StringVar()
        self.build_id = tk.StringVar(value=self._last_build_id())
        self.signal = tk.StringVar(value=DEFAULT_TEST_SIGNAL)
        self.release_note = tk.StringVar(value=default_release_note())
        self.requirements = tk.StringVar(value=default_requirements())
        self.device = tk.StringVar(value=SELECT_DEVICE)
        self.record_audio = tk.BooleanVar(value=False)

        # "pipeline" or "record" -- which subprocess just ended.
        self._phase = None

        # Label shown in the dropdown -> BLE address. The placeholder is
        # absent from this map, which is how start() detects no choice.
        self.device_addresses = {}
        self.scan_results = queue.Queue()

        self._build_controls()
        self._build_summary()
        self._build_log()
        self._build_framework()

        self._on_planner_change()
        self._describe_signal()
        self._refresh_play_buttons()

        self.after(80, self._drain)

    # ------------------------------------------------------------------
    # Layout
    # ------------------------------------------------------------------
    def _build_controls(self):
        box = ttk.LabelFrame(self, text="Run configuration", padding=10)
        box.grid(row=0, column=0, sticky="ew")
        box.columnconfigure(1, weight=1)

        # --- planner: the with/without API switch -----------------------
        ttk.Label(box, text="Planner:").grid(row=0, column=0, sticky="w")

        planner_row = ttk.Frame(box)
        planner_row.grid(row=0, column=1, sticky="w")

        ttk.Radiobutton(
            planner_row, text="Without API  (threshold rules)",
            variable=self.planner, value="rules",
            command=self._on_planner_change,
        ).pack(side="left")

        ttk.Radiobutton(
            # "an AI model", not a vendor: the Engine dropdown below is
            # what chooses the model, so naming one here would be wrong
            # for every other choice.
            planner_row, text="With API  (an AI model plans the run)",
            variable=self.planner, value="llm",
            command=self._on_planner_change,
        ).pack(side="left", padx=(16, 0))

        # --- engine: a dropdown, like the device picker -----------------
        ttk.Label(box, text="Engine:").grid(row=1, column=0, sticky="w")

        engine_row = ttk.Frame(box)
        engine_row.grid(row=1, column=1, sticky="ew", pady=(0, 8))
        # Engine and Build ID share this row. Both are short.
        engine_row.columnconfigure(0, weight=2)
        engine_row.columnconfigure(3, weight=3)

        # Label shown in the list -> engine name. Engines the design names but
        # that have no adapter are listed and marked rather than hidden: a
        # missing option reads as an oversight, a marked one states the
        # position. Choosing one is refused with an explanation.
        self.engine_labels = self._engine_choices()

        self.engine_box = ttk.Combobox(
            engine_row, textvariable=self.engine_label, state="readonly",
            values=list(self.engine_labels),
        )
        self.engine_box.grid(row=0, column=0, sticky="ew")
        self.engine_box.bind("<<ComboboxSelected>>", self._on_engine_change)

        ttk.Label(engine_row, text="Build ID:").grid(
            row=0, column=2, sticky="e", padx=(16, 6))

        ttk.Entry(engine_row, textvariable=self.build_id).grid(
            row=0, column=3, sticky="ew")

        ttk.Label(
            engine_row,
            text="label for the firmware on the board; two runs sharing one "
                 "are compared for determinism",
            foreground="#666",
        ).grid(row=1, column=3, sticky="w", pady=(2, 0))

        # Sits under the Engine box, mirroring the Build ID hint under its
        # own box. A row of its own would cost a line of height for one
        # line of grey text.
        self.api_status = ttk.Label(
            engine_row, text="", foreground="#666", wraplength=420)
        self.api_status.grid(row=1, column=0, sticky="w", pady=(2, 0))

        # --- API key -----------------------------------------------------
        # Its own row, gridded only on the With API path, so the rules path
        # carries no empty row where the key would go.
        self.key_label = ttk.Label(box, text="API key:")

        self.key_row = ttk.Frame(box)
        self.key_row.columnconfigure(0, weight=1)

        # show= masks it: the window is screenshotted for documentation.
        self.key_entry = ttk.Entry(
            self.key_row, textvariable=self.api_key, show="\u2022")
        self.key_entry.grid(row=0, column=0, sticky="ew")
        self.key_entry.bind("<Return>", lambda event: self._apply_api_key())

        ttk.Button(
            self.key_row, text="Use key", command=self._apply_api_key,
        ).grid(row=0, column=1, padx=(6, 0))

        self.key_hint = ttk.Label(
            self.key_row, text="", foreground="#666", wraplength=520)
        self.key_hint.grid(row=1, column=0, columnspan=3, sticky="w",
                           pady=(2, 0))

        # --- device ------------------------------------------------------
        # There is no free-text change box here, and there should not be:
        # typing a description per run is choosing the tests by hand, which
        # is the opposite of change-aware selection.
        #
        # A release note is a different thing. It is a statement about what
        # the release changed, written once and kept with the firmware, not
        # a per-run choice of what to exercise. It is also the only input
        # that works when all anyone has is a note and a .bin: a raw binary
        # carries no symbols, so the image comparison has nothing to read.
        #
        # Leave the field empty and the pipeline falls back to comparing
        # this firmware's symbols with the last build tested, which is the
        # better path whenever an ELF is available.

        ttk.Label(box, text="Device:").grid(row=3, column=0, sticky="w")

        device_row = ttk.Frame(box)
        device_row.grid(row=3, column=1, sticky="ew")
        device_row.columnconfigure(0, weight=1)

        self.device_box = ttk.Combobox(
            device_row, textvariable=self.device, state="readonly",
            values=[SELECT_DEVICE],
        )
        self.device_box.grid(row=0, column=0, sticky="ew")
        self.device_box.bind("<<ComboboxSelected>>", self._on_device_change)

        self.scan_btn = ttk.Button(
            device_row, text="Scan", command=self._scan_devices)
        self.scan_btn.grid(row=0, column=1, padx=(6, 0))

        self.device_info = ttk.Label(
            box,
            text="Press Scan, then choose the board to test.",
            foreground="#666",
        )
        self.device_info.grid(row=4, column=1, sticky="w", pady=(2, 8))

        # --- input audio ------------------------------------------------
        ttk.Label(box, text="Input file:").grid(row=5, column=0, sticky="w")

        signal_row = ttk.Frame(box)
        signal_row.grid(row=5, column=1, sticky="ew")
        signal_row.columnconfigure(0, weight=1)

        ttk.Entry(signal_row, textvariable=self.signal).grid(
            row=0, column=0, sticky="ew")

        ttk.Button(signal_row, text="Browse...", command=self._pick_signal).grid(
            row=0, column=1, padx=(6, 0))

        ttk.Button(signal_row, text="Default", command=self._reset_signal).grid(
            row=0, column=2, padx=(6, 0))

        self.signal_info = ttk.Label(box, text="", foreground="#666")
        self.signal_info.grid(row=6, column=1, sticky="w", pady=(2, 8))

        ttk.Label(box, text="Release note:").grid(row=7, column=0, sticky="w")

        note_row = ttk.Frame(box)
        note_row.grid(row=7, column=1, sticky="ew")
        note_row.columnconfigure(0, weight=1)

        ttk.Entry(note_row, textvariable=self.release_note).grid(
            row=0, column=0, sticky="ew")

        ttk.Button(note_row, text="Browse...",
                   command=self._pick_release_note).grid(
            row=0, column=1, padx=(6, 0))

        ttk.Button(note_row, text="Clear",
                   command=lambda: self.release_note.set("")).grid(
            row=0, column=2, padx=(6, 0))

        ttk.Label(
            box,
            text="When set, scenarios are selected from the note alone.",
            foreground="#666",
        ).grid(row=8, column=1, sticky="w", pady=(2, 2))

        ttk.Label(box, text="Requirements:").grid(row=9, column=0, sticky="w")

        req_row = ttk.Frame(box)
        req_row.grid(row=9, column=1, sticky="ew")
        req_row.columnconfigure(0, weight=1)

        ttk.Entry(req_row, textvariable=self.requirements).grid(
            row=0, column=0, sticky="ew")

        ttk.Button(req_row, text="Browse...",
                   command=self._pick_requirements).grid(
            row=0, column=1, padx=(6, 0))

        ttk.Button(req_row, text="Clear",
                   command=lambda: self.requirements.set("")).grid(
            row=0, column=2, padx=(6, 0))

        ttk.Label(
            box,
            text="Requirements generate tests that run every time, not only "
                 "when something changed.",
            foreground="#666",
        ).grid(row=10, column=1, sticky="w", pady=(2, 8))

        ttk.Label(
            box,
            text="The device must be powered on and advertising over BLE.",
            foreground="#666",
        ).grid(row=11, column=1, sticky="w")

        # --- buttons ----------------------------------------------------
        buttons = ttk.Frame(self, padding=(0, 10))
        buttons.grid(row=1, column=0, sticky="ew")

        self.start_btn = ttk.Button(
            buttons, text="START REGRESSION", command=self.start,
        )
        self.start_btn.pack(side="left")

        self.stop_btn = ttk.Button(
            buttons, text="Stop", command=self.stop, state="disabled",
        )
        self.stop_btn.pack(side="left", padx=(8, 0))

        ttk.Button(buttons, text="Clear log", command=self.clear).pack(
            side="left", padx=(8, 0)
        )

        # Offered only from a source checkout. Recording runs
        # tools/dsp_listen.py as `sys.executable -u <script>`, and in the
        # frozen build sys.executable IS this application and tools/ is not
        # shipped. Hidden rather than disabled: a
        # greyed-out control with no explanation reads as a defect, and
        # nothing in the packaged build can make it work.
        self.record_check = None

        if not is_frozen():
            self.record_check = ttk.Checkbutton(
                buttons,
                text="Record DSP audio after the run",
                variable=self.record_audio,
            )
            self.record_check.pack(side="left", padx=(20, 0))

        self.play_before_btn = ttk.Button(
            buttons, text="Play original", state="disabled",
            command=lambda: self._play(BEFORE_WAV))
        self.play_before_btn.pack(side="left", padx=(16, 0))

        self.play_after_btn = ttk.Button(
            buttons, text="Play processed", state="disabled",
            command=lambda: self._play(AFTER_WAV))
        self.play_after_btn.pack(side="left", padx=(6, 0))

    def _build_summary(self):
        box = ttk.LabelFrame(self, text="Test results", padding=10)
        box.grid(row=2, column=0, sticky="nsew")
        box.columnconfigure(0, weight=1)
        box.rowconfigure(2, weight=1)

        self.summary = ttk.Label(
            box, text="Not run yet", font=("TkDefaultFont", 10, "bold")
        )
        self.summary.grid(row=0, column=0, sticky="w", columnspan=2)

        self.metrics = ttk.Label(box, text="", foreground="#444")
        self.metrics.grid(row=1, column=0, sticky="w", columnspan=2, pady=(2, 6))

        self.tree = ttk.Treeview(
            box, columns=("result", "time"), show="tree headings", height=9,
        )
        self.tree.heading("#0", text="Test")
        self.tree.heading("result", text="Result")
        self.tree.heading("time", text="Time")
        self.tree.column("#0", width=340, anchor="w")
        self.tree.column("result", width=90, anchor="center")
        self.tree.column("time", width=80, anchor="e")
        self.tree.grid(row=2, column=0, sticky="nsew")

        bar = ttk.Scrollbar(box, orient="vertical", command=self.tree.yview)
        bar.grid(row=2, column=1, sticky="ns")
        self.tree.configure(yscrollcommand=bar.set)

        self.tree.tag_configure("PASSED", foreground="#0a7d2b")
        self.tree.tag_configure("FAILED", foreground="#c0271c")
        self.tree.tag_configure("ERROR", foreground="#c0271c")
        self.tree.tag_configure("SKIPPED", foreground="#a86400")

        self.tree.bind("<Double-1>", self._open_log)

        self.hint = ttk.Label(
            box,
            text="Double-click a test to open its log file.",
            foreground="#666",
        )
        self.hint.grid(row=3, column=0, sticky="w", pady=(6, 0))

        # test name -> log file written by regression.reporting
        self.log_paths = {}

        # test name -> treeview row, so live rows can be updated in place
        self.rows = {}

    def _open_log(self, event=None):
        selected = self.tree.focus()
        path = self.log_paths.get(selected)

        if not path or not os.path.exists(path):
            self.hint.configure(text="No log file for that row yet.")
            return

        try:
            if sys.platform.startswith("win"):
                os.startfile(path)          # noqa: S606
            else:
                subprocess.Popen(["xdg-open", path])
            self.hint.configure(text="Opened " + path)
        except Exception as exc:
            self.hint.configure(text="Could not open log: {}".format(exc))

    def _add_or_update(self, name, status, duration=None, log_path=None):
        """Insert a row the moment a test finishes, enrich it when the run ends.

        pytest -v gives the verdict live but no duration or log path; the
        TEST_RESULT lines printed afterwards carry both. Keying rows by test
        name lets the second pass fill in the first.
        """
        time_text = "{:.2f}s".format(duration) if duration is not None else "..."

        item = self.rows.get(name)

        if item is None:
            item = self.tree.insert(
                "", "end", text=name,
                values=(status, time_text), tags=(status,),
            )
            self.rows[name] = item
        else:
            self.tree.item(item, values=(status, time_text), tags=(status,))

        if log_path:
            self.log_paths[item] = log_path

        self.tree.see(item)

        self._update_running_tally()

    def _update_running_tally(self):
        counts = {}
        for item in self.rows.values():
            status = self.tree.item(item, "values")[0]
            counts[status] = counts.get(status, 0) + 1

        parts = ["{} {}".format(v, k.lower()) for k, v in sorted(counts.items())]
        self.summary.configure(text="Running...  " + "   ".join(parts))

    def _build_log(self):
        # Output and Framework share one row. The run log is what you watch
        # while a run is going; the framework panels are what you read when it
        # stops. Side by side, so neither hides the other.
        self.bottom = ttk.Frame(self)
        self.bottom.grid(row=3, column=0, sticky="nsew", pady=(10, 0))
        self.bottom.rowconfigure(0, weight=1)
        self.bottom.columnconfigure(0, weight=3)
        self.bottom.columnconfigure(1, weight=4)

        box = ttk.LabelFrame(self.bottom, text="Output", padding=6)
        box.grid(row=0, column=0, sticky="nsew", padx=(0, 6))
        box.columnconfigure(0, weight=1)
        box.rowconfigure(0, weight=1)

        self.log = tk.Text(box, wrap="none", height=10, font=("Consolas", 9))
        self.log.grid(row=0, column=0, sticky="nsew")

        scroll = ttk.Scrollbar(box, orient="vertical", command=self.log.yview)
        scroll.grid(row=0, column=1, sticky="ns")
        self.log.configure(yscrollcommand=scroll.set, state="disabled")

        self.log.tag_configure("pass", foreground="#0a7d2b")
        self.log.tag_configure("fail", foreground="#c0271c")
        self.log.tag_configure("info", foreground="#1a4fa0")
        self.log.tag_configure("warn", foreground="#a86400")

    # ------------------------------------------------------------------
    # Framework panel
    #
    # The tabs answer the questions a single run cannot: was this suite
    # reviewed, is the framework itself working, what is not covered, and is
    # it getting worse. Each refreshes on demand and after every run, and
    # each reports honestly when there is not enough data yet.
    # ------------------------------------------------------------------
    FRAMEWORK_TABS = (
        ("Governance", "_governance_text"),
        ("KPIs", "_kpi_text"),
        ("Coverage", "_coverage_text"),
        ("Trend", "_trend_text"),
    )

    def _build_framework(self):
        box = ttk.LabelFrame(self.bottom, text="Framework", padding=6)
        box.grid(row=0, column=1, sticky="nsew")
        box.columnconfigure(0, weight=1)
        box.rowconfigure(0, weight=1)

        book = ttk.Notebook(box)
        book.grid(row=0, column=0, sticky="nsew")

        for title, attribute in self.FRAMEWORK_TABS:
            frame = ttk.Frame(book, padding=4)
            frame.columnconfigure(0, weight=1)
            frame.rowconfigure(0, weight=1)

            text = tk.Text(frame, wrap="none", height=9,
                           font=("Consolas", 9), state="disabled")
            text.grid(row=0, column=0, sticky="nsew")

            bar = ttk.Scrollbar(frame, orient="vertical", command=text.yview)
            bar.grid(row=0, column=1, sticky="ns")
            text.configure(yscrollcommand=bar.set)

            text.tag_configure("good", foreground="#0a7d2b")
            text.tag_configure("bad", foreground="#c0271c")
            text.tag_configure("warn", foreground="#a86400")

            setattr(self, attribute, text)
            book.add(frame, text=title)

        row = ttk.Frame(box)
        row.grid(row=1, column=0, sticky="ew", pady=(6, 0))

        ttk.Button(row, text="Refresh", command=self.refresh_framework).pack(
            side="left")

        ttk.Button(row, text="Approve this suite",
                   command=self._approve_suite).pack(side="left", padx=(6, 0))

        self.framework_note = ttk.Label(row, text="", foreground="#666")
        self.framework_note.pack(side="left", padx=(10, 0))

        self.refresh_framework()

    def _set_panel(self, widget, body):
        """Replace a panel's contents, colouring the lines that matter."""
        widget.configure(state="normal")
        widget.delete("1.0", "end")

        for line in body.splitlines():
            tag = None
            lowered = line.lower()

            if "insufficient data" in lowered or "not approved" in lowered:
                tag = "warn"
            elif ("below target" in lowered or "regressed" in lowered
                    or "violation" in lowered or lowered.strip().startswith("!")):
                tag = "bad"
            elif "meets target" in lowered or "rules pass" in lowered:
                tag = "good"

            widget.insert("end", line + "\n", tag)

        widget.configure(state="disabled")

    def refresh_framework(self):
        """Recompute every tab. Safe to call with no run history at all."""
        panels = (
            ("_governance_text", self._governance_body),
            ("_kpi_text", self._kpi_body),
            ("_coverage_text", self._coverage_body),
            ("_trend_text", self._trend_body),
        )

        for attribute, builder in panels:
            widget = getattr(self, attribute, None)

            if widget is None:
                continue

            try:
                body = builder()
            except Exception as exc:  # noqa: BLE001 - a panel must never kill the GUI
                body = "Could not build this panel: {}".format(exc)

            self._set_panel(widget, body)

    # -- panel bodies ---------------------------------------------------

    def _governance_body(self):
        from regression import governance
        from regression.ai_engine import engines
        from regression.instrumentation import power_analyzer

        lines = []

        try:
            # The dropdown, not engines.policy_name(). That reads HA_ENGINE
            # from this process's environment, which the GUI never sets -- it
            # sets it on the pipeline subprocess.
            chosen = self.engine.get()

            lines.append("Engine policy: " + chosen)

            if self.planner.get() != "llm":
                lines.append("  not in use: planner is Without API "
                             "(threshold rules, no engine involved)")

            for entry in engines.describe_all():
                marker = "->" if entry.startswith(chosen) else "  "

                lines.append(" {} {}".format(marker, entry))
        except Exception as exc:  # noqa: BLE001
            lines.append("Engine: unavailable ({})".format(exc))

        lines += ["", "Power source: " + power_analyzer.source_description(), ""]

        # Every module a run executes, not just the planner's. An unapproved
        # module stops the run.
        modules = governance.generated_modules()

        if not modules:
            lines.append("No generated suite yet -- run the pipeline once.")
            return "\n".join(lines)

        for path in modules:
            violations = governance.check_file(path)
            record = governance.approval_for(path)

            lines.append("Generated suite: " + os.path.basename(path))
            lines.append("Fingerprint:     " + governance.fingerprint(path)[:12])

            if violations:
                lines.append("Acceptance:      {} violation(s)".format(
                    len(violations)))

                for violation in violations:
                    lines.append("  - " + str(violation))
            else:
                lines.append("Acceptance:      rules pass")

            if record:
                lines.append("Approval:        approved by {} at {}".format(
                    record.get("approved_by", "?"),
                    record.get("approved_at", "?")))
            else:
                lines.append("Approval:        not approved")
                lines.append("                 an unreviewed generated test "
                             "that asserts nothing")
                lines.append("                 passes forever and counts as "
                             "coverage")

            lines.append("")

        lines.append("Enforcement:     " + (
            "required (HA_REQUIRE_APPROVAL=1)"
            if governance.approval_required() else "advisory"))

        return "\n".join(lines)

    def _kpi_body(self):
        from regression import kpi

        return kpi.format_report()

    def _coverage_body(self):
        from regression import catalog

        return catalog.format_report()

    def _trend_body(self):
        from regression.analytics import trend

        return trend.format_report()

    def _approve_suite(self):
        """Record an approval against every generated module as it stands.

        Both modules are generated and both are executed, so both are
        approved together. Approving only the planner's module would leave
        the requirement suite running unapproved under
        HA_REQUIRE_APPROVAL=1.
        """
        from regression import governance

        modules = [path for path in (GENERATED_TEST_PATH, CANONICAL_TEST_PATH)
                   if os.path.exists(path)]

        if not modules:
            messagebox.showinfo(
                "Nothing to approve",
                "Run the pipeline once so there is a generated suite.")
            return

        # Named per module, so the reader can tell which suite to fix.
        violations = []

        for path in modules:
            name = os.path.basename(path)

            violations.extend("{}: {}".format(name, v)
                              for v in governance.check_file(path))

        if violations:
            messagebox.showerror(
                "Cannot approve",
                "The generated suites break {} acceptance rule(s):\n\n{}".format(
                    len(violations), "\n".join(violations[:8])))
            return

        who = simpledialog.askstring(
            "Approve suite",
            # governance.fingerprint leaves the run's METRICS line out, so
            # a rerun that measured different numbers keeps its approval.
            # Everything else in the module is covered.
            "Approving records your name against the module's test code\n"
            "(the run's METRICS line excluded).\n"
            "Editing the suite afterwards invalidates it.\n\n"
            "Approved by:",
            parent=self)

        if not who:
            return

        try:
            records = [governance.approve(path, approved_by=who)
                       for path in modules]
        except ValueError as exc:
            messagebox.showerror("Cannot approve", str(exc))
            return

        self.framework_note.configure(
            text="approved {} module(s) by {}".format(
                len(records), records[0]["approved_by"]))

        self.refresh_framework()

    # ------------------------------------------------------------------
    # Control state
    # ------------------------------------------------------------------
    NO_ADAPTER_SUFFIX = " (no adapter)"

    def _engine_choices(self):
        """Ordered {label: engine name} for the dropdown."""
        from regression.ai_engine import engines

        choices = {}

        for name in engines.available_engines():
            choices[name] = name

        for name in sorted(engines.NOT_IMPLEMENTED):
            choices[name + self.NO_ADAPTER_SUFFIX] = name

        return choices

    def _last_build_id(self):
        """The most recent recorded build id, so a repeat run matches it.

        Determinism compares runs sharing an id. A blank field does not
        tag the run 'unversioned' -- orchestrator.resolve_build_id reads
        the board when HA_BUILD_ID is empty, and 'unversioned' is only what
        remains when no board answered either. Offering the last recorded
        id still matters: it is what a bench with no board attached, or a
        board whose id cannot be read, has to fall back to, and without it
        those runs could never be compared with another.
        """
        try:
            from regression import kpi

            runs = kpi.load_runs()
        except Exception:  # noqa: BLE001 - a missing history is not an error
            return ""

        for run in reversed(runs):
            value = run.get("build_id", "")

            if value and value != "unversioned":
                return value

        return ""

    def _on_engine_change(self, event=None):
        """Apply the dropdown choice, refusing engines with no adapter."""
        from regression.ai_engine import engines

        chosen = self.engine_labels.get(self.engine_label.get())

        if chosen is None:
            return

        if chosen in engines.NOT_IMPLEMENTED:
            # Snap back to the engine that was actually in use, so the run
            # never starts against a selection that cannot work.
            self.engine_label.set(self.engine.get())

            self.api_status.configure(
                text="{} has no adapter: {}. Implemented: {}.".format(
                    chosen, engines.NOT_IMPLEMENTED[chosen],
                    ", ".join(engines.available_engines())),
                foreground="#a86400",
            )
            return

        self.engine.set(chosen)

        self._on_planner_change()

        if hasattr(self, "framework_note"):
            self.refresh_framework()

    # What each vendor's keys have looked like. A hint for catching a key
    # pasted against the wrong engine, not validation: vendors change these.
    KEY_PREFIXES = {
        "ANTHROPIC_API_KEY": ("sk-ant-",),
        "GEMINI_API_KEY": ("AIza", "AQ."),
    }

    def _key_looks_wrong(self, key):
        """A warning if this key looks like another engine's, else ""."""
        variable = self._key_env()
        expected = self.KEY_PREFIXES.get(variable)

        if not expected or key.startswith(tuple(expected)):
            return ""

        for other, prefixes in self.KEY_PREFIXES.items():
            if other != variable and key.startswith(tuple(prefixes)):
                engine = {"ANTHROPIC_API_KEY": "anthropic",
                          "GEMINI_API_KEY": "gemini"}[other]

                return ("This looks like a {} key, but Engine is set to "
                        "{}. Applied anyway - switch Engine to {} if that "
                        "is what you meant.".format(
                            engine, self.engine.get(), engine))

        return ("Applied, but this does not look like a {} key (they start "
                "with {}).".format(variable, " or ".join(expected)))

    def _key_env(self):
        """The variable the selected engine reads, or "" if it needs none."""
        from regression.ai_engine import engines

        try:
            return engines.get_engine(self.engine.get()).key_env
        except Exception:  # noqa: BLE001 - an unimplemented engine has none
            return ""

    def _show_key_box(self, shown):
        """Grid the key row on the With API path, remove it otherwise."""
        if not hasattr(self, "key_row"):
            return

        if shown:
            self.key_label.grid(row=2, column=0, sticky="nw", pady=(0, 8))
            self.key_row.grid(row=2, column=1, sticky="ew", pady=(0, 8))
            self._describe_key_state()
        else:
            self.key_label.grid_remove()
            self.key_row.grid_remove()

    def _describe_key_state(self):
        """Say where the key would come from, without showing any of it."""
        variable = self._key_env()

        if not variable:
            self.key_hint.configure(
                text="This engine has no adapter, so no key applies.")
            return

        if os.getenv(variable):
            self.key_hint.configure(
                text="{} is set for this session. Type here only to "
                     "replace it.".format(variable))
        else:
            self.key_hint.configure(
                text="Paste the {} key here, or set {} in the environment "
                     "before starting.".format(self.engine.get(), variable))

    def _apply_api_key(self):
        """Put the typed key into this process's environment.

        Runs are launched with a copy of os.environ, so setting it here is
        what carries it into the child. Nothing prints it: the Output pane
        is the run's log and is kept with the results.
        """
        variable = self._key_env()

        if not variable:
            return

        key = self.api_key.get().strip()

        if not key:
            self.key_hint.configure(
                text="Paste the key first, then press Use key.")
            return

        # The box writes to whichever engine is selected, so a key pasted
        # against the wrong one is set and then rejected by the vendor.
        # Prefixes are a hint, not a rule, so this warns and still applies.
        mismatch = self._key_looks_wrong(key)

        os.environ[variable] = key

        if mismatch:
            # Set after _on_planner_change, which calls _describe_key_state
            # and would otherwise overwrite the warning with the generic
            # hint.
            self._on_planner_change()
            self.key_hint.configure(text=mismatch, foreground="#a86400")
            self.api_key.set("")
            return

        self.key_hint.configure(foreground="#666")

        # Cleared from the widget once applied, so it is not sitting in a
        # box for the next screenshot. It stays in os.environ.
        self.api_key.set("")

        # Refresh the engine line first: it calls _describe_key_state, which
        # would otherwise overwrite the confirmation the user needs to see.
        self._on_planner_change()

        self.key_hint.configure(
            text="{} applied for this session. It is not saved "
                 "anywhere, and goes when this window closes.".format(
                     variable))

    def _set_engine_enabled(self, enabled):
        """The picker only means anything on the With API path."""
        if hasattr(self, "engine_box"):
            self.engine_box.configure(state="readonly" if enabled else "disabled")

    def _on_planner_change(self):
        # The Framework panel prints which planner is in use, so it has to
        # be rebuilt when that changes. Without this it goes on saying
        # "not in use: planner is Without API" after With API has been
        # chosen.
        if hasattr(self, "_governance_text"):
            self.after_idle(self.refresh_framework)

        if self.planner.get() == "rules":
            self._set_engine_enabled(False)
            self._show_key_box(False)
            self.api_status.configure(
                text="Offline, free, deterministic. No API key needed.",
                foreground="#666",
            )
            return

        self._set_engine_enabled(True)
        self._show_key_box(True)

        from regression.ai_engine import engines

        try:
            engine = engines.get_engine(self.engine.get())
        except engines.EngineNotAvailable as exc:
            self.api_status.configure(text=str(exc), foreground="#c0271c")
            return

        ok, reason = engine.available()

        if ok:
            self.api_status.configure(
                # With API does more than plan, and HA_CASE_SOURCE says
                # how much more: under HA_AI=llm "auto" (the default) and
                # "ai" have the model write the requirement and release-note
                # cases, "both" has it add extras beside the rule-written
                # ones, and "rules" leaves every case to the rules
                # (canonical._apply_case_source). The line names the
                # variable rather than one of those outcomes, because each
                # of them is false under the others. Wherever the model does
                # write cases, the requirement document and this build's
                # release-note section are sent with the prompt, which is
                # what SECURITY.md describes.
                text="Ready - {} ({}) will choose intensity and scenarios, "
                     "and, as HA_CASE_SOURCE sets, write test cases.".format(
                         engine.name, engine.model_name()),
                foreground="#0a7d2b",
            )
        else:
            self.api_status.configure(
                text="Unavailable: {}. The run will fall back to rules.".format(
                    reason),
                foreground="#a86400",
            )

    def _play(self, path):
        if not os.path.exists(path):
            self.hint.configure(text="No recording yet - tick the box and run.")
            return

        try:
            if sys.platform.startswith("win"):
                import winsound

                # Async so the window stays responsive while it plays.
                winsound.PlaySound(path, winsound.SND_FILENAME | winsound.SND_ASYNC)
            else:
                subprocess.Popen(["xdg-open", path])

            self.hint.configure(text="Playing " + os.path.basename(path))
        except Exception as exc:
            self.hint.configure(text="Could not play: {}".format(exc))

    def _refresh_play_buttons(self):
        for button, path in ((self.play_before_btn, BEFORE_WAV),
                             (self.play_after_btn, AFTER_WAV)):
            button.configure(
                state="normal" if os.path.exists(path) else "disabled")

    def _start_recording(self):
        """Stream audio through the device and capture what comes back."""
        self._phase = "record"

        self._append(
            "\n--- recording DSP audio ({:.1f}s, this takes a minute) ---\n"
            .format(RECORD_SECONDS), "info")

        env = dict(os.environ)
        env[DEVICE_ADDRESS_ENV] = self.device_addresses.get(self.device.get(), "")
        env["PYTHONIOENCODING"] = "utf-8"
        env["PYTHONUNBUFFERED"] = "1"

        cmd = [sys.executable, "-u",
               os.path.join(REPO_ROOT, "tools", "dsp_listen.py"),
               "--seconds", str(RECORD_SECONDS)]

        if self.signal.get() and self.signal.get() != DEFAULT_TEST_SIGNAL:
            cmd += ["--input", self.signal.get()]

        try:
            self.proc = subprocess.Popen(
                cmd, cwd=REPO_ROOT, env=env,
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, encoding="utf-8", errors="replace", bufsize=1,
                **NO_CONSOLE,
            )
        except Exception as exc:
            self._append("Could not record: {}\n".format(exc), "fail")
            self._phase = None
            self.start_btn.configure(state="normal")
            return

        self.stop_btn.configure(state="normal")

        threading.Thread(target=self._reader, args=(self.proc,), daemon=True).start()

    def _scan_devices(self):
        """Scan in a worker thread; results come back through a queue.

        BLE discovery blocks for several seconds, so it must never run on the
        UI thread or the window freezes.
        """
        self.scan_btn.configure(state="disabled")
        self.device_info.configure(
            text="Scanning for {:.0f}s...".format(SCAN_TIMEOUT), foreground="#1a4fa0")

        threading.Thread(target=self._scan_worker, daemon=True).start()

    def _scan_worker(self):
        import asyncio

        try:
            from bleak import BleakScanner

            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)

            try:
                found = loop.run_until_complete(
                    BleakScanner.discover(timeout=SCAN_TIMEOUT))
            finally:
                loop.close()

            self.scan_results.put([(d.name, d.address) for d in found])

        except Exception as exc:
            self.scan_results.put(exc)

    def _apply_scan(self, found):
        # The same queue carries build-id results, tagged so they are not
        # mistaken for a device list.
        if isinstance(found, tuple) and found and found[0] == "build_id":
            self._apply_build_id(found[1])
            return

        self.scan_btn.configure(state="normal")

        if isinstance(found, Exception):
            self.device_info.configure(
                text="Scan failed: {}".format(found), foreground="#c0271c")
            return

        # Named devices first, and the likely boards above those.
        def sort_key(item):
            name = item[0] or ""
            return (0 if DEVICE_NAME_MATCH.lower() in name.lower()
                    else 1 if name else 2, name.lower())

        self.device_addresses = {}
        labels = [SELECT_DEVICE]

        for name, address in sorted(set(found), key=sort_key):
            label = "{}  ({})".format(name or "(unnamed)", address)
            self.device_addresses[label] = address
            labels.append(label)

        self.device_box.configure(values=labels)

        # Never auto-select. The operator picks the board.
        if self.device.get() not in self.device_addresses:
            self.device.set(SELECT_DEVICE)

        named = sum(1 for n, _ in found if n)

        self.device_info.configure(
            text="Found {} devices ({} named). Choose one to test.".format(
                len(found), named),
            foreground="#0a7d2b" if found else "#a86400")

    def _on_device_change(self, event=None):
        """Read the build id off the chosen board, in the background.

        Pre-filling from the device beats making the operator type one: a
        typed label can be stale, and the determinism KPI compares runs
        sharing one. Two runs of different firmware under one label would
        look like non-determinism nobody could reproduce.
        """
        address = self.device_addresses.get(self.device.get())

        if not address:
            return

        self.device_info.configure(
            text="Reading build id from the board...", foreground="#666")

        threading.Thread(
            target=self._build_id_worker, args=(address,), daemon=True).start()

    def _build_id_worker(self, address):
        import asyncio

        result = None

        try:
            from regression.ble.telemetry import BUILD_CHAR_UUID
            from bleak import BleakClient

            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)

            async def read():
                async with BleakClient(address) as client:
                    raw = await client.read_gatt_char(BUILD_CHAR_UUID)
                    return bytes(raw).decode("utf-8", errors="replace").strip()

            try:
                result = loop.run_until_complete(read())
            finally:
                loop.close()

        except Exception as exc:  # noqa: BLE001 - older firmware has no such char
            result = exc

        self.scan_results.put(("build_id", result))

    def _apply_build_id(self, result):
        if isinstance(result, Exception) or not result:
            self.device_info.configure(
                text="Board reports no build id -- type one in Build ID "
                     "if you want determinism measured.",
                foreground="#a86400")
            return

        self.build_id.set(result)

        self.device_info.configure(
            text="Build ID read from the board: {}".format(result),
            foreground="#0a7d2b")

    def _pick_signal(self):
        chosen = filedialog.askopenfilename(
            title="Choose the audio streamed to the device",
            initialdir=os.path.dirname(self.signal.get() or BASE_DIR),
            filetypes=[("WAV audio", "*.wav"), ("All files", "*.*")],
        )

        if chosen:
            self.signal.set(chosen)
            self._describe_signal()

    def _pick_requirements(self):
        chosen = filedialog.askopenfilename(
            title="Choose the requirement document",
            initialdir=os.path.dirname(self.requirements.get() or BASE_DIR),
            filetypes=[("Markdown", "*.md"), ("Text", "*.txt"),
                       ("All files", "*.*")],
        )

        if chosen:
            self.requirements.set(chosen)

    def _pick_release_note(self):
        chosen = filedialog.askopenfilename(
            title="Choose the release note describing the firmware changes",
            initialdir=os.path.dirname(self.release_note.get() or BASE_DIR),
            filetypes=[("Markdown", "*.md"), ("Text", "*.txt"),
                       ("All files", "*.*")],
        )

        if chosen:
            self.release_note.set(chosen)

    def _reset_signal(self):
        self.signal.set(DEFAULT_TEST_SIGNAL)
        self._describe_signal()

    def _describe_signal(self):
        """Show what the file is and how it will be converted."""
        path = self.signal.get()

        if not path or not os.path.exists(path):
            self.signal_info.configure(
                text="File not found.", foreground="#c0271c")
            return False

        try:
            from regression.audio_prep import describe

            info = describe(path)

        except Exception as exc:
            self.signal_info.configure(
                text="Not a usable WAV: {}".format(exc), foreground="#c0271c")
            return False

        detail = "{} Hz, {} ch, {}-bit{}, {:.1f}s".format(
            info["rate"], info["channels"], info["bits"],
            " float" if info.get("float") else "", info["seconds"])

        if info["changes"]:
            self.signal_info.configure(
                text=detail + " -- will be " + " and ".join(info["changes"]),
                foreground="#666")
        else:
            self.signal_info.configure(text=detail, foreground="#666")

        return True

    # ------------------------------------------------------------------
    # Running
    # ------------------------------------------------------------------
    def start(self):
        if self.proc is not None:
            return

        if self.device.get() not in self.device_addresses:
            self.summary.configure(
                text="Select the device to test before starting",
                foreground="#c0271c")
            self.device_info.configure(
                text="Press Scan, then choose the board from the list.",
                foreground="#c0271c")
            return

        if not self._describe_signal():
            self.summary.configure(
                text="Pick a readable WAV file before starting",
                foreground="#c0271c")
            return

        self.clear()
        self.summary.configure(text="Running...", foreground="#1a4fa0")
        self.metrics.configure(text="")
        self.tree.delete(*self.tree.get_children())
        self.log_paths.clear()
        self.rows.clear()
        self.hint.configure(text="Double-click a test to open its log file.")

        self._phase = "pipeline"

        for key in ("_score", "_slice", "_planner", "_intensity", "_result"):
            if hasattr(self, key):
                delattr(self, key)

        env = dict(os.environ)
        env["HA_AI"] = self.planner.get()

        # The subprocess reads the policy from the environment, so a
        # GUI run and a command-line run take the same path.
        from regression.ai_engine import engines as engine_registry

        env[engine_registry.ENGINE_ENV] = self.engine.get()

        # Blank means "the board's own id": resolve_build_id reads the
        # device when HA_BUILD_ID is empty. Passing the field through is
        # what lets the operator pin a run to a label instead, so two runs
        # the determinism KPI should compare carry the same id.
        env["HA_BUILD_ID"] = self.build_id.get().strip()
        env[SIGNAL_ENV] = self.signal.get()

        # An empty HA_RELEASE_NOTE means "no note", so selection falls back
        # to the image comparison. An empty HA_REQUIREMENTS means "no
        # requirements this run" -- set-but-empty is how Clear tells the
        # orchestrator to leave them out (orchestrator.requirements_path).
        env["HA_RELEASE_NOTE"] = self.release_note.get().strip()
        env["HA_REQUIREMENTS"] = self.requirements.get().strip()
        env[DEVICE_ADDRESS_ENV] = self.device_addresses.get(self.device.get(), "")
        env["PYTHONIOENCODING"] = "utf-8"
        env["PYTHONUNBUFFERED"] = "1"

        if is_frozen():
            cmd = [sys.executable, PIPELINE_FLAG]
        else:
            cmd = [
                sys.executable, "-u", "-m", "regression.ai_engine.orchestrator",
            ]

        try:
            self.proc = subprocess.Popen(
                cmd,
                cwd=REPO_ROOT,
                env=env,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
                bufsize=1,
                **NO_CONSOLE,
            )
        except Exception as exc:
            self._append("Could not start: {}\n".format(exc), "fail")
            self.summary.configure(text="Failed to start", foreground="#c0271c")
            self.proc = None
            return

        self.start_btn.configure(state="disabled")
        self.stop_btn.configure(state="normal")

        threading.Thread(target=self._reader, args=(self.proc,), daemon=True).start()

    def _reader(self, proc):
        """Runs off the UI thread; never touches Tk directly."""
        for line in proc.stdout:
            self.lines.put(line)

        proc.wait()
        self.lines.put(None)

    def stop(self):
        if self.proc is None:
            return

        self._append("\n-- stopped by user --\n", "warn")
        try:
            self.proc.terminate()
        except Exception:
            pass

    def clear(self):
        self.log.configure(state="normal")
        self.log.delete("1.0", "end")
        self.log.configure(state="disabled")

    # ------------------------------------------------------------------
    # Output pump
    # ------------------------------------------------------------------
    def _drain(self):
        try:
            while True:
                self._apply_scan(self.scan_results.get_nowait())
        except queue.Empty:
            pass

        try:
            while True:
                line = self.lines.get_nowait()

                if line is None:
                    self._finished()
                else:
                    self._handle(line)

        except queue.Empty:
            pass

        self.after(80, self._drain)

    def _handle(self, line):
        stripped = line.strip()

        # Final marker: carries duration and the per-test log file path.
        result = parse_result_line(stripped)
        if result:
            self._add_or_update(
                result["name"], result["status"],
                result["duration"], result["log_path"],
            )
            return

        # Live line from pytest -v: show the verdict immediately.
        live = PATTERNS["live"].search(stripped)
        if live:
            self._add_or_update(live.group("name"), live.group("status"))

        tag = None

        if "PASSED" in line or stripped.startswith("Result: pass"):
            tag = "pass"
        elif "FAILED" in line or "Error" in line or stripped.startswith("Result: fail"):
            tag = "fail"
        elif stripped.startswith(("[planner]", "[cases]", "[ai-cases]",
                                  "[gemini]")):
            # These are the prefixes something actually prints: the case
            # generator's "[cases]" and "[ai-cases]", the planner's
            # "[planner]" and the Gemini client's "[gemini]".
            tag = "warn" if PATTERNS["fallback"].match(stripped) else "info"
        elif stripped.startswith(("Metrics:", "Planner:", "Selected intensity:")):
            tag = "info"

        self._append(line, tag)
        self._parse(stripped)

    def _parse(self, line):
        match = PATTERNS["metrics"].match(line)
        if match:
            try:
                import ast

                values = ast.literal_eval(match.group(1))
                self.metrics.configure(
                    text="power {}   memory {}   sync {}   retry {}".format(
                        values.get("power"), values.get("memory"),
                        values.get("sync"), values.get("retry"),
                    )
                )
            except Exception:
                pass
            return

        for key in ("score", "slice", "planner", "intensity", "result"):
            match = PATTERNS[key].search(line)
            if match:
                setattr(self, "_" + key, match.group(1))

    def _finished(self):
        self.proc = None
        self.stop_btn.configure(state="disabled")

        # The run just added a data point to every framework panel:
        # a KPI sample, a trend entry, and a new suite fingerprint.
        self.refresh_framework()

        # The recording pass runs after the pipeline, as a second subprocess.
        if self._phase == "record":
            self._phase = None
            self.start_btn.configure(state="normal")
            self._refresh_play_buttons()
            self.hint.configure(
                text="Recording done - use Play original / Play processed.")
            return

        self._show_summary()

        if self.record_audio.get() and getattr(self, "_result", None) is not None:
            self._start_recording()
            return

        self._phase = None
        self.start_btn.configure(state="normal")

    def _show_summary(self):
        result = getattr(self, "_result", None)
        score = getattr(self, "_score", "?")
        slice_name = SLICE_LABEL.get(getattr(self, "_slice", ""), "?")
        intensity = getattr(self, "_intensity", "?")
        planner = getattr(self, "_planner", "?")

        if result is None:
            self.summary.configure(
                text="Did not finish - check the log (is the device advertising?)",
                foreground="#a86400",
            )
            return

        self.summary.configure(
            text="{}   |   risk score {}   slice {}   intensity {}   planner {}".format(
                "PASS" if result == "pass" else "FAIL",
                score, slice_name, intensity, planner,
            ),
            foreground="#0a7d2b" if result == "pass" else "#c0271c",
        )

    def _append(self, text, tag=None):
        self.log.configure(state="normal")
        self.log.insert("end", text, tag or ())
        self.log.see("end")
        self.log.configure(state="disabled")


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

    # --help must not open a window.
    if "--help" in sys.argv or "-h" in sys.argv:
        print("usage: python -m regression.dashboard [{}]".format(
            PIPELINE_FLAG))
        print()
        print("Opens the regression dashboard. With {} it runs the "
              "pipeline".format(PIPELINE_FLAG))
        print("headlessly instead, which is how the frozen build "
              "re-invokes itself.")

        return 0

    # Frozen builds re-invoke this same exe to run the pipeline as a
    # subprocess; that keeps the window responsive and Stop working.
    if PIPELINE_FLAG in sys.argv:
        from regression.ai_engine.orchestrator import main as run_orchestrator

        sys.argv = [a for a in sys.argv if a != PIPELINE_FLAG]
        run_orchestrator()
        return

    root = tk.Tk()
    root.title("AI Enabled Regression Test Optimization")
    # Output and Framework share a row, and the Framework tabs are
    # unreadable squeezed into half of 820px.
    root.geometry("1180x860")
    root.minsize(900, 640)

    Dashboard(root)

    root.mainloop()


if __name__ == "__main__":
    main()
