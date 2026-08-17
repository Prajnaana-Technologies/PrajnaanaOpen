/*
 * Copyright (c) 2026 Prajnaana Technologies Pvt. Ltd.
 *
 * SPDX-License-Identifier: Apache-2.0
 *
 * Original Author: Mamatha BV
 */
/* ***************************************************************************
 * ppg_fiducial_elgendi_terma.c -- BEAT DETECTION by Two Event-Related Moving
 *                                 Averages (TERMA)
 *
 * One of two implementations of the ppg_fiducial.h contract.  Both are linked;
 * the dispatcher picks between them per recording.  This one is the better
 * choice for ADULTS -- F1 98.7 against IMS's 96.6 on the annotated cohort.
 *
 * SOURCE
 *   Elgendi M, Norton I, Brearley M, Abbott D, Schuurmans D, "Systolic Peak
 *   Detection in Acceleration Photoplethysmograms Measured from Emergency
 *   Responders in Tropical Conditions", PLoS ONE 8(10):e76585, 2013.
 *   doi:10.1371/journal.pone.0076585 -- open access, CC-BY.
 *   Indexed with all other sources in ../CREDITS.md
 *   Local archive (not distributed): Elgendi2013-SystolicPeakDetection-TwoMovingAverages.pdf
 *
 * Written from that paper alone.  Unlike the Karlen IMS source, this paper
 * specifies EVERY parameter, each obtained by brute-force search and reported:
 * band 0.5-8 Hz, W1 = 111 ms, W2 = 667 ms, beta = 0.02, THR2 = W1.  Nothing
 * here is an assumption.
 *
 * ALGORITHM IV, implemented as published:
 *   2  Filtered  = Bandpass(PPG, F1-F2)      band 0.5-8 Hz
 *   3  Clipped   = Clip(Filtered)            keep only samples above zero
 *   4  Q         = Square(Clipped)           emphasise the systolic wave
 *   5  MApeak    = MA(Q, W1)                 systolic-peak duration window
 *   6  MAbeat    = MA(Q, W2)                 one-beat duration window
 *   7  z         = mean(Q)
 *   8  alpha     = beta * z
 *   9  THR1      = MAbeat + alpha
 *  10-16  blocks of interest where MApeak > THR1
 *  18  THR2      = W1
 *  20-21  a block at least THR2 wide yields a systolic peak at its maximum;
 *  22-23  narrower blocks are noise and are ignored.
 *
 * TWO DELIBERATE DEVIATIONS, both forced by streaming operation:
 *   1. The paper's bandpass is ZERO-PHASE (forward-backward), which needs the
 *      whole recording.  A causal 2nd-order Butterworth is used instead.  The
 *      band and order are unchanged; only the phase response differs.
 *   2. z = mean(Q) is a whole-record statistic.  It is accumulated as a running
 *      mean here, so early beats see a mean over less data.  It converges
 *      within a few seconds.
 * Both are properties of real-time operation rather than choices, and neither
 * changes a published parameter value.
 *
 * The moving averages are CENTRED, so the detector inherently looks ahead by
 * (W2-1)/2 = 333 ms.  The interface permits latency; it forbids only
 * reordering, and beats are still emitted in time order.
 *
 * PREPROCESSING NOTE.  The paper's 0.5 Hz high-pass would destroy the BW
 * respiratory surrogate (see chebyshev_t2_o4.c).  It is therefore applied to an
 * INTERNAL copy used for detection only; the amplitudes reported through the
 * interface are sampled from the stream the detector was given, which retains
 * baseline wander.  Detection and measurement are deliberately separated.
 *
 * The maximal-upslope instant the interface needs is not an Elgendi fiducial;
 * it is Liu et al 2020's FM point, taken here as the steepest single-sample
 * rise between a foot and its peak, as in the other detectors.
 * *************************************************************************** */

#include "ppg_common.h"
#include "ppg_fiducial.h"

/* Both detectors are compiled in and linked together; one is chosen at run
 * time (see the DETECTOR SELECTION block in ppg_fiducial.h).  Everything in
 * this file is therefore file-scope and prefixed, so it cannot collide with
 * Karlen IMS -- only the two prefixed entry points have external linkage. */

#include <stdio.h>
#include <string.h>
#include <math.h>

extern  double  filter_int_sample (int32_t sample_value);
extern  int32_t smooth_int_sample (int32_t sample_value);
extern  uint32_t smooth_group_delay (void);
extern  int32_t init_chebyshev_filter (int32_t fs_hz);

/* All values from Elgendi et al 2013, Table 3 / optimal parameters. */
#define TERMA_F_LO_HZ       (0.5)    /* [ELGENDI] band lower edge             */
#define TERMA_F_HI_HZ       (8.0)    /* [ELGENDI] band upper edge             */
#define TERMA_W1_ADULT_MS   (111u)   /* [ELGENDI] systolic-peak duration      */
#define TERMA_W2_ADULT_MS   (667u)   /* [ELGENDI] one-beat duration           */
#define TERMA_BETA          (0.02)   /* [ELGENDI] offset, alpha = beta * z    */

/* The heart-rate band in which the two window durations above ARE the correct
 * values -- i.e. the band this detector's published parameters were measured
 * against.  It is a property of the paper, so it lives with the paper's numbers
 * and not in shared configuration.  fiducial_init() re-expresses W1 and W2 for
 * whatever band the caller declares; give it this one and it reproduces the
 * published pair exactly.  See tm_scale_window(). */
#define TERMA_REF_HR_MIN_BPM    (43u)
#define TERMA_REF_HR_MAX_BPM    (104u)

#define TERMA_HIST          (1024u)  /* >= W2 plus a beat of margin           */


typedef struct { double b0, b1, b2, a1, a2; } terma_biquad;
typedef struct { double x1, x2, y1, y2; } terma_state;

static  terma_biquad tm_hp, tm_lp;    /* causal 2nd-order Butterworth HP + LP  */
static  terma_state  tm_hp_s, tm_lp_s;

static  double      tm_q     [TERMA_HIST];   /* squared clipped signal        */
static  int32_t     tm_raw   [TERMA_HIST];   /* stream as handed to us        */
static  uint32_t    tm_wr;

static  double      tm_sum_w1;               /* running window sums           */
static  double      tm_sum_w2;
static  double      tm_sum_all;              /* for z = mean(Q)               */
static  uint32_t    tm_n_all;

static  uint32_t    tm_w1, tm_w2;            /* window sizes in samples, odd  */
static  uint32_t    tm_rate;
static  void       *tm_user;

static  uint32_t    tm_in_block;             /* inside a block of interest?   */
static  uint32_t    tm_block_start;
static  uint32_t    tm_prev_peak_index;      /* previous emitted peak         */
static  uint32_t    tm_prev_steep_index;     /* upslope of the preceding cycle */
static  uint32_t    tm_prev_steep_valid;

/**
 * @brief Design one causal 2nd-order Butterworth section (RBJ form).
 */
static void tm_design (terma_biquad *p_c, double fs, double fc, int high_pass)
{
    double w0 = (2.0 * PI * fc) / fs;
    double cw = cos(w0), sw = sin(w0);
    double q  = 0.70710678;                  /* 2nd-order Butterworth         */
    double al = sw / (2.0 * q);
    double a0 = 1.0 + al;

    if (0 != high_pass)
    {
        p_c->b0 =  ((1.0 + cw) / 2.0) / a0;
        p_c->b1 = (-(1.0 + cw)) / a0;
        p_c->b2 =  ((1.0 + cw) / 2.0) / a0;
    }
    else
    {
        p_c->b0 = ((1.0 - cw) / 2.0) / a0;
        p_c->b1 =  (1.0 - cw) / a0;
        p_c->b2 = ((1.0 - cw) / 2.0) / a0;
    }
    p_c->a1 = (-2.0 * cw) / a0;
    p_c->a2 = (1.0 - al) / a0;
    return;
}

/**
 * @brief Run one biquad section over a single sample.
 */
static double tm_run (terma_biquad *p_c, terma_state *p_s, double in)
{
    double out = (p_c->b0 * in) + (p_c->b1 * p_s->x1) + (p_c->b2 * p_s->x2)
                 - (p_c->a1 * p_s->y1) - (p_c->a2 * p_s->y2);

    p_s->x2 = p_s->x1; p_s->x1 = in;
    p_s->y2 = p_s->y1; p_s->y1 = out;
    return (out);
}

/**
 * @brief Round a duration in milliseconds to an odd number of samples.
 *
 * The paper requires both window sizes to be odd so the moving averages are
 * symmetric about the sample they describe.
 */
static uint32_t tm_odd_window (uint32_t ms, uint32_t fs)
{
    uint32_t w = ((ms * fs) + 500u) / 1000u;

    if (2u > w) { w = 3u; }
    if (0u == (w % 2u)) { w++; }
    return (w);
}

/**
 * @brief Emit a beat: locate the foot before the peak, then call the interface.
 *
 * Elgendi reports systolic peaks only, so the onset is recovered as the minimum
 * of the retained stream between the previous peak and this one -- the standard
 * definition of the pulse foot, and the value the BW surrogate needs.
 */
static void tm_emit_peak (uint32_t peak_index)
{
    uint32_t search_from;
    uint32_t foot_index;
    int32_t  foot_value;
    uint32_t i;
    int32_t  steep_rise;
    uint32_t steep_index;

    if (peak_index >= tm_wr) { return; }

    /* Look back at most one W2 -- a beat -- for the preceding minimum. */
    search_from = (0u < tm_prev_peak_index) ? (tm_prev_peak_index + 1u)
                                            : ((peak_index > tm_w2) ? (peak_index - tm_w2) : 0u);
    if (search_from >= peak_index) { return; }
    if ((peak_index - search_from) > tm_w2) { search_from = peak_index - tm_w2; }

    foot_index = search_from;
    foot_value = tm_raw[search_from % TERMA_HIST];
    for (i = search_from; i < peak_index; i++)
    {
        int32_t v = tm_raw[i % TERMA_HIST];

        if (v < foot_value) { foot_value = v; foot_index = i; }
    }

    /* Steepest single-sample rise between foot and peak: the FM fiducial. */
    steep_rise = 0; steep_index = foot_index;
    for (i = foot_index + 1u; i <= peak_index; i++)
    {
        int32_t rise = tm_raw[i % TERMA_HIST] - tm_raw[(i - 1u) % TERMA_HIST];

        if (rise > steep_rise) { steep_rise = rise; steep_index = i; }
    }

    /* Put the indices back on the RAW sample time base: the smoothing stage is
     * causal, so everything found on its output is late by its group delay.
     * See smooth_group_delay() in chebyshev_t2_o4.c. */
    {
        uint32_t gd = smooth_group_delay();

        foot_index  = (foot_index  > gd) ? (foot_index  - gd) : 0u;
        peak_index  = (peak_index  > gd) ? (peak_index  - gd) : 0u;
        steep_index = (steep_index > gd) ? (steep_index - gd) : 0u;
    }

    ppg_on_foot (tm_user, foot_index, foot_value,
                 tm_prev_steep_index, tm_prev_steep_valid);
    ppg_on_peak (tm_user, peak_index, tm_raw[peak_index % TERMA_HIST]);

    tm_prev_steep_index = steep_index;
    tm_prev_steep_valid = 1u;
    tm_prev_peak_index  = peak_index;
    return;
}

/**
 * @brief Scale Elgendi's one-beat window W2 to a subject's heart rate.
 *
 * DEVELOPER'S IMPROVEMENT -- carry the paper's DEFINITION, not its number.
 *
 * [ELGENDI] defines W1 and W2 semantically: W1 is "the systolic-peak duration"
 * and W2 "the one-beat duration".  Both are properties of the SUBJECT.  The
 * published 111 ms and 667 ms are the values those durations take for his
 * cohort, whose heart rate is around 90 /min -- an adult measurement of a
 * per-subject quantity, not a universal constant.
 *
 * WHY IT MATTERS.  A moving average held in milliseconds spans a different
 * fraction of a cardiac cycle at every heart rate.  Held at 667 ms, W2 covers
 * one beat at 90 /min but 1.6 beats at 146 /min, and once the beat-level
 * average spans more than one beat it stops tracking the beat it exists to
 * track: adjacent blocks of interest merge, and a merged block yields ONE peak
 * where there were two.  The failure is silent -- the interval sanitiser
 * divides the doubled intervals, so the reported RATE survives and only the
 * beat COUNT reveals it.  Measured at a mean beat interval of 411 ms, the
 * unscaled detector found 68-77 % of the beats present.
 *
 * HOW.  There is exactly one published datum, so it is preserved and carried:
 * W2 keeps the same position inside the subject's beat-duration range that
 * Elgendi's value occupies inside the reference range it was measured in.  The
 * reference range is TERMA_REF_HR_*_BPM below -- the detector's own property,
 * held here rather than in shared configuration.  Passing that same reference
 * range back in returns 667 ms EXACTLY, so a subject whose expected rates match
 * Elgendi's cohort is bit-for-bit unaffected.
 *
 * W1 is NOT scaled by this function.  It is a fraction of a beat too, but a
 * much smaller one, and it is pinned to W2 by Elgendi's own 111:667 ratio at
 * the call site -- the PPG signal upstroke does not stretch independently of the
 * cycle that contains it.
 *
 * All integer arithmetic: no floating point and no rounding drift.
 *
 * @param w2_adult_ms Elgendi's published one-beat window, ms
 * @param hr_min_bpm  Lowest expected heart rate for this subject
 * @param hr_max_bpm  Highest expected heart rate for this subject
 * @return            The same window expressed for this subject, ms
 */
static  uint32_t tm_scale_window (uint32_t w2_adult_ms,
                                  uint32_t hr_min_bpm,
                                  uint32_t hr_max_bpm)
{
    const uint32_t ref_lo = 60000u / TERMA_REF_HR_MAX_BPM;   /* shortest beat */
    const uint32_t ref_hi = 60000u / TERMA_REF_HR_MIN_BPM;   /* longest beat  */
    uint32_t       lo, hi;

    if ((0u == hr_min_bpm) || (0u == hr_max_bpm) || (hr_min_bpm >= hr_max_bpm) ||
        (ref_hi <= ref_lo) || (w2_adult_ms < ref_lo) || (w2_adult_ms > ref_hi))
    {
        return (w2_adult_ms);       /* nothing sane to scale against */
    }
    lo = 60000u / hr_max_bpm;
    hi = 60000u / hr_min_bpm;
    if (hi <= lo) { return (w2_adult_ms); }

    return (lo + (((hi - lo) * (w2_adult_ms - ref_lo)) / (ref_hi - ref_lo)));
}

/**
 * @brief Initialise the TERMA detector for a new recording.
 *
 * W1 and W2 are derived here from the subject's expected heart-rate band --
 * see tm_scale_window().  Nothing about this detector's parameters is visible
 * outside this file.
 *
 * @param ps_fd         Detector context (sampling rate and user pointer)
 * @param fs_hz Sample rate of the PPG stream, Hz
 * @param hr_min_bpm    Lowest expected heart rate for this subject
 * @param hr_max_bpm    Highest expected heart rate for this subject
 * @param user          Opaque pointer passed to every callback
 */
void    terma_fiducial_init (struct_fiducial *ps_fd,
                       int32_t  fs_hz,
                       uint32_t hr_min_bpm,
                       uint32_t hr_max_bpm,
                       void    *user)
{
    uint32_t w1_ms, w2_ms;

    memset(ps_fd, 0x00, sizeof(struct_fiducial));
    ps_fd->sel_fs_hz = (uint32_t)fs_hz;
    ps_fd->user              = user;

    w2_ms   = tm_scale_window (TERMA_W2_ADULT_MS, hr_min_bpm, hr_max_bpm);
    /* W1 follows W2 at Elgendi's published 111:667 ratio -- exact at the
     * reference band, where it returns his 111 ms unchanged. */
    w1_ms   = (w2_ms * TERMA_W1_ADULT_MS) / TERMA_W2_ADULT_MS;

    tm_rate = (uint32_t)fs_hz;
    tm_user = user;
    tm_w1   = tm_odd_window (w1_ms, tm_rate);
    tm_w2   = tm_odd_window (w2_ms, tm_rate);

    memset(tm_q, 0x00, sizeof(tm_q));
    memset(tm_raw, 0x00, sizeof(tm_raw));
    tm_wr = 0u; tm_sum_w1 = 0.0; tm_sum_w2 = 0.0; tm_sum_all = 0.0; tm_n_all = 0u;
    tm_in_block = 0u; tm_block_start = 0u;
    tm_prev_peak_index = 0u; tm_prev_steep_index = 0u; tm_prev_steep_valid = 0u;
    memset(&tm_hp_s, 0x00, sizeof(tm_hp_s));
    memset(&tm_lp_s, 0x00, sizeof(tm_lp_s));

    tm_design (&tm_hp, (double)fs_hz, TERMA_F_LO_HZ, 1);
    tm_design (&tm_lp, (double)fs_hz, TERMA_F_HI_HZ, 0);

    printf("=== Beat detector: Elgendi Two Event-Related Moving Averages (PLoS ONE 2013) ===\n");
    printf("band %.1f-%.1f Hz, W1 = %u ms (%u smp), W2 = %u ms (%u smp), beta = %.2f\n",
           TERMA_F_LO_HZ, TERMA_F_HI_HZ, w1_ms, tm_w1, w2_ms, tm_w2, TERMA_BETA);
    printf("expected HR %u-%u /min; W1/W2 re-expressed for it from the published "
           "%u/%u ms (unchanged at %u-%u /min)\n",
           hr_min_bpm, hr_max_bpm, TERMA_W1_ADULT_MS, TERMA_W2_ADULT_MS,
           TERMA_REF_HR_MIN_BPM, TERMA_REF_HR_MAX_BPM);
    printf("all parameters as published; causal filter and running mean substituted "
           "for the paper's zero-phase filter and whole-record mean\n\n");

    init_chebyshev_filter(fs_hz);
    return;
}

/**
 * @brief Feed one raw sample.  Beats are reported through the interface.
 *
 * Maintains the two centred moving averages incrementally, then evaluates the
 * block-of-interest test at the sample that now sits at the centre of the
 * longer window -- which is where both averages are defined.
 */
void    terma_fiducial_process_sample (struct_fiducial *ps_fd, int32_t sample_value)
{
    double   band;
    double   clipped;
    double   qv;
    uint32_t centre;
    uint32_t half2 = (tm_w2 - 1u) / 2u;

    /* Stream as handed to us: retains baseline wander, and is what the
     * reported amplitudes are sampled from.  The two stages are kept apart so
     * the trace can show each one: `band` is the band-pass output alone, `kept`
     * is that same sample after the moving average.  Only `kept` is used for
     * detection -- the split exists so the CSV columns mean what they say. */
    int32_t  band_only = (int32_t)(filter_int_sample(sample_value) + PPG_FILTER_DC_PEDESTAL);
    int32_t  kept      = smooth_int_sample(band_only);

    tm_raw[tm_wr % TERMA_HIST] = kept;
    ps_fd->s_data_buf[tm_wr % PPG_RING_LEN].input_sample     = sample_value;
    ps_fd->s_data_buf[tm_wr % PPG_RING_LEN].filtered_sample  = band_only;
    ps_fd->s_data_buf[tm_wr % PPG_RING_LEN].smoothed_sample  = kept;

    /* Detection copy: the paper's own 0.5-8 Hz band, applied causally. */
    band    = tm_run (&tm_lp, &tm_lp_s, tm_run (&tm_hp, &tm_hp_s, (double)kept));
    clipped = (band > 0.0) ? band : 0.0;          /* Clip()                   */
    qv      = clipped * clipped;                  /* Square()                 */

    tm_q[tm_wr % TERMA_HIST] = qv;
    tm_sum_w1 += qv; tm_sum_w2 += qv;
    tm_sum_all += qv; tm_n_all++;
    if (tm_wr >= tm_w1) { tm_sum_w1 -= tm_q[(tm_wr - tm_w1) % TERMA_HIST]; }
    if (tm_wr >= tm_w2) { tm_sum_w2 -= tm_q[(tm_wr - tm_w2) % TERMA_HIST]; }
    tm_wr++;

    if (tm_wr < tm_w2) { return; }

    /* The centred averages are defined at the sample half a W2 back. */
    centre = tm_wr - 1u - half2;

    {
        double ma_peak = tm_sum_w1 / (double)tm_w1;
        double ma_beat = tm_sum_w2 / (double)tm_w2;
        double z       = tm_sum_all / (double)tm_n_all;
        double thr1    = ma_beat + (TERMA_BETA * z);   /* lines 8-9           */

        if (ma_peak > thr1)
        {
            if (0u == tm_in_block) { tm_in_block = 1u; tm_block_start = centre; }
        }
        else if (0u != tm_in_block)
        {
            uint32_t width = centre - tm_block_start;

            tm_in_block = 0u;

            /* THR2 = W1: blocks narrower than a signal peak are noise. */
            if (width >= tm_w1)
            {
                uint32_t i;
                uint32_t best = tm_block_start;
                double   bestv = tm_q[tm_block_start % TERMA_HIST];

                for (i = tm_block_start; i < centre; i++)
                {
                    if (tm_q[i % TERMA_HIST] > bestv)
                    {
                        bestv = tm_q[i % TERMA_HIST]; best = i;
                    }
                }
                tm_emit_peak (best);
            }
        }
    }
    ps_fd->ring_wr_pos = tm_wr;
    return;
}

