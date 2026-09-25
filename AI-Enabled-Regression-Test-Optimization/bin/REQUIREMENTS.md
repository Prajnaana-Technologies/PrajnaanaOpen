# Test requirements

<!--
These are the requirements of the proof-of-concept target, the nRF52840
audio-compressor firmware. The framework is generic: another product
replaces this file with its own requirements, in the same format.

Read by the canonical builder:

    python -m regression.ai_engine.canonical \
        --requirements REQUIREMENTS.md \
        --note NRF_Firmware/RELEASE_NOTES.md

Format: an identifier, a colon, one sentence. Lines that are not
requirements are ignored, so headings and notes are safe to write.

The identifier decides the component the requirement traces to, so keep the
prefix accurate: REQ-BLE-, REQ-AUD-, REQ-MEM-, REQ-PWR-, REQ-SYN-, REQ-TLM-,
REQ-SEC-, REQ-BAT-, REQ-ANC-.
-->

## How this document was produced, and what that means

**Every number below was read out of the implementation, not decided.** Each
one is the constant an existing assertion already uses, or a value measured
on the bench. Nothing here was invented.

That makes this document useful and weak at the same time:

- **Useful**, because every line is traceable to a test that runs today, so
  the suite gains requirement traceability it did not have.
- **Weak**, because a requirement that merely records what the code happens
  to do can never fail. "Latency shall be under 2000 ms" is not a product
  decision if 2000 is simply the constant someone typed.

Lines marked **[DECIDE]** are the ones where the number ought to come from a
product decision rather than from the source. Replace those with real
figures and the document starts doing its job.

Lines marked **[UNVERIFIABLE]** cannot be checked on this bench. They stay,
because a requirement is true whether or not the equipment exists.

---

## Bluetooth link

REQ-BLE-001: The device shall advertise under a discoverable name and accept a connection from a central. *(verified by `test_connection`)*

REQ-BLE-002: The device shall re-establish a connection after a clean disconnect. *(verified by `test_reconnect`)*

REQ-BLE-003: The device shall survive a link drop during an audio stream and remain connectable afterwards. *(verified by `test_link_drop_mid_stream`)*

REQ-BLE-004: The device shall serve a telemetry read while an audio stream is in progress. *(verified by `test_metrics_read_during_stream`)*

REQ-BLE-005: The device shall support one concurrent central connection. *(source: `CONFIG_BT_MAX_CONN=1`)*

REQ-BLE-006: The device shall report a build identifier of at most 32 characters. *(source: `HA_BUILD_ID_MAX`; checked by the canonical `read_build_id` case)*

REQ-BLE-007: The device shall accept an audio write of at least 1 byte. *(no existing test sends a short write; a zero-length write is a documented way to upset a GATT handler)*

## Audio and compression

REQ-AUD-001: The device shall return processed audio for every stream it accepts. *(verified by `test_audio_stream_basic`)*

REQ-AUD-002: The device shall modify the samples it receives rather than echoing them unchanged. *(source: `diff > 0.001`; verified by `test_audio_dsp`)*

REQ-AUD-003: Processing shall yield a signal-to-noise ratio above 0 dB and below 50 dB. *(source: `assert 0 < snr < 50`; verified by `test_audio_dsp`)* **[DECIDE]**

REQ-AUD-004: Host-to-device-and-back latency shall be under 2000 ms. *(source: `MAX_LATENCY_MS`; verified by `test_audio_latency`)* **[DECIDE]**

REQ-AUD-005: Applied gain for quiet input shall exceed gain for loud input by at least 25 percent. *(source: `MIN_COMPRESSION_RATIO`; verified by `test_compression_curve`)*

REQ-AUD-006: Applied gain shall remain between 0.55 and 0.85 at all times. *(source: `WDRC_GAIN_FLOOR`, `WDRC_GAIN_CEIL`)*

REQ-AUD-007: The device shall not drop samples from a stream it has accepted. *(verified by `test_no_samples_lost`)*

REQ-AUD-008: Fewer than 1 percent of loud samples shall return with the opposite sign. *(source: `ratio < 0.01`; verified by `test_full_scale_no_clipping`)*

REQ-AUD-009: The device shall process audio at 16 kHz. *(source: `WDRC_SAMPLE_RATE`)*

REQ-AUD-010: The compressor shall reach its attack within 5 ms and release within 60 ms. *(source: `WDRC_ATTACK_S`, `WDRC_RELEASE_S`)*

## Noise cancellation

Not implemented in the proof-of-concept firmware. The requirements are
stated because the suite's job is to say what is untested, not to stay
silent about it: each one derives cases that report as skipped, naming the
component the bench cannot drive.

The identifier is what makes that work. `ANC` maps to a component with no
entry in the drivable list, so every case here is refused before it runs.
Without that mapping the classifier falls back to the prose: the third
requirement below mentions the compressor, was read as an audio case, ran,
and passed against firmware that has no noise cancellation.

REQ-ANC-001: Active noise cancellation shall reduce background noise by at least 12 dB.

REQ-ANC-002: Enabling noise cancellation shall not add more than 8 ms of latency.

REQ-ANC-003: Noise cancellation shall remain stable while the compressor is active.

## Battery

REQ-BAT-001: The device shall expose battery level on the standard Battery Service, characteristic 0x2A19. *(source: `CONFIG_BT_BAS`)*

REQ-BAT-002: The reported battery level shall be between 0 and 100 percent.

REQ-BAT-003: Battery level in telemetry shall agree with the Battery Service.

REQ-BAT-004: A battery level that cannot be measured shall be reported as unmeasured rather than as a default. *(0xFF in telemetry)*

REQ-BAT-005: The device shall enter protection when battery temperature exceeds its limit. **[UNVERIFIABLE]** *(no thermistor on the DK)*

## Memory

REQ-MEM-001: Work-queue stack high-water shall not exceed 3.76 KiB of the 4.00 KiB allocated. *(source: 24 bench samples, all reading 3.53 KiB; the limit sits halfway between that plateau and the 4.00 KiB ceiling, so it fires ~240 bytes before the stack is exhausted)*

REQ-MEM-002: Work-queue stack high-water shall not grow by more than 0.25 KiB across the streams of a single run. *(source: `MAX_STACK_GROWTH_KB`)* **[DECIDE]**

> This one currently fails on the bench, and the fault is in the sentence
> rather than in the firmware. "Grow by" asks for the difference between
> two readings; the telemetry characteristic reports one figure, the
> absolute high-water, so the generated case scores an absolute
> measurement against a growth budget and cannot pass. Either the
> requirement is rewritten as an absolute limit — REQ-MEM-001 already is
> one — or the firmware is changed to report the growth. See the known
> limitation in `NRF_Firmware/RELEASE_NOTES.md`.

## Power

REQ-PWR-001: Supply current during a sustained audio stream shall not exceed 60 mA. *(source: `METRIC_THRESHOLDS["power"]`, the single limit the risk score and the generated tests both apply)* **[UNVERIFIABLE]** **[DECIDE]**

## Binaural synchronisation

REQ-SYN-001: Binaural synchronisation error shall not exceed 50 ms. *(source: `METRIC_THRESHOLDS["sync"]`, the single limit the risk score and the generated tests both apply)* **[UNVERIFIABLE]** **[DECIDE]**

## Telemetry

REQ-TLM-001: The device shall publish telemetry in version 3 of the wire format. *(source: `HA_TELEMETRY_VERSION`)*

REQ-TLM-002: The host shall reject a telemetry version it does not recognise rather than misreading the payload.

REQ-TLM-003: A metric the device does not report shall be recorded as unmeasured, never substituted with a default.

## Security

REQ-SEC-001: The audio write characteristic shall require an encrypted link unless the firmware is built for bench use with `HA_OPEN_WRITE=1`. *(see `SECURITY.md`)*

REQ-SEC-002: A firmware built with `HA_OPEN_WRITE=1` shall warn at boot that the write path is unauthenticated.

---

## Open calibration questions

Writing the requirements down surfaced three places where the number is not
yet settled: one where the host and the firmware disagree outright, and two
where the limit above rests on a calibration thinner than it looks.

**1. The host accepts gains the firmware cannot produce.** The generated
test asserts gain is within `[0.45, 0.95]` (`GAIN_MIN`, `GAIN_MAX`), while
the firmware bounds it to `[0.55, 0.85]`. The host window is wider on both
sides, so a firmware that drifted to 0.50 or 0.90 would pass a test whose
purpose is to catch exactly that. Either the host constants should tighten
to the firmware contract, or the contract is intended to move.

**2. The memory threshold has no variance behind it.** `metrics.json`
records 24 samples that all read exactly 3.53 KiB, idle and loaded. The
threshold of 3.76 is halfway from that plateau to the 4.00 KiB ceiling,
which is the only placement a bounded metric admits: 3.53 x 1.15 is
4.06, above the stack itself, and a limit above the ceiling can never
fire. The placement is sound; the calibration behind it is not. Samples
that never move are a single measurement wearing a sample count, and
nothing here will detect a regression smaller than 6 percent.

Since `ef509275` the calibration no longer describes the metric at all.
That build made `mem_used` sample the system work queue explicitly rather
than whichever thread called in, and the reading moved from the 3.53 KiB
plateau to about 0.52 KiB. The 3.76 KiB limit therefore sits roughly seven
times above anything the current metric reports, and cannot fire. Both the
threshold and REQ-MEM-001 need recalibrating against the metric as it now
behaves.

**3. `retry` is provisional rather than calibrated.** The metric no
longer counts link drops since boot: the host subtracts the disconnects
it asked for itself, so what remains is drops the device caused during
this run, and an absolute limit is meaningful again. `metrics.json`
still carries the older note calling one underivable, which described
the cumulative counter. The limit of 5 is a first guess — a healthy
board reports 0 — and should be confirmed against a few clean runs
rather than treated as calibrated.
