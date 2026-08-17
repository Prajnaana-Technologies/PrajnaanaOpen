/*
 * Copyright (c) 2026 Prajnaana Technologies Pvt. Ltd.
 *
 * SPDX-License-Identifier: Apache-2.0
 *
 * Original Author: Mamatha BV
 */
#ifndef FILTER_BANDS_H
#define FILTER_BANDS_H

/* ***************************************************************************
 * filter_bands.h -- every filter parameter, selected by subject category.
 *
 * RULE FOR THIS FILE: no number in it is chosen by judgement.  Each one is
 * traceable to a named paper or clinical standard, cited beside it.  If a value
 * cannot be sourced it does not belong here -- see "UNSOURCED" at the bottom
 * for what that rule currently excludes.
 *
 * TIMING CONVENTION.  Every duration here is SIGNAL time: seconds of recorded
 * signal, never execution time.  This matters most for the word LATENCY, used
 * throughout the sizing discussion below.  "131 s latency" means the estimate
 * needs 131 seconds' worth of SAMPLES before it can exist -- an information
 * limit that would hold on infinitely fast hardware.  It is not compute time,
 * which is orders of magnitude smaller and never the constraint.
 *
 * SOURCES
 * -------
 * Every source below is also listed, with a resolvable DOI link, in
 * ../CREDITS.md.  The citation HERE is the authority: it sits beside the value
 * it justifies, and CREDITS.md is only the index.
 *
 * [FLEMING]  Fleming S, Thompson M, Stevens R, et al. "Normal ranges of heart
 *            rate and respiratory rate in children from birth to 18 years of
 *            age: a systematic review of observational studies."  The Lancet
 *            377(9770):1011-1018, 2011.  doi:10.1016/S0140-6736(10)62226-X  Web Tables 4 (respiratory rate) and 5
 *            (heart rate): centile cut-offs over 13 age bands, from respiratory
 *            data on 3881 children and heart-rate data on 143 346 children.
 *            Local archive (not distributed): Fleming2011_Lancet_RR_HR_centiles_Webappendix.pdf
 *
 * [LIU]      Liu H, Chen F, Hartmann V, et al. "Comparison of different
 *            modulations of photoplethysmography in extracting respiratory
 *            rate: from a physiological perspective."  Physiol. Meas.
 *            41:094001, 2020.  doi:10.1088/1361-6579/abaaf0  Searched 0.1-0.5 Hz for normal breathing and
 *            0.05-0.3 Hz for deep breathing, in healthy adults aged 19-58.
 *
 * [CHARLTON] Charlton PH, Bonnici T, Tarassenko L, et al. "An assessment of
 *            algorithms to estimate respiratory rate from the ECG and PPG."
 *            Physiol. Meas. 37:610-626, 2016.  doi:10.1088/0967-3334/37/4/610  Band-pass -3 dB at 4 and 60 bpm;
 *            spectral peak searched between 4 and 60 bpm, in healthy adults.
 *
 * [LIANG]    Liang Y, Elgendi M, Chen Z, Ward R.  "An optimal filter for short
 *            photoplethysmogram signals."  Scientific Data 5:180076, 2018.  doi:10.1038/sdata.2018.76
 *            Chebyshev Type II, 4th order, cheby2(order, 20, [fL fH]/Fn).
 *
 * [QI]       Qi J, Yu R, Wang X.  "Neonatal supraventricular tachycardia:
 *            current diagnostic approaches and emerging technologies."
 *            Frontiers in Pediatrics 13:1694215, 2026.
 *            doi:10.3389/fped.2025.1694215.  Orthodromic AVRT, the dominant
 *            neonatal mechanism, "produces a narrow QRS complex tachycardia with
 *            heart rates ranging from 220 to 320 beats per minute" (p3).
 *            Local archive (not distributed): Qi2026_FrontPediatr_NeonatalSVT.pdf
 *
 * [SATO]     Sato K, Miyamae Y, Kan M, et al.  "Accelerated Idioventricular
 *            Rhythm Following Intraoral Local Anesthetic Injection During
 *            General Anesthesia."  Anesthesia Progress 68(4):230-234, 2021.
 *            doi:10.2344/anpr-68-03-09.  Gives the escape rhythm as
 *            "a ventricular escape beat/rhythm (20-40 bpm)" -- quoted verbatim
 *            here because the article is not redistributable; resolve the DOI
 *            above for the full text.
 * *************************************************************************** */

/* ---------------------------------------------------------------------------
 * Subject category.  Declared, not inferred: the band must match the patient,
 * and guessing it from the signal is itself a source of error.  The measured
 * heart rate is used only to WARN when it disagrees with what is declared here.
 * ------------------------------------------------------------------------- */
/* No SUBJECT_CATEGORY macro exists any more.  The category is chosen at run time
 * with -s; see struct_subject_band in ppg_common.h and the table in ppg_main.c. */

/* ---------------------------------------------------------------------------
 * RESPIRATORY SEARCH BAND (breaths/min).
 *
 * This is the range the spectral peak search is confined to -- NOT the
 * sample-path filter, which is in Hz and is common to all categories below.
 *
 * For the two paediatric categories the band is the span of [FLEMING]'s 1st to
 * 99th centile cut-offs across every age band in the category, so it covers
 * 98 % of normal subjects of that age by construction:
 *
 *   [FLEMING] Web Table 4, respiratory rate, breaths/min
 *     age band   1st centile   99th centile
 *     0 - 3m         25             66
 *     3 - 6m         24             64
 *     6 - 9m         23             61
 *     9 - 12m        22             58     -> NEONATE spans 22 .. 66
 *     12 - 18m       21             53
 *     18 - 24m       19             46
 *     2 - 3y         18             38
 *     3 - 4y         17             33
 *     4 - 6y         17             29
 *     6 - 8y         16             27
 *     8 - 12y        14             25
 *     12 - 15y       12             23
 *     15 - 18y       11             22     -> CHILD spans 11 .. 53
 *
 * [FLEMING] stops at 18 years, so the adult band comes from the RR-algorithm
 * literature instead.
 *
 * CEILING, 30/min: [LIU]'s normal-breathing range is 0.1-0.5 Hz = 6-30/min.
 * [CHARLTON]'s wider 4-60/min was measured across the annotated adult recordings
 * and is materially worse (MAE 6.09 vs 3.39, subharmonic locking 23 % vs 13 %):
 * harmonic ambiguity exists wherever 2f <= f_hi, so a 60/min ceiling puts the
 * second harmonic of every normal adult rate inside the search.
 *
 * FLOOR, 4/min: a deliberate widening of [LIU]'s 6, adopted only after the low
 * bins were made usable -- a 512-sample Welch segment for resolution, 1/f
 * whitening, band edges rounded to the NEAREST bin, and the harmonic guard.
 * (Rounding outward instead would search below the floor the band asked for,
 * which is what made the low bins unusable; see docs/DESIGN.md.)  Commercial
 * monitors report down to 4/min, so a 6/min floor was a limit of this
 * implementation rather than of the physiology.  The cost is stated with the
 * sizing block below: at 4/min a 512-sample segment carries 2.18 cycles, short
 * of the 3-cycle rule.  Full derivation in docs/DESIGN.md, "Why the adult band
 * floor is 4 /min, and what it costs".
 * ------------------------------------------------------------------------- */
/* ALL THREE CATEGORIES ARE DEFINED, NOT ONE SELECTED.  The subject is a runtime
 * choice (see struct_subject_band in ppg_common.h), so every category's values must
 * be present in the binary; ppg_main.c assembles them into a table and the
 * operator picks one.  Nothing here is conditional. */
#define NEONATE_RR_BAND_MIN_BPM     (22u)   /* [FLEMING] WT4 1st centile, 9-12m   */
#define NEONATE_RR_BAND_MAX_BPM     (66u)   /* [FLEMING] WT4 99th centile, 0-3m   */
#define NEONATE_HR_MIN_BPM          (90u)   /* [FLEMING] WT5 1st centile, birth   */
#define NEONATE_HR_MAX_BPM          (181u)  /* [FLEMING] WT5 99th centile, 0-3m   */
#define NEONATE_NAME                "NEONATE (0-12 months)"

#define CHILD_RR_BAND_MIN_BPM       (11u)   /* [FLEMING] WT4 1st centile, 15-18y  */
#define CHILD_RR_BAND_MAX_BPM       (53u)   /* [FLEMING] WT4 99th centile, 12-18m */
#define CHILD_HR_MIN_BPM            (43u)   /* [FLEMING] WT5 1st centile, 15-18y  */
#define CHILD_HR_MAX_BPM            (156u)  /* [FLEMING] WT5 99th centile, 12-18m */
#define CHILD_NAME                  "CHILD (1-18 years)"

#define ADULT_RR_BAND_MIN_BPM       (4u)    /* deviation from [LIU]'s 6; see below */
#define ADULT_RR_BAND_MAX_BPM       (30u)   /* [LIU] 0.5 Hz, normal breathing     */
#define ADULT_HR_MIN_BPM            (43u)   /* [FLEMING] WT5 1st centile, 15-18y, */
#define ADULT_HR_MAX_BPM            (104u)  /*   the oldest band FLEMING reports  */
#define ADULT_NAME                  "ADULT (over 18 years)"

/* ---------------------------------------------------------------------------
 * PHYSIOLOGICAL PLAUSIBILITY ENVELOPE (bpm).  ONE pair for every category.
 *
 * The per-category HR_MIN/HR_MAX above are [FLEMING] centiles: what is NORMAL
 * for an age.  These two are what is POSSIBLE for a human heart, and they are
 * the only limits allowed to reject an interval outright.  The category band is
 * a prior that sustained evidence can overrule; this envelope is not.
 *
 * Why the two must not be the same number: docs/DESIGN.md, "The category band
 * is a prior, not a gate".
 * ------------------------------------------------------------------------- */
/* These bound the PULSE rate, which is what a PPG measures.  [QI] also reports
 * neonatal atrial flutter at 300-500/min, but that is an ATRIAL rate and reaches
 * the periphery divided by AV conduction (commonly 2:1), so it is not the bound. */
#define HR_PLAUSIBLE_MAX_BPM        (320u)  /* [QI]   neonatal AVRT upper bound   */
#define HR_PLAUSIBLE_MIN_BPM        (20u)   /* [SATO] ventricular escape floor    */

/* BEAT-INTERVAL LIMITS IN MILLISECONDS are NOT defined here.  Both the
 * per-category limits and the envelope above are reciprocals of a rate, and
 * ppg_analysis_init() derives them from the band it is handed, so sanitize_ibi()
 * works from context rather than from a macro.  Defining them here as well would
 * create a second source of truth that a later edit could change with no effect
 * -- which is exactly the trap worth avoiding. */


/* ---------------------------------------------------------------------------
 * SAMPLE-PATH FILTER (Hz).  Common to all categories.
 *
 * Type and order are [LIANG]: Chebyshev Type II, 4th-order prototype.  The
 * three deviations from that paper -- 40 dB rather than 20 dB, corners pinned
 * at -3 dB rather than at -Rs, and a 0.02 Hz rather than ~0.5 Hz lower corner
 * -- are each defended in docs/DESIGN.md and in the header of
 * chebyshev_t2_o4.c.  The lower corner in particular MUST stay low whatever the
 * subject category: the BW respiratory surrogate is baseline wander, and every
 * band above reaches well below 0.5 Hz.
 * ------------------------------------------------------------------------- */
#define BP_CHEB2_ORDER          (4)     /* [LIANG] prototype order -> 4 biquads */
#define BP_CHEB2_STOPBAND_DB    (40.0)  /* deviation from [LIANG]'s 20 dB       */
#define BP_HP_CORNER_HZ         (0.02)  /* deviation; see chebyshev_t2_o4.c     */
#define BP_LP_CORNER_HZ         (6.00)  /* [LIANG] effective upper edge 6.41 Hz */

/* DEVELOPER'S IMPROVEMENT -- no paper specifies a smoothing stage here, and
 * the source detector papers operate on unsmoothed input.
 * WHY: foot and peak AMPLITUDE are two of the three respiratory surrogates, so
 * a fiducial that lands one sample early on a noisy crest reports the wrong
 * amplitude and that error propagates straight into the respiratory estimate.
 *
 * Post-filter smoothing ahead of beat detection.  In MILLISECONDS so it scales
 * with the sampling rate.  The value is MEASURED: a duration sweep over the 12
 * annotated recordings puts the optimum at 40 ms, degrading on either side.
 * 0 disables the stage.  Rationale, the measured sweep and the reason it is a
 * duration and not a tap count: chebyshev_t2_o4.c. */
#ifndef PPG_SMOOTH_MS                   /* -D-overridable, for A/B measurement  */
#define PPG_SMOOTH_MS           (40.0)  /* = 5 taps at the 125 Hz design point  */
#endif

/* ---------------------------------------------------------------------------
 * SLOW / MEDITATIVE BREATHING -- why the adult lower edge is not lowered
 *
 * Meditative practice reaches rates far below the adult band.  Documented:
 * 2.5/min sustained for 15 min with Samavritti Pranayama (Bordoni et al,
 * Front. Syst. Neurosci. 2022, PMC8977447); 3-4/min for Zen Tanden breathing;
 * 4.32 +/- 1.87/min mean for yogic breathing.  No source was found for 1-2/min,
 * so 2/min is not adopted.  The shipped floor is 4/min -- widened from [LIU]'s
 * 6 once whitening and the harmonic guard made the low bins usable; see
 * docs/DESIGN.md.  A 2/min floor is not usable here: it would need a 131 s
 * segment and a 524 s window (8.7 min latency), and it collapsed a recording whose rate sits at
 * exactly twice the newly admitted floor (MAE 0.12 -> 6.06) by admitting its
 * own half-rate into the search.
 *
 * MORE IMPORTANTLY, lowering it does not buy slow breathing -- it buys the drift
 * bin.  The peak search runs on Welch SEGMENT bins, and the segment is
 * RR_WELCH_SEG samples:
 *
 *      segment      = 512 / 15.625 Hz = 32.8 s
 *      bin spacing  = 15.625 / 512    = 0.0305 Hz = 1.83 /min
 *      bin 1        = 0.0305 Hz       = 1.83 /min
 *      bin 2        = 0.0610 Hz       = 3.66 /min
 *
 * and compute_rr() rounds each edge to the NEAREST bin, then clamps
 * `if (1 > k_lo) k_lo = 1` so bin 0 (DC) can never be selected.  The shipped
 * 4/min floor rounds to bin 2; a 2/min floor would round to bin 1, which is
 * where residual baseline drift lives -- which is why the measured result was a
 * collapse, not an improvement.  (Only an edge below half a bin, 0.92 /min,
 * would round to bin 0 and be clamped up to bin 1.)  Resolving 2.5/min
 * needs a segment of at least 1/0.0417 Hz = 24 s and realistically 3 cycles,
 * i.e. 72 s, which rounds to the same 2048-point segment and 8192-point window
 * as a 2 /min floor -- 8.7 min of latency -- a change to RR_WELCH_SEG and
 * RR_VERTEX_SAMPLE_SIZE, not to a band.  Measured cost of each candidate
 * floor, at 3 cycles: 2/min -> 2048-sample segment, 8192 window, 8.7 min
 * latency; 3-4/min -> 1024 segment, 4096 window, 4.4 min; 6/min -> 512
 * segment, 2048 window, 2.2 min.  Three breaths at 2/min take 90 s; that is
 * physics, not an implementation choice.
 *
 * The sample-path filter is NOT the constraint: BP_HP_CORNER_HZ of 0.02 Hz is
 * 1.2 /min, so the front end already passes meditative breathing.  Only the
 * spectral resolution blocks it.
 * ------------------------------------------------------------------------- */

/* ---------------------------------------------------------------------------
 * NAMING, so a grep does not come up empty.  The derivations below use
 * RR_WELCH_SEG and RR_VERTEX_SAMPLE_SIZE as generic names for "the Welch
 * segment" and "the analysis window".  Neither is a macro.  The real constants
 * are per category -- NEONATE_/CHILD_/ADULT_WELCH_SEG and the matching
 * _WINDOW_PTS below -- and the values actually in force at run time are the
 * struct fields sel_rr_welch_seg and sel_rr_window_pts.
 *
 * SPECTRAL WINDOW SIZING -- derived from the band floor, not chosen
 *
 * The quantity that governs whether a respiratory peak can be resolved is the
 * number of breathing CYCLES inside one Welch SEGMENT -- not inside the
 * analysis window.  The two are often confused; the identity is:
 *
 *      T_seg   = RR_WELCH_SEG / intp_fs
 *      cycles  = f * T_seg
 *      bin / f = 1 / (f * T_seg) = 1 / cycles
 *
 * so "enough cycles" and "fine enough bins" are the SAME requirement: N cycles
 * in a segment means the bin is 1/N of the rate being measured.  The analysis
 * window contributes nothing to resolution -- it only sets how many segments
 * are averaged, i.e. the VARIANCE of the estimate.
 *
 * MEASURED THRESHOLD: 3 cycles.  Over 135 scored windows on the 12 annotated
 * adult recordings, median absolute error by cycles-per-segment was
 *
 *      < 3.0 cycles   1.98 /min      (n = 13)
 *      3.0 - 4.0      0.32           (n =  8)
 *      4.0 - 5.0      0.67           (n = 45)
 *      5.0 - 6.0      0.43           (n = 68)
 *
 * -- 3 to 6x worse below 3 cycles and flat above it.  So 3 is the floor and
 * more buys nothing.  Hence
 *
 *      RR_WELCH_SEG = 3 * intp_fs / f_lo
 *
 * which lands on powers of two almost exactly for the three bands.  Sizes are
 * kept powers of two by CONVENTION, not by necessity: welch_psd() evaluates a
 * direct DFT over the chosen bins and accepts any segment length.  Every cost
 * quoted in this header follows that convention, so the figures compare.  The
 * analysis window is 4 x the segment; at the shipped 75 % overlap that is 5
 * segments for adults and 13 for the paediatric categories.
 *
 *   category  floor   3-cycle segment  ->  chosen   T_seg    bin      window
 *   NEONATE   22/min      127.8            128      8.2 s   7.32/min   512
 *   CHILD     11/min      255.7            256     16.4 s   3.66/min  1024
 *   ADULT      4/min      703.1            512     32.8 s   1.83/min  1024  <-- see below
 *
 * ADULT DEVIATES DELIBERATELY and keeps 512 / 1024.  Applying the rule to its
 * 4 /min floor gives a 703-sample segment, which rounds up to 1024 and forces a
 * 4096-sample window -- 262 s of latency, four times what is shipped.
 *
 * It would also buy little.  Doubling the adult segment from 256 to 512 inside
 * the existing 1024 window moved MAE only 1.74 -> 1.65, because the gain is
 * confined to windows below 3 cycles: adults at 12-22 /min already sit at a
 * median of 5.1 cycles per segment, only 13 of 135 windows were in the starved
 * region, and those did improve (2.71 -> 2.41).  A further doubling would reach
 * fewer windows still, at four times the latency.
 *
 * CONSEQUENCE, stated rather than hidden: with a 512-sample segment the adult
 * band floor of 4 /min carries 2.18 cycles, short of the 3-cycle rule, so RR
 * near the floor is resolved but not comfortably.  That is the price of a 65.5 s latency, and it is
 * why the meditative-breathing note above concludes that 2-4 /min needs a
 * separate operating mode rather than a wider band.
 *
 * NEONATES gain the most and were the reason for doing this.  128 samples still
 * gives 3.0 cycles at their 22 /min floor, so nothing is lost, and the window
 * halves to 32.8 s -- the right direction for the population that deteriorates
 * fastest.  Measured on the two neonatal recordings (raw 12-bit input, -nu 1),
 * 1024/256 -> 512/128: median RR 26.84 -> 28.35 and 28.77 -> 28.78, with the
 * interquartile spread TIGHTENING from 3.09 -> 2.33 and 4.79 -> 1.99 despite
 * the shorter window.  No neonatal breath annotations exist, so that is
 * stability and plausibility, NOT a demonstration of accuracy.
 *
 * CHILD is unchanged at 256 / 1024 -- the derivation lands exactly on what was
 * already there (255.7 -> 256).  It is written out here so the value is
 * derived rather than coincidental.
 * ------------------------------------------------------------------------- */
#define NEONATE_WINDOW_PTS      (512u)      /* 32.8 s window at 15.625 Hz  */
#define NEONATE_WELCH_SEG       (128u)      /* 8.2 s, 3.0 cycles at 22/min */

#define CHILD_WINDOW_PTS        (1024u)     /* 65.5 s                      */
#define CHILD_WELCH_SEG         (256u)      /* 16.4 s, 3.0 cycles at 11/min */

/* ADULTS DEVIATE FROM THE 3-CYCLE RULE ABOVE, DELIBERATELY.
 *
 * With a 4 /min floor the rule would demand a 703-point segment (45 s), which at
 * the power-of-two sizes used throughout is 1024, and so a 4096-point window --
 * 262 s of latency, four times what is shipped.  512 is taken instead: 32.8 s,
 * 2.18 cycles at the floor and 4.4 at a normal 16 /min.  Two things make that
 * workable, and neither existed when the rule was written:
 *
 *   - the 1/f background is whitened before the peak search, so the drift bin
 *     no longer wins by default;
 *   - a harmonic guard (RR_HARMONIC_RATIO) catches the failure that IS left at
 *     low rates, which is the spectrum locking onto 2f, 3f or 4f rather than
 *     losing the peak.
 *
 * 512 in a 1024 window at 75 % overlap also keeps FIVE Welch segments, so
 * resolution is bought without giving up the averaging.  A 512-point WINDOW
 * cannot do this -- it forces a choice between one periodogram (resolution, no
 * averaging) and a 256 segment (averaging, no resolution), and both were
 * measured worse.  See docs/DESIGN.md. */
#define ADULT_WINDOW_PTS        (1024u)     /* 65.5 s                       */
#define ADULT_WELCH_SEG         (512u)      /* 32.8 s, 2.18 cycles at 4/min */

/* The largest window and segment any category asks for.  Storage throughout the
 * tree is dimensioned by these -- one binary serves every patient type, so the
 * buffers must fit the biggest -- while the ACTIVE length travels in the
 * context.  They are stated rather than computed because the preprocessor has
 * no max(); the checks below make a stale value a build failure, not a silent
 * overrun. */
#define RR_MAX_WINDOW_PTS       (1024u)
#define RR_MAX_WELCH_SEG        (512u)

#if (ADULT_WINDOW_PTS > RR_MAX_WINDOW_PTS) || (CHILD_WINDOW_PTS > RR_MAX_WINDOW_PTS) \
 || (NEONATE_WINDOW_PTS > RR_MAX_WINDOW_PTS)
#error "a category's window exceeds RR_MAX_WINDOW_PTS -- raise it"
#endif
#if (ADULT_WELCH_SEG > RR_MAX_WELCH_SEG) || (CHILD_WELCH_SEG > RR_MAX_WELCH_SEG) \
 || (NEONATE_WELCH_SEG > RR_MAX_WELCH_SEG)
#error "a category's Welch segment exceeds RR_MAX_WELCH_SEG -- raise it"
#endif

/* ---------------------------------------------------------------------------
 * UNSOURCED -- deliberately NOT defined here
 *
 * An adult band of 8-30/min was once measured as far better than any sourced
 * option (MAE 1.79 vs 3.39, subharmonic 4 % vs 13 %, 81 % within 2/min vs 65 %).
 *
 * ** THOSE NUMBERS ARE OBSOLETE AND THE CONCLUSION NO LONGER HOLDS. **  They
 * were taken on an earlier configuration -- floor 6, a 256-sample segment,
 * outward-rounded band edges and NO 1/f whitening.  The baseline it beat (3.39)
 * does not exist any more.  Re-measured on the current build:
 *
 *      floor 4 (shipped)   MAE 0.86   within-2 92 %   subharmonic 2 %
 *      floor 8             MAE 1.43   within-2 88 %   subharmonic 0 %
 *
 * (Both rows are one A/B taken together; it is their DIFFERENCE that matters.
 * The absolute level is not the shipped headline -- for that see docs/RESULTS.md,
 * which scores settled rows only.)
 *
 * Floor 8 is now materially WORSE overall.  It does help the records the drift
 * floor used to spoil -- the drift-heavy recordings improve by 0.8-1.1 /min, and
 * MAE excluding the slow-breathing recording falls 0.68 -> 0.54 -- but it puts a 6/min
 * subject outside the search entirely, and the slow-breathing recording goes
 * 2.79 -> 11.23.
 *
 * So the trade is no longer "fitted but better"; it is "fitted, and better only
 * if you are willing not to measure slow breathing at all".  It remains
 * excluded, now on evidence as well as on the sourcing rule.  Anyone quoting
 * these figures should re-measure first.
 * ------------------------------------------------------------------------- */

#endif /* FILTER_BANDS_H */
