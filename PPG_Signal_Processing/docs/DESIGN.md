# Design notes

Why the algorithm is built the way it is: the decision behind each choice and
the measurement that justifies it.

**Measured results are not here.** Every figure describing how the shipped build
performs lives in [RESULTS.md](RESULTS.md), which is the one file to update when
the algorithm is tuned. 

**Those measurements are dated experiments, and must be read as such.** An A/B
table here records what was seen *when that comparison was run*, on the build of
the day. Only the comparison *within* a table is meaningful — the option chosen
against the alternative measured. The absolute levels are not the current build's
performance and will not track it: later work moved them, and nobody re-runs a
settled experiment to keep its baseline column fresh. **For what the shipped
build does today, and only there, see [RESULTS.md](RESULTS.md).**

The project overview is in [../README.md](../README.md); the operating manual is
[USER_GUIDE.md](USER_GUIDE.md); the module map is in [ppg_arch.md](ppg_arch.md);
the detector contract, for anyone replacing the beat detector, is in
[FIDUCIAL_INTERFACE.md](FIDUCIAL_INTERFACE.md).

> **Timing convention — every duration below is SIGNAL time.** Seconds, minutes
> and window lengths always mean *seconds of recorded signal consumed*: a 65.5 s
> window holds 65.5 seconds' worth of samples, and a report "at 30 s" arrives
> once that much signal has been read. None of it is execution time. The program
> processes signal orders of magnitude faster than real time, so the two never
> coincide. Where execution cost is genuinely the subject, it is named as such.

## Reading the numbers

Every accuracy term used in this document, in `README.md` and in
[`FIDUCIAL_INTERFACE.md`](FIDUCIAL_INTERFACE.md) — `MAE`, `bias`, `within-2`,
`coverage`, `Se`, `PPV`, `F1`, `sub-harmonic` — is defined once under
**"Terms and abbreviations" → "Accuracy metrics"** below. That is the
authoritative list; nothing else redefines them.

Three conventions apply to every figure quoted anywhere in this project:

- **Settled, not provisional.** A window is *settled* once the analysis window
  is full; before that the estimate is *provisional* and carries a `_PROV`
  suffix on `Method`. The two are scored separately, because provisional rows
  are measurably worse — roughly three times the error — and mixing them
  understates settled accuracy. **Unless stated otherwise, quoted RR figures are settled
  rows.**
- **Median, not mean, across recordings** — with the worst case quoted beside
  it, because one bad recording matters more than an average conceals.
- **Signal time, not run time** — as stated above.

## Datasets and attribution

No dataset is distributed with this repository. Every accuracy figure below was
measured on two Open Access PhysioNet datasets, cited here because both licences
require a notice on works produced from the data — an obligation that follows
from **use**, not from redistribution:

| dataset | licence | used for |
|:---|:---|:---|
| BIDMC PPG and Respiration Dataset | ODC-BY v1.0 | the 12 annotated adult recordings — RR ground truth from the manual breath annotations supplied with the dataset, beat ground truth derived from the ECG lead II channel supplied with the dataset |
| MIMIC-III Waveform Database | ODbL v1.0 | the 68 short recordings and the 2 full-length neonatal recordings |

Full citations are in `../NOTICE`. The ODbL share-alike term applies to a
derivative *database*; this software is not one, so the Apache-2.0 licence of the
source is unaffected.

## Terms and abbreviations

### Signals and physiology

| term | meaning |
|:---|:---|
| **PPG** | photoplethysmogram — the optical pulse waveform this pipeline analyses |
| **ECG** | electrocardiogram. Supplied with the BIDMC recordings as a lead II channel and used only as *reference* truth for beat timing; never an input to the algorithm |
| **HR** | heart rate, beats/min |
| **RR** | respiratory rate, breaths/min. **Note the collision**: in HRV literature "RR interval" means the beat-to-beat (R-peak to R-peak) interval. In this file **RR always means respiratory rate**; beat intervals are called IBI or NN |
| **IBI** | inter-beat interval, ms — time between consecutive beats |
| **BBI** | breath-to-breath interval, ms |
| **NN** | normal-to-normal interval — an IBI that survived artifact rejection and was **not** substituted. HRV is defined over NN, never over repaired intervals |
| **HRV** | heart-rate variability — variability of the NN series |
| **PRV** | pulse-rate variability — the same measures computed from a *pulse* waveform rather than an ECG. What this pipeline actually produces; it is not interchangeable with HRV, because pulse arrival adds vascular transit jitter |
| **RRV** | respiratory-rate variability — variability of the breath-to-breath intervals |
| **RSA** | respiratory sinus arrhythmia — the modulation of heart rate by breathing. The physiological basis of the FM surrogate |
| **BW** | baseline wander — the slow drift of the PPG baseline. Here it is **signal, not noise**: it carries respiration |
| **AM / BW / FM** | the three respiratory surrogates: **A**mplitude modulation (peak height), **B**aseline **W**ander (foot height), **F**requency modulation (beat intervals) |
| **PIM** | pulse interval modulation — Liu's name for the FM surrogate, built from intervals between maximal-slope points |
| **fiducial** | a named landmark on one pulse: the foot (onset), the systolic peak, and the maximal-upslope point |
| **apnoea / bradypnoea** | absence of breathing / abnormally slow breathing |

### Accuracy metrics

| term | meaning |
|:---|:---|
| **beat match** | a detected beat counts as correct when it falls within **±150 ms** of a reference R-peak from the ECG lead II channel supplied with the dataset, after per-recording lag alignment. `Se`, `PPV` and `F1` below are all computed on that criterion |
| **MAE** | **mean absolute error** — average of \|estimate − reference\|, sign discarded. The headline accuracy number. Units are breaths/min for RR, ms for HRV |
| **bias** | **mean signed error** — average of (estimate − reference). Negative means the estimator reads low. MAE and bias together separate "wrong on average" from "scattered" |
| **MAPE** | mean absolute percentage error — MAE expressed as a fraction of the reference. Misleading at low rates, where a small absolute error is a large percentage |
| **within-2** | percentage of windows whose estimate is within 2 breaths/min of the reference. A clinical-tolerance measure rather than a statistical one |
| **Se %** | **sensitivity** (recall) — of the beats that truly occurred, the fraction the detector found. Low sensitivity = missed beats |
| **PPV %** | **positive predictive value** (precision) — of the beats the detector reported, the fraction that were real. Low PPV = false detections |
| **F1** | harmonic mean of Se and PPV: `2·Se·PPV / (Se + PPV)`. One balanced score, so a detector cannot look good by being trigger-happy or by being deaf |
| **sub-harmonic %** | percentage of reported windows reading **≤ 0.7 × the reference** — i.e. locked close to *half* the true rate. The dominant discrete failure mode, counted separately because it is a wrong answer rather than an imprecise one |
| **IQR** | interquartile range — spread between the 25th and 75th percentile. Used instead of standard deviation because it ignores outliers |
| **CV** | coefficient of variation — standard deviation divided by mean. Dimensionless measure of irregularity |
| **coverage** | windows that produced a value, over windows the pipeline could have produced one for. Accuracy figures must always be read next to coverage: declining to answer improves MAE without improving the estimate |

### HRV measures (Task Force of the ESC/NASPE, 1996)

| term | meaning |
|:---|:---|
| **meanNN** | mean normal-to-normal interval, ms — the reciprocal of mean HR |
| **SDNN** | standard deviation of NN intervals, ms. Duration-dependent, so values from different window lengths are not comparable |
| **RMSSD** | root mean square of **successive** differences, ms. Only genuinely adjacent pairs may contribute |
| **pNN50** | percentage of successive NN pairs differing by more than 50 ms |

### Spectral estimation

| term | meaning |
|:---|:---|
| **PSD** | power spectral density — how signal power is distributed across frequency |
| **DFT / FFT** | discrete / fast Fourier transform. This code evaluates a direct DFT over the few in-band bins, so segment lengths need not be powers of two |
| **Welch** | Welch's method — split the record into overlapping segments, take a periodogram of each, average them. Trades frequency resolution for reduced variance |
| **segment** | the block a single periodogram is computed over. **It sets frequency resolution**: `bin = 1 / T_segment` |
| **window** | the analysis span the segments are drawn from. Sets how many segments are averaged, and therefore variance — **not** resolution |
| **bin** | one frequency step of the DFT, `fs / N` |
| **slide** | how far the analysis window advances between reports. Sets reporting cadence only; it never changes an estimate |
| **Nyquist** | half the sampling rate — the highest representable frequency |
| **1/f background** | the smooth, monotonically decaying spectral floor from drift. A respiratory peak is a *local excess* over it, which is why it is whitened out before the peak search |
| **whitening** | dividing the PSD by a fitted background so peaks are judged against the local floor rather than against the largest absolute value |
| **Theil-Sen** | robust line fit — the median of all pairwise slopes. Used for the background fit so one contaminated bin cannot lead it |
| **prominence (`q`)** | peak power divided by the median in-band power — how far a peak stands above its surroundings |
| **parabolic refinement** | fitting a quadratic through the peak bin and its neighbours to locate the peak between bins |
| **harmonic / sub-harmonic** | integer multiples (2f, 3f…) and fractions (f/2) of the true rate. A non-sinusoidal breath puts real energy at its harmonics, so a spectral peak can land on the wrong one |
| **group delay** | the time lag a causal filter imposes. Constant delay cancels out of *intervals*, so it never affects a rate — but it does shift absolute fiducial timestamps |
| **zero-phase** | forward-then-reverse filtering, which cancels phase distortion. Needs the whole record, so it is only usable on the buffered respiratory path |

### Code-specific names

| term | meaning |
|:---|:---|
| **TERMA** | Two Event-Related Moving Averages — Elgendi's beat detector, the default |
| **IMS** | Incremental-Merge Segmentation — Karlen's beat detector, selectable |
| **TD_RR** | the time-domain RR estimate: 60 / mean breath duration, from counted breaths rather than a spectrum |
| **`Method` column** | which path produced the reported value; the vocabulary is listed in [USER_GUIDE.md](USER_GUIDE.md) |
| **surrogate** | one of the three respiratory signals derived from the beats — AM, BW or FM |
| **`-nu`** | input scaling — see [USER_GUIDE.md](USER_GUIDE.md) |
| **SQI** | signal quality index — a scalar rating of how usable a segment is |
| **ADC / ADU** | analog-to-digital converter / its output units (counts) |

**One binary. No build options at all.** Patient type and beat detector are both
runtime selections:

    make                # the whole thing
    make strict         # same flags plus -Werror -Wshadow
    make clean

    ./ppg_analysis -i rec.txt -s neonate           # patient type
    ./ppg_analysis -i rec.txt -s adult -d ims      # detector override, for comparison

Both detectors are compiled in and linked; `-s` picks the one that suits the
patient type and `-d` overrides it. See "The beat detector is a runtime choice"
below for why, and `ppg_fiducial.c` for the dispatch.

Both detector translation units must be in that list —
`ppg_fiducial_elgendi_terma.c` and `ppg_fiducial_karlen_ims.c` supply the entry
points `ppg_fiducial.c` dispatches to, so omitting either is a link error.

Run:

    ./ppg_analysis -i <neonatal_recording>.txt -c 0            # 12-bit ADC counts
    ./ppg_analysis -i <bidmc_recording>.txt -c 0 -nu 10000     # float input

`-nu` scales the input and defaults to **1**. Integer ADC
recordings need `-nu 1`; floating-point recordings with 5 decimal digits — the
BIDMC/MIMIC files — need **`-nu 10000`**, without which every sample truncates
to zero and no beats are detected.

The respiratory band and every other filter parameter live in
**`filter_bands.h`**, each cited to a paper or clinical standard, and are
selected as a complete set by `-s`. **There is no free-form band override** —
the former `-rrlo`/`-rrhi` options were removed — see "Why there is no band
override" below. An arbitrary band would
bypass the citation rule that file exists to enforce, and could silently
contradict the compiled-in subject category.

Source files carry the Prajnaana Technologies copyright and
`SPDX-License-Identifier: Apache-2.0`; the full licence text is in `../LICENSE`.

---

## Literature conformance and deliberate deviations

The reference corpus does **not** speak with one
voice — of the six papers, two recommend something other than Chebyshev II
(Hilbert **A**: 6th-order Butterworth LP at 15 Hz; Hilbert **B**: Chebyshev
Type **I** bandpass 0.5–16 Hz). This pipeline is therefore a **hybrid**: it
takes the best-supported guidance for each stage rather than following any one
paper end to end. What follows is what we conform to, what we deviate from, and
why.

### Primary source

Liang, Elgendi, Chen & Ward, *An optimal filter for short photoplethysmogram
signals*, Scientific Data **5**:180076 (2018). Nine filter types × ten orders
over 219 short (2.1 s) PPG records, scored by the **skewness signal-quality
index**. Conclusion: **Chebyshev Type II, 4th order** is optimal, because it
combines a sharp transition with a **flat, ripple-free passband** — Chebyshev I
and elliptic were rejected precisely because passband ripple distorts waveform
morphology.

Cited and reused by PPGFeat (`ref_1…`, which adds the band **0.4–8 Hz** and the
same **20 dB**) and by the CVD-indices paper (**0.5–8 Hz**).

### Sample path — `chebyshev_t2_o4.c`

| | corpus | here | status |
|:---|:---|:---|:---|
| Filter type | Chebyshev Type II | Chebyshev Type II | **conforms** |
| Order | 4th (prototype) | 4th prototype → 4 biquads | **conforms** |
| Stopband `Rs` | 20 dB | **40 dB** | deviates |
| Corner convention | `Ws` = where −`Rs` is reached | **−3 dB** at the stated corners | deviates |
| Upper corner | 0.4–8 Hz spec → −3 dB at **6.41 Hz** | −3 dB at **6.00 Hz** | ≈ conforms |
| Lower corner | 0.4–8 Hz spec → −3 dB at **0.50 Hz** | −3 dB at **0.02 Hz** | deviates |

Measured, so the convention point is concrete: `cheby2(4, 20, [0.4, 8])` at
125 Hz actually delivers a −3 dB band of **0.502 – 6.409 Hz**. Our upper corner
is therefore already in line with the literature; the real divergence is at the
bottom.

**Defence — lower corner 0.02 Hz instead of ~0.5 Hz.** This is the important
one, and it is deliberate. The papers high-pass at 0.4–0.5 Hz *in order to
remove baseline wander*, because for morphology work baseline wander is noise.
In this pipeline **baseline wander is signal**: the BW respiratory surrogate is
the per-beat **foot amplitude**, i.e. baseline wander sampled at the beat rate.
The respiratory bands this pipeline searches are 22–66/min (**0.37–1.10 Hz**)
for neonates and 4–30/min (**0.067–0.50 Hz**) for adults. A 0.5 Hz corner sits
inside both, and for adults would erase the band entirely. Adopting the papers'
lower corner would improve beat morphology at the cost of destroying one of the
three RR surrogates. The two objectives genuinely conflict and we choose the
one the product needs.

**Defence — 40 dB instead of 20 dB.** Because our corners pin −3 dB rather than
−`Rs`, `Rs` here controls only transition sharpness and the stopband floor, not
the passband. At a constant −3 dB band of 0.5–8 Hz the difference is large below
the band: **−75 dB at 0.1 Hz at 40 dB, against −25 dB at 20 dB**. Sub-band
drift leaking into the respiratory peak search is the exact failure documented
where the peak search kept landing on the lowest bins — see "Low-frequency
contamination" below — so the deeper stopband is
bought for a known reason. Note this places us outside the specific
configuration Liang validated; 20 dB remains a reasonable alternative and the
change is one constant (`CHEB2_STOPBAND_DB`).

**Defence — corner convention.** Pinning −3 dB makes the passband explicit and
independent of `Rs`, so changing the stopband depth cannot silently move the
band. Under the convention the two are coupled: at the same nominal
`[0.4, 8]`, raising `Rs` from 20 to 40 dB shifts the real −3 dB band from
0.502–6.409 Hz to 0.714–4.522 Hz. Stating both numbers is the only unambiguous
way to describe the filter.

### Respiratory path — `ppg_RR.c` (Butterworth, and why that is not an oversight)

**No paper in the corpus specifies a filter for respiratory-rate extraction.**
Respiration appears only as an application, a dataset field, or a physiological
effect. The Chebyshev II result therefore does not extend here by citation, and
its *rationale* does not transfer either:

- Liang's criterion was the **skewness SQI — a morphology metric** on the
  cardiac waveform. A beat-sampled AM/BW/FM surrogate has no systolic or
  diastolic morphology to preserve or distort.
- Chebyshev II won over Chebyshev I and elliptic specifically because passband
  ripple **distorts morphology**. This path feeds a **spectral peak search**,
  not morphology analysis; what matters is passband flatness (so no frequency is
  favoured) and rejection of sub-band drift.
- Chebyshev II's remaining advantage is a sharper transition per order. That is
  nearly free here anyway: this path filters **zero-phase (forward + reverse)**,
  which doubles the effective order at no cost, over short blocks where
  computation is not constrained.
- Butterworth is **maximally flat** in the passband — the ideal property for an
  unbiased peak search — and rolls off **monotonically without limit**, whereas
  Chebyshev II floors at −`Rs`. Against drift leaking into the peak search, the
  unbounded rolloff is a small advantage.

Butterworth is kept here on the merits. Changing it for consistency with the
sample path would be consistency for its own sake, against the evidence.

---

## RR/RRV lineage: Charlton 2016 and Liu 2020

Two further papers ground the respiratory path:

- **Charlton et al 2016**, *An assessment of algorithms to estimate respiratory
  rate from the ECG and PPG*, Physiol. Meas. **37**:610 — the systematic
  comparison, 314 algorithms. Its three-stage taxonomy (**extraction →
  estimation → fusion**) is the architecture used here.
- **Liu et al 2020**, *Comparison of different modulations of PPG in extracting
  respiratory rate*, Physiol. Meas. **41**:094001 — compares AM / BW / FM /
  direct filtering. **FM was the strongest modulation**, and in normal breathing
  the only one whose estimate was not significantly different from the reference
  (p > 0.05).

As with the sample path this is a **hybrid**, not an implementation of either
paper.

### Stage 1 — extraction

| | Liu 2020 | here | why |
|:---|:---|:---|:---|
| BW | curve through valleys | per-beat foot amplitude | same quantity |
| AM | peak curve **minus** valley curve | systolic peak amplitude | deviation, see below |
| FM | intervals between **maximal-slope** points, per-cycle detrended (PIM) | same ✓ | **implemented** |
| resampling | cubic spline | linear | follows Charlton/Karlen instead |

**Liu's FM definition was subsequently implemented** — see "Liu's FM definition,
implemented and measured" below. AM still deviates: it is the systolic peak
alone rather than peak-minus-valley, so AM and BW continue to share a baseline
component. That remains open and untested.

Linear resampling is not an oversight: Charlton resamples "at 5 Hz using linear
interpolation (Karlen et al 2013)", so both choices are paper-backed and this is
a hybrid. Our surrogate rate is 15.625 Hz against Charlton's 5 Hz.

Liu's own pre-processing is a low-pass with passband < 3 Hz. That would be
**wrong here**: our subjects are neonates whose cardiac fundamental alone is
≈ 2.5 Hz. This is a further reason for the 6 Hz upper corner
(`BP_LP_CORNER_HZ`).

Liu's acquisition high-passed at **0.05 Hz**, specifically to keep baseline
wander available for BW extraction. That is independent corroboration of the
0.02 Hz lower corner defended above against Liang's 0.5 Hz.

### Stage 2 — estimation

Welch PSD with a Hamming taper at **75 % overlap**, then a band-limited peak
search refined by parabolic interpolation. The segment length is per patient
type — 8.2 s for neonates, 16.4 s for children, 32.8 s for adults — which at that
overlap gives **13 averaged segments** for the paediatric categories and **5** for
adults. Liu used a **single
rectangular-window periodogram**; Charlton lists the Welch periodogram as
technique FT7. Welch is the deliberate improvement: averaging several
periodograms is what reduces the variance a single one carries.

**Deviation worth noting:** Charlton's *top-ranked* algorithms used **time-domain
breath detection**, not frequency-domain estimation. This pipeline is
frequency-domain throughout. Untested here.

### Stage 3 — fusion (a hybrid of two of Charlton's named techniques)

- **FM1 smart fusion** (Karlen 2013): quality-assess AM/BW/FM; if their std
  ≤ 4 bpm output the mean, else output nothing.
- **FM2 spectral peak-conditioned averaging** (Lázaro 2015): include a spectrum
  only if a sufficient proportion of its power sits around the peak.

Ours takes the power-concentration test from **FM2** (`RR_MIN_PEAK_PROMINENCE`,
peak ÷ median in-band power), the agreement gate and "otherwise no output" from
**FM1** (`RR_AGREEMENT_THRESHOLD`, and the `REJECTED` method), then averages
**quality-weighted** rather than as a plain mean. `Spread_bpm` is FM1's standard
deviation reported rather than used as a hard gate.

Smoothing deviates: Charlton's FT1 is exponential
(`RRi = 0.2·RRest + 0.8·RRi−1`); **no temporal smoothing is applied here at
all**. A 5-point median was tried in its place and removed — see "`Smoothed_RR`
was removed" below. Each window is reported on its own evidence, and one that
cannot be trusted is declined rather than averaged into its neighbours.

Window length deviates: **65.5 s** here against Charlton's **32 s**. The longer
window buys frequency resolution at the cost of update rate; at neonatal rates
32 s would still contain 16–42 breaths, so this is a trade rather than a
requirement.

### FM is the weakest surrogate on adults in this corpus

Liu's central result is that FM is physiologically the strongest modulation.
That is not what this corpus shows for the adult recordings, and the difference
matters to anyone re-tuning the fusion.

| surrogate | median ref/est | within ±15 % | at ≈½ the true rate |
|:---|---:|---:|---:|
| BW | **1.04** | **71 %** | 8 % |
| AM | 1.08 | 58 % | 24 % |
| **FM** | 1.39 | 35 % | 20 % |

**Why, and it is a rate effect.** A 65.5 s window holds roughly 100 real
FM samples at the 75–98 bpm of these recordings against ~165 at the ~150 bpm of
the neonatal pair, and the interpolated series subharmonic-locks as the sample
count falls. The ranking therefore reverses with the population, and a fusion
prior that amplifies FM would amplify the pipeline's weakest input exactly where
it is weakest. Fusion here stays purely data-driven for that reason.

The FM implemented is Liu's own definition — intervals between maximal-upslope
points, not peak-to-peak IBI — described under "Liu's FM definition, implemented
and measured" below.

### Subharmonic locking on adults, and the three changes that answered it

> **The figures in this subsection are from the build of the day and are scored
> against the monitor's own 1 Hz `RESP` channel, not against the manual breath
> annotations every shipped figure uses.** They are here to explain why three
> mechanisms exist, not to describe current accuracy. For that see
> [RESULTS.md](RESULTS.md), where settled RR is **MAE 0.42 /min**.

At the time, the cohort showed MAE 4.71 and bias −4.54 /min with the bias
negative on all 12 recordings (−0.70 to −11.15), and 28 % of fused windows
sitting at approximately half the reference rate.

This did not show up on the neonatal pair because the infant band starts at
20 /min, which structurally forbids a subharmonic of a 28 /min rate. The adult
band reaches down to 6 /min, so half of a normal 20 /min rate lands squarely
inside the search range. This is the same family of failure as the earlier
low-frequency contamination and band-edge defects, re-exposed by a population
they were never tested against.

Three remedies were identified, and **all three are in the shipped build**:

1. **Liu's FM definition** (maximal-slope/PIM) — addresses the worst surrogate
   directly, and is a prerequisite for revisiting the prior. *Implemented; see
   the next section.*
2. **Harmonic-aware peak selection** — when the power at 2× a candidate peak
   materially exceeds the candidate, prefer the higher frequency. Charlton's
   corpus notes octave errors as a known failure of spectral RR estimation.
   *Implemented, as the sequential tracking guard.*
3. **Reconsider the adult band's lower edge.** 6 /min admits subharmonics of
   every normal adult rate. It was chosen to cover bradypnoea; the cost is
   now measured. *Revisited — but the resolution ran the other way: the floor
   widened to 4 /min once whitening and the guard made the low bins usable, and
   the decisive factor was band-edge rounding: rounding outward searches
   territory the band never claimed.*

### RRV is not grounded in this corpus

Neither paper addresses respiratory-rate *variability*. Charlton is RR-only;
Liu touches HRV/RSA only as a mechanism. The RRV reported here (SD, RMSSD and CV
over breath intervals from the winning surrogate) is an extension **by analogy
with time-domain HRV metrics**, and should be presented as such rather than as a
literature-backed measurement.

The same standard already applied to HRV SDNN/RMSSD applies here, and more
strongly: those at least have a validated reference to be measured against and
were declared unusable at 125 Hz on that evidence, whereas RRV has **no
validated reference at all**. Even after the √2 interval gate
(`RRV_GATE_TOLERANCE`, `gate_breath_intervals()`) it runs ≈ 1.7 × the
spread of manually annotated breath intervals, for a reason inherent to the
method. Report SD, RMSSD and CV as **derived, indicative** figures — usable for
trend within a subject, not for comparison against a published norm.

### Neither paper covers neonates

Liu: 36 healthy adults, 19–58 y. Charlton: healthy adults. Liu searched
0.1–0.5 Hz (normal) and 0.05–0.3 Hz (deep) — adult bands. The paediatric bands
therefore come from outside this corpus: **Fleming et al, The Lancet 377:1011
(2011)**, whose respiratory-rate centiles over 13 age bands now source the
NEONATE and CHILD categories in `filter_bands.h`.

---

## Liu's FM definition, implemented and measured

**FM is the series of intervals between MAXIMAL-UPSLOPE points, one per cardiac
cycle** (pulse interval modulation), replacing peak-to-peak IBI. A foot closes
the cycle that began at the previous foot, so the running maximum of the first
derivative accumulated since then belongs to the cycle just finished; the
interval to the previous cycle's maximum is the FM sample.

Liu detrends each cycle by subtracting the straight line joining its two
bounding valleys before differentiating. That is **omitted deliberately**:
subtracting a straight line subtracts a *constant* from the derivative over that
cycle, which cannot move its argmax. Same fiducial, less arithmetic — a proof,
not a shortcut.

Heart rate still comes from **peak-to-peak** IBI: it is the clinically
meaningful beat interval, and it is the evidence the declared subject category
is checked against. `sanitize_ibi()` now runs *before* HR is derived rather than
after, so its missed-beat and false-peak corrections reach the HR history
instead of only a debug print.

Measured on the 12 BIDMC/MIMIC adult recordings against the monitor's `RESP`:

| | peak-to-peak IBI | **Liu PIM** |
|:---|---:|---:|
| MAE | 4.71 | **3.93** |
| bias | −4.54 | **−3.75** |
| within 2/min | 48 % | **59 %** |
| subharmonic | 28 % | **20 %** |
| FM ref/est median | 1.54 | **1.39** |
| FM within ±15 % | 30 % | **35 %** |

AM and BW are untouched, as expected. The neonatal pair is essentially neutral
(1.97 and 0.53 against 2.01 and 0.24) — the change matters where the beat rate
is low and a 65.5 s window holds ~100 intervals rather than ~165.

## `filter_bands.h` — every filter parameter, cited

All filter parameters moved into `filter_bands.h`, which defines all three
categories (neonate / child / adult) and lets `-s` select one at run time.
**The rule for that file is that no number in it is chosen by judgement** — each is traceable to a named paper or clinical standard, cited
beside it.

The category is **declared, not inferred**. Guessing it from the signal is
itself a source of error, and the old 120 bpm threshold was invented. Measured
HR is used only to warn when it falls outside Fleming's heart-rate centiles for
the declared category.

**It used to be evaluated once, and that was too weak.** `rr_select_band()` runs
it on the median of the first 16 beats. Because `-s` and `-r` both scale the
detector, a wrong declaration mis-tunes detection in the same stroke, and can drag
those early beats *into* the expected range:

| declared | true signal | first 16 beats | settled median HR | old behaviour |
|:---|:---|---:|---:|:---|
| `-s adult` | neonatal, HR 150 | 60–75 bpm | 150 | **silent** |
| `-r 250` | 125 Hz adult | 60–95 bpm | 178 | **silent** |

Both are exactly the mistake the check exists to catch, and in both the log said
nothing. **The check is now also re-run per analysis window**, on the settled
history, and warns at most once per recording (`hr_band_check()`). Both cases
above now warn; correct usage on either cohort stays silent, so the change adds
no false alarms. Computed outputs are untouched — the 14 reference recordings are
byte-identical across the change.

| category | band /min | source |
|:---|:---|:---|
| NEONATE (0–12 m) | 22–66 | Fleming 2011 WT4, 1st–99th centile span |
| CHILD (1–18 y) | 11–53 | Fleming 2011 WT4, 1st–99th centile span |
| ADULT (>18 y) | **4–30** | ceiling from Liu 2020 (0.5 Hz, normal breathing); floor widened from Liu's 6 — see "Why the adult band floor is 4 /min" |

Fleming et al, *The Lancet* **377**:1011 (2011) — respiratory-rate and
heart-rate centiles over 13 age bands, from 3881 children (RR) and 143 346 (HR).
Web Tables 4 and 5 were verified against the Lancet appendix, archived at
`Fleming2011_Lancet_RR_HR_centiles_Webappendix.pdf`.

The adult band was chosen by measurement among the *sourced* options:

| adult band | source | MAE | within 2/min | subharmonic |
|:---|:---|---:|---:|---:|
| 6–40 | none (previous) | 3.93 | 59 % | 20 % |
| 4–60 | Charlton 2016 | 6.09 | 43 % | 23 % |
| **6–30** | **Liu 2020** | **3.39** | **65 %** | **13 %** |
| 8–30 | **unsourced probe** | 1.79 | 81 % | 4 % |

The 8–30 probe is far better and is **excluded** — it is fitted, not cited.
`filter_bands.h` records it under `UNSOURCED` so it is one citation away from
being adoptable.

### Why the adult lower edge is not dropped for meditative breathing

Sourced slow rates: **2.5/min** sustained 15 min with Samavritti Pranayama
(PMC8977447), 3–4/min Zen Tanden, 4.32 ± 1.87/min yogic mean. Nothing at 1–2/min.

More decisively, **the segment cannot resolve those rates, and lowering the edge
makes things worse rather than achieving nothing.** The peak search runs on Welch
*segment* bins; the adult segment is 512 samples at 15.625 Hz = 32.8 s, so bin
spacing is 0.0305 Hz = 1.83/min. Edges are rounded to the nearest bin, so the
shipped 4/min floor lands on bin 2 (3.66/min), while a 2/min floor would land on
bin 1 (1.83/min) — they are *not* equivalent, and bin 1 is exactly where residual
baseline drift collects. That is the harm: dropping the edge does not open up
slow breathing, it admits the drift bin and a rate's own half-rate. Measured, a
2/min floor collapsed a recording sitting at twice the new floor, MAE 0.12 →
6.06.

Resolving 2.5/min honestly needs a longer segment, not a wider band: at 2.5/min a
32.8 s segment holds ~1.4 cycles against the 3-cycle rule, so it needs a 72 s
segment, which at the power-of-two sizes used throughout rounds to the same 2048
points (131 s) and 8192-point window (8.7 min latency) as a 2/min floor — a
change to `<CATEGORY>_WELCH_SEG` and
`<CATEGORY>_WINDOW_PTS` in `filter_bands.h`. The sample-path filter is not the
constraint — its 0.02 Hz corner is 1.2/min.

## Time-domain RR: reported, but NOT fused

`extract_breath_intervals()` already detects individual breaths (zero
up-crossings on the band-limited wave) for RRV. Charlton's **time-domain**
estimation techniques (ET1–5) turn exactly those into a rate — "the estimated RR
was calculated as the mean breath duration" — and his top-ranked algorithms used
that route rather than the spectral one. So the second estimate cost one
division, and was reported as `TD_RR` in `RR_Data.csv`.

It does **not** vote in the fusion; its only influence is the sub-harmonic
rescue documented below. Measured:

| | spectral | time-domain |
|:---|---:|---:|
| 12 MIMIC adults (MAE) | 3.39 | **2.59** |
| `neonatal_mimic_data1` (err) | **2.19** | 5.80 |
| `neonatal_mimic_data2` (err) | **1.42** | 5.99 |

Time-domain wins clearly on adults and loses badly on neonates, running ~20 %
high there — consistent with spurious zero-crossings when a window holds fewer
samples per breath. Adopting it globally would repeat the FM-prior mistake.

Adding it as a fourth vote was built and measured, then backed out: on adults it
moved MAE only 3.39 → 3.36, because the `RR_AGREEMENT_THRESHOLD` gate admitted
it in just **18 of 150** windows — precisely those where it already agreed and
therefore added nothing. In the 132 windows where it was excluded its error was
**2.74** against the fused **3.63**: the gate rejects the better estimate exactly
when it would help. That is structural, not a threshold to tune, because the
gate anchors on the spectral estimate.

Charlton's architecture does not fuse a frequency- and a time-domain estimate of
the same signal in any case — an algorithm selects one estimation technique and
then fuses across modulations. Variants of that shape were measured on the
adults: ET on all three surrogates + FM1 smart fusion **3.16** (emitting nothing
in 7 of 150 windows under Karlen's std ≤ 4 bpm rule), ET median across the three
**2.92**, ET on the spectrally-best surrogate **2.59**. None is validated on
neonates, and the neonatal result above says they very likely would not survive
there.

## Source layout — detection separated from analysis

The tree is split at the fiducial boundary, so detector algorithms can be
swapped without touching the analysis code:

| file | role |
|:---|:---|
| `ppg_fiducial.h` | **the interface** — detector context + the two callbacks |
| `ppg_fiducial.c` | detector dispatch, and the Goertzel fundamental check |
| `ppg_fiducial_elgendi_terma.c` | TERMA detector, written from Elgendi 2013 |
| `ppg_fiducial_karlen_ims.c` | IMS detector, written from Karlen 2012 |
| `ppg_analysis.c` | surrogates, HR, HRV, RR fusion, RRV |
| `ppg_RR.c` | respiratory bandpass, Welch PSD, RRV metrics |
| `chebyshev_t2_o4.c` | sample-path Chebyshev Type II bandpass |
| `filter_bands.h` | every filter parameter, each cited |

The detector calls exactly two functions:

```c
void ppg_on_foot (void *user, uint32_t foot_index, int32_t foot_value,
                  uint32_t upslope_index, uint32_t upslope_valid);
void ppg_on_peak (void *user, uint32_t peak_index, int32_t peak_value);
```

`ppg_on_foot` drives the BW surrogate and closes the cardiac cycle so FM can
take its maximal-upslope interval; `ppg_on_peak` drives AM and advances the heart
rate.

The boundary is verifiable rather than asserted: **`ppg_fiducial.c` contains zero
references to analysis state**, and each detector translation unit exports
**exactly two symbols** — `nm` on `ppg_fiducial_elgendi_terma.o` and
`ppg_fiducial_karlen_ims.o` shows only their `*_fiducial_init` and
`*_fiducial_process_sample`, so nothing else can be reached across the split.

**One reference goes the other way, deliberately.** `ppg_analysis.c` holds the
detector context as an opaque pointer and hands it back when the fusion stage
asks the detector layer to confirm a candidate rate against the buffered signal
(`fiducial_period_is_fundamental()`). It reads no field of that structure — the
type is used only to pass the handle through — but it is a dependency, and
`ppg_arch.md` draws it as the one dotted arrow returning to stage ③.

The split is behaviour-preserving: `RR_Data.csv` is **byte-identical on all 12
MIMIC recordings**, and the neonatal pair is unchanged (2.19 / 1.42).

Two consequences worth knowing:

- The sample ring belongs to the detector, so the interpolated-surrogate columns
  of the per-sample trace are kept in an analysis-side array instead.
- Initialisation is split one per side: `fiducial_init()` for the detector and
  `ppg_analysis_init()` for the analysis layer.

## Vertex deferral: commit after judgement, gate open

The detector delivers each cardiac cycle as two callbacks: `ppg_on_foot()` first
(systolic onset and the maximal-upslope point), then `ppg_on_peak()` (systolic
peak). Before this change the analysis layer committed each vertex the moment its
callback arrived — BW and FM at the foot, AM at the peak. That is correct while
every beat is accepted, but it makes any future accept/reject gate ineffective:
by the time `sanitize_ibi()` judges a beat, its vertices are already in the
surrogate series.

Variant B defers the commit without changing what is committed:

- `ppg_on_foot()` now **stages** the foot value and the maximal-upslope point in
  the analysis context; it no longer writes BW or FM directly.
- `ppg_commit_vertices()` runs at the peak, **after** `sanitize_ibi()` has judged
  the beat and **before** the beat's HR/HRV are published. It commits the staged
  BW/FM vertices and the AM vertex in the previous order.

The gate is **deliberately open**: every judged beat is accepted, so the deferral
reproduces the pre-change commit sequence exactly. On all 14 recordings the five
CSV outputs are **byte-identical** to the unmodified binary, and stdout content is
identical after sorting (only interleaving differs).

### Four invariants any future change must keep

1. **First beat has no previous peak** and therefore no interval to judge; it is
   accepted unconditionally, and its AM/BW vertices are still committed.
2. **FM is gated by beat N-1's decision, BW foot and AM peak by beat N's
   decision.** The FM vertex staged at foot N describes cycle N-1 (the interval
   between upslopes N-2 and N-1), so it must be withheld when beat N-1 was
   rejected, not when beat N was.
3. **Commit vertices after `sanitize_ibi()` judgement and before HR/HRV
   publication.** Reversing this order shifts every HR/HRV number by one beat
   while leaving every RR column unchanged — a silent, RR-invisible regression.
4. **Gate stays open.** Every beat commits today. Closing the gate is a separate,
   evidence-gated change, and it is what will make the risks deferred here live
   again.

## RR re-validated against manual breath annotations

The scoring above used the monitor's own 1 Hz `RESP` numeric, which is itself
algorithm-derived. The BIDMC dataset also ships **manual breath annotations from
two independent annotators**.

**Reference quality.** The two annotators marked an identical *number* of breaths
in all 12 recordings, differing only by a few samples in timing — an unusually
clean reference. Against them the monitor is accurate to ±0.4 /min on 10 of 12,
but is **+5.0 /min wrong on `bidmc_05`**, reporting 11 where the annotators count
6. That is close to a doubling: the monitor's impedance algorithm has its own
harmonic error on that record.

**Result.** Switching reference changes per-recording MAE by at most 0.2 /min and
changes no conclusion. The manual annotations are used for every RR figure quoted
in this document, because they are a direct human count rather than another
algorithm's output.

### What limits respiratory accuracy

Two things constrain the respiratory estimate, and it is worth being explicit
about which is which, because they call for different work.

**Beat detection sets a floor.** Every missed beat removes a sample from all
three surrogates at once and doubles an FM interval, so the surrogate series
becomes both sparser and distorted exactly where detection is weakest. A
respiratory estimator cannot recover what the beat stream never carried.

**That floor is no longer binding.** Under the shipped detector the correlation
between per-recording beat sensitivity and RR MAE is **−0.008** — effectively
none — across a sensitivity range of 93.1–99.7 %. Beat detection is uniformly
good enough that improving it further will not improve RR. What remains is on the
analysis side, and the sections below are about that: sub-harmonic locking, the
1/f background, band-edge rounding, and the quality gate.

**One detector-side effect does survive, and it is a design constraint rather
than a flaw.** A detector defines the **foot** differently, and the foot
amplitude *is* the BW surrogate. TERMA reports systolic peaks, so the onset is
recovered as the minimum between consecutive peaks. Any change of detector will
therefore move the BW surrogate — and with it the fused RR — even where beat
timing is equally accurate. This is why `FIDUCIAL_INTERFACE.md` §3 specifies
`foot_index` normatively: for RR it is load-bearing, not descriptive.

For the measured comparison of the two shipped detectors, see
[`RESULTS.md`](RESULTS.md).

## Why there is no band override

`-rrlo` and `-rrhi` once forced the respiratory band's edges at run time. They
were removed rather than repaired, and deliberately not replaced:

- A runtime override injects **uncited numbers** straight past the sourcing rule
  that `filter_bands.h` exists to enforce.
- It can **silently contradict** the selected patient type, with no consistency
  check between the two — precisely the user error the declared category was
  introduced to prevent.

To analyse a subject of a different age, turn the knob: `-s neonate`,
`-s child`, `-s adult`. The band then comes with its citation attached, and its
window and segment come with it.

The options themselves are documented in [USER_GUIDE.md](USER_GUIDE.md).


## Output columns: two decisions worth recording

The column meanings are in [USER_GUIDE.md](USER_GUIDE.md). What belongs here
is why two of them are the way they are.

`RRV_intervals` counts accepted **intervals**, not breaths — *n* breaths give
*n* − 1 intervals even when every one is detected. It was previously named
`RRV_breaths`, which invited consumers to read it as a breath count.

### `Smoothed_RR` was removed

A 5-deep running median over `AVG_RR` was reported as a `Smoothed_RR` column
during development. It is not in the shipped code, and neither is the history
buffer and median helper that supported it — so do not go looking for them.

Two reasons. It **fed nothing back into the algorithm** — it reached only the
console line and that one column, so it influenced no decision. And measured
against the manual breath annotations it was **worse than the value it
smoothed**:

| column | bias | MAE | within 2 |
|:---|---:|---:|---:|
| `AVG_RR` | −0.98 | **1.74** | **82 %** |
| `Smoothed_RR` (removed) | −0.95 | 1.76 | 79 % |

(Both columns are a snapshot taken at the time of removal, over a smaller window
set — the comparison between the two is the point, not their absolute level. The
band-edge, whitening and harmonic-guard work all came afterwards, and the
resulting `AVG_RR` is several times more accurate; see [RESULTS.md](RESULTS.md).)

Its stated purpose was that one bad window could not swing the reported value,
but the bad windows it was meant to absorb are the sub-harmonic ones, and those
are caught at source by the band-edge rounding and the rescue. It was lagging the
signal without removing errors that were still there.

Removing it does not change `AVG_RR`: verified byte-for-byte identical scoring
before and after (n=135, bias −0.98, MAE 1.74, within-2 82 %).

If a deliberately-slow trend value is wanted for display, it should be added as
such and labelled a trend — not as a second estimate of RR competing with the
one the algorithm actually produces.

---

## Sub-harmonic locking, and why it is not a bias

The fused RR carried a −1.76 /min bias that survived every detector change.
Split the 151 scored windows on whether the estimate is below 0.6 × reference:

| subset | n | bias | MAE |
|:---|---:|---:|---:|
| all windows | 151 | −1.76 | 2.36 |
| **sub-harmonic** | **22 (15 %)** | **−10.74** | 10.74 |
| everything else | 129 (85 %) | **−0.22** | **0.93** |

15 % × −10.74 = **−1.57 of the −1.76**. The estimator is essentially unbiased and
accurate on 85 % of windows and reports close to half the true rate on the rest.
The residual was never an offset to correct — it was a discrete failure mode
averaged into one.

Worth recording as a trap: the obvious diagnostic points the wrong way. The
est-on-ref slope is 0.62 and corr(error, reference) is −0.38, which reads as a
**scale compression** and would send you looking for a gain error. It is an
artefact of the bimodal mixture — on the correct windows the slope is 0.85.

**It is not physiology.** The reference breath intervals in the failing windows
are as regular as everywhere else (interval CV **0.082 vs 0.086**) and show
*less* long/short alternation (32 % vs 46 %), so there is no genuine energy at
half the breathing rate for the estimator to have locked onto.

Two independent causes produced it, and the design answers both.

### Cause 1 — rounding the band edges outward

`compute_rr()` mapped the configured band to bins with `floor` on the lower edge
and `ceil` on the upper one. Both round *outward*, so the search ran outside the
band the subject category asked for. At 125 Hz the bin is 3.662 /min:

| band | outward (old) | inward | **nearest (now)** |
|:---|---:|---:|---:|
| adult 6–30 | **3.66 – 32.96** | 7.32 – 29.30 | **7.32 – 29.30** |
| neonate 22–66 | 21.97 – 69.58 | 25.63 – 65.92 | **21.97 – 65.92** |
| child 11–53 | 10.99 – 54.93 | 14.65 – 51.27 | **10.99 – 51.27** |

Bin 1 alone — **3.66 /min** — was returned as the RR of windows whose true rate
was 20.7 /min. Once such a bin wins it cannot be outvoted, because it looks like
a perfectly clean peak.

**Nearest, not strictly inward.** Inward guarantees containment but discards up
to a full bin, and how much depends on where the edge happens to fall. It was
nearly free for adults and expensive for the other two categories, whose lower
edges already sit within 0.03 /min of a bin. Measured on the neonatal pair,
inward rounding **clipped**: every window pinned at or above the floor bin
(minimum reported RR 25.64 = the floor exactly). Nearest restores it (minimum
21.97) and is identical to inward for adults, so nothing is given up.

### Defect 2 — AM and BW are not independent, so the majority vote over-trusts them

Both are amplitude surrogates of the same waveform, so baseline wander corrupts
them **together**. When they lock onto the sub-harmonic they agree with each
other, and the fusion reads that agreement as confirmation rather than as one
piece of evidence counted twice. FM — the only timing-derived surrogate — is
frequently right in exactly those windows and is outvoted:

```
bidmc_01   AM 6.8   BW 6.9   FM 21.4   reference 22.7   ->  fused 6.80
bidmc_08   AM 21.6  BW 21.2  FM  3.7   reference 20.7   ->  fused 3.66
```

The second is the same failure in mirror image — the fusion takes FM alone while
both amplitude surrogates are right.

**The remedy: a sub-harmonic rescue arbitrated by the time-domain estimate.** `TD_RR`
counts breaths rather than reading a spectrum, so it cannot land on a
sub-harmonic at all — to report half the rate by counting you would have to miss
every second breath. Measured **0 %** half-rate reports against the fusion's
13–15 %. When it says the rate is twice what the spectrum says, the spectrum is
the one that is wrong.

**The tolerance is derived, not tuned: one Welch bin.** Two estimates separated
by less than the estimator's own resolution are indistinguishable from an exact
2:1 ratio, and it rescales with the sampling rate and `RR_WELCH_SEG`
automatically. Widening to 2 bins was measured and lowers MAE further
(1.74 → 1.59) but fires on 27 windows instead of 10 and costs within-2 accuracy
(82 % → 78 %) — a threshold chasing this dataset, so it is not taken.

This does **not** make `TD_RR` a fourth vote; that was built, measured and backed
out because it degrades the neonatal pair, where the time-domain route runs ~20 %
high. The rescue fires only on the 2:1 signature — **10 of 135 adult windows and
zero on the neonatal pair**, so that weakness is not reintroduced.

**Cost is negligible and none of it is in the hot path.** The breath intervals
are already extracted every window for RRV, so `TD_RR` is ~18 additions and one
division, and the rescue is one `fabs` and one comparison — about **21 flops
against the Welch DFT's 37,632**, or 0.06 %, once every 32.8 s. Zero added
operations per sample.

### Result — sub-harmonic locking

| stage | n | bias | MAE | within 2 | sub-harmonic |
|:---|---:|---:|---:|---:|---:|
| before | 151 | −1.76 | 2.36 | 74 % | 15 % |
| + band edges (nearest bin) | 135 | −1.54 | 2.15 | 82 % | 13 % |
| **+ sub-harmonic rescue** | 135 | **−0.98** | **1.74** | **82 %** | **7 %** |

Per record, 10 of 12 improve or are unchanged; `bidmc_05` (+0.37) and
`bidmc_10` (+0.14) are marginally worse. Biggest gains where the failures were
concentrated: `bidmc_09` 5.81 → 3.12, `bidmc_08` 3.94 → 2.10, `bidmc_03`
1.75 → 0.36.

**One honest caveat.** Coverage falls from 151 to 135 windows, because excluding
the spurious low bins means some windows no longer produce a peak at all. Part
of the MAE improvement is therefore survivorship rather than accuracy. The right
reading is that the pipeline now declines to answer where it previously answered
confidently and wrongly — which is the better failure — but the two effects are
not separable in a single MAE figure.

**Open at this step:** the remaining 7 % of sub-harmonic windows, and the
residual −0.98. `bidmc_01` and `bidmc_10` remain the worst records. *Both were
closed by later work — 1/f whitening, band edges rounded to the nearest bin, and
the sequential harmonic guard below. The shipped build reports 0 % sub-harmonic; see
[RESULTS.md](RESULTS.md).*

## Analysis window and Welch segment are derived from the band floor

The quantity that decides whether a respiratory peak can be resolved is the
number of breathing **cycles inside one Welch segment** — not inside the
analysis window. The identity:

```
T_seg   = RR_WELCH_SEG / intp_fs
cycles  = f x T_seg
bin / f = 1 / (f x T_seg) = 1 / cycles
```

so *"enough cycles"* and *"fine enough bins"* are the same requirement. The
analysis window contributes **nothing** to resolution — it only sets how many
segments are averaged, i.e. the variance.

**Measured threshold: 3 cycles.** Median absolute error over the 135 scored
adult windows, by cycles-per-segment:

| cycles/segment | n | median \|err\| |
|:---|---:|---:|
| **< 3.0** | 13 | **1.98** |
| 3.0 – 4.0 | 8 | 0.32 |
| 4.0 – 5.0 | 45 | 0.67 |
| 5.0 – 6.0 | 68 | 0.43 |

Three to six times worse below 3 cycles, flat above. So `RR_WELCH_SEG =
3 x intp_fs / f_lo`, which lands on powers of two almost exactly, with the
window at 4x the segment (13 averaged segments at the shipped 75 % overlap):

| category | floor | 3-cycle segment | **chosen** | T_seg | bin | **window** | latency |
|:---|---:|---:|---:|---:|---:|---:|---:|
| NEONATE | 22 | 127.8 | **128** | 8.2 s | 7.32 | **512** | **32.8 s** |
| CHILD | 11 | 255.7 | **256** | 16.4 s | 3.66 | **1024** | 65.5 s |
| ADULT | 4 | 703.1 | **512** | 32.8 s | 1.83 | **1024** | 65.5 s |

Both now live in `filter_bands.h` beside the band they derive from, not as
free-standing constants in `ppg_common.h`.

**Adult deviates from the rule deliberately.** Applying it to the 4 /min floor
gives a 703-sample segment, which rounds up to 1024 and forces a 4096-sample
window — **262 s** of latency, four times what is shipped. Adult keeps
**512 / 1024**, which carries 2.18 cycles at the floor rather than 3. The
justification is measured: doubling the segment from 256 to 512 inside the
existing window moved MAE only 1.74 → 1.65, because the gain is confined to
windows below 3 cycles, and a further doubling would reach fewer windows still
at four times the latency. Recorded in `filter_bands.h` beside the constants.

**Neonates were the reason for doing this** and gain the most. 128 samples still
gives 3.0 cycles at their 22 /min floor, so no resolution is lost, and latency
halves. Measured on the two neonatal recordings (raw 12-bit, `-nu 1`):

| | windows | median RR | IQR | latency |
|:---|---:|---:|---:|---:|
| 1024 / 256 (before) | 35 / 34 | 26.84 / 28.77 | 3.09 / 4.79 | 65.5 s |
| **512 / 128 (now)** | 71 / 66 | 28.35 / 28.78 | **2.33 / 1.99** | **32.8 s** |

The interquartile spread *tightens* despite the shorter window. **No neonatal
breath annotations exist**, so this is stability and plausibility, not a
demonstration of accuracy — the honest claim is that halving the latency costs
nothing visible, not that it improves the estimate.

**Child is unchanged at 256 / 1024** — the derivation lands on exactly what was
already there (255.7 → 256). It is now written out so the value is derived
rather than coincidental.

**Consequence for adults, stated rather than hidden:** with the shipped
512-sample segment the adult band floor of 4 /min carries only **2.18 cycles**,
short of the 3-cycle rule, so RR near the floor is resolved but not comfortably.
That is the price of 65.5 s latency, and it is why meditative breathing at
2–4 /min needs a separate operating mode rather than a wider band — three breaths
at 2 /min take 90 seconds, so no window arrangement measures it quickly:

| target floor | 3-cycle segment | window | latency |
|---:|---:|---:|---:|
| 2 /min | 2048 | 8192 | **8.7 min** |
| 3–4 /min | 1024 | 4096 | 4.4 min |
| 6 /min | 512 | 2048 | 2.2 min |

Adult output is **byte-identical** before and after this change, verified on the
12 annotated recordings.

## Low-frequency contamination: the 1/f background is whitened before peak-picking

`bidmc_01` and `bidmc_10` were the worst records with **excellent** beat
detection (F1 98.1 and 96.7), so the fault was never in detection.

**Diagnosis.** Dumping the in-band PSDs showed the error is a *jump*, not a
smear: chosen-bin minus true-bin is either **0 or −2/−3, never −1 and never
positive**. −3 bins lands on the band floor. Power in the lowest in-band bin
relative to power at the *true* respiratory bin, per record:

| rec | ref RR | P(floor) / P(true) | windows where > 1 | MAE then |
|:---|---:|---:|---:|---:|
| bidmc_06 | 20.00 | **0.09** | 5 % | 0.20 |
| bidmc_03 | 17.62 | 0.23 | 21 % | 0.36 |
| bidmc_10 | 18.34 | 0.30 | 30 % | 4.40 |
| bidmc_09 | 20.00 | 0.88 | 44 % | 3.12 |
| bidmc_01 | 21.26 | **1.09** | 50 % | 4.78 |

Almost monotonic with error. In `bidmc_01` the band floor outguns the real
respiratory peak in **half** the windows. Note `bidmc_06` and `bidmc_09` share
the same true RR of 20.00 and differ 24-fold in contamination — it is the
recording's baseline wander, not its rate.

**Why no filter removes it.** The RR band-pass corner sits *at* the band floor, so
the first in-band bin is only ~1.2× the corner frequency and receives drift at
almost full gain. Every filter passes 1.2× its own corner. Two things were
tested and ruled out on the way: parabolic refinement is not the cause (refined
beats raw argmax on every record), and the true peak *is* usually present — a
local maximum in 44–80 % of windows. It is simply outgunned.

**Fix: whiten the background before picking the peak.** A respiratory peak is a
*local excess over a smooth background*, not the largest number in the band. The
background is fitted as a straight line in log-power against log-frequency — the
standard 1/f^b form — and divided out; peak search, prominence and parabolic
refinement all then run on the whitened spectrum. Bin index stands in for
frequency, the two differing by a constant that lands in the intercept and
cancels.

Strategies compared at the per-surrogate level, all 12 recordings, 405 spectra:

| peak-picking rule | median \|err\| | mean \|err\| | within 2 |
|:---|---:|---:|---:|
| global argmax (previous) | 0.76 | 2.80 | 73 % |
| interior bins only | 0.74 | 2.61 | 69 % |
| best true local maximum | 0.71 | 2.38 | 72 % |
| **whiten 1/f, then argmax** | **0.64** | **2.22** | **79 %** |

### `RR_MIN_PEAK_PROMINENCE` had to be re-derived, not carried over

Whitening changes what `q` means. On a whitened spectrum the median sits at ~1
by construction, so `q` reads as "how many times the background does this peak
stand" — whereas the raw-PSD median is inflated by the drift skirt. Carrying the
old 2.5 across silently rejected **half of every neonatal recording**. The
measured trade:

| threshold | adult MAE | within 2 | adult coverage | neonatal coverage |
|---:|---:|---:|---:|---:|
| 2.5 | 0.64 | 97 % | 131/156 | 74/140 |
| 2.0 | 0.80 | 95 % | 144/156 | 103/140 |
| 1.7 | 1.11 | 93 % | 149/156 | 129/140 |
| **1.5** | **1.45** | **88 %** | **156/156** | **139/140** |

**1.5 is chosen because it is the only value that regresses nothing.** Raising
it buys genuine accuracy at the cost of coverage — that is a product decision,
and the curve is the input to it.

One consequence stated plainly: at 1.5 the gate never fires on adults. That is
informative rather than alarming — most of what it used to reject, it rejected
because drift dominated the band, and whitening removes the cause.

### Result — 1/f whitening

| adult, 12 recordings | before | after |
|:---|---:|---:|
| RR MAE | 1.74 | **1.45** |
| within 2 /min | 82 % | **88 %** |
| sub-harmonic windows | 7 % | **0 %** |
| coverage | 135/156 | **156/156** |

Per record, 11 of 12 improve or hold: `bidmc_01` **4.78 → 1.35**,
`bidmc_10` **4.40 → 0.61**, `bidmc_09` 3.12 → 0.66, `bidmc_08` 2.10 → 0.94.
**Excluding `bidmc_05`, MAE is 0.65.**

`bidmc_05` is the exception and the reason the headline is 1.45 rather than
0.65. That subject breathes at **5.98 /min**, and **the algorithm cannot
currently measure it** — the configured band starts at 6 /min and the searched
floor at 7.32. This is a limitation of our configuration, not of the subject:
5.98 /min is a perfectly ordinary rate for someone breathing slowly, and a
measurement device is responsible for covering the physiology, not the other way
round. Previously the drift skirt returned the band floor — a number that
*looked* close to 5.98 while measuring nothing. With the skirt gone the
estimator has no fundamental to lock to and its answers are frankly wrong rather
than accidentally plausible, which is at least honest. See the adaptive-floor
assessment below for what covering this case would take.

**Neonatal**, where no breath annotations exist: coverage 137/140 → 139/140, and
median RR moves 28.4 → 38.4 and 28.8 → 40.2. Normal neonatal RR is **30–60/min**
(Fleming WT4), so the new values sit inside the expected range and the old ones
sat below it — consistent with the same contamination having pulled neonates
toward *their* band floor of 22. Suggestive, not demonstrated: the IQR also
widens (2.3 → 6.5, 2.0 → 7.1), and without annotations neither can be confirmed.

### Lowering the adult band floor to 4 /min — measured, not adopted

Tested because `bidmc_05` sits below the floor. With whitening and the same
threshold, coverage 156/156 in both:

| floor | MAE | within 2 | sub-harmonic | MAE excl. 05 | bidmc_05 |
|---:|---:|---:|---:|---:|---:|
| **6 /min** | 1.45 | 88 % | **0 %** | **0.65** | 10.27 |
| 4 /min | 1.40 | 88 % | 4 % | 1.00 | 7.14 |

It helps `bidmc_05` (10.27 → 7.14) and hurts almost everything else —
`bidmc_10` +1.67, `bidmc_01` +1.18, `bidmc_08` +0.83 — because a 4 /min floor
maps to **bin 1 = 3.66 /min**, the very bin most contaminated by drift, and
re-admits sub-harmonics (0 % → 4 %). MAE excluding the out-of-band record gets
**worse, 0.65 → 1.00**.

And it does not solve the case it was meant to: at 5.98 /min a 16.4 s Welch
segment holds **1.63 breathing cycles**, well below the 3-cycle threshold. The
limit is resolution, not the band. Measuring 5.98 /min needs a longer segment —
the same conclusion the meditative-breathing note reaches. The floor stays at 6.

## Why the adult band floor is 4 /min, and what it costs

Commercial PPG monitors report RR down to 4 /min, so a 6 /min floor is a
limitation of ours rather than of the physiology. Getting there took four
changes, three of which are structural rather than tuned.

**The blocker was SEGMENTATION, not the window.** The 1024-point window is
65.5 s = **4.4 cycles at 4/min**, already ample. Welch was chopping it into
16.4 s segments = **1.09 cycles**. Lengthening the segment to 512 (32.8 s,
2.18 cycles at the floor) at 75 % overlap keeps **five** segments in the same
window, so resolution is bought without giving up the averaging — and without
touching latency.

**The residual failure at low rates is HARMONIC locking, not lost resolution.**
On the slow-breathing recording (true 5.3–6.6 /min) the surrogates reported
**12–27** — the 2nd, 3rd *and* 4th harmonics — while `TD_RR` read 5.9–9.0,
nearly correct. A non-sinusoidal breath carries energy at 2f, and with the
fundamental near the band-pass corner it is attenuated while 2f sits mid-band.
A guard testing for exactly 2:1 fired on **zero** of those windows; a ratio test
(`AVG ≥ 1.8 × TD` → prefer the breath count) catches them. Swept at 1.6 / 1.8 /
2.2 — all give MAE 0.84–0.89, so it is not a sharp threshold.

**The background fit had to become robust.** Admitting the drift-loaded bin
dragged the least-squares log-log slope steeper, over-correcting the mid-low
bins and manufacturing a *new* half-rate lock — `bidmc_01` windows reading 21.83
correctly at floor 6 jumped to 11.14 at floor 4. Theil-Sen (median of pairwise
slopes) ignores that bin instead of being led by it. No fitted constants.

### Result — the 4 /min floor

| adult, 12 recordings | before | **shipped** |
|:---|---:|---:|
| RR MAE | 1.45 | **0.85** (0.66 excluding bidmc_05) |
| bias | +0.93 | **+0.08** |
| within 2 /min | 88 % | **93 %** |
| sub-harmonic | 0 % | 2 % |
| coverage | 156/156 | **612/612** |
| median beat F1 | 99.0 | 99.0 |
| **slow-breathing record** | **10.27** | **2.92** |

Ten of twelve records are at ≥ 92 % within-2, six at 100 %. The one that could
not be measured at all now reads 2.92 MAE against a true 5.98 /min.

**The cost, stated plainly:** MAE on the other eleven records goes 0.65 → 0.66 —
essentially nothing — but the sub-harmonic rate goes 0 % → 2 %, and `bidmc_08`
(1.35) and `bidmc_01` (1.75) are slightly worse than their floor-6 bests. That
is the price of admitting the low bins.

### Reporting cadence: 8.2 s

`RR_WINDOW_SLIDE_PTS` is 128 grid points, so a report arrives every **8.2 s**
rather than every 32.8 s. The first still needs a full window (65.5 s).

**The slide does not change any estimate** — proved directly: slide 128 and
slide 256 over the same window and segment give 336 matched instants with **0
differences, max difference 0.000000**. Any MAE difference between slide
settings is a sampling artefact of which instants were scored.

**Consumers must know** that consecutive reports now share 87.5 % of their data.
They are not independent measurements, and "N consecutive readings above a
threshold" logic would be invalid on them.

### Why not a 512-point window

Measured, and it cannot give both resolution and averaging:

| config | MAE | within 2 | excl. 05 | slow record |
|:---|---:|---:|---:|---:|
| **win 1024, seg 512 @75 % (shipped)** | **0.85** | **93 %** | **0.66** | **2.92** |
| win 512, seg 512 (one periodogram) | 1.33 | 84 % | 1.04 | 4.61 |
| win 512, seg 256 @75 % (3 segments) | 1.44 | 86 % | 1.16 | 5.27 |

A 512 window forces a choice: segment 512 gives resolution but no averaging;
segment 256 gives averaging but only 1.09 cycles at 4/min, losing the floor-4
capability. The 1024 window is what allows both.

### Neonatal

No breath annotations, so plausibility and stability only. Coverage 142/142 and
138/138 reports at 8.2 s; median RR 42.6 and 43.3, inside the normal **30–60**
range (Fleming WT4); HR 153 and 147, inside the 90–181 range. The harmonic guard
fires twice in 280 windows, as intended — `TD_RR` runs *high* on neonates, so the
ratio rarely reaches 1.8 and the known weakness of the time-domain route there is
not reintroduced.

## The surrogate grid rate is derived from fs

A constant 8 raw samples per grid point would make the 1024-point analysis
window cover `8192 / fs` **seconds** — a duration that slides with the input
sampling rate, and is correct only at the 125 Hz design point. The constant is
therefore the grid *rate*:

```c
#define RR_INTP_GRID_HZ  (15.625)             /* = 125 / 8, the design point */
ps_ppg->cfg_rr_decimation = RR_DECIMATION(sampling_rate);
```

15.625 Hz is exactly 125/8, so **the 125 Hz behaviour is unchanged** — the
decimation still comes out as 8. Measured on `bidmc_01/02/06/11`, resampled and
re-run with `-r`:

| fs | decimation | grid | window | RR MAE before | RR MAE after |
|---:|---:|---:|---:|---:|---:|
| 100 Hz | 6 | 16.67 Hz | 61.4 s | 1.72 | **1.76** |
| **125 Hz** | 8 | 15.63 Hz | 65.5 s | 1.81 | **1.80** |
| 250 Hz | 16 | 15.63 Hz | 65.5 s | 2.16 | **1.75** |
| 367 Hz | 23 | 15.96 Hz | 64.2 s | **3.05** | **1.87** |

The spread across rates collapses from **1.33 to 0.12** breaths/min, and 367 Hz
— a rate that occurs in real data — improves by **39 %**. RR accuracy is now a
property of the algorithm rather than of the acquisition hardware.

The banner reports the resolved grid at startup, so a wrong `-r` is visible
rather than silent. It names the subject's **settled** window and bin, and the
warm-up size the window starts from:

```
RR grid: decimation 23 -> 15.957 Hz grid; window 64.2 s (grows from 8.0 s), bin 1.87 /min
```

*(It previously printed only `sel_rr_window_pts`, which at that moment still
holds `RR_PROG_MIN_PTS` — so an adult run reported an 8.2 s window against a real
analysis span of 65.5 s. That was corrected: a log line must not understate the
window it describes.)*

Two notes. The Chebyshev low-pass (`BP_LP_CORNER_HZ`, 6 Hz) stays below the
grid's Nyquist at every supported rate, so the decimation does not alias. And
the fs/4 warning for the RR band upper edge is computed from the derived grid,
so it still fires correctly.

## Post-filter smoothing, applied to both detectors

Neither TERMA nor IMS smooths ahead of detection — both papers operate on the
band-passed stream directly. A smoothing stage is added here anyway, for a reason
specific to this pipeline: `foot_value` and `peak_value` **are** the BW and AM
respiratory surrogates, so a fiducial that lands one sample early on a noisy
crest reports the wrong *amplitude*, and that error goes straight into the signal
the RR estimate reads. Detectors are judged on timing; here amplitude matters
just as much.

The window length is measured, not assumed. A duration sweep over all 12
annotated recordings puts the optimum at 40 ms, degrading on either side:

| smoothing | median F1 | RR MAE | bias | within 2 |
|:---|---:|---:|---:|---:|
| off | 99.0 | 2.47 | −1.88 | 72 % |
| 24 ms | 99.1 | 2.43 | −1.84 | 74 % |
| **40 ms — shipped** | **99.0** | **2.37** | **−1.77** | **74 %** |
| 64 ms | 98.7 | 2.67 | −2.08 | 72 % |
| 96 ms | 98.7 | 2.56 | −1.98 | 73 % |

Implemented once, in `chebyshev_t2_o4.c` (`smooth_int_sample()`), so every
detector sees the same conditioned stream; `ppg_fiducial.c` is the exception, as
it has always carried its own and is left alone. Configured by `PPG_SMOOTH_MS`
in `filter_bands.h`, and expressed in **milliseconds rather than taps** so it
obeys the same sampling-rate rule as everything else: 40 ms is exactly 5 taps at
125 Hz and stays a 40 ms window at 367 Hz, where a constant 5 taps would be 13.6 ms
and a different filter entirely.

Measured result with both changes in, all 12 recordings at 125 Hz:

| | before | after |
|:---|---:|---:|
| median beat F1 | 99.0 | **99.0** |
| worst beat F1 | 95.9 | 95.7 |
| RR MAE | 2.47 | **2.37** |
| RR bias | −1.88 | **−1.77** |
| within 2 /min | 72 % | **74 %** |

**Honest reading of the smoothing gain.** Per recording, almost all of the
0.10 /min comes from **one** record (`bidmc_07`, −0.71); the other eleven move by
≤ 0.10. That is a small effect on thin evidence, and it is worth saying so
plainly rather than presenting it as a clear win.

Three things justify keeping it. The **mechanism** is argued from the pipeline
rather than fitted to the data: amplitude fidelity at the fiducial matters here
because the amplitude *is* a surrogate. The sweep **optimum is broad** — 24 ms
and 40 ms are within 0.06 /min of each other — so the value sits on a plateau,
not on a spike that a different cohort would move. And **nothing regresses**:
beat detection, HR and RRV are all unchanged, so the stage costs nothing if the
gain is really only that one recording.

It is nonetheless an unsourced value — no paper specifies a smoothing stage at
this point — and `filter_bands.h` labels it as such under its `UNSOURCED`
discipline rather than dressing it up with a citation it does not have.

Group delay is `(taps − 1) / 2` = 2 samples (16 ms at 125 Hz), applied equally
to every fiducial, so beat *intervals* — and therefore HR and HRV — are
unaffected.

### Both changes re-measured on IMS

The two changes are detector-independent by construction, so IMS was re-run to
check that — and to make sure the smoothing was not a TERMA-specific gain.

**Regression check first.** IMS built with `PPG_SMOOTH_MS=0` reproduces its
pre-change figures *exactly* — median F1 96.9, worst 88.0, RR MAE 2.58, bias
−1.87, within-2 68 %. Since 15.625 Hz is 125/8, the derived decimation resolves
to 8 at 125 Hz and the change is a genuine no-op there. Both `PPG_SMOOTH_MS` and
`RR_INTP_GRID_HZ` are now `-D`-overridable so this A/B can be repeated.

**Deriving the grid from the input rate helps IMS as much as TERMA** — every rate
other than the design point improves, and the spread more than halves:

| fs | old decim / window | old MAE | new decim / window | new MAE |
|---:|---:|---:|---:|---:|
| **125 Hz** | 8 / 65.5 s | 3.48 | 8 / 65.5 s | 3.48 |
| 100 Hz | 8 / 81.9 s | 3.90 | 6 / 61.4 s | **2.98** |
| 250 Hz | 8 / 32.8 s | 3.56 | 16 / 65.5 s | **2.90** |
| 367 Hz | 8 / 22.3 s | 4.83 | 23 / 64.2 s | **3.15** |
| | | *spread 1.35* | | *spread 0.58* |

IMS's residual 0.58 spread is larger than TERMA's 0.12, and the cause is in IMS
itself rather than in the grid: `ims_m = ThT_samples / 2` is an integer division,
so the segment length quantises to m = 1, 2, 4, 5 at 100, 125, 250 and 367 Hz.
That is a real (if mild) rate-dependence in the detector — tolerable only because
the parameter sweep recorded in `ppg_fiducial_karlen_ims.c` shows that m barely
matters. TERMA has no equivalent, which is one more reason it is the default.

**The smoothing does *not* generalise to IMS.** Isolated, all 12 recordings:

| | median F1 | worst F1 | RR MAE | bias | within 2 |
|:---|---:|---:|---:|---:|---:|
| IMS, smoothing off | 96.9 | 88.0 | **2.58** | −1.87 | 68 % |
| IMS, smoothing on | **97.1** | **88.5** | 2.61 | −1.90 | 68 % |

Beat detection improves slightly, as it does for TERMA. RR gets marginally
*worse*, and the sign flip is one recording: `bidmc_01` goes 7.95 → **9.75**
(+1.80) while `bidmc_07` improves 3.88 → 3.13 (−0.75), the same record that
improved under TERMA. Everything else moves by ≤ 0.22.

This sharpens the earlier caveat rather than overturning it. Across both
detectors the smoothing is **neutral-to-slightly-positive for beat detection and
inconsistent for RR**, with the aggregate in either direction decided by one or
two recordings out of twelve. The honest summary is that at 40 ms it is not
harmful, its effect on RR is within the noise of a 12-recording sample, and the
case for it rests on the amplitude-fidelity mechanism above and on the sweep's
broad optimum — not on a demonstrated RR gain.

`bidmc_01` is worth noting on its own: it is the worst RR record under every
detector (TERMA 5.50, IMS 7.95–9.75) and IMS's PPV there is only 87 %. It is a
strong candidate to look at first when the residual bias is investigated.

### Liu's AM definition (peak − valley): tested and NOT adopted

Liu takes AM as the peak curve minus the valley curve, on the reasoning that
`peak = B + A·p_max` carries baseline *and* amplitude, while `peak − valley`
isolates `A`. That holds for Liu's pipeline, whose peaks and valleys are read
from a signal that has only been LOW-passed (passband < 3 Hz, hardware
high-pass 0.05 Hz), so the baseline is fully present and the valley is a good
estimate of it.

It does not hold here, and it measurably hurts:

| | AM = peak (current) | AM = peak − valley (Liu) |
|:---|---:|---:|
| fused MAE | **3.39** | 4.25 |
| within 2/min | **65 %** | 51 % |
| AM ref/est median | **1.10** | 1.43 |
| AM within ±15 % | **55 %** | 35 % |

The reason is visible in the surrogate correlations over all 14 recordings:

| | median r |
|:---|---:|
| corr(peak, foot) — our AM's coupling to BW | **+0.409** |
| corr(peak − foot, foot) — Liu AM's coupling to BW | **−0.425** |

**The subtraction does not decorrelate AM from BW; it flips the sign of the
coupling at similar magnitude.** Algebraically `corr(P−F, F) ∝ r·σ_P − σ_F`, so
it only vanishes when `r ≈ σ_F/σ_P`, which is not the case here — the
sample-path bandpass has already removed the slow baseline, leaving σ(peak) and
σ(foot) comparable, so subtracting the foot mostly injects the foot's own noise
into AM. In two recordings (`bidmc_06`, `bidmc_08`) where σ(foot) ≪ σ(peak) the
coupling even stays positive.

Keeping the systolic peak alone is therefore the right choice for this pipeline,
and the earlier claim that "AM and BW share a baseline component" — while true
(r = +0.41) — does not imply Liu's remedy is the answer to it.

## Known limits — not solvable in software alone

1. **Beats-per-breath.** At HR 150 and RR ~28/min there are ~5.4 beats per
   breath, which is workable. Should RR rise toward 60/min the surrogates —
   which carry one true sample per beat — approach their Nyquist limit and no
   amount of filtering will recover the rate. The code warns when the band's
   upper edge nears fs/4.

2. **Inter-surrogate spread is now 2.03 / 3.29 bpm** (from 3.45 / 5.18
   originally, via 4.32 / 5.39 with the corrected band but a single
   periodogram). Windows where AM and BW differ by more than 5 bpm fell from
   42 % to 11 % on file 1. File 2 is more stubborn — its BW surrogate is weak
   (median prominence 2.67 against AM's 6.12) and its spectrum is genuinely
   multi-modal, with comparable energy at 25.6, 29.3 and 33.0 /min, which is
   normal for neonatal periodic breathing. The residual is reported per window
   in the new `Spread_bpm` column rather than hidden: treat a large value as low
   confidence in that window.

3. **The prominence gate never fired** on these two files (0 windows rejected) —
   once the band is correct, every window has a credible peak. The threshold is
   therefore untested against genuinely bad data. *(The value quoted when this was
   written was 2.5, calibrated against the raw PSD. It was re-derived to
   `RR_MIN_PEAK_PROMINENCE` = **1.5** once the spectrum was whitened, because a
   whitened background sits at ~1 by construction and the two numbers are not
   comparable. Still treat it as provisional.)*

4. **No ground-truth respiration reference exists for these files** — no
   capnography, no impedance channel, no ventilator rate. The agreement above is
   against an independent *algorithm*, not against a measured truth.

   *(When this was written the category was inferred from heart rate. It no
   longer is: the category is **declared** with `-s`, and measured HR is used only
   to warn when it disagrees with what was declared. The exposure is therefore not
   a bad inference but a bad declaration — which the HR sanity check now tests
   both at start-up and on the settled rate; see "`filter_bands.h` — every filter
   parameter, cited".)*

---

> **Measured results have moved.** Every figure describing how the shipped
> build performs — per-recording tables, detector comparison, sampling-rate
> invariance, HRV against ECG — now lives in one place:
> **[`RESULTS.md`](RESULTS.md)**.
>
> This document keeps only the measurements that *justify a decision* at the
> point the decision is explained, because those are evidence for a choice
> rather than a claim about the product. Anything quotable about accuracy
> belongs in `RESULTS.md` and is maintained there alone.

## Surrogate co-timing: why the grid buffers are twice the window

AM, BW and FM are filled from three different fiducials — peak, foot and maximal
upslope — so they do **not** reach a full analysis window on the same beat. Two
things follow, and both are load-bearing:

- The grid buffers are `INTP_MAVG_BUFF_SIZE = RR_MAX_WINDOW_PTS * 2` — 2048 grid
  points — not one window's worth. Whichever surrogate runs ahead must be able to
  hold its surplus while the others catch up; a buffer sized to the window exactly
  would either drop those points or force an early analysis. It is sized from the
  *largest* category rather than the active one, so the allocation does not change
  shape when the patient knob moves.
- The window fires only when **all three** hold a full window. `estimate_resp_rate()`
  takes `avail` as the *smallest* of the three vertex counts and returns early
  until that reaches the subject's full window; each then slides by the same
  `RR_WINDOW_SLIDE_PTS`, so the relative offsets are preserved from one window to
  the next.

**Verified by measurement, not by reading.** Instrumenting the gate over three
adult recordings and one neonatal recording:

| | adult (worst of 3) | neonatal |
|:---|---:|---:|
| windows analysed | 51 | 138 |
| max divergence between the three counts | 121 grid points (7.7 s) | 74 |
| peak buffer occupancy | 1125 of 2048 | 561 of 1024 |
| appends dropped for want of space | 0 | 0 |
| **spread between the three window START times** | **0 samples** | **0 samples** |

The last row is the one that matters: in every analysed window on every
recording, sample 0 of AM, BW and FM sits at the *same absolute raw index*. The
divergence in the row above it is entirely surplus — how far each surrogate has
run PAST the window — which is exactly what the doubled buffer exists to absorb.
The three spectra therefore describe the same span of time, which is the
precondition for fusing them at all.

Past capacity the sample cannot be stored while the count must still advance, so
the surrogate would slip in time against the other two. That case now prints a
warning rather than being absorbed silently: peak occupancy is 55 % of capacity
and it has never fired, but a silent alignment loss would be invisible in every
output the pipeline produces.

## Early reporting, spectral accumulation and a quality gate

Three changes that work together. Each was measured separately before being
combined; the numbers below are the 12 annotated adult recordings.

### 1. The window grows instead of waiting

A full window is 65.5 s, and a monitor that shows nothing for the first minute
is not usable. The window now starts at `RR_PROG_MIN_PTS` = 128 grid points and
doubles — 128 → 256 → 512 → 1024 — after which the estimator is **exactly** the
shipped one and never shrinks again.

Frequency resolution is `1/T_segment`, so a short record genuinely does not
contain a slow rate. While the window is short the reportable floor is therefore
raised to what the segment can resolve (`RR_MIN_CYCLES_PER_SEG` = 3 cycles):
**22 /min at 8.2 s, 11 /min at 16.4 s, 5.5 /min at 32.8 s**. Once full, the
declared band is used unchanged — the adult 4 /min floor is a deliberate
2.18-cycle compromise and is not silently overridden.

Rows emitted before the window fills are marked **`_PROV`** in the `Method`
column. The CSV column set is settled, so the distinction goes in a column whose
vocabulary is already a set of names, not in a new field.

**Resolution comes from duration, not from sample count.** Zero-padding widens
nothing real — the −3 dB main lobe is 5.49 /min padded against 2.75 /min for a
genuinely full record — and a 32 ms surrogate grid measures identical to 64 ms at
matched segment duration. Seconds carry the information; samples do not.

### 2. Cross-window spectral averaging

`RR_PSD_ACCUM_N` = 4, an exponential average of the in-band PSD, per surrogate,
applied before the peak search. This is Charlton's temporal fusion (FT1) — of
the top-ranked algorithms in that survey, "all except three used either smart
fusion or temporal fusion", and we had the first and not the second.

Averaging the **spectrum** rather than the output rate is the point: it changes
which peak *wins*. Averaging the output cannot — once the peak-picker has taken
a sub-harmonic, no downstream filter recovers the fundamental. Measured: output
mean-of-4 gives 0.82 and output median-of-4 0.74, against **0.66** for this.

The average is discarded whenever the segment length changes during warm-up: a
bin index means a different frequency at a different segment length, and
carrying it across would average two different frequency axes together.

### 3. Quality gate — Karlen 2013 smart fusion

*"If their standard deviation is ≤ 4 bpm then RR is estimated as the mean,
otherwise no RR is output."* `Spread_bpm` **is** that standard deviation. It was
being computed and then ignored — the estimate fell back to the single most
prominent surrogate instead of declining.

| | n | MAE | within-2 | sub-harm |
|:---|---:|---:|---:|---:|
| ALL_THREE | 351 | 0.40 | 98 % | 0.3 % |
| TWO_AGREE | 195 | 0.80 | 94 % | 2.1 % |
| **BEST_ONLY (the fall-back)** | **26** | **5.95** | **35 %** | **30.8 %** |

Those 26 windows are exactly what the gate now declines, so **`BEST_ONLY` is no
longer an outcome the program can produce** — it is listed here as the measurement
that justified the gate, not as a value to expect in `Method`. A window with only
one corroborating surrogate reports `DECLINED`.

Two conditions decline a window, and they are applied differently. **No
corroboration** (only one surrogate agreed) is a statement about the evidence,
not about how much data exists, so it declines at any stage. The **spread
threshold** applies only once settled, because a warm-up row is already marked
provisional and declining it as well pushes the first number past usefulness.

A declined window is also **undone from the accumulator** — a window we would
not report must not corrupt the next four either.

Karlen's 4 /min is an absolute figure measured on adults. A neonatal band spans
22–66 against the adult 4–30, and three estimates of a 45 /min rate scatter
proportionally more than three estimates of a 16 /min one; applied unchanged it
declined more than half of all neonatal windows. The threshold is therefore
scaled by the declared band width, anchored so the **adult band reproduces
Karlen's number exactly** (adult 4.0, child 6.5, neonate 6.8).

The harmonic rescues are **disabled during warm-up**. Both compare the spectral
estimate against the breath count, which assumes the fundamental was inside the
searched band — while the floor is raised it may be excluded by construction,
and the ratio test then fires on a rate the search could never have found.
Measured on warm-up rows with the rescue enabled: MAE 6.26, 43 % sub-harmonic.

### Result — early reporting and the quality gate

These are the figures **as measured at this step** — the harmonic guard came
afterwards and moved them again. Current figures are in
[`RESULTS.md`](RESULTS.md).

| | n | MAE | within-2 | sub-harm |
|:---|---:|---:|---:|---:|
| **settled rows** | 551 | **0.47** | **98 %** | **0.0 %** |
| provisional (warm-up) | 45 | 1.55 | 78 % | 4.4 % |
| all reported | 596 | 0.55 | 96 % | 0.3 % |
| *(previously, everything reported)* | 612 | 0.85 | 92 % | 2.1 % |

**Coverage 87 %** at this step, **91 %** once the harmonic guard was added. The
first valid report stopped being a flat 65.5 s — a full window — and became a
median of well under half that.

The range is 18.1–82.8 s, and the late end is honest rather than a failing: the
82.8 s recording is the 6 /min breather, and 16–33 s of data cannot contain a
6 /min rate at any FFT length. The subject who most needs a fast answer is the
one the physics makes wait.

### Three invariants any future change must keep

Each of these was broken during development and each failed silently:

1. **All three surrogates slide together or not at all.** Sliding whichever one
   is ahead lets them drift apart, and the common window then never refills —
   the estimator runs at a short window for the rest of the recording with
   nothing in the output to say so.
2. **Rate-limit reports during warm-up.** Nothing is discarded while the window
   grows, so without a cadence limit every beat triggers a report — 2058 rows
   for a 480 s recording.
3. **Timestamp from the stream position**, not a running sum of slide
   increments. The cadence changes when warm-up ends, and an accumulated
   timestamp drifts the moment it does — observed reaching 1286 s on a 480 s
   recording.

### Not addressed: rate drift

Sampling-clock drift is a non-issue: a one-bin shift needs **61 000 ppm** at
30 /min, against 100 ppm for a loose crystal — 600× smaller. Over a 90 s
accumulation a 100 ppm clock slips 9 ms, 0.14 of one grid sample.

*Physiological* drift is real (20 → 24 /min is 2.2 bins) and is **not** caught
by the quality gate: median spread is 0.67 / 1.17 / 0.78 across drift buckets of
< 0.5, 0.5–1 and 1–2 bins. A drifting subject produces three surrogates that
agree with each other and move together, which looks like a good window. Gating
the accumulator on peak movement was measured **worse** (0.77 against 0.66),
because a sub-harmonic lock *is* a peak jump and the gate resets on exactly the
windows the averaging exists to smooth. Left unhandled: 13 of 564 windows drift
more than a bin, costing +0.07 MAE there against −0.22 everywhere else.

## Known limitation: the 1/f background is fitted over the NARROWED search range

**Open. Found, measured, and left in place — the alternative makes the affected
recording worse, so it needs a decision rather than a change.**

The whitening fits its log-log background model over `k_lo..k_hi`. The harmonic
guard narrows that range, so on a tracked slow breather the background ends up
fitted to **three bins that ARE the respiratory peak**. Instrumented on the
6 /min recording:

```
klo=2 khi=4 frac=3.0000   psd[klo..khi] = 8.9  34.9  40.0
```

The maximum is at bin **4**; the search returns bin **3**. The background model
has absorbed the peak and inverted the whitened spectrum. All three surrogates
then return the identical value and the parabolic refinement yields zero, so the
output snaps to a bin centre — which is why that recording reports only 5.49 and
7.32 /min (bins 3 and 4) for long stretches, against a truth of ~6.2.

**Fixing it correctly makes that recording worse**, because the current numbers
depend on the error:

| | shipped | background fitted over the full band |
|:---|---:|---:|
| cohort settled MAE | 0.42 | **0.40** |
| coverage | **90 %** | 87 % |
| bidmc_05 MAE | **1.02** | 1.09 |
| bidmc_05 coverage | **67 %** | **25 %** |

With the background modelled properly, sub-bin refinement starts working (2 of
57 windows land on a bin centre, against most before) and that recording's
respiratory peak turns out to have prominence **q ≈ 1.0–1.5** against a gate
threshold of 1.5 — it is genuinely marginal, and 24 of its windows are then
rejected. The narrow fit had been suppressing the higher bin and selecting the
lower one, which happens to sit nearer the truth: as it stands the error is −0.7,
and with the wider fit it is +1.4.

**So the slow-breathing figures in the tables above are flattered by this.** The
whole cohort coverage loss is that one recording; the other eleven are
unaffected either way. Three honest routes, none taken:

1. Accept the correction and the coverage loss (25 % on that recording).
2. Correct it and re-derive `RR_MIN_PEAK_PROMINENCE` — but that threshold gates
   every recording and was derived, not swept.
3. Leave it, with this note.

## The anchor must not be a band-edge peak

**Found by asking why one recording took 69 s to say anything.**

The three surrogates are fused by picking the most PROMINENT one as an anchor
and averaging whichever others agree with it. Prominence is not correctness:

| t | AM (q) | BW (q) | FM (q) | anchor | outcome |
|---:|---:|---:|---:|:---|:---|
| 34.8 s | 15.74 (4.4) | 15.98 (5.3) | **5.49 (15.0)** | **FM** | `DECLINED` |

The true rate is 15.3 /min. **Two surrogates agree to within 0.24 /min and both
are correct** — but the third, at three times their prominence, is anchored on,
so neither falls inside `RR_AGREEMENT_THRESHOLD` and the window is declined for
"no corroboration". That repeated for 35 s while the answer sat in plain view.

The outlier's 5.49 /min is **exactly `k_lo`**, the lowest searchable bin. A peak
pinned there is the truncated drift skirt rather than a resolved respiratory
peak — `estimate_rr_peak_bin()` already refuses to refine it parabolically for
that very reason — yet it was still allowed to be the anchor.

**Fix.** An edge-pinned surrogate may still JOIN the average if it agrees; it
may not be the thing agreement is measured against. If every candidate is
edge-pinned the original choice stands, because then there is nothing better.

| | before | after |
|:---|---:|---:|
| bidmc_02 first report | 69.1 s | **34.8 s** |
| bidmc_02 MAE | 0.48 | 0.46 |
| cohort settled MAE | 0.42 | 0.42 |
| within-2 | 99 % | 99 % |
| provisional MAE | 1.53 | **1.41** |

**Every other recording is unchanged** — same first report, same MAE to two
decimal places. Coverage moves 91 % → 90 % (one row), because five rows shift
from settled to provisional when the recording starts reporting earlier.

### Why the slow breather still waits 82.8 s

bidmc_05 remains OPEN. Its three surrogates lock onto DIFFERENT harmonics of the
true 6 /min — 2.8f, 4.0f, 4.5f — so they never corroborate each other, while the
breath count reads **7.49 from 34.5 s onward**. The correct answer exists early;
the harmonic rescues are disabled during warm-up (the raised floor makes their
ratio test invalid), so it cannot be used until the window fills.

The band-edge rule above is what applies here: it removes a surrogate on the
grounds that **its own peak is unresolvable**, not on the grounds that the others
outvote it. Anchoring on the consensus instead would reintroduce what the
sub-harmonic rescue's own note warns against — AM and BW are both amplitude
surrogates of the same waveform, so baseline wander corrupts them TOGETHER, and a
majority vote reads their agreement as confirmation rather than as one piece of
evidence counted twice.

## Harmonic guard by sequential tracking

**Source.** Zhang C, Wei S, Dong G, Zeng Y, Zhu G, Zhou X, Liu F, *"Respiratory
rate estimation from photoplethysmogram baseline wandering by harmonic analysis
and sequential fusion"*, Biomedical Signal Processing and Control 100:107006,
2025, doi:10.1016/j.bspc.2024.107006. Closed access; the authors publish their
code at `github.com/Chi1988723/PPG-respiratory-rate-estimation`, which is what
this was read from.

**Why.** A breathing waveform is not a sine — it carries energy at 2f and 3f as
well as f. When the fundamental is weak the spectrum can lock onto a harmonic
and report a multiple of the true rate. `RR_HARMONIC_RATIO` catches this only
when the breath COUNT disagrees, and on a slow breather the count is unreliable
too. Constraining the search to the neighbourhood of an already-established rate
catches it directly: a subject breathing at 6 /min does not jump to 24 /min in
one window.

**What.** Their sequential fusion is a scalar Kalman filter on the rate whose
covariance feeds back as the *next window's search range* — not a smoother on
the output. `div = P/R`, range `[rr/(1+div), rr·(1+div)]`. Q, R, the agreement
tolerance and the confidence cap are all theirs. A `DECLINED` window never
updates the tracker: a window we would not report must not steer the next one.

**The one deviation, and why.** Applied unconditionally as published it repairs
the slow record and costs everything else. The ambiguity only exists where a
harmonic of the true rate is **itself inside the band**, i.e. 2f ≤ f_hi. Above
f_hi/2 there is no in-band harmonic to be confused with, so narrowing protects
against nothing and costs only responsiveness to a real rate change. The
condition is derived from where the ambiguity can exist, not swept.

| | cohort MAE | within-2 | coverage | bidmc_05 |
|:---|---:|---:|---:|---:|
| guard off | 0.47 | 98 % | 87 % | 2.93 |
| **as published** (always on) | 0.54 | 98 % | 91 % | **1.02** |
| **adopted** (only below f_hi/2) | **0.42** | **99 %** | **91 %** | **1.02** |

Per recording, the whole effect is one record: bidmc_05 2.93 → **1.02**,
bidmc_02 0.53 → 0.48, bidmc_11 0.83 → 0.84, **every other record unchanged to
two decimal places**. bidmc_05's coverage doubles, 32 % → **67 %**, and its
within-2 goes 61 % → **95 %**.  (Cohort coverage reads 90 % in the final build
rather than the 91 % measured here, because the band-edge anchor rule that came
afterwards moves five rows from settled to provisional.)

**The method counts show it is preventing rather than repairing.** `TD_HARM`
falls from 13 to 2 and `DECLINED` from 78 to 50: the breath-count rescue barely
needs to fire, and fewer windows disagree enough to be declined. A guard that
merely corrected afterwards would leave those counts alone. Neonatal is inert — its band ceiling of 66 /min puts the
threshold at 33, above the observed 43 — and verified identical with the guard
on and off.

**The knob.** `RR_TRACK_HARMONIC_GUARD`, default **1**. Build with
`-DRR_TRACK_HARMONIC_GUARD=0` to disable and get the "guard off" row above. It
is compile-time rather than a command-line option because it is not a property
of the patient — unlike `-s` and `-d`, nothing about a given recording should
change it.

> **The evidence rests on one recording.** bidmc_05 is the only slow breather in
> the cohort, so the entire measured gain comes from it. The mechanism is sound
> and the cost elsewhere is nil, but this has not been demonstrated across a
> population of slow breathers. That is the reason the knob exists.

### What was NOT taken from that paper

Its other contribution — scoring each candidate f by the summed power at f, 2f,
3f … — **does not transfer**, for a structural reason. The number of harmonic
terms depends on f, and what matters is how many the *true* rate gets:

| candidate | terms in our 4–30 band | terms in their 4–80 band |
|---:|---:|---:|
| 6 /min | 5 | 13 |
| 15 /min | 2 | 5 |
| **20 /min** | **1** | 4 |

We band-pass the surrogate to the respiratory band *before* the PSD; they
low-pass at 66 /min and keep everything up to the heart rate. A normal adult at
15–20 /min therefore gets **exactly one term** in our band — the sum degenerates
to a plain periodogram for the right answer while handing a spurious 10 /min
candidate three terms, one of which is the true peak itself. Measured on
this tree in August: harmonic sum MAE 1.11 against 0.86 with sub-harmonic
locking 4 % against 2 %; product 1.99 / 10 %; geometric mean 0.82 / 1 % but with
bidmc_11's within-2 collapsing from 94 % to 47 %.

Their headline BIDMC figure is MAE **2.0** across 53 recordings, against the
settled figure in [RESULTS.md](RESULTS.md)
here on 12 — not directly comparable, but no reason to think the pipeline as a
whole is behind.

## Clipped and disconnected signal: Karlen's adjacency rule

[Karlen] rejects an up-slope that is adjacent to a **flat** line, on either side.
A flat line is a segment of *exactly zero* amplitude, which in a 12-bit ADC
stream is not a physiological state — it is saturation, clipping, or a sensor
that has stopped reporting. An up-slope with dead signal on one side of it is
not a pulse, whatever its shape.

The rule is applied on **both** sides. The line that follows an up-slope is known
when the up-slope is judged; the line that precedes it is carried forward
explicitly, because an up-slope is judged one line after it is found.

**What it costs, measured over the 68-recording cohort.** Five recordings are
affected. Their flat-sample fractions are 52 %, 43 %, 21 %, 15 % and 8 %, against
a cohort median of **4.1 %** — four of the five are among the thirteen most
corrupted recordings in the set. The two cleanest recordings tested, at 1.0 % and
0.8 % flat, take **zero** rejections: the rule does not fire on intact signal at
all.

The visible effect is 7 declined windows out of 408 across the cohort. On the
worst case — a recording that is 21.8 % flat and contains a single **10.5-second
stretch of constant signal** — one window flips from reported to declined, and
that recording now reports no respiratory rate at all. Reporting one from it was
the defect; declining it is the gate working. Two further rates move away from
the independent reference, but both of those references are themselves derived
from the same corrupted recordings — one is 56 % flat with an 18-second constant
stretch — so agreement with them is not evidence of correctness in either
direction.

## The beat detector is a runtime choice too

Both detectors are compiled in and linked; one is selected when a recording
starts. It used to be a build-time macro, with each detector file wrapping its
whole body in an `#if` so the unselected one produced no symbols.

**Why it changed: which detector is better depends on the patient**, and the
patient is already a runtime knob. Measured against an independent reference on
the neonatal pair — a Butterworth + `find_peaks` detector written for the
purpose, which recovers 95–97 % of the beats implied by the PPG's own cardiac
spectrum on a signal with no flat segments and a 32 ms IBI IQR:

| neonatal, raw intervals, **before any repair** | reference | TERMA | IMS |
|:---|---:|---:|---:|
| neonatal_mimic_data1 heart rate | 146.7 | **133.9 (−8.7 %)** | 145.2 (−1.0 %) |
| neonatal_mimic_data2 heart rate | 141.0 | **125.5 (−11.0 %)** | 142.9 (+1.3 %) |
| neonatal_mimic_data1 SDNN / RMSSD | 98 / 121 | **191 / 236** | 104 / 114 |
| neonatal_mimic_data2 SDNN / RMSSD | 111 / 151 | **211 / 296** | 108 / 138 |
| sensitivity | — | 88–89 % | **94–96 %** |

The beats TERMA misses at neonatal rates arrive as **doubled intervals**, which
is why its rate reads low and its variability measures roughly double. On adults
the ranking reverses, because IMS over-detects there — the measured comparison is
in [`RESULTS.md`](RESULTS.md). One compile-time choice cannot serve both, and
shipping two binaries is what the patient-type knob exists to avoid.

`sanitize_ibi()` already absorbed most of the damage, which is why this never
showed up in the reported rate: HR reads 150 and 144 either way. The tell was
`HRV_n` — TERMA reached only 293 of 300 intervals on one recording, discarding
beats to stay honest, and the reported SDNN still differed by 40 %.

### How it is wired

Each detector exposes **prefixed** entry points — `terma_fiducial_init` /
`terma_fiducial_process_sample`, `ims_fiducial_init` /
`ims_fiducial_process_sample` — and keeps everything else file-scope, so the two
link together with no collision. `ppg_fiducial.c` holds the dispatch table and
presents the unchanged `fiducial_init()` / `fiducial_process_sample()` contract:

```c
static const struct_fiducial_entry g_fiducial [FIDUCIAL_COUNT] = {
    { "terma", terma_fiducial_init, terma_fiducial_process_sample },
    { "ims",   ims_fiducial_init,   ims_fiducial_process_sample   },
};
static const struct_fiducial_entry *gps_active = &g_fiducial[FIDUCIAL_TERMA];
```

The active row is held as a **pointer, not an index**, so the per-sample path is
one indirect call with no bounds test — it runs at the full sampling rate.

**Selection happens once, before any sample is seen, and cannot change
mid-recording.** A detector carries filter history, running means and block
bookkeeping that would be meaningless if swapped mid-stream.

The patient-type table carries the choice, exactly like the bands and the window
sizing; `-d terma|ims` overrides it. The override exists for **comparison** —
it is how the control arm in [`RESULTS.md`](RESULTS.md) was run — not for
production use.

```
  -s   <subject>              patient type: neonate | child | adult (default adult)
         neonate   RR 22-66 /min, HR  90-181, window 512,  segment 128, detector ims
         child     RR 11-53 /min, HR  43-156, window 1024, segment 256, detector terma
         adult     RR  4-30 /min, HR  43-104, window 1024, segment 512, detector terma
  -d   <detector>             terma | ims -- override the patient type's choice
```

The start-up banner states which detector ran and why, so a log can never be
ambiguous about it:

```
Patient type: NEONATE (0-12 months) (-s neonate)   beat detector: ims (from patient type)
Patient type: ADULT (over 18 years) (-s adult)     beat detector: ims (-d override)
```

An out-of-range enum reaching `fiducial_init()` falls back to TERMA **and says
so** — it runs before any sample, so a silent fallback would be
indistinguishable from a correct run for the whole recording.

**Cost:** both detectors' state is always resident. TERMA's history buffers
dominate at about 13 KB; IMS's state is a few hundred bytes.

**Verified:** all 12 adult recordings are **byte-identical** to the previous
compile-time build — the dispatch layer changed nothing on the path that did not
switch. ASan/UBSan clean across all 6 patient × detector combinations.

> **This rests on two recordings with no ground truth**, judged against a
> reference detector written for the purpose. The raw-interval evidence is
> strong and consistent, but the neonate → IMS mapping is one row in
> `g_subject[]` that a wider neonatal dataset could reverse.

## Beat-window scaling at neonatal heart rates

**The problem.** The reported HR was right, but right only *after repair*. The
detector found 68–77 % of the beats; `sanitize_ibi()` recognised the resulting
≈ 2 × NN intervals and divided them, restoring the **rate** while leaving the
individual beat timings gone. Neonatal HRV RMSSD and pNN50 therefore rested on
reconstructed intervals.

The expected count was measured independently of any detector, from the dominant
peak of the raw PPG's own cardiac spectrum (0.7–5 Hz Welch): 152.0 and
148.3 /min, i.e. 3012 and 2862 beats.

**The mechanism.** Elgendi defines `W2` semantically — *the one-beat duration* —
and publishes 667 ms, the value that duration takes for his cohort at ≈ 90 /min.
Held constant in milliseconds it spans a different fraction of a cardiac cycle at
every heart rate: one beat at 90 /min, but **1.6 beats** at a neonatal 146 /min.
Once the beat-level moving average spans more than one beat it stops tracking
the beat it exists to track, adjacent blocks of interest merge, and a merged
block yields one peak where there were two.

**The rule — carry the definition, not the number.** `fiducial_init()` takes
the subject's expected heart-rate band, and `tm_scale_window()` re-expresses W2
for it: the window keeps the same position inside the subject's beat-duration
range that Elgendi's value occupies inside the reference range it was measured
in. W1 follows at his published 111:667 ratio. Give the detector back the
reference band and it returns 111/667 exactly, so **the adult build is
byte-identical by construction** — verified across all 12 recordings.

| | before | after |
|:---|---:|---:|
| W1 / W2, adult (43–104 /min) | 111 / 667 ms | 111 / 667 ms (unchanged) |
| W1 / W2, neonate (90–181 /min) | 111 / 667 ms | **61 / 368 ms** |
| beats found, recording 1 | 2314 (77 %) | **2654 (88 %)** |
| beats found, recording 2 | 1958 (68 %) | **2420 (85 %)** |
| RR reports valid | 140/142, 137/138 | 138/142, 136/138 |

The beat counts in that table are the **TERMA** arm, measured before the quality
gate existed — which is why almost every window still
reported a rate. `-s neonate` now dispatches to IMS and the gate declines about
half the windows by design; the shipped figures are in [`RESULTS.md`](RESULTS.md)
and are not comparable with this row.

**Where the remaining 12–15 % goes, and why it is not chased.** Sweeping W2 on
the *annotated adult* recordings — the only ones with ECG ground truth — shows
detection F1 rising as W2 shortens (98.7 at 667 ms, 99.4 at 300 ms) with
precision holding at 99.3–99.6 %, so the extra beats a shorter window finds are
real rather than false. The same sweep shows respiratory rate moving the *other
way* (MAE 0.85 at 667 ms, 0.93 at 300 ms, 0.74 at 800 ms): RR prefers fewer,
cleaner beats. Both directions are therefore available, and picking a point on
that curve would be choosing a value from the only annotated cohort we have —
exactly the dataset tuning this document refuses elsewhere. Elgendi's published
value stands, re-expressed per subject and nothing more.

**Karlen IMS corroborates the mechanism.** It has no constant one-beat window — it
merges segments incrementally, so the beat sets its own scale — and it finds
**2880 and 2758** beats against the same expectation, i.e. 96 % both times, with
no rate-dependent parameter at all. On adults the ranking reverses — see [`RESULTS.md`](RESULTS.md) — which is what
a constant beat window predicts: the deficit is specific to the high-rate case.

## The patient type is a knob, not a build

`-s neonate | child | adult`, default `adult`. One binary serves every patient
type, the way a bedside monitor has a patient-type setting rather than three
part numbers. There is no `SUBJECT_CATEGORY` macro and no per-patient make
target any more.

```
  -s   <subject>              patient type: neonate | child | adult (default adult)
         neonate   NEONATE (0-12 months)  RR 22-66 /min, HR  90-181, window 512,  segment 128
         child     CHILD (1-18 years)     RR 11-53 /min, HR  43-156, window 1024, segment 256
         adult     ADULT (over 18 years)  RR  4-30 /min, HR  43-104, window 1024, segment 512
```

**Where the knob is wired.** `ppg_main.c` holds one table, `g_subject[]`, with a
row per position — bands, expected heart rate, window, segment, slide. It is the
only place in the program that knows patient categories exist. One row goes to
`ppg_analysis_init()` and its heart-rate band to `fiducial_init()`; neither can
ask which category is active, because neither is told. Reading the selection
from a device setting instead of a command line is an edit to that one block.

Every value in the table is cited in `filter_bands.h`, which now defines all
three categories unconditionally rather than `#if`-selecting one — a runtime
choice needs every position present in the binary.

**Storage is constant, the active length travels.** The surrogate grid buffer is
`INTP_MAVG_BUFF_SIZE = RR_MAX_WINDOW_PTS * 2` = **2048 grid points**, and it does
not change shape when the knob moves. Analysis arrays are dimensioned by
`RR_MAX_WINDOW_PTS` / `RR_MAX_WELCH_SEG`; the active `cfg_rr_window_pts`,
`cfg_rr_welch_seg`, `cfg_rr_welch_overlap` and `cfg_rr_slide_pts` live in the
context and are what every loop actually counts to. `welch_psd()` takes the
window, segment and overlap as arguments. No allocation happens when the
selection changes, and the program never allocates at all: there is no `malloc`,
`calloc` or `free` anywhere in `src/`, so it runs with no heap. That is a
property worth checking rather than trusting:

```sh
grep -nE '\b(malloc|calloc|realloc|free)[[:space:]]*\(' src/*.c   # returns nothing
```

A build-time check makes a stale limit a compile error rather than an overrun:

```c
#if (ADULT_WINDOW_PTS > RR_MAX_WINDOW_PTS) || ...
#error "a category's window exceeds RR_MAX_WINDOW_PTS -- raise it"
#endif
```

**Cost, measured.** 41 512 bytes of context and 126.3 KB data+bss for the single
binary, against 29 208 / 86.2 KB for what used to be a neonate-only build — about
**12 KB**, the price of not shipping three firmware images.

**Two invariants worth keeping.**

- *No algorithm-specific constant in a shared header.* Elgendi's W1, W2 and the
  reference band they were measured in live in `ppg_fiducial_elgendi_terma.c`; a
  detector added later declares its own and exposes none of them. The generic
  side of the interface carries physiology and nothing about how a detector uses
  it.
- *Refuse, never default.* An unknown `-s` value is rejected outright, and
  `ppg_analysis_init()` refuses a NULL, incoherent or oversized configuration
  rather than substituting something workable. Analysing a neonate with an adult
  band is the largest single source of respiratory-rate error and it leaves no
  trace in the output, so it must fail where someone can see it.

**Verified behaviour-neutral.** All 14 recordings, run from the single binary
with `-s adult` and `-s neonate`, are **byte-identical** to the results from the
previous compile-time-selected builds. 2 detectors x clean strict build,
ASan/UBSan clean including all three knob positions on the same recording.


## Build and robustness

| check | result |
|:---|:---|
| single build, no options | **0 warnings**, `-std=c99 -Wall -Wextra -Wpedantic -Werror -Wshadow` |
| whole-folder compile, no makefile | links, 0 warnings, both detectors resident |
| 6 runtime combinations (terma/ims × adult/neonate/child) | ASan + UBSan clean |
| `-s bogus`, `-d bogus` | refused with the valid names, exit 255 |
| `-r 0`, `-r -5`, `-nu 0` | exit 255 with a diagnostic (previously NaN coefficients, exit 0) |
| ASan + UBSan over a full recording | 0 complaints |
| second compiler (clang), same strict flags | 0 warnings |
| `cppcheck` warning/performance/portability | 0 findings |
| `gcc -fanalyzer` | 1 finding, analysed and rejected — see below |
| identical output at `-O0`, `-O2`, `-O3` | byte-identical `RR_Data.csv` |

**The one analyzer finding, and why it is not a real one.** `gcc -fanalyzer`
reports a possible use of an uninitialised `lx[jj]` in the Theil-Sen fit. It is a
false positive: one loop fills `lx[0 .. n-1]` from the in-band bins and the next
reads exactly `lx[0 .. n-1]`, but the two are separate loops sharing a computed
`n`, which the analyser cannot connect. The bound on `n` is independently safe —
`k_hi` is clamped to `seg/2 - 1` and `lx` holds `RR_MAX_WELCH_SEG`, giving at
least 2x headroom. Left in place rather than silenced, so the next person sees
the same report and the same reasoning.

**One bound was added rather than assumed.** The Theil-Sen background fit stores
one slope per *pair* of in-band bins, so it fills as `n(n-1)/2` while its array
scales with `n`. Every shipped band leaves about 19x headroom — the worst case is
105 pairs against a capacity of 2048 — but the `k_hi` clamp alone permits an `n`
that would need far more. Widening a band must degrade the fit rather than
overrun the array, so the fill is now explicitly bounded. No shipped
configuration reaches it, and the 14 reference recordings are byte-identical
across the change.

## Known weak point

`bidmc_05` breathes at ~6 /min. At that rate **four harmonics fall inside the
4–30 band** (12, 18, 24, 30), which breaks both estimators at once: the spectrum
can lock onto a harmonic, and the same harmonics add spurious zero-crossings to
the breath counter.

It is still the cohort's weakest recording, but it is no longer badly wrong. As
shipped it reports **MAE 1.02** with **95 %** of its reported windows within
2 /min, over **38 of 57** windows — the rest declined rather than guessed. The
harmonic guard is what closed the gap, and it now works by *prevention*: only 2
windows need the `TD_HARM` breath-count rescue and 1 the sub-harmonic rescue,
where before the guard the rescue was carrying the recording.

A peak-based breath counter was tried as an alternative to the zero-crossing
count. It improves the count on the other eleven recordings but makes this one
worse — trading the cohort's hardest case for a small gain on its easiest — so it
is not shipped.

## The category band is a prior, not a gate

Found by sweeping 68 short MIMIC-III Waveform recordings (7441 samples each,
one of them 7442 = 59.5 s at 125 Hz, cohort median HR 154 → neonatal).

`sanitize_ibi()` rejected any interval outside the declared category's
heart-rate band. That band is a **Fleming 2011 centile range** — it describes
what is *normal* for an age, not what is *possible*. Used as a hard gate it
fails precisely on the patients who matter:

> a neonate in SVT at 197 /min produces 304 ms intervals. The neonate ceiling of
> 181 /min puts the floor at 331 ms, so **every real beat** was rejected as
> `EXTREME` and replaced by the running mean — and because the gate runs before
> calibration, the running mean never saw a true interval either.

```
sanitize_ibi(): *** EXTREME IBI: 288 ms (limits: 331-666), replacing with 498
```

The reported rate settled at about **half** the real one, with no warning.
**Beat detection was correct throughout**: on exactly these records our beat
counts match an independent count to 0.96–0.99. The rate was destroyed *after*
detection. 3553 intervals were overwritten this way across 65 of the 68.

By construction 1 % of *healthy* neonates lie above a 99th centile. The same
shape applies to `CHILD` (156) and `ADULT` (104) — an adult in SVT would read
half — though there is no data here to confirm those.

### The rule: sustained evidence overrules the prior

Widening the band was rejected: the prior does real work, rejecting **1025**
isolated false peaks across the 12 annotated adults (a lone 432 ms interval in
an adult *is* an artefact). So the roles were split instead:

| role | source | can it be overruled? |
|:---|:---|:---|
| RR/HR **search** band + category warning | Fleming centiles, per category | yes — it is a prior |
| **plausibility envelope**, the only hard limit | 20–320 /min, same for all | never |

The envelope is the widest rate a human heart is documented to reach, so the
only thing it can exclude is the impossible — fast end from neonatal SVT
(Qi *et al.* 2026, *Front. Pediatr.* 13:1694215, "220 to 320 beats per
minute"), slow end from ventricular escape rhythm in complete AV block
(StatPearls NBK459147, 20–40 /min).

The prior yields only to evidence an artefact cannot fake: **10 consecutive
out-of-category intervals that agree with each other to ±60 %**. A single
spurious peak breaks the run; a genuinely tachycardic patient rebuilds it every
beat. **Both numbers are the ones the adaptive tier already used** — the
calibration length and the outlier tolerance — so the override can be neither
quicker nor more credulous than ordinary start-up, and **no new tuning constant
enters the codebase**. On re-anchor the running mean is reseeded from the run's
median (the old one is known-corrupt, built from substitutions) and the
transition is logged loudly, because it means the declared category disagrees
with the patient.

### Results

| | before | after |
|:---|---:|---:|
| true HR **above** the 181 ceiling (n=11) | bias −49, MAE **49** bpm | bias −7.0, MAE **7.0** bpm |
| true HR **inside** 90–181 (n=23) | bias +3.3, MAE 4.8 bpm | bias +3.2, MAE **4.6** bpm, 0 re-anchors |
| 12 annotated adults + 2 neonatal | — | **byte-identical**, no re-anchor forms |

Scored only on the 34 of 68 where three independent HR estimators agree within
8 bpm; the rest are reported unresolved rather than counted against us.

**These figures predate the dicrotic finding above and are not corrected for
it.** Whether any of the 5 doubled recordings falls inside the scored 34 is not
known, because the membership of that subset was not recorded alongside the
values. Read them as what they were measured to be, not as a bound.

**What that reference is, and how it fails.** No annotation exists for this
cohort, so the reference is a construction of this work: the peak of a Welch
spectrum of the band-passed signal, the median inter-peak interval, and the
FIRST autocorrelation peak. Each detail is there because the obvious version was
measured wrong — on the raw signal the low-frequency artefact leaks into the
edge bin, and the GLOBAL autocorrelation maximum locks onto 2–4 beat lags.

A dicrotic notch defeats all three of those estimators at once: the notch is a
second peak to count, it puts the first autocorrelation peak at the half-period,
and it can make the second harmonic the strongest bin. Three estimators that
fail the same way are not three estimators, so agreement is evidence, not proof.

**And this analyser reads high on the same kind of recording.** The spectral
evidence is the durable part: on those recordings the cardiac spectrum carries
2-13x more power at half the reported rate than at the reported rate. The
amplitude statistic once quoted alongside it -- a median small/large ratio of
0.34-0.57 over successive pairs -- is a min/max ratio, so it is <= 1 by
construction and cannot say WHICH of the pair is the systolic peak; measured
with the pairs labelled, the second peak is not reliably the smaller. See
"Dicrotic doubling — an open defect" below, which also shows that most of this
is a declared-subject mismatch rather than a notch.

The per-record reference values are retained with the sweep rather than
re-derived, because a reference rebuilt to a slightly different recipe is a
different reference.

## Ectopic beats do not reach the respiratory surrogates

[CHARLTON] eliminates features derived from ectopic beats **before** extracting
the respiratory signals, citing [MATEO]. Until this change every beat's vertices
entered the AM, BW and FM surrogates regardless of what `sanitize_ibi()` had
just decided about it.

**The distinction that makes this safe is WHICH repair.** This program repairs an
interval for two different reasons, and only one of them says anything about the
beat:

| repair | what it means | vertex |
|:---|:---|:---|
| `IBI_REPAIR_LOCAL` | the interval disagreed with the running mean of its own neighbours — a **local-deviation** test, which is what [MATEO] and [CHARLTON] describe | **excluded** |
| `IBI_REPAIR_SUBSTITUTED` | the interval fell outside the **declared category's band** | kept |
| `IBI_REPAIR_SPLIT` | a beat was missed and the gap divided; **this beat is real** | kept |

**Why the band test is not used, measured.** It is a statement about the
declaration, not about the beat — and the declaration is exactly what is wrong
when it is wrong. Excluding vertices on it costs the annotated adults settled RR
MAE **0.42 → 0.48** and within-2 99 % → 98 %. Excluding them on the local test
costs those figures **nothing** — 0.42, 99 %, the same 570 settled rows.

| gate | adult RR MAE | within-2 | settled rows | neonatal RR windows | 2nd recording's IQR |
|:---|---:|---:|---:|---:|---:|
| none | 0.42 | 99 % | 570 | 71 / 68 | 7.48 |
| **local only — shipped** | **0.42** | **99 %** | **570** | **73 / 71** | **3.12** |
| band only | 0.48 | 98 % | 558 | 88 / 76 | 3.72 |
| band + local | 0.47 | 98 % | 557 | 79 / 82 | 2.22 |

**The gate is not conditioned on `-s`.** What separates the noisier recordings
from the clean ones is the **repair rate**, not the patient type, so on a noisy
adult wearable or a clean neonatal trace the category would point the wrong way.
The discriminator is the band test versus the local test, which applies to every
subject alike.

**Deleting a vertex is not deleting a sample.** [MATEO] argues against deletion
for HRV spectra because it shortens the series. Here the surrogates are
resampled **linearly between consecutive vertices** onto a uniform grid, so the
grid interpolates across the gap: in this architecture deletion *is*
interpolation, which is the correction [MATEO] prefers, and it comes for free
from resampling the program already does.

`RR_GATE_ON_BAND`, `RR_GATE_ON_LOCAL` and `RR_EXCLUDE_SPLIT_VERTICES` are
build-time overridable so the table above can be reproduced rather than believed.

### Dicrotic doubling — an open defect

**With one subject category declared for the whole cohort, this analyser
over-reads the heart rate on 11 of the 58 short recordings that have a resolved
reference. Declaring the category each recording's rate actually belongs to
removes 9 of those 11 and introduces none, leaving 2.** It is stated here
because a rate that is exactly twice the truth, sitting inside the expected
band, is the most dangerous thing this program can report.

**The dominant cause is a declared-subject mismatch, not waveform morphology.**
The cohort sweep declares `-s neonate` for all 68 recordings, expected HR
90–181 /min. Ten of the 58 with a resolved reference have a reference rate
*outside* that band — 54 to 84 /min — and every one falls inside the adult band
of 43–104. Told to expect a rate the subject does not have, the detector rejects
the true interval and locks onto a periodicity that fits the declared band.

Re-running each recording under the category its reference falls in reassigns 12
of the 68, all to `adult`:

| recording | reference | `-s neonate` | re-run | |
|:---|---:|---:|---:|:---|
| `80057524_0001_114` | 56 | 136 | 68 | doubling removed |
| `80222656_0004_90` | 69 | 144 | 66 | doubling removed |
| `81002096_0001_53` | 73 | 154 | 60 | doubling removed |
| `81250824_0005_44` | 83 | 170 | 51 | doubling removed |
| `81396664_0001_66` | 78 | 166 | 55 | doubling removed |
| `82342224_0005_48` | 81 | 150 | 74 | doubling removed |
| `83268087_0001_6` | 62 | 144 | 56 | doubling removed |
| `83988903_0006_57` | 91 | 154 | 74 | doubling removed |
| `84248019_0005_9` | 54 | 150 | 59 | doubling removed |
| `82552643_0001_37` | 78 | 117 | 78 | closer |
| `83404654_0001_2` | 84 | 108 | 63 | closer |
| `82924339_0006_8` | 90 | 91 | 90 | closer |

**Nine doublings removed, none introduced.** MAE across those twelve falls from
**65.4 to 11.6 /min**.

**Read that with its caveat.** This cohort ships no patient metadata, so the
category can only be inferred from the reference rate — which is circular for HR
accuracy, because the band is chosen to contain the answer. It is not evidence
that the analyser is accurate. What it *is* evidence for is the attribution:
a wrong `-s`, not the waveform, produced nine of the eleven over-readings. The
fixed-category sweep therefore remains the regression baseline, and the
per-subject pass is reported separately.

**What remains after that is two recordings**, both of which stay in the
neonatal band and still read 2.05 × their reference: `80666640_0014_146`
(118 → 242) and `81741333_0001_34` (122 → 250).

**And the second peak does not look like a dicrotic wave.** Labelling alternate
peaks systolic and dicrotic on the recordings that read high, and measuring
against the schematic below:

| property | a dicrotic wave | measured |
|:---|:---|:---|
| position within the cycle | 30–40 % | **50–53 %** (p10 35–46, p90 60–65) |
| trough before it, as % of the systolic rise above the foot | 40–70 % up | **−7 %** — it falls to or below baseline |
| absolute level against the systolic peak | clearly lower | **equal**; higher on 43 % of beats |
| the two interleaved intervals | short–long | **differ by 4–8 %** — near-uniform |

A dicrotic notch is by definition a shallow inflection on the diastolic decay;
these troughs return fully to baseline at the midpoint of the cycle and reach
the amplitude of the peak they follow. Two of them have almost no pulsatile
signal at all — perfusion index **4.1 %** and **6.3 %** against a cohort median
of 40 % — so the detector is marking beats on a trace that barely contains a
pulse. The systolic/dicrotic labelling assumes the reference rate is correct;
the perfusion index and the band arithmetic above do not.

> **This will be addressed in an upcoming release.** It is not closed in this
> one. What that costs a reader of the output, and what a fix has to do, are set
> out below.

**What IS done about it, and it is what the source algorithms prescribe.** Both
detectors carry their authors' own defence against counting a dicrotic wave as a
beat, and both are implemented:

| detector | the author's mechanism | here |
|:---|:---|:---|
| [ELGENDI] | squaring, which *"emphasises the large differences resulting from the systolic wave, whilst suppressing the small differences arising from the diastolic wave"* | implemented |
| [ELGENDI] | `THR2 = W1`, which *"rejects the blocks that contain diastolic wave and noise"* so that *"the accepted blocks will contain systolic peaks only"* | implemented |
| [KARLEN] | adaptive amplitude thresholds, calculated *"to prevent misdetection of the dicrotic notch and artifacts as individual pulses"* | implemented, including Algorithm 2's neighbour condition |

So the answer to "is nothing being done" is no: the published defences are in
place. **What the measurements show is that they are not sufficient at this
operating point**, and both papers say why in their own words. [KARLEN]'s
discussion states that the validation set *"only contained a limited number of
PPG morphologies. For example, only few beats with dicrotic notches were
present."* [ELGENDI]'s cohort is heat-stressed **adults**, mean age 34.7, resting
HR 76, with W1 and W2 brute-forced on it. The recordings that fail here are
bradycardic patients at 69–85 /min declared as neonates — a combination neither
author validated, and one Karlen flags in advance as untested for exactly this.

[CHARLTON] meets the same wall independently: technique X_B10 was **excluded**
from that study because it *"variably detected the end of the PPG pulse as
either the time of the minimum immediately before the diastolic peak, or the
time of the diastolic peak, causing inaccuracies."* That minimum is the dicrotic
notch. The ambiguity is a known hard problem in the field, not an oversight
here.

**What the dicrotic wave is.** One cardiac cycle of a peripheral pulse contains
**two** rises, not one:

```
    foot ──▶ SYSTOLIC PEAK ──▶ dicrotic notch ──▶ DICROTIC PEAK ──▶ next foot
             (ejection)        (valve closure)    (reflected wave)
```

The systolic peak is ejection. The dicrotic notch is the incisura at aortic
valve closure, and the dicrotic peak that follows it is the wave reflected back
from the periphery. Both are **normal features of every peripheral PPG**. They
arrive from the ADC in every recording, they cannot be filtered away without
taking the pulse with them, and they are *most* pronounced in young compliant
vessels — that is, in the neonatal population this program is aimed at. A signal
without a dicrotic wave is the exception, not the rule, and an algorithm that
requires one is not an algorithm for this problem.

A beat is one *systolic* peak. Where the dicrotic peak is large enough to look
like a pulse, the detector emits it as a beat and the reported rate doubles. Doubling a bradycardic
neonate at 75 /min gives 150 /min, which is *inside* the neonate band of
90–181, so no re-anchor forms, no warning prints, and the plausibility envelope
sees nothing wrong. It is silent, and it looks healthy.

**The evidence.** Two independent measurements agree, on the same 5 recordings
(the sample on which the mechanism was confirmed beat-by-beat):

| | doubled (n = 5) | undisputed (n = 4) |
|:---|---:|---:|
| median small/large amplitude of successive pulses | **0.34 – 0.57** | 0.90 – 0.95 |
| spectral power at half the reported rate ÷ at the reported rate | **2 – 13 ×** | ≈ 0 |

The amplitude signature is the alternation itself: a doubled train runs large,
small, large, small, because the notch rise is always smaller than the systolic
rise it follows.

**Why the interval stream cannot fix it.** A detector reporting two beats per
cycle emits a steady train of half-length intervals, which is arithmetically
identical to a heart beating twice as fast. Any rule written on intervals alone
must either accept both or reject both.

A partial repair is worse than none: it converts a rate that is cleanly wrong by
a factor of two into one that is wrong by an unpredictable amount, which no
downstream check can recognise. So the tree ships **unrepaired and documented**
rather than half-repaired.

**Why vetoing beats cannot work here, measured.** Every amplitude rule above
assumes the emitted train alternates large, small, large, small. Instrumenting
the detector shows it does not. On one doubled recording the up-slope amplitudes
IMS emits run from **6 to 2492 within a single 59.5 s recording** — a 415-fold
spread, quartiles 91 / 207 / 504. The cohort-level statistic that looked like
clean alternation (a median successive-pair ratio of 0.34) is equally consistent
with amplitudes that are simply erratic, which is what they are. There is no
stable pulse amplitude in that stream to compare a candidate against, so no
threshold on it can be made to work.

**What a real fix needs.** The dicrotic wave has to be *modelled*, not vetoed:
the detector must identify the notch and the dicrotic peak as named fiducials in
their own right, so that neither can ever be mistaken for a systolic peak. The
discriminator that does this is **timing**, not amplitude — the interval from a
candidate valley to the peak that follows it separates a true diastolic foot
from a dicrotic notch, because the systolic upstroke is short and steep while
the reflected wave rises later and more slowly.

### The one measurement that does recover the true rate

Every attempt above fails at the same point, and the last two locate it
precisely: the correction needs to know the true cardiac period, and every
source tried either derives it from the corrupted beat stream or has too little
signal to resolve it.

Given enough signal, it resolves. The same envelope sweep, floored above the
respiratory ceiling and given the **whole recording** instead of the detector's
8.2-second ring:

| | true | 8.2 s ring | 20 s | whole recording |
|:---|---:|---:|---:|---:|
| doubled recording 1 | 69 | 104 | 87 | **70** |
| doubled recording 2 | 73 | 75 | 72 | **76** |
| doubled recording 3 | 83 | 76 | **82** | 110 |
| doubled recording 4 | 78 | 241 | 240 | **77** |
| doubled recording 5 | 81 | 81 | 80 | **83** |
| control 1 | 163 | 164 | 164 | 163 |
| control 2 | 147 | 149 | 150 | 149 |

**Four of the five land within 1–3 /min of the truth, and neither control
moves** — against two of five at the ring length the detector actually has. The
method is sound; what defeats it is the buffer, and the buffer is a property of
where the estimate is computed rather than of the estimate.

That is the difference between this and the attempts before it. It is not
another discriminator to threshold: it is an independent measurement of the
quantity every one of those attempts was missing.

**It has since been built and measured**, on its own 65.5-second buffer at the
respiratory interpolator's grid rate, held in the dispatch layer so that it
belongs to neither detector and the fiducial contract is untouched. As an
estimator it works: 6 of 7 test recordings within 3 /min, both controls exact,
and stable across a 19-minute recording. What is *not* solved is what to do with
it — see the last two rows of the table above. Driving a per-candidate veto with
it gives the best cohort figures yet and damages correct recordings; gating that
veto behind a per-recording classifier damages nothing and under-corrects. The
measurement is sound; the correction it should drive is still open.

Note what the earlier table row does and does not refute: a *bare threshold* on
that interval fails, so the timing information is not separable one rise at a
time, in isolation. That is an argument for the modelled form and against the
shortcut — the notch has to be located relative to the systolic peak that
precedes it, within a cycle the detector has already committed to, rather than
scored as a free-standing candidate. It also means the modelled form is
unproven, not merely unimplemented. That requires a derivative and
inflection-point stage ahead of the beat decision, which neither Elgendi TERMA
nor Karlen IMS provides, and it is a design change rather than a guard.

### Literature conformance — two deviations found by re-reading the sources

Both source detectors *do* address the dicrotic wave, and re-reading them turned
up two places where this implementation departs from what they specify. Neither
explains the doubling — both were implemented and measured — but they are
recorded because fidelity to a cited source is a claim this project makes.

| deviation | what the paper says | measured effect |
|:---|:---|:---|
| **W1 is scaled with the cycle.** `terma_fiducial_init()` derives W1 proportionally from W2, giving 111 ms → 82 → **61 ms** across the three categories | [ELGENDI] anchors W1 to *"the systolic duration in PPG, which is 100 ± 20 ms in healthy adults"* — a physiological duration, not a fraction of the cycle. `THR2 = W1` is his dicrotic rejector, so shrinking W1 below the 40–150 ms dicrotic rise disables it | **Does not fix it.** Fixed at 111 ms, the five read 142/113/120/99/136; floored at 80 ms, 126/115/118/126/122 — against a true 69/73/83/78/81. The floor also **breaks a control**, 147 → 112, so it is rejected |
| **Threshold adaptation ignores the neighbours.** `ims_adapt()` runs on every classified up-slope | [KARLEN] Algorithm 2 line 4 adapts only when `α_z > 0 & α_{z−1} ≠ 0 & α_{z+1} ≠ 0` — neither neighbour flat | **Immaterial.** One recording moves, 180 → 172; the other six are unchanged |

**Why neither explains it.** Both papers state their own limits, and both land on
this cohort. [KARLEN]'s discussion says of the validation set: *"it only
contained a limited number of PPG morphologies. For example, only few beats with
dicrotic notches were present."* [ELGENDI]'s cohort is heat-stressed **adults**,
mean age 34.7, resting HR 76, with W1 and W2 brute-forced on it. The failing
recordings here are bradycardic patients at 69–85 /min declared as neonates.
**That is operating outside the envelope both authors validated, not misreading
them** — and Karlen says in advance that this is where the dicrotic defence is
untested.

### Known limit — do not attack this from the interval stream

A detector reporting two beats per cycle emits a steady train of half-length
intervals, which is *arithmetically identical* to a heart beating twice as
fast. Three of the 68 re-anchor to ≈2× their reference for that reason. Four
candidate discriminators were measured and **all overlap**:

| discriminator | harmonic cases | genuine cases |
|:---|:---|:---|
| pulse-amplitude quality flag | 76–100 % | 75–100 % |
| median interval of the run | 240–256 ms | 272–320 ms |
| run median ÷ established mean | 0.00–0.52 | 0.00–0.87 |
| share of in-band intervals | 1–44 % | 2–66 % |

The distinguishing information is not in the intervals — it is in the raw
signal's cardiac spectrum, where a doubled train shows its fundamental. A
spectral cross-check at the moment of re-anchor is the way to settle it, and is
deliberately not done here.

### Two files in that set are not ADC counts

two of the 68 (`short_mimic_data1`, `short_mimic_data2`) hold floats in [0,1]. At `-nu 1` they
truncate to zero and yield **0 beats**; `-nu 10` leaves ~9 quantisation levels
and still fails. At `-nu 4095` — the same 12-bit range as the other 66 — both
produce beats, and `short_mimic_data1` reads 120 bpm against an independent 119. **The
`-nu` scale belongs to the file, not to the run.**

### Sources introduced by this work

Three, all now cited at the point of use. Archived copies and verbatim quotes:
`REFERENCE_NOTES_heart_rate_extremes.md`.

| tag | source | used for | local copy |
|:---|:---|:---|:---|
| `[QI]` | Qi, Yu & Wang, *Front. Pediatr.* 13:1694215, 2026, doi:10.3389/fped.2025.1694215 | `HR_PLAUSIBLE_MAX_BPM` 320 | **PDF archived** |
| `[SATO]` | Sato *et al.*, *Anesth. Prog.* 68(4):230–234, 2021, doi:10.2344/anpr-68-03-09 | `HR_PLAUSIBLE_MIN_BPM` 20 | quote only — PMC blocks retrieval |
| `[GOERTZEL]` | Goertzel, *Amer. Math. Monthly* 65(1):34–35, 1958, doi:10.2307/2310304 | the spectral cross-check | quote only — paywalled |

**The envelope bounds the PULSE rate, not the atrial rate.** `[QI]` also reports
neonatal atrial flutter at 300–500 /min, but that is atrial and reaches the
periphery divided by AV conduction (commonly 2:1). A PPG sees the ventricular
rate, so the 320 /min AVRT figure is the correct ceiling and the flutter figure
is not.

**A citation error found and corrected (2026-08-14).** The slow end was first
attributed to StatPearls chapter NBK459147 with the authors given as
Kashou/Goyal/Nguyen/Chhabra. Checking the chapter showed **both were wrong**: it
is by Ahmed, Goyal & Chhabra, and it states no bpm figure at all — only that the
escape rhythm runs at "a regular but slower rate". The 20–40 range had come from
a search-engine summary rather than from the source. It now cites `[SATO]`,
whose wording was read and quoted directly. The value itself did not change; its
provenance did.

### The spectral cross-check, and what it cannot settle

The re-anchor above trusts a run of intervals, and intervals alone cannot tell a
doubled beat train from a genuinely faster heart. `fiducial_period_is_fundamental()`
asks the signal instead: two Goertzel evaluations over the 1024-sample filtered
ring the detector already keeps — band power at the candidate rate against band
power at half of it. No FFT, no new buffer, no threshold.

**Confirmation is required, not assumed.** Every path that cannot decide — ring
not yet full, half the candidate not resolvable in the window — returns 0, and
the caller defers. An earlier version returned "accept" when undecidable, and
the check silently never ran: the re-anchor fires around 3 s in, the ring needs
8.2 s to fill, so *every* case took the abstain path. It reported zero refusals
and looked like it was working.

On deferral the run is **not** reset. Requiring ten fresh beats each time was
measured and costs genuine cases (above-181 MAE 7.0 → 8.3) while rejecting no
doubled train the test can actually see.

**Measured over the 68:** the check confirms 12 of the 13 re-anchors and changes
none of their rates; the reference recordings stay byte-identical because no
re-anchor forms in them at all.

Two of the three records previously called "harmonic re-anchors" turned out to
be **our reading being right and the 3-method reference being wrong** — their
cardiac spectra peak at 238 and 245 bpm against our 242 and 250, while the
reference's `ibi` and `autocorr` estimators had both locked onto the half-rate.
That is a caution about the reference, not about the algorithm.

The third, `short_mimic_data3`, is a genuine doubling and stays **unresolved**. Its
cardiac spectrum has no fundamental to find — the top peaks are 77, 59, 99 and
70 bpm at relative powers 1.00, 0.88, 0.77, 0.69, which is noise. Comparing two
frequencies in that is meaningless, and a stricter rule does not help: requiring
the candidate to be the strongest bin across the whole envelope was implemented
and measured **worse** — it defers genuine cases long enough to spoil them
(`short_mimic_data4` fell 197 → 140 bpm, `short_mimic_data5` 192 → 150) and still
confirmed `short_mimic_data3`.
On a recording with no resolvable cardiac fundamental, no spectral rule can
answer the question, because the signal does not contain the answer.

## HRV carries a quality flag

HRV SDNN/RMSSD/pNN50 need something that distinguishes a value the window
actually supported from one it did not: across the 12 adult recordings,
**0 of 2052 HRV field-values carried a sentinel**, so an unqualified number was
indistinguishable from a validated one.

**The flag is the `-1` sentinel, not a new column**, because the CSV column set
is settled. That is the same convention RRV already uses, so a consumer needs no
new parsing rule.

### The condition underneath

SDNN needs `n >= 3` NN intervals, while RMSSD and pNN50 could otherwise be built
from a **single** adjacent pair. A repaired beat breaks adjacency, so `npair`
can fall far below `n`, and one difference could become an RMSSD
indistinguishable from one backed by hundreds. There are now not two minimums
but one, `HRV_MIN_INTERVALS`, applied to the NN count for SDNN and to the
adjacent-pair count for RMSSD/pNN50. No new value is introduced — it is the 3
that SDNN already used.

| | rows carrying a sentinel |
|:---|---:|
| 12 adult recordings, before | 0 / 684 |
| 12 adult recordings, after | **3 / 684** |
| 68 short MIMIC, before | 62 / 408 (15 %) |
| 68 short MIMIC, after | **86 / 406 (21 %)** |

The effect is concentrated where it matters: on 59.5 s recordings, some 23 more
rows are now marked rather than presented as measurements.

The two cohort denominators differ because of the adjacency rule described under
"Clipped and disconnected signal" above: rejecting an up-slope on the *earlier*
side of a horizontal line, and not only the later one, costs the cohort two
analysis windows outright. Confirmed by disabling that one half of the test,
which returns the cohort to 87 / 408 exactly. The adult figures are unaffected —
the rule belongs to the segmentation detector, which the adult path does not run.

### What the CSV cannot express, and is declared instead

A sentinel can say "too little data"; it cannot say "computed correctly but not
standard-conformant". Since no column may be added, that is declared once at
start-up, in numbers the operator can check:

```
HRV: window 300 beats = 173-418 s at this subject's 43-104 bpm; [TASKFORCE]
     short-term standard is 300 s.
     Values are INDICATIVE, not Task Force conformant.  Fields read -1 when
     fewer than 3 intervals (SDNN) or adjacent pairs (RMSSD, pNN50) survive;
     HRV_n carries the count.
```

Note what that line exposes: at 43 bpm the 300-beat window spans 418 s and *does*
exceed the 5-minute standard; at 104 bpm it spans 173 s and does not. The window
straddles the standard depending on the patient, which a blanket disclaimer would
have hidden.

`HRV_n` is the per-row quality channel that already existed — it carries the NN
count backing each row, and is now documented as such.

### Why the values are qualified rather than suppressed

HRV is qualified rather than suppressed at 125 Hz, and the sources say why.
`[TASKFORCE]` recommends 250–500 Hz, but `[SHAFFER]` (Shaffer & Ginsberg,
*Front. Public Health* 5:258, 2017) qualifies it: "a sampling rate of 125 Hz …
may be sufficient when RSA amplitude is normal", while "a minimum sampling
frequency of 500 Hz may be required … when RSA amplitude is low". So 125 Hz is
a **conditional** limitation, not a disqualification, and blanking the column
would overstate the case in the other direction.

Two larger options remain open and are **not** taken here, both being product
decisions: emitting `-1` on every row while the window
is under 300 s, or making the window time-based at 5 minutes so HRV becomes
duration-conformant (which would cost all HRV on recordings shorter than 5 min,
including every MIMIC file).

**Measured:** RR, RRV and HR outputs are bit-identical — 0 of 8892 non-HRV cells
changed across the 12 adults. Only 3 HRV cells moved.
