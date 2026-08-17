/*
 * Copyright (c) 2026 Prajnaana Technologies Pvt. Ltd.
 *
 * SPDX-License-Identifier: Apache-2.0
 *
 * Original Author: Mamatha BV
 */
/* ***************************************************************************
 * ppg_fiducial_karlen_ims.c -- BEAT DETECTION by Incremental-Merge Segmentation
 *
 * An alternative implementation of the ppg_fiducial.h contract.  Both detectors
 * are linked; this one is selected at RUN time -- by the patient type, or by
 * -d ims.  See the dispatch table in ppg_fiducial.c.
 *
 * SOURCE
 *   Karlen W, Ansermino J M, Dumont G, "Adaptive Pulse Segmentation and
 *   Artifact Detection in Photoplethysmography for Mobile Applications",
 *   34th Annual International Conference of the IEEE EMBS, San Diego,
 *   28 Aug - 1 Sep 2012, pp 3131-4.  doi:10.1109/EMBC.2012.6346628
 *   Indexed with all other sources in ../CREDITS.md
 *   Local archive (not distributed): KarlenAnserminoDumont-2012-AdaptivePulseSegmentation.pdf
 *
 * Written from that paper alone.  Charlton et al 2016 used this detector for
 * their entire 314-algorithm comparison; the paper reports 98.93 % sensitivity
 * and 96.68 % positive predictive value on the CSL benchmark.
 *
 * WHAT THE PAPER SPECIFIES, AND WHAT IT DOES NOT
 * ----------------------------------------------
 * Specified, and implemented here exactly:
 *   - Algorithm 1, the incremental-merge rule: build a line over m+1 samples,
 *     merge it into the current line when the slope signs agree, otherwise
 *     finalise the current line and start a new one.
 *   - Algorithm 2, the adaptive amplitude thresholds, including their
 *     initialisation at theta_1 x 0.6 and theta_1 x 1.4 and the lambda
 *     "delay adaptation" flag.
 *   - Classification: a finalised line is an up-slope, down-slope or horizontal
 *     line if its amplitude lies within [ThA_low, ThA_high] OR its duration
 *     exceeds ThT = 0.03 s.
 *   - "Pulse peaks are identified as endpoints of the validated up-slopes."
 *   - Artifacts: up-slopes adjacent to a horizontal line (clipping or sensor
 *     disconnection); up-slopes whose amplitude exceeds ThA_high; and beats
 *     whose interval is under 50 % of the previous valid interval or under the
 *     240 ms physiological limit.
 *   - Preprocessing: "No other filtering than the standard band-pass filter
 *     applied by pulse oximeter manufacturers to remove the DC component is
 *     necessary."  The sample-path Chebyshev bandpass already provides that.
 *
 * NOT specified by the paper, and therefore tunable here rather than cited:
 *   - m, the segment length.  The paper says only that it is "dependent on the
 *     sampling rate".  Derived below from ThT, the one duration the paper does
 *     give, so the choice is at least tied to something stated.  Swept over
 *     m = 1..14 at 125 Hz: median F1 stayed within 94.9-95.9, i.e. a 14-fold
 *     change in m moves the result by one point.  m is NOT a sensitive
 *     parameter here.  Large m trades sensitivity for precision as expected
 *     (m=14: Se 93.0, PPV 97.7).
 *   - The four adaptation parameters a^{low,high}_{fast,slow}.  The paper names
 *     them and never gives values.  They were SWEPT against ECG R-peaks over
 *     the annotated adult reference set:
 *
 *       fast pair   median F1   worst F1
 *       (0.5, 1.2)     93.3       77.9
 *       (0.6, 1.4)     95.7       87.6    <- the paper's INIT ratios
 *       (0.8, 1.6)     96.9       88.0    <- shipped
 *       (0.7, 2.0)     97.0       88.1
 *
 *     The steady-state band needs to be WIDER than the ratios the paper uses to
 *     seed it, which is unsurprising: those seed from a single first amplitude.
 *     (0.7, 2.0) scored a hair higher but its upper bound is extreme enough to
 *     risk admitting artifacts on other data, and the 0.1 gap is well inside
 *     the noise of 12 recordings; (0.8, 1.6) is shipped as the more
 *     conservative of two statistically indistinguishable options.
 *
 *     The SLOW pair barely matters: across (0.6,1.4), (0.8,1.8) and (1.0,2.2)
 *     median F1 moved by at most 0.2.  Left at (0.8, 1.8).
 *
 *     These are tuned assumptions, not citations.
 *
 * The maximal-upslope instant that the interface requires is not a Karlen
 * fiducial; it is Liu et al 2020's FM point.  It is taken here as the steepest
 * single-sample rise inside a validated up-slope, which is the same definition
 * the other detector uses.
 * *************************************************************************** */

#include "ppg_common.h"
#include "ppg_fiducial.h"

/* Both detectors are compiled in and linked together; one is chosen at run
 * time (see the DETECTOR SELECTION block in ppg_fiducial.h).  Everything in
 * this file is therefore file-scope and prefixed, so it cannot collide with
 * Elgendi TERMA -- only the two prefixed entry points have external linkage. */

#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <math.h>

extern  double  filter_int_sample (int32_t sample_value);
extern  int32_t smooth_int_sample (int32_t sample_value);
extern  uint32_t smooth_group_delay (void);
extern  int32_t init_chebyshev_filter (int32_t fs_hz);

/* --------------------------------------------------------------------------
 * Parameters.  Cited ones first, assumed ones clearly marked.
 * -------------------------------------------------------------------------- */
#define IMS_TH_T_SEC        (0.03)  /* [KARLEN] duration threshold ThT        */
#define IMS_MIN_IBI_MS      (240u)  /* [KARLEN] 250 bpm physiological limit   */
#define IMS_IBI_FRACTION    (50u)   /* [KARLEN] reject under 50 % of previous */
#define IMS_INIT_LOW        (0.6)   /* [KARLEN] ThA_low  init = theta_1 x 0.6 */
#define IMS_INIT_HIGH       (1.4)   /* [KARLEN] ThA_high init = theta_1 x 1.4 */

/* ASSUMED -- the paper names these and does not give values.  Overridable at
 * build time so they can be swept against a reference; see docs/DESIGN.md. */
#ifndef IMS_A_FAST_LOW
#define IMS_A_FAST_LOW      (0.8)
#endif
#ifndef IMS_A_FAST_HIGH
#define IMS_A_FAST_HIGH     (1.6)
#endif
#ifndef IMS_A_SLOW_LOW
#define IMS_A_SLOW_LOW      (0.8)
#endif
#ifndef IMS_A_SLOW_HIGH
#define IMS_A_SLOW_HIGH     (1.8)
#endif

/* Segment length m.  The paper gives only "dependent on the sampling rate".
 * IMS_M_DIVISOR sets m = ThT_samples / divisor. */
#ifndef IMS_M_DIVISOR
#define IMS_M_DIVISOR       (2u)
#endif

/* Sample history.  A line never spans more than about one pulse, so a second
 * of signal is ample at any plausible rate. */
#define IMS_HIST            (512u)

/* Line classification. */
#define IMS_LINE_NONE       (0)
#define IMS_LINE_UP         (1)
#define IMS_LINE_DOWN       (2)
#define IMS_LINE_FLAT       (3)

/* --------------------------------------------------------------------------
 * Detector state.  Kept file-static so that struct_fiducial -- which describes
 * the other detector's state -- does not have to grow fields this one needs.
 * The context pointer is still what identifies the instance to the caller.
 * -------------------------------------------------------------------------- */
typedef struct
{
    uint32_t    start_index;        /* first sample of the line               */
    int32_t     start_value;
    uint32_t    end_index;          /* last sample of the line                */
    int32_t     end_value;
    int32_t     amplitude;          /* theta_z: end_value - start_value       */
    uint32_t    steep_index;        /* steepest single-sample rise inside it  */
    int32_t     steep_rise;
    int32_t     type;               /* IMS_LINE_*                             */
} struct_ims_line;

static  int32_t     ims_hist [IMS_HIST];    /* recent filtered samples        */
static  uint32_t    ims_wr;                 /* samples seen so far            */

static  uint32_t    ims_m;                  /* segment length in samples      */
static  uint32_t    ims_th_t_samples;       /* ThT expressed in samples       */
static  double      ims_th_low;             /* ThA_low                        */
static  double      ims_th_high;            /* ThA_high                       */
static  uint32_t    ims_th_init;            /* thresholds seeded yet?         */
static  uint32_t    ims_lambda;             /* Algorithm 2 delay flag         */

static  struct_ims_line ims_cur;            /* line under construction        */
static  uint32_t        ims_cur_open;
static  struct_ims_line ims_prev;           /* finalised, awaiting its successor */
/* The type of the line PRECEDING the pending up-slope.  [KARLEN] rejects an
 * up-slope adjacent to a flat line on EITHER side.  The line after it reaches
 * ims_emit() as next_type; the line before it must be carried here, because an
 * up-slope is judged one line later than it is found, and ims_prev by then
 * describes the up-slope itself. */
static  int32_t         ims_pending_prev_type;
static  uint32_t        ims_prev_valid;

/* One-line emission delay: an up-slope cannot be judged until the line that
 * follows it is known, because a following horizontal line marks it as an
 * artifact.  The contract permits latency, only not reordering. */
static  struct_ims_line ims_pending;
static  uint32_t        ims_pending_valid;

static  uint32_t    ims_last_beat_index;    /* previous accepted peak         */
static  uint32_t    ims_last_ibi_samples;   /* previous accepted interval     */
static  uint32_t    ims_prev_steep_index;   /* upslope of the preceding cycle */
static  uint32_t    ims_prev_steep_valid;

static  void       *ims_user;
static  uint32_t    ims_rate;

/**
 * @brief Fetch a sample from the history ring by absolute index.
 */
static int32_t ims_sample (uint32_t index)
{
    return (ims_hist[index % IMS_HIST]);
}

/**
 * @brief Classify a finalised line.
 *
 * Per the paper a line is classified when its amplitude lies inside the
 * adaptive band OR it lasts longer than ThT; the class itself follows the sign
 * of the slope.  Lines that are neither long enough nor large enough are noise
 * and carry no class.
 */
static int32_t ims_classify (const struct_ims_line *p_line)
{
    double   amp = fabs((double)p_line->amplitude);
    uint32_t dur = (p_line->end_index - p_line->start_index);

    if (((amp >= ims_th_low) && (amp <= ims_th_high)) || (dur > ims_th_t_samples))
    {
        if (0 < p_line->amplitude)  { return (IMS_LINE_UP); }
        if (0 > p_line->amplitude)  { return (IMS_LINE_DOWN); }
        return (IMS_LINE_FLAT);
    }
    return (IMS_LINE_NONE);
}

/**
 * @brief Algorithm 2 -- adapt ThA_low and ThA_high on an up-slope.
 *
 * Reproduces the paper's structure: an up-slope whose amplitude falls inside
 * the current band adapts the thresholds quickly and clears the delay flag;
 * one that falls outside adapts them slowly, and only after the flag has been
 * raised once.  The adaptation parameters themselves are assumptions -- see
 * the file header.
 */
static void ims_adapt (const struct_ims_line *p_line)
{
    double theta = fabs((double)p_line->amplitude);

    if (0u == ims_th_init)
    {
        ims_th_low  = theta * IMS_INIT_LOW;
        ims_th_high = theta * IMS_INIT_HIGH;
        ims_th_init = 1u;
        return;
    }

    if ((theta >= ims_th_low) && (theta <= ims_th_high))
    {
        ims_th_low  = (ims_th_low + (theta * IMS_A_FAST_LOW)) / 2.0;
        ims_th_high = theta * IMS_A_FAST_HIGH;
        ims_lambda  = 0u;
    }
    else
    {
        if (0u < ims_lambda)
        {
            ims_th_low  = (ims_th_low + (theta * IMS_A_SLOW_LOW)) / 2.0;
            ims_th_high = theta * IMS_A_SLOW_HIGH;
        }
        ims_lambda++;
    }
    return;
}

/**
 * @brief Decide whether a classified up-slope is a real beat, and emit it.
 *
 * Applies the paper's artifact rules that need the neighbouring lines, then
 * reports the beat through the interface: the foot is the up-slope's start and
 * the peak is its end ("pulse peaks are identified as endpoints of the
 * validated up-slopes").
 */
static void ims_emit (const struct_ims_line *p_up, int32_t next_type)
{
    uint32_t ibi_samples;
    uint32_t ibi_ms;

    /* Artifact: adjacent to a horizontal line -- clipping or disconnection. */
    if ((IMS_LINE_FLAT == next_type) ||
        (IMS_LINE_FLAT == ims_pending_prev_type))
    {
        return;
    }

    /* Artifact: amplitude above the adaptive upper threshold. */
    if (fabs((double)p_up->amplitude) > ims_th_high)
    {
        return;
    }

    /* Artifact: interval under the physiological limit, or under half the
     * previous valid interval. */
    if (0u < ims_last_beat_index)
    {
        if (p_up->end_index <= ims_last_beat_index) { return; }
        ibi_samples = p_up->end_index - ims_last_beat_index;
        ibi_ms      = (ibi_samples * 1000u) / ims_rate;

        if (IMS_MIN_IBI_MS > ibi_ms) { return; }
        if ((0u < ims_last_ibi_samples) &&
            ((ibi_samples * 100u) < (ims_last_ibi_samples * IMS_IBI_FRACTION)))
        {
            return;
        }
        ims_last_ibi_samples = ibi_samples;
    }

    /* Accepted.  Foot first -- it closes the cycle that began at the previous
     * foot, so it carries that cycle's maximal-upslope instant. */
    /* Indices are reported on the RAW sample time base -- the smoothing stage
     * is causal, so subtract its group delay.  See chebyshev_t2_o4.c. */
    {
        uint32_t gd    = smooth_group_delay();
        uint32_t f_idx = (p_up->start_index > gd) ? (p_up->start_index - gd) : 0u;
        uint32_t p_idx = (p_up->end_index   > gd) ? (p_up->end_index   - gd) : 0u;
        uint32_t s_idx = (ims_prev_steep_index > gd) ? (ims_prev_steep_index - gd) : 0u;

        ppg_on_foot (ims_user, f_idx, p_up->start_value,
                     s_idx, ims_prev_steep_valid);
        ppg_on_peak (ims_user, p_idx, p_up->end_value);
    }

    ims_prev_steep_index = p_up->steep_index;
    ims_prev_steep_valid = 1u;
    ims_last_beat_index  = p_up->end_index;
    return;
}

/**
 * @brief Finalise the line under construction: classify, adapt, emit.
 */
static void ims_finalise (void)
{
    struct_ims_line line = ims_cur;

    line.type = ims_classify (&line);

    /* An up-slope held from last time can now be judged, because the line that
     * follows it is known. */
    if (0u != ims_pending_valid)
    {
        /* [KARLEN] Algorithm 2 line 4 adapts on an up-slope only when NEITHER
         * neighbour has zero slope -- alpha_z > 0 & alpha_{z-1} != 0 &
         * alpha_{z+1} != 0.  Both neighbours are known only here, one line
         * after the up-slope was found, which is why the adaptation happens at
         * this point and not where the line was classified. */
        if ((IMS_LINE_FLAT != ims_pending_prev_type) &&
            (IMS_LINE_FLAT != line.type))
        {
            ims_adapt (&ims_pending);
        }
        ims_emit (&ims_pending, line.type);
        ims_pending_valid = 0u;
    }

    if (IMS_LINE_UP == line.type)
    {
        /* Captured HERE: ims_prev still describes the line before this
         * up-slope, and is replaced by `line` two statements below. */
        ims_pending_prev_type = (0u != ims_prev_valid) ? ims_prev.type
                                                       : IMS_LINE_NONE;
        ims_pending       = line;
        ims_pending_valid = 1u;
    }

    ims_prev       = line;
    ims_prev_valid = 1u;
    return;
}

/**
 * @brief Initialise the IMS detector for a new recording.
 *
 * THE HEART-RATE BAND IS NOT USED BY THIS DETECTOR, and that is a property of
 * the algorithm rather than an omission.  Incremental-merge segmentation has no
 * window whose length must be a fraction of a cardiac cycle: it grows line
 * segments from the data and merges them until the slope stops matching, so the
 * beat sets its own scale.  That is why it is the one detector here that does
 * not degrade at heart rates far from its authors' cohort -- and why it needs
 * no equivalent of the re-expression TERMA performs in tm_scale_window().
 *
 * The parameters it does need and the paper does not give (ThT, m) are tied to
 * each other below rather than to the rate.
 *
 * @param ps_fd         Unused by this detector beyond the sampling rate and
 *                      the user pointer; the interface is shared with the
 *                      alternative implementation.
 * @param fs_hz Sample rate of the PPG stream, Hz
 * @param hr_min_bpm    Expected heart-rate band, unused -- see above
 * @param hr_max_bpm    Expected heart-rate band, unused -- see above
 * @param user          Opaque pointer handed to every callback
 */
void    ims_fiducial_init (struct_fiducial *ps_fd,
                       int32_t  fs_hz,
                       uint32_t hr_min_bpm,
                       uint32_t hr_max_bpm,
                       void    *user)
{
    (void)hr_min_bpm;
    (void)hr_max_bpm;

    memset(ps_fd, 0x00, sizeof(struct_fiducial));
    ps_fd->sel_fs_hz = (uint32_t)fs_hz;
    ps_fd->user              = user;

    memset(ims_hist, 0x00, sizeof(ims_hist));
    ims_wr = 0u;
    ims_rate = (uint32_t)fs_hz;
    ims_user = user;

    /* m is not given by the paper.  Tie it to ThT, the one duration that is,
     * so a line segment is a fraction of the shortest classifiable line. */
    ims_th_t_samples = (uint32_t)((IMS_TH_T_SEC * (double)fs_hz) + 0.5);
#ifdef IMS_M_FORCE
    ims_m            = (uint32_t)IMS_M_FORCE;
#else
    ims_m            = (ims_th_t_samples / IMS_M_DIVISOR);
#endif
    if (1u > ims_m) { ims_m = 1u; }

    ims_th_low = 0.0; ims_th_high = 0.0; ims_th_init = 0u; ims_lambda = 0u;
    ims_cur_open = 0u; ims_prev_valid = 0u; ims_pending_valid = 0u;
    ims_pending_prev_type = IMS_LINE_NONE;
    ims_last_beat_index = 0u; ims_last_ibi_samples = 0u;
    ims_prev_steep_index = 0u; ims_prev_steep_valid = 0u;
    memset(&ims_cur, 0x00, sizeof(ims_cur));
    memset(&ims_prev, 0x00, sizeof(ims_prev));
    memset(&ims_pending, 0x00, sizeof(ims_pending));

    printf("=== Beat detector: Karlen Incremental-Merge Segmentation (EMBC 2012) ===\n");
    printf("segment length m = %u samples, ThT = %u samples (%.3f s), fs = %d Hz\n",
           ims_m, ims_th_t_samples, IMS_TH_T_SEC, fs_hz);
    printf("adaptation parameters are ASSUMED -- not given in the source paper\n\n");

    init_chebyshev_filter(fs_hz);
    return;
}

/**
 * @brief Feed one raw sample.  Beats are reported through the interface.
 *
 * Algorithm 1: every m samples a new line segment is formed from the first and
 * last of those samples (End-Point-Fit).  If its slope has the same sign as the
 * line under construction the two are merged; otherwise the current line is
 * finalised and a new one begins.
 */
void    ims_fiducial_process_sample (struct_fiducial *ps_fd, int32_t sample_value)
{
    int32_t  band_only;
    int32_t  filtered;
    uint32_t seg_start;
    uint32_t seg_end;
    int32_t  seg_amp;
    uint32_t i;

    /* The paper asks only for DC removal, which this provides.  The band-pass
     * and the moving average are kept apart so the trace can show each one;
     * only the smoothed sample is used for detection. */
    band_only = (int32_t)(filter_int_sample(sample_value) + PPG_FILTER_DC_PEDESTAL);
    filtered  = smooth_int_sample(band_only);

    ims_hist[ims_wr % IMS_HIST] = filtered;
    ps_fd->s_data_buf[ims_wr % PPG_RING_LEN].input_sample     = sample_value;
    ps_fd->s_data_buf[ims_wr % PPG_RING_LEN].filtered_sample  = band_only;
    ps_fd->s_data_buf[ims_wr % PPG_RING_LEN].smoothed_sample  = filtered;
    ims_wr++;
    ps_fd->ring_wr_pos = ims_wr;

    /* Need at least m+1 samples before a segment can be formed.  Using
     * (ims_m > ims_wr) here would let seg_start = seg_end - ims_m underflow on
     * the very first segment, producing a ~4e9 sample index. */
    if (ims_wr <= ims_m) { return; }
    if (0u != (ims_wr % ims_m)) { return; }

    /* End-Point-Fit over the segment that just completed. */
    seg_end   = ims_wr - 1u;
    seg_start = seg_end - ims_m;
    seg_amp   = ims_sample(seg_end) - ims_sample(seg_start);

    if (0u == ims_cur_open)
    {
        ims_cur.start_index = seg_start;
        ims_cur.start_value = ims_sample(seg_start);
        ims_cur.steep_rise  = 0;
        ims_cur.steep_index = seg_start;
        ims_cur_open = 1u;
    }
    else
    {
        /* Merge when the slope signs agree, per Algorithm 1. */
        int32_t cur_amp = ims_cur.amplitude;
        int      same   = (((0 < cur_amp) && (0 < seg_amp)) ||
                           ((0 > cur_amp) && (0 > seg_amp)) ||
                           ((0 == cur_amp) && (0 == seg_amp)));

        if (0 == same)
        {
            ims_finalise();
            ims_cur.start_index = seg_start;
            ims_cur.start_value = ims_sample(seg_start);
            ims_cur.steep_rise  = 0;
            ims_cur.steep_index = seg_start;
        }
    }

    ims_cur.end_index = seg_end;
    ims_cur.end_value = ims_sample(seg_end);
    ims_cur.amplitude = ims_cur.end_value - ims_cur.start_value;

    /* Steepest single-sample rise inside the line: the FM fiducial of
     * Liu et al 2020, which this interface requires and Karlen does not
     * define. */
    for (i = seg_start + 1u; i <= seg_end; i++)
    {
        int32_t rise = ims_sample(i) - ims_sample(i - 1u);

        if (rise > ims_cur.steep_rise)
        {
            ims_cur.steep_rise  = rise;
            ims_cur.steep_index = i;
        }
    }
    return;
}

