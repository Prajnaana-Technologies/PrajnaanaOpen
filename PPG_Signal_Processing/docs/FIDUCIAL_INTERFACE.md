# Fiducial detector — interface contract

**Purpose.** This document specifies, in terms of physiology, units and timing,
what a PPG beat detector must deliver so that the respiratory/cardiac analysis
layer can compute **HR, HRV, RR and RRV**. It is deliberately written without
reference to any existing implementation: it states *what* is required and *why*,
never *how*.

> **Timing convention — every duration in this document is SIGNAL time.**
> Seconds and window lengths mean *seconds of recorded signal consumed*, never
> execution time. Millisecond figures in the contract itself (tolerances,
> latencies, refractory periods) are likewise positions on the signal's own time
> axis, not deadlines a detector must meet on a wall clock.

**Why it exists.** This contract lets the beat detector be swapped for an
implementation written from published literature. Two are shipped and selected at
run time — Elgendi TERMA (default on adults and children) and Karlen IMS (default
on neonates) — and the same contract is the integration point if a detector is
supplied by the caller.

**Why the beat detector alone gets a contract, when no other stage has one.**
It is the only *pluggable* stage, and that is a property of the code rather than
of the documentation: it is the only one with an interface header
(`ppg_fiducial.h`), an enum of interchangeable implementations, two shipped
implementations behind it, and a runtime switch to choose between them. Every
other stage — the sample-path filter, the surrogate extraction, the spectral
estimator, the fusion — has exactly one implementation and no dispatch, because
which one is better does not depend on the patient. Only beat detection has that
property, and it is why `-s neonate` picks a different detector rather than the
same one with different numbers.

Writing a contract for the other stages would be documenting an interface nobody
can implement against. Replacing the spectral estimator is not "implement these
two callbacks", it is a rewrite of the analysis layer — so those stages are
described in [`ppg_arch.md`](ppg_arch.md) as a module map and argued for in
[`DESIGN.md`](DESIGN.md), which is what they need and all they need.

---

## 1. Scope

**In scope.** Detection of three fiducial points per cardiac cycle, and their
delivery to the analysis layer.

**Explicitly OUT of scope.** The detector is *not* required to find or report:

- the dicrotic notch or the dicrotic peak
- area under the curve, stroke volume, or any haemodynamic pressure metric
- inflection points or any other secondary wave feature

None of these is consumed by HR, HRV, RR or RRV. They belong to a different
analysis and must not be re-introduced as a dependency.

**Out of scope for the INTERFACE, not forbidden inside a detector.** A detector
may find whatever it needs to do its job, and one of them does: the dicrotic
detector locates the notch and the dicrotic peak internally, because
distinguishing a reflected wave from a systolic peak is precisely how it avoids
reporting the first as the second. What the rule forbids is *crossing this
boundary* with them — the analysis layer must never come to depend on a fiducial
only some detectors can supply, or the detectors stop being interchangeable.

---

## 2. What the detector must emit

Exactly one record per accepted cardiac cycle:

| field | type | meaning |
|:---|:---|:---|
| `foot_index` | sample index | pulse **onset** — the minimum at the start of the systolic upstroke |
| `foot_value` | filtered amplitude | signal value at `foot_index` |
| `peak_index` | sample index | **systolic peak** — the maximum of the same cycle |
| `peak_value` | filtered amplitude | signal value at `peak_index` |
| `upslope_index` | sample index | instant of **maximum first derivative** between `foot_index` and `peak_index` |
| `valid` | flag | 0 = the analysis layer must ignore this beat entirely |

### The shape

```c
/* Implemented in ppg_analysis.c; declared in ppg_fiducial.h. */
void ppg_on_foot (void *user, uint32_t foot_index, int32_t foot_value,
                  uint32_t upslope_index, uint32_t upslope_valid);
void ppg_on_peak (void *user, uint32_t peak_index, int32_t peak_value);

/* The detector's own entry points. */
void fiducial_init (struct_fiducial *ps_fd, int32_t fs_hz,
                    enum_fiducial e_detector,
                    uint32_t hr_min_bpm, uint32_t hr_max_bpm, void *user);
void fiducial_process_sample (struct_fiducial *ps_fd, int32_t sample_value);
```

**This is now implemented** — see `ppg_fiducial.h`. Two callbacks rather than one
per-beat record, because the analysis layer is streaming and wants each fiducial
as it is found: the foot closes a cycle (BW + FM), the peak opens one (AM + HR).
`user` is the opaque analysis context handed to `fiducial_init()`.

---

## 3. Conventions the detector must honour

1. **Indices** are sample counts since the start of the stream, at the sampling
   rate declared at initialisation. They never reset and never wrap during a
   recording.
2. **Ordering.** Beats arrive in strictly increasing `foot_index` order, each
   delivered exactly once. Latency is permitted — the detector may look ahead and
   report a beat well after it occurred — but reordering is not.
3. **Within a beat**, `foot_index < upslope_index <= peak_index` must hold.
4. **Amplitudes** are on the scale of the filtered waveform the detector was given,
   and must be consistent between `foot_value` and `peak_value`. The analysis layer
   uses their variation over time, not their absolute magnitude, so any constant
   offset or gain is acceptable provided it is constant.
5. **`valid = 0`** means the beat's timing and amplitudes are both untrustworthy.
   The analysis layer will skip it and record a discontinuity — see §5.
6. **No back-correction.** A beat, once delivered, is final. The detector must not
   revise or retract it.
7. **Sampling-rate independence.** Every window, threshold and filter corner the
   detector uses must be expressed in **seconds or hertz** and converted to samples
   at `fiducial_init()` using the declared rate — never written as a sample count.
   Detection quality must not change when the same signal is presented at a
   different rate. Both shipped detectors comply and are verified to; §6c has the
   measurements, and records the analysis-layer case the same check exposed.
8. **The sample ring is written honestly.** Each sample the detector consumes must
   be recorded in `struct_data_buf` as all three of `input_sample` (as read),
   `filtered_sample` (after the band-pass, *before* smoothing) and
   `smoothed_sample` (after smoothing — the stream the detector actually decides
   on). The last two must not be set to the same value: they are published as the
   `Chebyshev` and `Smoothed` trace columns, and a user comparing them is entitled
   to see the moving average's effect by itself.

---

## 4. What the analysis layer derives

All four outputs come from the three fiducials, and nothing else.

| output | derived from | notes |
|:---|:---|:---|
| **HR** | consecutive `upslope_index` differences | see §4.1 |
| **HRV** | the same interval series | see §4.2 — *different treatment of artifacts* |
| **RR** | `peak_value` (AM), `foot_value` (BW), `upslope_index` intervals (FM) | fused across surrogates |
| **RRV** | breath intervals of the winning respiratory surrogate | already implemented |

### 4.1 Why `upslope_index` for timing

The maximum-upslope point is the most timing-stable fiducial on a PPG pulse: the
systolic peak flattens and shifts under vasomotion and damping, while the steepest
point of the upstroke is defined by a derivative extremum and moves far less.
Liu et al 2020 use precisely this point for their pulse-interval-modulation
surrogate. Using one fiducial for HR, HRV **and** the FM surrogate also means
those three quantities cannot disagree about when a beat happened.

> **Decision required.** The present code derives HR from *peak-to-peak* intervals.
> Moving it to upslope-to-upslope is recommended for the reason above, but HR is a
> currently-validated output — see [`RESULTS.md`](RESULTS.md) — so the change must
> be measured against those figures before adoption, not assumed to be neutral.

### 4.2 HRV — and why it must NOT reuse the HR interval series verbatim

Reference: **Task Force of the European Society of Cardiology and the North
American Society of Pacing and Electrophysiology**, *Heart rate variability:
standards of measurement, physiological interpretation, and clinical use*,
Circulation **93**:1043–1065, 1996 (also Eur Heart J **17**:354–381).

Time-domain measures to compute:

| measure | definition |
|:---|:---|
| **mean NN** | mean of normal-to-normal intervals, ms |
| **SDNN** | standard deviation of NN intervals, ms |
| **RMSSD** | root mean square of *successive* differences, ms |
| **pNN50** | proportion of successive NN pairs differing by more than 50 ms |

Three constraints follow from that standard, and each is a real trap:

**(a) Artifact substitution corrupts HRV.** For a *rate*, replacing an outlying
interval with a plausible value is harmless. For a *variability* measure it is not:
substitution injects artificial regularity and biases SDNN and RMSSD downward. The
Task Force requires normal-to-normal intervals — artifacts must be **excluded**,
not repaired. The detector's `valid` flag and any downstream artifact rejection
must therefore mark intervals as *unusable*, and HRV must skip them. HR may
continue to use repaired values.

**(b) Excluded beats break adjacency.** RMSSD and pNN50 are defined over
*successive* pairs. If a beat is dropped, the intervals either side of the gap are
not successive, and differencing across it is invalid. Track adjacency explicitly
and difference only genuinely adjacent pairs. (This is easy to get wrong, and the
RRV path carries the same rule for the same reason.)

**(c) Window length is not free.** The Task Force standard short-term recording is
**5 minutes**, and SDNN is duration-dependent — values from different window
lengths are not comparable and should not be presented as if they were. The RR
path currently uses a 65.5 s window. Either compute HRV over a separate, longer
window, or report the window length alongside every HRV value and do not call it
a Task Force SDNN.

**(d) Nomenclature.** Derived from PPG this is strictly **pulse rate variability
(PRV)**, not HRV. PRV tracks HRV closely at rest but diverges with vascular tone
and during exercise. Report it as PRV, or as HRV with the derivation stated.

### 4.3 RR and RRV

Unchanged from the current implementation, which is original work:

- **AM** = `peak_value` series; **BW** = `foot_value` series; **FM** = successive
  `upslope_index` interval series.
- Each is resampled onto a uniform grid, detrended, band-passed to the respiratory
  band for the declared subject category, and its power spectrum searched for a
  peak. Estimates are fused by confidence. The resampling is not incidental — a
  spectrum cannot be taken over beat-locked samples, and
  [`ppg_arch.md`](ppg_arch.md) explains why, with a figure.
- RRV comes from breath intervals of the surrogate that carried the clearest peak,
  narrowed after the rate is final to those consistent with the reported period —
  a merged or split breath is otherwise indistinguishable from real variability.
  Rows whose surviving intervals cannot support the metrics report −1.

The RR layer needs **no** other information about the beat.

---

## 5. Discontinuities

Whenever the detector reports `valid = 0`, or the caller detects a gap in the
stream, the analysis layer must be told, so that:

- HRV excludes the affected interval and marks the adjacency break (§4.2b);
- the respiratory surrogates do not interpolate a straight line across a gap long
  enough to fabricate a respiratory cycle.

A beat gap longer than one expected respiratory period is significant and should
invalidate the affected analysis window rather than be smoothed over.

---

## 6. Sources for a fresh implementation

The detector can be implemented entirely from published method, with no reference
to any existing implementation.

> **Status — both candidates below have been implemented and measured.**
> `ppg_fiducial_elgendi_terma.c` (TERMA) is the default on adults and children;
> `ppg_fiducial_karlen_ims.c` (IMS) is the default on neonates. The scoring
> method is §6a, what the results mean for this contract is §6b, and the measured
> figures are in [`RESULTS.md`](RESULTS.md). Both meet the functional acceptance
> criteria of §7; §7.1 records the one criterion only partly met. This section is
> retained as the citation trail — the record of *what was read* to write those
> two files.

**Prefer a PPG-specific, noise-robust detector.** These signals are not clean:
by Liang's skewness SQI, 4 of the 14 available recordings classify **G3 "unfit"**
(both neonatal records, and `bidmc_02`/`bidmc_05`), and not one reaches G1
"excellent". A detector validated only on clean fingertip data will over-detect
here. Two published candidates designed for this regime:

- **Incremental-merge segmentation (IMS)**, Karlen et al 2012 — the PPG beat
  detector Charlton et al 2016 used for their entire 314-algorithm comparison,
  developed for wearable/ambulatory PPG.
- **Event-related moving averages**, Elgendi et al — explicitly targeted at noisy
  PPG. (Elgendi is a co-author of the optimal-filter paper already in the corpus.)

The two below specify the fiducials in full and are already in the corpus, but
their detection rules are simpler and assume a cleaner signal — usable as the
definition of *what* to find, less so as the noise-handling strategy:

- **Liu H, Chen F, Hartmann V, Khalid SG, Hughes S, Zheng D**, *Comparison of
  different modulations of photoplethysmography in extracting respiratory rate*,
  Physiol. Meas. **41**:094001 (2020). §2.3.2 specifies peak and valley detection
  from the first-difference sign change, exclusion of a candidate when a
  higher/lower sample lies within ±0.1 s, and the rule that exactly one valley is
  kept between consecutive peaks. It also defines the maximum-slope point used for
  pulse interval modulation.
- **"A Robust PPG Onset and Systolic Peak Detection"** (Hilbert-transform method,
  in the reference corpus). Gives a complete alternative: 6th-order Butterworth
  low-pass at 15 Hz, first and second derivatives, Hilbert transform, and
  zero-crossing logic locating onset and systolic peak.

The maximum-upslope point is then the argmax of the first derivative between the
detected onset and the following peak.

Preprocessing is **already provided** by the analysis side
(`chebyshev_t2_o4.c`, original work): a 4th-order-prototype Chebyshev Type II
band-pass. A detector that expects its own preprocessing should be given the
filtered stream rather than adding a second filter.

---

## 6a. How a detector is judged — the measurement method

Scored against **beat-level ground truth** rather than aggregate rate. The BIDMC
dataset carries simultaneous **ECG Lead II**, so R-peaks give a per-beat
reference. That reference is itself validated before use, against the bedside
monitor's own HR channel — the calibration and its check are described in
[`RESULTS.md`](RESULTS.md).

Matching uses a ±150 ms window, with the PPG-to-ECG offset chosen per recording
to maximise matches — estimating pulse transit time from a nearest-neighbour
distribution does not work, because it is contaminated by adjacent beats one
period away and silently aligns to the wrong beat.

**Metric definitions** are in [`DESIGN.md`](DESIGN.md), under "Reading the
numbers". In short: **Se** is the fraction of true beats found, **PPV** the
fraction of reported beats that are real, and **F1** their harmonic mean.

**Caveats.** The R-peak reference is algorithmic (Pan-Tompkins style), validated
at aggregate HR against the monitor — the dataset has no manual beat
annotations. Per-recording alignment offsets range from −104 to +552 ms, which is
wider than physiological pulse transit time and suggests a timing offset in at
least one record; the comparison is still fair because the detectors are aligned
identically.

---

## 6b. What the measurements mean for this contract

Both arms run from the **same binary**; the IMS arm is `-d ims`, which overrides
the patient type's default.

**The measurements are in [`RESULTS.md`](RESULTS.md)** — per-recording
Se / PPV / F1 for each detector, the RR figures each produces end to end, and
the neonatal pair. They are not repeated here, so that tuning the algorithm
updates one file rather than four.

What the contract needs you to take from them:

- **On adults TERMA leads**, and the gap is in precision rather than recall: IMS
  has marginally higher median sensitivity but over-calls, and those false beats
  propagate into the AM, BW and FM surrogates. TERMA's block-width test
  (`THR2 = W1`) rejects narrow noise blocks that IMS's amplitude-only gate admits.
- **On neonates the ranking reverses**, which is why the choice is made at run
  time rather than compiled in: IMS carries no window whose length must be a
  fraction of a cardiac cycle. `-s neonate` therefore dispatches to IMS.
- **A different detector shifts RR even where detection is equally good.** A
  detector defines the **foot** differently, and the foot amplitude *is* the BW
  surrogate (§4.3). TERMA reports peaks, so the onset is recovered as the minimum
  between consecutive peaks. **§3's definition of `foot_index` is load-bearing
  for RR, not merely descriptive.**
- **Detection is no longer the RR bottleneck.** Under the shipped detector,
  per-recording beat sensitivity and RR error are essentially uncorrelated, so
  improving detection further will not improve RR. The measured correlation is in
  [`DESIGN.md`](DESIGN.md), "What limits respiratory accuracy".

**Tuning burden differs sharply, and this matters for provenance.** TERMA
required **none** — Elgendi publishes every parameter (0.5–8 Hz, W1 = 111 ms,
W2 = 667 ms, β = 0.02, THR2 = W1), all brute-force optimised in the paper, so
the file is traceable line-for-line to an open-access source. IMS needed five
constants the paper names but never states; they were swept against the ECG
reference, and each sweep is recorded beside the constant it sets in
`ppg_fiducial_karlen_ims.c`. Those five values are therefore ours, not Karlen's,
and are labelled as assumptions there.

## 6c. Sampling-rate independence — what the check found

The contract requires §3.7. It was verified by resampling four recordings by
linear interpolation and re-running the whole pipeline with `-r` set accordingly.
367 Hz is included because such data exists in the field and stands in no integer
relation to 125 Hz. **The measured invariance is in [`RESULTS.md`](RESULTS.md)**;
what belongs here is what the check taught about the contract.

**The detector side passed.** TERMA's parameters are all in ms or Hz and are
converted at init, so beat detection is rate-invariant to within the resampling
interpolation itself — a single beat in several hundred, and a single bpm. IMS is
equally clean by construction, since `ims_m` derives from `IMS_TH_T_SEC`.

**The analysis side did not, and that is what the check exposed.** Decimation was
a constant 8 samples, so the surrogate grid would run at `fs / 8` and a
1024-point window would cover `8192 / fs` seconds — an analysis *duration* that
moves with the input rate. At the highest rate tested the window had shrunk to a third of its
design length and the Welch bin had widened correspondingly, so RR error grew
substantially on identical signals. Nothing physiological had changed; the
estimator had been handed a different spectrum.

**The decimation is therefore derived from the rate**, so the *grid rate* is the
constant rather than the sample step (`RR_INTP_GRID_HZ` = 15.625 Hz, which is
125/8 exactly, leaving the 125 Hz design point untouched). RR accuracy is a
property of the algorithm rather than of the acquisition hardware.

**One residual, and it is a lesson for a detector author.** IMS keeps a small
rate-dependent spread that TERMA does not, and the cause is in the detector:
`ims_m = ThT_samples / 2` is an integer division, so its segment length quantises
coarsely at low rates. It satisfies the *letter* of §3.7 — the constant is a
duration — but not its intent. **Read §3.7 as requiring the resolved parameter to
be stable, not merely the constant it derives from.**

That resolution is entirely on the analysis side; this interface is unaffected. The
resolved grid is printed at startup so a wrong `-r` is visible rather than silent.


## 7. Acceptance criteria

A replacement detector is acceptable when all of the following hold.

The **thresholds are the current shipped figures**, whatever they are at the time
you check — read them from [`RESULTS.md`](RESULTS.md) rather than from a number
copied into this list, which is how such a list goes stale. Scoring method is
§6a.

**Functional**

1. **HR agrees with the current implementation** to within a stated tolerance,
   on both neonatal recordings and on the adult cohort.
2. **RR does not regress** against the manual breath annotations across the 12
   annotated adult recordings, on all three of settled MAE, within-2, and window
   coverage.
3. **Beat detection does not regress** on median and worst-case F1, scored
   against ground truth rather than against the incumbent detector — see the note
   on scale dependence below for why the comparison must be to truth.
4. **Contract conformance**: ordering, index relations (§3.3) and
   one-record-per-beat are asserted in a test, not assumed.
5. **Sampling-rate independence** (§3.7) and **amplitude independence**: every
   window and corner expressed in seconds or hertz, and every threshold relative
   to the signal's own statistics rather than to an absolute ADC count.

**Provenance**

6. No identifier, comment, constant or structural element carried over from any
   other implementation the author has had sight of. Verify mechanically, not by
   eye: function and macro name overlap ≈ 0, and per-function line-level
   similarity below a stated threshold.
7. Every algorithmic choice cites the paper it came from.
8. Ideally implemented by someone who has not read any pre-existing
   implementation. Where that is not possible, the citation trail in (7) carries
   the argument and (6) must be demonstrated rather than asserted.

### 7.1 Status of the two shipped detectors

| # | criterion | TERMA | Karlen IMS |
|:---:|:---|:---|:---|
| 1–3 | functional | **met** — it is the arm the shipped adult figures were measured on | **met on neonates**, which is what it is dispatched for; on adults it is the weaker arm (§6b) |
| 4 | conformance asserted | **partly.** Ordering and index relations are enforced at run time by the guard in `interpolate_vertex()`, which rejects implausible or backwards spans. Not yet a standalone unit test — **open item** | same guard, same open item |
| 5 | rate and amplitude independence | **met.** Its threshold is `alpha = beta * z` against a running mean of the signal's own power, so it is unaffected by input scaling | **met** |
| 6–7 | provenance | **met.** Elgendi 2013 is open-access CC-BY and publishes all five parameters, so the file is traceable line-for-line to the paper | **partly.** Five constants the paper names but never states were swept against the ECG reference; they are ours, not Karlen's, and are labelled as assumptions in the source |
| 8 | clean-room authorship | not applicable — written from the published paper | not applicable |

**Why criterion 3 is scored against ground truth, not against the incumbent.**
A detector whose thresholds are absolute ADC counts produces a different answer
for the same signal at a different input scale, so "within tolerance of the
current detector" is not even well defined without stating `-nu`. Both shipped
detectors are scale-invariant and do not have this problem, which is why
criterion 5 exists — but a candidate that fails it would make criterion 3
unmeasurable. Compare to truth.
