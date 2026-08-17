/*
 * Copyright (c) 2026 Prajnaana Technologies Pvt. Ltd.
 *
 * SPDX-License-Identifier: Apache-2.0
 *
 * Original Author: Mamatha BV
 */
/* ***************************************************************************
 * chebyshev_t2_o4.c -- sample-path Chebyshev Type II bandpass for PPG.
 *
 * DESIGN
 * ------
 * A Chebyshev Type II band-pass, synthesised the standard way:
 *
 *   analog Type II low-pass prototype (order CHEB2_ORDER, stopband edge at
 *   w = 1, stopband attenuation CHEB2_STOPBAND_DB)
 *     -> rescaled so its -3 dB point sits at w = 1
 *     -> low-pass to band-pass transform  S = (s^2 + w0^2) / (BW * s)
 *     -> bilinear transform with frequency pre-warping
 *     -> factored into conjugate-pair biquads, Direct Form 1
 *
 * ORDER CONVENTION: CHEB2_ORDER is the PROTOTYPE order, matching how the PPG
 * literature and scipy state it -- scipy's cheby2(4, rs, [f1,f2], 'band') also
 * yields an 8th-order band-pass (4 biquads) from a 4th-order prototype.  So
 * "4th order Chebyshev Type II band-pass" here means 4 cascaded biquads.
 *
 * Type II places its ripple in the STOPBAND and is maximally flat in the
 * passband, so the passband is clean while rejection settles at -rs dB rather
 * than rolling off forever.  Gain is normalised to unity at band centre, so no
 * compensating fudge is needed at the call site.
 *
 * The filter is causal and streams one sample at a time (the respiratory path
 * in ppg_RR.c can afford forward+reverse zero-phase filtering because it holds
 * a whole window; this one cannot).  Section state is primed on the first
 * sample to the steady state for that DC level, which avoids a startup step
 * that would otherwise take ~1000 samples to settle at the 0.02 Hz edge.
 *
 * Complex arithmetic is done with a local struct rather than <complex.h>,
 * because MSVC does not support C99 _Complex in C mode and this tree is built
 * on Windows as well as on Linux/QNX.
 *
 * LITERATURE CONFORMANCE  (Liang et al., Sci Data 5:180076, 2018,
 * doi:10.1038/sdata.2018.76 -- indexed in ../CREDITS.md; and the
 * PPGFeat / CVD-indices papers that cite it).  Full
 * discussion in docs/DESIGN.md, "Literature conformance and deliberate
 * deviations".
 *
 *   CONFORMS : Chebyshev Type II, 4th-order prototype (-> 4 biquads).
 *
 *   DEVIATES : stopband 40 dB, not the papers' 20 dB.  Because our corners pin
 *              -3 dB rather than -Rs, Rs sets only transition sharpness and the
 *              stopband floor, never the passband.  At a constant 0.5-8 Hz band,
 *              40 dB gives -75 dB at 0.1 Hz where 20 dB gives only -25 dB, and
 *              sub-band drift leaking into the respiratory peak search is a
 *              failure this pipeline has already had -- see docs/DESIGN.md,
 *              "Low-frequency contamination".
 *
 *   DEVIATES : lower corner 0.02 Hz, not the papers' effective ~0.50 Hz.
 *              DELIBERATE AND LOAD-BEARING.  The papers high-pass at 0.4-0.5 Hz
 *              to REMOVE baseline wander, which is noise for morphology work.
 *              Here baseline wander is SIGNAL: the BW respiratory surrogate is
 *              the per-beat foot amplitude.  The searched respiratory bands are
 *              0.37-1.10 Hz (neonate, 22-66/min) and 0.10-0.50 Hz (adult,
 *              6-30/min); a 0.5 Hz corner sits inside both and would erase the
 *              whole adult band.  See filter_bands.h for the cited values.
 *              Raising this corner improves beat morphology and destroys one of
 *              the three RR surrogates.  Do not "restore" it to match a paper.
 *
 *   DEVIATES : corners are the -3 dB points.  MATLAB/scipy cheby2(n,Rs,Ws)
 *              treats Ws as where -Rs is reached, so the same numbers describe
 *              different filters.  Measured: cheby2(4,20,[0.4,8]) at 125 Hz is
 *              really -3 dB over 0.502-6.409 Hz -- i.e. our 6.00 Hz upper
 *              corner already agrees with the literature; only the lower one
 *              diverges.  Pinning -3 dB keeps the passband independent of Rs.
 * *************************************************************************** */

#include <stdio.h>
#include <math.h>
#include <stdint.h>
#include <string.h>

#include "ppg_common.h"

/* Filter parameters (and their citations) live in filter_bands.h. */
#define CHEB2_ORDER         BP_CHEB2_ORDER
#define CHEB2_STOPBAND_DB   BP_CHEB2_STOPBAND_DB

#define BP_SECTIONS         (CHEB2_ORDER)   /* 4 biquads = 8th-order band-pass */

/* --------------------------------------------------------------------------
 * Minimal complex helpers (MSVC-safe).
 * -------------------------------------------------------------------------- */
typedef struct { double re, im; } cplx;

/** @brief Build a complex number from its real and imaginary parts. */
static cplx c_make (double r, double i)  { cplx c; c.re = r; c.im = i; return c; }

/** @brief Complex addition, a + b. */
static cplx c_add  (cplx a, cplx b)      { return c_make(a.re + b.re, a.im + b.im); }

/** @brief Complex subtraction, a - b. */
static cplx c_sub  (cplx a, cplx b)      { return c_make(a.re - b.re, a.im - b.im); }

/** @brief Complex multiplication, a * b. */
static cplx c_mul  (cplx a, cplx b)      { return c_make((a.re * b.re) - (a.im * b.im),
                                                         (a.re * b.im) + (a.im * b.re)); }

/** @brief Scale a complex number by a real factor. */
static cplx c_scale(cplx a, double s)    { return c_make(a.re * s, a.im * s); }

/** @brief Modulus of a complex number. */
static double c_abs(cplx a)              { return sqrt((a.re * a.re) + (a.im * a.im)); }

/**
 * @brief Complex division, a / b.  Returns 0 when b is zero.
 */
static cplx c_div (cplx a, cplx b)
{
    double d = (b.re * b.re) + (b.im * b.im);

    if (0.0 == d) { return c_make(0.0, 0.0); }
    return c_make(((a.re * b.re) + (a.im * b.im)) / d,
                  ((a.im * b.re) - (a.re * b.im)) / d);
}

/* Principal square root of a complex number. */
static cplx c_sqrt (cplx a)
{
    double m = c_abs(a);
    double r = sqrt((m + a.re) / 2.0);
    double i = sqrt((m - a.re) / 2.0);

    if (a.im < 0.0) { i = -i; }
    return c_make(r, i);
}

/* --------------------------------------------------------------------------
 * Filter storage
 * -------------------------------------------------------------------------- */
typedef struct {
    double b0, b1, b2;      /* numerator   */
    double a1, a2;          /* denominator (a0 normalised to 1) */
} bp_coeffs;

typedef struct {
    double x1, x2, y1, y2;  /* Direct Form 1 state */
} bp_states;

static bp_coeffs bp_coeff [BP_SECTIONS];
static bp_states bp_state [BP_SECTIONS];
static uint32_t bp_designed = 0u;
static uint32_t bp_primed   = 0u;

/* --------------------------------------------------------------------------
 * POST-FILTER SMOOTHING
 *
 * A short moving average on the band-passed stream, ahead of beat detection.
 * It is NOT a second band-limiting stage -- the Chebyshev has already set the
 * band at BP_LP_CORNER_HZ.  Its purpose is to take the sample-level jitter out
 * of the value AT the fiducial, because foot_value and peak_value are the BW
 * and AM respiratory surrogates: a peak that lands one sample early on a noisy
 * crest reports the wrong amplitude, and that error goes straight into the
 * surrogate the RR estimate reads.
 *
 * Length: sweeping the duration on all 12 annotated recordings put the optimum
 * at 5 taps at 125 Hz, degrading on either side (RR MAE 2.47 off, 2.43 at 24 ms,
 * 2.37 at 40 ms, 2.67 at 64 ms, 2.56 at 96 ms).  It is expressed in MILLISECONDS, not
 * taps, so that it obeys the sampling-rate rule (docs/FIDUCIAL_INTERFACE.md SS3.7):
 * 40 ms is exactly 5 taps at the 125 Hz design point and stays a 40 ms window
 * at any other rate, where a constant 5 taps would not.
 *
 * At 125 Hz the first null is at 25 Hz and the -3 dB point near 11 Hz, both far
 * above the 6 Hz the Chebyshev already passes, so the pulse waveform itself is
 * essentially untouched -- confirmed by beat detection being unchanged
 * (median F1 99.0 with and without).
 *
 * Applied here rather than in each detector so that every detector sees the same
 * conditioned stream, and so the choice of detector cannot quietly change the
 * conditioning behind it.
 * -------------------------------------------------------------------------- */
/* The sample path's own instance of the shared moving average.  Its taps are
 * derived from the input rate at design time; every other stream that needs
 * smoothing owns its own struct_movavg and calls the same movavg_run(). */
static struct_movavg  s_sample_ma;

/* --------------------------------------------------------------------------
 * Analog Chebyshev Type II low-pass prototype
 * -------------------------------------------------------------------------- */

/**
 * @brief Build the analog Type II low-pass prototype poles and zeros.
 *
 * Stopband edge is normalised to w = 1 and the DC gain is unity.  Type II poles
 * are the reciprocals of the corresponding Type I poles; the zeros lie on the
 * imaginary axis at j / cos(theta_k), which is what produces the equiripple
 * stopband.  For an even order every k yields a finite zero, so the prototype
 * has as many zeros as poles and settles at -rs dB rather than rolling off.
 *
 * @param n     Prototype order (must be even here)
 * @param rs_db Stopband attenuation, dB
 * @param p     Out: n poles
 * @param z     Out: n zeros
 */
static void cheb2_prototype (int n, double rs_db, cplx *p, cplx *z)
{
    double eps = 1.0 / sqrt(pow(10.0, 0.1 * rs_db) - 1.0);
    double mu  = asinh(1.0 / eps) / (double)n;
    double th;
    int    k;

    for (k = 0; k < n; k++)
    {
        /* theta of the k-th Type I pole, k = 0 .. n-1 */
        th = PI * (double)((2 * k) + 1) / (2.0 * (double)n);

        /* Type I pole, then reciprocate to get the Type II pole. */
        p[k] = c_div(c_make(1.0, 0.0),
                     c_make(-sinh(mu) * sin(th), cosh(mu) * cos(th)));

        /* Type II zero on the imaginary axis (cos(th) != 0 for even n). */
        z[k] = c_make(0.0, 1.0 / cos(th));
    }
    return;
}

/**
 * @brief Magnitude of the prototype at s = jw, normalised to its DC value.
 */
static double proto_mag (const cplx *p, const cplx *z, int n, double w)
{
    cplx   s   = c_make(0.0, w);
    double num = 1.0, den = 1.0, num0 = 1.0, den0 = 1.0;
    int    k;

    for (k = 0; k < n; k++)
    {
        num *= c_abs(c_sub(s, z[k]));
        den *= c_abs(c_sub(s, p[k]));
        num0 *= c_abs(z[k]);        /* |0 - z| */
        den0 *= c_abs(p[k]);        /* |0 - p| */
    }
    if ((0.0 == den) || (0.0 == num0)) { return (0.0); }
    return ((num / den) / (num0 / den0));
}

/**
 * @brief Find the prototype's -3 dB frequency by bisection.
 *
 * Type II is flat at DC and falls monotonically to the stopband edge, so the
 * -3 dB point lies in (0, 1) and bisection is safe.  Rescaling the prototype by
 * this value puts the -3 dB point at w = 1, which lets the band-pass transform
 * be driven by the -3 dB edges the caller actually asked for.
 */
static double proto_3db (const cplx *p, const cplx *z, int n)
{
    double lo = 1e-9, hi = 1.0, mid = 0.5;
    double target = 1.0 / sqrt(2.0);
    int    i;

    for (i = 0; i < 200; i++)
    {
        mid = 0.5 * (lo + hi);
        if (proto_mag(p, z, n, mid) > target) { lo = mid; } else { hi = mid; }
    }
    return (mid);
}

/* --------------------------------------------------------------------------
 * Design
 * -------------------------------------------------------------------------- */

/**
 * @brief Evaluate the cascade's magnitude response at a digital frequency.
 */
static double cascade_mag (double w)
{
    cplx   zm1 = c_make(cos(-w), sin(-w));      /* z^-1 */
    cplx   zm2 = c_mul(zm1, zm1);
    double m   = 1.0;
    uint32_t i;

    for (i = 0u; i < BP_SECTIONS; i++)
    {
        cplx num = c_add(c_add(c_make(bp_coeff[i].b0, 0.0),
                               c_scale(zm1, bp_coeff[i].b1)),
                         c_scale(zm2, bp_coeff[i].b2));
        cplx den = c_add(c_add(c_make(1.0, 0.0),
                               c_scale(zm1, bp_coeff[i].a1)),
                         c_scale(zm2, bp_coeff[i].a2));
        m *= c_abs(c_div(num, den));
    }
    return (m);
}

/**
 * @brief Report each section's pole radius so instability is visible.
 *
 * For a normalised biquad the poles satisfy z^2 + a1 z + a2 = 0, so a complex
 * conjugate pair has radius sqrt(a2).  This is the z-plane test the original
 * file attempted -- on analog poles, where it did not apply.
 */
static void report_stability (void)
{
    uint32_t i;
    double   r;

    for (i = 0u; i < BP_SECTIONS; i++)
    {
        r = sqrt(fabs(bp_coeff[i].a2));
        printf("  section %u: pole radius = %.8f  %s\n", i, r,
               (r < 1.0) ? "(stable)" : "** UNSTABLE **");
    }
    return;
}

/**
 * @brief Design the sample-path Chebyshev Type II band-pass.
 *
 * Corners are clamped away from DC and Nyquist so an unusual sampling rate
 * cannot produce a degenerate design.  Gain is normalised to unity at the
 * geometric-mean band centre.
 *
 * NOTE ON THE BAND: f_lo/f_hi are the -3 dB points, NOT the MATLAB cheby2
 * convention where the stated frequency is where -Rs is reached.  The upper
 * corner matches the literature; the lower one is deliberately far below it
 * because the BW respiratory surrogate needs baseline wander preserved.  See
 * the LITERATURE CONFORMANCE block in the file header before changing either.
 *
 * @param fs_hz Sample rate of the raw PPG stream, Hz
 * @return 0 on success, -1 if the design could not be produced.
 */
int32_t init_chebyshev_filter (int32_t fs_hz)
{
    cplx     pp [CHEB2_ORDER], pz [CHEB2_ORDER];
    double   fs   = (double)fs_hz;
    double   nyq  = fs / 2.0;
    double   f_lo = BP_HP_CORNER_HZ;
    double   f_hi = BP_LP_CORNER_HZ;
    double   w1, w2, w0sq, bw, sc, gain, wc;
    int      half = CHEB2_ORDER / 2;
    int      k, sec;

    if (f_hi > (nyq * 0.95))     { f_hi = nyq * 0.95; }
    if (f_lo < (fs / 100000.0))  { f_lo = fs / 100000.0; }
    if (f_lo >= f_hi)            { f_lo = f_hi / 4.0; }

    cheb2_prototype (CHEB2_ORDER, CHEB2_STOPBAND_DB, pp, pz);

    /* Put the prototype's -3 dB point at w = 1 so the band-pass transform is
     * driven by the -3 dB edges requested above. */
    sc = proto_3db (pp, pz, CHEB2_ORDER);
    if (sc <= 0.0) { bp_designed = 0u; return (-1); }
    for (k = 0; k < CHEB2_ORDER; k++)
    {
        pp[k] = c_scale(pp[k], 1.0 / sc);
        pz[k] = c_scale(pz[k], 1.0 / sc);
    }

    /* Pre-warp the digital edges into analog frequencies for the bilinear
     * transform, then form the band-pass parameters. */
    w1   = 2.0 * fs * tan(PI * f_lo / fs);
    w2   = 2.0 * fs * tan(PI * f_hi / fs);
    w0sq = w1 * w2;
    bw   = w2 - w1;

    /* Each conjugate prototype pair (take the k-th of the first half) maps to
     * two conjugate band-pass pairs, i.e. two biquads. */
    for (k = 0, sec = 0; k < half; k++, sec += 2)
    {
        cplx ph = c_scale(pp[k], bw * 0.5);
        cplx zh = c_scale(pz[k], bw * 0.5);
        cplx pr = c_sqrt(c_sub(c_mul(ph, ph), c_make(w0sq, 0.0)));
        cplx zr = c_sqrt(c_sub(c_mul(zh, zh), c_make(w0sq, 0.0)));
        cplx sp [2], sz [2];
        int  j;

        sp[0] = c_add(ph, pr);  sp[1] = c_sub(ph, pr);
        sz[0] = c_add(zh, zr);  sz[1] = c_sub(zh, zr);

        for (j = 0; j < 2; j++)
        {
            /* Bilinear:  z = (2 fs + s) / (2 fs - s) */
            cplx dp = c_div(c_add(c_make(2.0 * fs, 0.0), sp[j]),
                            c_sub(c_make(2.0 * fs, 0.0), sp[j]));
            cplx dz = c_div(c_add(c_make(2.0 * fs, 0.0), sz[j]),
                            c_sub(c_make(2.0 * fs, 0.0), sz[j]));

            /* Conjugate pair -> real biquad coefficients. */
            bp_coeff[sec + j].b0 =  1.0;
            bp_coeff[sec + j].b1 = -2.0 * dz.re;
            bp_coeff[sec + j].b2 =  (dz.re * dz.re) + (dz.im * dz.im);
            bp_coeff[sec + j].a1 = -2.0 * dp.re;
            bp_coeff[sec + j].a2 =  (dp.re * dp.re) + (dp.im * dp.im);
        }
    }
    bp_designed = 1u;
    bp_primed   = 0u;

    /* Normalise to unity at the geometric-mean band centre. */
    wc   = 2.0 * PI * sqrt(f_lo * f_hi) / fs;
    gain = cascade_mag(wc);
    if (gain > 0.0)
    {
        double g = pow(1.0 / gain, 1.0 / (double)BP_SECTIONS);
        uint32_t i;

        for (i = 0u; i < BP_SECTIONS; i++)
        {
            bp_coeff[i].b0 *= g;
            bp_coeff[i].b1 *= g;
            bp_coeff[i].b2 *= g;
        }
    }

    printf("=== PPG sample-path filter: Chebyshev Type II band-pass ===\n");
    printf("Prototype order %d (-> %u biquads, 8th-order band-pass), "
           "stopband %.1f dB\n", CHEB2_ORDER, (unsigned)BP_SECTIONS,
           CHEB2_STOPBAND_DB);
    printf("-3 dB band %.4f - %.4f Hz, fs = %.1f Hz, unity gain at centre, "
           "Direct Form 1, causal\n", f_lo, f_hi, fs);
    report_stability();

    /* `| 1u` rounds up to odd, which also guarantees a minimum of 1 tap, so no
     * separate lower clamp is needed.  movavg_init() applies the upper clamp:
     * MOVAVG_MAX_TAPS bounds the ring, and a high -r can reach it. */
    movavg_init (&s_sample_ma,
                 ((uint32_t)(((PPG_SMOOTH_MS * fs) / 1000.0) + 0.5)) | 1u);
    printf("Post-filter smoothing: %u ms = %u taps at %.1f Hz\n",
           (unsigned)PPG_SMOOTH_MS, s_sample_ma.taps, fs);
    putchar ('\n');
    return (0);
}

/* --------------------------------------------------------------------------
 * Runtime
 * -------------------------------------------------------------------------- */

/**
 * @brief Prime all sections to the steady state for a constant input.
 *
 * Walks the DC level through the cascade, setting each section's input history
 * to what it sees and its output history to what it would settle at, so the
 * filter does not see a step from zero on the first sample.
 */
static void prime_sections (double x0)
{
    double   v = x0;
    double   num, den, out;
    uint32_t i;

    for (i = 0u; i < BP_SECTIONS; i++)
    {
        num = bp_coeff[i].b0 + bp_coeff[i].b1 + bp_coeff[i].b2;
        den = 1.0 + bp_coeff[i].a1 + bp_coeff[i].a2;
        out = (0.0 == den) ? 0.0 : ((num / den) * v);

        bp_state[i].x1 = v;
        bp_state[i].x2 = v;
        bp_state[i].y1 = out;
        bp_state[i].y2 = out;
        v = out;
    }
    bp_primed = 1u;
    return;
}

/**
 * @brief Run one Direct Form 1 biquad section over a single sample.
 */
static double run_section (bp_coeffs *p_c, bp_states *p_s, double in)
{
    double out = (p_c->b0 * in) + (p_c->b1 * p_s->x1) + (p_c->b2 * p_s->x2)
                 - (p_c->a1 * p_s->y1) - (p_c->a2 * p_s->y2);

    p_s->x2 = p_s->x1; p_s->x1 = in;
    p_s->y2 = p_s->y1; p_s->y1 = out;
    return (out);
}

/**
 * @brief Filter one integer ADC sample through the Chebyshev Type II bandpass.
 *
 * Output is AC-coupled (it swings about zero) with unity passband gain, so it
 * is on the same amplitude scale as the input pulse.  The caller adds its own
 * DC pedestal -- see PPG_FILTER_DC_PEDESTAL in ppg_common.h.
 */
double filter_int_sample (int32_t sample_value)
{
    double   v = (double)sample_value;
    uint32_t i;

    if (0u == bp_designed)
    {
        return (v);
    }
    if (0u == bp_primed)
    {
        prime_sections (v);
    }

    for (i = 0u; i < BP_SECTIONS; i++)
    {
        v = run_section (&bp_coeff[i], &bp_state[i], v);
    }
    return (v);
}

/**
 * @brief Initialise one moving-average instance.
 *
 * @param ps_ma Instance to initialise
 * @param taps  Window length in samples of whatever stream this instance sees.
 *              Clamped to MOVAVG_MAX_TAPS, which bounds the ring.
 */
void    movavg_init (struct_movavg *ps_ma, uint32_t taps)
{
    if (NULL == ps_ma) { return; }

    if (1u > taps)               { taps = 1u; }
    if (MOVAVG_MAX_TAPS < taps)  { taps = MOVAVG_MAX_TAPS; }

    memset (ps_ma->ring, 0x00, sizeof(ps_ma->ring));
    ps_ma->sum  = 0;
    ps_ma->wr   = 0u;
    ps_ma->taps = taps;
    return;
}

/**
 * @brief Push one sample through a moving average and return the smoothed value.
 *
 * Trailing (causal) average of the last `taps` samples, kept as a running sum so
 * the cost is one add and one subtract per sample whatever the window length.
 *
 * Before the window has filled, only the samples that have actually arrived are
 * averaged -- otherwise the zero-filled tail would drag the first outputs toward
 * zero.  A caller that waits for a full window before using the output never
 * reaches that branch, and gets the plain N-tap average from its first result.
 *
 * @param ps_ma        The instance
 * @param sample_value One input sample
 * @return             The smoothed value, or the input unchanged at one tap
 */
int32_t movavg_run (struct_movavg *ps_ma, int32_t sample_value)
{
    uint32_t slot;

    if ((NULL == ps_ma) || (2u > ps_ma->taps))
    {
        return (sample_value);
    }

    slot                = ps_ma->wr % ps_ma->taps;
    ps_ma->sum         -= (int64_t)ps_ma->ring[slot];
    ps_ma->ring[slot]   = sample_value;
    ps_ma->sum         += (int64_t)sample_value;
    ps_ma->wr++;

    if (ps_ma->taps > ps_ma->wr)
    {
        return ((int32_t)(ps_ma->sum / (int64_t)ps_ma->wr));
    }
    return ((int32_t)(ps_ma->sum / (int64_t)ps_ma->taps));
}

/**
 * @brief Group delay of a moving-average instance, in samples of its own stream.
 *
 * A trailing N-tap average delays its output by (N-1)/2 samples, so anything
 * located on the smoothed stream is that much late.  A causal filter cannot
 * centre its own output -- the value for sample n would need samples up to
 * n + (N-1)/2 -- so the delay is published here and each consumer puts its own
 * results back on the true time base.  See the block comment in ppg_common.h.
 *
 * @param ps_ma The instance
 * @return      Delay in samples; 0 when the window is a single tap.
 */
uint32_t movavg_group_delay (const struct_movavg *ps_ma)
{
    if ((NULL == ps_ma) || (2u > ps_ma->taps)) { return (0u); }
    return ((ps_ma->taps - 1u) / 2u);
}

/**
 * @brief Group delay of the sample-path smoothing stage, in input samples.
 *
 * 2 samples -- 16 ms -- at the 125 Hz design point.  Applied equally to every
 * fiducial, so beat INTERVALS, and therefore HR and HRV, are unaffected by it;
 * what it moves is each fiducial's absolute timestamp, which matters to anything
 * correlating this signal with another.  The detectors subtract it from the
 * indices they report.
 *
 * @return Delay in samples; 0 when smoothing is disabled.
 */
uint32_t smooth_group_delay (void)
{
    return (movavg_group_delay (&s_sample_ma));
}

/**
 * @brief Smooth one band-passed sample with the post-filter moving average.
 *
 * The sample path's instance of the shared moving average -- see movavg_run().
 *
 * @param sample_value One band-passed sample
 * @return        The smoothed value, or the input unchanged if the filter is
 *                not designed or the window rounds to a single tap
 */
int32_t smooth_int_sample (int32_t sample_value)
{
    if (0u == bp_designed)
    {
        return (sample_value);
    }
    return (movavg_run (&s_sample_ma, sample_value));
}
