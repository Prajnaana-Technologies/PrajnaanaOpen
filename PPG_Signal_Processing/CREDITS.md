# Credits

One place to find every source this implementation rests on, and everyone who
helped build it.

Every citation here also appears in the source, beside the constant or method it
justifies, and the dataset attributions are in [`NOTICE`](NOTICE). This file is
the index, not the authority: if the two ever disagree, the source comment is
right, because that is where the number lives.

---

## People

We gratefully acknowledge **Dr. Arathy R** for sharing her expertise in medical
signal processing and for providing valuable technical advisory feedback in a
personal capacity during the development of the PPG signal-processing
algorithms.

**Mamatha BV** — original author; design and implementation.

Copyright 2026 Prajnaana Technologies Pvt. Ltd. — see [`LICENSE`](LICENSE).

---

## Methods drawn from published literature

Every physiological and filter constant below is cited beside the value it
justifies, and `include/filter_bands.h` enforces that as a rule: *no number in it
is chosen by judgement — if a value cannot be sourced it does not belong here.*

Not every part of the pipeline comes from a paper. Where a mechanism was
designed for this implementation it is marked `DEVELOPER'S IMPROVEMENT` in the
source at the point it is defined, with the reasoning and the measurement beside
it — so a cited value and a designed one are always distinguishable by reading
the code. Those contributions are listed under
[Original to this implementation](#original-to-this-implementation) below.

### Beat detection

`[ELGENDI]` **Elgendi M, Norton I, Brearley M, Abbott D, Schuurmans D.**
"Systolic Peak Detection in Acceleration Photoplethysmograms Measured from
Emergency Responders in Tropical Conditions."
*PLoS ONE* 8(10):e76585, 2013. [doi:10.1371/journal.pone.0076585](https://doi.org/10.1371/journal.pone.0076585) — open access, CC-BY.
→ the TERMA detector, `src/ppg_fiducial_elgendi_terma.c`

`[KARLEN]` **Karlen W, Ansermino JM, Dumont G.**
"Adaptive Pulse Segmentation and Artifact Detection in Photoplethysmography for
Mobile Applications."
*34th Annual International Conference of the IEEE EMBS*, 2012, pp 3131–4. [doi:10.1109/EMBC.2012.6346628](https://doi.org/10.1109/EMBC.2012.6346628)
→ the IMS detector, `src/ppg_fiducial_karlen_ims.c`

`[MATEO]` **Mateo J, Laguna P.**
"Analysis of heart rate variability in the presence of ectopic beats using the
heart timing signal."
*IEEE Transactions on Biomedical Engineering* 50(3):334–343, 2003. [doi:10.1109/TBME.2003.808831](https://doi.org/10.1109/TBME.2003.808831) — paywalled; no local copy.
→ the reason ectopic-derived vertices are excluded from the respiratory
surrogates, `ppg_common.h`, `ppg_analysis.c`

### Sample-path filtering

**Liang Y, Elgendi M, Chen Z, Ward R.**
"An optimal filter for short photoplethysmogram signals."
*Scientific Data* 5:180076, 2018. [doi:10.1038/sdata.2018.76](https://doi.org/10.1038/sdata.2018.76)
→ Chebyshev Type II, 4th-order prototype, `src/chebyshev_t2_o4.c`

### Respiratory rate

**Charlton PH, Bonnici T, Tarassenko L, et al.**
"An assessment of algorithms to estimate respiratory rate from the ECG and PPG."
*Physiological Measurement* 37:610–626, 2016. [doi:10.1088/0967-3334/37/4/610](https://doi.org/10.1088/0967-3334/37/4/610)
→ the fusion architecture and the surrogate taxonomy

**Liu H, Chen F, Hartmann V, et al.**
"Comparison of different modulations of photoplethysmography in extracting
respiratory rate: from a physiological perspective."
*Physiological Measurement* 41:094001, 2020. [doi:10.1088/1361-6579/abaaf0](https://doi.org/10.1088/1361-6579/abaaf0)
→ the FM surrogate definition and the adult search band

**Karlen W, Raman S, Ansermino JM, Dumont GA.**
"Multiparameter Respiratory Rate Estimation From the Photoplethysmogram."
*IEEE Transactions on Biomedical Engineering* 60(7):1946–1953, 2013. [doi:10.1109/TBME.2013.2246160](https://doi.org/10.1109/TBME.2013.2246160)
→ tagged `[KARLEN-13]`. **Smart fusion** — the quality gate. The three respiratory surrogates are
quality-assessed, and if their standard deviation exceeds a threshold **no rate
is output** rather than a doubtful one. Karlen's threshold is 4 breaths/min; here
it is scaled by the declared band width, anchored so the adult band reproduces
that figure exactly. Implemented as the `DECLINED` path and the `Spread_bpm`
column in `src/ppg_analysis.c`.

> **Route of citation, stated because it matters.** This method reached the
> implementation through **Charlton 2016**, which catalogues it as technique FM1
> and quotes the rule verbatim; the primary paper above was not consulted
> directly. It is credited to Karlen because the method is theirs, but the text
> this code was written against is Charlton's.

**Lázaro Plaza J.**
"Non-invasive techniques for respiratory information extraction based on pulse
photoplethysmogram and electrocardiogram."
PhD thesis, University of Zaragoza, 2015, sec 2.3.6 eq (2.21).
→ peak-conditioned cross-window spectral averaging — tagged `[LAZARO-T]`

**Lázaro J, Gil E, Bailón R, Mincholé A, Laguna P.**
"Deriving respiration from photoplethysmographic pulse width."
*Med. Biol. Eng. Comput.* 51:233–242, 2013. [doi:10.1007/s11517-012-0954-0](https://doi.org/10.1007/s11517-012-0954-0) — closed access; the
thesis above is by the same author and carries the method.
→ tagged `[LAZARO-13]`. Cited as the method this implementation deliberately
**departs from**: it smooths the output rate, where this averages the spectrum.
The measurement behind that choice is in `docs/DESIGN.md`.

**Bailón R, Sörnmo L, Laguna P.**
"A robust method for ECG-based estimation of the respiratory frequency during
stress testing."
*IEEE Trans. Biomed. Eng.* 53(7):1273–1285, 2006. [doi:10.1109/TBME.2006.871888](https://doi.org/10.1109/TBME.2006.871888)
→ the basis Lázaro's averaging builds on

**Zhang C, Wei S, Dong G, Zeng Y, Zhu G, Zhou X, Liu F.**
"Respiratory rate estimation from photoplethysmogram baseline wandering by
harmonic analysis and sequential fusion."
*Biomedical Signal Processing and Control* 100:107006, 2025. [doi:10.1016/j.bspc.2024.107006](https://doi.org/10.1016/j.bspc.2024.107006)
→ the sequential-tracking harmonic guard

### Physiological ranges

**Fleming S, Thompson M, Stevens R, et al.**
"Normal ranges of heart rate and respiratory rate in children from birth to
18 years of age: a systematic review of observational studies."
*The Lancet* 377(9770):1011–1018, 2011. [doi:10.1016/S0140-6736(10)62226-X](https://doi.org/10.1016/S0140-6736\(10\)62226-X)
→ every per-age RR and HR band, from Web Tables 4 and 5

**Qi J, Yu R, Wang X.**
"Neonatal supraventricular tachycardia: current diagnostic approaches and
emerging technologies."
*Frontiers in Pediatrics* 13:1694215, 2026. [doi:10.3389/fped.2025.1694215](https://doi.org/10.3389/fped.2025.1694215)
→ the fast end of the plausibility envelope, 320 /min

**Sato K, Miyamae Y, Kan M, et al.**
"Accelerated Idioventricular Rhythm Following Intraoral Local Anesthetic
Injection During General Anesthesia."
*Anesthesia Progress* 68(4):230–234, 2021. [doi:10.2344/anpr-68-03-09](https://doi.org/10.2344/anpr-68-03-09)
→ the slow end of the plausibility envelope, 20 /min

### Heart-rate variability

**Task Force of the ESC / NASPE.**
"Heart rate variability: standards of measurement, physiological interpretation
and clinical use."
*Circulation* 93(5):1043–1065, 1996. [doi:10.1161/01.CIR.93.5.1043](https://doi.org/10.1161/01.CIR.93.5.1043)
→ SDNN, RMSSD, pNN50, and the 5-minute short-term standard this does not meet

**Shaffer F, Ginsberg JP.**
"An Overview of Heart Rate Variability Metrics and Norms."
*Frontiers in Public Health* 5:258, 2017. [doi:10.3389/fpubh.2017.00258](https://doi.org/10.3389/fpubh.2017.00258)
→ why 125 Hz is a conditional limitation rather than a disqualification

### Numerical method

**Goertzel G.**
"An Algorithm for the Evaluation of Finite Trigonometric Series."
*The American Mathematical Monthly* 65(1):34–35, 1958. [doi:10.2307/2310304](https://doi.org/10.2307/2310304)
→ band power at two frequencies without an FFT, in the re-anchor spectral check

---

## Original to this implementation

Design contributions by **Mamatha BV**, each marked `DEVELOPER'S IMPROVEMENT`
where it is defined and measured in [`docs/DESIGN.md`](docs/DESIGN.md).

| contribution | file |
|:---|:---|
| **Subject-scaled detector windows.** Elgendi defines W1 and W2 *semantically* — "the systolic-peak duration", "the one-beat duration" — so his published millisecond values describe his cohort, not a constant. They are re-expressed for the declared heart-rate band instead of being copied. | `ppg_fiducial_elgendi_terma.c` |
| **1/f whitening before peak-picking**, with a Theil-Sen robust background fit so a single contaminated bin cannot lead the estimate. | `ppg_RR.c` |
| **Prominence-weighted agreement gate.** Charlton's architecture fuses across modulations but specifies no rule for when they disagree. | `ppg_analysis.c` |
| **Band edges rounded to the nearest bin**, with the lower edge clamped off bin 0 so the DC/drift bin can never be selected. | `ppg_analysis.c` |
| **Anchor rejection at the band edge.** Prominence is not correctness: a peak pinned to the lowest searchable bin is usually the truncated drift skirt. | `ppg_analysis.c` |
| **Time-domain estimate policing the spectral one.** The literature treats them as alternatives; using one to catch the other's harmonic locks does not follow from either. | `ppg_analysis.c` |
| **Gating of the harmonic guard.** The sequential tracker itself is Zhang's, cited above; confining it to `2f <= f_hi`, the only region where harmonic ambiguity can exist, is not — applied throughout it costs accuracy. | `ppg_analysis.c` |
| **Progressive warm-up schedule**, so a bedside display is not blank for a full analysis window. | `ppg_common.h` |
| **Confidence gate on peak prominence** — refuse to report when no respiratory peak stands above the background. | `ppg_common.h` |
| **Multi-beat gap handling** in interval sanitising, where a run of missed beats is neither a single repairable gap nor a genuine arrhythmia. | `ppg_common.h` |
| **Sampling-rate-independent surrogate grid**, so analysis duration is a property of the algorithm rather than of the acquisition hardware. | `ppg_common.h` |
| **Post-filter smoothing at the fiducial**, because foot and peak *amplitude* are themselves two of the three respiratory surrogates. | `filter_bands.h` |
| **Breath-interval gating for RRV**, separating what the variability measure may use from what the rate estimate may use. | `ppg_RR.c` |

The whole **runtime patient-type architecture** — one binary, the category
resolved once and passed down as data — is also original; see
[`docs/ppg_arch.md`](docs/ppg_arch.md).

---

## Datasets

No dataset is distributed with this repository. Both are Open Access on
PhysioNet, and both licences require attribution for work produced from the
data — an obligation that follows from **use**, not from redistribution.

**BIDMC PPG and Respiration Dataset** — Open Data Commons Attribution License v1.0
Used for the 12 annotated adult recordings: RR ground truth from the manual
breath annotations supplied with the dataset, beat ground truth derived from the
ECG lead II channel supplied with the dataset.

> Pimentel MAF, et al. "Towards a Robust Estimation of Respiratory Rate from
> Pulse Oximeters." *IEEE Trans. Biomed. Eng.* 64(8):1914–1923, 2016.
> [doi:10.1109/TBME.2016.2613124](https://doi.org/10.1109/TBME.2016.2613124)

**MIMIC-III Waveform Database** — Open Data Commons Open Database License v1.0
Used for the 68 short recordings and the 2 full-length neonatal recordings.

> Moody B, Moody G, Villarroel M, Clifford GD, Silva I. "MIMIC-III Waveform
> Database (version 1.0)." PhysioNet, 2020.
> [doi:10.13026/c2607m](https://doi.org/10.13026/c2607m)
>
> Johnson AEW, Pollard TJ, Shen L, et al. "MIMIC-III, a freely accessible
> critical care database." *Scientific Data* 3:160035, 2016.

**PhysioNet**

> Pollard T, Moody BE, Lehman L, et al. "PhysioNet as a global platform for
> biomedical research." *Nature Health*, 2026.
> [doi:10.1038/s44360-026-00096-z](https://doi.org/10.1038/s44360-026-00096-z)

The ODbL share-alike term applies to a derivative *database*. This software is
not one, so the Apache-2.0 licence of the source is unaffected.

---

## Where each citation lives in the code

| tag | file |
|:---|:---|
| `[FLEMING]` `[QI]` `[SATO]` `[LIU]` `[CHARLTON]` `[LIANG]` | `include/filter_bands.h` |
| `[TASKFORCE]` `[SHAFFER]` `[LAZARO-T]` `[LAZARO-13]` `[BAILON]` `[ZHANG]` `[MATEO]` | `include/ppg_common.h` |
| `[GOERTZEL]` | `src/ppg_fiducial.c` |
| `[ELGENDI]` | `src/ppg_fiducial_elgendi_terma.c` |
| `[KARLEN]` | `src/ppg_fiducial_karlen_ims.c` |
| `[KARLEN-13]` (smart fusion) | `include/ppg_common.h`, `src/ppg_analysis.c` |

Deviations from a cited source — where this implementation deliberately departs
from the paper, and what was measured to justify it — are recorded in
[`docs/DESIGN.md`](docs/DESIGN.md), "Literature conformance and deliberate
deviations".
