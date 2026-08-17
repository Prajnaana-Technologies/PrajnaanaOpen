/*
 * Copyright (c) 2026 Prajnaana Technologies Pvt. Ltd.
 *
 * SPDX-License-Identifier: Apache-2.0
 *
 * Original Author: Mamatha BV
 */
#ifndef PPG_COMMON_H
#define PPG_COMMON_H

/* *********************************************************************************** *
 *                                  INCLUDES                                           *
 * *********************************************************************************** */
#include <stdint.h>
#include <stdio.h>      /* struct_intp holds a FILE* -- keep this header
                         * self-contained rather than relying on the includer
                         * having pulled in <stdio.h> first. */

/* *********************************************************************************** *
 *                                  GENERIC MACROs                                     *
 * *********************************************************************************** */
#ifndef false
#define false   0
#endif
#ifndef true
#define true    1
#endif
#define PI      ((double)3.14159265358979323846)

/* THE VERSION.  Printed in the start-up banner, reported by -v, and written into
 * the last column of every row of every CSV, so a row lifted out of its file
 * still names the version that produced it.
 *
 * This is the single place it is defined.  It is a property of the SOURCE, so
 * releasing means editing it here and tagging that commit.  There is
 * deliberately no build-time override -- one would let the same sources report
 * two different versions, which is exactly what traceability must exclude. */
#define PPG_ANALYSIS_VERSION            "1.0.0"

/* The program's own name, for the banner and every diagnostic.  The Makefile
 * passes -DPPG_PROG_NAME with the name of the binary it is writing, so a build
 * under a different name reports that name and there is nothing to keep in step
 * by hand.  This fallback covers a bare compiler invocation that passes no -D. */
#ifndef PPG_PROG_NAME
#define PPG_PROG_NAME                   "ppg_analysis"
#endif

/* TIMING CONVENTION.  Every duration in this header -- and in filter_bands.h,
 * and throughout docs/ -- is SIGNAL time: seconds of recorded signal consumed,
 * never execution time.  A "65.5 s window" holds 65.5 seconds' worth of samples
 * and says nothing about how long it takes to compute.  Sample counts convert
 * at the grid rate (RR_INTP_GRID_HZ) or the input rate, never at wall-clock
 * rate; the program runs orders of magnitude faster than real time. */

/* *********************************************************************************** *
 *                               CODE Control MACROs                                   *
 * *********************************************************************************** */
#ifdef _WIN32

#ifndef int32_t
#define int32_t     int
#endif

#ifndef uint32_t
#define uint32_t    unsigned int
#endif

#else
#endif

/* *********************************************************************************** *
 *                       PPG SAMPLE-PATH AND BEAT-RATE LIMITS                          *
 * *********************************************************************************** */
#define DEFAULT_PPG_SAMPLING_RATE       (125u)  /* 125 samples per second */

/* Accepted range for -r.  A non-positive rate makes every filter coefficient
 * NaN (measured: "pole radius = nan ** UNSTABLE **") and the program used to
 * carry on and exit 0.  The upper bound is where the 40 ms smoothing window
 * would exceed SM_MAX_TAPS. */
#define MIN_SAMPLING_RATE_HZ            (20)
#define MAX_SAMPLING_RATE_HZ            (1000)

/* DC pedestal added to the sample-path bandpass output.
 *
 * The bandpass is AC-coupled, so its output swings about zero.  Re-centring on
 * the ADC full-scale value keeps the whole waveform positive with margin: the
 * largest negative excursion measured across both reference recordings was
 * -3262, leaving ~830 counts of headroom.
 *
 * WHAT IT AFFECTS NOW, stated because it is less than it once was.  It was
 * introduced for an amplitude check that expressed its threshold as a ratio
 * against the previous vertex, which needs a positive baseline; that check no
 * longer exists.  Neither shipped detector is sensitive to the offset: TERMA
 * high-passes at 0.5 Hz before clipping and squaring, so the DC is gone before
 * detection, and IMS works on segment amplitudes (end minus start) in which a
 * constant offset cancels.  Downstream, the surrogates are detrended before the
 * spectrum, which removes it again.
 *
 * It is kept because the value DOES reach the reported foot/peak amplitudes and
 * the per-sample trace, so removing it would shift those outputs.  Treat it as a
 * presentation offset, not as something detection depends on.
 *
 * This is a level shift, NOT filter gain: the waveform is offset, never scaled,
 * so no stage downstream sees an altered amplitude ratio.
 */
#define PPG_FILTER_DC_PEDESTAL          (4096.0)

/* Cited sources appear beside the values they justify.  ../CREDITS.md indexes
 * all of them in one place, with DOI links.
 *
 * Respiratory search band and every other filter parameter now live in
 * filter_bands.h, one set per patient type, selected at run time.  Each value
 * there is cited to
 * a paper or clinical standard; do not add unsourced numbers to it. */
#include "filter_bands.h"

/* DEVELOPER'S IMPROVEMENT -- a confidence gate is not in the source papers.
 * WHY: without one, a window containing no respiratory peak at all still emits
 * a number, taken from whatever bin happened to be largest in 1/f noise.  A
 * wrong value presented with no qualification is worse than no value.
 *
 * A window is only accepted when its spectral peak stands this far above the
 * median in-band power.  Rejecting weak windows is what stops the estimate
 * jumping around when there is no real respiratory peak to find.
 *
 * RE-DERIVED for the WHITENED spectrum (see estimate_rr_peak_bin).  On a
 * whitened spectrum the fitted 1/f background is divided out, so the median
 * sits at ~1 by construction and `q` reads directly as "how many times the
 * background does this peak stand".  The previous 2.5 was calibrated against
 * RAW PSD values, where the median is inflated by the drift skirt; the two
 * numbers are not comparable and carrying 2.5 across would silently reject
 * half of every neonatal recording.
 *
 * Measured coverage/accuracy trade on the whitened spectrum (adults, 12
 * annotated recordings; neonatal coverage from the two neonatal recordings):
 *
 *      thresh   adult MAE   within-2   adult cov   neonatal cov
 *        2.5      0.64        97 %      131/156      74/140
 *        2.0      0.80        95 %      144/156     103/140
 *        1.7      1.11        93 %      149/156     129/140
 *        1.5      1.45        88 %      156/156     139/140   <-- selected
 *
 * 1.5 is chosen because it is the only value that regresses NOTHING against
 * the pre-whitening build (MAE 1.74, within-2 82 %, adult coverage 135/156,
 * neonatal 137/140): every one of those improves.  Raising it buys real
 * accuracy at the cost of coverage, and that is a product decision -- the curve
 * above is the input to it, not a law.
 *
 * Note what 1.5 implies: on adults the gate now never fires.  That is
 * informative rather than alarming.  Most of what it used to reject, it
 * unusable because drift dominated the band -- and whitening removes the cause,
 * so the rejection is no longer needed. */
#ifndef RR_MIN_PEAK_PROMINENCE
#define RR_MIN_PEAK_PROMINENCE      (1.5)
#endif

/* Above this ratio the fused spectral estimate is treated as a harmonic of the
 * true rate and the breath count is preferred.  See the TD_HARM branch. */
#define RR_HARMONIC_RATIO           (1.8)

/* ---------------------------------------------------------------------------
 * CROSS-WINDOW SPECTRAL AVERAGING.
 *
 * SOURCE -- [LAZARO-T] Lazaro Plaza J, "Non-invasive techniques for respiratory
 * information extraction based on pulse photoplethysmogram and
 * electrocardiogram", PhD thesis, University of Zaragoza, 2015, sec 2.3.6
 * eq (2.21), building on [BAILON] Bailon R, Sornmo L, Laguna P, "A robust
 * method for ECG-based estimation of the respiratory frequency during stress
 * testing", IEEE Trans. Biomed. Eng. 53(7):1273-1285, 2006.
 * doi:10.1109/TBME.2006.871888
 * Local archive (not distributed): LazaroPlaza2015_PhD_RespiratoryInfoExtraction_PPG_ECG.pdf
 *
 * Their "peak-conditioned average" is
 *
 *      S_bar_k(f) = SUM_{l=0..Ls-1} SUM_j  chi_A(j,k-l) chi_B(j,k-l) S_j(k-l)(f)
 *
 * -- Ls successive spectra averaged ACROSS TIME (the k-l index) and across the
 * j respiratory surrogates, with chi_A admitting only spectra that are
 * "peaked enough" and chi_B preferring the more peaked surrogate.  What is
 * implemented here is the same three ideas: average the spectrum over time,
 * admit only surrogates that agree (RR_AGREEMENT_THRESHOLD, weighted by
 * prominence), and keep a window out of the average unless it passes the
 * quality gate (RR_SPREAD_MAX_BPM).
 *
 * TWO DELIBERATE DIFFERENCES.  Theirs is a rectangular average over Ls spectra;
 * this is exponential, which needs one buffer per surrogate instead of Ls and
 * weights recent data more.  Theirs normalises each spectrum's power in the
 * 0-1 Hz band before averaging; this does not, because the peak search that
 * follows is scale-free -- it compares a peak against the median of its own
 * in-band power.
 *
 * [CHARLTON] Charlton et al 2016 catalogues the same family as temporal fusion
 * (FT1) and reports that of the top-ranked algorithms on both ECG and PPG "all
 * except three used either smart fusion (FM1) or temporal fusion (FT1)".  We
 * had the first and not the second.  Its FT1 entry cites [LAZARO-13] Lazaro J,
 * Gil E, Bailon R, Minchole A, Laguna P, "Deriving respiration from
 * photoplethysmographic pulse width", Med. Biol. Eng. Comput. 51:233-242, 2013
 * (doi:10.1007/s11517-012-0954-0 -- closed access; the thesis above is by the
 * same author and carries the method), which smooths the OUTPUT rate as
 * RRi = 0.2*RR_est + 0.8*RR_(i-1).
 *
 * WHY THE SPECTRUM AND NOT THE OUTPUT: averaging the spectrum changes which
 * peak WINS.  Averaging the output cannot -- once the peak-picker has chosen a
 * sub-harmonic, no downstream filter recovers the fundamental.  Measured:
 * output mean-of-4 gives MAE 0.82 and output median-of-4 0.74, against 0.66 for
 * this.  That is also why [LAZARO-T] averages spectra where [LAZARO-13]
 * smoothed rates.
 *
 * ALPHA = 1/4.  Swept: 1/2 (0.78), 1/4 (0.66), 1/8 (0.78).  Note successive
 * windows overlap 87.5 %, so four of them are NOT four independent looks -- only
 * 12.5 % of each is new, and the effective span is ~90 s rather than 4 x 65.5.
 * That is why the gain is well short of the sqrt(4) that independent averaging
 * would predict. */
#define RR_PSD_ACCUM_N              (4u)

/* [KARLEN-13] Karlen W, Raman S, Ansermino JM, Dumont GA, "Multiparameter
 * Respiratory Rate Estimation From the Photoplethysmogram", IEEE Trans. Biomed.
 * Eng. 60(7):1946-1953, 2013 (doi:10.1109/TBME.2013.2246160).  Indexed in
 * ../CREDITS.md.  Reached this implementation through Charlton 2016, which
 * catalogues it as FM1 and quotes the rule verbatim; the primary paper was not
 * consulted directly.
 *
 * Smart fusion, as quoted in Charlton 2016: "RRs estimated
 * from BW, AM and FM respiratory signals are quality assessed.  If their
 * standard deviation is <= 4 bpm then RR is estimated as the mean, OTHERWISE NO
 * RR IS OUTPUT."  Spread_bpm is that standard deviation.  It was already being
 * computed and then ignored -- the estimate fell back to the single most
 * prominent surrogate instead of declining.  Measured over 612 windows, those
 * fall-back windows carry MAE 5.95 and 31 % sub-harmonic locking against 0.40
 * and 0.3 % when all three agree, so they are the worst rows in the output and
 * they were being reported with no mark on them. */
#define RR_SPREAD_MAX_BPM           (4.0)

/* Karlen's threshold is an ABSOLUTE 4 /min, measured on adults.  A neonatal
 * band spans 22-66 /min against the adult 4-30 -- 70 % wider -- and three
 * estimates of a 45 /min rate scatter proportionally more than three estimates
 * of a 16 /min one.  Applying 4 /min unchanged there declined more than half of
 * all windows.  The threshold is therefore scaled by the declared band width,
 * anchored so that the ADULT band reproduces Karlen's number exactly.
 *
 * This is a DEVELOPER'S extension of a published constant, not a new constant:
 * adult = 4.0 by construction, child = 6.5, neonate = 6.8. */
#define RR_SPREAD_ANCHOR_SPAN_BPM   (26u)   /* the adult band, 4..30 /min */

/* ---------------------------------------------------------------------------
 * HARMONIC GUARD BY SEQUENTIAL TRACKING.
 *
 * SOURCE -- [ZHANG] Zhang C, Wei S, Dong G, Zeng Y, Zhu G, Zhou X, Liu F,
 * "Respiratory rate estimation from photoplethysmogram baseline wandering by
 * harmonic analysis and sequential fusion", Biomedical Signal Processing and
 * Control 100:107006, 2025 (doi:10.1016/j.bspc.2024.107006).  Closed access;
 * the authors publish their MATLAB at
 * github.com/Chi1988723/PPG-respiratory-rate-estimation, which is what this was
 * read from.  Their sequential fusion is a scalar Kalman filter on the rate
 * whose covariance feeds back as the NEXT window's SEARCH RANGE -- not a
 * smoother on the output.  Constants Q and R below are theirs.
 *
 * WHAT PROBLEM IT SOLVES.  A breathing waveform is not a sine: it carries
 * energy at 2f and 3f as well as f.  When the fundamental is weak the spectrum
 * can lock onto a harmonic and report a multiple of the true rate.  The
 * existing RR_HARMONIC_RATIO guard catches this only when the breath COUNT
 * disagrees; it cannot help when the count is also unreliable.  Constraining
 * the search to the neighbourhood of an already-established rate catches it
 * directly: a subject breathing at 6 /min does not jump to 24 /min in one
 * window, so a candidate that far away is rejected before it can be chosen.
 *
 * WHY ONLY BELOW HALF THE BAND CEILING (the deviation from [ZHANG]).
 * The ambiguity exists only where a harmonic of the true rate is ITSELF inside
 * the search band -- that is, where 2f <= f_hi.  Above f_hi/2 there is no
 * in-band harmonic to be confused with, so narrowing the search protects
 * against nothing and costs only responsiveness to a genuine rate change.
 * Applied unconditionally, as published, it repairs the slow-breathing record
 * (MAE 2.93 -> 1.02) but costs every other record 0.02 to 0.49 and the cohort
 * 0.47 -> 0.54.  Applied only below f_hi/2 it keeps the whole repair and costs
 * nothing: cohort 0.47 -> 0.42, within-2 98 -> 99 %, coverage 87 -> 91 %.
 *
 * The condition is DERIVED from where the ambiguity can exist, not swept.
 *
 * CAVEAT ON THE EVIDENCE: the cohort contains exactly ONE slow breather, so
 * the entire measured gain rests on one recording.  The mechanism is sound and
 * the cost elsewhere is nil, but this has not been demonstrated across a
 * population of slow breathers.
 * ------------------------------------------------------------------------- */
#ifndef RR_TRACK_HARMONIC_GUARD
#define RR_TRACK_HARMONIC_GUARD     (1)     /* 0 disables; build with -D to A/B */
#endif

#define RR_TRACK_Q                  (1.0)   /* [ZHANG] process noise           */
#define RR_TRACK_R                  (5.0)   /* [ZHANG] measurement noise       */
#define RR_TRACK_P0                 (999.0) /* [ZHANG] initial covariance      */
#define RR_TRACK_P_LOST             (1000.0)/* [ZHANG] reset when tracking is lost */
#define RR_TRACK_AGREE_BPM          (1.0)   /* [ZHANG] agreement tolerance     */
#define RR_TRACK_COUNT_MAX          (10)    /* [ZHANG] confidence cap          */
#define RR_SPREAD_LIMIT(lo_, hi_)                                       \
            ((RR_SPREAD_MAX_BPM * (double)((hi_) - (lo_)))              \
             / (double)RR_SPREAD_ANCHOR_SPAN_BPM)

/* Two surrogates "agree" when they are within this many breaths/min. */
#define RR_AGREEMENT_THRESHOLD      (3.0)

#define RRV_MAX_BREATHS             (128u)  /* breath intervals kept for RRV   */

/* Breath intervals feed TWO consumers with opposite requirements, so they are
 * gated twice.  extract_breath_intervals() keeps the whole respiratory band,
 * because the time-domain rate built from it must stay free to contradict the
 * spectrum -- that is what makes it useful as a half/double-rate witness.
 * gate_breath_intervals() then narrows the series to the reported period before
 * VARIABILITY is computed, because a merged or split breath is indistinguishable
 * from real variability once it is inside the series.
 *
 * sqrt(2) is the log-domain midpoint between the period and its half and double,
 * i.e. an interval is kept when the reported period is its nearest plausible
 * multiple.  It is derived, not swept.  See gate_breath_intervals(). */
#define RRV_GATE_TOLERANCE          (1.41421356237309504880)

/* RRV is reported only when the surviving intervals describe the SAME rhythm as
 * the rate columns on that row: their mean rate must be within one Welch bin of
 * the reported rate.  Otherwise the row would carry a variability figure for a
 * rhythm the row does not report, so it is emitted as not-reportable instead. */
#define RRV_NOT_REPORTABLE          (-1.0)

/* ---------------------------------------------------------------------------
 * HRV (heart-rate variability) -- docs/FIDUCIAL_INTERFACE.md sec 4.2
 * The measures and the three rules below are the Task Force standard; the
 * rolling BEAT-count window is a DEVELOPER'S choice, explained at (c).
 *
 * Task Force of the ESC / NASPE, Circulation 93:1043-1065, 1996.  Time-domain
 * measures over NORMAL-TO-NORMAL intervals: mean NN, SDNN, RMSSD, pNN50.
 *
 * Three rules from that standard drive the implementation, and each is a trap:
 *
 *  (a) ARTIFACT SUBSTITUTION CORRUPTS HRV.  Replacing an outlying interval with
 *      a plausible value is fine for a RATE but injects artificial regularity
 *      into a VARIABILITY measure.  sanitize_ibi() repairs intervals for the HR
 *      path; HRV must see only intervals it did NOT repair, so sanitize_ibi now
 *      reports whether it touched the value and repaired beats are EXCLUDED.
 *  (b) EXCLUDED BEATS BREAK ADJACENCY.  RMSSD and pNN50 are defined over
 *      SUCCESSIVE pairs.  Differencing across a dropped beat compares intervals
 *      that are not neighbours, which silently inflates both.  hrv_adj[] marks
 *      genuinely adjacent pairs, exactly as bbi_adj[] does for RRV.
 *  [TASKFORCE] Task Force of the ESC / NASPE.  "Heart rate variability:
 *      standards of measurement, physiological interpretation and clinical
 *      use."  Circulation 93(5):1043-1065, 1996.
 *      doi:10.1161/01.CIR.93.5.1043
 *      Short-term standard 5 min; recommended ECG sampling 250-500 Hz.
 *  [SHAFFER] Shaffer F, Ginsberg JP.  "An Overview of Heart Rate Variability
 *      Metrics and Norms."  Front. Public Health 5:258, 2017.
 *      doi:10.3389/fpubh.2017.00258.  Qualifies the rate requirement: "a
 *      sampling rate of 125 Hz ... may be sufficient when RSA amplitude is
 *      normal", while "a minimum sampling frequency of 500 Hz may be required
 *      ... when RSA amplitude is low".  So 125 Hz is a CONDITIONAL limitation,
 *      not a disqualification -- which is why the values are emitted and
 *      qualified rather than suppressed.
 *
 *  (c) WINDOW LENGTH IS NOT FREE.  The Task Force short-term standard is
 *      5 MINUTES and SDNN is duration-dependent, so these values are NOT
 *      comparable with published 5-minute norms.  HRV_WINDOW_BEATS holds a
 *      rolling window sized in BEATS; at 60-100 bpm 300 beats is 3-5 min, the
 *      closest this pipeline can get while still reporting per analysis window.
 *      The emitted CSV column names carry no claim of Task Force compliance.
 * ------------------------------------------------------------------------- */
#define HRV_WINDOW_BEATS            (300u)
#define HRV_PNN_THRESHOLD_MS        (50u)   /* the "50" in pNN50 */
/* Fewest values a dispersion statistic may rest on.  Applied to the NN count
 * for SDNN AND to the adjacent-pair count for RMSSD/pNN50 -- one minimum, not
 * two, so a row cannot carry an RMSSD derived from fewer samples than its own
 * SDNN.  Below it the field is -1, the same sentinel RRV uses. */
#define HRV_MIN_INTERVALS           (3u)
#define RR_HR_HIST_LEN              (256u)
/* DEVELOPER'S IMPROVEMENT.  WHY: a gap of exactly one missed beat was already
 * repaired, but a run of two or three fell through to the generic outlier
 * branch and was either replaced wholesale or kept as a genuine arrhythmia,
 * both of which corrupt the rate.  4 is where a "gap" stops being a missed beat
 * and becomes a signal dropout that should not be interpolated at all. */
#define MAX_MISSED_BEATS            (4u)    /* largest gap sanitize_ibi will split */

/* Both are used TWICE by sanitize_ibi() -- once for start-up, once for the
 * re-anchor that can overrule the category prior -- so the override is never
 * quicker or more credulous than start-up, and adds no new tuning constant. */
#define IBI_CALIBRATION_BEATS       (10u)   /* beats before a rate is trusted   */
#define IBI_OUTLIER_TOLERANCE_PCT   (60u)   /* outlier window, % of running mean */

/* WHY A REPAIRED INTERVAL IS CLASSIFIED, NOT JUST FLAGGED.
 *
 * [CHARLTON] eliminates features derived from ectopic beats BEFORE extracting
 * the respiratory signals, citing [MATEO].  Doing that needs more than "this
 * interval was repaired", because the repairs are not alike:
 *
 *   SUBSTITUTED -- the measured interval was discarded and replaced by the last
 *                  valid one.  Whatever this beat's timing was, it is not what
 *                  the analysis now holds, so a respiratory vertex taken from it
 *                  is a vertex on a fabricated time base.
 *   SPLIT       -- a beat was MISSED and the long interval was divided.  The
 *                  beat that closes it is REAL and its vertex is a real
 *                  observation; only the interval was reconstructed.
 *
 * The distinction decides which vertices may enter the surrogates.  Both still
 * exclude the interval from the HRV NN series, which is why any non-zero value
 * keeps the existing `0 == repaired` test working unchanged.
 *
 * [MATEO] Mateo J, Laguna P, "Analysis of heart rate variability in the presence
 * of ectopic beats using the heart timing signal", IEEE Transactions on
 * Biomedical Engineering 50(3):334-343, 2003, doi:10.1109/TBME.2003.808831.
 * Paywalled; no local copy.  Indexed with all other sources in ../CREDITS.md */
#define IBI_REPAIR_NONE         (0u)
#define IBI_REPAIR_SUBSTITUTED  (1u)   /* band test -- CATEGORY-DEPENDENT      */
#define IBI_REPAIR_SPLIT        (2u)
#define IBI_REPAIR_LOCAL        (3u)   /* local-deviation test -- band-free    */

/* Whether a SPLIT beat's vertices are excluded too.  Off: only substituted
 * intervals are excluded, which is [CHARLTON]'s intent applied to the repairs
 * that actually fabricate a time base.  Overridable so the alternative can be
 * measured rather than argued -- see docs/DESIGN.md. */
#ifndef RR_EXCLUDE_SPLIT_VERTICES
#define RR_EXCLUDE_SPLIT_VERTICES (0)
#endif
/* WHICH REPAIRS EXCLUDE A VERTEX -- and why only the band-free one is used.
 *
 * [CHARLTON] eliminates ectopic-derived features before extracting respiratory
 * signals, citing [MATEO].  Both identify an ectopic beat from the interval
 * series' OWN LOCAL STATISTICS -- a deviation from the surrounding trend --
 * never from a population band.
 *
 * That distinction is load-bearing here.  This program repairs an interval for
 * two different reasons, and only one of them is a statement about the beat:
 *
 *   BAND  -- the interval fell outside the DECLARED CATEGORY's limits.  That is
 *            a statement about the declaration, not about the beat, and the
 *            declaration is exactly what is wrong when it is wrong.
 *   LOCAL -- the interval disagreed with the running mean of its own
 *            neighbours.  Band-free, and what the sources actually describe.
 *
 * MEASURED, and the difference is the whole decision.  Excluding vertices on
 * the BAND test costs the annotated adults settled RR MAE 0.42 -> 0.48 and
 * within-2 99 % -> 98 %.  Excluding them on the LOCAL test costs those figures
 * NOTHING -- 0.42, 99 %, the same 570 settled rows -- while still tightening
 * the neonatal recordings (one gains 3 reportable windows and its inter-quartile
 * range falls 7.48 -> 3.12 /min).
 *
 * The knobs exist so that result can be reproduced rather than believed. */
#ifndef RR_GATE_ON_BAND
#define RR_GATE_ON_BAND  (0)
#endif
#ifndef RR_GATE_ON_LOCAL
#define RR_GATE_ON_LOCAL (1)
#endif

#define MIN(x_, y_)   ((x_) < (y_) ? (x_) : (y_))

/* *********************************************************************************** *
 *                        THE MOVING AVERAGE -- ONE IMPLEMENTATION                      *
 *
 * Both smoothing stages in this program are the SAME filter: a causal trailing
 * average of N taps with a truncating integer divide.  They differ only in how
 * many taps and on which axis:
 *
 *     sample path      5 taps at the input rate      = PPG_SMOOTH_MS (40 ms)
 *     surrogate grid   RR_MVG_AVG_SAMPLE_CNT taps at
 *                      RR_INTP_GRID_HZ               = 320 ms
 *
 * They are one implementation -- movavg_run() -- with one instance per stream,
 * so there is a single place that decides what a moving average does and a
 * single definition of its group delay.  They were once two separate pieces of
 * code with two different notions of "delay", and that is precisely how the
 * surrogate trace came to be published 2 grid points out of step with its own
 * raw column.  One filter, one delay, one place to correct it.
 *
 * GROUP DELAY.  A causal average cannot centre its own output: the value for
 * sample n would need samples up to n + (N-1)/2, which have not arrived.  So it
 * runs late by movavg_group_delay() = (N-1)/2 taps, and each consumer puts its
 * own results back on the true time base -- the detectors subtract it from the
 * fiducial indices they report, and the surrogate trace labels each row at the
 * centre of the window it averaged.  No RATE is affected either way: a constant
 * delay cancels out of every interval, and a magnitude spectrum ignores phase.
 * *********************************************************************************** */
#define MOVAVG_MAX_TAPS                 (63u)   /* 40 ms at up to ~1575 Hz      */
#define RR_MVG_AVG_SAMPLE_CNT           5
/* The average needs RR_MVG_AVG_SAMPLE_CNT REAL samples before it means
 * anything.  A minimum of (CNT/2)+1 = 3 would mean that at idx_raw 3 and 4 the average
 * covered slots that were untouched zeros and still divided by 5, so the first
 * two outputs were 40 % and 20 % low. */
#define RR_MVG_AVG_SAMPLE_DELAY         (RR_MVG_AVG_SAMPLE_CNT)

/* One moving-average instance.  Held as a running sum, so the cost is one add
 * and one subtract per sample whatever the window length. */
typedef struct  tag_movavg
{
    int32_t     ring [MOVAVG_MAX_TAPS];
    int64_t     sum;
    uint32_t    wr;
    uint32_t    taps;

} struct_movavg;

void        movavg_init        (struct_movavg *ps_ma, uint32_t taps);
int32_t     movavg_run         (struct_movavg *ps_ma, int32_t sample_value);
uint32_t    movavg_group_delay (const struct_movavg *ps_ma);


/* *********************************************************************************** *
 *                              ENUM TYPE-DEFINITIONS                                  *
 * *********************************************************************************** */

/* Enum to indicate what data to interpolate */
typedef enum
{
    INTERPOLATE_BW,
    INTERPOLATE_AM,
    INTERPOLATE_FM,
    INTERPOLATE_INDEX_MAX,

} enum_interpolate;

/* *********************************************************************************** *
 *                              STRUCTURE DEFINITIONS                                  *
 * *********************************************************************************** */

/* ----------------------------------------------------------------------------
 * Spectral estimator geometry.
 *
 * A single periodogram is NOT a consistent estimator: every bin is chi-squared
 * with 2 degrees of freedom, so its standard deviation equals its mean NO
 * MATTER HOW LONG the window.  That is why AM/BW/FM agreed on their medians
 * (within 1.7 /min of each other) yet disagreed by more than 5 bpm in 42-67 %
 * of individual windows: each estimate was a single draw from a distribution as
 * wide as its own mean.  Lengthening the window alone does not help.
 *
 * AVERAGE periodograms (Welch): the analysis window is split into overlapping
 * segments whose periodograms are averaged, which divides the variance by
 * roughly the number of independent segments.
 *
 * Measured inter-surrogate spread (median std of AM/BW/FM, breaths/min):
 *      512-pt window,  single periodogram   4.32 / 5.39
 *     1024-pt window,  single periodogram   3.45 / 3.38
 *     1024-pt window,  Welch 7 x 256/50%    1.73 / 2.99   <-- Welch adopted here
 *     2048-pt window,  Welch 7 x 512/50%    1.50 / 3.05   (2x the latency)
 *
 * That comparison chose Welch over a single periodogram.  The segment length and
 * overlap were sized separately afterwards -- see filter_bands.h -- and the
 * shipped build uses 75 % overlap with a per-category segment, not the 7 x 50 %
 * of the rows above.
 *
 * (RR_VERTEX_SAMPLE_SIZE below is a generic name for "the analysis window", not
 * a macro; the real constants are NEONATE_/CHILD_/ADULT_WINDOW_PTS in
 * filter_bands.h and the active value is the field sel_rr_window_pts.)
 *
 * Trade-off: the analysis window grows to RR_VERTEX_SAMPLE_SIZE interpolated
 * samples (65.5 s for adults and children, 32.8 s for neonates, at the
 * RR_INTP_GRID_HZ grid rate below), so the first estimate arrives later and
 * each one is an average over a longer stretch.  Segment length RR_WELCH_SEG
 * sets the raw bin spacing; parabolic peak interpolation recovers the sub-bin
 * precision that costs.  Both are derived from the band floor in
 * filter_bands.h -- see the sizing block there.
 * -------------------------------------------------------------------------- */
/* THE ANALYSIS WINDOW AND SEGMENT ARE RUNTIME VALUES, not macros.
 *
 * They are derived from the subject's band floor -- see the sizing block in
 * filter_bands.h -- and the subject is selected while the program is running,
 * so they live in the context as sel_rr_window_pts / sel_rr_welch_seg.  The two
 * constants below are the LARGEST any category asks for, and exist only to
 * dimension storage.  Nothing computes with them.
 *
 * WHY STORAGE IS SIZED FOR THE WORST CASE RATHER THAN THE SELECTED SUBJECT.
 * This is a monitor with a patient-type knob, not three different monitors.
 * One binary must serve a neonate and an adult, so the buffers have to hold
 * whichever is larger and the ACTIVE LENGTH is carried alongside.  The cost is
 * about 12 KB of context on the smallest configuration, which is the price of
 * not shipping three firmware images.
 *
 * RR_MAX_WINDOW_PTS and RR_MAX_WELCH_SEG are defined in filter_bands.h, beside
 * the per-category sizing that determines them. */

/* Raw-sample ring.  Deliberately NOT tied to RR_VERTEX_SAMPLE_SIZE: a beat
 * detector's history requirement is independent of the RR window, and
 * process_ppg_in_samples() primes with (PPG_RING_LEN - 1) samples out of a
 * 1024-entry block, so growing this would read past that block. */
#define PPG_RING_LEN          (1024u)

/* Surrogate grid buffer.  FIXED, and deliberately independent of the selected
 * subject: it is twice the largest analysis window, because AM, BW and FM fill
 * from different fiducials and whichever runs ahead must hold its surplus until
 * the slowest completes a window.  Sizing this from the active window would
 * make the buffer change shape when the knob moves, which is exactly what a
 * statically allocated design must not do. */
#define INTP_MAVG_BUFF_SIZE     (RR_MAX_WINDOW_PTS * 2u)   /* 2048 grid points */
/* How far the analysis window slides between reports.  This sets the REPORTING
 * CADENCE only -- it does not touch a single estimate.  Proved directly: slide
 * 128 and slide 256 over the same window and segment give 336 matched instants
 * with 0 differences, max difference 0.000000.  Any MAE difference between two
 * slide settings is therefore a sampling artefact of which instants happened to
 * be scored, never an accuracy difference.
 *
 * 128 grid points = 8.2 s at the 125 Hz design point, against 32.8 s for the
 * previous half-window slide.  Cost is ~4x the per-window work, which is a few
 * percent of total runtime.
 *
 * NOTE for anything consuming these reports: consecutive windows now share
 * 87.5 % of their data, so successive values are NOT independent measurements.
 * Trend or alarm logic of the form "N consecutive readings above a threshold"
 * would be invalid on them. */
#define RR_WINDOW_SLIDE_PTS     (128u)

/* ---------------------------------------------------------------------------
 * WARM-UP: report early from a SHORT window, then grow into the full one.
 *
 * DEVELOPER'S IMPROVEMENT -- no paper specifies a warm-up schedule.
 *
 * WHY: a full window is 65.5 s, and a monitor that shows nothing for the first
 * minute is not usable at the bedside.  There is no way to shorten it without
 * losing something -- frequency resolution is 1/T_segment, so a short record
 * simply does not contain a slow rate -- but there IS a way to report what the
 * data available so far can support, and to say how far that is.  The window
 * therefore starts at RR_PROG_MIN_PTS and doubles until it reaches the
 * subject's full size, after which the estimator is EXACTLY the shipped one.
 *
 * Zero-padding a short record up to the full length is not usable:
 * it quarters the bin SPACING while leaving the RESOLUTION where the available
 * data put it (-3 dB main lobe 5.49 /min padded, against 2.75 /min for a full
 * record), so it reports false precision.  Oversampling the surrogate grid was
 * likewise rejected -- at matched segment DURATION a 32 ms grid measured
 * identical to the 64 ms one, because seconds carry the information and samples
 * do not.  See docs/DESIGN.md. */
#define RR_PROG_MIN_PTS         (128u)  /* earliest report: ~17 s in practice  */

/* A segment of T seconds cannot resolve a rate slower than about this many
 * cycles within T -- the same threshold that sizes the windows in
 * filter_bands.h.  While the window is still growing, the reportable floor is
 * therefore HIGHER than the subject's declared floor, and the row says so
 * rather than emitting a rate the data cannot support.  Once the window is
 * full the DECLARED band is used unchanged: the adult 4 /min floor is a
 * deliberate 2.18-cycle compromise documented in filter_bands.h, and this must
 * not silently override it. */
#define RR_MIN_CYCLES_PER_SEG   (3.0)

/* --------------------------------------------------------------------------
 * SURROGATE GRID RATE -- why this is derived from fs and not a bare constant
 *
 * DEVELOPER'S IMPROVEMENT.  No paper states this; it matters only once the
 * input sampling rate moves away from the value the other constants were
 * chosen at.
 *
 * The surrogates are resampled onto a uniform grid every N raw samples, and
 * RR_VERTEX_SAMPLE_SIZE grid points are then analysed.  If N is a FIXED sample
 * count, the analysis window is (RR_VERTEX_SAMPLE_SIZE * N / fs) SECONDS -- a
 * duration that slides with the input sampling rate.  Held at 8, that is
 * is correct only at the 125 Hz design point.  Measured, same recordings, only
 * the rate changed:
 *
 *      fs      grid      window    Welch bin    RR MAE
 *     100 Hz   12.50 Hz   81.9 s   2.93 /min     1.72
 *     125 Hz   15.63 Hz   65.5 s   3.66 /min     1.81   <-- design point
 *     250 Hz   31.25 Hz   32.8 s   7.32 /min     2.16
 *     367 Hz   45.88 Hz   22.3 s  10.75 /min     3.05   <-- 69 % worse
 *
 * Nothing physiological changed; the window shrank to 22 s and the Welch bin
 * widened to 10.75 breaths/min, so the estimator read the respiratory peak
 * through a coarser grid over fewer breaths.  Fixing the GRID RATE instead of
 * the decimation keeps the window at ~65 s for any input rate, which is what
 * every measured figure in docs/DESIGN.md was obtained at.
 *
 * 15.625 Hz is 125/8 exactly, so the 125 Hz behaviour is bit-for-bit unchanged.
 * The Chebyshev low-pass at BP_LP_CORNER_HZ (6 Hz) sits below this grid's
 * Nyquist (7.8 Hz), so the decimation does not alias.
 * -------------------------------------------------------------------------- */
#ifndef RR_INTP_GRID_HZ                     /* -D-overridable, for A/B measurement */
#define RR_INTP_GRID_HZ         (15.625)    /* = 125 Hz / 8, the design point  */
#endif
#define RR_DECIMATION(fs_)   RR_DECIMATION_CLAMP((uint32_t) \
                                    ((((double)(fs_)) / RR_INTP_GRID_HZ) + 0.5))
#define RR_DECIMATION_CLAMP(n_)  (((n_) < 1u) ? 1u : (n_))

/* Longest span the interpolator will bridge between two beats.  A detector
 * that reports indices out of order, or skips implausibly far, is rejected
 * rather than allowed to stall the grid loop.  60 s at any sane rate. */
#define MAX_PLAUSIBLE_BEAT_SAMPLES  (60u * 1000u)
#define INTP_BUFF_SIZE          (8u)

/* One entry of the detector's sample ring.  Only what a detector actually
 * produces: the input, the band-passed value and the conditioned value it
 * detects on.  Derivative, landmark and interpolation fields belong to whatever
 * detector needs them, not to this shared ring.
 *
 * A detector must fill all three, and must keep the last two DISTINCT: they are
 * written to the trace as the `Chebyshev` and `Smoothed` columns, and a reader
 * comparing them is entitled to see the moving average's effect on its own. */
typedef struct  tag_data_buf
{
    int32_t             input_sample;     /* as read, after -nu scaling        */
    int32_t             filtered_sample;  /* after the band-pass, BEFORE the MA */
    int32_t             smoothed_sample;  /* after the MA -- what it detects on */

} struct_data_buf;


/* ----------------------------------------------------------------------------
 * Respiratory bandpass.
 *
 * The original design used ONE RBJ bandpass biquad whose Q was derived as
 * Q = f0 / (f_high - f_low).  For a wide band that yields Q < 0.5, which is not
 * a bandpass in any useful sense: measured -3dB span was 2.7-39.8 /min with
 * only +/-1.3 dB of shaping across the whole range, so sub-respiratory drift
 * passed essentially unattenuated.
 *
 * Replaced by a 4th-order Butterworth HIGH-pass cascaded with a 4th-order
 * Butterworth LOW-pass (4 biquads).  Independent HP/LP sections are the correct
 * construction for a WIDE band; the geometric-mean bandpass biquad is only
 * appropriate for narrow ones.  Applied forward-then-reverse per window, which
 * gives zero phase distortion and leaves no filter state to carry from one
 * window into the next.
 * -------------------------------------------------------------------------- */
#define RR_BP_SECTIONS  (4u)

typedef struct {
    double b0, b1, b2;      /* numerator   */
    double a1, a2;          /* denominator (a0 normalised to 1) */
} rr_biquad_coeffs;

typedef struct {
    double x1, x2, y1, y2;  /* Direct Form 1 state */
} rr_biquad_state;

typedef struct {
    rr_biquad_coeffs  s [RR_BP_SECTIONS];
    double          fs;
    double          f_lo;       /* Hz */
    double          f_hi;       /* Hz */
    uint32_t        designed;
} biquad_bp;

typedef struct  tag_intp
{
    FILE        *fp_est_rr;       /* Just to log raw and moving-average samples */

    /* Circular buffer structure for RR related computations.
     *
     * "vertex" is deliberately generic.  One struct_intp serves all three
     * respiratory surrogates, and the fiducial it tracks differs per instance:
     *
     *     s_intp_peak  (AM)  vertex = signal PEAK  amplitude
     *     s_intp_foot  (BW)  vertex = signal FOOT  amplitude
     *     s_intp_freq  (FM)  vertex = maximal-upslope INTERVAL, not an amplitude
     *
     * so no single name here can say "peak" or "foot" without being wrong for
     * two of the three.  The caller states which one via enum_interpolate. */
    uint32_t    cur_intp_sample_index;
    uint32_t    prev_vertex_index;
    int32_t     prev_vertex_value;
    uint32_t    intp_vertex_count;
    int32_t     mvg_avg_sample [INTP_MAVG_BUFF_SIZE];
    int32_t     intp_buff [INTP_BUFF_SIZE];
    uint32_t    mvg_idx;
    /* This surrogate's own instance of the shared moving average. */
    struct_movavg s_mvg;
    biquad_bp   bp_filter;

} struct_intp;

/* Main context structure for the Algorithm, holding all necessary information
 * required for accurate detection of all fiducial points and the Metrics data.
 */
/* ---------------------------------------------------------------------------
 * ANALYSIS context.  Owned by ppg_analysis.c.
 *
 * Holds ONLY what the respiratory/cardiac analysis needs.  Beat-detection state
 * lives in struct_fiducial (ppg_fiducial.h) and is not visible here -- that
 * separation is what makes the detector replaceable.
 * ------------------------------------------------------------------------- */
typedef struct  tag_ppg_analysis
{
    uint32_t    sel_fs_hz;
    uint32_t    sel_rr_decimation;      /* raw samples per surrogate grid pt  */
    /* ---- analysis sizing, from the selected subject; <= the RR_MAX_* above ---- */
    uint32_t    sel_rr_window_pts;      /* ACTIVE window, grid points         */
    uint32_t    sel_rr_welch_seg;       /* ACTIVE Welch segment, grid points  */
    uint32_t    sel_rr_window_full;     /* the subject's full window          */
    uint32_t    sel_rr_welch_seg_full;  /* the subject's full segment         */
    uint32_t    rr_warmup_done;         /* the full window has been reached   */
    uint32_t    rr_next_report_pts;     /* warm-up report cadence             */
    /* ---- sequential tracking, for the harmonic guard ---- */
    double      rr_track_rr;            /* tracked rate, -1 until established */
    double      rr_track_p;             /* Kalman covariance                  */
    int32_t     rr_track_count;         /* consecutive agreements, <= MAX     */
    uint32_t    sel_rr_welch_overlap;   /* = 75 % of the segment              */
    uint32_t    sel_rr_slide_pts;       /* window advance between reports     */
    uint32_t    samples_processed;

    /* Beat-interval sanitiser state (HR path). */
    uint32_t    ibi_last_valid_ms;
    uint32_t    ibi_running_mean_ms;
    uint32_t    ibi_history_count;
    /* Re-anchor state.  Once ibi_reanchored is set the category prior is spent
     * and only the plausibility envelope gates -- see sanitize_ibi(). */
    uint32_t    ibi_run_ms;             /* most recent member of the run      */
    uint32_t    ibi_run_len;            /* how many agreed in a row           */
    uint32_t    ibi_reanchored;         /* 1 = category prior overruled       */
    void       *ps_fiducial;            /* detector ctx, for the spectral check;
                                         * void* so ppg_common.h stays free of
                                         * ppg_fiducial.h.  Set by ppg_main.c. */

    /* Heart rate, derived from the beats the detector reports. */
    uint32_t    last_peak_index;        /* 0 = no peak seen yet               */
    uint32_t    last_upslope_index;     /* previous cycle's maximal-upslope   */
    int32_t     last_pulse_amplitude;   /* previous cycle's OWN peak-to-foot  */

    /* ---- HRV: normal-to-normal beat intervals ---- */
    uint32_t    nn_ms  [HRV_WINDOW_BEATS];  /* UNREPAIRED intervals only      */
    uint8_t     nn_adj [HRV_WINDOW_BEATS];  /* 1 = adjacent to the previous   */
    uint32_t    nn_cnt;
    uint32_t    nn_wr;
    uint32_t    nn_prev_accepted;       /* previous beat entered the series   */
    double      hrv_mean_ms;
    double      hrv_sdnn_ms;
    double      hrv_rmssd_ms;
    double      hrv_pnn50_pct;
    uint32_t    hrv_n;                  /* intervals the metrics are based on */

    /* Diagnostic only: interpolated surrogate value per sample, for the CSV
     * trace.  Held here because the sample ring belongs to the detector. */
    int32_t     intp_trace [INTERPOLATE_INDEX_MAX][PPG_RING_LEN];
    uint32_t    intp_trace_hi;          /* highest index written               */
    int32_t     last_peak_value;
    int32_t     last_foot_value;
    uint32_t    hr_bpm;                 /* most recent instantaneous HR       */

    /* ---- Vertex deferral (see docs/DESIGN.md, "Vertex deferral").
     *
     * A foot is STAGED here when it is detected and COMMITTED at its peak,
     * after the beat has been judged by sanitize_ibi() and BEFORE the beat's
     * HR/HRV are published.  That order is load-bearing: committing after the
     * publish instead shifts every HR/HRV number by one beat while leaving
     * every RR column unchanged, which no respiratory figure would reveal.
     *
     * The decision fields below carry the judgement to the commit: a beat whose
     * interval was repaired by the LOCAL test contributes no vertex, because
     * its timing is the ectopic one.  See ppg_on_peak() and docs/DESIGN.md,
     * "Ectopic beats do not reach the respiratory surrogates". */
    uint32_t    pend_foot_index;        /* staged foot index, for the BW vertex */
    int32_t     pend_foot_value;        /* staged foot value                    */
    uint32_t    pend_upslope_index;     /* staged upslope of the cycle just ended */
    uint32_t    pend_upslope_valid;     /* 1 = that upslope was established      */
    uint32_t    pend_foot_present;      /* 1 = a foot is staged and not consumed */
    /* Accept/reject state.  Beat N's decision gates BW and AM of beat N; the
     * FM vertex committed at foot N+1 describes cycle N, so it is gated by
     * beat N's decision.  Both are 1 today: every beat is accepted. */
    uint32_t    beat_last_accepted;     /* decision for beat N-1                */
    uint32_t    beat_this_accepted;     /* decision for beat N                  */

    /* Respiratory surrogates: AM (peaks), BW (feet), FM (upslope intervals) */
    struct_intp s_intp_peak;
    struct_intp s_intp_foot;
    struct_intp s_intp_freq;

    /* ---- subject band: supplied to ppg_analysis_init(), never a macro here ---- */
    uint32_t    sel_rr_min_bpm;
    uint32_t    sel_rr_max_bpm;
    uint32_t    sel_hr_min_bpm;         /* expected HR, for the sanity warning  */
    uint32_t    sel_hr_max_bpm;
    uint32_t    sel_ibi_min_ms;         /* = 60000 / sel_hr_max_bpm  (PRIOR)    */
    uint32_t    sel_ibi_max_ms;         /* = 60000 / sel_hr_min_bpm  (PRIOR)    */
    /* Plausibility envelope -- the only hard limits, same for every category. */
    uint32_t    sel_ibi_floor_ms;       /* = 60000 / HR_PLAUSIBLE_MAX_BPM       */
    uint32_t    sel_ibi_ceil_ms;        /* = 60000 / HR_PLAUSIBLE_MIN_BPM       */
    const char *sel_subject_name;       /* for messages only                    */
    uint32_t    rr_band_locked;         /* band has been applied                */
    uint32_t    hr_band_warned;         /* HR-vs-category warning already issued */
    uint32_t    hr_hist [RR_HR_HIST_LEN];
    uint32_t    hr_hist_cnt;
    uint32_t    hr_hist_wr;

    /* ---- accepted-RR history (median smoothing) ---- */

    /* ---- RRV: breath-to-breath intervals harvested from the respiratory wave ---- */
    double      bbi_ms [RRV_MAX_BREATHS];
    uint8_t     bbi_adj [RRV_MAX_BREATHS];   /* 1 = adjacent to the previous */
    uint32_t    bbi_cnt;
    double      rrv_sd_ms;              /* SD of breath-to-breath intervals   */
    double      rrv_rmssd_ms;           /* RMS of successive BBI differences  */
    double      rrv_cv_pct;             /* coefficient of variation, %        */

} struct_ppg_analysis;

/* ---------------------------------------------------------------------------
 * THE SUBJECT IS A KNOB, NOT A BUILD.
 *
 * Which patient type a recording belongs to is selected while the program is
 * running -- the same binary serves a neonate, a child and an adult, the way a
 * bedside monitor has a patient-type setting rather than three part numbers.
 * Everything below the entry point works from the values in this structure and
 * never asks which category is active: the whole file set is generic, and only
 * ppg_main.c, where the operator's selection arrives, turns a name into numbers.
 *
 * The sizing members are here for the same reason as the bands.  They are
 * derived from the band floor (see filter_bands.h) and must travel with it: a
 * band without its window length is not a usable configuration, because the
 * segment is what decides whether that floor can be resolved at all.
 *
 * Storage is dimensioned by RR_MAX_WINDOW_PTS / RR_MAX_WELCH_SEG and the ACTIVE
 * length is carried in the context, so no allocation happens when the selection
 * changes and the code runs without a heap.
 * ------------------------------------------------------------------------- */
typedef struct
{
    const char *name;               /* as typed by the operator, e.g. "adult" */
    const char *description;        /* shown in messages                      */
    uint32_t    rr_min_bpm;         /* respiratory search band, breaths/min   */
    uint32_t    rr_max_bpm;
    uint32_t    hr_min_bpm;         /* expected heart rate, beats/min         */
    uint32_t    hr_max_bpm;
    uint32_t    window_pts;         /* analysis window,  <= RR_MAX_WINDOW_PTS */
    uint32_t    welch_seg;          /* Welch segment,    <= RR_MAX_WELCH_SEG  */
    uint32_t    slide_pts;          /* advance between reports                */
    /* Which beat detector suits this patient type.  Declared here for the same
     * reason as the bands: it is a property of the subject, not of the build.
     * TERMA is better on adults, IMS at neonatal rates -- see ppg_fiducial.c. */
    int32_t     detector;           /* an enum_fiducial; int32_t keeps this
                                     * header free of ppg_fiducial.h          */

} struct_subject_band;

/**
 * @brief Reset the analysis layer for a new recording.
 *
 * @param ps_ppg  Analysis context
 * @param fs_hz Sample rate of the PPG stream, Hz
 * @param ps_band       The selected subject: bands and analysis sizing.  Beat
 *                      interval limits and the Welch overlap are derived here,
 *                      so no caller has to compute them.
 */
void    ppg_analysis_init (struct_ppg_analysis *ps_ppg,
                           int32_t fs_hz,
                           const struct_subject_band *ps_band);

#endif  /* PPG_COMMON_H */

