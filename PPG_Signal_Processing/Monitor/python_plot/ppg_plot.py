# Copyright (c) 2026 Prajnaana Technologies Pvt. Ltd.
# SPDX-License-Identifier: Apache License, Version 2.0
# Original Author: Dhanya Shree S

"""Live PPG plot, driven straight from ppg_analysis over a pipe.

    python ppg_plot.py -i <recording> [options]

Python spawns the engine, which keeps writing its own CSV files exactly as it
always has, and additionally streams two kinds of row to stdout under -p on:

    index,raw,smoothed,foot,peak        per sample
    R,...                               per analysis window

Python reads those and plots them. It never writes to the engine, so the classic
pipe deadlock -- child blocked on a full stdout while the parent is blocked on a
full stdin -- has no cycle to close. That single fact is what keeps this file
short.

stderr is NOT piped. It is left inherited, so the engine's console log lands in
the terminal in order. That is not laziness: an undrained stderr pipe fills and
blocks the engine part-way through a message, which is a hang with no
diagnostic, in the very output whose only job is to diagnose things.
"""

import argparse
import glob
import os
import queue
import shutil
import subprocess
import sys
import threading
import time

from display import LivePpgDisplay, MetricCard
from record_adapter import RecordAdapter

DEFAULT_RATE_HZ = 125
# Where the CSVs go. The engine writes them into its working directory, so the
# child is run from here rather than from wherever the operator happened to be
# standing -- otherwise one command run twice from two places scatters output.
#
# Nothing here READS those files. Everything the display draws arrives on the
# pipe; the CSVs are the engine's own record and this process never opens them.
DEFAULT_OUTDIR = "run"


def run_display(result_queue: "queue.Queue[dict]",
                adapter: RecordAdapter,
                record_name: str | None = None) -> int:
    """
    Show the window, after two adjustments display.py cannot make for itself.

    An eighth card is added, for HRV mean NN. display.py defines seven and none
    of them is for that value, so the card is made here and the second row is
    re-laid so the four HRV figures read in a sensible order: the mean first,
    then the three measures of spread around it. Because display.py knows
    nothing about the new card it cannot fill it either, hence the small timer
    below that reads the latched value from the adapter.

    The legend loses two of its six rows. Those two describe a second detector
    this front end never draws, so they are always empty -- and because the
    legend is opaque and sits at the top-left, the extra height covered the
    detection signal's markers once the waveforms were stacked close together.
    Measured on the neonate recording, 8 of 64 marks disappeared behind it.
    Dropping the dead rows clears every one, on both recordings, with the legend
    left where it is. Moving it does not work: there is no free corner once the
    two curves fill the plot, and bottom-anchored positions cover the foot
    markers instead of the peaks.

    Both are done HERE so that display.py itself stays untouched. Otherwise this
    is exactly what its own show_display() does.
    """
    from PySide6.QtWidgets import QApplication

    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import QGridLayout

    app = QApplication.instance() or QApplication([])
    display = LivePpgDisplay(result_queue, record_name=record_name)

    # The card grid is the second item in the window's root layout. Located by
    # type rather than by index, so an added row elsewhere does not break it.
    grid = None
    root = display.centralWidget().layout()
    for i in range(root.count()):
        candidate = root.itemAt(i).layout()
        if isinstance(candidate, QGridLayout):
            grid = candidate
            break

    mean_card = None
    if grid is not None:
        # The second row is rebuilt so the four HRV figures read in a sensible
        # order: the mean first, then the three measures of spread around it.
        # display.py has no card for the mean, so one is made here.
        for card in (display.sdnn_card, display.rmssd_card, display.pnn50_card):
            grid.removeWidget(card)

        mean_card = MetricCard("~", "HRV Mean NN", "#7dd3fc")
        for column, card in enumerate((mean_card, display.sdnn_card,
                                       display.rmssd_card, display.pnn50_card)):
            grid.addWidget(card, 1, column)

        def _show_mean():
            value = adapter.metrics.get("hrv_mean_nn_ms")
            # Same rule as every other card: absent means "--", never a zero.
            mean_card.set_value("--" if value is None else "%.1f ms" % value)

        mean_timer = QTimer(display)
        mean_timer.timeout.connect(_show_mean)
        mean_timer.start(50)                      # display.py's own refresh rate
        _show_mean()

    legend = getattr(display.chart.plotItem, "legend", None)
    if legend is not None:
        for curve in (display.peak2_curve, display.foot2_curve):
            try:
                legend.removeItem(curve)
            except Exception:
                # A pyqtgraph that names this differently is not worth failing
                # over: a slightly tall legend is a cosmetic loss, not a fault.
                pass

    display.show()
    return app.exec()


def project_root() -> str:
    """The tree this script lives in: Monitor/python_plot/ -> two levels up."""
    here = os.path.dirname(os.path.abspath(__file__))
    return os.path.abspath(os.path.join(here, os.pardir, os.pardir))


def find_engine(explicit: str | None) -> str:
    """
    Locate the ppg_analysis binary.

    Looked for in this file's own project root first, because that is where the
    Makefile puts it, and a stale copy on PATH producing subtly different
    numbers is a genuinely nasty afternoon.
    """
    if explicit:
        # Resolved against the CALLER's directory, because the engine is run
        # from the output directory: a relative --exe such as ./ppg_analysis
        # would otherwise be looked for in there. A bare name is left alone, so
        # a copy on PATH still works.
        looks_like_a_path = (os.sep in explicit
                             or (os.altsep and os.altsep in explicit)
                             or os.path.isfile(explicit))
        return os.path.abspath(explicit) if looks_like_a_path else explicit

    root = project_root()

    for name in ("ppg_analysis.exe", "ppg_analysis"):
        candidate = os.path.join(root, name)
        if os.path.isfile(candidate):
            return candidate

    return os.path.join(root, "ppg_analysis.exe" if os.name == "nt"
                              else "ppg_analysis")


def engine_is_stale(engine: str) -> bool:
    """
    Is the binary missing, or older than any source it was built from?

    Checked so that ONE command is enough after an edit. Silently running a
    binary that predates the source is how a fix gets declared not to work.
    """
    if not os.path.isfile(engine):
        return True

    root = project_root()
    built = os.path.getmtime(engine)

    for pattern in ("src/*.c", "include/*.h", "Makefile"):
        for path in glob.glob(os.path.join(root, pattern)):
            if os.path.getmtime(path) > built:
                return True

    return False


def build_engine(engine: str) -> None:
    """
    Run the project's own Makefile.

    CC is forced to gcc only when the Makefile's default of `cc` is not on PATH,
    which is the normal state of a MinGW install -- so the common case needs no
    argument and an unusual toolchain is not overridden.
    """
    root = project_root()
    make = None

    for candidate in ("mingw32-make", "make", "gmake"):
        if shutil.which(candidate):
            make = candidate
            break

    if make is None:
        raise SystemExit(
            "The engine needs building and no make was found on PATH.\n"
            "  Build it by hand:  cd %s && make\n"
            "  or point at an existing binary with --exe" % root
        )

    command = [make]
    if not shutil.which("cc") and shutil.which("gcc"):
        command.append("CC=gcc")

    print("ppg_plot: building the engine -- %s" % " ".join(command),
          file=sys.stderr)

    result = subprocess.run(command, cwd=root, stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT)
    output = result.stdout.decode("utf-8", errors="replace").strip()

    if result.returncode != 0:
        raise SystemExit("The engine did not build:\n%s" % output)

    if not os.path.isfile(engine):
        raise SystemExit(
            "make succeeded but %s is not there. Check the Makefile's BIN name."
            % engine
        )

    print("ppg_plot: built %s" % engine, file=sys.stderr)


def build_command(args: argparse.Namespace, engine: str) -> list[str]:
    """Assemble the engine's command line."""
    command = [
        engine,
        "-i", args.input,
        "-r", str(args.rate),
        "-c", str(args.count),
        "-p", "on",
    ]

    # -nu is passed ONLY if the operator named one. Omitted, the engine decides
    # the scale from the input text, which is what it should do: a wrong -nu is
    # the commonest way to get nothing out.
    if args.scale is not None:
        command += ["-nu", str(args.scale)]
    if args.subject:
        command += ["-s", args.subject]
    if args.detector:
        command += ["-d", args.detector]
    if args.prefix:
        command += ["-o", args.prefix]

    return command


def spawn_engine(command: list[str], workdir: str) -> subprocess.Popen:
    """
    Start the engine, with a usable message when it will not start.

    WinError 1260 is worth catching by name. A freshly linked, unsigned
    ppg_analysis.exe can be refused by Windows Smart App Control for several
    minutes after a build, and the default message sends the reader hunting for
    a build error that is not there. That matters more now that one command may
    have just built it.
    """
    try:
        return subprocess.Popen(
            command,
            cwd=workdir,                # the engine writes its CSVs here
            stdout=subprocess.PIPE,     # the plot stream
            stderr=None,                # inherited: the log goes to the terminal
            stdin=subprocess.DEVNULL,   # nothing is ever sent to the engine
            bufsize=0,                  # the engine flushes per row; do not re-buffer
        )
    except OSError as error:
        if getattr(error, "winerror", None) == 1260:
            raise SystemExit(
                "The engine was BLOCKED, not missing: Windows Smart App Control "
                "refused\n  %s\nA freshly built, unsigned binary is often "
                "refused for a few minutes after linking. Wait and run the same "
                "command again, or use --exe to point at an allowed copy."
                % command[0]
            ) from error
        raise SystemExit(
            "Cannot start the engine %s: %s" % (command[0], error)
        ) from error


def read_plot_stream(
    process: subprocess.Popen,
    adapter: RecordAdapter,
    result_queue: "queue.Queue[dict]",
    running: threading.Event,
    rate_hz: int,
    turbo: bool,
    use_metrics: bool,
) -> None:
    """
    Read the plot stream and post one display record per sample.

    PACING IS DONE HERE, ON THE READS, and it needs no cooperation from the
    engine. The engine will consume a ten-minute recording in about two seconds,
    and a display fed that fast shows nothing a human can read. Reading roughly
    `rate_hz` rows a second fills the OS pipe, the engine blocks in write(), and
    the whole pipeline settles at real time. Bounded memory, no queue to size,
    and no timing code in the signal processing.

    A malformed row is counted and skipped, never raised. Letting it escape
    would stop the drain, and the engine would then block on a full stdout with
    the plot frozen and no error shown.
    """
    interval = 1.0 / float(rate_hz) if rate_hz > 0 else 0.0
    next_due = time.perf_counter()
    bad = 0
    posted = 0

    stream = process.stdout
    if stream is None:
        return

    try:
        for raw in stream:
            if not running.is_set():
                break

            line = raw.decode("ascii", errors="replace").strip()
            if not line:
                continue

            fields = line.split(",")

            # TWO ROW SHAPES, TOLD APART BY ONE CHARACTER.
            #
            # A per-sample row starts with a digit or a sign; a per-window
            # metrics row starts with 'R'. Dispatching on the first character
            # keeps the hot path -- 125 rows a second -- free of any tag to
            # write or skip, while the rare row that needs distinguishing says
            # so plainly.
            if line[0] == "H":
                if not use_metrics:
                    continue
                if not adapter.update_from_hr_row(fields[1:]):
                    bad += 1
                    if bad == 1:
                        print("ppg_plot: ignoring short beat row: %r" % line,
                              file=sys.stderr)
                continue

            if line[0] == "R":
                if not use_metrics:
                    continue
                if not adapter.update_from_rr_row(fields[1:]):
                    bad += 1
                    if bad == 1:
                        print("ppg_plot: ignoring short metrics row: %r" % line,
                              file=sys.stderr)
                continue

            if len(fields) != 5:
                bad += 1
                if bad == 1:
                    print("ppg_plot: ignoring malformed plot row: %r" % line,
                          file=sys.stderr)
                continue

            try:
                index = int(fields[0])
                raw_sample = int(fields[1])
                sample = int(fields[2])
                foot = int(fields[3])
                peak = int(fields[4])
            except ValueError:
                bad += 1
                if bad == 1:
                    print("ppg_plot: ignoring non-numeric plot row: %r" % line,
                          file=sys.stderr)
                continue

            result_queue.put(
                adapter.record(index, raw_sample, sample, foot, peak))
            posted += 1

            if not turbo:
                next_due += interval
                delay = next_due - time.perf_counter()
                if delay > 0:
                    time.sleep(delay)
                else:
                    next_due = time.perf_counter()
    except (OSError, ValueError) as error:
        print("ppg_plot: plot stream ended: %s" % error, file=sys.stderr)

    # The engine has closed its end. Whether that was the end of the recording
    # or a crash is the difference between a finished plot and a broken one, so
    # it is reported either way rather than left to look the same.
    code = process.wait()
    print("ppg_plot: %d samples and %d metrics row(s) plotted, %d malformed; "
          "engine exit %d" % (posted, adapter.window_count, bad, code),
          file=sys.stderr)

    if code != 0:
        result_queue.put({
            "type": "error",
            "fatal": True,
            "message": "engine exited with status %d -- see the log above" % code,
        })

    running.clear()


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Live PPG plot driven from ppg_analysis over a pipe.")

    parser.add_argument("-i", "--input", required=True,
                        help="recording to analyse. '-' or 'stdin' makes the "
                             "engine read stdin, which then needs a feeder")
    parser.add_argument("-r", "--rate", type=int, default=DEFAULT_RATE_HZ,
                        help="sampling rate in Hz (default %d)" % DEFAULT_RATE_HZ)
    parser.add_argument("-nu", "--scale", type=int, default=None,
                        help="input scale. Omit to let the engine decide it "
                             "from the input text")
    parser.add_argument("-s", "--subject", default=None,
                        choices=["neonate", "child", "adult"],
                        help="patient type (default: the engine's own)")
    parser.add_argument("-d", "--detector", default=None,
                        choices=["ims", "terma"],
                        help="override the patient type's detector")
    parser.add_argument("-c", "--count", type=int, default=0,
                        help="samples to analyse, 0 = the whole file (default)")
    parser.add_argument("-o", "--prefix", default=None,
                        help="output prefix, so successive runs do not "
                             "overwrite one another")
    parser.add_argument("--exe", default=None,
                        help="path to the ppg_analysis binary")
    parser.add_argument("--outdir", default=None,
                        help="where the CSVs are written (default: run/ under "
                             "the project, created if missing)")
    parser.add_argument("--no-build", action="store_true",
                        help="never invoke make, even if the binary is missing "
                             "or older than the sources")
    parser.add_argument("--turbo", action="store_true",
                        help="do not pace the reads; plot as fast as the "
                             "engine will go")
    parser.add_argument("--no-display", action="store_true",
                        help="headless: drain the stream and print a summary")
    parser.add_argument("--no-stack", action="store_true",
                        help="draw both waveforms at their true amplitudes "
                             "instead of stacking raw above the detection "
                             "signal")
    parser.add_argument("--no-cards", action="store_true",
                        help="ignore the R rows on the stream, so the metric "
                             "cards stay at '--'")

    args = parser.parse_args()

    if args.rate <= 0:
        parser.error("--rate must be greater than zero")
    if args.count < 0:
        parser.error("--count must be zero or more")

    engine = find_engine(args.exe)

    # ONE COMMAND SHOULD BE ENOUGH. Build only when the binary is missing or
    # older than a source; an up-to-date tree costs a few stat() calls and no
    # output. --exe means the operator chose a binary, so it is left alone.
    if not args.no_build and not args.exe and engine_is_stale(engine):
        build_engine(engine)

    outdir = os.path.abspath(args.outdir or
                             os.path.join(project_root(), DEFAULT_OUTDIR))
    os.makedirs(outdir, exist_ok=True)

    # Resolved BEFORE the child is given a different working directory, or a
    # relative recording path would be looked for in the wrong place.
    if args.input not in ("-", "stdin"):
        args.input = os.path.abspath(args.input)
        if not os.path.isfile(args.input):
            raise SystemExit("No such recording: %s" % args.input)

    command = build_command(args, engine)

    print("ppg_plot: %s" % " ".join(command), file=sys.stderr)
    print("ppg_plot: writing CSVs to %s" % outdir, file=sys.stderr)

    adapter = RecordAdapter(rate_hz=args.rate,
                            stack=not args.no_stack)
    result_queue: "queue.Queue[dict]" = queue.Queue()
    running = threading.Event()
    running.set()

    process = spawn_engine(command, outdir)

    reader = threading.Thread(
        target=read_plot_stream,
        args=(process, adapter, result_queue, running, args.rate, args.turbo,
              not args.no_cards),
        name="ppg-plot-reader",
        daemon=True,
    )
    reader.start()

    try:
        if args.no_display:
            reader.join()
        else:
            # Qt owns the main thread, as it must.
            # The recording's own name, not its path: the window says whose
            # readings these are, and a directory tree in the top bar would
            # crowd out the thing that matters. A piped run has no name to
            # show, so it says where the samples came from instead.
            if args.input in ("-", "stdin"):
                shown = "stdin"
            else:
                shown = os.path.splitext(os.path.basename(args.input))[0]
            run_display(result_queue, adapter, shown)
    finally:
        running.clear()
        if process.poll() is None:
            # The window was closed mid-run. Ask, then insist, so a closed
            # window never leaves an orphan engine behind.
            process.terminate()
            try:
                process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                process.kill()

    return 0 if process.returncode in (0, None) else process.returncode


if __name__ == "__main__":
    raise SystemExit(main())
