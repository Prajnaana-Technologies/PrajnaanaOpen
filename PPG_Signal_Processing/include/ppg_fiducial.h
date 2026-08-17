/*
 * Copyright (c) 2026 Prajnaana Technologies Pvt. Ltd.
 *
 * SPDX-License-Identifier: Apache-2.0
 *
 * Original Author: Mamatha BV
 */
#ifndef PPG_FIDUCIAL_H
#define PPG_FIDUCIAL_H

/* ---------------------------------------------------------------------------
 * DETECTOR SELECTION -- AT RUN TIME.
 *
 * Both detectors are compiled in and linked, and one is chosen when a recording
 * starts.
 *
 * WHY IT IS A RUNTIME CHOICE.  Which detector is better DEPENDS ON THE PATIENT, and the
 * patient is already a runtime knob.  Measured against an independent reference
 * on the neonatal pair, Karlen IMS reaches sensitivity 94-96 % where Elgendi
 * TERMA reaches 88-89, and the beats TERMA misses arrive as doubled intervals:
 * its raw heart rate reads 9-11 % LOW and its SDNN and RMSSD roughly DOUBLE.
 * On adults the ranking reverses -- TERMA F1 98.7 against 96.6 -- because IMS
 * over-detects there.  A single compile-time choice cannot serve both, and
 * shipping two binaries is exactly what the patient-type knob exists to avoid.
 *
 * Each detector therefore exposes PREFIXED entry points and the dispatcher in
 * ppg_fiducial.c presents the fiducial_init() / fiducial_process_sample()
 * contract to the rest of the program.  A detector added later declares its own
 * pair here and gains a row in the dispatch table; nothing else changes.
 * ------------------------------------------------------------------------- */
typedef enum
{
    FIDUCIAL_TERMA = 0,     /* Elgendi TERMA, PLoS ONE 2013 -- best on adults  */
    FIDUCIAL_IMS,           /* Karlen IMS, EMBC 2012 -- best at neonatal rates */

    FIDUCIAL_COUNT
} enum_fiducial;


/* ***************************************************************************
 * ppg_fiducial.h -- the boundary between BEAT DETECTION and PPG ANALYSIS.
 *
 * Everything above this line is replaceable.  The analysis layer (HR, HRV, RR,
 * RRV) never sees how a beat was found: it is told, by callback, that a foot or
 * a peak occurred.  Any detector that keeps that promise can be dropped in, so
 * detector algorithms can be compared without touching the analysis code.
 *
 * The full rationale, the fiducial definitions and the acceptance criteria for
 * a replacement detector are in docs/FIDUCIAL_INTERFACE.md.
 *
 * CONTRACT
 *   - Indices are sample counts since the start of the stream.  They never
 *     reset and are strictly increasing across successive callbacks.
 *   - A foot closes the cardiac cycle that began at the previous foot; the
 *     upslope index reported with it belongs to the cycle just ENDED.
 *   - Amplitudes are on the scale of whatever waveform the detector was given.
 *     The analysis layer uses their variation over time, never their absolute
 *     magnitude, so a constant offset or gain is acceptable.
 *   - A callback, once made, is final.  Beats are not revised or retracted.
 * *************************************************************************** */

#include <stdint.h>
#include "ppg_common.h"

/* ---------------------------------------------------------------------------
 * THE DICROTIC WAVE, AND WHAT THIS INTERFACE DOES NOT MODEL.  READ THIS FIRST.
 *
 * One cardiac cycle of a peripheral pulse contains TWO rises, not one:
 *
 *      foot -> SYSTOLIC PEAK -> dicrotic notch -> DICROTIC PEAK -> next foot
 *
 * The systolic peak is ejection.  The dicrotic notch is the incisura at aortic
 * valve closure, and the dicrotic peak after it is the reflected wave returning
 * from the periphery.  Both are NORMAL features of every peripheral PPG.  They
 * arrive from the ADC in every recording, they cannot be filtered away without
 * removing the pulse with them, and they are MOST pronounced in young compliant
 * vessels -- which is to say in the neonatal population this program is aimed
 * at.  A signal without them is the exception, not the rule.
 *
 * A beat, for this interface, is ONE systolic peak.  Neither detector models
 * the dicrotic wave as a named fiducial: both search for pulses, and where the
 * dicrotic peak is large enough to look like one it is emitted as a beat and
 * the reported rate DOUBLES.  Measured on 5 of the 68 short recordings.
 *
 * Each detector does carry its author's own defence against this -- [ELGENDI]'s
 * squaring and THR2, [KARLEN]'s adaptive amplitude thresholds -- and both are
 * implemented.  They are not sufficient at this operating point, which lies
 * outside the cohorts either paper validated.  See docs/DESIGN.md.
 *
 * Doubling is the worst shape a rate error can take here, because doubling a
 * bradycardic neonate at 75 /min gives 150 /min -- inside the expected band, so
 * no re-anchor forms, no warning prints, and the result looks healthy.
 *
 * It CANNOT be settled from the interval stream: a doubled train is
 * arithmetically identical to a heart beating twice as fast, so any rule
 * written on intervals alone must accept both or reject both.  Nor can a rule
 * that scores one candidate rise on its own -- by amplitude, by slope or by
 * duration -- separate them.  The evidence for both statements, and what a
 * complete correction needs, are in docs/DESIGN.md, "Dicrotic doubling".
 * It is to be addressed in an upcoming release.
 * ------------------------------------------------------------------------- */

/* ---------------------------------------------------------------------------
 * Detector state.
 *
 * Opaque to the analysis layer by convention: nothing outside ppg_fiducial.c
 * may read or write these fields.  Deliberately minimal -- the analysis layer
 * must not be able to reach into a detector's internals, so everything private
 * to a detector lives in that detector's own translation unit as file-scope
 * state.  What remains here is only what the HARNESS needs: the configured
 * rate, the opaque callback context, and a short ring of samples that
 * ppg_main.c dumps for diagnostics.
 *
 * A detector's own state machine -- amplitude limits, notch windows, inflection
 * bookkeeping, adaptive thresholds -- does NOT belong here.  It lives in that
 * detector's translation unit, so replacing a detector touches one file.
 * ------------------------------------------------------------------------- */
typedef struct  tag_fiducial
{
    uint32_t        sel_fs_hz;
    void           *user;               /* opaque analysis context            */
    uint32_t        ring_wr_pos;           /* samples written to s_data_buf      */
    struct_data_buf s_data_buf [PPG_RING_LEN];

} struct_fiducial;

/* --------------------------------------------------------------------------
 * The detector's own entry points.
 * -------------------------------------------------------------------------- */

/**
 * @brief Initialise a detector for a new recording.
 *
 * THE EXPECTED HEART-RATE BAND IS PART OF THE CONTRACT, not a convenience.
 * Every beat detector carries at least one parameter whose correct value is a
 * fraction of a cardiac cycle -- a moving-average length, a segment duration, a
 * refractory period -- and such a parameter is WRONG whenever it is expressed
 * as a constant number of milliseconds and the subject's rate is not the rate the
 * parameter was derived at.  Published values are measured on a specific
 * cohort; carrying the number rather than the definition is what breaks a
 * detector at a rate its authors never saw.
 *
 * Passing the band here rather than reaching into shared configuration keeps
 * each detector self-contained: it derives its own parameters from the band and
 * exposes none of them.  A new detector added later implements this same
 * signature and derives whatever it needs, with nothing about it appearing in a
 * generic header.
 *
 * @param ps_fd         Detector context
 * @param fs_hz Sample rate of the PPG stream, Hz
 * @param hr_min_bpm    Lowest expected heart rate for this subject
 * @param hr_max_bpm    Highest expected heart rate for this subject
 * @param user          Opaque pointer passed to every callback below
 */
void    fiducial_init (struct_fiducial *ps_fd,
                       int32_t        fs_hz,
                       enum_fiducial  e_detector,
                       uint32_t       hr_min_bpm,
                       uint32_t       hr_max_bpm,
                       void          *user);

/**
 * @brief Human-readable name of a detector, for messages.
 */
const char *fiducial_name (enum_fiducial e_detector);

/**
 * @brief One-line description of a detector, for the usage text.
 */
const char *fiducial_describe (enum_fiducial e_detector);

/* --------------------------------------------------------------------------
 * Each detector's own entry points.  These are what a detector implements; the
 * dispatcher above is what everything else calls.  A detector must define both
 * and nothing else with external linkage -- all of its state and helpers stay
 * file-scope, prefixed, so two detectors can be linked together.
 * -------------------------------------------------------------------------- */
void    terma_fiducial_init (struct_fiducial *ps_fd, int32_t fs_hz,
                             uint32_t hr_min_bpm, uint32_t hr_max_bpm, void *user);
void    terma_fiducial_process_sample (struct_fiducial *ps_fd, int32_t sample_value);

void    ims_fiducial_init (struct_fiducial *ps_fd, int32_t fs_hz,
                           uint32_t hr_min_bpm, uint32_t hr_max_bpm, void *user);
void    ims_fiducial_process_sample (struct_fiducial *ps_fd, int32_t sample_value);

/**
 * @brief Feed one raw sample.  Callbacks fire as beats are recognised.
 */
void    fiducial_process_sample (struct_fiducial *ps_fd, int32_t sample_value);

/**
 * @brief Is @p period_ms the signal's fundamental, or half of it?
 *
 * Answers the one question the beat intervals cannot: a detector reporting two
 * beats per cycle emits a train arithmetically identical to a heart beating
 * twice as fast.  Compares band power at 1/period against 1/(2*period) over the
 * buffered filtered signal.  See docs/DESIGN.md, "The category band is a
 * prior, not a gate".
 *
 * @param ps_fd     Detector context (holds the sample buffer)
 * @param period_ms Candidate beat period, milliseconds
 * @return 1 only if CONFIRMED as the fundamental; 0 if half that rate carries
 *         more power, and also 0 while the buffer is too short to tell -- the
 *         caller must wait and ask again rather than act on an unexamined case
 */
uint32_t fiducial_period_is_fundamental (const struct_fiducial *ps_fd,
                                         uint32_t period_ms);

/* --------------------------------------------------------------------------
 * Callbacks INTO the analysis layer.  Implemented in ppg_analysis.c.
 * A replacement detector must call exactly these.
 * -------------------------------------------------------------------------- */

/**
 * @brief A pulse onset (foot) was detected.
 *
 * Feeds the BW respiratory surrogate, and closes the previous cardiac cycle so
 * the FM surrogate can take its maximal-upslope interval.
 *
 * @param user          Opaque pointer supplied to fiducial_init()
 * @param foot_index    Sample index of the onset
 * @param foot_value    Waveform value there
 * @param upslope_index Maximal-upslope instant of the cycle that just ENDED
 * @param upslope_valid 0 if no upslope was established for that cycle
 */
void    ppg_on_foot (void *user, uint32_t foot_index, int32_t foot_value,
                     uint32_t upslope_index, uint32_t upslope_valid);

/**
 * @brief A PPG signal peak was detected.
 *
 * Feeds the AM respiratory surrogate and advances the heart-rate estimate.
 *
 * @param user       Opaque pointer supplied to fiducial_init()
 * @param peak_index Sample index of the PPG signal peak
 * @param peak_value Waveform value there
 */
void    ppg_on_peak (void *user, uint32_t peak_index, int32_t peak_value);

#endif /* PPG_FIDUCIAL_H */
