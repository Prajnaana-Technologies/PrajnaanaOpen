/*
 * Copyright (c) 2026 Prajnaana Technologies Pvt. Ltd.
 *
 * SPDX-License-Identifier: Apache-2.0
 *
 * Original Author: Mamatha BV
 */
/* ***************************************************************************
 * ppg_fiducial.c -- BEAT-DETECTOR DISPATCH.
 *
 * Both detectors are linked; this file chooses between them at run time and
 * presents the single fiducial_init() / fiducial_process_sample() contract that
 * the rest of the program calls.  Nothing outside this file knows which
 * detector is running.
 *
 * WHY THE CHOICE IS RUNTIME AND NOT A BUILD FLAG.  Which detector is better
 * depends on the patient, and the patient type is already selected at run time.
 * Measured against an independent reference on the neonatal pair:
 *
 *                      TERMA           IMS
 *      sensitivity     88-89 %         94-96 %
 *      raw heart rate  9-11 % LOW      within 1.3 %
 *      raw SDNN        ~2x reference   within 6 %
 *      raw RMSSD       ~2x reference   within 9 %
 *
 * The beats TERMA misses at neonatal rates arrive as DOUBLED intervals, which
 * is why its rate reads low and its variability measures inflate.  On adults
 * the ranking reverses (TERMA F1 98.7 against 96.6) because IMS over-detects
 * there.  One compile-time choice cannot serve both, and shipping two binaries
 * is exactly what the patient-type knob exists to avoid.
 *
 * The cost is that both detectors' state is always resident.  TERMA's history
 * buffers dominate it at about 13 KB; IMS's state is a few hundred bytes.  That
 * is the price of one binary, and it is paid once at link time rather than per
 * recording.
 * *************************************************************************** */

#include <stddef.h>
#include <stdio.h>
#include <math.h>

#include "ppg_common.h"
#include "ppg_fiducial.h"

/* --------------------------------------------------------------------------
 * The dispatch table.  One row per detector, indexed by enum_fiducial.
 *
 * A detector added later declares its prefixed pair in ppg_fiducial.h and gains
 * a row here.  Nothing else in the tree changes -- which is the whole point of
 * the contract.
 * -------------------------------------------------------------------------- */
typedef struct
{
    const char *name;
    const char *desc;       /* shown in the usage text, so help cannot go stale */
    void      (*init) (struct_fiducial *ps_fd, int32_t fs_hz,
                       uint32_t hr_min_bpm, uint32_t hr_max_bpm, void *user);
    void      (*process) (struct_fiducial *ps_fd, int32_t sample_value);

} struct_fiducial_entry;

static const struct_fiducial_entry g_fiducial [FIDUCIAL_COUNT] = {
    { "terma",
      "Elgendi 2013 two event-related moving averages (ppg_fiducial_elgendi_terma.c)",
      terma_fiducial_init, terma_fiducial_process_sample },

    { "ims",
      "Karlen 2012 incremental-merge segmentation (ppg_fiducial_karlen_ims.c)",
      ims_fiducial_init,   ims_fiducial_process_sample   },
};

/* Which one is live for the current recording.  Held as a pointer rather than
 * an index so the per-sample path is one indirect call and no bounds test --
 * fiducial_process_sample() runs at the full sampling rate. */
static const struct_fiducial_entry *gps_active = &g_fiducial[FIDUCIAL_TERMA];

/**
 * @brief Human-readable name of a detector, for messages.
 *
 * @param e_detector  Which detector
 * @return            Its short name, or "?" if the value is out of range
 */
const char *fiducial_name (enum_fiducial e_detector)
{
    if (((int32_t)e_detector < 0) || (FIDUCIAL_COUNT <= e_detector))
    {
        return ("?");
    }
    return (g_fiducial[e_detector].name);
}

/**
 * @brief One-line description of a detector, for the usage text.
 *
 * The description lives in the dispatch table beside the function pointers, so
 * a detector added later cannot be listed in the help with a stale description
 * or omitted from it -- the help is generated from the same rows that do the
 * dispatching.
 *
 * @param e_detector  Which detector
 * @return            Its description, or "" if the value is out of range
 */
const char *fiducial_describe (enum_fiducial e_detector)
{
    if (((int32_t)e_detector < 0) || (FIDUCIAL_COUNT <= e_detector))
    {
        return ("");
    }
    return (g_fiducial[e_detector].desc);
}

/**
 * @brief Select a detector and initialise it for a new recording.
 *
 * The selection is made HERE and nowhere else; from this point the analysis
 * layer only sees callbacks.  An out-of-range selection falls back to TERMA
 * and says so, rather than dereferencing past the table -- this runs before
 * any sample has been seen, so a silent fallback would be indistinguishable
 * from a correct run for the whole recording.
 *
 * @param ps_fd          Detector context
 * @param fs_hz Sample rate of the PPG stream, Hz
 * @param e_detector     Which detector to run
 * @param hr_min_bpm     Lowest expected heart rate for this subject
 * @param hr_max_bpm     Highest expected heart rate for this subject
 * @param user           Opaque pointer passed to every callback
 */
void    fiducial_init (struct_fiducial *ps_fd,
                       int32_t        fs_hz,
                       enum_fiducial  e_detector,
                       uint32_t       hr_min_bpm,
                       uint32_t       hr_max_bpm,
                       void          *user)
{
    if (((int32_t)e_detector < 0) || (FIDUCIAL_COUNT <= e_detector))
    {
        printf("fiducial_init(): ** detector %d is not one of the %d linked in "
               "-- falling back to %s **\n",
               (int)e_detector, (int)FIDUCIAL_COUNT, g_fiducial[FIDUCIAL_TERMA].name);
        e_detector = FIDUCIAL_TERMA;
    }
    gps_active = &g_fiducial[e_detector];
    gps_active->init (ps_fd, fs_hz, hr_min_bpm, hr_max_bpm, user);

    return;
}

/**
 * @brief Feed one raw sample to the selected detector.
 *
 * @param ps_fd     Detector context
 * @param sample_value  Raw sample
 */
void    fiducial_process_sample (struct_fiducial *ps_fd, int32_t sample_value)
{
    gps_active->process (ps_fd, sample_value);
    return;
}

/**
 * @brief Band power at one frequency, by the Goertzel recurrence.
 *
 * [GOERTZEL] Goertzel G, "An Algorithm for the Evaluation of Finite
 * Trigonometric Series", The American Mathematical Monthly 65(1):34-35, 1958,
 * doi:10.2307/2310304.  Paywalled; no local copy.
 * Indexed with all other sources in ../CREDITS.md
 *
 * Two frequencies are wanted, not a spectrum, so this costs O(N) each and needs
 * no FFT, no twiddle table and no scratch buffer.
 *
 * @param ps_buf  Sample window (mean already removed by the caller)
 * @param n       Window length
 * @param hz      Frequency of interest
 * @param fs_hz Sample rate of the PPG stream, Hz
 * @return Squared magnitude at @p hz
 */

static double   goertzel_power (const double *ps_buf, uint32_t n,
                                double hz, double fs_hz)
{
    double  coeff = 2.0 * cos ((2.0 * PI * hz) / fs_hz);
    double  s1 = 0.0, s2 = 0.0, s0;
    uint32_t i;

    for (i = 0u; i < n; i++)
    {
        s0 = ps_buf[i] + (coeff * s1) - s2;
        s2 = s1;
        s1 = s0;
    }
    return ((s1 * s1) + (s2 * s2) - (coeff * s1 * s2));
}

/**
 * @brief Is period_ms CONFIRMED as the signal's fundamental?  See the header.
 *
 * Confirmation is required, not assumed: every path that cannot decide returns
 * 0, so a caller can never act on evidence this function did not actually
 * examine.  The caller's correct response to 0 is to wait and ask again.
 *
 * @param ps_fd     Detector context
 * @param period_ms Candidate beat period, milliseconds
 * @return 1 = confirmed fundamental, 0 = doubled train OR not yet decidable
 */
uint32_t fiducial_period_is_fundamental (const struct_fiducial *ps_fd,
                                         uint32_t period_ms)
{
    double      window [PPG_RING_LEN];
    double      fs_hz, cand_hz, mean = 0.0;
    uint32_t    i, n = PPG_RING_LEN;

    if ((NULL == ps_fd) || (0u == period_ms) || (0u == ps_fd->sel_fs_hz))
    {
        return (0u);
    }
    /* The ring is not full yet, so most of it is still the zeros it was
     * initialised with.  Deciding on that would be deciding on nothing. */
    if (ps_fd->ring_wr_pos < n)
    {
        return (0u);
    }
    fs_hz   = (double)ps_fd->sel_fs_hz;
    cand_hz = 1000.0 / (double)period_ms;

    /* At least two whole cycles of HALF the candidate must fit, or the doubled
     * case being tested for is not resolvable in this window. */
    if (((double)n / fs_hz) < (4.0 * ((double)period_ms / 1000.0)))
    {
        return (0u);
    }
    for (i = 0u; i < n; i++)
    {
        /* The smoothed sample: this check runs on the same stream the detector
         * decides on, not on the band-pass output before the moving average. */
        window[i] = (double)ps_fd->s_data_buf[i].smoothed_sample;
        mean     += window[i];
    }
    mean /= (double)n;
    for (i = 0u; i < n; i++) { window[i] -= mean; }

    /* A doubled train puts the fundamental at half the candidate, so that is
     * where the energy would be.  Sweeping the whole band instead was measured
     * and is WORSE -- it defers genuine cases long enough to spoil them (two
     * records fell 197->140 and 192->150 bpm) and solves nothing. */
    return ((goertzel_power (window, n, cand_hz,       fs_hz) >=
             goertzel_power (window, n, cand_hz / 2.0, fs_hz)) ? 1u : 0u);
}
