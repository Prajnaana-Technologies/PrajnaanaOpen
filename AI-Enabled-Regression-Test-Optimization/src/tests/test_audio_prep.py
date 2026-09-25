# Copyright (c) 2026 Prajnaana Technologies Pvt. Ltd.
# Original author: Dhanya Shree S
# SPDX-License-Identifier: MIT
#
# Licensed under the MIT License. See LICENSE in the project root.

"""Tests for WAV loading and conversion.

The device only handles 16-bit mono at 16 kHz. Everything else has to be
converted on the host, and getting that wrong is quiet and nasty: interleaved
stereo read as mono sounds distorted, and a wrong sample rate silently changes
the compressor's attack and release times.

No hardware needed -- files are built here.
"""

import os
import struct
import tempfile
import wave

import numpy as np
import pytest

from regression.audio_prep import (
    TARGET_RATE,
    AudioError,
    describe,
    load_wav,
    resample,
    to_mono,
)


def tone(n, freq=440.0, rate=44100, amplitude=0.5):
    t = np.arange(n, dtype=np.float64) / rate
    return amplitude * np.sin(2 * np.pi * freq * t)


def write_pcm(path, samples, rate, channels, bits):
    """Write an integer PCM WAV at the given bit depth."""
    if channels > 1:
        samples = np.repeat(samples, channels)

    with wave.open(path, "wb") as handle:
        handle.setnchannels(channels)
        handle.setsampwidth(bits // 8)
        handle.setframerate(rate)

        if bits == 8:
            raw = ((samples * 127) + 128).clip(0, 255).astype(np.uint8).tobytes()
        elif bits == 16:
            raw = (samples * 32767).astype("<i2").tobytes()
        elif bits == 24:
            wide = (samples * (2 ** 23 - 1)).astype("<i4")
            raw = wide.view(np.uint8).reshape(-1, 4)[:, :3].tobytes()
        elif bits == 32:
            raw = (samples * (2 ** 31 - 1)).astype("<i4").tobytes()
        else:
            raise ValueError(bits)

        handle.writeframes(raw)

    return path


def write_float(path, samples, rate, channels=1, bits=32):
    """Write an IEEE float WAV by hand -- the wave module cannot."""
    if channels > 1:
        samples = np.repeat(samples, channels)

    dtype = "<f4" if bits == 32 else "<f8"
    data = samples.astype(dtype).tobytes()

    block_align = channels * bits // 8

    fmt = struct.pack("<HHIIHH", 3, channels, rate,
                      rate * block_align, block_align, bits)

    with open(path, "wb") as handle:
        handle.write(b"RIFF")
        handle.write(struct.pack("<I", 4 + 8 + len(fmt) + 8 + len(data)))
        handle.write(b"WAVE")
        handle.write(b"fmt " + struct.pack("<I", len(fmt)) + fmt)
        handle.write(b"data" + struct.pack("<I", len(data)) + data)

    return path


@pytest.fixture
def tmpwav(tmp_path):
    def make(name):
        return str(tmp_path / name)
    return make


# --------------------------------------------------------------------------
# Bit depths
# --------------------------------------------------------------------------

@pytest.mark.parametrize("bits", [8, 16, 24, 32])
def test_integer_bit_depths_load(tmpwav, bits):
    path = write_pcm(tmpwav("d{}.wav".format(bits)),
                     tone(4410), rate=44100, channels=1, bits=bits)

    audio = load_wav(path)

    assert len(audio) == 1600, "0.1s at 44.1k should become 1600 at 16k"
    assert audio.dtype == np.float32
    assert 0.2 < np.max(np.abs(audio)) <= 1.0


@pytest.mark.parametrize("bits", [32, 64])
def test_float_wavs_load(tmpwav, bits):
    """The wave module cannot open these at all."""
    path = write_float(tmpwav("f{}.wav".format(bits)),
                       tone(4410), rate=44100, bits=bits)

    audio = load_wav(path)

    assert len(audio) == 1600
    assert 0.2 < np.max(np.abs(audio)) <= 1.0


def test_float_above_unity_is_normalised(tmpwav):
    """Float WAVs are not bounded to [-1, 1]; clipping would distort."""
    path = write_float(tmpwav("hot.wav"), tone(4410, amplitude=2.5), rate=44100)

    audio = load_wav(path)

    assert np.max(np.abs(audio)) <= 1.0 + 1e-6


def test_8bit_is_treated_as_unsigned(tmpwav):
    """8-bit WAV is centred on 128, not 0. Getting this wrong adds huge DC."""
    path = write_pcm(tmpwav("u8.wav"), tone(4410), 44100, 1, 8)

    audio = load_wav(path)

    assert abs(float(np.mean(audio))) < 0.05, "large DC offset means unsigned/signed mixup"


# --------------------------------------------------------------------------
# Channels and rate
# --------------------------------------------------------------------------

def test_stereo_is_mixed_not_interleaved(tmpwav):
    """Stereo frames are mixed down to mono, not read as interleaved."""
    path = write_pcm(tmpwav("st.wav"), tone(4410), 44100, 2, 16)

    audio = load_wav(path)

    assert len(audio) == 1600, "stereo frames must halve, not double"


def test_mono_channel_mixdown():
    interleaved = np.array([1.0, -1.0, 1.0, -1.0], dtype=np.float32)

    assert np.allclose(to_mono(interleaved, 2), [0.0, 0.0])


@pytest.mark.parametrize("rate", [8000, 16000, 22050, 44100, 48000])
def test_any_sample_rate_becomes_16k(tmpwav, rate):
    path = write_pcm(tmpwav("r{}.wav".format(rate)),
                     tone(rate, rate=rate), rate, 1, 16)

    audio = load_wav(path)

    assert abs(len(audio) - TARGET_RATE) <= 2, "one second in, one second out"


def test_resample_preserves_duration():
    audio = np.zeros(44100, dtype=np.float32)

    assert abs(len(resample(audio, 44100, 16000)) - 16000) <= 1


def test_16k_mono_is_left_alone():
    from regression.audio_prep import resample as rs

    audio = np.linspace(-1, 1, 100).astype(np.float32)

    assert rs(audio, 16000, 16000) is audio


# --------------------------------------------------------------------------
# describe() drives the dashboard's message
# --------------------------------------------------------------------------

def test_describe_lists_needed_changes(tmpwav):
    path = write_pcm(tmpwav("desc.wav"), tone(44100), 44100, 2, 24)

    info = describe(path)

    assert info["rate"] == 44100
    assert info["channels"] == 2
    assert info["bits"] == 24
    assert "mixed to mono" in info["changes"]
    assert "resampled to 16000 Hz" in info["changes"]
    assert "converted to 16-bit" in info["changes"]


def test_describe_reports_nothing_for_a_ready_file(tmpwav):
    path = write_pcm(tmpwav("ready.wav"), tone(16000, rate=16000), 16000, 1, 16)

    assert describe(path)["changes"] == []


def test_describe_handles_float_wavs(tmpwav):
    path = write_float(tmpwav("df.wav"), tone(4410), 44100)

    info = describe(path)

    assert info["float"] is True
    assert "converted to 16-bit" in info["changes"]


# --------------------------------------------------------------------------
# Trimming and errors
# --------------------------------------------------------------------------

def test_max_seconds_trims(tmpwav):
    path = write_pcm(tmpwav("long.wav"), tone(44100 * 3, rate=44100), 44100, 1, 16)

    audio = load_wav(path, max_seconds=1.0)

    assert len(audio) == TARGET_RATE


def test_non_wav_is_rejected(tmp_path):
    path = str(tmp_path / "not.wav")

    with open(path, "wb") as handle:
        handle.write(b"this is not a wav file at all")

    with pytest.raises(AudioError):
        load_wav(path)
