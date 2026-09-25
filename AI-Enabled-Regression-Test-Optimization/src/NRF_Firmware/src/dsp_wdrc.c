/*
 * Copyright (c) 2026 Prajnaana Technologies Pvt. Ltd.
 * SPDX-License-Identifier: MIT
 *
 * Licensed under the MIT License. See LICENSE in the project root.
 */

/*
 * Wide dynamic range compression. See dsp_wdrc.h for the contract the host
 * regression test depends on.
 *
 * Uses float: the nRF52840 is Cortex-M4F, with a single-precision FPU.
 * If you port this to a core without an FPU, replace
 * powf() with a small gain lookup table indexed by envelope -- that is the
 * only expensive call here.
 */

#include "dsp_wdrc.h"

#include <math.h>

/* One-pole attack/release coefficients, precomputed on first use. */
static float s_attack_coef;
static float s_release_coef;
static float s_envelope;
static int s_initialised;

static void wdrc_init(void)
{
    s_attack_coef = expf(-1.0f / (WDRC_ATTACK_S * (float)WDRC_SAMPLE_RATE));
    s_release_coef = expf(-1.0f / (WDRC_RELEASE_S * (float)WDRC_SAMPLE_RATE));
    s_envelope = 0.0f;
    s_initialised = 1;
}

void wdrc_reset(void)
{
    wdrc_init();
}

void wdrc_process(int16_t *samples, size_t count)
{
    if (!s_initialised) {
        wdrc_init();
    }

    for (size_t i = 0; i < count; i++) {

        float x = (float)samples[i] / 32767.0f;
        float mag = fabsf(x);

        /* Envelope follower: fast attack, slow release. */
        float coef = (mag > s_envelope) ? s_attack_coef : s_release_coef;
        s_envelope = coef * s_envelope + (1.0f - coef) * mag;

        /* Compression gain above the knee. */
        float gain = WDRC_GAIN_CEIL;

        if (s_envelope > WDRC_KNEE) {
            gain = WDRC_GAIN_CEIL *
                   powf(s_envelope / WDRC_KNEE, (1.0f / WDRC_RATIO) - 1.0f);
        }

        if (gain > WDRC_GAIN_CEIL) {
            gain = WDRC_GAIN_CEIL;
        } else if (gain < WDRC_GAIN_FLOOR) {
            gain = WDRC_GAIN_FLOOR;
        }

        float y = x * gain;

        if (y > 1.0f) {
            y = 1.0f;
        } else if (y < -1.0f) {
            y = -1.0f;
        }

        samples[i] = (int16_t)(y * 32767.0f);
    }
}
