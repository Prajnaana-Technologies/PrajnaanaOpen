/*
 * Copyright (c) 2026 Prajnaana Technologies Pvt. Ltd.
 *
 * SPDX-License-Identifier: Apache-2.0
 *
 * Original Author: Mamatha BV
 */
/* ***************************************************************************
 * ppg_RR.c -- respiratory-band signal conditioning and spectral estimation.
 *
 *   1. init_bandpass_rr() built ONE RBJ bandpass biquad with
 *      Q = f0/(f_high-f_low) = 0.179.  Measured -3 dB span was 2.7-39.8 /min
 *      with only +/-1.3 dB of shaping across the entire range, i.e. no useful
 *      selectivity, and only -5.7 dB at 1.8/min so baseline drift survived.
 *      Replaced with 4th-order Butterworth HP + 4th-order Butterworth LP.
 *
 *   2. Filter state (z1,z2,z1_1,z2_1) persisted across 50%-overlapping windows
 *      that were each independently detrended -- state/data mismatch.  The
 *      filter is now reset per window and run forward+reverse (zero phase).
 *
 *   3. estimate_rr_from_psd() searched every bin from 1 to N/2 (0.03-7.81 Hz).
 *      RR_MIN/RR_MAX were defined but never used.  It also used '>=', so ties
 *      resolved to the HIGHEST bin.  Now band-limited with '>' and refined by
 *      parabolic interpolation to break the 1.83 bpm bin quantisation.
 *
 *   4. No confidence measure existed, so a window with no respiratory peak
 *      still emitted a number.  A peak-prominence ratio is now returned and
 *      weak windows are rejected by the caller.
 * *************************************************************************** */

#include <stddef.h>
#include <math.h>
#include <stdio.h>
#include <stdint.h>
#include "ppg_common.h"

/* Working buffers (single-threaded batch analysis). */

/* ***************************************************************************
 *                    BANDPASS: 4th-order Butterworth HP + LP
 * *************************************************************************** */

/* Q values of the two cascaded sections of a 4th-order Butterworth response. */
static const double butter4_q [2] = { 0.54119610, 1.30656296 };

/**
 * @brief Design one RBJ 2nd-order high-pass section.
 */
static void design_hp_section (rr_biquad_coeffs *p_c, double fs, double fc, double q)
{
    double w0    = (2.0 * PI * fc) / fs;
    double cw    = cos(w0);
    double sw    = sin(w0);
    double alpha = sw / (2.0 * q);
    double a0    = 1.0 + alpha;

    p_c->b0 =  ((1.0 + cw) / 2.0) / a0;
    p_c->b1 = (-(1.0 + cw)) / a0;
    p_c->b2 =  ((1.0 + cw) / 2.0) / a0;
    p_c->a1 =  (-2.0 * cw) / a0;
    p_c->a2 =  (1.0 - alpha) / a0;
    return;
}

/**
 * @brief Design one RBJ 2nd-order low-pass section.
 */
static void design_lp_section (rr_biquad_coeffs *p_c, double fs, double fc, double q)
{
    double w0    = (2.0 * PI * fc) / fs;
    double cw    = cos(w0);
    double sw    = sin(w0);
    double alpha = sw / (2.0 * q);
    double a0    = 1.0 + alpha;

    p_c->b0 = ((1.0 - cw) / 2.0) / a0;
    p_c->b1 =  (1.0 - cw) / a0;
    p_c->b2 = ((1.0 - cw) / 2.0) / a0;
    p_c->a1 = (-2.0 * cw) / a0;
    p_c->a2 =  (1.0 - alpha) / a0;
    return;
}

/**
 * @brief Design the respiratory bandpass for the given band.
 *
 * Sections 0-1 form the 4th-order Butterworth high-pass at f_lo and sections
 * 2-3 the 4th-order Butterworth low-pass at f_hi.  Cascading independent HP and
 * LP sections is the correct construction for a WIDE band; a geometric-mean
 * bandpass biquad only works for narrow ones.
 *
 * WHY BUTTERWORTH HERE AND CHEBYSHEV II ON THE SAMPLE PATH.  This is a
 * considered split, not an oversight.  No paper in the reference corpus specifies a
 * filter for respiratory-rate extraction -- the Chebyshev II result (Liang et
 * al.) was won on a MORPHOLOGY metric (skewness SQI) applied to the raw cardiac
 * waveform, and Chebyshev II beat Chebyshev I and elliptic there specifically
 * because passband ripple distorts morphology.  A beat-sampled AM/BW/FM
 * surrogate has no such morphology, and this path feeds a SPECTRAL PEAK SEARCH:
 * what matters is a flat passband (so no frequency is favoured) and rejection
 * of sub-band drift.  Butterworth is maximally flat and rolls off without
 * limit, where Chebyshev II floors at -Rs.  Its one real edge, a sharper
 * transition per order, is nearly free here anyway because this path filters
 * zero-phase (forward+reverse), doubling the effective order at no cost.
 * See docs/DESIGN.md, "Literature conformance and deliberate deviations".
 *
 * @param ps_bp_filter Filter to populate
 * @param fs           Sampling rate of the interpolated surrogate, Hz
 * @param f_lo         Lower edge, Hz
 * @param f_hi         Upper edge, Hz
 */
void init_bandpass_rr_band (biquad_bp *ps_bp_filter, double fs, double f_lo, double f_hi)
{
    double nyq = fs / 2.0;
    int    i;

    if (f_hi > (nyq * 0.95))  { f_hi = nyq * 0.95; }
    if (f_lo < (fs / 1000.0)) { f_lo = fs / 1000.0; }
    if (f_lo >= f_hi)         { f_lo = f_hi / 4.0; }

    for (i = 0; i < 2; i++)
    {
        design_hp_section (&(ps_bp_filter->s[i]),     fs, f_lo, butter4_q[i]);
        design_lp_section (&(ps_bp_filter->s[i + 2]), fs, f_hi, butter4_q[i]);
    }
    ps_bp_filter->fs       = fs;
    ps_bp_filter->f_lo     = f_lo;
    ps_bp_filter->f_hi     = f_hi;
    ps_bp_filter->designed = 1u;

    printf("RR bandpass: 4th-order Butterworth HP @ %.4f Hz (%.1f/min) + "
           "4th-order Butterworth LP @ %.4f Hz (%.1f/min), fs = %.4f Hz, "
           "zero-phase (fwd+rev)\n",
           f_lo, f_lo * 60.0, f_hi, f_hi * 60.0, fs);
    return;
}

/**
 * @brief Run one biquad section over an array, from a locally-zeroed state.
 *
 * State is deliberately local: every window is filtered from rest, so nothing
 * leaks between the 50%-overlapping windows the way it used to.
 */
static void run_section (const rr_biquad_coeffs *p_c, double *x, int n)
{
    rr_biquad_state st;
    double        in, out;
    int           i;

    /* Prime with the first sample so the section does not see a step from zero
     * at the segment boundary. */
    st.x1 = x[0]; st.x2 = x[0];
    st.y1 = x[0]; st.y2 = x[0];

    for (i = 0; i < n; i++)
    {
        in  = x[i];
        out = (p_c->b0 * in) + (p_c->b1 * st.x1) + (p_c->b2 * st.x2)
              - (p_c->a1 * st.y1) - (p_c->a2 * st.y2);
        st.x2 = st.x1; st.x1 = in;
        st.y2 = st.y1; st.y1 = out;
        x[i]  = out;
    }
    return;
}

/**
 * @brief Zero-phase respiratory bandpass: forward pass, then reverse pass.
 *
 * Forward-then-reverse squares the magnitude response (8th order overall) and
 * cancels the phase, so the respiratory peak is not shifted in time.  The whole
 * window is in hand, so this costs nothing that matters here.
 */
void bandpass_filter (double *x, int n, biquad_bp *ps_bp_filter)
{
    double t;
    int    i, j;

    if (0u == ps_bp_filter->designed)
    {
        printf("bandpass_filter(): ** filter not designed; window skipped **\n");
        return;
    }

    for (i = 0; i < (int)RR_BP_SECTIONS; i++)
    {
        run_section (&(ps_bp_filter->s[i]), x, n);
    }

    for (i = 0, j = n - 1; i < j; i++, j--) { t = x[i]; x[i] = x[j]; x[j] = t; }
    for (i = 0; i < (int)RR_BP_SECTIONS; i++)
    {
        run_section (&(ps_bp_filter->s[i]), x, n);
    }
    for (i = 0, j = n - 1; i < j; i++, j--) { t = x[i]; x[i] = x[j]; x[j] = t; }
    return;
}

/* ***************************************************************************
 *                        DETREND + SPECTRAL ESTIMATION
 * *************************************************************************** */

/**
 * @brief Remove the linear trend, then normalise to zero mean / unit variance.
 */
void detrend (double *x, int n)
{
    double sumx = 0.0, sumy = 0.0, sumxy = 0.0, sumxx = 0.0;
    double a, b, mean = 0.0, stddev = 0.0;
    int    i;

    for (i = 0; i < n; i++)
    {
        sumx  += (double)i;
        sumy  += x[i];
        sumxy += (double)i * x[i];
        sumxx += (double)i * (double)i;
    }
    a = (((double)n * sumxy) - (sumx * sumy)) / (((double)n * sumxx) - (sumx * sumx));
    b = (sumy - (a * sumx)) / (double)n;
    for (i = 0; i < n; i++)
    {
        x[i] -= ((a * (double)i) + b);
    }

    for (i = 0; i < n; i++) { mean += x[i]; }
    mean /= (double)n;
    for (i = 0; i < n; i++) { stddev += (x[i] - mean) * (x[i] - mean); }
    stddev = sqrt(stddev / (double)n);
    if (stddev < 1e-12) { stddev = 1.0; }   /* flat segment: avoid divide-by-zero */
    for (i = 0; i < n; i++)
    {
        x[i] = (x[i] - mean) / stddev;
    }
    return;
}

/**
 * @brief Welch PSD: average the periodograms of overlapping segments.
 *
 * This is the estimator that actually makes AM/BW/FM agree window by window.
 * A single periodogram has 2 degrees of freedom and therefore ~100 % relative
 * standard deviation in every bin however long the record; averaging M
 * roughly-independent segments cuts that by about sqrt(M).
 *
 * Each segment is mean-removed and Hamming-tapered before its DFT, and only the
 * bins in [k_lo, k_hi] -- expressed in SEGMENT bins, i.e. multiples of
 * fs/seg_pts -- are evaluated.
 *
 * The window, segment and overlap arrive as arguments because the subject type
 * is selected at run time; storage is dimensioned for the largest category.
 *
 * @param x       Band-limited window, n_pts samples
 * @param psd_out Out: averaged PSD, indexed by segment bin
 * @param n_pts   Length of the analysis window, grid points
 * @param seg_pts Welch segment length, grid points (<= RR_MAX_WELCH_SEG)
 * @param overlap Segment overlap, grid points (< seg_pts)
 * @param k_lo    First segment bin to evaluate (inclusive)
 * @param k_hi    Last segment bin to evaluate (inclusive)
 */
void welch_psd (const double *x, double *psd_out,
                uint32_t n_pts, uint32_t seg_pts, uint32_t overlap,
                int k_lo, int k_hi)
{
    /* Sized for the largest category so one binary serves every patient type;
     * only the first seg_pts entries are used. */
    double   seg [RR_MAX_WELCH_SEG];
    double   mean, wsum;
    uint32_t s, i, nseg = 0u, advance;

    int      k, n;

    if ((0u == seg_pts) || (seg_pts > RR_MAX_WELCH_SEG) || (seg_pts > n_pts) ||
        (overlap >= seg_pts))
    {
        return;                     /* refuse an impossible segmentation */
    }
    advance = seg_pts - overlap;

    for (k = 0; k < (int)(seg_pts / 2u); k++) { psd_out[k] = 0.0; }

    /* Hamming power normalisation, so the averaged PSD keeps a meaningful scale. */
    wsum = 0.0;
    for (i = 0u; i < seg_pts; i++)
    {
        double w = 0.54 - (0.46 * cos((2.0 * PI * (double)i) / (double)(seg_pts - 1u)));
        wsum += w * w;
    }
    if (wsum < 1e-12) { wsum = 1.0; }

    for (s = 0u; (s + seg_pts) <= n_pts; s += advance)
    {
        mean = 0.0;
        for (i = 0u; i < seg_pts; i++) { mean += x[s + i]; }
        mean /= (double)seg_pts;

        for (i = 0u; i < seg_pts; i++)
        {
            double w = 0.54 - (0.46 * cos((2.0 * PI * (double)i) / (double)(seg_pts - 1u)));
            seg[i] = (x[s + i] - mean) * w;
        }

        for (k = k_lo; k <= k_hi; k++)
        {
            double re = 0.0, im = 0.0;
            double w  = (2.0 * PI * (double)k) / (double)seg_pts;
            for (n = 0; n < (int)seg_pts; n++)
            {
                double ang = w * (double)n;
                re += seg[n] * cos(ang);
                im -= seg[n] * sin(ang);
            }
            psd_out[k] += ((re * re) + (im * im)) / wsum;
        }
        nseg++;
    }

    if (0u < nseg)
    {
        for (k = k_lo; k <= k_hi; k++) { psd_out[k] /= (double)nseg; }
    }
    return;
}

/**
 * @brief Locate the respiratory peak inside [k_lo, k_hi] and rate its quality.
 *
 * Returns the peak position as a FRACTIONAL bin index: a three-point parabolic
 * fit around the maximum recovers sub-bin resolution, which matters because the
 * raw bin spacing is fs/N = 1.83 breaths/min -- coarse enough on its own to look
 * like instability from one window to the next.
 *
 * @param psd       One-sided PSD
 * @param k_lo      First bin to search (inclusive)
 * @param k_hi      Last bin to search (inclusive)
 * @param p_quality Out: peak power divided by the median in-band power.
 *                  ~1.0 means "no peak at all, just 1/f noise".
 * @return           Fractional bin index of the peak, or -1.0 if none.
 */
double estimate_rr_peak_bin (const double *psd, int k_lo, int k_hi, double *p_quality)
{
    double  best = -1.0, frac, denom, delta, med;
    int     k, k_best = -1, cnt, i, j = 0;

    double  wht [RR_MAX_WELCH_SEG];   /* sized for the largest category */
    /* In-band power, copied out to be sorted for the median.  The band cannot
     * be wider than the spectrum: k_hi is clamped to seg/2 - 1, so cnt can
     * never exceed RR_MAX_WELCH_SEG.  Sized statically for the same reason
     * wht[] is -- nothing here allocates. */
    double  tmp [RR_MAX_WELCH_SEG];

    *p_quality = 0.0;
    if (k_hi <= k_lo) { return (-1.0); }

    /* ---- WHITEN THE 1/f BACKGROUND --------------------------------------
     *
     * DEVELOPER'S IMPROVEMENT -- no paper prescribes this for PPG respiratory
     * rate.  Background removal before peak-picking is standard practice in
     * spectral analysis generally, but the decision to apply it here, and the
     * log-log straight-line background model, are ours.
     *
     * WHY: the respiratory band-pass has its corner AT the band floor, so the
     * lowest in-band bin sits about 1.2x the corner frequency and receives
     * residual baseline drift at almost full gain.  No filter removes that --
     * every filter passes 1.2x its own corner -- so the drift has to be dealt
     * with in the spectrum instead.  A respiratory peak is a LOCAL EXCESS over
     * a smooth background, not the largest absolute number in the band, and
     * taking the plain argmax confuses the two.
     * A respiratory peak is a LOCAL EXCESS over a smooth background, not the
     * largest number in the band.  Taking the global maximum of the raw PSD
     * confuses the two, and the surrogates carry enough residual baseline
     * wander for that to matter: the RR band-pass has its corner AT the band
     * floor, so the first in-band bin sits about 1.2x the corner frequency and
     * receives drift at almost full gain.  No filter removes that -- any filter
     * passes 1.2x its own corner -- so the background is removed here instead.
     *
     * The scale of the problem this addresses -- these
     * are the numbers that MOTIVATED it, not a description of the current
     * build.  Power in the LOWEST in-band bin relative to power at the TRUE
     * respiratory bin, across an annotated adult reference set:
     *
     *      cleanest recording   0.09     drift-heavy recording   0.88
     *      typical recording    0.23     worst recording         1.09
     *
     * i.e. on the worst the band floor held MORE power than the real
     * respiratory peak, in half of all windows.  After whitening and the longer
     * segment the same ratio falls to 0.14 and 0.19 on those two, and the
     * fraction of windows where the floor outguns the peak drops from 50 % to
     * 16 %.
     *
     * The background is fitted as a straight line in log-power against
     * log-frequency -- the standard 1/f^b form -- and divided out.  Bin index
     * is used in place of frequency; they differ by a constant factor, which
     * lands in the intercept and cancels.  Everything downstream (peak search,
     * prominence, parabolic refinement) then runs on the whitened spectrum, so
     * `q` becomes "how far this peak stands above the local background" rather
     * than "how far above the median", which is what it was always meant to be.
     * That also removes a pathology: a band containing nothing but a 1/f skirt
     * used to produce the HIGHEST prominence in the dataset (q = 23.7) because
     * its median collapsed.  Whitened, such a band is flat and scores q ~ 1. */
    {
        double lx [RR_MAX_WELCH_SEG], ly [RR_MAX_WELCH_SEG],
               sl [RR_MAX_WELCH_SEG * 4];
        double a, b, t;
        int    n = 0, ii, jj, m = 0;

        for (k = k_lo; k <= k_hi; k++)
        {
            lx[n] = log((double)k);
            ly[n] = log((psd[k] > 1e-30) ? psd[k] : 1e-30);
            n++;
        }

        /* THEIL-SEN (Theil 1950; Sen, JASA 63:1379, 1968) -- a standard
         * robust line fit.  Choosing it HERE is a DEVELOPER'S IMPROVEMENT.
         *
         * The slope is the MEDIAN of all pairwise slopes, and the
         * intercept the median residual.  A plain least-squares fit is not
         * usable here.  The lowest in-band bin is not "background" at all --
         * it is a spurious drift peak -- and including it in a least-squares
         * fit drags the slope steeper, which then over-corrects the mid-low
         * bins and manufactures a NEW failure: measured with the band floor at
         * 4/min, windows that read the rate correctly jumped to exactly half
         * of it, because the over-steep background inflated the whitened
         * mid-band where the half-rate bin sits.
         * A median-based fit ignores that one bin instead of being led by it. */
        for (ii = 0; ii < n; ii++)
        {
            for (jj = ii + 1; jj < n; jj++)
            {
                double dx = lx[jj] - lx[ii];

                /* BOUND.  sl[] holds one slope per PAIR, so it fills as
                 * n(n-1)/2 while its capacity scales with n.  Every shipped
                 * band leaves ~19x headroom (worst case 105 pairs of 2048), but
                 * the k_hi clamp alone permits n = seg/2 - 1, which would need
                 * far more.  Widening a band must degrade the fit, never
                 * overrun the array -- a truncated slope set still yields a
                 * usable median. */
                if (m >= (int)(sizeof(sl) / sizeof(sl[0]))) { break; }
                if (fabs(dx) > 1e-30) { sl[m] = (ly[jj] - ly[ii]) / dx; m++; }
            }
            if (m >= (int)(sizeof(sl) / sizeof(sl[0]))) { break; }
        }
        for (ii = 1; ii < m; ii++)
        {
            t = sl[ii];
            for (jj = ii; (jj > 0) && (sl[jj - 1] > t); jj--) { sl[jj] = sl[jj - 1]; }
            sl[jj] = t;
        }
        b = (m > 0) ? sl[m / 2] : 0.0;

        for (ii = 0; ii < n; ii++) { sl[ii] = ly[ii] - (b * lx[ii]); }
        for (ii = 1; ii < n; ii++)
        {
            t = sl[ii];
            for (jj = ii; (jj > 0) && (sl[jj - 1] > t); jj--) { sl[jj] = sl[jj - 1]; }
            sl[jj] = t;
        }
        a = (n > 0) ? sl[n / 2] : 0.0;

        for (k = k_lo; k <= k_hi; k++)
        {
            double bg = exp(a + (b * log((double)k)));

            wht[k] = (bg > 1e-30) ? (psd[k] / bg) : psd[k];
        }
    }
    psd = wht;

    /* Strict '>' so a run of equal bins keeps the FIRST (lowest) one; the
     * original '>=' silently walked ties up to the highest bin in the sweep. */
    for (k = k_lo; k <= k_hi; k++)
    {
        if (psd[k] > best) { best = psd[k]; k_best = k; }
    }
    if ((k_best < 0) || (best <= 0.0)) { return (-1.0); }

    /* Median in-band power -> peak prominence. */
    cnt = (k_hi - k_lo) + 1;
    for (k = k_lo, i = 0; k <= k_hi; k++, i++) { tmp[i] = psd[k]; }
    for (i = 1; i < cnt; i++)
    {
        double v = tmp[i];
        for (j = i - 1; (j >= 0) && (tmp[j] > v); j--) { tmp[j + 1] = tmp[j]; }
        tmp[j + 1] = v;
    }
    med = tmp[cnt / 2];
    *p_quality = (med > 1e-30) ? (best / med) : 0.0;

    /* Parabolic refinement (skipped at the band edges). */
    frac = (double)k_best;
    if ((k_best > k_lo) && (k_best < k_hi))
    {
        denom = psd[k_best - 1] - (2.0 * psd[k_best]) + psd[k_best + 1];
        if (fabs(denom) > 1e-30)
        {
            delta = (0.5 * (psd[k_best - 1] - psd[k_best + 1])) / denom;
            if ((delta > -0.5) && (delta < 0.5)) { frac = (double)k_best + delta; }
        }
    }
    return (frac);
}

/* ***************************************************************************
 *                     BREATH DETECTION AND RR VARIABILITY
 * *************************************************************************** */

/**
 * @brief Extract breath-to-breath intervals from a band-limited respiratory wave.
 *
 * RRV requires INDIVIDUAL breath timings; a per-window mean RR cannot yield it,
 * which is why no RRV existed before.  The input must already be bandpassed to
 * the respiratory band, so each zero up-crossing marks one breath.  Crossings
 * are used rather than peaks because they are far less sensitive to amplitude
 * noise.
 *
 * @param x       Bandpassed respiratory signal (NOT Hamming-windowed)
 * @param n       Number of samples
 * @param fs      Sampling rate, Hz
 * @param min_s   Shortest plausible breath interval, seconds
 * @param max_s   Longest plausible breath interval, seconds
 * @param p_bbi   Out: breath-to-breath intervals, ms
 * @param p_adj   Out: 1 if p_bbi[k] is temporally ADJACENT to p_bbi[k-1], else
 *                0.  Intervals outside [min_s, max_s] are dropped, which
 *                breaks the chain; RMSSD must only difference adjacent pairs.
 * @param max_bbi Capacity of p_bbi and p_adj
 * @return         Number of intervals written
 */
uint32_t extract_breath_intervals (const double *x, int n, double fs,
                                   double min_s, double max_s,
                                   double *p_bbi, uint8_t *p_adj, uint32_t max_bbi)
{
    double   last_t = -1.0, t, frac, ibi;
    uint32_t cnt = 0u;
    uint32_t contiguous = 0u;   /* previous crossing produced an ACCEPTED interval */
    int      i;

    for (i = 1; (i < n) && (cnt < max_bbi); i++)
    {
        if ((x[i - 1] <= 0.0) && (x[i] > 0.0))
        {
            /* Linear sub-sample estimate of the crossing instant. */
            frac = (x[i] - x[i - 1]);
            frac = (frac > 1e-30) ? (-x[i - 1] / frac) : 0.0;
            t    = ((double)(i - 1) + frac) / fs;

            if (last_t >= 0.0)
            {
                ibi = t - last_t;
                if ((ibi >= min_s) && (ibi <= max_s))
                {
                    p_bbi[cnt] = ibi * 1000.0;
                    /* Adjacent only if the PREVIOUS interval was also accepted:
                     * a rejected interval breaks the chain, and differencing
                     * across that gap would not be a successive difference. */
                    p_adj[cnt] = (uint8_t)((0u != contiguous) ? 1u : 0u);
                    cnt++;
                    contiguous = 1u;
                }
                else
                {
                    contiguous = 0u;   /* gap: next interval is not successive */
                }
            }
            else
            {
                contiguous = 0u;
            }
            last_t = t;
        }
    }
    return (cnt);
}

/**
 * @brief Keep only the breath intervals consistent with the reported rate.
 *
 * DEVELOPER'S IMPROVEMENT -- not from any paper.
 *
 * WHY.  extract_breath_intervals() accepts anything inside the whole declared
 * respiratory band, because its other consumer is the time-domain rate, which
 * must stay a FREE and INDEPENDENT witness against a spectral half- or
 * double-rate lock.  That width is wrong for variability: at a rate near the
 * middle of the band, a missed up-crossing merges two breaths into one interval
 * of ~2T and a spurious crossing splits one into ~T/2, and BOTH survive the band
 * test.  The interval series then becomes multi-modal, and SD / RMSSD -- which
 * are simply the spread of that series -- measure the detector's miss and split
 * rate rather than the subject's respiratory variability.  Measured on the adult
 * cohort: 19 % of accepted intervals sit outside 0.75-1.35 x the reported
 * period, and they inflate median SD to 3.3 x the manually annotated value.
 *
 * WHY THIS BOUND.  The tolerance is sqrt(2), which is DERIVED rather than swept:
 * on a logarithmic period axis it is the exact midpoint between the fundamental
 * and both of its confusable neighbours, T/2 and 2T.  An interval is therefore
 * kept when the reported period is its NEAREST plausible multiple, and dropped
 * when a half or a double is nearer.  Nothing about it is fitted to a dataset,
 * and it cannot be tightened without biasing the very spread being measured.
 *
 * The gate runs AFTER the rate is final, so it changes no rate, no fusion
 * decision and no rescue -- only which intervals back the variability numbers.
 *
 * @param p_bbi     In/out: breath intervals, ms; compacted in place
 * @param p_adj     In/out: adjacency flags, rebuilt for the compacted series
 * @param cnt       Number of intervals on entry
 * @param period_ms Reported breath period for this window, ms
 * @param p_mean_ms Out: mean of the surviving intervals, ms (0.0 if none)
 * @return           Number of intervals surviving
 */
uint32_t gate_breath_intervals (double *p_bbi, uint8_t *p_adj, uint32_t cnt,
                                double period_ms, double *p_mean_ms)
{
    const double lo = period_ms / RRV_GATE_TOLERANCE;
    const double hi = period_ms * RRV_GATE_TOLERANCE;
    double       sum = 0.0;
    uint32_t     i, out = 0u, contiguous = 0u;

    *p_mean_ms = 0.0;
    if (period_ms <= 0.0) { return (cnt); }

    for (i = 0u; i < cnt; i++)
    {
        if ((p_bbi[i] >= lo) && (p_bbi[i] <= hi))
        {
            /* Adjacency must be rebuilt, not inherited: dropping an interval
             * separates its neighbours, so the pair either side of the hole is
             * no longer successive and must not feed RMSSD. */
            p_bbi[out] = p_bbi[i];
            p_adj[out] = (uint8_t)((0u != contiguous) ? 1u : 0u);
            sum       += p_bbi[i];
            out++;
            contiguous = 1u;
        }
        else
        {
            contiguous = 0u;
        }
    }
    if (0u < out) { *p_mean_ms = sum / (double)out; }
    return (out);
}

/**
 * @brief Compute RRV metrics from a set of breath-to-breath intervals.
 *
 * NOT GROUNDED IN THE REFERENCE CORPUS.  Neither Charlton et al 2016 nor Liu
 * et al 2020 addresses respiratory-rate VARIABILITY -- both stop at the rate.
 * SD / RMSSD / CV here are carried over by analogy with the time-domain HRV
 * metrics of the same names.  The analogy is reasonable (both are variability
 * of an inter-event interval) but it is OUR extension: report these as derived
 * measures, not as literature-validated ones.
 *
 * @param p_bbi    Breath intervals, ms
 * @param p_adj    Adjacency flags from extract_breath_intervals(); RMSSD uses
 *                 only pairs marked adjacent, and is 0.0 if none are
 * @param cnt      Number of intervals (>= 3 required)
 * @param p_sd     Out: standard deviation of the intervals, ms
 * @param p_rmssd  Out: RMS of successive differences, ms
 * @param p_cv_pct Out: coefficient of variation, %
 * @return          1 if the metrics are valid, 0 otherwise
 */
int compute_rrv (const double *p_bbi, const uint8_t *p_adj, uint32_t cnt,
                 double *p_sd, double *p_rmssd, double *p_cv_pct)
{
    double   mean = 0.0, var = 0.0, ssd = 0.0, d;
    uint32_t i, npair = 0u;

    *p_sd = 0.0; *p_rmssd = 0.0; *p_cv_pct = 0.0;
    if (3u > cnt) { return (0); }

    for (i = 0u; i < cnt; i++) { mean += p_bbi[i]; }
    mean /= (double)cnt;

    for (i = 0u; i < cnt; i++)
    {
        d    = p_bbi[i] - mean;
        var += d * d;
    }
    *p_sd = sqrt(var / (double)(cnt - 1u));

    /* RMSSD is the RMS of SUCCESSIVE differences, so only pairs that are
     * genuinely adjacent may contribute.  extract_breath_intervals() drops
     * implausible intervals, and differencing across such a gap would compare
     * breaths that are not neighbours -- silently inflating the metric. */
    for (i = 1u; i < cnt; i++)
    {
        if (0u != p_adj[i])
        {
            d    = p_bbi[i] - p_bbi[i - 1u];
            ssd += d * d;
            npair++;
        }
    }
    if (0u == npair)
    {
        /* No adjacent pair survived: SD and CV are still meaningful, RMSSD is
         * not.  Report the not-available sentinel, NOT 0.0 -- a zero here would
         * read as "no variability between breaths", which is a measurement this
         * window did not make.  -1 is what every other RRV field uses when it
         * has nothing to report, and is what the user guide documents. */
        *p_rmssd = RRV_NOT_REPORTABLE;
    }
    else
    {
        *p_rmssd = sqrt(ssd / (double)npair);
    }

    if (mean > 1e-9) { *p_cv_pct = (*p_sd / mean) * 100.0; }
    return (1);
}
