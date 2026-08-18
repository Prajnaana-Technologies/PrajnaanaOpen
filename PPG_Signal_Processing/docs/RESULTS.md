# Results

**The single source of truth for every measured figure in this project.** When
the algorithm is tuned, this file is the one that changes; `README.md`,
`USER_GUIDE.md`, `FIDUCIAL_INTERFACE.md` and `DESIGN.md` cite it rather than
restating numbers, so a figure cannot go stale in one document while being
correct in another.

> **Timing convention — all durations are SIGNAL time**, seconds of recorded
> signal consumed, never execution time. Metric definitions — `MAE`, `bias`,
> `within-2`, `coverage`, `Se`, `PPV`, `F1`, `sub-harmonic` — are in
> [`DESIGN.md`](DESIGN.md), "Terms and abbreviations".

## Headline

| | |
|:---|---:|
| respiratory rate, settled windows | **MAE 0.42 /min** |
| within 2 /min | **99 %** |
| sub-harmonic reports | **0 %** |
| beat detection F1 (median / worst) | **98.7 / 92.0** |
| window coverage | **90 %** |
| first RR report | **median 25.9 s of signal** |

Settled rows only; provisional rows (`_PROV`) are scored separately below.

## Method

All 14 recordings: 12 adult BIDMC/MIMIC with manual breath annotations and ECG,
plus the 2 neonatal recordings. Everything runs from **one binary** — the
detector is the `-d` runtime knob, not a build — with the default detector for
the patient type unless stated:

```sh
adult     ppg_analysis -i <rec> -nu 10000 -c 0 -s adult      # Elgendi TERMA
neonatal  ppg_analysis -i <rec> -nu 1     -c 0 -s neonate    # Karlen IMS
```

## Adult — 12 BIDMC/MIMIC recordings

Beat detection is scored against the ECG lead II R-peak reference supplied with
the dataset (±150 ms, per-recording lag alignment); respiratory rate against the
manual breath annotations.

**How the reference rate is built matters, and is stated here so the figures can
be reproduced.** The reference is `60·(n−1)/(t_last − t_first)` over the
annotated breaths inside the window — an interval rate. The obvious
alternative, `60·n/T_window`, is a *count*, and it quantises the reference to
multiples of 60/65.536 = 0.92 /min because a breath is either inside the window
or not, regardless of where its phase falls. Scoring the identical output both
ways gives MAE **0.85** (interval) against **0.95** (count), bias −0.01 against
0.00, within-2 92.2 % against 91.7 %. The gap is entirely the reference's own
quantisation, not a difference in the estimator. The interval form is used
throughout this document.

**Which rows the per-recording columns cover.** `RR MAE`, `bias` and `≤2 /min`
below are over **SETTLED rows only** — warm-up rows carrying a `_PROV` method
are excluded, because a provisional estimate from a partly-filled window is not
the estimator's steady-state accuracy. The cohort summary that follows the table
reports settled, provisional and all-reported separately.

This matters when comparing against anyone else's scoring: the same output over
**all reported** rows gives a higher per-recording MAE (bidmc_01 0.21 → 0.62,
bidmc_10 0.69 → 1.05), and both are correct. Scoring the all-reported subset
against this table therefore disagrees with it column for column until the two
subsets are aligned, after which the values match to 0.01. State which subset a
figure came from before comparing it with anything.
`RRV RMSSD`, `HRV SDNN` and `HRV RMSSD` are medians over that recording's
reportable rows.

| rec | ref RR | beats | Se % | PPV % | F1 | HR | 1st rpt | cover | RR MAE¹ | bias¹ | ≤2 /min¹ | RRV RMSSD | HRV SDNN | HRV RMSSD |
|:---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| bidmc_01 | 21.3 | 713 | 96.6 | 99.2 | 97.9 | 91 | 25.0 | 77 % | 0.21 | −0.08 | 100 % | 588 | 22.3 | 31.5 |
| bidmc_02 | 15.3 | 720 | 97.9 | 99.3 | 98.6 | 90 | **34.8** | 88 % | 0.45 | −0.12 | 100 % | 1075 | 46.2 | 74.8 |
| bidmc_03 | 17.7 | 597 | 91.4 | 94.0 | 92.7 | 76 | 34.7 | 96 % | 0.35 | +0.32 | 100 % | 791 | 19.4 | 27.2 |
| bidmc_04 | 16.6 | 695 | 89.2 | 95.0 | 92.0 | 93 | 25.4 | 98 % | 0.32 | −0.14 | 100 % | 695 | 15.5 | 20.9 |
| **bidmc_05** | **6.0** | 761 | 95.9 | 98.9 | 97.4 | 97 | 82.8 | **67 %** | **1.02** | +0.53 | **95 %** | 1355 | 17.5 | 26.5 |
| bidmc_06 | 20.1 | 653 | 99.4 | 99.8 | 99.6 | 81 | 25.9 | 98 % | 0.17 | +0.06 | 100 % | 455 | 9.0 | 13.0 |
| bidmc_07 | 20.0 | 720 | 99.7 | 100.0 | 99.9 | 90 | 26.0 | 96 % | 0.08 | +0.08 | 100 % | 711 | 5.8 | 8.6 |
| bidmc_08 | 21.2 | 794 | 99.5 | 100.0 | 99.7 | 100 | 41.9 | 88 % | 0.32 | +0.12 | 100 % | 714 | 6.4 | 8.5 |
| bidmc_09 | 20.1 | 609 | 98.4 | 99.3 | 98.9 | 76 | 25.8 | 81 % | 0.19 | +0.00 | 100 % | 855 | 5.9 | 7.7 |
| bidmc_10 | 18.7 | 640 | 93.9 | 96.7 | 95.3 | 81 | **18.1** | 100 % | 0.69 | −0.44 | 92 % | 761 | 75.0 | 40.8 |
| bidmc_11 | 14.8 | 738 | 98.9 | 99.9 | 99.4 | 91 | 25.7 | 98 % | 0.85 | +0.30 | 98 % | 887 | 34.3 | 29.0 |
| bidmc_12 | 18.9 | 738 | 98.7 | 99.7 | 99.2 | 91 | 25.9 | 98 % | 0.42 | +0.19 | 100 % | 700 | 33.3 | 16.4 |

¹ settled rows only — see the note above the table. Every column here is
produced by the validation procedure described in
[`../README.md`](../README.md), "Reproducing the measured figures", so this
table is regenerated rather than transcribed.

**Beat detection** — median Se **98.2 %**, PPV **99.3 %**, F1 **98.7**, worst 92.0.

**First RR report** — median **25.9 s**, range 18.1–82.8 s.

**Coverage 90 %** — 619 of 684 rows carry a rate; the rest are `DECLINED` by the
quality gate or `REJECTED` for want of a credible peak.

| | n | MAE | bias | within-2 |
|:---|---:|---:|---:|---:|
| **settled rows** | 570 | **0.42** | +0.06 | **99 %** |
| provisional (warm-up, `_PROV`) | 49 | 1.42 | −0.44 | 80 % |
| all reported | 619 | 0.50 | +0.02 | 97 % |

Ten of twelve recordings reach **100 % within-2**, eight are below 0.5 MAE, and
the slow-breathing record now reaches **95 %** where it was 61 %.

### Setting these beside the literature — and why they are not comparable

The respiratory-rate literature reports 95 % limits of agreement rather than
MAE, so the same rows are given both ways:

| | 95 % limits of agreement | bias |
|:---|:---|---:|
| **this implementation**, all reported rows | **−1.9 to +1.9 /min** | +0.0 |
| best of 314 algorithms on PPG, [CHARLTON] | −5.1 to +7.2 /min | +1.0 |
| best on ECG, [CHARLTON] | −4.7 to +4.7 /min | 0.0 |
| impedance pneumography — the clinical standard, [CHARLTON] | −5.6 to +5.2 /min | −0.2 |

**Do not read that first row as beating the field.** Four differences make the
comparison invalid, and the first is decisive:

- **Coverage is not the same measurement.** [CHARLTON] reports every window. This
  reports 90 % of them and declines the rest, and the declined windows are
  exactly the ones where the three surrogates disagree — that is, the hard ones.
  A method that answers only when its own estimators agree will always show
  tighter limits than one that must answer every time. The declined windows are
  counted in *Coverage* above and are not silently dropped, but they are not in
  this interval either.
- **Different subjects.** [CHARLTON]'s cohort is healthy adults aged 18–40
  breathing spontaneously under controlled conditions. These recordings are
  critically-ill patients.
- **Different reference.** [CHARLTON] measures against nasal-oral pressure, a
  direct airflow signal. This measures against two annotators' manual breath
  marks on the impedance channel.
- **Different windows.** The window length, overlap and reporting cadence differ,
  and limits of agreement depend on all three.

What the comparison *does* support is narrower and worth stating: on the windows
this implementation is willing to answer, its agreement with the reference is of
the same order as published work rather than an order worse — and the price of
that is the 10 % it declines.

Fusion method over the 684 rows: `ALL_THREE` 402, `TWO_AGREE` 165,
`DECLINED` 51, `ALL_THREE_PROV` 25, `TWO_AGREE_PROV` 24, `REJECTED` 14,
`TD_HARM` 2, `TD_SUBHARM` 1.

**Two of those counts are the harmonic guard's real signature.** `TD_HARM` fell
from 13 to 2 and `DECLINED` from 78 to 50 when it was enabled: the guard
PREVENTS the harmonic locks rather than rescuing them afterwards, so the
breath-count fallback barely fires and fewer windows disagree enough to be
declined. It is not a second correction layered on top of the first.

The `RRV RMSSD` column is the median over that recording's reportable rows, and
is a **derived, indicative** figure: it has no validated reference, and against
manual breath intervals it still reads ≈ 1.7 × for reasons inherent to
zero-crossing timing. It is not comparable with a published norm.

**bidmc_05, the slow breather, is the record every change has been measured
against.** It now reports 67 % of its windows at MAE 1.02 and 95 % within-2,
against 32 % / 2.93 / 61 % before the harmonic guard. Its first report is still
82.8 s. Part of that is physics — 16–33 s of data cannot contain a 6 /min rate
at any FFT length — and part is that its surrogates lock onto three different
harmonics, so they never corroborate one another. Both routes out of that were
measured and rejected; see [`DESIGN.md`](DESIGN.md), "Why the slow breather
still waits 82.8 s".

**bidmc_02 reports at 34.8 s, and that is a design choice rather than physics.**
Anchoring the fusion on a band-edge peak would delay it to 69.1 s — slower than
the analysis window itself. See [`DESIGN.md`](DESIGN.md), "The anchor must not be
a band-edge peak".

## Neonatal — 2 recordings, `-s neonate -nu 1`

Run with `-s neonate`, which dispatches to **Karlen IMS** — see
[`DESIGN.md`](DESIGN.md), "The beat detector is a runtime choice too".

| | neonatal_mimic_data1 | neonatal_mimic_data2 |
|:---|---:|---:|
| duration | 1190 s | 1158 s |
| beats detected / median HR | 2877 / **150 bpm** | 2758 / **144 bpm** |
| beats implied by the PPG's own cardiac spectrum | 3012 | 2862 |
| **detection rate** | **96 %** | **96 %** |
| RR coverage | 73 / 144 (51 %) | 71 / 140 (51 %) |
| RR median / IQR / range | 43.44 / 1.23 / 36.5–45.9 | 44.12 / 3.12 / 36.2–52.0 |
| RRV reportable | 71 / 144 | 69 / 140 |
| RRV RMSSD (median) | 358 ms | 309 ms |
| HRV SDNN / RMSSD | 25.2 / 23.3 | 44.2 / 64.0 |

**The second recording's inter-quartile range more than halved — 7.48 → 3.12
/min — when ectopic-derived vertices stopped entering the surrogates.** That is
the clearest single effect of the change described in
[`DESIGN.md`](DESIGN.md), "Ectopic beats do not reach the respiratory
surrogates", and it is why the change was kept: the adult figures do not move at
all, and the recordings carrying artefact tighten.

**RR coverage is 51 %, and that is the gate working rather than failing.** With
no breath annotations the test is self-consistency against each recording's own
median rate: declined windows sit **2.82 and 3.64 /min** from it against **1.63
and 0.80** for those kept, and are more than twice as likely to be over 5 /min
out. The declines are **episodic** — runs of up to 7 and 11 consecutive
separated by long clean stretches — which is the signature of movement or
periodic breathing, not a chronic deficiency. Raising the threshold would
readmit exactly those windows.

**Better detection did not buy coverage.** Switching to IMS added 226 beats on
the first recording and coverage went 67 % → 49 %; on the second, 48 % → 49 %.
Detection is not the coverage bottleneck — the case for IMS here rests on rate
and HRV, not on RR.

HR sits inside Fleming's 90–181 neonatal range and RR inside the normal
**30–60 /min**, having previously read ~28 — below the normal range — before the
1/f whitening.

**Why the neonatal detection rate is 96 % and not higher**, and how the beat
window is scaled to reach it, are in [`DESIGN.md`](DESIGN.md),
"Beat-window scaling at neonatal heart rates".

---

## Detector comparison — identical pipeline

| detector | med Se | med PPV | med F1 | worst F1 | settled RR MAE | within-2 | coverage |
|:---|---:|---:|---:|---:|---:|---:|---:|
| **TERMA — what `-s adult` selects** | 98.2 | **99.3** | **98.7** | **92.0** | **0.42** | **99 %** | **90 %** |
| Karlen IMS (`-d ims`) | **98.4** | 95.0 | 96.6 | 87.2 | 0.68 | 95 % | 80 % |

The gap widened once the harmonic guard was added: TERMA's cleaner estimates
track well, while IMS's noisier ones trip the tracker more often and are
declined.

This is the evidence for keeping the split rather than standardising on one
detector: TERMA is meaningfully better on adults on every measure except raw
sensitivity, and IMS pays for that sensitivity in precision (95.0 against 99.3).

On the neonatal pair the ranking reverses: IMS finds **96 %** of the expected
beats against TERMA's 85–88 %, because it carries no window whose length must be
a fraction of a cardiac cycle. That is why `-s neonate` dispatches to it.

Both arms scored against the same ECG beat reference and the same interval-rate
breath reference. IMS has the marginally higher
sensitivity but pays for it in precision, and those false beats propagate into
the AM/BW/FM surrogates.

## Sampling-rate invariance

Four recordings resampled and re-run with `-r` set accordingly:

| fs | decimation | window | RR MAE | beats (bidmc_01) | HR |
|---:|---:|---:|---:|---:|---:|
| 100 Hz | 6 | 61.4 s | 0.76 | 713 | 90 |
| **125 Hz** | 8 | 65.5 s | **0.86** | 712 | 90 |
| 250 Hz | 16 | 65.5 s | 0.79 | 712 | 90 |
| 367 Hz | 23 | 64.2 s | 0.81 | 712 | 91 |

Spread 0.10 /min across a 3.7× rate range.

## HRV against ECG-derived HRV

Same recordings, same NN rules, HRV computed from the ECG R-peaks as reference:

| metric | bias | MAE |
|:---|---:|---:|
| meanNN | +2.28 ms | **4.79 ms** |
| SDNN | −0.45 ms | 19.77 ms |
| RMSSD | −6.64 ms | 25.23 ms |

**`meanNN` is sound** — 4.79 ms on ~660 ms is 0.7 %, which confirms the beat
detection. **The variability metrics are not usable as clinical HRV**: 125 Hz
quantises an IBI to 8 ms, so an RMSSD of 6–20 ms is one to three quantisation
steps, and PPG pulse arrival carries vascular jitter the ECG does not see — this
is PRV, not HRV. Report `HR` and `meanNN`; do not report SDNN/RMSSD/pNN50 as HRV
until the sampling rate supports them.

