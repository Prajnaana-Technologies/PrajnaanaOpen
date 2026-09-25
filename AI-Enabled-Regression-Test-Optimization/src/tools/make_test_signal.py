# Copyright (c) 2026 Prajnaana Technologies Pvt. Ltd.
# Original author: Dhanya Shree S
# SPDX-License-Identifier: MIT
#
# Licensed under the MIT License. See LICENSE in the project root.

"""Generate the test audio used by the regression suite.

    python tools/make_test_signal.py
    python tools/make_test_signal.py --out /tmp/other.wav
    python tools/make_test_signal.py --check

Writes regression/audio/test_signal.wav unless --out says otherwise.

Why this exists: a recorded clip from a public set such as ESC-50 is licensed
CC BY-NC 3.0. Non-commercial terms are incompatible with releasing this project
under a permissive licence, and a few hundred KB of binary in git is worth
avoiding anyway. This synthesises an equivalent stimulus instead, so the repo
owns everything it ships.

The signal is built to exercise a hearing aid DSP:

- three harmonics in the speech band, so the single-band WDRC compressor is
  driven by a spectrum rather than a pure tone
- a syllabic amplitude envelope (~4 Hz), so the compressor's attack and
  release actually engage rather than sitting at a fixed gain
- a low noise floor, so the SNR maths in test_audio_dsp is well conditioned

It is fully deterministic: same bytes on every machine, every run.
"""

import argparse
import hashlib
import os
import wave

import numpy as np

SAMPLE_RATE = 16000
DURATION_S = 2.0
SEED = 1337

OUTPUT = os.path.join("regression", "audio", "test_signal.wav")

# Speech-band harmonics (Hz) and their relative levels.
HARMONICS = ((220.0, 1.00), (660.0, 0.45), (1320.0, 0.20))

SYLLABIC_HZ = 4.0     # amplitude modulation rate, roughly syllable speed
NOISE_LEVEL = 0.01    # low but non-zero, keeps SNR maths well conditioned
PEAK = 0.6            # headroom so nothing clips before the DSP sees it


def make_signal(sample_rate=SAMPLE_RATE, duration_s=DURATION_S, seed=SEED):
    """Return float32 samples in [-1, 1]."""
    n = int(sample_rate * duration_s)
    t = np.arange(n, dtype=np.float64) / sample_rate

    tone = np.zeros(n, dtype=np.float64)
    for freq, level in HARMONICS:
        tone += level * np.sin(2 * np.pi * freq * t)

    # Syllabic envelope: never fully silent, so the compressor always has
    # something to act on and mean(|clean - processed|) stays well above the
    # 0.001 floor that test_audio_dsp asserts against.
    envelope = 0.35 + 0.65 * (0.5 * (1 - np.cos(2 * np.pi * SYLLABIC_HZ * t)))

    rng = np.random.default_rng(seed)
    noise = NOISE_LEVEL * rng.standard_normal(n)

    signal = tone * envelope + noise
    signal = PEAK * signal / np.max(np.abs(signal))

    return signal.astype(np.float32)


def write_wav(path=OUTPUT, sample_rate=SAMPLE_RATE):
    samples = make_signal(sample_rate=sample_rate)
    pcm = np.clip(samples * 32767.0, -32768, 32767).astype(np.int16)

    directory = os.path.dirname(path)
    if directory:
        os.makedirs(directory, exist_ok=True)

    with wave.open(path, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(pcm.tobytes())

    return path, samples


def main(argv=None):
    # An argument parser, so that --help prints help.
    parser = argparse.ArgumentParser(
        description="Write the deterministic WAV the audio tests stream")
    parser.add_argument("--out", default=OUTPUT,
                        help="where to write the WAV (default: {})".format(OUTPUT))
    parser.add_argument("--check", action="store_true",
                        help="report what would be written, write nothing")

    args = parser.parse_args(argv)

    samples = make_signal()

    if args.check:
        pcm = np.clip(samples * 32767.0, -32768, 32767).astype(np.int16)

        print("would write {} ({} samples @ {} Hz, {:.1f}s)".format(
            args.out, len(samples), SAMPLE_RATE, len(samples) / SAMPLE_RATE
        ))
        print("sha256 of the PCM = {}".format(
            hashlib.sha256(pcm.tobytes()).hexdigest()
        ))
    else:
        path, samples = write_wav(args.out)

        print("wrote {} ({} samples @ {} Hz, {:.1f}s)".format(
            path, len(samples), SAMPLE_RATE, len(samples) / SAMPLE_RATE
        ))

    print("mean|x| = {:.4f}   peak = {:.4f}".format(
        float(np.mean(np.abs(samples))), float(np.max(np.abs(samples)))
    ))

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
