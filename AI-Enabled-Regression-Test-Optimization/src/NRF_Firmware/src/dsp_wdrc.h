/*
 * Copyright (c) 2026 Prajnaana Technologies Pvt. Ltd.
 * SPDX-License-Identifier: MIT
 *
 * Licensed under the MIT License. See LICENSE in the project root.
 */

/*
 * Wide dynamic range compression -- the core of a hearing aid DSP.
 *
 * The host-side regression test asserts, on the audio it gets back:
 *
 *     diff = mean(|clean - processed|)        must be > 0.001
 *     SNR  = 10*log10(Psig / Perr)            must be in (0, 50) dB
 *
 * Those hold for any non-silent input as long as the applied gain stays
 * inside [WDRC_GAIN_FLOOR, WDRC_GAIN_CEIL], both strictly below 1.0:
 *
 *     err   = clean - processed = clean * (1 - g)
 *     SNR   = -20*log10(1 - g)
 *
 * With the constants below that lands in roughly [6.9, 16.5] dB. Change them
 * and you change what the host test will accept.
 */

#ifndef DSP_WDRC_H
#define DSP_WDRC_H

#include <stdint.h>
#include <stddef.h>

/* Insertion loss applied even to quiet input. */
#define WDRC_GAIN_CEIL   0.85f

/* Hardest compression applied to loud input. */
#define WDRC_GAIN_FLOOR  0.55f

/* Linear amplitude (0..1) at which compression starts. */
#define WDRC_KNEE        0.05f

/* Compression ratio above the knee. */
#define WDRC_RATIO       3.0f

#define WDRC_ATTACK_S    0.005f
#define WDRC_RELEASE_S   0.060f

#define WDRC_SAMPLE_RATE 16000

/* Reset the envelope follower. Call on stream start. */
void wdrc_reset(void);

/*
 * Compress `count` PCM16 samples in place.
 *
 * State is carried between calls so the compressor behaves correctly when a
 * stream arrives as many small BLE writes.
 */
void wdrc_process(int16_t *samples, size_t count);

#endif /* DSP_WDRC_H */
