#!/usr/bin/env python3
# Copyright (c) 2026 Prajnaana Technologies Pvt. Ltd.
#
# SPDX-License-Identifier: Apache-2.0
#
# Original Author: Dhanya Shree S
"""
send_bt_win.py -- (WINDOWS) stream a PPG recording to the Android PPG Monitor
over a Bluetooth virtual COM port (SPP).

Pair the phone in Windows Settings, then add an *Outgoing* COM port for its SPP
service (More Bluetooth settings -> COM Ports -> Add -> Outgoing -> phone).
Note the port it assigns, e.g. COM5.

Prereqs:
    pip install pyserial

Usage:
    python send_bt_win.py <COM_PORT> <record_file> [rate_hz] --subject TYPE
Example:
    python send_bt_win.py COM5 synthesized_sample_adult.txt 125 --subject adult
    python send_bt_win.py COM5 synthesized_sample_neonate.txt 125 --subject neonate

TYPE is neonate, child or adult, and is REQUIRED: it selects the beat detector
and the heart-rate band the app analyses with, and a wrong one is silent -- the
trace still draws and the rates read plausibly but wrongly. It is named here
rather than defaulted because this command is the only place that knows which
recording is being sent. It is announced to the app, with the rate, in a header
line ahead of the samples, so the app needs no rebuild to follow it.
"""
import sys
import time
from pathlib import Path
import serial                          # pip install pyserial


# ---------------------------------------------------------------------------
# THE RECORDING READER -- CHOOSING THE INPUT SCALE INSTEAD OF BEING TOLD IT.
#
# Carried here from what used to be a separate ppg_source.py, so that this
# sender is one file with nothing beside it to copy or forget.
#
# The recordings are plain text with no declared scale. A BIDMC file holds
# values like 0.43597; the engine works in integer counts, so every one of them
# truncates to 0 unless multiplied first. Getting --scale wrong is the
# commonest way to get nothing out, and it does not look like a wrong flag: no
# beat is ever found, every card sits at "--", and the display looks like a
# broken engine.
#
# So the scale is worked out from the text, by the same rule the C engine uses
# for -nu auto, and the numbers below are that rule's numbers rather than new
# ones. The decision is REPORTED, because a monitor that quietly picks a scale
# is worse than one that says which it picked -- the choice changes every
# amplitude on the trace, even though it changes no rate.
#
# The decision is LEXICAL: it reads the digits after the decimal point in the
# text, not the value. Text is what distinguishes "0.5" from "0.500000" -- the
# same number, written by instruments of different resolution -- and the second
# deserves the larger multiplier.
# ---------------------------------------------------------------------------
NU_TARGET_COUNTS = 100000.0     # enough resolution that even RMSSD has converged
NU_SAFE_MAX = 1.0e9             # and never past this, so the int cast cannot overflow
NU_AUTO_MAX_DIGITS = 6          # 10^6 is resolution enough
NU_PROBE_SAMPLES = 2000         # how much of the file the decision is based on


def _fractional_digits(token: str) -> int:
    """How many digits this token writes after its decimal point."""
    if "." not in token:
        return 0
    tail = token.split(".", 1)[1]
    # An exponent is not a fractional digit: 1.5e-3 has one written digit.
    for marker in ("e", "E"):
        if marker in tail:
            tail = tail.split(marker, 1)[0]
            break
    return len(tail)


def detect_scale(filename: str | Path, probe: int = NU_PROBE_SAMPLES) -> tuple[int, str]:
    """
    Decide the multiplier for one recording. Returns (scale, why).

    No '.' anywhere means integer counts, which are already usable, so the file
    is left exactly alone -- a recording that ran before this existed produces
    the identical trace. An all-zero probe is left alone too: there is nothing
    in it to size a scale from, and saying so beats inventing one.
    """
    frac_max = 0
    value_max = 0.0
    seen = 0

    with Path(filename).open("r", encoding="utf-8-sig") as file:
        for line in file:
            token = line.strip()
            if not token:
                continue
            try:
                value = float(token)
            except ValueError:
                continue

            frac_max = max(frac_max, _fractional_digits(token))
            value_max = max(value_max, abs(value))
            seen += 1

            if seen >= probe:
                break

    if seen == 0:
        return 1, "no readable samples; left at 1"

    digits = min(frac_max, NU_AUTO_MAX_DIGITS)
    scale = 1

    if digits > 0 and value_max > 0.0:
        scale = 10 ** digits
        # Up until the largest sample is worth enough counts to measure.
        while scale * value_max < NU_TARGET_COUNTS and scale <= (2**31 - 1) // 10:
            scale *= 10
        # And back down if that overshot what an int can hold.
        while scale > 1 and scale * value_max > NU_SAFE_MAX:
            scale //= 10

    kind = "integer text" if frac_max == 0 else "decimal text"
    why = "%s, %d fractional digit%s, largest |value| %g over %d samples" % (
        kind, frac_max, "" if frac_max == 1 else "s", value_max, seen
    )
    return scale, why


def resolve_recording(name: str | Path) -> Path:
    """
    Find the recording named on the command line.

    A path that exists is taken exactly as given. A bare filename that does not
    is then looked for beside this script and in the folder above it, which is
    where the supplied recordings sit while the senders keep their own
    subfolder. That way the documented command works from either place, and a
    path that was always correct still means what it said.
    """
    given = Path(name)
    if given.exists():
        return given

    tried = [given.resolve()]
    if given.parent == Path("."):           # a bare name, so ours to look for
        here = Path(__file__).resolve().parent
        for candidate in (here / given.name, here.parent / given.name):
            if candidate.exists():
                print(f"Recording: {candidate}")
                return candidate
            if candidate not in tried:      # cwd may already be one of these
                tried.append(candidate)

    raise FileNotFoundError(
        "cannot find the recording %r. Looked in:\n  %s"
        % (str(name), "\n  ".join(str(path) for path in tried))
    )


class FilePpgSource:
    def __init__(self, filename: str | Path, scale: int | None = None) -> None:
        """
        ``scale=None`` means work it out from the file. An explicit number is
        still obeyed exactly, so anything that passed one keeps its behaviour.
        """
        self.filename = Path(filename)

        if scale is None:
            self.scale, reason = detect_scale(self.filename)
            self.scale_auto = True
            self.scale_reason = reason
            print(f"Input scale: --scale {self.scale} (auto: {reason})")
        else:
            self.scale = scale
            self.scale_auto = False
            self.scale_reason = "given on the command line"

    def samples(self):
        with self.filename.open("r", encoding="utf-8-sig") as file:
            for line in file:
                line = line.strip()
                if not line:
                    continue

                try:
                    yield int(float(line) * self.scale)
                except ValueError:
                    continue


SUBJECTS = ("neonate", "child", "adult")


def take_subject(argv):
    """
    Pull an optional --subject out of the argument list, returning it and the
    arguments that remain. It is a flag rather than a positional because the
    senders do not agree on what their third argument is, and a patient type
    silently landing in a port or a rate would be worse than not offering it.
    """
    rest = []
    subject = None
    i = 0
    while i < len(argv):
        arg = argv[i]
        if arg == "--subject" and (i + 1) < len(argv):
            subject = argv[i + 1]
            i += 2
            continue
        if arg.startswith("--subject="):
            subject = arg.split("=", 1)[1]
            i += 1
            continue
        rest.append(arg)
        i += 1

    if subject is not None:
        subject = subject.strip().lower()
        if subject not in SUBJECTS:
            raise SystemExit(
                "--subject must be one of: %s (got %r)" % (", ".join(SUBJECTS), subject)
            )
    return subject, rest


def header_line(rate_hz, subject):
    """
    The configuration the app cannot work out for itself.

    The rate is always stated: it is the pacing this sender was told to use, so
    it is known exactly here and merely guessable there. The patient type is
    stated only when given, which leaves an unflagged run on whatever the app
    was built with -- the behaviour every existing script already relies on.

    An app that predates the header reads this line as neither an integer nor a
    float and skips it, so sending one is safe against any version.
    """
    fields = ["rate=%g" % rate_hz]
    if subject:
        fields.append("subject=%s" % subject)
    return "#ppg " + " ".join(fields) + "\n"


def main() -> int:
    subject, argv = take_subject(sys.argv)
    if len(argv) < 3:
        print(__doc__)
        return 2
    if subject is None:
        print(__doc__)
        print("** --subject is required: one of %s" % ", ".join(SUBJECTS))
        return 2
    port    = argv[1]                                      # e.g. COM5
    record  = argv[2]
    rate_hz = float(argv[3]) if len(argv) > 3 else 125.0
    period  = 1.0 / rate_hz

    try:
        record = resolve_recording(record)
    except FileNotFoundError as err:
        print(err)
        return 1

    print(f"opening {port} (this initiates the Bluetooth connection) ...")
    # Baud is ignored for a Bluetooth virtual COM port, but a value is required.
    sock = serial.Serial(port, baudrate=115200, timeout=1)
    print("connected; streaming (Ctrl-C to stop)")

    sock.write(header_line(rate_hz, subject).encode("ascii"))

    src = FilePpgSource(record)          # yields integer counts, auto-scaled
    n = 0
    next_t = time.monotonic()
    try:
        for sample in src.samples():
            sock.write(f"{sample}\n".encode("ascii"))
            n += 1
            if n % int(rate_hz) == 0:
                print(f"  sent {n} samples ({n / rate_hz:.0f}s)")
            next_t += period
            delay = next_t - time.monotonic()
            if delay > 0:
                time.sleep(delay)
    except KeyboardInterrupt:
        print("\nstopped by user")
    finally:
        sock.close()
        print(f"done, {n} samples sent")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
