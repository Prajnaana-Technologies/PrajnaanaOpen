# Copyright (c) 2026 Prajnaana Technologies Pvt. Ltd.
# Original author: Dhanya Shree S
# SPDX-License-Identifier: MIT
#
# Licensed under the MIT License. See LICENSE in the project root.

"""Hear what the DSP is doing.

    python tools/dsp_listen.py              # built-in loud/quiet demo
    python tools/dsp_listen.py --play       # ...and play both through the PC
    python tools/dsp_listen.py --input my.wav

Streams audio to the device, captures the processed audio it sends back, and
writes two files you can play side by side:

    dsp_before.wav   what was sent
    dsp_after.wav    what the device returned

The numbers in the regression suite prove the DSP changed the signal. This
lets you confirm it changed it the way a hearing aid should. The compressor's
gain is always below 1 (0.55-0.85, dsp_wdrc.h), so nothing is made louder:
loud passages are held back harder than quiet ones, which narrows the gap
between them.

The default demo alternates quiet and loud sections so the compression is
obvious by ear: in the processed file they should sound much closer in volume
than in the original.
"""

import argparse
import asyncio
import os
import sys
import wave

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np

from regression.audio_prep import TARGET_RATE, describe
from regression.audio_prep import load_wav as prepare_wav
from regression.ble.ble_audio import RealBLEDevice

SAMPLE_RATE = 16000

# One call to send_audio_stream carries at most this many samples.
BLOCK = 2000

# Length of each quiet/loud section in the demo. The compressor needs
# ~3250 samples (~200 ms) to recover from loud audio, so shorter
# sections never reach full gain and the effect is hard to hear.
SECTION_S = 0.5

BEFORE = "dsp_before.wav"
AFTER = "dsp_after.wav"


def dynamics_demo(seconds=2.0, sample_rate=SAMPLE_RATE):
    """Alternating quiet and loud speech-band tone bursts.

    A compressor should narrow the gap between the two. A plain volume
    control would not.
    """
    n = int(seconds * sample_rate)
    t = np.arange(n, dtype=np.float32) / sample_rate

    tone = (0.6 * np.sin(2 * np.pi * 220 * t)
            + 0.3 * np.sin(2 * np.pi * 660 * t)
            + 0.1 * np.sin(2 * np.pi * 1320 * t))
    tone /= np.max(np.abs(tone))

    # Quiet / loud every SECTION_S (0.5 s), with short ramps so there are
    # no clicks.
    envelope = np.empty(n, dtype=np.float32)
    step = int(SECTION_S * sample_rate)

    for i in range(0, n, step):
        level = 0.06 if (i // step) % 2 == 0 else 0.90
        envelope[i:i + step] = level

    ramp = int(0.005 * sample_rate)
    kernel = np.ones(ramp, dtype=np.float32) / ramp
    envelope = np.convolve(envelope, kernel, mode="same").astype(np.float32)

    return (tone * envelope).astype(np.float32)


def write_wav(path, samples, sample_rate=SAMPLE_RATE):
    pcm = np.clip(samples * 32767.0, -32768, 32767).astype(np.int16)

    with wave.open(path, "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(sample_rate)
        handle.writeframes(pcm.tobytes())


async def process_through_device(audio):
    """Send audio block by block, return what the device sent back."""
    device = RealBLEDevice()

    await device.connect()

    try:
        if not await device.supports_audio_loopback():
            raise SystemExit(
                "device does not echo processed audio -- nothing to listen to.\n"
                "Run: python tools/ble_probe.py")

        blocks = [audio[i:i + BLOCK] for i in range(0, len(audio), BLOCK)]
        out = []

        for index, block in enumerate(blocks, 1):
            print("  block {}/{} ({} samples)".format(index, len(blocks), len(block)))

            result = await device.send_audio_stream(block)

            if result is None:
                print("  no audio returned for this block, stopping")
                break

            if len(result) % 2:
                result = result[:-1]

            out.append(np.frombuffer(result, dtype=np.int16)
                       .astype(np.float32) / 32767.0)

        return np.concatenate(out) if out else np.array([], dtype=np.float32)

    finally:
        await device.disconnect()


def section_levels(audio, sample_rate=SAMPLE_RATE, step=SECTION_S):
    """RMS per `step` seconds (SECTION_S, 0.5), so quiet and loud sections
    can be compared."""
    size = int(step * sample_rate)
    return [float(np.sqrt(np.mean(audio[i:i + size] ** 2)))
            for i in range(0, len(audio) - size + 1, size)]


def report(before, after):
    n = min(len(before), len(after))
    before, after = before[:n], after[:n]

    levels_in = section_levels(before)
    levels_out = section_levels(after)

    quiet_in = [x for x in levels_in if x < np.median(levels_in)]
    loud_in = [x for x in levels_in if x >= np.median(levels_in)]

    pairs = list(zip(levels_in, levels_out))
    quiet = [(a, b) for a, b in pairs if a < np.median(levels_in)]
    loud = [(a, b) for a, b in pairs if a >= np.median(levels_in)]

    print("\n--- what the DSP did ---")

    if quiet and loud:
        quiet_gain = np.mean([b / max(a, 1e-9) for a, b in quiet])
        loud_gain = np.mean([b / max(a, 1e-9) for a, b in loud])

        print("  gain on quiet sections : {:.2f}x".format(quiet_gain))
        print("  gain on loud sections  : {:.2f}x".format(loud_gain))

        in_range = max(loud_in) / max(min(quiet_in), 1e-9)
        out_quiet = [b for a, b in quiet]
        out_loud = [b for a, b in loud]
        out_range = max(out_loud) / max(min(out_quiet), 1e-9)

        print("  loud/quiet ratio in    : {:.1f}x".format(in_range))
        print("  loud/quiet ratio out   : {:.1f}x".format(out_range))

        if in_range < 3.0:
            # Commercial music is already heavily compressed. With almost no
            # dynamic range to act on, a correct compressor applies nearly
            # constant gain -- that is not evidence it is broken.
            print("\n  This material has very little dynamic range "
                  "({:.1f}x loud-to-quiet).".format(in_range))
            print("  There is almost nothing for a compressor to work on, so")
            print("  near-constant gain here is expected, not a fault.")
            print("  Run without --input to hear compression on the demo signal.")
        elif out_range < in_range * 0.95:
            print("\n  The gap between quiet and loud got SMALLER.")
            print("  That is compression working -- what a hearing aid should do.")
        else:
            print("\n  The gap did not narrow, on material that had range to")
            print("  compress. The DSP is not compressing.")


def play(path):
    if not sys.platform.startswith("win"):
        print("  (auto-play is Windows only -- open {} yourself)".format(path))
        return

    import winsound

    print("  playing {} ...".format(path))
    winsound.PlaySound(path, winsound.SND_FILENAME)


def main():
    parser = argparse.ArgumentParser(description="Listen to the device's DSP")
    parser.add_argument("--input",
                        help="any WAV to send (default: built-in demo)")
    parser.add_argument("--seconds", type=float, default=10.0,
                        help="length of the built-in demo")
    # Streaming takes about three times the audio's own duration, which is
    # what a person needs to know before choosing a length. One 0.125 s
    # burst is 4000 bytes: about 32 GATT writes of 128 bytes paced 5 ms
    # apart (ble_audio.WRITE_PACING, 0.16 s), plus the 0.25 s of silence
    # that ends the burst (QUIET_PERIOD) -- roughly 0.4 s of wall clock per
    # 0.125 s of audio. The dashboard says the same ("10 s ... takes a
    # minute"). The cost is the pacing and that wait, not throughput: GATT
    # carries about 26 kB/s (audio_prep.py) against the stream's 32 kB/s,
    # which alone would be only about 1.2x slower than realtime.
    parser.add_argument("--max-seconds", type=float, default=10.0,
                        help="cap on --input length; streaming takes about "
                             "3x the audio's duration")
    parser.add_argument("--play", action="store_true",
                        help="play both files when done")

    args = parser.parse_args()

    if args.input:
        info = describe(args.input)

        print("Loaded {}: {} Hz, {} ch, {:.1f}s".format(
            args.input, info["rate"], info["channels"], info["seconds"]))

        if info["changes"]:
            print("  converting: " + ", ".join(info["changes"]))

        audio = prepare_wav(args.input, max_seconds=args.max_seconds)
        rate = TARGET_RATE

        held = len(audio) / float(rate)
        if held < info["seconds"] - 0.05:
            print("  trimmed to {:.1f}s (--max-seconds)".format(held))
    else:
        audio = dynamics_demo(args.seconds)
        rate = SAMPLE_RATE
        print("Using the built-in quiet/loud demo ({:.1f}s)".format(args.seconds))

    print("\nStreaming through the device...")
    processed = asyncio.run(process_through_device(audio))

    if processed.size == 0:
        raise SystemExit("nothing came back from the device")

    write_wav(BEFORE, audio[:len(processed)], rate)
    write_wav(AFTER, processed, rate)

    print("\nWrote {} and {}".format(BEFORE, AFTER))

    report(audio, processed)

    if args.play:
        print("\n--- listen ---")
        play(BEFORE)
        play(AFTER)
    else:
        print("\nPlay them to compare, or re-run with --play")


if __name__ == "__main__":
    main()
