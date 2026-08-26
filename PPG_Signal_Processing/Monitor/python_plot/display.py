# Copyright (c) 2026 Prajnaana Technologies Pvt. Ltd.
# SPDX-License-Identifier: Apache License, Version 2.0
# Original Author: Dhanya Shree S

from collections import deque
from queue import Empty, Queue
from typing import Any

import pyqtgraph as pg
from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QColor, QFont
from PySide6.QtWidgets import (
    QApplication,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QVBoxLayout,
    QWidget,
)


class MetricCard(QFrame):
    def __init__(self, icon: str, title: str, color: str) -> None:
        super().__init__()

        self.setObjectName("metricCard")
        self.setStyleSheet(
            f"""
            QFrame#metricCard {{
                background-color: #172033;
                border: 1px solid #2a3a56;
                border-radius: 14px;
            }}
            """
        )

        layout = QHBoxLayout(self)
        layout.setContentsMargins(18, 14, 18, 14)
        layout.setSpacing(12)

        icon_label = QLabel(icon)
        icon_label.setAlignment(Qt.AlignCenter)
        icon_label.setFixedSize(42, 42)
        icon_label.setFont(QFont("Segoe UI Symbol", 21))
        icon_label.setStyleSheet(
            f"""
            QLabel {{
                color: {color};
                background-color: #101827;
                border-radius: 21px;
            }}
            """
        )

        text_layout = QVBoxLayout()
        text_layout.setSpacing(1)

        self.title_label = QLabel(title.upper())
        self.title_label.setStyleSheet(
            "color: #9aa9c0; font-size: 11px; font-weight: bold;"
        )

        self.value_label = QLabel("--")
        self.value_label.setStyleSheet(
            f"color: {color}; font-size: 22px; font-weight: bold;"
        )

        text_layout.addWidget(self.title_label)
        text_layout.addWidget(self.value_label)

        layout.addWidget(icon_label)
        layout.addLayout(text_layout)

    def set_value(self, value: str) -> None:
        self.value_label.setText(value)

    def set_pair(self, left: str, right: str) -> None:
        """
        Show two engines' readings side by side.

        Kept in ONE card rather than two, because the point of comparing is to
        read the difference at a glance -- two cards several centimetres apart
        make that an act of memory. The separator is thin and dim so the eye
        groups the pair rather than the halves.
        """
        self.value_label.setText(
            f"{left}"
            f"<span style='color:#475569; font-weight:normal;'> | </span>"
            f"{right}"
        )

    def set_title(self, title: str) -> None:
        self.title_label.setText(title.upper())


class LivePpgDisplay(QMainWindow):
    def __init__(
        self,
        result_queue: Queue[dict[str, Any]],
        max_samples: int = 1_250,
        refresh_ms: int = 50,
        record_name: str | None = None,
    ) -> None:
        super().__init__()

        self.result_queue = result_queue
        self.record_name = record_name
        self.max_samples = max_samples

        self.samples = deque(maxlen=max_samples)
        self.raw_values = deque(maxlen=max_samples)
        self.filtered_values = deque(maxlen=max_samples)
        # Marker INDICES only. The heights come from the curve at draw time --
        # see _on_curve() for why storing the reported values would be wrong.
        #
        # Two sets, because the server may be running both beat detectors on the
        # same samples (--compare). The second stays empty otherwise and its
        # curves draw nothing, so one code path serves both modes.
        self.peak_samples = deque(maxlen=max_samples)
        self.foot_samples = deque(maxlen=max_samples)
        self.peak2_samples = deque(maxlen=max_samples)
        self.foot2_samples = deque(maxlen=max_samples)

        # Set from the first result that declares them; None until then.
        self.compare_engines: list[str] | None = None

        # Which engine the server reported computing these numbers, shown in
        # the status bar. Worth surfacing: the server can be started with a
        # reference detector that reports roughly double the true heart rate,
        # and an operator should be able to see which one produced what is on
        # screen without going to look at the server's console.
        self.engine_name: str | None = None
        self.error_message: str | None = None
        self.fatal_error = False

        self.setWindowTitle("PPG Live Monitor")
        self.resize(1280, 760)
        self.setStyleSheet(
            """
            QMainWindow {
                background-color: #0b1220;
            }

            QLabel {
                font-family: Segoe UI, Arial;
            }
            """
        )

        self._create_ui()

        self.refresh_timer = QTimer(self)
        self.refresh_timer.timeout.connect(self._refresh)
        self.refresh_timer.start(refresh_ms)

    def _create_ui(self) -> None:
        root = QWidget()
        self.setCentralWidget(root)

        root_layout = QVBoxLayout(root)
        root_layout.setContentsMargins(24, 22, 24, 24)
        root_layout.setSpacing(18)

        # A GRID, not a row of stretches.
        #
        # Two stretches either side of the label divide only what is LEFT after
        # the title and the pill have taken their widths, and those two are not
        # the same width -- so the label lands off centre by the difference,
        # measured at about 80 px. Giving the outer columns equal stretch makes
        # them equal WIDTH instead, which puts the middle column on the window's
        # centre line and keeps it there through a resize.
        header_layout = QGridLayout()
        header_layout.setContentsMargins(0, 0, 0, 0)
        header_layout.setColumnStretch(0, 1)
        header_layout.setColumnStretch(2, 1)

        title_layout = QVBoxLayout()
        title_layout.setSpacing(2)

        title = QLabel("PPG Live Monitor")
        title.setStyleSheet("color: #f8fafc; font-size: 27px; font-weight: bold;")

        subtitle = QLabel("Continuous real-time PPG processing")
        subtitle.setStyleSheet("color: #93a4be; font-size: 13px;")

        title_layout.addWidget(title)
        title_layout.addWidget(subtitle)

        self.connection_label = QLabel("● CONNECTING")
        self.connection_label.setStyleSheet(
            """
            QLabel {
                color: #fbbf24;
                background-color: #2b2412;
                padding: 8px 12px;
                border-radius: 12px;
                font-weight: bold;
                font-size: 12px;
            }
            """
        )

        # WHICH RECORDING IS ON SCREEN, in the middle of the top bar.
        #
        # Every number below belongs to one subject, and a window that does not
        # say which one is a window whose readings cannot be attributed. Two of
        # these open side by side look identical otherwise.
        #
        # Centred on the window itself -- see the grid below.
        self.record_label = QLabel(self.record_name or "")
        self.record_label.setAlignment(Qt.AlignCenter)
        self.record_label.setStyleSheet(
            "color: #e2e8f0; font-size: 19px; font-weight: 600;"
        )

        header_layout.addLayout(title_layout, 0, 0, Qt.AlignLeft | Qt.AlignVCenter)
        header_layout.addWidget(self.record_label, 0, 1, Qt.AlignCenter)
        header_layout.addWidget(self.connection_label, 0, 2,
                                Qt.AlignRight | Qt.AlignVCenter)

        root_layout.addLayout(header_layout)

        # Two rows: what the C engine computes, grouped by what it measures.
        # Every card starts at "--" and STAYS there until a real measurement
        # arrives -- a heart rate needs two beats, HRV three intervals, a
        # respiratory rate a full analysis window of 8-65 s depending on
        # patient type. Showing 0 in the meantime would be a reading nothing
        # supports, which is the one thing a monitor must not display.
        metrics_layout = QGridLayout()
        metrics_layout.setHorizontalSpacing(14)
        metrics_layout.setVerticalSpacing(12)

        self.hr_card = MetricCard("♥", "Heart Rate", "#fb7185")
        self.rr_card = MetricCard("〰", "Respiratory Rate", "#60a5fa")
        self.rrv_card = MetricCard("⌁", "RR Variability (SD)", "#c084fc")
        self.signal_card = MetricCard("◉", "RR Status", "#34d399")

        self.sdnn_card = MetricCard("⊕", "HRV SDNN", "#f472b6")
        self.rmssd_card = MetricCard("⊗", "HRV RMSSD", "#fb923c")
        self.pnn50_card = MetricCard("⊙", "HRV pNN50", "#38bdf8")

        for column, card in enumerate(
            (self.hr_card, self.rr_card, self.rrv_card, self.signal_card)
        ):
            metrics_layout.addWidget(card, 0, column)

        for column, card in enumerate(
            (self.sdnn_card, self.rmssd_card, self.pnn50_card)
        ):
            metrics_layout.addWidget(card, 1, column)

        root_layout.addLayout(metrics_layout)

        self.chart = pg.PlotWidget()
        self.chart.setBackground("#101827")
        self.chart.showGrid(x=True, y=True, alpha=0.18)
        self.chart.setLabel("left", "PPG amplitude", color="#cbd5e1")
        self.chart.setLabel("bottom", "Sample number", color="#cbd5e1")
        self.chart.setTitle("Live PPG Waveform", color="#f8fafc", size="13pt")
        self.chart.addLegend(
            offset=(12, 12),
            labelTextColor="#dce6f7",
            brush=pg.mkBrush("#172033"),
            pen=pg.mkPen("#2a3a56"),
        )

        self.chart.getAxis("left").setTextPen("#94a3b8")
        self.chart.getAxis("bottom").setTextPen("#94a3b8")

        self.raw_curve = self.chart.plot(
            pen=pg.mkPen("#64748b", width=1),
            name="Raw PPG",
        )
        # Named for what it IS: the waveform the detectors are handed, after the
        # band-pass and the smoothing. Calling it "Filtered PPG" invited the
        # assumption that markers could be drawn against the band-pass output,
        # which is a different amplitude scale.
        self.filtered_curve = self.chart.plot(
            pen=pg.mkPen("#22c55e", width=2),
            name="Detection signal (filtered + smoothed)",
        )
        self.peak_curve = self.chart.plot(
            pen=None,
            symbol="t",
            symbolSize=12,
            symbolBrush=QColor("#ef4444"),
            name="Systolic peak",
        )
        self.foot_curve = self.chart.plot(
            pen=None,
            symbol="t1",
            symbolSize=12,
            symbolBrush=QColor("#a855f7"),
            name="Systolic foot",
        )

        # Second detector's markers, drawn only in --compare mode. Distinct
        # SHAPES as well as colours: the two sets overlap by design -- that is
        # what makes the comparison readable -- and colour alone would leave
        # them indistinguishable wherever they coincide, as well as unreadable
        # to anyone with a colour deficiency.
        self.peak2_curve = self.chart.plot(
            pen=None,
            symbol="s",
            symbolSize=9,
            symbolBrush=None,
            symbolPen=pg.mkPen("#f97316", width=2),
            name="Systolic peak (2nd detector)",
        )
        self.foot2_curve = self.chart.plot(
            pen=None,
            symbol="d",
            symbolSize=10,
            symbolBrush=None,
            symbolPen=pg.mkPen("#38bdf8", width=2),
            name="Systolic foot (2nd detector)",
        )

        root_layout.addWidget(self.chart, stretch=1)

        self.status_label = QLabel(
            "Waiting for PPG results from the processing system..."
        )
        self.status_label.setStyleSheet(
            """
            QLabel {
                color: #93a4be;
                background-color: #101827;
                padding: 10px 12px;
                border-radius: 8px;
                font-size: 12px;
            }
            """
        )
        root_layout.addWidget(self.status_label)

    @staticmethod
    def _format(value: Any, unit: str, decimals: int = 1,
                age_s: float | None = None, note: str | None = None) -> str:
        """
        Render a metric, or "--" when there is no measurement.

        None arrives from the server for every rate that does not yet have the
        history it needs. It must render as "--" and never as a number: a
        plausible-looking value that no measurement supports is worse than a
        visibly absent one.

        A HELD reading -- one the adapter is carrying through a window that
        declined -- arrives with an age, and is drawn with that age beside it in
        a dimmer colour. It is deliberately not passed off as current: the point
        is that the number stays on screen, and that it is obvious it is not
        this window's.
        """
        if value is None:
            return "--"
        text = f"{value:.{decimals}f} {unit}".strip()
        if age_s:
            text += ("<span style='color:#64748b; font-size:15px;"
                     " font-weight:normal;'> &middot; %ds</span>" % int(age_s))
        elif note:
            text += ("<span style='color:#64748b; font-size:15px;"
                     " font-weight:normal;'> &middot; %s</span>" % note)
        return text

    def _read_results(self) -> int:
        received_count = 0

        while True:
            try:
                result = self.result_queue.get_nowait()
            except Empty:
                return received_count

            # An error from the server is shown, not discarded. Left silent,
            # a server that could not start its engine produced a window
            # sitting at "--" for ever with no way to tell whether the signal
            # was bad or the far end was broken.
            if result.get("type") == "error":
                self.error_message = str(result.get("message", "unknown error"))
                self.fatal_error = bool(result.get("fatal"))
                continue

            sequence = result.get("sequence")
            raw_value = result.get("raw_value")

            if sequence is None or raw_value is None:
                continue

            received_count += 1

            # The DETECTION signal, not the band-pass output. The detectors are
            # handed the smoothed waveform and report their fiducials on that
            # scale, so this is the curve the markers below belong on -- drawn
            # against the band-passed one they float off the trace by the height
            # of the smoothing difference.
            detection_value = result.get(
                "smoothed_value", result.get("filtered_value", raw_value)
            )

            self.samples.append(sequence)
            self.raw_values.append(raw_value)
            self.filtered_values.append(detection_value)

            # MARKERS GO WHERE THE BEAT IS, NOT WHERE IT WAS ANNOUNCED.
            #
            # No detector can confirm a systolic peak at the instant it passes:
            # TERMA needs half its beat window buffered, IMS the segment that
            # closes the up-slope. Measured on bidmc_01, the announcement lands
            # 59-72 samples (470-580 ms) after the fiducial it describes.
            #
            # Plotting at `sequence` -- which this did -- put every marker half a
            # second late, and because the detector announces the foot and the
            # peak of a beat back to back, both landed on the SAME sample. Two
            # markers stacked on one point, once per beat: correct detection
            # rendered as an obvious fault.
            #
            # ONLY THE INDEX IS KEPT. The height is looked up in this window's
            # own curve at draw time, rather than taken from the reported value,
            # so a marker cannot float off the trace. It would: the detector
            # shifts its indices back by the smoothing group delay but captures
            # the foot's amplitude before that shift, so the reported foot value
            # belongs to a sample a couple of positions away from the reported
            # foot index. Reading the curve makes the marker right by
            # construction and immune to any such detail changing.
            engines = result.get("compare_engines")

            if not engines:
                if result.get("peak_detected") and result.get("peak_index"):
                    self.peak_samples.append(result["peak_index"])
                if result.get("foot_detected") and result.get("foot_index"):
                    self.foot_samples.append(result["foot_index"])
            else:
                # Compare mode: each detector's own markers, kept apart so the
                # graph shows WHERE they disagree, not just that they do.
                if self.compare_engines is None:
                    self._enter_compare_mode(engines)

                first, second = engines[0], engines[1]
                if result.get(f"{first}_peak_detected") and result.get(f"{first}_peak_index"):
                    self.peak_samples.append(result[f"{first}_peak_index"])
                if result.get(f"{first}_foot_detected") and result.get(f"{first}_foot_index"):
                    self.foot_samples.append(result[f"{first}_foot_index"])
                if result.get(f"{second}_peak_detected") and result.get(f"{second}_peak_index"):
                    self.peak2_samples.append(result[f"{second}_peak_index"])
                if result.get(f"{second}_foot_detected") and result.get(f"{second}_foot_index"):
                    self.foot2_samples.append(result[f"{second}_foot_index"])

            # Set on EVERY result, including when the value is None, so a
            # metric that stops being available goes back to "--" instead of
            # leaving a stale number on screen looking current.
            if engines:
                a, b = engines[0], engines[1]
                for card, field, unit in (
                    (self.hr_card, "heart_rate_bpm", ""),
                    (self.rr_card, "respiratory_rate_bpm", ""),
                    (self.sdnn_card, "hrv_sdnn_ms", ""),
                    (self.rmssd_card, "hrv_rmssd_ms", ""),
                ):
                    card.set_pair(
                        self._format(result.get(f"{a}_{field}"), unit),
                        self._format(result.get(f"{b}_{field}"), unit),
                    )
                # Not computed per detector -- one value, from the primary.
                self.rrv_card.set_value(self._format(result.get("rrv_sd_ms"), "ms"))
                self.pnn50_card.set_value(
                    self._format(result.get("hrv_pnn50_pct"), "%")
                )
            else:
                ages = result.get("metric_ages_s") or {}
                # The HRV window fills to 300 beats over minutes, so these carry
                # the interval count they rest on: SDNN from twenty intervals
                # and SDNN from three hundred are not the same number twice.
                hrv_n = result.get("hrv_n")
                support = ("n=%d" % hrv_n) if hrv_n else None
                for card, field, unit, note in (
                    (self.hr_card, "heart_rate_bpm", "bpm", None),
                    (self.rr_card, "respiratory_rate_bpm", "/min", None),
                    (self.rrv_card, "rrv_sd_ms", "ms", None),
                    (self.sdnn_card, "hrv_sdnn_ms", "ms", support),
                    (self.rmssd_card, "hrv_rmssd_ms", "ms", support),
                    (self.pnn50_card, "hrv_pnn50_pct", "%", support),
                ):
                    card.set_value(
                        self._format(result.get(field), unit,
                                     age_s=ages.get(field), note=note)
                    )

            # WHAT THE ENGINE DID WITH THIS WINDOW, not a quality index.
            #
            # This card used to read "Signal Quality: Active" for the whole run.
            # It measured nothing -- no engine computes a quality index -- and
            # "Active" only repeated what the LIVE pill already says, while
            # sitting unchanged through a minute and a half in which the
            # respiratory card showed nothing at all.
            #
            # The engine has always sent the answer: `Method` says whether the
            # window was confirmed by three surrogates, by two, or declined
            # because they disagreed. The adapter was already parsing it and
            # nothing read it. Now it is what this card shows, so a reader who
            # sees no respiratory rate can see why in the same glance.
            self.signal_card.set_value(result.get("rr_state") or "Warming up")

            engine = result.get("engine")
            if engine and engine != self.engine_name:
                self.engine_name = engine

        return received_count

    def _enter_compare_mode(self, engines: list[str]) -> None:
        """
        Relabel the window for a two-detector session.

        Done once, on the first result that declares it, rather than from a
        command-line flag on this side: the server decides what it is running,
        and a display that had to be told separately could be told wrong.
        """
        self.compare_engines = engines
        first, second = engines[0].upper(), engines[1].upper()

        for card, name in (
            (self.hr_card, "Heart Rate"),
            (self.rr_card, "Respiratory Rate"),
            (self.sdnn_card, "HRV SDNN"),
            (self.rmssd_card, "HRV RMSSD"),
        ):
            card.set_title(f"{name}  {first} | {second}")

        self.peak2_curve.opts["name"] = f"Systolic peak ({engines[1]})"
        self.setWindowTitle(f"PPG Live Monitor - {first} vs {second}")

    @staticmethod
    def _prune(indices: deque, oldest: int) -> None:
        """Discard marker indices older than the visible window."""
        while indices and indices[0] < oldest:
            indices.popleft()

    def _on_curve(self, indices: deque, oldest: int) -> tuple[list, list]:
        """
        Pair each marker index with the curve's own value at that sample.

        The waveform deque holds contiguous, increasing sample numbers, so the
        position of sample `i` is simply `i - oldest`. Taking the height from
        the drawn curve rather than from the value the engine reported is what
        guarantees a marker sits ON the trace: the two are different numbers,
        because the detector shifts its indices back by the smoothing group
        delay but reads the foot's amplitude before that shift.
        """
        xs: list[int] = []
        ys: list[float] = []
        span = len(self.filtered_values)

        for index in indices:
            position = index - oldest
            if 0 <= position < span:
                xs.append(index)
                ys.append(self.filtered_values[position])

        return xs, ys

    def _set_pill(self, text: str, colour: str, background: str) -> None:
        self.connection_label.setText(text)
        self.connection_label.setStyleSheet(
            f"""
            QLabel {{
                color: {colour};
                background-color: {background};
                padding: 8px 12px;
                border-radius: 12px;
                font-weight: bold;
                font-size: 12px;
            }}
            """
        )

    def _refresh(self) -> None:
        received_count = self._read_results()

        # An error is reported even with no waveform to draw -- which is
        # exactly the case that matters, because a server whose engine failed
        # to start sends an error and no samples at all.
        if self.error_message is not None:
            self._set_pill(
                "● ERROR" if self.fatal_error else "● WARNING", "#fca5a5", "#3f1d1d"
            )
            self.status_label.setText(f"Server: {self.error_message}")
            if self.fatal_error:
                return

        if not self.samples:
            return

        # Drop markers that have scrolled off the left edge.
        #
        # Needed because the two series fill at very different rates: samples
        # arrive 125 times a second and the waveform deque holds 10 seconds of
        # them, while beats arrive about 1.5 times a second, so the same deque
        # length holds over 13 MINUTES of markers. Left alone, pyqtgraph would
        # be asked to plot points far to the left of the visible waveform, and
        # autoscaling the x-axis to fit them squashes the live trace into a
        # sliver at the right-hand edge.
        oldest = self.samples[0]
        for series in (self.peak_samples, self.foot_samples,
                       self.peak2_samples, self.foot2_samples):
            self._prune(series, oldest)

        self.raw_curve.setData(list(self.samples), list(self.raw_values))
        self.filtered_curve.setData(
            list(self.samples),
            list(self.filtered_values),
        )

        for curve, series in (
            (self.peak_curve, self.peak_samples),
            (self.foot_curve, self.foot_samples),
            (self.peak2_curve, self.peak2_samples),
            (self.foot2_curve, self.foot2_samples),
        ):
            xs, ys = self._on_curve(series, oldest)
            curve.setData(xs, ys)

        if self.error_message is None:
            self._set_pill("● LIVE", "#4ade80", "#102b20")

            engine = f" • engine: {self.engine_name}" if self.engine_name else ""
            self.status_label.setText(
                f"Live stream active • Latest sample: {self.samples[-1]} "
                f"• {received_count} result(s) received{engine}"
            )


def show_display(result_queue: Queue[dict[str, Any]],
                 record_name: str | None = None) -> int:
    app = QApplication.instance() or QApplication([])
    display = LivePpgDisplay(result_queue, record_name=record_name)
    display.show()
    return app.exec()
