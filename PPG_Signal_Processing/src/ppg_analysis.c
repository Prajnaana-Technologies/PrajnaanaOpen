/*
 * Copyright (c) 2026 Prajnaana Technologies Pvt. Ltd.
 *
 * SPDX-License-Identifier: Apache-2.0
 *
 * Original Author: Mamatha BV
 */
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <math.h>

#include "ppg_common.h"
#include "ppg_fiducial.h"

extern  int32_t init_chebyshev_filter (int32_t fs_hz);
extern  void    bandpass_filter (double *x, int n, biquad_bp *ps_bp_filter);
extern  void    detrend (double* x, int n);
extern  void    welch_psd (const double *x, double *psd_out,
                           uint32_t n_pts, uint32_t seg_pts, uint32_t overlap,
                           int k_lo, int k_hi);
extern  double  estimate_rr_peak_bin (const double *psd, int k_lo, int k_hi, double *p_quality);
extern  void    init_bandpass_rr_band (biquad_bp *ps_bp_filter, double fs, double f_lo, double f_hi);
extern  uint32_t extract_breath_intervals (const double *x, int n, double fs,
                                           double min_s, double max_s,
                                           double *p_bbi, uint8_t *p_adj,
                                           uint32_t max_bbi);
extern  uint32_t gate_breath_intervals (double *p_bbi, uint8_t *p_adj, uint32_t cnt,
                                        double period_ms, double *p_mean_ms);
extern  int     compute_rrv (const double *p_bbi, const uint8_t *p_adj, uint32_t cnt,
                             double *p_sd, double *p_rmssd, double *p_cv_pct);
extern  FILE    *fp_rr;

static  double  intp_foot  [RR_MAX_WINDOW_PTS];
static  double  am_signal  [RR_MAX_WINDOW_PTS];
static  double  intp_fm   [RR_MAX_WINDOW_PTS];

/* Band-limited copy of each surrogate, kept UN-windowed so breath timings can
 * be recovered from it for RRV. */
static  double  filt_keep  [INTERPOLATE_INDEX_MAX][RR_MAX_WINDOW_PTS];

static  double  psd [RR_MAX_WINDOW_PTS];

/* Cross-window spectral averaging (RR_PSD_ACCUM_N).
 *
 * psd_ema is the running average the peak search actually reads; psd_undo is
 * the copy taken just before each update, so a window that fails the quality
 * gate can be UNDONE.  A window we decline to report must not be allowed to
 * corrupt the next four either.
 *
 * psd_ema_seg records the segment length the average was built at.  A bin index
 * means a different FREQUENCY at a different segment length, so the average is
 * discarded whenever the window grows during warm-up -- carrying it across
 * would average two different frequency axes together.  Reset per recording by
 * ppg_analysis_init(). */
static  double   psd_ema   [INTERPOLATE_INDEX_MAX][RR_MAX_WELCH_SEG / 2u];
static  double   psd_undo  [INTERPOLATE_INDEX_MAX][RR_MAX_WELCH_SEG / 2u];
static  uint32_t psd_ema_primed;
static  uint32_t psd_ema_seg;

/* Set by compute_rr(): 1 when that surrogate's peak sat on the lowest or
 * highest searchable bin.  Such a peak is TRUNCATED by the band edge rather
 * than resolved inside it -- estimate_rr_peak_bin() already declines to
 * refine it parabolically for exactly that reason -- so it must not become the
 * anchor the other surrogates are judged against, however prominent it looks. */
static  uint32_t rr_edge_pinned [INTERPOLATE_INDEX_MAX];
/* Per-RECORDING, not per-process: ppg_analysis_init() resets them.  They used
 * to be plain file statics, so a second recording in the same process carried
 * the first one's elapsed time and window count into its timestamps. */
static  double  window_duration = 0.0;
static  uint32_t window_count = 0;

/* Set to 1 to restore the original per-window PSD dump on stdout. */
#define RR_DUMP_PSD     0

/**
 * @brief Warn once if the measured heart rate contradicts the declared category.
 *
 * Consistency check, not a classifier: Fleming et al 2011 heart-rate centiles for
 * the declared category.
 *
 * WHY IT IS EVALUATED MORE THAN ONCE.  It used to run only inside
 * rr_select_band(), on the median of the first 16 beats, and never again.  That
 * misses the case it exists for: -s and -r both scale the detector, so a wrong
 * declaration can mis-tune detection in the very way that drags the EARLY beats
 * into the expected range.  Measured: 125 Hz data declared as -r 250 gives first
 * beats at 60-95 bpm (inside the adult band, so the early check passed) while the
 * run settles at a median of 182 bpm -- far outside it, with no warning printed.
 * A log that stays silent there is reporting something untrue by omission.
 *
 * @param ps_ppg  Analysis context
 * @param med_hr  Median heart rate, bpm, over the history available now
 */
static void hr_band_check (struct_ppg_analysis *ps_ppg, uint32_t med_hr)
{
    if (0u != ps_ppg->hr_band_warned) { return; }
    if ((med_hr < ps_ppg->sel_hr_min_bpm) || (med_hr > ps_ppg->sel_hr_max_bpm))
    {
        ps_ppg->hr_band_warned = 1u;
        printf("RR band: ** WARNING ** measured median HR %u bpm is outside "
               "the %u-%u bpm expected for %s.\n"
               "          Check the declared subject band -- the wrong band\n"
               "          is the largest single source of RR error.\n",
               med_hr, ps_ppg->sel_hr_min_bpm, ps_ppg->sel_hr_max_bpm,
               ps_ppg->sel_subject_name);
    }
}

/**
 * @brief Select and lock the respiratory band, then design the band-pass.
 *
 * The band itself is taken verbatim from the caller's struct_subject_band --
 * it is DECLARED, never inferred from the signal (see filter_bands.h).  The
 * measured heart rate is used only to WARN when it disagrees with the declared
 * category, which is why this waits until enough beats exist to have a median.
 */
static  void    rr_select_band (struct_ppg_analysis *ps_ppg)

{
    uint32_t    med_hr = 0u;
    uint32_t    i, j, n;
    uint32_t    t [RR_HR_HIST_LEN];
    double      intp_fs;

    if (ps_ppg->rr_band_locked) { return; }

    n = ps_ppg->hr_hist_cnt;
    if (n > RR_HR_HIST_LEN) { n = RR_HR_HIST_LEN; }

    if (16u > n)
    {
        /* Not enough beats yet to classify; leave the band alone and retry. */
        return;
    }
    else
    {
        for (i = 0u; i < n; i++) { t[i] = ps_ppg->hr_hist[i]; }
        for (i = 1u; i < n; i++)
        {
            uint32_t v = t[i];
            for (j = i; (j > 0u) && (t[j - 1u] > v); j--) { t[j] = t[j - 1u]; }
            t[j] = v;
        }
        med_hr = t[n / 2u];

        /* The category and its band were printed at start-up, and the bandpass
         * design below prints the edges again as it is built, so nothing is
         * restated here.  What follows is the only new fact at this point: how
         * the measured rate compares with the declared category. */

        /* Consistency check, not a classifier: Fleming et al 2011 heart-rate
         * centiles for the declared category. */
        hr_band_check (ps_ppg, med_hr);
    }

    /* (Re)design all three bandpasses for the chosen band. */
    intp_fs = (double)ps_ppg->sel_fs_hz / (double)ps_ppg->sel_rr_decimation;
    init_bandpass_rr_band (&(ps_ppg->s_intp_peak.bp_filter), intp_fs,
                           (double)ps_ppg->sel_rr_min_bpm / 60.0,
                           (double)ps_ppg->sel_rr_max_bpm / 60.0);
    ps_ppg->s_intp_foot.bp_filter = ps_ppg->s_intp_peak.bp_filter;
    ps_ppg->s_intp_freq.bp_filter = ps_ppg->s_intp_peak.bp_filter;

    if (((double)ps_ppg->sel_rr_max_bpm / 60.0) > (intp_fs / 4.0))
    {
        printf("RR band: ** WARNING ** upper edge %.3f Hz is above fs/4 (%.3f Hz).\n"
               "          The surrogates carry only one true sample per beat, so a\n"
               "          respiratory rate this close to their Nyquist limit cannot\n"
               "          be resolved reliably. Raise RR_INTP_GRID_HZ or expect\n"
               "          low-confidence windows.\n",
               (double)ps_ppg->sel_rr_max_bpm / 60.0, intp_fs / 4.0);
    }
    ps_ppg->rr_band_locked = 1u;
    return;
}


/**
 * @brief Time-domain HRV over the rolling normal-to-normal series.
 *
 * Task Force of the ESC / NASPE 1996: mean NN, SDNN, RMSSD and pNN50.  Only
 * intervals that sanitize_ibi() did NOT repair are in the series, and only
 * genuinely adjacent pairs contribute to RMSSD and pNN50 -- see the block
 * comment on HRV_WINDOW_BEATS for why both rules matter.
 *
 * These are NOT comparable with published 5-minute norms: the series is a
 * rolling window of HRV_WINDOW_BEATS beats, and SDNN is duration-dependent.
 *
 * @return 1 if the metrics are valid, 0 if there is too little data
 */
static int compute_hrv (struct_ppg_analysis *ps_ppg)
{
    double   mean = 0.0, var = 0.0, ssd = 0.0, d;
    uint32_t i, n = ps_ppg->nn_cnt, npair = 0u, nn50 = 0u;
    uint32_t base = (ps_ppg->nn_wr > n) ? (ps_ppg->nn_wr - n) : 0u;

    ps_ppg->hrv_mean_ms   = -1.0;
    ps_ppg->hrv_sdnn_ms   = -1.0;
    ps_ppg->hrv_rmssd_ms  = -1.0;
    ps_ppg->hrv_pnn50_pct = -1.0;
    ps_ppg->hrv_n         = n;
    if (HRV_MIN_INTERVALS > n) { return (0); }

    for (i = 0u; i < n; i++) { mean += (double)ps_ppg->nn_ms[(base + i) % HRV_WINDOW_BEATS]; }
    mean /= (double)n;

    for (i = 0u; i < n; i++)
    {
        d    = (double)ps_ppg->nn_ms[(base + i) % HRV_WINDOW_BEATS] - mean;
        var += d * d;
    }

    for (i = 1u; i < n; i++)
    {
        uint32_t cur = (base + i) % HRV_WINDOW_BEATS;

        if (0u != ps_ppg->nn_adj[cur])
        {
            uint32_t prv = (base + i - 1u) % HRV_WINDOW_BEATS;

            d    = (double)ps_ppg->nn_ms[cur] - (double)ps_ppg->nn_ms[prv];
            ssd += d * d;
            if (fabs(d) > (double)HRV_PNN_THRESHOLD_MS) { nn50++; }
            npair++;
        }
    }

    ps_ppg->hrv_mean_ms = mean;
    ps_ppg->hrv_sdnn_ms = sqrt(var / (double)(n - 1u));
    /* RMSSD and pNN50 rest on ADJACENT pairs, which a repaired beat breaks, so
     * npair can be far below n.  They are held to the same minimum as SDNN, so
     * that a single surviving difference cannot become an RMSSD
     * indistinguishable from one backed by hundreds. */
    if (HRV_MIN_INTERVALS <= npair)
    {
        ps_ppg->hrv_rmssd_ms  = sqrt(ssd / (double)npair);
        ps_ppg->hrv_pnn50_pct = (100.0 * (double)nn50) / (double)npair;
    }
    return (1);
}

/**
 * @brief Estimate respiration rate from one surrogate over the current window.
 *
 * @param ps_ppg Algorithm context
 * @param ps_intp      Surrogate context (owns the window-slide bookkeeping)
 * @param p_data        Surrogate samples (modified in place)
 * @param e_which       Which surrogate, selects the filtered-copy slot
 * @param sig_id       Label for logging
 * @param p_quality    Out: peak prominence (peak power / median in-band power)
 * @return            Respiration rate in breaths/min, or -1.0 if unusable
 */
static  double  compute_rr (struct_ppg_analysis *ps_ppg, struct_intp *ps_intp,
                            double *p_data, enum_interpolate e_which,
                            const char *sig_id, double *p_quality)
{
    double      intp_fs = (double)ps_ppg->sel_fs_hz / (double)ps_ppg->sel_rr_decimation;
    double      f_lo    = (double)ps_ppg->sel_rr_min_bpm / 60.0;
    double      f_hi    = (double)ps_ppg->sel_rr_max_bpm / 60.0;
    double      frac;
    int32_t     k_lo, k_hi;
    int32_t     v_count;
    int32_t     i;

    /* Slide the window: keep the overlap, drop what has been consumed.
     *
     * NOT WHILE THE WINDOW IS STILL GROWING.  During warm-up nothing is
     * discarded, so all three surrogates grow together and reach the full size
     * together.  Sliding whichever one happens to be ahead lets them drift
     * apart, and once they have, the COMMON window never reaches full size
     * again -- the estimator then runs at a short window for the rest of the
     * recording, silently.  This was measured, not imagined. */
    if (0u == ps_ppg->rr_warmup_done)
    {
        v_count = (int32_t)ps_intp->intp_vertex_count;
    }
    else
    {
        v_count = (int32_t)ps_intp->intp_vertex_count - (int32_t)ps_ppg->sel_rr_slide_pts;
        if (0 > v_count) { v_count = 0; }
        /* The COUNTER deliberately keeps advancing past capacity -- see
         * interpolate_vertex(), where it drives window-full and the report
         * cadence, so clamping it would silently move when reports fire.  The
         * BUFFER does not, so the copy is bounded here instead, leaving the
         * count untouched.  On every recording measured this is a no-op: the
         * capacity warning has never fired, including on 480 s records.  The
         * index is now bounded by construction rather than by argument. */
        {
            int32_t copy_n = v_count;
            int32_t room   = (int32_t)INTP_MAVG_BUFF_SIZE
                             - (int32_t)ps_ppg->sel_rr_slide_pts;

            if (0 > room)      { room = 0; }
            if (copy_n > room) { copy_n = room; }
            for (i = 0; i < copy_n; i++)
            {
                ps_intp->mvg_avg_sample[i] =
                    ps_intp->mvg_avg_sample[i + (int32_t)ps_ppg->sel_rr_slide_pts];
            }
        }
    }
    ps_intp->intp_vertex_count = (uint32_t)v_count;

    /* Detrend first so the bandpass is not driven by baseline drift. */
    detrend (p_data, (int)ps_ppg->sel_rr_window_pts);

    /* Zero-phase 4th-order Butterworth HP+LP over the selected band. */
    bandpass_filter (p_data, (int)ps_ppg->sel_rr_window_pts, &(ps_intp->bp_filter));

    /* Keep an UN-windowed copy: the Hamming taper below would corrupt the
     * breath timings that RRV is derived from. */
    for (i = 0; i < (int32_t)ps_ppg->sel_rr_window_pts; i++)
    {
        filt_keep[e_which][i] = p_data[i];
    }

    /* NOTE: no whole-window Hamming taper here.  Welch tapers each SEGMENT
     * internally; tapering twice would just throw away the window edges. */

    /* Bins are now SEGMENT bins -- multiples of intp_fs / sel_rr_welch_seg.
     *
     * DEVELOPER'S IMPROVEMENT (the rounding rule; the band itself is cited).
     * WHY: mapping a band edge to a bin must round SOMEWHERE, and rounding
     * outward silently searches outside the band the caller asked for -- at
     * coarse bin spacing that admitted a bin more than 2 /min below the floor,
     * which is exactly where residual drift lives.  Rounding to the nearest bin
     * honours each edge to within half a bin in either direction, which is the
     * best any bin-limited search can do.
     *
     * Round each edge to the NEAREST bin.  This was floor on the lower edge and
     * ceil on the upper one, i.e. rounded OUTWARD, which searches outside the
     * band the subject category asked for.  At the 125 Hz design point the bin
     * was 3.662 /min at the 256-sample segment then in use, so the adult band
     * of 6-30 was actually searched over
     * 3.66-32.96 -- 2.34 /min below the floor and 2.96 above the ceiling.
     *
     * That is not cosmetic.  Bin 1 alone (3.66 /min) was returned as the RR of
     * windows whose true rate was 20.7 /min: a low-frequency bin the band was
     * explicitly configured to exclude was allowed to win, and once it wins it
     * cannot be outvoted, because it looks like a perfectly clean peak.
     *
     * Nearest rather than strictly inward.  Inward guarantees containment but
     * discards up to a full bin of the requested range, and how much it discards
     * depends on where the edge happens to fall between bins -- which made it
     * almost free for adults (6 -> 7.32) and expensive for the other two
     * categories, whose edges already sit close to a bin boundary:
     *
     *     band            outward (old)      inward            NEAREST
     *     adult   6-30     3.66-32.96         7.32-29.30        7.32-29.30
     *     neonate 22-66   21.97-69.58        25.63-65.92       21.97-65.92
     *     child   11-53   10.99-54.93        14.65-51.27       10.99-51.27
     *
     * Inward would cost the neonatal band its whole 22-25.6 /min range and the
     * child band 11-14.7, for no gain -- both lower edges land within 0.03 /min
     * of a bin already.  Nearest honours every
     * edge to within half a bin in either direction, which is the best any
     * bin-limited search can do, and is what actually removes the failure:
     * bin 1 is 0.64 bins below the adult floor, so nearest excludes it.
     *
     * The parabolic refinement in estimate_rr_peak_bin() cannot widen this: it
     * is skipped when the peak sits on k_lo or k_hi, and bounded to +/-0.5 bin
     * elsewhere, so the returned frequency stays inside [k_lo, k_hi]. */
    /* While the window is still growing, raise the floor to what this segment
     * length can actually resolve (RR_MIN_CYCLES_PER_SEG cycles).  Once full,
     * the DECLARED band is used unchanged -- the adult 4 /min floor is a
     * deliberate 2.18-cycle compromise and must not be overridden here. */
    if (0u == ps_ppg->rr_warmup_done)
    {
        double t_seg = (double)ps_ppg->sel_rr_welch_seg / intp_fs;
        double f_min = RR_MIN_CYCLES_PER_SEG / t_seg;

        if (f_lo < f_min) { f_lo = f_min; }
        if (f_lo >= f_hi) { return (-1.0); }   /* nothing reportable yet */
    }

#if (0 != RR_TRACK_HARMONIC_GUARD)
    /* ---- HARMONIC GUARD -- [ZHANG] sequential fusion --------------------
     *
     * Narrow the search to the neighbourhood of the tracked rate, with a
     * half-width driven by the Kalman covariance: confident tracking narrows
     * it, a disagreement widens it back out.  Applied ONLY where a harmonic of
     * the true rate would itself fall inside the band (2f <= f_hi); above that
     * there is nothing to be confused with.  See ppg_common.h. */
    if ((ps_ppg->rr_track_rr > 0.0) &&
        (ps_ppg->rr_track_count >= 1) &&
        (ps_ppg->rr_track_rr < ((double)ps_ppg->sel_rr_max_bpm / 2.0)))
    {
        double div = ps_ppg->rr_track_p / RR_TRACK_R;
        double lo  = (ps_ppg->rr_track_rr / (1.0 + div)) / 60.0;
        double hi  = (ps_ppg->rr_track_rr * (1.0 + div)) / 60.0;

        if (lo > f_lo) { f_lo = lo; }
        if (hi < f_hi) { f_hi = hi; }
        if (f_lo >= f_hi) { f_lo = f_hi / 2.0; }   /* never invert the band */
    }
#endif

    k_lo = (int32_t)floor (((f_lo * (double)ps_ppg->sel_rr_welch_seg) / intp_fs) + 0.5);
    k_hi = (int32_t)floor (((f_hi * (double)ps_ppg->sel_rr_welch_seg) / intp_fs) + 0.5);
    if (1 > k_lo) { k_lo = 1; }
    if (k_hi > ((int32_t)(ps_ppg->sel_rr_welch_seg / 2u) - 1))
    { k_hi = (int32_t)(ps_ppg->sel_rr_welch_seg / 2u) - 1; }

    /* Averaged periodogram: the estimator change that actually makes AM/BW/FM
     * agree from window to window (see the note in ppg_common.h). */
    welch_psd (p_data, psd, ps_ppg->sel_rr_window_pts,
               ps_ppg->sel_rr_welch_seg, ps_ppg->sel_rr_welch_overlap,
               k_lo, k_hi);

#if RR_DUMP_PSD
    printf("%s", sig_id);
    for (i = k_lo; i <= k_hi; i++) { printf("%.2f,", psd[i]); }
    fputs ("]\n", stdout);
#else
    (void)sig_id;
#endif

    /* ---- Cross-window spectral averaging -----------------------------
     *
     * Save the current average first, so a declined window can be undone, then
     * fold this window in and search the AVERAGE rather than this window
     * alone.  See RR_PSD_ACCUM_N in ppg_common.h for why the spectrum and not
     * the output rate is averaged. */
    {
        int32_t kk;

        if (psd_ema_seg != ps_ppg->sel_rr_welch_seg)
        {
            /* the frequency axis changed under us -- start again */
            memset(psd_ema, 0x00, sizeof(psd_ema));
            psd_ema_primed = 0u;
            psd_ema_seg    = ps_ppg->sel_rr_welch_seg;
        }
        for (kk = k_lo; kk <= k_hi; kk++) { psd_undo[e_which][kk] = psd_ema[e_which][kk]; }

        if (0u == (psd_ema_primed & (1u << (uint32_t)e_which)))
        {
            for (kk = k_lo; kk <= k_hi; kk++) { psd_ema[e_which][kk] = psd[kk]; }
            psd_ema_primed |= (1u << (uint32_t)e_which);
        }
        else
        {
            const double alpha = 1.0 / (double)RR_PSD_ACCUM_N;

            for (kk = k_lo; kk <= k_hi; kk++)
            {
                psd_ema[e_which][kk] = (alpha * psd[kk])
                                     + ((1.0 - alpha) * psd_ema[e_which][kk]);
            }
        }
        for (kk = k_lo; kk <= k_hi; kk++) { psd[kk] = psd_ema[e_which][kk]; }
    }

    frac = estimate_rr_peak_bin (psd, k_lo, k_hi, p_quality);
    if (0.0 > frac) { return (-1.0); }

    rr_edge_pinned[e_which] = ((frac <= ((double)k_lo + 0.01)) ||
                               (frac >= ((double)k_hi - 0.01))) ? 1u : 0u;

    return ((intp_fs * frac * 60.0) / (double)ps_ppg->sel_rr_welch_seg);
}

/**
 * @brief Estimate respiration rate and respiration-rate variability.
 *
 * Runs once per analysis window on all three surrogates (AM / BW / FM), fuses
 * them by CONFIDENCE rather than by blind averaging, and derives RRV from the
 * breath timings of whichever surrogate carried the clearest respiratory peak.
 * The fused value is reported as computed -- no temporal smoothing is applied
 * to it; see the DEVIATES note below.
 *
 * LINEAGE (hybrid; full discussion in docs/DESIGN.md, "RR/RRV lineage").
 * The extraction -> estimation -> fusion structure is Charlton et al 2016
 * (Physiol. Meas. 37:610).  The fusion below combines two of that paper's
 * catalogued techniques: the power-concentration test from FM2 (Lazaro 2015,
 * here RR_MIN_PEAK_PROMINENCE) with the agreement gate and the
 * "otherwise emit nothing" rule from FM1 smart fusion (Karlen 2013, here
 * RR_AGREEMENT_THRESHOLD and the REJECTED method) -- but weighted by quality
 * rather than averaged plainly.
 *
 * NO FM PRIOR.  Liu et al 2020 report FM as the strongest modulation, and a
 * weight favouring FM was built and measured.  It helped on neonatal data and
 * hurt monotonically across the annotated adult reference set, where FM is in
 * fact the WORST surrogate here (reference/estimate median 1.54 against 1.04
 * for BW; FM sits at half the true rate in 29 % of windows).  The prior was
 * therefore removed rather than left disabled.  It is worth revisiting only
 * once FM is computed Liu's way -- see docs/DESIGN.md.
 *
 * DEVIATES: Charlton's temporal smoothing FT1 (exponential,
 * RRi = 0.2*RRest + 0.8*RRi-1) is NOT applied.  A 5-point median was tried in
 * its place and removed: it fed nothing back into the algorithm and measured
 * no better than the value it smoothed.  Each window is reported on its own
 * evidence, and a window that cannot be trusted is declined instead of being
 * averaged into a neighbour.  The analysis window is 65.5 s against Charlton's
 * 32 s -- more frequency resolution, slower updates.
 *
 * NOT IN THE CORPUS: RRV.  Neither paper addresses respiratory-rate
 * variability; the SD/RMSSD/CV reported here are an extension by analogy with
 * time-domain HRV metrics.  Do not cite them as literature-backed.
 */
void    estimate_resp_rate (struct_ppg_analysis *ps_ppg)
{
    struct_intp *ps_intp_peak = &(ps_ppg->s_intp_peak);
    struct_intp *ps_intp_foot = &(ps_ppg->s_intp_foot);
    struct_intp *ps_intp_fm  = &(ps_ppg->s_intp_freq);
    double      intp_fs     = (double)ps_ppg->sel_fs_hz / (double)ps_ppg->sel_rr_decimation;
    double      rr [INTERPOLATE_INDEX_MAX];
    double      qy [INTERPOLATE_INDEX_MAX];
    double      acc [INTERPOLATE_INDEX_MAX];
    double      final_rr = -1.0, sum, wsum, spread = -1.0;
    double      td_rr    = -1.0;
    const char  *method  = "NONE";
    uint32_t    n_used   = 0u;
    int32_t     best     = -1;
    int32_t     i, k;

    /* Wait until all three surrogates hold a full window. */
    /* ---- How much window do we have, and how much may we use? -----------
     *
     * The three surrogates fill from different fiducials, so the honest common
     * window is the SMALLEST of the three counts.  While warming up it grows by
     * doubling; once the subject's full window has been reached the estimator
     * reverts to exactly the shipped behaviour and never shrinks again. */
    {
        uint32_t avail = ps_intp_peak->intp_vertex_count;

        if (ps_intp_foot->intp_vertex_count < avail) { avail = ps_intp_foot->intp_vertex_count; }
        if (ps_intp_fm->intp_vertex_count  < avail) { avail = ps_intp_fm->intp_vertex_count;  }

        if (avail >= ps_ppg->sel_rr_window_full) { ps_ppg->rr_warmup_done = 1u; }

        if (0u != ps_ppg->rr_warmup_done)
        {
            if (avail < ps_ppg->sel_rr_window_full) { return; }
            ps_ppg->sel_rr_window_pts = ps_ppg->sel_rr_window_full;
            ps_ppg->sel_rr_welch_seg  = ps_ppg->sel_rr_welch_seg_full;
        }
        else
        {
            uint32_t p2 = RR_PROG_MIN_PTS;

            if (avail < RR_PROG_MIN_PTS) { return; }
            while (((p2 * 2u) <= avail) && ((p2 * 2u) <= ps_ppg->sel_rr_window_full))
            {
                p2 *= 2u;
            }
            ps_ppg->sel_rr_window_pts = p2;
            /* Resolution before averaging: while the record is short, spend
             * every sample on ONE periodogram.  Splitting it into segments
             * would buy variance reduction at the price of the resolution that
             * is the scarce thing here. */
            ps_ppg->sel_rr_welch_seg  = p2;
        }
        ps_ppg->sel_rr_welch_overlap =
            (ps_ppg->sel_rr_welch_seg * 3u) / 4u;
    }

    /* Band selection depends on measured HR, so it can only run now. */
    rr_select_band (ps_ppg);
    if (0u == ps_ppg->rr_band_locked) { return; }

    for (i = 0; i < (int32_t)ps_ppg->sel_rr_window_pts; i++)
    {
        intp_foot[i] = (double)(ps_intp_foot->mvg_avg_sample[i]);
        am_signal[i] = (double)(ps_intp_peak->mvg_avg_sample[i]);
        intp_fm[i]  = (double)(ps_intp_fm->mvg_avg_sample[i]);
    }

    /* Each surrogate is detrended and z-score normalised by detrend(), which
     * compute_rr() calls before the PSD.  No separate amplitude-matching step is
     * needed ahead of that: it would only rescale data that is about to be
     * normalised anyway. */
    rr[INTERPOLATE_AM] = compute_rr (ps_ppg, ps_intp_peak, am_signal,
                                     INTERPOLATE_AM, "PSD_AM [",  &(qy[INTERPOLATE_AM]));
    rr[INTERPOLATE_BW] = compute_rr (ps_ppg, ps_intp_foot, intp_foot,
                                     INTERPOLATE_BW, "PSD_BW [",  &(qy[INTERPOLATE_BW]));
    rr[INTERPOLATE_FM] = compute_rr (ps_ppg, ps_intp_fm,  intp_fm,
                                     INTERPOLATE_FM, "PSD_IBI [", &(qy[INTERPOLATE_FM]));

    printf(" RR: AM = %.2f (q=%.2f)  BW = %.2f (q=%.2f)  FM = %.2f (q=%.2f)\n",
           rr[INTERPOLATE_AM], qy[INTERPOLATE_AM],
           rr[INTERPOLATE_BW], qy[INTERPOLATE_BW],
           rr[INTERPOLATE_FM], qy[INTERPOLATE_FM]);

    /* ---- Confidence-weighted fusion -------------------------------------
     * A surrogate has to earn its place: it must show a real spectral peak
     * (prominence above RR_MIN_PEAK_PROMINENCE) before it is allowed to vote at
     * all.  Averaging whichever pair happens to lie closest would let two
     * equally wrong estimates outvote one good one.
     */
    for (i = 0; i < INTERPOLATE_INDEX_MAX; i++)
    {
        if ((rr[i] > 0.0) && (qy[i] >= RR_MIN_PEAK_PROMINENCE))
        {
            acc[n_used] = (double)i;
            n_used++;
            if ((best < 0) || (qy[i] > qy[best])) { best = i; }
        }
    }

    if (0u == n_used)
    {
        /* Nothing in this window has a credible respiratory peak.  Emitting a
         * number here is exactly what made the output look erratic; report the
         * window as unusable instead. */
        printf(" ==> NO credible respiratory peak in this window (all q < %.1f) -- REJECTED\n",
               RR_MIN_PEAK_PROMINENCE);
        method = "REJECTED";

        /* RRV is derived from the winning surrogate's breath timings.  With no
         * winning surrogate there are none, so the fields are cleared here.
         * Leaving them alone would carry the PREVIOUS window's values into a row
         * whose AVG_RR is -1, presenting stale variability beside a fresh
         * rejection -- which reads as a measurement rather than the absence of
         * one. */
        ps_ppg->bbi_cnt      = 0u;
        ps_ppg->rrv_sd_ms    = RRV_NOT_REPORTABLE;
        ps_ppg->rrv_rmssd_ms = RRV_NOT_REPORTABLE;
        ps_ppg->rrv_cv_pct   = RRV_NOT_REPORTABLE;
    }
    else
    {
        /* ---- Time-domain estimate, Charlton et al 2016 techniques ET1..5 ----
         * Charlton PH, Bonnici T, Tarassenko L, Clifton DA, Beale R, Watkinson
         * PJ, "An assessment of algorithms to estimate respiratory rate from the
         * electrocardiogram and photoplethysmogram", Physiol. Meas. 37:610-626
         * (2016).  Frequency-domain techniques (EF1..7) take a spectral peak --
         * that is what rr[] above holds.  Time-domain techniques (ET1..5)
         * instead compute "the mean breath duration" from detected breaths.
         *
         * The breath timings are already extracted here for RRV, so this costs
         * one division and NOTHING in the per-sample path.
         *
         * WHY IT IS WORTH HAVING ALONGSIDE A GOOD SPECTRAL ESTIMATE.  The two
         * answer different questions, and one error class is structurally
         * impossible in the time domain: to report HALF the true rate by
         * counting breaths you would have to miss every second breath, whereas
         * a periodogram finds a half-rate peak whenever the modulation depth
         * alternates slightly or a 1/f skirt reaches into the band.  Measured:
         * 0 % sub-harmonic reports from the time-domain estimate against 13-15 %
         * from the fusion.  The DFT also assumes the rate is CONSTANT across
         * the window, which over 65.5 s it is not -- that variation is exactly
         * what RRV measures -- while interval counting handles it natively.
         * Their errors correlate at only 0.64, i.e. they fail on different
         * windows, which is what makes the pairing worth anything.
         *
         * It still does NOT vote in the fusion.  Adding it as a fourth vote was
         * built and measured: it gained almost nothing on adults (3.39 -> 3.36,
         * the agreement gate admitting it in only 18 of 150 windows -- exactly
         * those where it already agreed) and it degraded the neonatal pair,
         * where it runs ~20 % high (err 5.80/5.99 vs 2.19/1.42 spectral) because
         * fewer samples per breath means more spurious crossings.  Its only
         * influence is the sub-harmonic rescue below, which fires on the 2:1
         * signature alone -- 10 of 135 adult windows, and NEVER on the neonatal
         * pair, so that weakness is not reintroduced. */
        ps_ppg->bbi_cnt = extract_breath_intervals (
                                  filt_keep[best], (int)ps_ppg->sel_rr_window_pts, intp_fs,
                                  60.0 / (double)ps_ppg->sel_rr_max_bpm,
                                  60.0 / (double)ps_ppg->sel_rr_min_bpm,
                                  ps_ppg->bbi_ms, ps_ppg->bbi_adj,
                                  RRV_MAX_BREATHS);
        if (2u <= ps_ppg->bbi_cnt)
        {
            double   bsum = 0.0;
            uint32_t b;

            for (b = 0u; b < ps_ppg->bbi_cnt; b++) { bsum += ps_ppg->bbi_ms[b]; }
            if (bsum > 0.0)
            {
                td_rr = 60000.0 / (bsum / (double)ps_ppg->bbi_cnt);
            }
        }
        /* Average the accepted surrogates that agree with the best one.
         *
         * DEVELOPER'S IMPROVEMENT.  Charlton's architecture fuses across
         * modulations but does not specify an agreement gate; weighting by
         * spectral prominence and excluding surrogates that disagree with the
         * most prominent one is ours.  WHY: the three surrogates fail
         * independently, so a surrogate that has locked onto a wrong peak must
         * not be allowed to drag the average -- and a simple mean lets it. */
        /* ---- Do not anchor on a band-edge peak -------------------------
         *
         * DEVELOPER'S IMPROVEMENT.  The anchor is the most PROMINENT surrogate,
         * and prominence is not correctness: a peak pinned to the lowest
         * searchable bin is the truncated drift skirt, and it can be several
         * times more prominent than a real respiratory peak.  Measured on a
         * recording whose true rate was 15.3 /min: one surrogate sat on the
         * band-edge bin at 5.49 /min with prominence 15.0 against 4.4 and 5.3
         * for the two that were reading 15.7 and 16.0 correctly.  Anchoring on
         * it put both of those outside the agreement threshold, so the window
         * was declined for "no corroboration" -- and the recording reported
         * nothing for its first 69 s while two surrogates agreed to within
         * 0.24 /min throughout.
         *
         * An edge-pinned surrogate may still JOIN the average if it agrees; it
         * may not be the thing agreement is measured against.  If every
         * candidate is edge-pinned the original choice stands, because then
         * there is nothing better to anchor on. */
        {
            int32_t cand = -1, ci;

            for (ci = 0; ci < (int32_t)n_used; ci++)
            {
                int32_t ai = (int32_t)acc[ci];

                if (0u != rr_edge_pinned[ai]) { continue; }
                if ((cand < 0) || (qy[ai] > qy[cand])) { cand = ai; }
            }
            if (cand >= 0) { best = cand; }
        }

        sum = 0.0; wsum = 0.0; k = 0;
        for (i = 0; i < (int32_t)n_used; i++)
        {
            int32_t idx = (int32_t)acc[i];
            if (fabs(rr[idx] - rr[best]) <= RR_AGREEMENT_THRESHOLD)
            {
                sum  += rr[idx] * qy[idx];
                wsum += qy[idx];
                k++;
            }
        }
        final_rr = (wsum > 0.0) ? (sum / wsum) : rr[best];
        /* Only k >= 2 can survive: the quality gate immediately below declines
         * every window where fewer than two surrogates corroborated, so there
         * is no "one surrogate was enough" outcome to name here. */
        method   = (k >= 3) ? "ALL_THREE" : "TWO_AGREE";

        /* ---- QUALITY GATE -- Karlen et al 2013 smart fusion --------------
         *
         * "If their standard deviation is <= 4 bpm then RR is estimated as the
         * mean, otherwise no RR is output."  Two conditions decline a window:
         *
         *   k < 2         only ONE surrogate agreed with the most prominent
         *                 one.  There is no corroboration, and these are the
         *                 worst rows in the output by a wide margin -- MAE
         *                 5.95 and 31 % sub-harmonic locking, against 0.40 and
         *                 0.3 % when all three agree.
         *   spread > 4    the three disagree by more than Karlen's threshold.
         *
         * A DECLINED window is reported as such rather than replaced by a
         * guess.  It must also NOT enter the spectral accumulator: the whole
         * point of staging the spectrum is that a window we would not report
         * cannot corrupt the next four either. */
        {
            double   m2 = 0.0, v2 = 0.0;
            int32_t  nv2 = 0, j2;


            for (j2 = 0; j2 < (int32_t)INTERPOLATE_INDEX_MAX; j2++)
            {
                if (rr[j2] > 0.0) { m2 += rr[j2]; nv2++; }
            }
            if (nv2 > 1)
            {
                m2 /= (double)nv2;
                for (j2 = 0; j2 < (int32_t)INTERPOLATE_INDEX_MAX; j2++)
                {
                    if (rr[j2] > 0.0) { v2 += (rr[j2] - m2) * (rr[j2] - m2); }
                }
                v2 = sqrt (v2 / (double)nv2);
            }
            /* The gate applies to SETTLED rows only.  A warm-up row is
             * already marked provisional and is understood to rest on less
             * data; declining it as well would delay the first number to well
             * past the point of usefulness -- measured, the first valid report
             * slipped from ~17 s to a median of 25.9 s and as late as 82.8 s.
             * Provisional says "do not trust this yet"; declining says "there
             * is nothing here", and during warm-up the first is the truth. */
            /* Two conditions, applied differently.
             *
             * k < 2 -- no corroboration -- is a statement about the EVIDENCE,
             * not about how much data there is, so it declines at any stage.
             * Measured on warm-up rows alone: MAE 6.20 and 40 % sub-harmonic
             * locking, so these are not merely uncertain, they are wrong.
             *
             * The spread threshold applies only once settled: a warm-up row is
             * already marked provisional and declining it as well would push
             * the first number past the point of usefulness. */
            double spread_limit = RR_SPREAD_LIMIT(ps_ppg->sel_rr_min_bpm,
                                                  ps_ppg->sel_rr_max_bpm);

            if ((k < 2) ||
                ((0u != ps_ppg->rr_warmup_done) &&
                 (nv2 > 1) && (v2 > spread_limit)))
            {
                printf(" ==> DECLINED: %s, surrogate spread %.2f /min "
                       "(limit %.1f) -- no rate reported for this window\n",
                       (k < 2) ? "only one surrogate corroborated" : "surrogates disagree",
                       v2, spread_limit);
                method   = "DECLINED";
                final_rr = -1.0;
                /* Undo this window's contribution to the running average. */
                memcpy(psd_ema, psd_undo, sizeof(psd_ema));
            }
        }

        /* ---- SUB-HARMONIC RESCUE ---------------------------------------
         *
         * DEVELOPER'S IMPROVEMENT -- not from any paper.  Charlton et al 2016
         * describes frequency- and time-domain estimators as alternatives; using
         * one to police the other is ours.
         *
         * WHY: the dominant error of a spectral respiratory estimate is not a
         * bias but a discrete HALF-RATE lock, and a lock cannot be averaged
         * away -- it looks like a clean peak.  A breath COUNT cannot report half
         * a rate (you would have to miss every second breath), so it is the one
         * available estimate that is structurally immune to this failure and can
         * be used as a witness against it.
         * The dominant error of the fused estimate is not a bias but a
         * half-rate lock.  Measured over 151 windows on the 12 annotated
         * recordings: 85 % of windows carry bias -0.22 and MAE 0.93, while
         * 15 % report close to HALF the true rate with a mean error of -10.74
         * -- those alone accounting for -1.57 of the -1.76 overall bias.  It
         * is a discrete failure to be caught, not an offset to correct.
         *
         * It is not physiology: reference breath intervals in the failing
         * windows are as regular as everywhere else (interval CV 0.082 vs
         * 0.086) and show LESS long/short alternation (32 % vs 46 %), so there
         * is no real energy at half the breathing rate to have found.
         *
         * The fusion cannot catch it alone because AM and BW are both
         * AMPLITUDE surrogates of the same waveform -- baseline wander
         * corrupts them TOGETHER, so when they lock onto the sub-harmonic they
         * agree, and a majority vote reads that agreement as confirmation
         * rather than as one piece of evidence counted twice.  FM, the only
         * timing-derived surrogate, is frequently right in those windows and
         * is outvoted.
         *
         * TOLERANCE IS DERIVED, NOT TUNED: one Welch bin.  Two estimates
         * separated by less than the estimator's own resolution are
         * indistinguishable from an exact 2:1 ratio, and it rescales with the
         * sampling rate and the Welch segment automatically.  Widening to 2 bins
         * was measured -- MAE 1.74 -> 1.59, but firing on 27 windows instead
         * of 10 and costing within-2 accuracy (82 % -> 78 %).  That is a
         * threshold chasing one dataset, so it is not taken. */
        /* NOT DURING WARM-UP.  Both rescues compare the spectral estimate with
         * the breath count, and that comparison assumes the fundamental was
         * inside the searched band.  While the window is short the floor has
         * been raised above the subject's declared one, so the fundamental may
         * be excluded by construction and the ratio test then fires on a rate
         * the search could never have found.  Measured on warm-up rows: MAE
         * 6.26 and 43 % sub-harmonic locking when the rescue was allowed to
         * run there. */
        if ((td_rr > 0.0) && (final_rr > 0.0) && (0u != ps_ppg->rr_warmup_done))
        {
            double bin_bpm = (intp_fs * 60.0) / (double)ps_ppg->sel_rr_welch_seg;

            if (fabs (td_rr - (2.0 * final_rr)) <= bin_bpm)
            {
                final_rr = td_rr;
                method   = "TD_SUBHARM";
            }
            else if (final_rr >= (RR_HARMONIC_RATIO * td_rr))
            {
                /* MIRROR CASE.  A non-sinusoidal breath carries energy at 2f as
                 * well as f.  When the fundamental sits near the band-pass
                 * corner it is attenuated while 2f sits mid-band, so the
                 * spectrum can lock onto the HARMONIC and report double.  This
                 * only becomes reachable once the band floor is low enough to
                 * admit slow breathing; at a 6/min floor the fundamental is
                 * outside the search entirely and there is nothing to recover.
                 *
                 * The test is a RATIO, not a 2:1 match, because the lock is not
                 * confined to the second harmonic.  Measured on a slow-breathing
                 * true rate is 5.3-6.6 /min, the surrogates reported 12-27 --
                 * the 2nd, 3rd and 4th harmonics -- while TD_RR read 5.9-9.0.
                 * A guard that only recognised 2:1 fired on 0 of those windows.
                 *
                 * RR_HARMONIC_RATIO is 1.8 rather than 2.0 so that a harmonic
                 * lock is still caught when the fused value has been pulled
                 * slightly below an exact multiple by the agreement average.
                 * Swept: 1.6, 1.8 and 2.2 all give the same result on this
                 * cohort (MAE 0.84-0.89), so it is not a sharp threshold. */
                final_rr = td_rr;
                method   = "TD_HARM";
            }
        }

        printf(" ==> fused RR = %.2f /min from %d estimate(s) (%s, best=%s q=%.2f, "
               "time-domain=%.2f)\n",
               final_rr, k, method,
               (best == INTERPOLATE_AM) ? "AM" : ((best == INTERPOLATE_BW) ? "BW" : "FM"),
               qy[best], td_rr);

        /* ---- RRV from the same breath timings extracted above -----------
         *
         * The rate is final at this point (fusion and both rescues have run),
         * so the interval series can now be narrowed to the rhythm the row
         * actually reports.  Everything above -- td_rr, TD_SUBHARM, TD_HARM --
         * has already been computed from the UNGATED series and is unaffected;
         * only the variability figures change. */
        {
            double   gated_mean_ms = 0.0;
            double   bin_bpm       = (intp_fs * 60.0) / (double)ps_ppg->sel_rr_welch_seg;

            ps_ppg->bbi_cnt = gate_breath_intervals (
                                      ps_ppg->bbi_ms, ps_ppg->bbi_adj,
                                      ps_ppg->bbi_cnt,
                                      (final_rr > 0.0) ? (60000.0 / final_rr) : 0.0,
                                      &gated_mean_ms);

            if (0 == compute_rrv (ps_ppg->bbi_ms, ps_ppg->bbi_adj,
                                  ps_ppg->bbi_cnt,
                                  &(ps_ppg->rrv_sd_ms), &(ps_ppg->rrv_rmssd_ms),
                                  &(ps_ppg->rrv_cv_pct)))
            {
                printf(" ==> RRV: only %u usable breath interval(s) in this window"
                       " -- not reported\n", ps_ppg->bbi_cnt);
                ps_ppg->rrv_sd_ms    = RRV_NOT_REPORTABLE;
                ps_ppg->rrv_rmssd_ms = RRV_NOT_REPORTABLE;
                ps_ppg->rrv_cv_pct   = RRV_NOT_REPORTABLE;
            }
            else if ((gated_mean_ms <= 0.0) ||
                     (fabs ((60000.0 / gated_mean_ms) - final_rr) > bin_bpm))
            {
                /* The surviving intervals average to a rate the row does not
                 * report.  Emitting their spread here would attach a
                 * variability figure to a rhythm that is not the reported one,
                 * so the row declines instead. */
                printf(" ==> RRV: interval mean %.2f /min disagrees with reported"
                       " %.2f /min -- not reported\n",
                       (gated_mean_ms > 0.0) ? (60000.0 / gated_mean_ms) : 0.0,
                       final_rr);
                ps_ppg->rrv_sd_ms    = RRV_NOT_REPORTABLE;
                ps_ppg->rrv_rmssd_ms = RRV_NOT_REPORTABLE;
                ps_ppg->rrv_cv_pct   = RRV_NOT_REPORTABLE;
            }
            else if (ps_ppg->rrv_rmssd_ms < 0.0)
            {
                /* SD and CV are measured, but no two breath intervals in this
                 * window were adjacent, so RMSSD alone has nothing behind it.
                 * Say so rather than printing the sentinel as if it were a
                 * measurement. */
                printf(" ==> RRV over %u intervals: SD = %.1f ms"
                       "  RMSSD = not available (no adjacent interval pair)"
                       "  CV = %.1f %%\n",
                       ps_ppg->bbi_cnt, ps_ppg->rrv_sd_ms, ps_ppg->rrv_cv_pct);
            }
            else
            {
                printf(" ==> RRV over %u intervals: SD = %.1f ms  RMSSD = %.1f ms"
                       "  CV = %.1f %%\n",
                       ps_ppg->bbi_cnt, ps_ppg->rrv_sd_ms,
                       ps_ppg->rrv_rmssd_ms, ps_ppg->rrv_cv_pct);
            }
        }
    }

    /* ---- Inter-surrogate spread = an honest per-window uncertainty ------
     * AM, BW and FM measure genuinely different physiology and need not agree.
     * Welch averaging removed the ESTIMATOR variance that used to dominate this
     * number, so what remains is largely real: a wide spread means the subject's
     * respiratory content is multi-modal (common in neonates) or one surrogate
     * is weak.  Report it rather than hide it. */
    {
        double m = 0.0, v = 0.0; int32_t nv = 0;
        for (i = 0; i < INTERPOLATE_INDEX_MAX; i++)
        {
            if (rr[i] > 0.0) { m += rr[i]; nv++; }
        }
        if (1 < nv)
        {
            m /= (double)nv;
            for (i = 0; i < INTERPOLATE_INDEX_MAX; i++)
            {
                if (rr[i] > 0.0) { v += (rr[i] - m) * (rr[i] - m); }
            }
            spread = sqrt(v / (double)nv);
        }
    }

    /* ---- Window bookkeeping and logging ------------------------------- */
#if (0 != RR_TRACK_HARMONIC_GUARD)
    /* ---- Update the tracker -------------------------------------------
     *
     * DECLINED windows set final_rr to -1 and so never update it: a window we
     * would not report must not steer the search for the next one either.
     * Warm-up rows DO update it -- they are the only thing available early,
     * and withholding them would leave the guard disengaged exactly while the
     * estimate is least settled. */
    if (final_rr > 0.0)
    {
        double p_pred = ps_ppg->rr_track_p + RR_TRACK_Q;
        double gain   = p_pred / (p_pred + RR_TRACK_R);

        if (0.0 >= ps_ppg->rr_track_rr)
        {
            ps_ppg->rr_track_rr    = final_rr;
            ps_ppg->rr_track_p     = p_pred;
            ps_ppg->rr_track_count = 1;
        }
        else
        {
            if (fabs (final_rr - ps_ppg->rr_track_rr) <= RR_TRACK_AGREE_BPM)
            {
                if (RR_TRACK_COUNT_MAX > ps_ppg->rr_track_count)
                {
                    ps_ppg->rr_track_count++;
                }
            }
            else
            {
                ps_ppg->rr_track_count--;
            }
            /* Lost track: throw the covariance wide open so the next window
             * searches the whole band again rather than hunting near a rate
             * that is no longer the subject's. */
            ps_ppg->rr_track_p = (1 > ps_ppg->rr_track_count)
                                       ? RR_TRACK_P_LOST
                                       : ((1.0 - gain) * p_pred);
            ps_ppg->rr_track_rr += gain * (final_rr - ps_ppg->rr_track_rr);
        }
    }
#endif

    /* ---- Mark warm-up rows PROVISIONAL ---------------------------------
     *
     * A row emitted before the window is full is not equivalent to a settled
     * one: it rests on less data, and its band floor has been raised to what
     * that data can resolve (RR_MIN_CYCLES_PER_SEG), so a slow rate is outside
     * what it could have reported at all.  Consumers must be able to tell the
     * two apart, and the CSV column set is settled -- so the distinction goes in the
     * Method column, whose vocabulary is already a set of names. */
    {
        static char method_buf [24];

        if ((0u == ps_ppg->rr_warmup_done) && (final_rr > 0.0))
        {
            (void)snprintf (method_buf, sizeof(method_buf), "%s_PROV", method);
            method = method_buf;
        }
    }

    /* TRUE stream position, not a running sum of slide increments.  The report
     * cadence changes when warm-up ends, and an accumulated timestamp drifts
     * the moment it does -- measured reaching 1286 s on a 480 s recording. */
    window_duration = (double)ps_ppg->s_intp_peak.cur_intp_sample_index
                      / (double)ps_ppg->sel_fs_hz;
    window_count++;

    (void)compute_hrv (ps_ppg);

    /* Re-run the category sanity check on the settled history.  rr_select_band()
     * ran it once on the first 16 beats; by now there are far more, and a wrong
     * -s or -r that flattered those early beats shows up here.  hr_band_check()
     * warns at most once per recording. */
    {
        uint32_t hn = ps_ppg->hr_hist_cnt;
        uint32_t hi_, hj, med;
        uint32_t tmp [RR_HR_HIST_LEN];

        if (hn > RR_HR_HIST_LEN) { hn = RR_HR_HIST_LEN; }
        if (16u <= hn)
        {
            for (hi_ = 0u; hi_ < hn; hi_++) { tmp[hi_] = ps_ppg->hr_hist[hi_]; }
            for (hi_ = 1u; hi_ < hn; hi_++)
            {
                uint32_t v = tmp[hi_];
                for (hj = hi_; (hj > 0u) && (tmp[hj - 1u] > v); hj--) { tmp[hj] = tmp[hj - 1u]; }
                tmp[hj] = v;
            }
            med = tmp[hn / 2u];
            hr_band_check (ps_ppg, med);
        }
    }

    printf(" ~~~~~~~~ t = %.1f s : RR = %.2f /min | HR = %u bpm | "
           "HRV SDNN %.1f RMSSD %.1f pNN50 %.1f%% (n=%u) ~~~~~~~~\n",
           window_duration, final_rr, ps_ppg->hr_bpm,
           ps_ppg->hrv_sdnn_ms, ps_ppg->hrv_rmssd_ms,
           ps_ppg->hrv_pnn50_pct, ps_ppg->hrv_n);

    if (NULL == fp_rr) { return; }
    fprintf(fp_rr, "%.3f,%.3f,%.3f,%.3f,%.3f,%.3f,%.3f,%.3f,%.3f,%s,%u,%.3f,%.3f,%.3f,%u,"
                   "%u,%.3f,%.3f,%.3f,%.3f,%u\n",
            window_duration,
            rr[INTERPOLATE_AM], rr[INTERPOLATE_BW], rr[INTERPOLATE_FM],
            qy[INTERPOLATE_AM], qy[INTERPOLATE_BW], qy[INTERPOLATE_FM],
            td_rr, final_rr, method, n_used, spread,
            ps_ppg->rrv_sd_ms, ps_ppg->rrv_rmssd_ms, ps_ppg->bbi_cnt,
            ps_ppg->hr_bpm, ps_ppg->hrv_mean_ms, ps_ppg->hrv_sdnn_ms,
            ps_ppg->hrv_rmssd_ms, ps_ppg->hrv_pnn50_pct, ps_ppg->hrv_n);
    fflush(fp_rr);
    return;
}

/**
 * @brief Sanitize IBI value to remove outliers and artifacts.
 *
 * Three tiers, in order:
 *
 *   1. PLAUSIBILITY ENVELOPE.  Outside HR_PLAUSIBLE_MIN/MAX_BPM no heart could
 *      have produced the interval, so it is rejected outright.  Same for every
 *      category; never widens.
 *   2. CATEGORY PRIOR.  Inside the envelope but outside the declared category's
 *      band, the interval is rejected too -- UNLESS it is part of a sustained,
 *      self-consistent run, in which case the prior is overruled (see below).
 *   3. ADAPTIVE OUTLIER TEST.  After IBI_CALIBRATION_BEATS, an
 *      IBI_OUTLIER_TOLERANCE_PCT window around the running mean, repairing
 *      missed beats (~2x expected) and false peaks (~0.5x).
 *
 * The tier-2 prior yields to IBI_CALIBRATION_BEATS consecutive out-of-category
 * intervals that agree to IBI_OUTLIER_TOLERANCE_PCT AND that the signal's own
 * spectrum confirms (fiducial_period_is_fundamental()).  Rationale, measurements
 * and the discriminators that did NOT work: docs/DESIGN.md, "The category band
 * is a prior, not a gate".
 *
 * @param ps_ppg Algorithm context (holds the sanitiser state)
 * @param ibi_ms       Current IBI in milliseconds
 * @param quality_flag Signal quality indicator (1=good, 0=poor)
 * @param p_repaired   IBI_REPAIR_NONE, _SUBSTITUTED or _SPLIT -- see the
 *                     classes in ppg_common.h.  Any non-zero value excludes
 *                     the interval from the HRV NN series as before.
 * @return Sanitized IBI value in milliseconds
 */
static uint32_t sanitize_ibi(struct_ppg_analysis *ps_ppg, uint32_t ibi_ms, int quality_flag,
                             uint32_t *p_repaired)
{
    /* State lives in the context so ppg_analysis_init() resets it; the
     * limits come from the declared subject category (filter_bands.h), so a
     * neonate is no longer judged against adult-ICU bounds. */
    uint32_t last_valid_ibi   = ps_ppg->ibi_last_valid_ms;
    uint32_t running_mean_ibi = ps_ppg->ibi_running_mean_ms;
    uint32_t ibi_history_count = ps_ppg->ibi_history_count;
    uint32_t sanitized_ibi = ibi_ms;
    /* The gate applied this beat: the prior until overruled, then the envelope. */
    uint32_t gate_min_ms = (0u != ps_ppg->ibi_reanchored)
                           ? ps_ppg->sel_ibi_floor_ms : ps_ppg->sel_ibi_min_ms;
    uint32_t gate_max_ms = (0u != ps_ppg->ibi_reanchored)
                           ? ps_ppg->sel_ibi_ceil_ms  : ps_ppg->sel_ibi_max_ms;

    /* HRV needs to know whether this interval was REPAIRED: a substituted value
     * must not enter the NN series (Task Force -- see HRV_WINDOW_BEATS). */
    if (NULL != p_repaired) { *p_repaired = IBI_REPAIR_NONE; }

    /* ---- Out of category, but not out of physiology ------------------------
     * Track the run; the gate below still rejects this interval as usual.  The
     * run only decides how the NEXT beat is judged. */
    if ((0u == ps_ppg->ibi_reanchored) &&
        ((ibi_ms < gate_min_ms) || (ibi_ms > gate_max_ms)) &&
        (ibi_ms >= ps_ppg->sel_ibi_floor_ms) &&
        (ibi_ms <= ps_ppg->sel_ibi_ceil_ms))
    {
        uint32_t prev = ps_ppg->ibi_run_ms;
        uint32_t tol  = (prev / 100u) * IBI_OUTLIER_TOLERANCE_PCT;

        /* A member that disagrees with the one before it starts a fresh run --
         * this is what stops scattered artefacts from ever accumulating. */
        if ((0u != ps_ppg->ibi_run_len) &&
            ((ibi_ms + tol < prev) || (ibi_ms > prev + tol)))
        {
            ps_ppg->ibi_run_len = 0u;
        }
        ps_ppg->ibi_run_ms = ibi_ms;
        ps_ppg->ibi_run_len++;

        if (IBI_CALIBRATION_BEATS <= ps_ppg->ibi_run_len)
        {
            /* Intervals cannot tell a doubled train from a faster heart; ask
             * the signal before believing the run. */
            if (0u == fiducial_period_is_fundamental (
                          (const struct_fiducial *)ps_ppg->ps_fiducial, ibi_ms))
            {
                printf("sanitize_ibi(): *** RE-ANCHOR DEFERRED: %u ms (%u bpm) not confirmed "
                       "by the signal -- either the train is doubled, or too little\n"
                       "          signal has been buffered to tell yet.\n",
                       ibi_ms, (0u < ibi_ms) ? (60000u / ibi_ms) : 0u);
                /* Run deliberately NOT reset -- it is still valid evidence; only
                 * the signal's answer is missing.  Resetting costs MAE 7.0 -> 8.3. */
            }
            else
            {
            /* Prior contradicted for as long as start-up itself takes, and the
             * signal agrees.  Hand the rate to tier 3; stop applying the band. */
            printf("sanitize_ibi(): *** IBI RE-ANCHOR: %u consecutive intervals near "
                   "%u ms (%u bpm) lie outside the %s band of %u-%u ms.\n"
                   "          The declared subject category does not match this patient;\n"
                   "          re-anchoring to the measured rate.  Only the %u-%u ms\n"
                   "          physiological envelope applies from here.\n",
                   ps_ppg->ibi_run_len, ibi_ms,
                   (0u < ibi_ms) ? (60000u / ibi_ms) : 0u,
                   ps_ppg->sel_subject_name,
                   ps_ppg->sel_ibi_min_ms, ps_ppg->sel_ibi_max_ms,
                   ps_ppg->sel_ibi_floor_ms, ps_ppg->sel_ibi_ceil_ms);

            /* Old running mean was built from substitutions and is known-corrupt;
             * seed from the evidence and mark calibration complete. */
            ps_ppg->ibi_reanchored   = 1u;
            ps_ppg->ibi_run_len      = 0u;
            ps_ppg->ibi_last_valid_ms   = ibi_ms;
            ps_ppg->ibi_running_mean_ms = ibi_ms;
            ps_ppg->ibi_history_count   = IBI_CALIBRATION_BEATS;
            return (ibi_ms);
            }
        }
    }
    else
    {
        ps_ppg->ibi_run_len = 0u;
    }

    /* ---- Tier 1/2 gate ---------------------------------------------------- */
    if ((ibi_ms < gate_min_ms) || (ibi_ms > gate_max_ms)) {
        printf("sanitize_ibi(): *** EXTREME IBI: %u ms (limits: %u-%u), replacing with %u\n",
               ibi_ms, gate_min_ms, gate_max_ms, last_valid_ibi);
        sanitized_ibi = last_valid_ibi;
        if (NULL != p_repaired) { *p_repaired = IBI_REPAIR_SUBSTITUTED; }
    }
    else if (ibi_history_count < IBI_CALIBRATION_BEATS) {
        // Calibration phase - accumulate running mean
        running_mean_ibi = (running_mean_ibi * ibi_history_count + ibi_ms) / (ibi_history_count + 1);
        ibi_history_count++;
        last_valid_ibi = ibi_ms;
        printf("sanitize_ibi(): IBI calibration = %u ms (count=%u, running_mean=%u)\n",
               ibi_ms, ibi_history_count, running_mean_ibi);
    }
    else {
        /* Past calibration: judge each interval against the running mean, with a
         * tolerance band wide enough not to reject normal beat-to-beat variation. */
        uint32_t outlier_tolerance = (running_mean_ibi * IBI_OUTLIER_TOLERANCE_PCT) / 100u;
        uint32_t expected_min = running_mean_ibi - outlier_tolerance;
        uint32_t expected_max = running_mean_ibi + outlier_tolerance;

        if (ibi_ms < expected_min || ibi_ms > expected_max) {
            /* Outlier.  First ask whether it is a whole missed beat -- an IBI at
             * about twice the expected one -- before treating it as noise. */
            if (ibi_ms > running_mean_ibi * 17 / 10 && ibi_ms < running_mean_ibi * 23 / 10) {
                printf("sanitize_ibi(): *** MISSED BEAT: %u ms (~2x expected %u), splitting interval\n",
                       ibi_ms, running_mean_ibi);
                /* Split by the actual multiple, not always by 2: a 3x or 4x
                 * gap used to fall through to the generic outlier branch and be
                 * replaced wholesale or kept as "arrhythmia". */
                {
                    uint32_t mult = (running_mean_ibi > 0u)
                                    ? ((ibi_ms + (running_mean_ibi / 2u)) / running_mean_ibi)
                                    : 2u;
                    if (2u > mult) { mult = 2u; }
                    if (MAX_MISSED_BEATS < mult) { mult = MAX_MISSED_BEATS; }
                    sanitized_ibi = ibi_ms / mult;
                }
                if (NULL != p_repaired) { *p_repaired = IBI_REPAIR_SPLIT; }
            }
            // Check if this is a false detection (IBI ~ 0.5x expected)
            else if (ibi_ms < running_mean_ibi * 6 / 10 && ibi_ms > running_mean_ibi * 4 / 10) {
                printf("sanitize_ibi(): *** FALSE PEAK: %u ms (~0.5x expected %u), using previous %u\n",
                       ibi_ms, running_mean_ibi, last_valid_ibi);
                sanitized_ibi = last_valid_ibi;
                if (NULL != p_repaired) { *p_repaired = IBI_REPAIR_LOCAL; }
            }
            // Signal quality check for other outliers
            else if (quality_flag == 0) {
                // Poor signal quality + outlier = likely artifact
                printf("sanitize_ibi(): *** LOW QUALITY OUTLIER: %u ms (expected %u-%u), replacing with %u\n",
                       ibi_ms, expected_min, expected_max, last_valid_ibi);
                sanitized_ibi = last_valid_ibi;
                if (NULL != p_repaired) { *p_repaired = IBI_REPAIR_LOCAL; }
            }
            else {
                // Good signal quality + outlier = likely real arrhythmia
                printf("sanitize_ibi(): *** REAL ARRHYTHMIA: %u ms (expected %u-%u), keeping\n",
                       ibi_ms, expected_min, expected_max);
                // Keep the value, update running mean very slowly
                running_mean_ibi = (running_mean_ibi * 98 + ibi_ms * 2) / 100;
                last_valid_ibi = ibi_ms;
            }
        }
        else {
            // Normal variation - update running mean
            running_mean_ibi = (running_mean_ibi * 95 + ibi_ms * 5) / 100;
            last_valid_ibi = ibi_ms;
        }
    }

    /* Write the updated state back into the context. */
    ps_ppg->ibi_last_valid_ms   = last_valid_ibi;
    ps_ppg->ibi_running_mean_ms = running_mean_ibi;
    ps_ppg->ibi_history_count   = ibi_history_count;

    return sanitized_ibi;
}

/**
 * @brief Linearly interpolate one surrogate's vertices onto a uniform time grid.
 *
 * Vertices arrive one per beat, at irregular instants.  Each call fills the grid
 * points lying between the PREVIOUS vertex and the one just supplied, by linear
 * interpolation between their two values, and appends them to that surrogate's
 * buffer.  The result is the evenly sampled series the spectral stage needs.
 *
 * WHY IT IS NEEDED.  A spectrum assumes its input arrives at a steady tick;
 * beats do not.  Worse, breathing itself stretches and squeezes the gaps between
 * beats -- that is what FM measures -- so the timing error follows the very
 * rhythm being looked for and will not average out.  Resampling onto an even
 * grid first leaves a regular time axis with only the VALUE still varying.
 *
 * COUPLING TO THE SAMPLE-PATH FILTER: the three surrogates are read off the
 * FILTERED waveform, so the sample-path bandpass decides what respiratory
 * modulation survives to be measured here.  INTERPOLATE_BW in particular is the
 * per-beat foot amplitude -- it IS baseline wander.  That is why
 * BP_HP_CORNER_HZ is 0.02 Hz rather than the ~0.5 Hz the PPG filtering papers
 * recommend: their corner is chosen to DELETE baseline wander, which would
 * delete this surrogate.  Liu et al 2020 corroborate: their acquisition
 * high-passed at 0.05 Hz precisely to keep BW extractable.
 * See the file header of chebyshev_t2_o4.c.
 *
 * SURROGATE DEFINITIONS against Liu et al 2020:
 *   BW  MATCHES  -- the curve through the valleys, i.e. per-beat foot amplitude.
 *   FM  MATCHES  -- intervals between MAXIMAL-SLOPE points (pulse interval
 *                   modulation); the detector reports that point as
 *                   upslope_index, see ppg_fiducial.h.
 *   AM  DEVIATES -- Liu takes the peak curve MINUS the valley curve; this uses
 *                   the PPG signal peak alone.  Liu reads peaks from a merely
 *                   LOW-passed signal, where the valley is a good estimate of
 *                   the baseline.  Here the sample-path bandpass has already
 *                   removed the slow baseline, so sigma(peak) and sigma(foot)
 *                   are comparable and subtracting the foot mainly injects its
 *                   noise.  The measurements behind this deviation are in
 *                   docs/DESIGN.md.
 * Resampling is LINEAR, which follows Charlton et al 2016 ("resampled at 5 Hz
 * using linear interpolation (Karlen et al 2013)"); Liu uses a cubic spline.
 * Both are paper-backed -- this is a hybrid choice, not an oversight.
 *
 * The vertex arguments are deliberately generic: this function serves all three
 * surrogates, and WHICH fiducial they carry is stated by e_interpolate, not by
 * the parameter names.  Naming them "peak" or "foot" would be correct for one
 * caller and wrong for the other two.
 *
 * @param ps_ppg    Analysis context
 * @param e_interpolate   Which surrogate, and so which fiducial has arrived:
 *                        INTERPOLATE_AM = signal PEAK amplitude,
 *                        INTERPOLATE_BW = signal FOOT amplitude,
 *                        INTERPOLATE_FM = maximal-upslope INTERVAL (not an
 *                        amplitude at all)
 * @param cur_vertex_index  Sample index of the vertex just detected
 * @param cur_vertex_value  Its value, in the units implied by e_interpolate
 */
void    interpolate_vertex (struct_ppg_analysis* ps_ppg, enum_interpolate e_interpolate,
                            uint32_t cur_vertex_index,
                            int32_t  cur_vertex_value)
{
    struct_intp *ps_intp_vertex;
    uint32_t    intp_count;
    uint32_t    intp_index;
    uint32_t    beat_period;
    int32_t     last_vertex;
    uint32_t    idx_raw;
    /* The two stages of one grid sample, kept distinct because they are two
     * lines apart and easily transposed: intp_raw_value is interpolated between
     * a pair of vertices and goes into intp_buff[]; intp_mvg_value is what the
     * 5-tap moving average makes of that buffer and goes into
     * mvg_avg_sample[], which is what the spectral stage reads. */
    int32_t     intp_mvg_value = 0;
    int32_t     intp_raw_value;

    /* Which fiducial the vertex arguments carry is decided here, by the
     * caller's enum -- BW brings a FOOT, AM a PEAK, FM an upslope INTERVAL. */
    if (e_interpolate == INTERPOLATE_BW) {
        ps_intp_vertex = &(ps_ppg->s_intp_foot);
    } else if (e_interpolate == INTERPOLATE_AM) {
        ps_intp_vertex = &(ps_ppg->s_intp_peak);
    } else {
        ps_intp_vertex = &(ps_ppg->s_intp_freq);
        e_interpolate = INTERPOLATE_FM;
    }

    intp_count  = ps_intp_vertex->intp_vertex_count;
    intp_index  = ps_intp_vertex->cur_intp_sample_index;
    beat_period = (cur_vertex_index - ps_intp_vertex->prev_vertex_index);
    last_vertex = ps_intp_vertex->prev_vertex_value;
    idx_raw     = ps_intp_vertex->mvg_idx;

    if (0u == beat_period) { return; }   /* nothing to span */

    /* Contract guard.  The detector is a swappable component, so a malformed
     * index must not be able to hang the analysis layer: an index that ran
     * backwards makes beat_period underflow to ~4e9, and the grid loop below
     * would then iterate for hours.  Reject the beat and say so. */
    if ((MAX_PLAUSIBLE_BEAT_SAMPLES < beat_period) ||
        (cur_vertex_index < ps_intp_vertex->prev_vertex_index))
    {
        printf("interpolate_vertex(): ** detector contract violation ** index %u "
               "after %u (span %u) -- beat ignored\n",
               cur_vertex_index, ps_intp_vertex->prev_vertex_index, beat_period);
        ps_intp_vertex->prev_vertex_index = cur_vertex_index;
        ps_intp_vertex->prev_vertex_value = cur_vertex_value;
        return;
    }

    while (intp_index <= cur_vertex_index)
    {
        /* The value at each grid point is computed DIRECTLY from the two
         * bracketing vertices in 64-bit arithmetic.
         *
         * The previous form pre-computed an integer slope
         *     slope = ((v1 - v0) * 100) / beat_period
         * and then an integer step
         *     step  = (slope * sel_rr_decimation) / 100
         * and ACCUMULATED that step.  Measured over 2755 real beats, the ladder
         * climbed 42 where the true change was 48 (median), a 14 % per-beat
         * error, and on 7.7 % of beats the step truncated to ZERO so that
         * beat's modulation was discarded outright.  The ladder also never
         * landed on the next vertex, leaving a discontinuity at every beat that
         * injected broadband noise at the beat rate -- directly into the band
         * the respiratory estimate is trying to read.
         */
        {
            int64_t span = (int64_t)(intp_index - ps_intp_vertex->prev_vertex_index);
            int64_t diff = (int64_t)cur_vertex_value - (int64_t)last_vertex;
            intp_raw_value  = (int32_t)((int64_t)last_vertex +
                                     ((diff * span) / (int64_t)beat_period));
        }

        ps_intp_vertex->intp_buff [idx_raw % INTP_BUFF_SIZE] = intp_raw_value;
        idx_raw++;
        /* Same moving average as the sample path, one instance per surrogate.
         * Fed on EVERY grid point -- OUTSIDE the guard below -- because a running
         * sum is only correct if it sees the whole stream.  The guard governs
         * when the result is USED, not when the filter is fed. */
        intp_mvg_value = movavg_run (&ps_intp_vertex->s_mvg, intp_raw_value);
        if (RR_MVG_AVG_SAMPLE_DELAY <= idx_raw)
        {
            /* The buffer is deliberately TWICE the analysis window (see
             * INTP_MAVG_BUFF_SIZE).  AM, BW and FM are filled from different
             * fiducials, so they do not reach a full window on the same beat --
             * one runs ahead while the others catch up, and the surplus has to
             * be held so that all three can be analysed over the SAME span of
             * time once the slowest is ready.  Measured headroom in use: peak
             * 1125 of 2048 grid points on the adult recordings and 561 of 1024
             * on the neonatal pair.
             *
             * Past capacity the sample cannot be stored, but intp_count must
             * still advance or this surrogate would silently slip in time
             * against the other two.  That combination loses data, so it is
             * reported rather than absorbed: the guard exists to prevent an
             * overrun, not to make one survivable. */
            if (INTP_MAVG_BUFF_SIZE > intp_count) {
                ps_intp_vertex->mvg_avg_sample [intp_count] = intp_mvg_value;
            } else {
                printf("interpolate_vertex(): ** WARNING ** surrogate %d surplus "
                       "exceeded %u grid points -- sample dropped, window "
                       "alignment lost\n", (int)e_interpolate,
                       (unsigned)INTP_MAVG_BUFF_SIZE);
            }

            /* Label the row at the CENTRE of the average's own window, not at
             * its trailing edge.  RR_MOVING_AVERAGE sums grid points
             * [idx_raw - CNT .. idx_raw - 1], whose centre is idx_raw - CNT/2 - 1.
             * Back-dating by the full CNT instead put the smoothed column two
             * grid points -- 128 ms -- ahead of the raw column in the same row,
             * measured by cross-correlation.  Both columns now describe the same
             * instant, which is what a reader plotting them together assumes.
             *
             * Trace only: the analysis reads mvg_avg_sample[] and is untouched. */
            if (NULL != ps_intp_vertex->fp_est_rr) {
            uint32_t idx_mid = idx_raw - (RR_MVG_AVG_SAMPLE_CNT / 2u) - 1u;
            fprintf(ps_intp_vertex->fp_est_rr, "%u,%d,%d\n", idx_mid,
                    ps_intp_vertex->intp_buff [idx_mid % INTP_BUFF_SIZE],
                    intp_mvg_value); }

            intp_count++;


            /* Diagnostic trace only.  The sample ring is the detector's, so the
             * interpolated value is kept here instead. */
            ps_ppg->intp_trace [e_interpolate][intp_index % PPG_RING_LEN] = intp_mvg_value;
            if (intp_index > ps_ppg->intp_trace_hi) { ps_ppg->intp_trace_hi = intp_index; }
        }
        intp_index += ps_ppg->sel_rr_decimation;
    }

    /* Save back the updated variables */
    ps_intp_vertex->intp_vertex_count     = intp_count;
    ps_intp_vertex->cur_intp_sample_index = intp_index;
    ps_intp_vertex->prev_vertex_index   = cur_vertex_index;
    ps_intp_vertex->prev_vertex_value   = cur_vertex_value;
    ps_intp_vertex->mvg_idx               = idx_raw;

    /* While the window is still growing nothing is discarded, so the count
     * only rises -- without a cadence limit every beat would trigger a report.
     * One report per slide_pts of NEW data, driven by a single surrogate so the
     * three cannot each fire one. */
    if (0u != ps_ppg->rr_warmup_done)
    {
        if (ps_ppg->sel_rr_window_full <= intp_count)
        {
            estimate_resp_rate (ps_ppg);
        }
    }
    else if (INTERPOLATE_AM == e_interpolate)
    {
        if (intp_count >= ps_ppg->rr_next_report_pts)
        {
            ps_ppg->rr_next_report_pts = intp_count + ps_ppg->sel_rr_slide_pts;
            estimate_resp_rate (ps_ppg);
        }
    }
    return;
}

/* ***************************************************************************
 *            THE INTERFACE:  what the beat detector calls into
 *
 * These two functions are the whole of the analysis layer's public surface to
 * a detector.  Everything above is private to the respiratory/cardiac
 * analysis; everything the detector knows is declared in ppg_fiducial.h.
 * *************************************************************************** */

/**
 * @brief A pulse onset was detected -- feed the BW and FM surrogates.
 *
 * BW is the foot amplitude itself: baseline wander sampled once per beat.
 * FM is the interval between consecutive maximal-upslope points (Liu et al
 * 2020, pulse interval modulation); the previous upslope is tracked here rather
 * than in the detector, because it is an analysis quantity.
 *
 * THIS IS LIU'S DEFINITION, VERBATIM: maximal-slope point to maximal-slope
 * point, not onset to onset.
 * Liu 2020 sec 2.3.2: "the point of the maximal slope was selected by
 * calculating the derivative of the detrended PPG signal ... The time intervals
 * between consecutive maximal slope points were calculated."  Onset-to-onset is
 * sometimes quoted as "Liu's definition"; it is not, and measured it moves FM
 * error 2.85 -> 2.81 and fused MAE 0.86 -> 0.83, i.e. noise.
 *
 * Liu additionally detrends each cycle by subtracting the line between its
 * bounding valleys before differentiating.  We do not, and it cannot matter:
 * subtracting a straight line subtracts a CONSTANT from the derivative, so the
 * location of its maximum -- the only thing FM uses -- does not move.
 *
 * FM is nonetheless the weakest of the three surrogates (error exceeds AM and
 * BW on 10 of 12 recordings).  That is because it measures respiratory sinus
 * arrhythmia, which is weak in these ICU adults -- all three fiducial choices
 * give the same ~2.8 /min error.  The agreement gate excludes it when it is
 * wrong, which is what keeps the fused output correct.
 */
void    ppg_on_foot (void *user, uint32_t foot_index, int32_t foot_value,
                     uint32_t upslope_index, uint32_t upslope_valid)
{
    struct_ppg_analysis *ps_ppg = (struct_ppg_analysis *)user;

    if (NULL == ps_ppg) { return; }

    /* The foot value is needed at the peak for pulse amplitude; the BW and FM
     * vertices are only STAGED here.  ppg_commit_vertices() commits them at
     * the peak, after sanitize_ibi() has judged the beat -- see the "Vertex
     * deferral" section of docs/DESIGN.md. */
    ps_ppg->last_foot_value      = foot_value;
    ps_ppg->pend_foot_index      = foot_index;
    ps_ppg->pend_foot_value      = foot_value;
    ps_ppg->pend_upslope_index   = upslope_index;
    ps_ppg->pend_upslope_valid   = upslope_valid;
    ps_ppg->pend_foot_present    = 1u;
    return;
}

/**
 * @brief Commit the vertices staged by ppg_on_foot(), then the AM vertex.
 *
 * Called from ppg_on_peak() AFTER the beat has been judged by sanitize_ibi()
 * and BEFORE the beat's HR/HRV are published.  Reversing either of those two
 * orderings is silent and RR-invisible -- see docs/DESIGN.md, "Vertex
 * deferral".
 *
 * Gating:
 *   - BW (foot amplitude) and AM (peak amplitude) are gated by beat N's
 *     decision (ps_ppg->beat_this_accepted).
 *   - FM is the interval between consecutive maximal-upslope points.  The
 *     vertex staged at foot N was computed from upslope N-1 minus upslope
 *     N-2, so it describes cycle N-1 and is gated by beat N-1's decision
 *     (ps_ppg->beat_last_accepted).
 *
 * The gate is OPEN today: both decision fields are 1, so every vertex commits
 * and this function reproduces the previous commit sequence exactly.
 */
static void ppg_commit_vertices (struct_ppg_analysis *ps_ppg,
                                 uint32_t peak_index, int32_t peak_value)
{
    if (0u != ps_ppg->pend_foot_present)
    {
        if (0u != ps_ppg->beat_this_accepted)
        {
            interpolate_vertex (ps_ppg, INTERPOLATE_BW,
                                ps_ppg->pend_foot_index,
                                ps_ppg->pend_foot_value);
        }
        if (0u != ps_ppg->pend_upslope_valid)
        {
            if ((0u != ps_ppg->beat_last_accepted) &&
                (0u < ps_ppg->last_upslope_index) &&
                (ps_ppg->pend_upslope_index > ps_ppg->last_upslope_index))
            {
                uint32_t pim_samples = ps_ppg->pend_upslope_index -
                                       ps_ppg->last_upslope_index;
                uint32_t pim_ms      = ((pim_samples * 1000u) /
                                        ps_ppg->sel_fs_hz);

                printf("%u:     FM(PIM) slope-to-slope = %u samples = %u ms\n",
                       ps_ppg->pend_foot_index, pim_samples, pim_ms);
                interpolate_vertex (ps_ppg, INTERPOLATE_FM,
                                    ps_ppg->pend_upslope_index,
                                    (int32_t)pim_ms);
            }
            ps_ppg->last_upslope_index = ps_ppg->pend_upslope_index;
        }
        ps_ppg->pend_foot_present = 0u;
    }

    if (0u != ps_ppg->beat_this_accepted)
    {
        interpolate_vertex (ps_ppg, INTERPOLATE_AM, peak_index, peak_value);
    }
    return;
}

/**
 * @brief A PPG signal peak was detected -- feed the AM surrogate and update HR.
 *
 * Heart rate comes from peak-to-peak intervals: the clinically meaningful beat
 * interval, and the evidence the declared subject category is checked against.
 * sanitize_ibi() runs BEFORE the rate is derived, so its missed-beat and
 * false-peak repairs reach the HR history rather than only a log line.
 */
void    ppg_on_peak (void *user, uint32_t peak_index, int32_t peak_value)
{
    struct_ppg_analysis *ps_ppg = (struct_ppg_analysis *)user;

    if (NULL == ps_ppg) { return; }

    if (0u < ps_ppg->last_peak_index)
    {
        uint32_t ibi_samples = peak_index - ps_ppg->last_peak_index;
        uint32_t ibi_ms = (ibi_samples * 1000u) / ps_ppg->sel_fs_hz;
        uint32_t cur_hr;
        int32_t  pulse_amplitude = peak_value - ps_ppg->last_foot_value;
        int      quality_flag;
        uint32_t repaired = 0u;
        uint32_t raw_ibi_ms = ibi_ms;
        double   amplitude_ratio;

        /* Pulse amplitude is available here directly (peak minus the most
         * recent foot), so the quality test needs nothing from the detector.
         *
         * The denominator must be the PREVIOUS PULSE AMPLITUDE, carried as one
         * value.  An expression mixing a stored peak with a stored foot would
         * straddle two beats: ppg_on_foot() runs before ppg_on_peak() for the
         * same beat, so last_foot_value has already advanced by the time the
         * peak arrives.  Such an expression is not any pulse's amplitude, and
         * under baseline wander it collapses toward zero or goes negative --
         * which matters because this ratio feeds sanitize_ibi()'s missed-beat,
         * false-peak and arrhythmia branch.
         *
         * The hazard is created by the callback split, so carrying
         * last_pulse_amplitude -- one value, computed
         * while both endpoints of that beat were in hand -- removes the
         * possibility rather than guarding against it. */
        amplitude_ratio = (0 != ps_ppg->last_pulse_amplitude)
                          ? ((double)pulse_amplitude /
                             (double)ps_ppg->last_pulse_amplitude)
                          : 1.0;
        ps_ppg->last_pulse_amplitude = pulse_amplitude;
        quality_flag = ((amplitude_ratio >= 0.5) && (amplitude_ratio <= 2.0)) ? 1 : 0;

        ibi_ms = sanitize_ibi(ps_ppg, ibi_ms, quality_flag, &repaired);
        /* ECTOPIC ELIMINATION, [CHARLTON] citing [MATEO]: features derived
         * from ectopic beats are removed BEFORE the respiratory signals are
         * extracted.  A SUBSTITUTED interval means this beat's timing was
         * discarded, so a vertex taken from it sits on a fabricated time base
         * and must not enter the surrogates.  A SPLIT beat is real -- only the
         * interval was reconstructed -- so its vertex is kept unless the build
         * says otherwise.
         *
         * Deleting the vertex is not the same as deleting a sample: the
         * surrogates are resampled LINEARLY BETWEEN CONSECUTIVE VERTICES onto a
         * uniform grid, so the grid interpolates across the gap.  That is the
         * correction [MATEO] prefers over deletion, and it comes for free from
         * the resampling this program already does. */
        ps_ppg->beat_this_accepted =
            ((RR_GATE_ON_BAND    && (IBI_REPAIR_SUBSTITUTED == repaired)) ||
             (RR_GATE_ON_LOCAL   && (IBI_REPAIR_LOCAL       == repaired)) ||
             (RR_EXCLUDE_SPLIT_VERTICES && (IBI_REPAIR_SPLIT == repaired)))
            ? 0u : 1u;
        ppg_commit_vertices (ps_ppg, peak_index, peak_value);
        cur_hr = (0u < ibi_ms) ? (60000u / ibi_ms)
                               : ((ps_ppg->sel_fs_hz * 60u) /
                                  ((0u < ibi_samples) ? ibi_samples : 1u));
        ps_ppg->hr_bpm = cur_hr;

        /* ---- HRV: NORMAL-TO-NORMAL series -------------------------------
         * A repaired interval must not enter the series (substitution injects
         * artificial regularity), and a beat that is excluded breaks adjacency
         * for the NEXT one -- RMSSD and pNN50 may only difference genuinely
         * successive pairs.  See HRV_WINDOW_BEATS in ppg_common.h. */
        if ((0u == repaired) && (0 != quality_flag) &&
            (ps_ppg->sel_ibi_min_ms <= raw_ibi_ms) &&
            (ps_ppg->sel_ibi_max_ms >= raw_ibi_ms))
        {
            uint32_t slot = ps_ppg->nn_wr % HRV_WINDOW_BEATS;

            ps_ppg->nn_ms[slot]  = raw_ibi_ms;
            ps_ppg->nn_adj[slot] = (uint8_t)((0u != ps_ppg->nn_prev_accepted) ? 1u : 0u);
            ps_ppg->nn_wr++;
            if (HRV_WINDOW_BEATS > ps_ppg->nn_cnt) { ps_ppg->nn_cnt++; }
            ps_ppg->nn_prev_accepted = 1u;
        }
        else
        {
            ps_ppg->nn_prev_accepted = 0u;   /* gap: next pair is not successive */
        }

        printf("@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@\n");
        printf("Last_sys_idx = %u  This_sys_idx = %u\n",
               ps_ppg->last_peak_index, peak_index);
        printf("IBI = %u samples = %u ms, HR = %u bpm\n",
               ibi_samples, ibi_ms, cur_hr);

        /* Rolling, not one-shot: this used to stop at RR_HR_HIST_LEN so the
         * category consistency check only ever saw the first 256 beats. */
        ps_ppg->hr_hist[ps_ppg->hr_hist_wr % RR_HR_HIST_LEN] = cur_hr;
        ps_ppg->hr_hist_wr++;
        if (RR_HR_HIST_LEN > ps_ppg->hr_hist_cnt) { ps_ppg->hr_hist_cnt++; }
    }
    else
    {
        /* First beat: there is no interval to judge, so it is accepted
         * unconditionally -- but its vertices are still committed, exactly as
         * the pre-deferral code did. */
        ps_ppg->beat_this_accepted = 1u;
        ppg_commit_vertices (ps_ppg, peak_index, peak_value);
    }
    ps_ppg->last_peak_index = peak_index;
    ps_ppg->last_peak_value = peak_value;
    ps_ppg->beat_last_accepted = ps_ppg->beat_this_accepted;
    return;
}

/**
 * @brief Initialise the analysis layer for a new recording.
 *
 * The subject's bands arrive as data -- see struct_subject_band in ppg_common.h.
 * Nothing in this file knows which patient type is active; the
 * caller resolves those once and hands the numbers over, so this layer is the
 * same code for a neonate and an adult.  A NULL or incoherent band is refused
 * rather than silently replaced with a default, because an unnoticed wrong band
 * is the largest single source of respiratory-rate error.
 *
 * @param ps_ppg  Analysis context
 * @param fs_hz Sample rate of the PPG stream, Hz
 * @param ps_band       Respiratory and heart-rate bands for this subject
 */
void    ppg_analysis_init (struct_ppg_analysis *ps_ppg,
                           int32_t fs_hz,
                           const struct_subject_band *ps_band)
{
    FILE *saved_peak = ps_ppg->s_intp_peak.fp_est_rr;
    FILE *saved_foot = ps_ppg->s_intp_foot.fp_est_rr;
    FILE *saved_freq = ps_ppg->s_intp_freq.fp_est_rr;

    memset(ps_ppg, 0x00, sizeof(struct_ppg_analysis));
    ps_ppg->s_intp_peak.fp_est_rr = saved_peak;
    ps_ppg->s_intp_foot.fp_est_rr = saved_foot;
    ps_ppg->s_intp_freq.fp_est_rr = saved_freq;

    ps_ppg->sel_fs_hz   = (uint32_t)fs_hz;

    if ((NULL == ps_band) ||
        (0u == ps_band->rr_min_bpm) || (ps_band->rr_min_bpm >= ps_band->rr_max_bpm) ||
        (0u == ps_band->hr_min_bpm) || (ps_band->hr_min_bpm >= ps_band->hr_max_bpm))
    {
        printf("RR band: ** FATAL ** no usable subject band supplied to "
               "ppg_analysis_init()\n");
        return;
    }
    ps_ppg->sel_rr_min_bpm   = ps_band->rr_min_bpm;
    ps_ppg->sel_rr_max_bpm   = ps_band->rr_max_bpm;
    ps_ppg->sel_hr_min_bpm   = ps_band->hr_min_bpm;
    ps_ppg->sel_hr_max_bpm   = ps_band->hr_max_bpm;
    /* Reciprocal of the heart-rate band.  This is the CATEGORY PRIOR; sustained
     * contrary evidence can overrule it -- see sanitize_ibi(). */
    ps_ppg->sel_ibi_min_ms   = 60000u / ps_band->hr_max_bpm;
    ps_ppg->sel_ibi_max_ms   = 60000u / ps_band->hr_min_bpm;
    /* Envelope: not per-category, never widens.  See filter_bands.h. */
    ps_ppg->sel_ibi_floor_ms = 60000u / HR_PLAUSIBLE_MAX_BPM;
    ps_ppg->sel_ibi_ceil_ms  = 60000u / HR_PLAUSIBLE_MIN_BPM;
    ps_ppg->sel_subject_name = (NULL != ps_band->description)
                                   ? ps_band->description : "(unnamed)";

    /* Analysis sizing travels with the band -- a floor without a segment long
     * enough to resolve it is not a usable configuration.  Refused rather than
     * clamped: silently shortening a window would change what the numbers mean. */
    if ((0u == ps_band->window_pts) || (ps_band->window_pts > RR_MAX_WINDOW_PTS) ||
        (0u == ps_band->welch_seg)  || (ps_band->welch_seg  > RR_MAX_WELCH_SEG)  ||
        (ps_band->welch_seg > ps_band->window_pts) ||
        (0u == ps_band->slide_pts)  || (ps_band->slide_pts  > ps_band->window_pts))
    {
        printf("RR band: ** FATAL ** subject sizing %u/%u/%u is not usable "
               "(limits %u/%u)\n", ps_band->window_pts, ps_band->welch_seg,
               ps_band->slide_pts, (unsigned)RR_MAX_WINDOW_PTS,
               (unsigned)RR_MAX_WELCH_SEG);
        return;
    }
    ps_ppg->sel_rr_window_full    = ps_band->window_pts;
    ps_ppg->sel_rr_welch_seg_full = ps_band->welch_seg;
    ps_ppg->sel_rr_slide_pts      = ps_band->slide_pts;
    /* The ACTIVE window starts small and grows -- see RR_PROG_MIN_PTS. */
    ps_ppg->sel_rr_window_pts     = RR_PROG_MIN_PTS;
    ps_ppg->sel_rr_welch_seg      = RR_PROG_MIN_PTS;
    ps_ppg->rr_warmup_done        = 0u;
    ps_ppg->rr_next_report_pts    = RR_PROG_MIN_PTS;
    ps_ppg->rr_track_rr           = -1.0;
    ps_ppg->rr_track_p            = RR_TRACK_P0;
    ps_ppg->rr_track_count        = 0;
    memset(psd_ema,  0x00, sizeof(psd_ema));
    memset(psd_undo, 0x00, sizeof(psd_undo));
    psd_ema_primed = 0u;
    psd_ema_seg    = 0u;
    /* Each surrogate smooths with its own instance of the same moving average
     * the sample path uses.  RR_MVG_AVG_SAMPLE_CNT taps on the 15.625 Hz grid is
     * 320 ms -- a different duration from the sample path's 40 ms, because it
     * smooths a beat-to-beat series rather than a pulse waveform. */
    movavg_init (&(ps_ppg->s_intp_peak.s_mvg), RR_MVG_AVG_SAMPLE_CNT);
    movavg_init (&(ps_ppg->s_intp_foot.s_mvg), RR_MVG_AVG_SAMPLE_CNT);
    movavg_init (&(ps_ppg->s_intp_freq.s_mvg), RR_MVG_AVG_SAMPLE_CNT);
    /* 75 % overlap -- see the Welch discussion in docs/DESIGN.md. */
    ps_ppg->sel_rr_welch_overlap = (ps_ppg->sel_rr_welch_seg * 3u) / 4u;

    /* Seeded from the midpoint of the allowed range -- deliberately mechanical
     * rather than a guessed "normal" rate. */
    ps_ppg->ibi_last_valid_ms   = (ps_ppg->sel_ibi_min_ms +
                                       ps_ppg->sel_ibi_max_ms) / 2u;
    ps_ppg->ibi_running_mean_ms = ps_ppg->ibi_last_valid_ms;

    /* Decimation is DERIVED so the analysis window is a constant DURATION rather
     * than a constant sample count -- see RR_INTP_GRID_HZ in ppg_common.h. */
    window_duration = 0.0;
    window_count    = 0u;

    ps_ppg->sel_rr_decimation   = RR_DECIMATION(fs_hz);
    /* Report the SETTLED window and bin -- the values that describe this
     * subject's analysis -- and name the warm-up start separately.  Printing
     * sel_rr_window_pts here would report RR_PROG_MIN_PTS, the size the window
     * begins at, which for an adult understates the analysis span eightfold. */
    printf("RR grid: decimation %u -> %.3f Hz grid; window %.1f s "
           "(grows from %.1f s), bin %.2f /min\n",
           ps_ppg->sel_rr_decimation,
           (double)fs_hz / (double)ps_ppg->sel_rr_decimation,
           ((double)ps_ppg->sel_rr_window_full * (double)ps_ppg->sel_rr_decimation)
               / (double)fs_hz,
           ((double)ps_ppg->sel_rr_window_pts * (double)ps_ppg->sel_rr_decimation)
               / (double)fs_hz,
           (60.0 * (double)fs_hz)
               / ((double)ps_ppg->sel_rr_decimation
                  * (double)ps_ppg->sel_rr_welch_seg_full));


    printf("RR band: subject category %s, band %u-%u /min "
           "(expected HR %u-%u, beat interval %u-%u ms)\n",
           ps_ppg->sel_subject_name,
           ps_ppg->sel_rr_min_bpm, ps_ppg->sel_rr_max_bpm,
           ps_ppg->sel_hr_min_bpm, ps_ppg->sel_hr_max_bpm,
           ps_ppg->sel_ibi_min_ms, ps_ppg->sel_ibi_max_ms);

    /* HRV cannot be flagged per row without a new CSV column, and the column
     * set is settled, so the qualification is declared once here and the -1
     * sentinel carries the per-row part.  Stated as numbers the operator can
     * check, not as a disclaimer. */
    printf("HRV: window %u beats = %u-%u s at this subject's %u-%u bpm; "
           "[TASKFORCE] short-term standard is 300 s.\n"
           "     Values are INDICATIVE, not Task Force conformant.  Fields read -1 when "
           "fewer than %u\n     intervals (SDNN) or adjacent pairs (RMSSD, pNN50) survive; "
           "HRV_n carries the count.\n",
           HRV_WINDOW_BEATS,
           (HRV_WINDOW_BEATS * 60u) / ps_ppg->sel_hr_max_bpm,
           (HRV_WINDOW_BEATS * 60u) / ps_ppg->sel_hr_min_bpm,
           ps_ppg->sel_hr_min_bpm, ps_ppg->sel_hr_max_bpm,
           HRV_MIN_INTERVALS);
    return;
}
