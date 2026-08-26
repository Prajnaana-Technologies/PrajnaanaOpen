# Copyright (c) 2026 Prajnaana Technologies Pvt. Ltd.
# SPDX-License-Identifier: Apache License, Version 2.0
# Original Author: Dhanya Shree S

"""Turn the engine's plot stream into the records display.py expects.

Everything arrives on one pipe, in two row shapes at very different rates:

  * per sample, at the sampling rate -- ``index,raw,smoothed,foot,peak``;
  * per analysis window, every 8.2 s of signal -- an ``R`` row carrying the
    rate and its variability.

No file is read. This module owns the join between the two, and the window
values are LATCHED: once a window has reported, its numbers ride along on every
subsequent sample record, so a card holds its reading between windows instead
of blinking.
"""

# The per-window row, by position, AFTER the leading "R" has been stripped.
# This is the engine's stream format -- a subset of what it writes to its own
# CSV, carrying only the values something on screen actually uses. Named here
# rather than read from a header so that a change upstream fails loudly on the
# next run instead of quietly mapping one metric onto another.
RR_TIME_S = 0
RR_AVG_RR = 1
RR_METHOD = 2
RR_N_USED = 3
RR_SPREAD = 4
RR_RRV_SD = 5
RR_RRV_RMSSD = 6
RR_RRV_N = 7
RR_HR_BPM = 8
RR_HRV_MEAN = 9
RR_HRV_SDNN = 10
RR_HRV_RMSSD = 11
RR_HRV_PNN50 = 12
RR_COLUMNS = 13

# Appended later, and OPTIONAL: a row that stops at RR_COLUMNS is still valid,
# so an older engine keeps working. The two window figures say how far through
# its warm-up the respiratory estimator is -- it doubles its window until it
# reaches the subject's full one -- and HRV_n is the interval count the HRV
# figures rest on.
RR_WINDOW_S = 13
RR_WINDOW_FULL_S = 14
RR_HRV_N = 15
# The three surrogates, their spectral prominences, and the two thresholds they
# are judged by. The thresholds travel with them because both are per-subject --
# hard-coding the adult values would quietly mis-explain every neonatal window.
RR_AM = 16
RR_BW = 17
RR_FM = 18
RR_AM_Q = 19
RR_BW_Q = 20
RR_FM_Q = 21
RR_AGREE_BPM = 22
RR_MIN_PROM = 23

# The per-beat row, after the leading "H" has been stripped. It exists because
# the per-window row does not appear until the respiratory estimator reports,
# which on one of the shipped recordings is 16.8 s after a heart rate was first
# knowable. Two fields and no more: variability needs history and stays on the
# window row, where the interval count says what it rests on.
HR_TIME_S = 0
HR_BPM = 1
HR_COLUMNS = 2

# HOW LONG A READING SURVIVES A WINDOW THAT DECLINED.
#
# A window is declined when the three respiratory surrogates disagree, and on a
# real recording those declines ARRIVE IN RUNS: on bidmc_01 they run for 81 s
# and then 41 s, during which the respiratory card went blank while heart rate
# and the variability figures kept updating beside it.  Blanking a vital sign
# for a minute and a half, with nothing said about why, is not what a monitor
# does -- it holds the last reading and shows its age.
#
# THIS IS NOT A DELAY.  A window that reports a rate is shown the instant it
# arrives, with no age against it -- the hold applies only where there is
# nothing new to show, which is a window the engine DECLINED.  Nothing waits on
# this constant.
#
# It must not hold FOREVER, which is the hazard the blanking was guarding
# against: a number with nothing behind it is worse than a visible gap.  One
# analysis window is 65.536 s, so a reading held for up to 60 s still overlaps
# the span the engine is working on now.  Past that it describes breathing the
# current window no longer contains, and it goes to "--".
HOLD_S = 60.0


def _measured(text, zero_is_a_reading=False):
    """
    Parse one value into a number, or None if it is not a measurement.

    TWO THINGS MEAN "NOT MEASURED":

      * an empty or non-numeric cell;
      * a NEGATIVE value -- the engine's ``-1`` sentinel, written when the
        respiratory surrogates disagree or no credible spectral peak exists.

    ZERO IS NOT ONE OF THEM, and treating it as one was a bug.  ``pNN50 = 0 %``
    is a real reading: it says no pair of successive intervals differed by more
    than 50 ms, which is what a very regular rhythm looks like.  Measured on the
    shipped recordings it is 10 of 57 windows, and every one of them showed
    "--" as though nothing had been measured.

    Zero is still refused for the rates and intervals, because a heart rate or
    a mean interval of zero is not a reading of anything -- hence the flag
    rather than a blanket rule.  Of the fields on the stream only pNN50 (and
    the spread, which nothing displays) can legitimately be zero.

    display.py renders None as "--" and any number as itself, so returning a
    number here that nothing supports is the one thing a monitor must not do.
    """
    text = text.strip()

    if not text:
        return None

    try:
        value = float(text)
    except ValueError:
        return None

    if value < 0.0:
        return None
    if value == 0.0 and not zero_is_a_reading:
        return None

    return value


SURROGATE_NAMES = ("AM", "BW", "FM")


def _why(values, proms, agree, prom_floor):
    """Which surrogate is the odd one out, in one short phrase.

    This is not a second opinion: with the three estimates, their prominences
    and the two thresholds all on the row, the engine's own verdict is
    reproducible here, so the phrase describes what the engine did rather than
    guessing at it.

    A surrogate below the prominence floor never entered the vote -- its
    spectrum had no peak to speak of -- and that is a different statement from
    disagreeing, so it is worded differently.
    """
    if agree is None or prom_floor is None:
        return None
    if any(v is None or q is None for v, q in zip(values, proms)):
        return None

    weak = [SURROGATE_NAMES[i] for i in range(3)
            if values[i] > 0.0 and proms[i] < prom_floor]
    voting = [i for i in range(3)
              if values[i] > 0.0 and proms[i] >= prom_floor]

    if len(voting) < 2:
        return ("%s has no peak" % "/".join(weak)) if weak else None

    # The largest set that mutually agrees. One outlier against a pair is worth
    # naming; three that all disagree is worth saying plainly.
    best = ()
    for i in voting:
        group = tuple(j for j in voting if abs(values[j] - values[i]) <= agree)
        if len(group) > len(best):
            best = group
    if len(best) < 2:
        return "all three differ"
    odd = [SURROGATE_NAMES[i] for i in voting if i not in best]
    if odd:
        return "%s differs" % "/".join(odd)
    return None


def _warming(window_s, window_full_s):
    """"Warming up" with how much longer, when the engine has said.

    A bare "warming up" that sits there for half a minute is indistinguishable
    from a stall.  The engine reports progressively, doubling its window until
    it reaches the subject's full one, and it now says on each row where it has
    got to -- so the wait can be shown as a wait rather than as nothing
    happening.
    """
    if not window_s or not window_full_s or window_full_s <= 0.0:
        return "Warming up"
    if window_s >= window_full_s:
        return "Warming up"
    return "Warming up %.0f of %.0f s" % (window_s, window_full_s)


def _rr_state(method, rate_seen=False, window_s=None, window_full_s=None,
              why=None):
    """Say what the engine did with this window, in the reader's language.

    The engine's `Method` already carries this and the adapter was already
    parsing it -- it simply had no reader.  DECLINED is not an error and should
    not read like one: it means the three surrogates did not agree closely
    enough to stand behind a number, which is the gate working.

    BEFORE THE FIRST RATE, EVERY OUTCOME IS WARM-UP.  The respiratory window
    grows from about 8 s to 65 s, so the earliest windows have too little to
    find a peak in and are rejected for exactly that reason -- on bidmc_04 the
    first window reports all three prominences at 1.00, meaning a flat spectrum.
    Calling that "no clear peak" reads as a fault at the one moment it is
    guaranteed to be benign, so it does not say so until a rate has been
    reported at least once and the phrase can mean something.
    """
    if not method:
        return _warming(window_s, window_full_s)
    if method.startswith("ALL_THREE"):
        state = "Strong match"
    elif method.startswith("TWO_AGREE"):
        state = "Moderate match"
    elif method.startswith("DECLINED"):
        if not rate_seen:
            return _warming(window_s, window_full_s)
        return ("Searching \u00b7 %s" % why) if why else "Searching"
    elif method.startswith("REJECTED"):
        return ("No clear peak" if rate_seen
                else _warming(window_s, window_full_s))
    else:
        state = method
    return state + (" \u00b7 warm-up" if method.endswith("_PROV") else "")


# ---------------------------------------------------------------------------
# STACKING THE TWO WAVEFORMS: RAW ON TOP, DETECTION SIGNAL BELOW.
#
# Left at their true amplitudes the two curves land wherever their own offsets
# put them, and that is not somewhere useful. The raw recording carries whatever
# DC the sensor had, while the band-pass has already taken the DC out of the
# detection signal -- so on one of the shipped recordings raw sits above, and on
# the other it sits BELOW, which makes the display inconsistent from one file to
# the next.
#
# So each curve is placed deliberately. Three steps, all of them running
# estimates because this has to work on a stream:
#
#   1. Remove each curve's own slow baseline, which brings both onto zero. The
#      time constant is 8.0 s -- the band-pass's own high-pass corner of
#      0.02 Hz expressed as 1/(2*pi*f), so we remove the same slow content the
#      filter already removes rather than a number picked to look nice.
#   2. Track each curve's envelope, as a running peak that decays over about
#      ten seconds. That is what says how much vertical room each one needs.
#   3. Lift raw by its own envelope and drop the detection signal by its own,
#      leaving a small gap between them.
#
# The gap is a fraction of the two envelopes rather than a fixed number of
# counts, because the recordings differ in scale by three orders of magnitude --
# anything absolute would be invisible on one and enormous on the other. At 4 %
# of the combined envelope it measures under 1 % of the plotted height on both
# shipped recordings, and the curves never touch.
#
# This is a DISPLAY transform and nothing else. The stream still carries the
# true values, the CSV files are untouched, and the marker values in the record
# stay as the true amplitudes.
# ---------------------------------------------------------------------------
BASELINE_TAU_S = 8.0       # matches BP_HP_CORNER_HZ = 0.02 Hz
ENVELOPE_HOLD_S = 10.0     # how long a peak keeps influencing the spacing
STACK_GAP = 0.04           # of the reserved room
# How much of each envelope to reserve. Reserving the FULL envelope keeps the
# curves apart at their worst moment, but a PPG spends most of its time nowhere
# near its peak, so the two would sit much further apart than they need to for
# almost the whole recording -- measured at 28 % of the plot height typically,
# against a worst case of under 1 %.
#
# Half the envelope roughly halves that typical distance and still never lets
# the curves touch. Measured over both shipped recordings: at 0.5 the typical
# separation is 17 % of the height with zero crossings in 5 441 samples, while
# 0.4 begins to collide, at 312 samples. So this is the tighter of the two
# settings that still guarantees clearance, not the tightest that happened to
# look acceptable.
STACK_ROOM = 0.5


class _Track:
    """One curve's baseline and envelope, both as running estimates."""

    def __init__(self, rate_hz: float) -> None:
        self._alpha = 1.0 / max(BASELINE_TAU_S * float(rate_hz), 1.0)
        self._decay = 1.0 - 1.0 / max(ENVELOPE_HOLD_S * float(rate_hz), 1.0)
        self._mean: float | None = None
        self.envelope = 1.0    # never zero, so the first gap is well defined

    def centre(self, value: int) -> float:
        """Subtract the slow baseline and update the envelope."""
        # Seeded from the first sample rather than from zero, or the curve opens
        # with a large false excursion and drags the autoscale with it.
        if self._mean is None:
            self._mean = float(value)
        else:
            self._mean += self._alpha * (float(value) - self._mean)
        centred = float(value) - self._mean
        self.envelope = max(abs(centred), self.envelope * self._decay)
        return centred


class RecordAdapter:
    """Join the per-sample plot stream to the per-window RR metrics."""

    def __init__(self, rate_hz: float = 125.0, stack: bool = True) -> None:
        self._metrics: dict = {}
        self._ages: dict = {}
        self._held: dict = {}        # name -> (value, window time it came from)
        self._status: str | None = None
        self._rr_state: str = "Warming up"
        self._rate_seen: bool = False
        self._window_s: float | None = None
        self._window_full_s: float | None = None
        self._hrv_n: int | None = None
        self._why: str | None = None
        self.window_count = 0
        self._stack = stack
        self._raw_track = _Track(rate_hz)
        self._smooth_track = _Track(rate_hz)

    # ---------------------------------------------------------------- windows
    def update_from_rr_row(self, fields: list[str]) -> bool:
        """
        Latch one per-window row. Returns True if the row was usable.

        A short row is ignored rather than padded, because half a row of
        metrics is worse than none: padding would put zeroes into cards, and
        _measured would then read those zeroes as real measurements of zero.
        """
        if len(fields) < RR_COLUMNS:
            return False

        fresh = {
            "heart_rate_bpm": _measured(fields[RR_HR_BPM]),
            "respiratory_rate_bpm": _measured(fields[RR_AVG_RR]),
            "rrv_sd_ms": _measured(fields[RR_RRV_SD]),
            "rrv_rmssd_ms": _measured(fields[RR_RRV_RMSSD]),
            "rrv_intervals": _measured(fields[RR_RRV_N]),
            "hrv_mean_nn_ms": _measured(fields[RR_HRV_MEAN]),
            "hrv_sdnn_ms": _measured(fields[RR_HRV_SDNN]),
            "hrv_rmssd_ms": _measured(fields[RR_HRV_RMSSD]),
            "hrv_pnn50_pct": _measured(fields[RR_HRV_PNN50], zero_is_a_reading=True),
        }

        try:
            now = float(fields[RR_TIME_S])
        except ValueError:
            now = None

        # Each metric independently: a fresh value replaces and re-dates the
        # held one; a missing value keeps the held one until HOLD_S has passed,
        # carrying its AGE so the display can say it is not current.
        self._metrics = {}
        self._ages = {}
        for name, value in fresh.items():
            if value is not None:
                self._held[name] = (value, now)
                self._metrics[name] = value
                self._ages[name] = 0.0
                continue

            previous = self._held.get(name)
            if previous is None or now is None or previous[1] is None:
                self._metrics[name] = None
                continue

            age = now - previous[1]
            if age <= HOLD_S:
                self._metrics[name] = previous[0]
                self._ages[name] = age
            else:
                self._metrics[name] = None
                self._held.pop(name, None)

        self.window_count += 1

        method = fields[RR_METHOD].strip()
        time_s = fields[RR_TIME_S].strip()
        def _optional(index):
            if len(fields) <= index:
                return None
            try:
                return float(fields[index])
            except ValueError:
                return None

        values = [_optional(RR_AM), _optional(RR_BW), _optional(RR_FM)]
        proms = [_optional(RR_AM_Q), _optional(RR_BW_Q), _optional(RR_FM_Q)]
        self._why = _why(values, proms,
                         _optional(RR_AGREE_BPM), _optional(RR_MIN_PROM))

        self._window_s = _optional(RR_WINDOW_S)
        self._window_full_s = _optional(RR_WINDOW_FULL_S)
        hrv_n = _optional(RR_HRV_N)
        self._hrv_n = int(hrv_n) if hrv_n is not None else None

        if fresh["respiratory_rate_bpm"] is not None:
            self._rate_seen = True
        self._status = "t = %s s - %s" % (time_s, method if method else "no method")
        self._rr_state = _rr_state(method, self._rate_seen,
                                   self._window_s, self._window_full_s,
                                   self._why)
        return True

    # ------------------------------------------------------------------ beats
    def update_from_hr_row(self, fields: list[str]) -> bool:
        """
        Latch one beat's heart rate. Returns True if the row was usable.

        This lands about 1.5 times a second and does not wait on the window, so
        the rate is on screen from the second beat rather than from the first
        respiratory report. It is the engine's own value, sanitised the same way
        the window row's is -- the two cannot disagree, because they are the
        same variable read at different moments.
        """
        if len(fields) < HR_COLUMNS:
            return False

        rate = _measured(fields[HR_BPM])
        if rate is None:
            return False

        try:
            now = float(fields[HR_TIME_S])
        except ValueError:
            now = None

        self._held["heart_rate_bpm"] = (rate, now)
        self._metrics["heart_rate_bpm"] = rate
        self._ages.pop("heart_rate_bpm", None)
        return True

    # ---------------------------------------------------------------- samples
    def record(self, index: int, raw: int, sample: int,
               foot: int, peak: int) -> dict:
        """
        Build one display record from one plot-stream row.

        TWO WAVEFORMS, AND THEY MUST NOT BE SWAPPED.

          * ``raw_value`` is the recording as it arrived, after -nu scaling.
            display.py draws it as the grey "Raw PPG" curve.
          * ``smoothed_value`` is the signal the detectors were actually handed,
            after the band-pass and the smoother. display.py draws it as the
            green "Detection signal" curve, and -- this is the part that
            matters -- takes every marker's HEIGHT from it (``_on_curve``).

        So the marks belong on the smoothed curve and nowhere else. Put the raw
        signal there instead and they sit beside the visible peaks rather than
        on them: raw still carries the baseline wander and the noise the filter
        removed, so its local maximum is not at the fiducial's index.

        ``filtered_value`` is set to the smoothed sample rather than the
        band-pass output, because display.py falls back through it when
        ``smoothed_value`` is absent and a fallback onto a different amplitude
        scale would move the markers. Both are set explicitly so this does not
        depend on that file's internals.

        THE MARKER INDICES ARE EXACT, and it is worth knowing why. A detector
        cannot confirm a systolic peak at the instant it passes, so a naive
        stream would have to report the marker's index separately from the row
        it arrived on.

        This stream does not: each mark travels on the row of the sample it
        actually describes, so ``peak_index == sequence`` by construction. display.py still takes
        the marker's height from the drawn curve, so nothing on its side
        changes; the correction it was written to apply is simply already
        applied.
        """
        # Drawn heights. Both tracks are advanced on EVERY sample, never
        # conditionally, or their baselines and envelopes drift out of step with
        # the signal they are following.
        if self._stack:
            raw_centred = self._raw_track.centre(raw)
            smooth_centred = self._smooth_track.centre(sample)
            raw_room = STACK_ROOM * self._raw_track.envelope
            smooth_room = STACK_ROOM * self._smooth_track.envelope
            gap = STACK_GAP * (raw_room + smooth_room)
            draw_raw = int(round(raw_centred + raw_room + gap / 2.0))
            draw_sample = int(round(smooth_centred - smooth_room - gap / 2.0))
        else:
            draw_raw, draw_sample = raw, sample

        record = {
            "sequence": index,
            "raw_value": draw_raw,
            "filtered_value": draw_sample,
            "smoothed_value": draw_sample,
        }

        # The marker VALUES stay the TRUE smoothed amplitudes rather than the
        # drawn ones. display.py takes each marker's height from the curve
        # itself, so these are read only by whatever else looks at the record,
        # and a real amplitude is more useful there than a placed one.
        if peak:
            record["peak_detected"] = True
            record["peak_index"] = index
            record["peak_value"] = sample
        if foot:
            record["foot_detected"] = True
            record["foot_index"] = index
            record["foot_value"] = sample

        # Attached on EVERY record, including while still empty.  A metric that
        # stops being available now holds its last reading for HOLD_S and
        # carries its age alongside, so the display can show it as held rather
        # than either blanking it or passing it off as current.
        record.update(self._metrics)
        record["metric_ages_s"] = dict(self._ages)
        record["rr_state"] = self._rr_state
        record["hrv_n"] = self._hrv_n
        return record

    @property
    def status(self) -> str | None:
        return self._status

    @property
    def metrics(self) -> dict:
        """The values latched from the last per-window row.

        Exposed because display.py has no card for HRV mean NN, so ppg_plot
        adds one and needs somewhere to read the number from.
        """
        return dict(self._metrics)
