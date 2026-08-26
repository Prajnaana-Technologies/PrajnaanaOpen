/*
 * Copyright (c) 2026 Prajnaana Technologies Pvt. Ltd.
 *
 * SPDX-License-Identifier: Apache-2.0
 *
 * Original Author: Mamatha BV
 */
/* dup(), dup2(), fileno() and fdopen() are POSIX, not ISO C, and this tree
 * builds -std=c99, which hides them.  Asked for before the first system header,
 * because a feature-test macro set afterwards has no effect.  See the plot
 * stream's handle below for what they are for. */
#if !defined(_WIN32) && !defined(_POSIX_C_SOURCE)
#define _POSIX_C_SOURCE 200809L
#endif

#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <errno.h>
#include <math.h>

#include "ppg_common.h"
#include "ppg_fiducial.h"

/* ---------------------------------------------------------------------------
 * THE PLOT STREAM'S HANDLE.
 *
 * -p on hands the per-sample trace to a reader on stdout.  The difficulty is
 * that stdout already has an occupant: the banner, the per-block progress, the
 * per-window summaries and every sanitize_ibi() note are printf, and there are
 * some forty of them across five files.  Interleaved with data rows they make
 * the stream unparseable, and a stray progress line is indistinguishable from a
 * corrupt row.
 *
 * So the real stdout is duplicated aside as fp_data, and stdout ITSELF is
 * pointed at stderr.  Every existing printf then lands on stderr with no edit
 * at all, and fp_data is the only handle a reader ever sees.  Four lines here
 * instead of a sweep of five files -- and a sweep can miss a site, while this
 * cannot.
 *
 * _setmode(_O_BINARY) is not decoration.  In text mode the Windows CRT turns
 * every \n into \r\n, so the wire format would be LF on one platform and CRLF
 * on another.  Binary makes it exactly LF everywhere.
 * ------------------------------------------------------------------------- */
#if defined(_WIN32)
#include <io.h>
#include <fcntl.h>
#define PPG_DUP(fd_)            _dup(fd_)
#define PPG_DUP2(old_, new_)    _dup2((old_), (new_))
#define PPG_FILENO(fp_)         _fileno(fp_)
#define PPG_FDOPEN(fd_, mode_)  _fdopen((fd_), (mode_))
#define PPG_SET_BINARY(fp_)     ((void)_setmode(_fileno(fp_), _O_BINARY))
#else
#include <unistd.h>
#define PPG_DUP(fd_)            dup(fd_)
#define PPG_DUP2(old_, new_)    dup2((old_), (new_))
#define PPG_FILENO(fp_)         fileno(fp_)
#define PPG_FDOPEN(fd_, mode_)  fdopen((fd_), (mode_))
#define PPG_SET_BINARY(fp_)     ((void)0)
#endif

#define MAX_PPG_DATA                 (1024u)
#define PPG_DEFAULT_SAMPLE_LIMIT    (2048u)

/* ---------------------------------------------------------------------------
 * DECIDING THE INPUT SCALE INSTEAD OF DECLARING IT.
 *
 * The input is plain text with no declared scale, and getting -nu wrong is the
 * commonest way to get nothing out: with -nu 1 on a decimal recording every
 * sample truncates to 0 and no beat is ever found.
 *
 * It can be decided from the text, and doing so is safe rather than a guess,
 * because the analysis is scale-invariant above the truncation floor -- every
 * amplitude constant in the tree is a RATIO, not a count.  The IMS thresholds
 * multiply an amplitude measured from the signal itself, TERMA's alpha is
 * beta * z on the signal's own mean, and RR_MIN_PEAK_PROMINENCE and
 * RR_HARMONIC_RATIO are spectral ratios.  Two different scales on the same
 * recording therefore give the same rates, which is why the user guide can
 * offer -nu 10000 or -nu 4095 for one file.
 *
 * That makes the failure asymmetric: under-scaling is fatal, over-scaling is
 * free until an int32_t will not hold the sample.  So the job is not to
 * classify the format but to take as much resolution as the headroom allows.
 *
 * The decision is LEXICAL, on the token's text, not on its value.  An integer
 * recording contains no '.' anywhere, while "1.00000" contains one and is a
 * decimal recording whose value happens to be integral.  A test on the value
 * cannot tell those apart, and would rescale a perfectly good low-amplitude
 * integer channel for no reason.
 * ------------------------------------------------------------------------- */
#define PPG_TOKEN_MAX               (64)        /* keep "%63s" below in step */
#define NU_AUTO_MAX_DIGITS          (6)         /* 10^6 is resolution enough */
/* Below this many counts full-scale, integer truncation costs real signal, so a
 * decimal recording is scaled up until it clears it.  A digit count alone does
 * not get there: "1.5e-05" has one fractional digit and needs far more than ten.
 *
 * MEASURED, not chosen.  On bidmc_01 (vmax 0.6217), sweeping -nu 1000 / 10000 /
 * 100000 / 1000000 -- that is 622 / 6217 / 62170 / 621700 counts full-scale --
 * HR, HRV meanNN, SDNN, pNN50 and the beat count are BIT-IDENTICAL at every
 * scale from 6217 counts up: 0 rows of 57 differ.  Which is the scale-invariance
 * the whole decision rests on.
 *
 * HRV RMSSD is the exception, and it is the reason this is 1e5 and not 1e4.  It
 * is a root-mean-square of SUCCESSIVE differences, so a fiducial moved by one
 * sample by integer rounding shows up in it twice; it still differs by 0.073 %
 * over 24 windows between 6217 and 62170 counts, and is bit-identical from
 * 62170 counts upward.  At 622 counts everything degrades together -- that is
 * the truncation floor doing real damage, and the reason a floor exists.
 *
 * So: enough resolution that even the most quantisation-sensitive metric has
 * converged.  It costs nothing -- NU_SAFE_MAX leaves four more decades. */
#define NU_TARGET_COUNTS            (100000.0)
/* And never past this, so the cast in the reader cannot overflow.  Two-times
 * headroom under INT32_MAX; the filters need none of their own, being double
 * internally with an int64_t moving-average accumulator. */
#define NU_SAFE_MAX                 (1.0e9)
#define LOG_PPG_FILE(...)        if (fp_out)  { fprintf (fp_out, __VA_ARGS__); }
/* Every output handle may be NULL: OPEN_PPG_FILE reports the failure and lets
 * the run continue, so writes must be guarded or a read-only output directory
 * segfaults the process. */
#define LOG_CSV(fp_, ...)     if (fp_) { fprintf (fp_, __VA_ARGS__); }
#define CLOSE_PPG_FILE(fp_)   if (fp_) { fclose (fp_); }

#if defined(_MSC_VER) || defined(__STDC_LIB_EXT1__)
#define OPEN_PPG_FILE(fp_, name_, mode_)          \
do { \
    fopen_s(&fp_, name_, mode_);   \
    if (NULL == fp_)  {   \
        fprintf (stderr, "%s: ** cannot open %s\n", PPG_PROG_NAME, name_); \
    }   \
} while(0)
#else
#define OPEN_PPG_FILE(fp_, name_, mode_)  \
do { \
    fp_ = fopen(name_, mode_);   \
    if (NULL == fp_)  {   \
        fprintf (stderr, "%s: ** cannot open %s\n", PPG_PROG_NAME, name_); \
    }   \
} while(0)
#endif

/* ---------------------------------------------------------------------------
 * THE PATIENT-TYPE KNOB.
 *
 * Every position the knob can take, in one table.  This is the ONLY place in
 * the program that knows patient categories exist: the analysis layer and the
 * beat detector each receive one row and work from its numbers, so adding a
 * category -- or reading the selection from a device setting instead of a
 * command line -- is an edit here and nowhere else.
 *
 * Every value is cited in filter_bands.h.  Nothing is chosen here.
 * ------------------------------------------------------------------------- */
static const struct_subject_band g_subject [] = {
    { "neonate", NEONATE_NAME,
      NEONATE_RR_BAND_MIN_BPM, NEONATE_RR_BAND_MAX_BPM,
      NEONATE_HR_MIN_BPM,      NEONATE_HR_MAX_BPM,
      NEONATE_WINDOW_PTS,      NEONATE_WELCH_SEG,      RR_WINDOW_SLIDE_PTS,
      FIDUCIAL_IMS,
      RR_PSD_ACCUM_N,          RR_AGREEMENT_THRESHOLD,
      RR_PROG_MIN_PTS,         RR_MIN_PEAK_PROMINENCE,
      BP_HP_CORNER_HZ,         BP_LP_CORNER_HZ,        PPG_SMOOTH_MS },

    { "child",   CHILD_NAME,
      CHILD_RR_BAND_MIN_BPM,   CHILD_RR_BAND_MAX_BPM,
      CHILD_HR_MIN_BPM,        CHILD_HR_MAX_BPM,
      CHILD_WINDOW_PTS,        CHILD_WELCH_SEG,        RR_WINDOW_SLIDE_PTS,
      FIDUCIAL_TERMA,
      RR_PSD_ACCUM_N,          RR_AGREEMENT_THRESHOLD,
      RR_PROG_MIN_PTS,         RR_MIN_PEAK_PROMINENCE,
      BP_HP_CORNER_HZ,         BP_LP_CORNER_HZ,        PPG_SMOOTH_MS },

    { "adult",   ADULT_NAME,
      ADULT_RR_BAND_MIN_BPM,   ADULT_RR_BAND_MAX_BPM,
      ADULT_HR_MIN_BPM,        ADULT_HR_MAX_BPM,
      ADULT_WINDOW_PTS,        ADULT_WELCH_SEG,        RR_WINDOW_SLIDE_PTS,
      FIDUCIAL_TERMA,
      RR_PSD_ACCUM_N,          RR_AGREEMENT_THRESHOLD,
      RR_PROG_MIN_PTS,         RR_MIN_PEAK_PROMINENCE,
      BP_HP_CORNER_HZ,         BP_LP_CORNER_HZ,        PPG_SMOOTH_MS },
};
#define SUBJECT_COUNT   (sizeof(g_subject) / sizeof(g_subject[0]))
/* PPG_ANALYSIS_VERSION lives in ppg_common.h, so every module that stamps it can
 * see it.  See the note beside it there. */

/* The start-up banner, 80 columns wide.  The rules and the organisation line are
 * fixed strings.  The title line carries the program's name and its version:
 * both are compile-time constants, but neither has a length this file can know,
 * so the padding that centres them in the 76-column interior is computed once per
 * run rather than written into the literal.  The banner therefore stays centred
 * and stays 80 columns for any name and any version. */
#define PPG_BANNER_RULE \
    "/* ************************************************************************** */\n"
#define PPG_BANNER_ORG \
    "/*                  Prajnaana Technologies, Bengaluru, India                  */\n"
#define PPG_BANNER_INNER    (76)

#ifndef SUBJECT_DEFAULT
#define SUBJECT_DEFAULT (2)     /* adult -- the commonest setting, and the one
                                 * whose band is widest, so a forgotten -s
                                 * degrades accuracy rather than excluding a
                                 * rate outright.  -D-overridable so a device
                                 * that ships for one population can default to
                                 * it; every category stays reachable with -s. */
#endif

static  struct_ppg_analysis  s_ppg_analysis;
static  FILE             *fp_out = NULL;
static  FILE             *fp_in  = NULL;
        FILE             *fp_rr = NULL;
static  char              input_path[128]  = "ppg_data.txt\0";
static  char              output_path[160] = { 0 };
static  char              rr_filename [160] = { 0 };
/* -o <prefix> keeps successive runs from overwriting one another. */
static  char              out_prefix  [96]  = { 0 };
static  int32_t           ppg_data [MAX_PPG_DATA];

/* The plot stream, and the three settings that decide where samples come from
 * and what they are multiplied by.  All NULL/off unless asked for, so a run
 * that names none of them behaves exactly as it did before they existed. */
/* NOT static: the per-window half of the plot stream is written by
 * ppg_analysis.c, which owns those numbers.  Same arrangement as fp_rr above,
 * and for the same reason. */
        FILE             *fp_data       = NULL;   /* -p on: the real stdout   */
static  int32_t           s_plot_stream = 0;      /* -p on|off               */
static  int32_t           s_stdin_mode  = 0;      /* -i - | -i stdin         */
/* -nu auto is the DEFAULT.  On an integer recording it resolves to 1, which is
 * what the old default was, so nothing that worked before changes; on a decimal
 * recording it resolves to a scale that works, where the old default produced a
 * file of zeros and no beats at all.  An explicit -nu <n> switches it off. */
static  int32_t           s_nu_auto     = 1;
static  int32_t           s_nu_scale    = 0;      /* 0 until resolved         */
/* The first block is held as read while the scale is being decided, then
 * scaled in place.  Deciding at the end of the block it probed -- rather than
 * rewinding and reading again -- is what lets -i stdin work at all: a pipe
 * cannot be rewound. */
static  double            s_probe [MAX_PPG_DATA];


/* The detector owns beat finding; the analysis layer owns HR/HRV/RR/RRV.  They
 * meet only at the callbacks in ppg_fiducial.h. */
static  struct_fiducial   s_fiducial;

/* HOW LONG THE TRACE WAITS BEFORE IT STARTS.
 *
 * A sample's peak and foot are not decided when it arrives: a detector
 * recognises a beat from the signal that FOLLOWS it, and marks samples already
 * past.  So the trace lets the detector get ahead first, then runs a fixed
 * distance behind it -- one sample in, one row out -- and every row it writes
 * has marks that can no longer change.
 *
 * The wait is counted in beats rather than samples, because the distance a
 * detector reaches back is roughly a beat and scales with the subject's rate.
 * Waiting for this many puts the start comfortably past the deepest reach
 * either detector shows.
 *
 * MEASURED, NOT DERIVED.  Across the whole corpus this loses no mark; at four
 * beats a recording still loses some.  The margin is real but it is evidence,
 * not proof -- which is why the end-of-run summary reports marks placed
 * alongside marks written, so a recording that ever needs longer says so
 * instead of quietly dropping them. */
#define TRACE_START_BEATS       (6u)
/* A gap longer than this many rows with neither a peak nor a foot is reported.
 * Sized in main() from the subject's slowest beat. */
static  uint32_t          s_quiet_limit   = (PPG_RING_LEN - 1u);
static  uint32_t          s_log_fs_hz     = DEFAULT_PPG_SAMPLING_RATE;
static  uint32_t          s_unmarked_run  = 0u;

/**
 * @brief Report a run of unmarked rows, if it lasted long enough to mean
 *        something.
 *
 * The limit is several of the subject's slowest beats.  Below that a gap is an
 * ordinary interval between pulses and says nothing; above it, no rhythm inside
 * the declared band accounts for the silence, so the recording -- not the
 * tooling -- is what the blank flag columns are describing.
 *
 * @param n_end  Index of the first row that carried a mark again, or one past
 *               the last row written when the recording ends mid-run
 */
static  void    log_report_unmarked_run (uint32_t n_end)
{
    if (s_unmarked_run < s_quiet_limit) { return; }

    printf("  ** no peak or foot from sample %u to %u (%u samples, %u ms): "
           "no pulse was found there **\n",
           (unsigned)(n_end - s_unmarked_run), (unsigned)(n_end - 1u),
           (unsigned)s_unmarked_run,
           (unsigned)((s_unmarked_run * 1000u) / s_log_fs_hz));
    return;
}

/**
 * @brief Write one trace row, and keep track of how long the marks have been
 *        absent.
 *
 * @param ps_ppg  Analysis context, for the interpolation tracks
 * @param n       Sample index to write; must still be inside the sample ring
 */
static  void    log_ppg_row (const struct_ppg_analysis *ps_ppg, uint32_t n)
{
    int32_t k = (int32_t)(n % PPG_RING_LEN);

    /* A stretch with no peak and no foot in it says something about the
     * RECORDING -- no pulse was found there -- and must not be left to look
     * like the program having dropped the samples.  Announced as it goes past
     * rather than totalled at the end, so the message can name where in the
     * recording it happened. */
    if ((0 != s_fiducial.s_data_buf[k].it_is_peak) ||
        (0 != s_fiducial.s_data_buf[k].it_is_foot))
    {
        log_report_unmarked_run (n);
        s_unmarked_run = 0u;
    }
    else
    {
        s_unmarked_run++;
    }

    LOG_PPG_FILE(" %d, %d, %d, %d, %d, %d, %d, %d, %d, %d\n", (int32_t)n,
        s_fiducial.s_data_buf[k].input_sample,
        s_fiducial.s_data_buf[k].filtered_sample,
        s_fiducial.s_data_buf[k].smoothed_sample,
        ((0 != s_fiducial.s_data_buf[k].it_is_foot) ? s_fiducial.s_data_buf[k].smoothed_sample : 0),
        ((0 != s_fiducial.s_data_buf[k].it_is_peak) ? s_fiducial.s_data_buf[k].smoothed_sample : 0),
        ps_ppg->intp_trace[INTERPOLATE_BW][k],
        ps_ppg->intp_trace[INTERPOLATE_AM][k],
        ps_ppg->intp_trace[INTERPOLATE_FM][k],
        (ps_ppg->intp_trace[INTERPOLATE_AM][k] - ps_ppg->intp_trace[INTERPOLATE_BW][k]));

    /* THE PLOT STREAM: the same row, reduced to what a live trace needs.
     *
     * BOTH waveforms are sent, and which is which matters.  The display draws
     * two curves -- the recording as it arrived, and the signal the detectors
     * were actually handed -- so the raw sample comes first and the smoothed
     * one second.
     *
     * The MARKS BELONG ON THE SMOOTHED CURVE.  The fiducials were found there,
     * and drawn against the raw trace they sit beside the visible peaks rather
     * than on them: raw still carries the baseline wander and the noise that
     * the band-pass and the smoother removed, so its local maximum is not at
     * the fiducial's index.  A reader must therefore keep the two apart rather
     * than treating either as "the waveform".
     *
     * Peak and foot are FLAGS, and lose nothing by being flags.  The columns
     * above are smoothed_sample or 0, so on that scale the amplitude a flag
     * replaces is the sample already on the row.
     *
     * The index is sent even though the rows are contiguous, because a reader
     * that counted lines instead would silently shift every mark after a
     * dropped one, for the rest of the session, with nothing to notice it by.
     *
     * fflush per row is not optional.  A pipe's stdout is fully buffered, and
     * setvbuf(_IOLBF) is not honoured by every C library, so without this the
     * trace reaches the reader in block-sized bursts and a "live" plot advances
     * in visible jumps. */
    if (NULL != fp_data)
    {
        fprintf (fp_data, "%d,%d,%d,%d,%d\n", (int32_t)n,
                 s_fiducial.s_data_buf[k].input_sample,
                 s_fiducial.s_data_buf[k].smoothed_sample,
                 s_fiducial.s_data_buf[k].it_is_foot,
                 s_fiducial.s_data_buf[k].it_is_peak);
        (void)fflush (fp_data);
    }
    return;
}


/**
 * @brief Exact, case-insensitive match of a command-line option token.
 *
 * The parser used a prefix compare over the OPTION's length:
 * "-r" then also matched "-rrlo" and "-rrhi", so those options were unreachable
 * and their argument was consumed as the sampling rate instead.  Matching the
 * whole token removes that entire class of collision.
 */
static  int     opt_is (const char *arg, const char *opt)
{
    size_t i;

    /* Compared here rather than with strncasecmp(): that is POSIX, not ISO C,
     * so a strict -std=c99 build has no declaration for it, and MSVC spells it
     * _strnicmp.  Option tokens are ASCII, so this is both portable and exact. */
    for (i = 0u; ('\0' != arg[i]) && ('\0' != opt[i]); i++)
    {
        int a = (int)(unsigned char)arg[i];
        int b = (int)(unsigned char)opt[i];

        if (('A' <= a) && ('Z' >= a)) { a += ('a' - 'A'); }
        if (('A' <= b) && ('Z' >= b)) { b += ('a' - 'A'); }
        if (a != b) { return (0); }
    }
    return (('\0' == arg[i]) && ('\0' == opt[i]));
}

/* THE OPTION SET THAT TAKES AN ARGUMENT, STATED ONCE.
 *
 * Both the pair walk in main() and the leftover-token check after it ask this
 * table, so an option added to one can never be missing from the other.  The
 * failure that produced is quiet and wrong: a real option, left without its
 * argument, reported as "unknown".  -v, -h and --help are not here -- they take
 * no argument and are answered before the walk begins. */
static const char *const g_opt_with_arg [] = {
    "-c", "-d", "-i", "-nu", "-o", "-p", "-r", "-s"
};
#define OPT_WITH_ARG_COUNT  (sizeof(g_opt_with_arg) / sizeof(g_opt_with_arg[0]))

/**
 * @brief Is this token one of the options that takes exactly one argument?
 *
 * @param arg  One command-line token
 * @return     1 if the token is a known option, 0 otherwise
 */
static  int     opt_takes_arg (const char *arg)
{
    size_t  k;
    int     found = 0;

    for (k = 0u; k < OPT_WITH_ARG_COUNT; k++)
    {
        if (opt_is (arg, g_opt_with_arg[k])) { found = 1; }
    }
    return (found);
}

/**
 * @brief Print the identifying banner: who produced this build and which version.
 *
 * Emitted before any option is parsed, so that a run which dies on a bad
 * argument or a missing file still leaves a log naming the version that produced
 * it.  The one exception is -v/--version, answered before this and deliberately
 * emitting a single parseable line instead.  A log without a version cannot be traced
 * back to a build, and the runs most worth tracing are the ones that failed.
 */
static  void    print_banner (void)
{
    char        title [PPG_BANNER_INNER + 1];
    int32_t     pad;
    int32_t     len;

    (void)snprintf (title, sizeof(title), "%s %s",
                    PPG_PROG_NAME, PPG_ANALYSIS_VERSION);
    len = (int32_t)strlen (title);
    pad = (PPG_BANNER_INNER - len) / 2;
    if (0 > pad) { pad = 0; }

    fputs   (PPG_BANNER_RULE, stdout);
    printf  ("/*%*s%-*s*/\n", (int)pad, "", (int)(PPG_BANNER_INNER - pad), title);
    fputs   (PPG_BANNER_ORG,  stdout);
    fputs   (PPG_BANNER_RULE, stdout);
    return;
}

/**
 * @brief Push one block of raw PPG samples through the pipeline.
 *
 * On the first block it writes the CSV headers.
 *
 * Feeding and logging are deliberately not the same step.  The trace carries a
 * peak and a foot column, and a detector does not settle those when the sample
 * arrives -- it recognises a beat from the signal that follows, and marks
 * samples already past.  A row written the moment its sample was fed would
 * claim "not a peak" about one the detector is about to mark, and the plot
 * would show a waveform with most of its feet missing.
 *
 * So each sample is fed, and one row is written: the oldest sample the detector
 * says it has finished with.  Whatever it has not finished with when the input
 * ends is written by log_ppg_flush_tail(), so no sample is dropped.
 */
static  void    process_ppg_in_samples (struct_ppg_analysis *ps_ppg,
                                int32_t        *pi_ppg_data,
                                int32_t         ppg_data_count)
{
    uint32_t    n_done = ps_ppg->samples_processed;
    uint32_t    n_fed  = ps_ppg->samples_fed;
    int32_t     i = 0;

    /* Headers on the first block.  Keyed to what has been FED, not to what has
     * been written: the trace writes nothing until the detector has found a
     * beat, and on a poor recording that can take longer than the first block,
     * which would have the headers emitted a second time. */
    if (0u == n_fed)
    {
        LOG_CSV(fp_rr, "Time(sec),AM_RR,BW_RR,FM_RR,AM_q,BW_q,FM_q,"
                       "TD_RR,AVG_RR,Method,N_used,Spread_bpm,"
                       "RRV_SD_ms,RRV_RMSSD_ms,RRV_intervals,"
                       "HR_bpm,HRV_meanNN_ms,HRV_SDNN_ms,HRV_RMSSD_ms,HRV_pNN50_pct,HRV_n,"
                       "Version=" PPG_ANALYSIS_VERSION "\n");
        LOG_CSV(s_ppg_analysis.s_intp_peak.fp_est_rr, "Index,Peak_raw,Peak_Mvg,Version=" PPG_ANALYSIS_VERSION "\n");
        LOG_CSV(s_ppg_analysis.s_intp_foot.fp_est_rr, "Index,Foot_raw,Foot_Mvg,Version=" PPG_ANALYSIS_VERSION "\n");
        LOG_CSV(s_ppg_analysis.s_intp_freq.fp_est_rr, "Index,FM_raw,FM_Mvg,Version=" PPG_ANALYSIS_VERSION "\n");

        LOG_PPG_FILE("# recording: %s\n", input_path);
        LOG_PPG_FILE("Index,InputSample,Chebyshev,Smoothed,ppg_foot,ppg_peak,interp_foot,interp_peak,interp_fm,AM-signal,"
                     "Version=" PPG_ANALYSIS_VERSION "\n");
    }
    for (i = 0; i < ppg_data_count; i++)
    {
        fiducial_process_sample (&s_fiducial, pi_ppg_data[i]);
        n_fed++;

        /* Let the detector get its first few beats out before writing
         * anything -- see TRACE_START_BEATS. */
        if ((TRACE_START_BEATS > s_fiducial.peak_count) ||
            (TRACE_START_BEATS > s_fiducial.foot_count))
        {
            continue;
        }

        /* One sample in, one row out.  Whatever is still unwritten when the
         * input ends is flushed by log_ppg_flush_tail(). */
        log_ppg_row (ps_ppg, n_done);
        n_done++;
    }

    ps_ppg->samples_processed = n_done;
    ps_ppg->samples_fed       = n_fed;
    return;
}

/**
 * @brief Write the rows still in the pipe once the input has ended.
 *
 * The trace runs behind the input, so when the last sample has been fed some
 * rows have not been written yet.  Nothing more will arrive to change them, so
 * whatever marks they carry now is all they will ever carry -- the one place
 * the trace rests on less evidence than elsewhere, and the reason it is done
 * here rather than pretended away.
 *
 * @param ps_ppg  Analysis context
 */
static  void    log_ppg_flush_tail (struct_ppg_analysis *ps_ppg)
{
    while (ps_ppg->samples_processed < ps_ppg->samples_fed)
    {
        log_ppg_row (ps_ppg, ps_ppg->samples_processed);
        ps_ppg->samples_processed++;
    }
    log_report_unmarked_run (ps_ppg->samples_processed);
    return;
}

/**
 * @brief Parse a whole-number option argument, strictly.
 *
 * `atoi()` cannot distinguish "0" from "not a number": it returns 0 for both,
 * and stops silently at the first non-digit, so `-c abc` used to select the
 * whole file and `-c 12abc` would be accepted as 12.  A monitor must not
 * quietly do something other than what the operator typed.
 *
 * @param arg     Token to parse
 * @param p_out   Receives the value on success
 * @return 1 on success, 0 if the token is empty, non-numeric, has trailing
 *         characters, or is out of int32_t range
 */
static  int     parse_int (const char *arg, int32_t *p_out)
{
    char    *end = NULL;
    long     v;

    if ((NULL == arg) || ('\0' == arg[0])) { return (0); }
    errno = 0;
    v = strtol (arg, &end, 10);
    if ((NULL == end) || ('\0' != *end))   { return (0); }   /* trailing junk */
    if (0 != errno)                        { return (0); }   /* over/underflow */
    if ((v > 2147483647L) || (v < -2147483647L)) { return (0); }
    *p_out = (int32_t)v;
    return (1);
}

/* The scale-decision helpers, defined below read_sample_block() because that is
 * the only caller.  Declared here so a doc comment always sits immediately
 * above the function it describes. */
static  int32_t token_frac_digits (const char *tok);
static  void    resolve_nu_scale (int32_t frac_max, double vmax, uint32_t n);
static  int     scale_sample (double v, uint32_t idx, int32_t *p_out);

/**
 * @brief Fill one block from the input file.
 *
 * The read is factored out so the caller works in whole BLOCKS rather than in
 * single samples.  That removes the duplicated flush the sample-at-a-time form
 * needed: a short block is simply the last block, handled by the same path as
 * every other one.
 *
 * @param fp        Open input file, or stdin under -i -
 * @param ps_dst    Destination block, at least MAX_PPG_DATA entries
 * @param budget    Samples still permitted by -c
 * @return Samples placed in @p ps_dst; short means end of input or budget
 */
static  int32_t read_sample_block (FILE *fp, int32_t *ps_dst, uint32_t budget)
{
    uint32_t cap      = (budget < MAX_PPG_DATA) ? budget : MAX_PPG_DATA;
    uint32_t n        = 0u;
    int32_t  frac_max = 0;
    double   vmax     = 0.0;
    /* Probing happens on the first block that carries samples and never again:
     * after it s_nu_scale is set, and every later block takes the direct path. */
    int32_t  probing  = ((0 != s_nu_auto) && (0 >= s_nu_scale)) ? 1 : 0;
    char     tok [PPG_TOKEN_MAX];

    while (n < cap)
    {
        double       v   = 0.0;
        char        *end = NULL;
        const char  *num = NULL;

        /* Read the TOKEN, not the number.  fscanf("%lf") hands back the value
         * and throws the text away, and the text is what says whether this
         * recording is written in decimals -- see token_frac_digits().  It also
         * makes the check below possible: "%lf" accepts "1.5x" as 1.5 and leaves
         * the "x" to fail the NEXT read, so a malformed file used to stop
         * without ever saying what was wrong with it. */
#if defined(_MSC_VER) || defined(__STDC_LIB_EXT1__)
        if (1 != fscanf_s (fp, "%63s", tok, (unsigned)sizeof(tok)))
#else
        if (1 != fscanf (fp, "%63s", tok))
#endif
        {
            break;                              /* end of input */
        }

        /* A UTF-8 byte-order mark on the very first token is tolerated, not
         * refused.  Windows PowerShell prepends one when it pipes text into a
         * native program, so `Get-Content rec.txt | ppg_analysis -i stdin` --
         * the obvious way to feed this on Windows -- would otherwise fail on
         * sample 0 over three invisible bytes.  Only the first token can carry
         * one, and only ever at its start. */
        num = tok;
        if ((0u == n) &&
            (0xEFu == (unsigned char)num[0]) &&
            (0xBBu == (unsigned char)num[1]) &&
            (0xBFu == (unsigned char)num[2]))
        {
            num += 3;
        }

        errno = 0;
        v = strtod (num, &end);
        if ((NULL == end) || (num == end) || ('\0' != *end))
        {
            (void)fflush (stdout);
            fprintf (stderr, "%s: ** sample %u is not a number ('%s'). "
                     "Nothing further was read.\n",
                     PPG_PROG_NAME, (unsigned)n, num);
            break;
        }
        /* "nan" and "inf" are both accepted by strtod, and neither survives a
         * cast to int32_t as anything but undefined behaviour. */
        if (0 == isfinite (v))
        {
            (void)fflush (stdout);
            fprintf (stderr, "%s: ** sample %u is not finite ('%s'). "
                     "Nothing further was read.\n",
                     PPG_PROG_NAME, (unsigned)n, tok);
            break;
        }

        if (0 != probing)
        {
            int32_t d = token_frac_digits (num);
            double  a = (0.0 > v) ? -v : v;

            if (d > frac_max) { frac_max = d; }
            if (a > vmax)     { vmax     = a; }
            s_probe[n] = v;
        }
        else if (0 == scale_sample (v, n, &ps_dst[n]))
        {
            break;
        }
        else
        {
            /* scaled and stored */
        }
        n++;
    }

    /* The probed block is scaled in place, now that there is a scale to use. */
    if ((0 != probing) && (0u < n))
    {
        uint32_t i;

        resolve_nu_scale (frac_max, vmax, n);
        for (i = 0u; i < n; i++)
        {
            if (0 == scale_sample (s_probe[i], i, &ps_dst[i]))
            {
                n = i;                          /* keep what was representable */
                break;
            }
        }
    }
    return ((int32_t)n);
}

/**
 * @brief Count the digits after the decimal point in a token's TEXT.
 *
 * Text, not value: "1.00000" answers 5, and an integer token answers 0.  That
 * distinction is the whole basis of the scale decision -- a test on the value
 * cannot make it.
 *
 * The digit run stops at the first character that is not a digit, so an exponent
 * form such as "1.02979e+00" answers 5 rather than running into the exponent.
 * The exponent still shifts the magnitude, which is why resolve_nu_scale() also
 * looks at how large the samples actually are.
 *
 * @param tok  One whitespace-delimited input token
 * @return Digits between the '.' and the first non-digit after it
 */
static  int32_t token_frac_digits (const char *tok)
{
    const char  *p = strchr (tok, '.');
    int32_t      n = 0;

    if (NULL == p) { return (0); }
    for (p++; ('0' <= *p) && ('9' >= *p); p++) { n++; }
    return (n);
}

/**
 * @brief Decide the input scale from what the probed block looked like.
 *
 * The decision goes on the log, because a monitor that quietly picks a scale is
 * worse than one that says which it picked: the number chosen here changes every
 * amplitude in the trace, even though it changes no rate.
 *
 * @param frac_max  Most fractional digits seen in any token's text
 * @param vmax      Largest absolute value seen
 * @param n         Samples the decision is based on
 */
static  void    resolve_nu_scale (int32_t frac_max, double vmax, uint32_t n)
{
    int32_t scale  = 1;
    int32_t digits = (NU_AUTO_MAX_DIGITS < frac_max) ? NU_AUTO_MAX_DIGITS
                                                     : frac_max;

    /* No '.' anywhere means integer counts, which are already usable: left
     * exactly alone, so a recording that ran before this existed still produces
     * the identical trace.  An all-zero probe is left alone too -- there is
     * nothing in it to size a scale from, and saying so beats inventing one. */
    if ((0 < digits) && (0.0 < vmax))
    {
        int32_t i;

        for (i = 0; i < digits; i++) { scale *= 10; }

        while ((((double)scale * vmax) < NU_TARGET_COUNTS) &&
               (scale <= (INT32_MAX / 10)))
        {
            scale *= 10;
        }
        while ((1 < scale) && (((double)scale * vmax) > NU_SAFE_MAX))
        {
            scale /= 10;
        }
    }

    s_nu_scale = scale;
    printf ("Input scale: -nu %d (auto: %s", (int)scale,
            (0 == frac_max) ? "integer text" : "decimal text");
    if (0 != frac_max)
    {
        printf (", %d fractional digit%s", (int)frac_max,
                (1 == frac_max) ? "" : "s");
    }
    printf (", max |v| = %g over %u samples)\n", vmax, (unsigned)n);
    return;
}

/**
 * @brief Turn one value into a sample at the resolved scale, or refuse it.
 *
 * A value the cast cannot represent is REFUSED, not truncated.  Casting a
 * non-finite or out-of-range double to int32_t is undefined behaviour, and a
 * truncating cast turns 1e300 into a plausible-looking sample that the whole
 * analysis then runs on.
 *
 * @param v       Value as read
 * @param idx     Index within the block, for the diagnostic
 * @param p_out   Receives the scaled sample
 * @return 1 on success, 0 if the value was refused
 */
static  int     scale_sample (double v, uint32_t idx, int32_t *p_out)
{
    double scaled = (double)s_nu_scale * v;

    if ((0 == isfinite (scaled)) ||
        (scaled < (double)INT32_MIN) || (scaled > (double)INT32_MAX))
    {
        (void)fflush (stdout);
        fprintf (stderr, "%s: ** sample %u is not a representable number "
                 "(%g at -nu %d). Nothing further was read.\n",
                 PPG_PROG_NAME, (unsigned)idx, v, (int)s_nu_scale);
        return (0);
    }
    *p_out = (int32_t)scaled;
    return (1);
}

/**
 * @brief Program entry point: parse the command line and run the PPG pipeline.
 *
 * Opens the input recording and the trace/RR output files, initialises the
 * detector and the analysis layer, then reads samples in MAX_PPG_DATA blocks
 * and hands each block to process_ppg_in_samples() until the requested sample count is
 * processed.
 */
int32_t main (int32_t argc, char *argv[])
{
    int32_t     total_ppg_data_count = 0;
    int32_t     fs_hz       = DEFAULT_PPG_SAMPLING_RATE;
    int32_t     i;
    uint32_t    sample_budget  = PPG_DEFAULT_SAMPLE_LIMIT;
    int32_t     subject_idx         = SUBJECT_DEFAULT;
    /* -1 = use whatever the selected patient type asks for.  The override
     * exists so the two detectors can be compared on the same recording. */
    int32_t     detector_override   = -1;
    int32_t     want_help           = 0;

    /* Answered before anything else is printed, so -v emits exactly one line and
     * a script can parse it.  Also before the option walk further down, which
     * advances two tokens at a time -- a flag taking no argument breaks that,
     * which is why -h is recognised here and not in the walk. */
    for (i = 1; i < argc; i++)
    {
        if (opt_is(argv[i], "-v") || opt_is(argv[i], "--version"))
        {
            printf ("%s %s\n", PPG_PROG_NAME, PPG_ANALYSIS_VERSION);
            return (0);
        }
        if (opt_is(argv[i], "-h") || opt_is(argv[i], "--help"))
        {
            want_help = 1;
        }
        /* -p is decoded HERE as well as in the walk below, because the plot
         * stream has to be set up before print_banner() -- which is the very
         * next statement.  Left to the walk, the banner would already have gone
         * down the pipe as the reader's first "data" row.  The walk still
         * validates the argument, so a typo is still refused. */
        if (opt_is(argv[i], "-p") && ((i + 1) < argc) &&
            opt_is(argv[i + 1], "on"))
        {
            s_plot_stream = 1;
        }
    }

    /* THE FOUR LINES THAT CLEAR STDOUT FOR DATA.  See the note beside PPG_DUP.
     * Everything printf writes from here on lands on stderr; fp_data is the only
     * handle that reaches a reader. */
    if (0 != s_plot_stream)
    {
        int fd_data = PPG_DUP (PPG_FILENO (stdout));

        if (0 <= fd_data) { fp_data = PPG_FDOPEN (fd_data, "w"); }
        if (NULL == fp_data)
        {
            fprintf (stderr, "%s: ** cannot duplicate stdout for the plot "
                     "stream. Nothing was run.\n", PPG_PROG_NAME);
            return (-1);
        }
        PPG_SET_BINARY (fp_data);
        (void)PPG_DUP2 (PPG_FILENO (stderr), PPG_FILENO (stdout));
    }

    print_banner ();
    putchar ('\n');

    if ((2 > argc) || (0 != want_help))
    {
        printf ("\n%s -- options\n", PPG_PROG_NAME);
        printf ("Usage: %s [options]\n\n", PPG_PROG_NAME);
        printf ("  -i   <input_filename>       recording to analyse;\n");
        printf ("                              '-' or 'stdin' reads samples from stdin\n");
        printf ("  -nu  <scale|auto>           auto (default) decides it from the input\n");
        printf ("                              text; 1 = integer ADC counts,\n");
        printf ("                              10000 = floating point, 5 decimal digits\n");
        printf ("  -p   <on|off>               off (default). on streams three row shapes\n");
        printf ("                              to stdout for a live plot, told apart by their\n");
        printf ("                              first character, and moves the whole console\n");
        printf ("                              log to stderr:\n");
        printf ("                                <digit>  index,raw,smoothed,foot,peak\n");
        printf ("                                H        time,heart rate -- one per beat\n");
        printf ("                                R        the per-window metrics\n");
        printf ("  -r   <rate>                 sampling rate, Hz (default %u)\n", DEFAULT_PPG_SAMPLING_RATE);
        printf ("  -c   <#samples>             0 = the whole file\n");
        printf ("  -o   <prefix>               prefix for ppg_analysis.csv and RR_Data.csv,\n");
        printf ("                              so successive runs do not overwrite\n");
        printf ("  -v                          print the version and exit\n");
        printf ("  -h                          print this help and exit\n");
        printf ("  -s   <subject>              patient type: ");
        for (i = 0; i < (int32_t)SUBJECT_COUNT; i++)
        {
            printf ("%s%s", (0 == i) ? "" : " | ", g_subject[i].name);
        }
        printf (" (default %s)\n", g_subject[SUBJECT_DEFAULT].name);
        for (i = 0; i < (int32_t)SUBJECT_COUNT; i++)
        {
            printf ("         %-9s %-22s RR %2u-%2u /min, HR %3u-%3u, "
                    "window %u, segment %u, detector %s\n",
                    g_subject[i].name, g_subject[i].description,
                    g_subject[i].rr_min_bpm, g_subject[i].rr_max_bpm,
                    g_subject[i].hr_min_bpm, g_subject[i].hr_max_bpm,
                    g_subject[i].window_pts, g_subject[i].welch_seg,
                    fiducial_name((enum_fiducial)g_subject[i].detector));
        }
        printf ("  -d   <detector>             override the patient type's choice\n");
        for (i = 0; i < (int32_t)FIDUCIAL_COUNT; i++)
        {
            printf ("         %-9s %s\n", fiducial_name((enum_fiducial)i),
                    fiducial_describe((enum_fiducial)i));
        }
        putchar ('\n');

        /* -h was ASKED for, so it succeeds.  Reaching the same list by running
         * with no arguments still fails, deliberately and as documented: that
         * path cannot open the default input, and a script must not treat it as
         * a success. */
        if (0 != want_help)
        {
            return (0);
        }
    }
    else
    {
        i = 1;
        while ((i + 1) < argc)      /* every option takes exactly one argument */
        {
            /* The table is the authority on what an option IS; the chain below
             * only decodes the ones it knows.  Asking it here means the same
             * answer is given to the leftover-token check after the loop. */
            if (0 == opt_takes_arg (argv[i]))
            {
                /* Fatal, not a note.  An unknown option consumes its argument
                 * too, so continuing would swallow the NEXT option as well and
                 * run with settings the user never asked for. */
                fprintf(stderr, "%s: ** unknown option '%s'. Nothing was run.\n",
                        PPG_PROG_NAME, argv[i]);
                return (-1);
            }

            if (opt_is(argv[i], "-r"))
            {
                if (0 == parse_int (argv[i+1], &fs_hz))
                {
                    fprintf(stderr, "%s: ** -r %s is not a whole number.\n",
                            PPG_PROG_NAME, argv[i+1]);
                    return (-1);
                }
                if ((MIN_SAMPLING_RATE_HZ > fs_hz) ||
                    (MAX_SAMPLING_RATE_HZ < fs_hz))
                {
                    fprintf(stderr, "%s: ** -r %d is outside %d-%d Hz. A non-positive "
                            "rate makes every filter coefficient NaN.\n",
                            PPG_PROG_NAME, fs_hz, MIN_SAMPLING_RATE_HZ,
                            MAX_SAMPLING_RATE_HZ);
                    return (-1);
                }
            }
            else if (opt_is(argv[i], "-s"))
            {
                int32_t j;

                subject_idx = -1;
                for (j = 0; j < (int32_t)SUBJECT_COUNT; j++)
                {
                    if (opt_is(argv[i+1], g_subject[j].name)) { subject_idx = j; }
                }
                if (0 > subject_idx)
                {
                    /* Refused, not defaulted.  Silently analysing a neonate
                     * with an adult band is the largest single source of
                     * respiratory-rate error, and it leaves no trace. */
                    fprintf(stderr, "%s: ** -s %s is not a patient type. Use one of:",
                            PPG_PROG_NAME, argv[i+1]);
                    for (j = 0; j < (int32_t)SUBJECT_COUNT; j++)
                    {
                        fprintf(stderr, " %s", g_subject[j].name);
                    }
                    fputc ('\n', stderr);
                    return (-1);
                }
            }
            else if (opt_is(argv[i], "-d"))
            {
                int32_t j;

                detector_override = -1;
                for (j = 0; j < (int32_t)FIDUCIAL_COUNT; j++)
                {
                    if (opt_is(argv[i+1], fiducial_name((enum_fiducial)j)))
                    {
                        detector_override = j;
                    }
                }
                if (0 > detector_override)
                {
                    fprintf(stderr, "%s: ** -d %s is not a detector. Use one of:",
                            PPG_PROG_NAME, argv[i+1]);
                    for (j = 0; j < (int32_t)FIDUCIAL_COUNT; j++)
                    {
                        fprintf(stderr, " %s", fiducial_name((enum_fiducial)j));
                    }
                    fputc ('\n', stderr);
                    return (-1);
                }
            }
            else if (opt_is(argv[i], "-c"))
            {
                int32_t want = 0;

                if (0 == parse_int (argv[i+1], &want))
                {
                    fprintf(stderr, "%s: ** -c %s is not a whole number. Use 0 for "
                            "the whole file.\n", PPG_PROG_NAME, argv[i+1]);
                    return (-1);
                }
                if (0 > want)
                {
                    fprintf(stderr, "%s: ** -c %d is negative. Use 0 for the whole "
                            "file.\n", PPG_PROG_NAME, want);
                    return (-1);
                }
                /* 0 is the documented "whole file" selector; the cap is simply
                 * the largest budget the sample counter can carry. */
                sample_budget = (0 < want) ? (uint32_t)want : 0x7FFFFFFFu;
            }
            else if (opt_is(argv[i], "-i"))
            {
                /* '-' is the long-standing convention and 'stdin' is the word
                 * most people reach for, so both are accepted.  Without this,
                 * -i stdin opens a file literally named "stdin", fails, and
                 * reports a missing file -- a clear message about the wrong
                 * thing. */
                s_stdin_mode = ((0 != opt_is(argv[i+1], "-")) ||
                                (0 != opt_is(argv[i+1], "stdin"))) ? 1 : 0;
#if defined(_MSC_VER) || defined(__STDC_LIB_EXT1__)
                strncpy_s(input_path, sizeof(input_path), argv[i + 1], sizeof(input_path) - 1);
#else
                strncpy (input_path, argv[i+1], (sizeof(input_path) - 1));
#endif
            }
            else if (opt_is(argv[i], "-p"))
            {
                if (0 != opt_is(argv[i+1], "on"))
                {
                    s_plot_stream = 1;      /* already acted on, pre-banner */
                }
                else if (0 != opt_is(argv[i+1], "off"))
                {
                    s_plot_stream = 0;
                }
                else
                {
                    fprintf(stderr, "%s: ** -p %s is not on or off.\n",
                            PPG_PROG_NAME, argv[i+1]);
                    return (-1);
                }
            }
            else if (opt_is(argv[i], "-o"))
            {
                /* Refuse rather than truncate.  strncpy() silently cuts a long
                 * prefix, and the run then writes to a DIFFERENT file than the
                 * caller asked for -- which looks like the run never happened. */
                if (strlen(argv[i+1]) >= (sizeof(out_prefix) - 1))
                {
                    fprintf(stderr, "%s: ** -o prefix is longer than %u characters.\n",
                            PPG_PROG_NAME, (unsigned)(sizeof(out_prefix) - 2));
                    return (-1);
                }
#if defined(_MSC_VER) || defined(__STDC_LIB_EXT1__)
                strncpy_s(out_prefix, sizeof(out_prefix), argv[i + 1], (sizeof(out_prefix) - 1));
#else
                strncpy (out_prefix, argv[i+1], (sizeof(out_prefix) - 1));
#endif
            }
            else if (opt_is(argv[i], "-nu"))
            {
                int32_t nu_val = 1;

                /* Checked before parse_int(), which knows only numbers. */
                if (0 != opt_is(argv[i + 1], "auto"))
                {
                    s_nu_auto  = 1;
                    s_nu_scale = 0;
                }
                else
                {
                    if (0 == parse_int (argv[i + 1], &nu_val))
                    {
                        fprintf(stderr, "%s: ** -nu %s is not a whole number "
                                "or 'auto'.\n", PPG_PROG_NAME, argv[i + 1]);
                        return (-1);
                    }
                    if (0 >= nu_val)
                    {
                        fprintf(stderr, "%s: ** -nu %d is not positive; every sample would "
                                "scale to zero and no beat could be found.\n",
                                PPG_PROG_NAME, nu_val);
                        return (-1);
                    }
                    /* An explicit scale turns the decision off.  Someone who
                     * names a number means that number. */
                    s_nu_auto  = 0;
                    s_nu_scale = nu_val;
                }
            }
            else
            {
                /* Unreachable unless g_opt_with_arg[] gained an entry that no
                 * branch above decodes.  Said plainly rather than ignored: the
                 * alternative is an option that is accepted and does nothing. */
                fprintf(stderr, "%s: ** option '%s' is listed but not decoded. "
                        "Nothing was run.\n", PPG_PROG_NAME, argv[i]);
                return (-1);
            }
            i += 2;
        }
        /* The loop above consumes options in pairs, so a single token left over
         * is either a known option whose argument is missing, or an option the
         * parser does not know at all.  Ignoring it would let a typo change the
         * run in silence: `-c 200 -s` would fall back to the default patient
         * type without a word, and the patient type is the setting that most
         * affects the result. */
        if (i < argc)
        {
            if ('-' == argv[i][0])
            {
                if (0 != opt_takes_arg (argv[i]))
                {
                    fprintf(stderr, "%s: ** option '%s' is missing its argument. "
                            "Nothing was run.\n", PPG_PROG_NAME, argv[i]);
                }
                else
                {
                    fprintf(stderr, "%s: ** unknown option '%s'. Nothing was "
                            "run.\n", PPG_PROG_NAME, argv[i]);
                }
            }
            else
            {
                fprintf(stderr, "%s: ** unexpected argument '%s'. Nothing was "
                        "run.\n", PPG_PROG_NAME, argv[i]);
            }
            return (-1);
        }
    }

    /* THIS IS THE ONLY PLACE THE SUBJECT CATEGORY IS RESOLVED.  Below this
     * line the tree is generic: the analysis layer and the beat detector both
     * receive the subject's bands as data and derive everything they need from
     * them.  Reading the category from a config file or the command line later
     * changes this block and nothing else. */
    {
        const struct_subject_band *ps_band = &g_subject[subject_idx];
        enum_fiducial e_det = (0 <= detector_override)
                              ? (enum_fiducial)detector_override
                              : (enum_fiducial)ps_band->detector;

        printf("Patient type: %s (-s %s)   beat detector: %s%s\n",
               ps_band->description, ps_band->name, fiducial_name(e_det),
               (0 <= detector_override) ? " (-d override)" : " (from patient type)");
        /* A silence longer than this many rows is worth saying out loud: no
         * rhythm inside the declared band accounts for it, so it describes the
         * recording rather than an ordinary gap between pulses.  Five of the
         * subject's slowest beats. */
        s_log_fs_hz   = (uint32_t)fs_hz;
        s_quiet_limit = (5u * 60u * (uint32_t)fs_hz) / ps_band->hr_min_bpm;

        /* The band-pass corners and the smoothing span are properties of the
         * subject too -- see struct_subject_band -- so they are handed over
         * before anything designs a filter. */
        filter_configure (ps_band->bp_hp_corner_hz, ps_band->bp_lp_corner_hz,
                          ps_band->smooth_ms);
        ppg_analysis_init (&s_ppg_analysis, fs_hz, ps_band);
        fiducial_init (&s_fiducial, fs_hz, e_det,
                       ps_band->hr_min_bpm, ps_band->hr_max_bpm, &s_ppg_analysis);
        /* Closes the loop the other way, so the sanitiser can ask the detector's
         * buffered signal whether a candidate rate is real.  After both inits --
         * ppg_analysis_init() clears the context. */
        s_ppg_analysis.ps_fiducial = &s_fiducial;
    }
    if (0 != out_prefix[0])
    {
        snprintf(output_path, sizeof(output_path), "%s_ppg_analysis.csv", out_prefix);
        snprintf(rr_filename,  sizeof(rr_filename),  "%s_RR_Data.csv",   out_prefix);
    }
    else
    {
        snprintf(output_path, sizeof(output_path), "ppg_analysis.csv");
        snprintf(rr_filename,  sizeof(rr_filename),  "RR_Data.csv");
    }
    /* The version is not repeated here -- print_banner() has already stamped it
     * at the top of this log, before anything could fail. */
    printf ("Reading %s (sample limit %u)\n",
            (0 != s_stdin_mode) ? "standard input" : input_path, sample_budget);
    /* The input scale belongs in the log: a wrong -nu is the most common reason
     * a run finds no beats, and the run's own record should say what it used.
     * Under -nu auto the scale is not known yet -- resolve_nu_scale() prints the
     * decision, and the evidence for it, as soon as the first block is in. */
    if (0 != s_nu_auto)
    {
        printf ("Input scale: -nu auto (decided from the first block)\n");
    }
    else
    {
        printf ("Input scale: -nu %d (%s)\n", (int)s_nu_scale,
                (1 == s_nu_scale) ? "integer ADC counts"
                                  : "decimal input, multiplied to integer counts");
    }
    printf ("Writing results to %s\n", output_path);
    if (NULL != fp_data)
    {
        printf ("Plot stream: stdout, index,raw,smoothed,foot,peak per sample "
                "(this log is on stderr)\n");
    }


    /* stdin needs no opening, and must not be fopen()ed: under -i - the samples
     * are already arriving on it. */
    if (0 != s_stdin_mode) { fp_in = stdin; }
    else                   { OPEN_PPG_FILE (fp_in,  input_path,  "r"); }
    OPEN_PPG_FILE (fp_out, output_path, "w");
    //CSV to get RRV

    /* Open CSV file to log RR data */
    OPEN_PPG_FILE (fp_rr, rr_filename, "w");
    OPEN_PPG_FILE (s_ppg_analysis.s_intp_peak.fp_est_rr, "INTP_RR_peak.csv", "w");
    OPEN_PPG_FILE (s_ppg_analysis.s_intp_foot.fp_est_rr, "INTP_RR_foot.csv", "w");
    OPEN_PPG_FILE (s_ppg_analysis.s_intp_freq.fp_est_rr, "INTP_RR_fm.csv",  "w");

    if (NULL == fp_in)
    {
        /* Without this a mistyped input would fall through to a silent exit(0), so
         * filename looked like a successful run that produced nothing. */
        fflush (stdout);   /* keep stderr from jumping ahead of buffered stdout */
        fprintf (stderr, "FATAL: cannot read input file '%s'\n", input_path);
        CLOSE_PPG_FILE(fp_out);
        CLOSE_PPG_FILE(fp_rr);
        CLOSE_PPG_FILE(s_ppg_analysis.s_intp_peak.fp_est_rr);
        CLOSE_PPG_FILE(s_ppg_analysis.s_intp_foot.fp_est_rr);
        CLOSE_PPG_FILE(s_ppg_analysis.s_intp_freq.fp_est_rr);
        return (1);
    }

    {
        uint32_t    remaining = sample_budget;
        int32_t     got;

        for (;;)
        {
            got = read_sample_block (fp_in, ppg_data, remaining);
            if (0 >= got)
            {
                break;
            }
            remaining            -= (uint32_t)got;
            total_ppg_data_count += got;
            printf("  read %d samples (total %d)\n", got, total_ppg_data_count);
            putchar ('\n');
            process_ppg_in_samples (&s_ppg_analysis, ppg_data, got);

            if ((uint32_t)got < MAX_PPG_DATA)
            {
                break;              /* short block: input or budget exhausted */
            }
        }
    }

    log_report_unmarked_run (s_ppg_analysis.samples_processed);

    /* The input is exhausted, so nothing more can arrive to change a mark.
     * Everything still in the pipe is written now. */
    log_ppg_flush_tail (&s_ppg_analysis);
    printf ("\n  trace: %u rows written for %u samples read; "
            "%u peaks and %u feet marked\n",
            (unsigned)s_ppg_analysis.samples_processed,
            (unsigned)s_ppg_analysis.samples_fed,
            (unsigned)s_fiducial.peak_count,
            (unsigned)s_fiducial.foot_count);

    /* NOT ONE BEAT IN THE WHOLE RECORDING.
     *
     * Every number this program reports is built on detected beats, so with
     * none there is nothing behind any of them -- the trace carries a waveform
     * and no marks, and the rate columns are empty for the entire run.  That is
     * a different thing from a poor recording scoring badly, and it must not be
     * left to be inferred from a page of blank columns.
     *
     * On stderr, where a caller that discards stdout still sees it. */
    if ((0u == s_fiducial.peak_count) || (0u == s_fiducial.foot_count))
    {
        fflush (stdout);
        fprintf (stderr,
                 "\n"
                 "  ****************************************************************\n"
                 "  ** ERROR: NO BEATS DETECTED IN THIS RECORDING                 **\n"
                 "  **                                                            **\n"
                 "  ** %6u peaks and %6u feet in %8u samples.          **\n"
                 "  ** Nothing downstream of beat detection has any input, so     **\n"
                 "  ** heart rate, variability and respiratory rate are all       **\n"
                 "  ** absent -- not merely poor.  Check that the input really is **\n"
                 "  ** a PPG waveform, that -nu suits its number format, and that **\n"
                 "  ** -r matches its sampling rate.                              **\n"
                 "  ****************************************************************\n",
                 (unsigned)s_fiducial.peak_count,
                 (unsigned)s_fiducial.foot_count,
                 (unsigned)s_ppg_analysis.samples_fed);
    }

    /* stdin was never opened here, so it is not closed here either. */
    if (0 == s_stdin_mode) { CLOSE_PPG_FILE(fp_in); }
    CLOSE_PPG_FILE(fp_out);
    CLOSE_PPG_FILE(fp_rr);
    CLOSE_PPG_FILE(s_ppg_analysis.s_intp_peak.fp_est_rr);
    CLOSE_PPG_FILE(s_ppg_analysis.s_intp_foot.fp_est_rr);
    CLOSE_PPG_FILE(s_ppg_analysis.s_intp_freq.fp_est_rr);
    /* Last, and flushed: a reader at the far end of the pipe sees EOF only when
     * this closes, and takes that as the end of the recording. */
    CLOSE_PPG_FILE(fp_data);

    return 0;
}


