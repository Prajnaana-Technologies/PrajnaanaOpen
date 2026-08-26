# Change log

Figures cited here are defined and reproduced in [`docs/RESULTS.md`](docs/RESULTS.md).

## 1.1.0

Everything below is measured against **1.0.0**.

| # | change | benefit | fixes a defect? |
|:--|:---|:---|:---|
| 1 | Block boundary recorded on the W1 axis | beat F1 98.7 → **99.0**, worst recording 92.0 → **94.1** | **Yes.** The search for the peak inside a block returned a window edge on 696 of 700 beats. It was never finding a peak. |
| 2 | Pulse onset found from its own rising edge | onset no longer moves when the peak does | **Yes.** Correcting the peak alone relocated 343 of 712 onsets on a waveform where none had moved. |
| 3 | Karlen line length bounded to two beats | worst reach 1152 → **336** samples | **Yes.** A flat stretch merged one line without limit, past what the sample ring holds, on six of 66 MIMIC recordings. |
| 4 | Trace marks each onset and peak | `ppg_foot` / `ppg_peak` columns for waveform display | No — new capability. |
| 5 | Rows held until the detector can no longer reach them | every mark final when written; 93 typical / 187 worst samples for an adult | **Yes.** A row written on arrival claimed "no peak here" about samples the detector was about to mark. |
| 6 | Long unmarked stretches reported | a blank flag column is no longer mistaken for a tooling gap | No — new capability. |
| 7 | Single-witness windows keep their period for variability | neonatal reportable variability 49 % → **87 %** and 49 % → **84 %** | No — the rate is still withheld, as before. |
| 8 | Surrogate agreement threshold refitted | held out: settled MAE 0.557 → **0.476**, within-2 97.4 → **99.5 %**, limits of agreement 2.14 → **1.44** | No — a threshold set while two surrogates were near-copies of each other. |
| 9 | Seven values moved from shared headers to the subject row | none today — **byte-identical output** on all 82 recordings | No — structural. Makes per-subject answers possible where several are demonstrably needed. |
| 10 | Each moving average stored at the middle of its own window | `Chebyshev` and `Smoothed` in one trace row describe the same sample; no stage corrects another's offset | **Yes.** The smoothed stream was stored at the newest sample, so it sat half a window late and each consumer had to subtract that offset back out. |
| 11 | Detector-selection rationale corrected in the source | the comments describe what the code does | **Yes.** They said a missed beat at neonatal rates makes the reported rate read low. The interval sanitiser splits those intervals, so it does not. |
| 12 | A live monitor, in `Monitor/` | the engine's output as a patient-monitor display, driven straight off the plot stream | No — new capability. |
| 13 | The plot stream carries a per-beat row, the warm-up's progress, `HRV_n`, and the surrogates with their thresholds | heart rate on screen from the second beat instead of 16.8 s; a declined window can say which surrogate disagreed | No — new capability. The CSVs are byte-identical and `-p off` emits nothing. |

Cohort after all of the above: beat F1 **99.0** (worst **94.1**), respiratory rate
settled **MAE 0.42 /min**, within-2 **99 %**, limits of agreement **−1.9 to
+2.1**, neonatal beat counts unchanged, MIMIC rates unchanged on all 68.

Changes 10 and 11 were made after those figures were measured and **do not move
any of them**: every published figure above is identical before and after, on all
81 recordings under both detectors.

**One recording regresses and is not fixed** — see *Known limitation* below.

---

### Signal conditioning

- **Each moving average is stored at the middle of its own window.** An average
  of N taps describes the centre of the span it covers, but the sample-path
  average was stored against the newest sample instead, leaving the smoothed
  stream half a window late and every fiducial found on it late by the same
  amount. Each detector then subtracted that offset back out of the indices it
  reported. The average is now stored where it belongs, the subtractions are
  gone, and the two paths that smooth — the sample stream and the surrogate grid
  — follow one rule instead of two. `Chebyshev` and `Smoothed` in the same trace
  row now describe the same sample.

  This does not change any rate. A shift common to every fiducial cancels out of
  every interval, which is why beat detection, HR and HRV are bit-identical
  across all 81 recordings. What it corrects is each fiducial's absolute
  timestamp and the alignment of the trace columns.

  The band-pass ahead of it is a causal IIR and imposes a delay of its own, which
  is **not** corrected and is much the larger of the two. That is a known
  limitation, not an oversight: correcting it needs either a constant that is
  wrong at every other heart rate, or zero-phase filtering, which cannot be done
  on a streaming sample path.

### Beat detection

- **The systolic peak is placed on the pulse.** A block of interest opens and
  closes on the W1 average, whose window is centred half a W1 behind the newest
  sample, but the boundary was being recorded at the W2 centre — 34 samples
  earlier at an adult 125 Hz. The search for the maximum inside the block then
  ran over signal the pulse had not reached, and returned a window edge on 696
  of 700 beats rather than a peak. Boundaries are now recorded on the W1 axis.

- **The pulse onset is found from its own rising edge.** It was the lowest
  sample between the previous peak and this one, so it moved whenever the peak
  did. It is now the lowest sample in the beat ending at this pulse's crest,
  which asks nothing about the beat before.

- **A Karlen line can no longer grow without limit.** A flat or slowly drifting
  stretch merged segment after segment into one line that never closed, and
  because a line's start is an onset the detector may still report, the oldest
  sample it could name receded indefinitely. Lines now close by force after two
  of the subject's slowest beats. No recording's beat count changes.

### Trace output

- **The trace marks each detected onset and peak**, in two new columns,
  `ppg_foot` and `ppg_peak`, carrying the smoothed value at that sample and zero
  elsewhere.

- **Rows are held back until their marks are final.** Each detector reports how
  far back it can still reach and the trace waits that long: 93 samples typical
  and 187 worst for an adult, 22 and 336 for a neonate. Samples still inside
  that reach when the input ends are not written.

- **A long stretch with no fiducial is reported**, naming the sample range it
  covers.

### Respiratory rate

- **A window with one corroborating surrogate reports no rate but keeps its
  variability.** The rate is withheld as before; the period it would have
  reported now narrows that window's own breath intervals, which is all the
  variability figures need and nothing else can supply.

- **The agreement threshold is 1.75 /min**, fitted on bidmc_01..08 and reported
  on bidmc_09..12. With the fiducials corrected the amplitude and baseline
  tracks stopped being near-copies of one another — they had agreed to within
  0.27 /min on eleven of twelve recordings — and a threshold set while they were
  admits a pair that is merely close as though it concurred.

### Configuration

- **Seven values are carried by the subject row** rather than by a shared
  header: the spectral accumulation depth, the agreement tolerance, the smallest
  window that may report, the peak prominence floor, both band-pass corners and
  the smoothing span. Every category holds the value its build already used, so
  output is byte-identical; what changes is that these can now be answered per
  subject. See [`docs/DESIGN.md`](docs/DESIGN.md), "Values that belong to the
  subject, not to the build".

- A detector counts the marks it places, in `peak_count` and `foot_count` on
  the context. The trace waits for a few beats of them before it starts writing,
  and the end-of-run summary reports them against the marks that reached the
  trace -- which is how a caller that started too early is caught. See
  [`docs/FIDUCIAL_INTERFACE.md`](docs/FIDUCIAL_INTERFACE.md).

- Karlen IMS now uses the expected heart-rate band it previously discarded.

### Live monitor

- **`Monitor/` is new in this release.** A desktop application that presents
  this engine's output the way a patient monitor would: the raw and detection
  waveforms with the detector's onset and peak marks on them, and cards for
  heart rate, respiratory rate, respiratory variability, the three HRV measures,
  and what the engine did with the current window.

  It opens no files. It launches the engine and reads the plot stream added
  under `-p on`, so the display shows what was just emitted rather than a file
  re-read afterwards. Two synthesized recordings ship with it, adult and
  neonatal, so it runs without the third-party datasets.

  Installing it, running it and reading each card are in
  [`Monitor/User_Guide_PPG_SignalMonitoring.pdf`](Monitor/User_Guide_PPG_SignalMonitoring.pdf);
  how it is built and how it joins the stream's two row shapes are in
  [`Monitor/TechDoc_PPG_SignalMonitoring.pdf`](Monitor/TechDoc_PPG_SignalMonitoring.pdf).

- **A reading is held through a window the engine declines**, with its age shown
  beside it, and expires after one analysis window. Declines arrive in runs, and
  a card that blanks for a minute while every other one keeps updating tells a
  reader less than an ageing number does. This is not a delay: a window that
  reports is displayed as soon as it arrives, unmarked.

- **The window's own verdict is on screen.** The card that read a fixed
  "Active" now shows whether the window was confirmed by three surrogates, by
  two, or declined because they disagreed — and, when the row says enough to
  tell, *which* surrogate stood apart. A reader who sees no respiratory rate can
  see why in the same glance.

- **Heart rate no longer waits for the respiratory estimator.** It rode on the
  per-window row, which does not exist until that estimator first reports —
  16.8 s on one of the annotated recordings, against 1.4 s for the second beat
  and therefore the first heart rate. A beat now emits its own row and the rate
  is on screen from the first frame drawn.

- **The wait says how long it is.** The respiratory estimator reports
  progressively, doubling its window until it reaches the subject's full one,
  and now says on each row where it has got to — so the card reads
  "Warming up 16 of 66 s" rather than a bare "warming up" that is
  indistinguishable from a stall. It is **not** reported early as a guess: at
  the smallest window the spectrum has four usable bins across the adult band,
  and a number drawn from it would be a bin, not a measurement.

- **HRV says what it rests on.** `HRV_n` was in the CSV and not on the stream,
  so a display could not show whether SDNN came from twenty intervals or three
  hundred. The HRV cards now carry it.

- **The top bar names the recording**, so a window's readings can be attributed
  to a subject.

### Known limitation

**Slow breathing is not handled, and one recording is worse than in 1.0.0.**
bidmc_05 breathes at 6.0 /min and reports a rate on 9 % of its windows against
67 % previously. Two surrogates must concur before a window is published and at
this rate they do not. Investigated at length: the respiratory peak carries
about 1 % of the power in the search band, and the 1/f background correction —
which is what makes the other eleven recordings work — removes it, taking the
fundamental-to-harmonic ratio from 3.62 to 0.61. Neither a longer analysis
segment, a different amplitude definition, input gain, an unnarrowed search nor
a per-surrogate history separates a genuine slow breath from baseline drift at
this rate. The cohort holds exactly one such recording, so nothing in it can
corroborate a repair either. See [`docs/RESULTS.md`](docs/RESULTS.md), "Adult
cohort".

## 1.0.0

First release.
