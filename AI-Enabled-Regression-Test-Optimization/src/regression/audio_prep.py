# Copyright (c) 2026 Prajnaana Technologies Pvt. Ltd.
# Original author: Dhanya Shree S
# SPDX-License-Identifier: MIT
#
# Licensed under the MIT License. See LICENSE in the project root.

"""Get any WAV into the shape the device expects.

The firmware works in 16-bit mono at 16 kHz, and that is the right choice for
it: the compressor's attack and release constants are derived from the sample
rate, a hearing aid has one microphone and one speaker, and BLE GATT carries
about 26 kB/s -- less than one uncompressed 16 kHz mono stream.

Converting on the device would burn embedded cycles doing what the host can do
instantly, so all the flexibility lives here instead. Any WAV goes in:

    8 / 16 / 24 / 32-bit integer, or 32 / 64-bit float
    any sample rate
    any number of channels

and float32 mono at 16 kHz comes out, samples in -1.0..1.0. The
conversion to the 16-bit integers the firmware receives happens at the
point of sending, in ble_audio.send_audio_stream.
"""

import struct
import wave

import numpy as np

TARGET_RATE = 16000

# WAV format tags from the RIFF spec.
FORMAT_PCM = 0x0001
FORMAT_FLOAT = 0x0003
FORMAT_EXTENSIBLE = 0xFFFE


class AudioError(ValueError):
    pass


# --------------------------------------------------------------------------
# Reading
# --------------------------------------------------------------------------

def _parse_riff(path):
    """Read a WAV by walking its chunks.

    Python's wave module only understands integer PCM, so float WAVs and
    WAVE_FORMAT_EXTENSIBLE files raise "unknown format". This handles both.

    Returns (raw_data_bytes, format_tag, channels, rate, bits).
    """
    with open(path, "rb") as handle:
        riff = handle.read(12)

        if len(riff) < 12 or riff[:4] != b"RIFF" or riff[8:12] != b"WAVE":
            raise AudioError("not a RIFF/WAVE file")

        fmt = None
        data = None

        while True:
            header = handle.read(8)

            if len(header) < 8:
                break

            chunk_id, size = struct.unpack("<4sI", header)

            if chunk_id == b"fmt ":
                fmt = handle.read(size)
            elif chunk_id == b"data":
                data = handle.read(size)
            else:
                handle.seek(size, 1)

            # Chunks are word aligned.
            if size % 2:
                handle.seek(1, 1)

            if fmt is not None and data is not None:
                break

    if fmt is None or data is None:
        raise AudioError("missing fmt or data chunk")

    tag, channels, rate, _byte_rate, _align, bits = struct.unpack("<HHIIHH", fmt[:16])

    if tag == FORMAT_EXTENSIBLE:
        # The real format sits in the SubFormat GUID's first two bytes.
        if len(fmt) >= 26:
            tag = struct.unpack("<H", fmt[24:26])[0]
        else:
            tag = FORMAT_PCM

    return data, tag, channels, rate, bits


def _to_float(data, tag, bits):
    """Decode raw sample bytes to float32 in [-1, 1]."""
    if tag == FORMAT_FLOAT:
        if bits == 32:
            return np.frombuffer(data, dtype="<f4").astype(np.float32)
        if bits == 64:
            return np.frombuffer(data, dtype="<f8").astype(np.float32)
        raise AudioError("unsupported float width: {}-bit".format(bits))

    if tag != FORMAT_PCM:
        raise AudioError("unsupported WAV format tag 0x{:04x}".format(tag))

    if bits == 8:
        # 8-bit WAV is unsigned, centred on 128.
        raw = np.frombuffer(data, dtype=np.uint8).astype(np.float32)
        return (raw - 128.0) / 128.0

    if bits == 16:
        return np.frombuffer(data, dtype="<i2").astype(np.float32) / 32768.0

    if bits == 24:
        # Three bytes per sample, little-endian, signed. Widen to int32 by
        # putting the 24 bits in the high bytes so the sign carries over.
        raw = np.frombuffer(data, dtype=np.uint8)
        usable = (len(raw) // 3) * 3
        triples = raw[:usable].reshape(-1, 3)

        wide = np.zeros((len(triples), 4), dtype=np.uint8)
        wide[:, 1:] = triples

        values = wide.view("<i4").reshape(-1).astype(np.float32)

        return values / float(1 << 31)

    if bits == 32:
        return np.frombuffer(data, dtype="<i4").astype(np.float32) / float(1 << 31)

    raise AudioError("unsupported bit depth: {}-bit".format(bits))


# --------------------------------------------------------------------------
# Conversion
# --------------------------------------------------------------------------

def to_mono(audio, channels):
    """Average interleaved channels down to one."""
    if channels <= 1:
        return audio

    usable = (len(audio) // channels) * channels

    return audio[:usable].reshape(-1, channels).mean(axis=1)


def resample(audio, src_rate, dst_rate=TARGET_RATE):
    """Linear resample. Fine for test stimulus; not audiophile grade."""
    if src_rate == dst_rate or len(audio) == 0:
        return audio

    duration = len(audio) / float(src_rate)
    n_out = max(1, int(round(duration * dst_rate)))

    src_x = np.arange(len(audio), dtype=np.float64)
    dst_x = np.linspace(0, len(audio) - 1, n_out)

    return np.interp(dst_x, src_x, audio).astype(np.float32)


# --------------------------------------------------------------------------
# Public API
# --------------------------------------------------------------------------

def describe(path):
    """Report a file's properties and the conversion it needs."""
    try:
        with wave.open(path, "rb") as handle:
            info = {
                "rate": handle.getframerate(),
                "channels": handle.getnchannels(),
                "width": handle.getsampwidth(),
                "frames": handle.getnframes(),
                "float": False,
            }
    except Exception:
        # wave cannot open float or extensible WAVs; fall back to the parser.
        data, tag, channels, rate, bits = _parse_riff(path)

        info = {
            "rate": rate,
            "channels": channels,
            "width": max(1, bits // 8),
            "frames": len(data) // max(1, (bits // 8) * max(1, channels)),
            "float": tag == FORMAT_FLOAT,
        }

    info["bits"] = info["width"] * 8
    info["seconds"] = info["frames"] / float(info["rate"] or 1)

    changes = []
    if info["channels"] > 1:
        changes.append("mixed to mono")
    if info["rate"] != TARGET_RATE:
        changes.append("resampled to {} Hz".format(TARGET_RATE))
    if info["bits"] != 16 or info["float"]:
        changes.append("converted to 16-bit")

    info["changes"] = changes

    return info


def load_wav(path, target_rate=TARGET_RATE, max_seconds=None):
    """Return float32 mono samples at target_rate, ready to stream."""
    data, tag, channels, rate, bits = _parse_riff(path)

    if not rate:
        raise AudioError("file reports a sample rate of 0")

    audio = _to_float(data, tag, bits)

    audio = to_mono(audio, channels)
    audio = resample(audio, rate, target_rate)

    # Float WAVs are not guaranteed to stay inside [-1, 1].
    peak = float(np.max(np.abs(audio))) if len(audio) else 0.0
    if peak > 1.0:
        audio = audio / peak

    if max_seconds is not None:
        audio = audio[:int(max_seconds * target_rate)]

    return audio.astype(np.float32)
