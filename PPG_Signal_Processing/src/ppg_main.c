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
#include <errno.h>
#include <math.h>

#include "ppg_common.h"
#include "ppg_fiducial.h"

#define MAX_PPG_DATA                 (1024u)
#define PPG_DEFAULT_SAMPLE_LIMIT    (2048u)
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
      FIDUCIAL_IMS },

    { "child",   CHILD_NAME,
      CHILD_RR_BAND_MIN_BPM,   CHILD_RR_BAND_MAX_BPM,
      CHILD_HR_MIN_BPM,        CHILD_HR_MAX_BPM,
      CHILD_WINDOW_PTS,        CHILD_WELCH_SEG,        RR_WINDOW_SLIDE_PTS,
      FIDUCIAL_TERMA },

    { "adult",   ADULT_NAME,
      ADULT_RR_BAND_MIN_BPM,   ADULT_RR_BAND_MAX_BPM,
      ADULT_HR_MIN_BPM,        ADULT_HR_MAX_BPM,
      ADULT_WINDOW_PTS,        ADULT_WELCH_SEG,        RR_WINDOW_SLIDE_PTS,
      FIDUCIAL_TERMA },
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


/* The detector owns beat finding; the analysis layer owns HR/HRV/RR/RRV.  They
 * meet only at the callbacks in ppg_fiducial.h. */
static  struct_fiducial   s_fiducial;

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
    "-c", "-d", "-i", "-nu", "-o", "-r", "-s"
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
 * On the first block it writes the CSV headers.  There is no priming pass:
 * every sample, from the very first, is fed to the detector and then written as
 * its own trace row.  The interpolation columns simply read zero until the first
 * beats arrive, which is what actually happened.
 */
static  void    process_ppg_in_samples (struct_ppg_analysis *ps_ppg,
                                int32_t        *pi_ppg_data,
                                int32_t         ppg_data_count)
{
    int32_t     k;
    int32_t     n_done = ps_ppg->samples_processed;
    int32_t     i = 0;

    if (0 == n_done)
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
        LOG_PPG_FILE("Index,InputSample,Chebyshev,Smoothed,interp_foot,interp_peak,interp_fm,AM-signal,"
                     "Version=" PPG_ANALYSIS_VERSION "\n");
        /* There is no priming loop.  One that fed the first
         * PPG_RING_LEN-1 samples WITHOUT advancing n_done and without
         * logging, which cost 1023 trace rows out of every recording (58978
         * rows for 60001 samples).  It was unnecessary: the trace reads slot
         * n_done % PPG_RING_LEN immediately after the detector has
         * written that same slot for that same sample, so logging from the
         * first sample is correct.  The interpolation columns are simply zero
         * until the first beats arrive, which is the truth.
         *
         * (Alignment was never the issue -- every emitted row was correctly
         * labelled; the trace was only short.) */
    }
    for (; i < ppg_data_count; i++, n_done++)
    {
        fiducial_process_sample (&s_fiducial, pi_ppg_data[i]);
        k = n_done % PPG_RING_LEN;
        LOG_PPG_FILE(" %d, %d, %d, %d, %d, %d, %d, %d\n", n_done,
            s_fiducial.s_data_buf[k].input_sample,    s_fiducial.s_data_buf[k].filtered_sample,
            s_fiducial.s_data_buf[k].smoothed_sample,
            ps_ppg->intp_trace[INTERPOLATE_BW][k],
            ps_ppg->intp_trace[INTERPOLATE_AM][k],
            ps_ppg->intp_trace[INTERPOLATE_FM][k],
            (ps_ppg->intp_trace[INTERPOLATE_AM][k] - ps_ppg->intp_trace[INTERPOLATE_BW][k]));
    }
    ps_ppg->samples_processed = n_done;
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

/**
 * @brief Fill one block from the input file.
 *
 * The read is factored out so the caller works in whole BLOCKS rather than in
 * single samples.  That removes the duplicated flush the sample-at-a-time form
 * needed: a short block is simply the last block, handled by the same path as
 * every other one.
 *
 * @param fp        Open input file
 * @param ps_dst    Destination block, at least MAX_PPG_DATA entries
 * @param budget    Samples still permitted by -c
 * @param scale     -nu multiplier applied to each value
 * @return Samples placed in @p ps_dst; short means end of input or budget
 */
static  int32_t read_sample_block (FILE *fp, int32_t *ps_dst,
                                   uint32_t budget, int32_t scale)
{
    uint32_t cap = (budget < MAX_PPG_DATA) ? budget : MAX_PPG_DATA;
    uint32_t n   = 0u;
    double   v   = 0.0;

    while (n < cap)
    {
#if defined(_MSC_VER) || defined(__STDC_LIB_EXT1__)
        if (1 != fscanf_s (fp, "%lf", &v))
#else
        if (1 != fscanf (fp, "%lf", &v))
#endif
        {
            break;
        }
        {
            /* A value the cast cannot represent is REFUSED, not truncated.
             * Casting a non-finite or out-of-range double to int32_t is
             * undefined behaviour: "nan", "inf" and 1e300 are all accepted by
             * "%lf", and a truncating cast turns them into a plausible-looking
             * sample that the whole analysis then runs on.  Reading stops at
             * the offending sample and says so. */
            double scaled = (double)scale * v;

            if ((0 == isfinite (scaled)) ||
                (scaled < (double)INT32_MIN) || (scaled > (double)INT32_MAX))
            {
                fflush (stdout);
                fprintf (stderr, "%s: ** sample %u is not a representable number "
                         "(%g). Nothing further was read.\n",
                         PPG_PROG_NAME, (unsigned)n, v);
                break;
            }
            ps_dst[n] = (int32_t)scaled;
        }
        n++;
    }
    return ((int32_t)n);
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
    /* Input scaling: 1 for integer ADC counts (the default), 10000 for
     * floating-point recordings carrying about 5 decimal digits.
     * Getting this wrong truncates every sample to zero -- see
     * docs/USER_GUIDE.md, "-nu -- get this one right". */
    int32_t     nu_val = 1;
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
    }

    print_banner ();
    putchar ('\n');

    if ((2 > argc) || (0 != want_help))
    {
        printf ("\n%s -- options\n", PPG_PROG_NAME);
        printf ("Usage: %s [options]\n\n", PPG_PROG_NAME);
        printf ("  -i   <input_filename>       recording to analyse\n");
        printf ("  -nu  <scale>                1 = integer ADC counts (default),\n");
        printf ("                              10000 = floating point, 5 decimal digits\n");
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
#if defined(_MSC_VER) || defined(__STDC_LIB_EXT1__)
                strncpy_s(input_path, sizeof(input_path), argv[i + 1], sizeof(input_path) - 1);
#else
                strncpy (input_path, argv[i+1], (sizeof(input_path) - 1));
#endif
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
                strncpy (out_prefix, argv[i+1], (sizeof(out_prefix) - 1));
            }
            else if (opt_is(argv[i], "-nu"))
            {
                if (0 == parse_int (argv[i + 1], &nu_val))
                {
                    fprintf(stderr, "%s: ** -nu %s is not a whole number.\n",
                            PPG_PROG_NAME, argv[i + 1]);
                    return (-1);
                }
                if (0 >= nu_val)
                {
                    fprintf(stderr, "%s: ** -nu %d is not positive; every sample would "
                            "scale to zero and no beat could be found.\n",
                            PPG_PROG_NAME, nu_val);
                    return (-1);
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
    printf ("Reading %s (sample limit %u)\n", input_path, sample_budget);
    /* The input scale belongs in the log: a wrong -nu is the most common reason
     * a run finds no beats, and the run's own record should say what it used. */
    printf ("Input scale: -nu %d (%s)\n", nu_val,
            (1 == nu_val) ? "integer ADC counts"
                          : "decimal input, multiplied to integer counts");
    printf ("Writing results to %s\n", output_path);


    OPEN_PPG_FILE (fp_in,  input_path,  "r");
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
            got = read_sample_block (fp_in, ppg_data, remaining, nu_val);
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

    CLOSE_PPG_FILE(fp_in);
    CLOSE_PPG_FILE(fp_out);
    CLOSE_PPG_FILE(fp_rr);
    CLOSE_PPG_FILE(s_ppg_analysis.s_intp_peak.fp_est_rr);
    CLOSE_PPG_FILE(s_ppg_analysis.s_intp_foot.fp_est_rr);
    CLOSE_PPG_FILE(s_ppg_analysis.s_intp_freq.fp_est_rr);

    return 0;
}


