# Firmware release notes

<!--
Release notes of the proof-of-concept firmware (nRF52840). Another product
supplies its own release note in the same shape; nothing else changes.

How to write this file. Kept in a comment on purpose: the section under test
is read as the change description, so prose outside a comment selects
scenarios of its own — guidance written in the open would choose tests every
release, forever.

  - One section per build, headed by the build id the board reports, newest
    first. The pipeline reads only the section matching the build under
    test; older sections are history and select nothing.
  - The id has two halves. After the "+" is the hash of the firmware sources
    and build options: that is what identifies the code. Before it is
    `git describe`, which moves with every commit even when nothing in the
    image changed, so retitle the newest section whenever the board reports
    a new id for the same code.
  - Say what changed in plain words. "Removed the knee from the compressor"
    reads better than "audio dsp", and the planner understands both.
  - Name every change in that section. It is the only thing selection reads
    for that build, so a change left out of it is not tested for.
  - Include changes the image cannot show: work the compiler optimised away,
    build-system changes, anything that leaves no symbol behind.

Used by:  orchestrator --release-note NRF_Firmware/RELEASE_NOTES.md
          or the Release note field in the dashboard.
-->

## 355a3760+234c9ec0

Latest build. Comment-only edits, so this section names nothing to test.

Comments in `ble_telemetry_service.c`, `main.c` and `prj.conf` now state the
rule each one describes and the reason for it. No instruction in the shipped
image changed; the three files are hashed, so the build id moved anyway.

No GATT attribute changed in this build, so the static address stays as it is
and a host's cached database is still valid.

## eb0b6f9c+f008db6a

(The same code was also reported as `aa27dadd+f008db6a`.)

Latest build. One change, and it does not reach the image, so this section
names nothing to test.

The warning printk inside `main.c`'s `HA_DISCONNECT_TEST` block said "One
connection serves the whole test session" immediately after announcing that
it drops the link, which contradicted itself. It now says the host holds one
connection per session, so each drop interrupts whichever test is running.
The block is compiled out by default -- the switch is 0 -- so no instruction
in the shipped image changed. `main.c` is hashed, so the build id moved
anyway.

No GATT attribute changed in this build, so the static address stays as it
is and Windows' cached layout is still valid.

## e0eb3a29-dirty+6b64d6b2

(The same code was also reported as `8d9d738d+6b64d6b2`.)
(Includes build `87128edc-dirty+dd586d8d`, which differed only in the
nRF5340 comments of `prj.conf` and `dsp_wdrc.c`.)

History. Clears the audio carry on reset, drops an unused Bluetooth
option, and makes the build id independent of how the sources are checked
out.

### Audio

- The odd-byte carry is now cleared whenever the ring is reset, through a
  new `audio_stream_reset()` that replaces the three bare `ring_buf_reset`
  call sites (`main.c:138-144`). Resetting the ring without clearing the
  carry left one stale byte to be prepended to the first block of the next
  stream, which shifted every sample after it by one byte. A fresh
  subscriber now starts from a genuinely empty stream.

### Bluetooth

- `CONFIG_BT_GATT_DYNAMIC_DB` removed from `prj.conf`. Both services are
  declared with `BT_GATT_SERVICE_DEFINE` and register statically at boot,
  so nothing in this firmware registers a service at run time and the
  option only added code that could never be reached. The attribute table
  is unchanged, so the identity address stays at C1:F5:B1:81:20:F2 and a
  host's cached database is still valid — but the symbol set changed, so
  the image comparison will report it.

### Build system

- The build-identity hash now normalises CRLF to LF before hashing
  (`CMakeLists.txt:115-122`). `file(SHA256)` hashes raw bytes, so the same
  commit produced one id on a Windows checkout and another on Linux or in
  CI. Nobody rebuilding could reproduce the id this file's own headings
  name, and the section matcher then fell back to the whole note. An LF and
  a CRLF checkout now both give the same hash.
- SPDX and copyright headers added, and comment corrections in `prj.conf`,
  `dsp_wdrc.c` (the core is the nRF52840's Cortex-M4F; the nRF5340's is a
  Cortex-M33 and this firmware does not run on one), `ble_telemetry_service.c`,
  `main.c` and the board overlay, where the rail limits are now named. The
  sources, `prj.conf` and the overlay are all hashed (`CMakeLists.txt` is
  not), so a comment edit to any of them moves the build id although the
  compiled image is unchanged.

## ef509275-dirty+9e4947d1

History. Corrects the memory metric, the audio ring buffer and the
name of a test-only switch.

### Telemetry

- `mem_used` now samples the system work queue explicitly, through
  `k_work_queue_thread_get(&k_sys_work_q)`, rather than whichever thread
  happened to call in. `refresh()` was reached from three: the work queue,
  the Bluetooth receive thread whenever the host reads the telemetry
  characteristic, and the main thread at init. The host only ever reads
  `dee1` — it never subscribes — so the figure was usually the work
  queue's stack size minus the receive thread's unused stack. The reported
  high-water moves with this; the limit it is scored against does not.
- `read_snapshot()` no longer refreshes. The work queue owns the sample,
  and a read is not a sampling point.

### Audio

- An odd trailing byte is now held in a static carry and prepended to the
  next block, instead of being written back at the tail of the ring. A
  consumer putting a byte back behind newer data breaks the
  single-producer invariant and would reorder the stream. Every write the
  host makes today is even-sized, so nothing had gone wrong yet.

### Naming

- `CONFIG_HA_DISCONNECT_TEST` is now `HA_DISCONNECT_TEST`. It is a plain C
  define; the `CONFIG_` prefix belongs to Kconfig, and a reader looking for
  it in `prj.conf` would never have found it. No behaviour change.

No GATT attribute changed in this build, so the static address stays as it
is and Windows' cached layout is still valid.

## 3b0d68c5-dirty+4117746a

History. Adds battery level reporting.

### Battery

- Enabled the standard Battery Service, so characteristic 0x2A19 exists.
  The host has read that UUID since before it was there: `read_battery()`
  targeted it, and a comment claimed the device "already exposes" it. It did
  not — `CONFIG_BT_BAS` was never set, so every read failed and battery
  stayed unmeasured.
- Level is measured, not declared. The SAADC reads the supply rail through
  its internal VDD input and the firmware maps 1800-3600 mV onto 0-100
  percent. It reads 66 percent on this bench, which is the DK's 3.0 V rail.
  This is a rail voltage, not a state of charge; a product would read a fuel
  gauge. A rail that cannot be read reports 0xFF, unmeasured, rather than a
  default.
- Telemetry and the Battery Service now agree: both report the same figure
  from the same sample.

### Bluetooth

- Identity address bumped to C1:F5:B1:81:20:F2. Adding a service changes the
  GATT table, and a host holding a cached database against the old address
  would not re-discover it.

### Build system

- The devicetree overlay is named explicitly in `CMakeLists.txt`, rather
  than left to the `boards/<board>.overlay` convention. This entry used to
  say the convention did not match the board target `nrf52840dk/nrf52840`;
  that is not true of the file as it is named now, because for that target
  Zephyr looks for `boards/nrf52840dk_nrf52840.overlay`, which is exactly
  this file. Naming it explicitly is kept because it costs nothing and the
  same overlay is applied either way.
- Overlays are now part of the build-identity hash, alongside the sources
  and `prj.conf`. An overlay shapes the image, so a change to one must
  change the build id.
- `HA_OPEN_WRITE` now reaches the compiler. `SECURITY.md` has always
  documented `-DHA_OPEN_WRITE=0` for a build that requires an encrypted link,
  but `CMakeLists.txt` never forwarded the CMake variable, so `main.c`
  defaulted it back to 1 and the "secure" image came out byte-identical to the
  bench one. It is a cached option: it stays in the build directory until it
  is set again.
- The option now feeds the build-identity hash, and only when it is not the
  default. Two builds that differ solely in it have identical sources and
  would otherwise share an id, which is what the host keys its fingerprints
  on. Folding in only the non-default value leaves the delivered bench image
  where it was, at `+4117746a`, and gives the encrypted-write build a hash of
  its own. Neither option leaves a symbol behind, so nothing else records
  this.

## 99b1e43e-dirty+d8a23192

History. Restores the compressor after a deliberate regression used to
validate the suite. Symbol-identical to the build tested before that
experiment: the fingerprint comparison reports nothing added, removed or
resized.

### Audio

- Restored the knee branch in `wdrc_process`. Gain falls as the envelope
  rises again, bounded by `WDRC_GAIN_FLOOR` and `WDRC_GAIN_CEIL`, so the
  compressor compresses rather than applying a fixed gain. This reverts the
  regression in `+8493fc1f`, which `test_compression_curve` caught at 0.849
  quiet against 0.850 loud.

### Build system

- The source hash in `CMakeLists.txt` is now a configure dependency, so
  editing a firmware source recomputes the build id instead of reusing the
  previous one. Without it an incremental build produced changed code under
  the previous build's id, which would make the determinism KPI compare two
  builds that are not the same build, and overwrite one stored fingerprint
  with another. `CMakeLists.txt` is not itself hashed, so this changes no
  symbol and no build id — the image comparison cannot see it, and this
  note is its only record. The file is still not hashed today; what it
  contributes to the id is the one value it folds in itself, a non-default
  `HA_OPEN_WRITE` (see the 3b0d68c5 section).

## 99b1e43e-dirty+8493fc1f

History. Superseded by the section above; not selected against.

### Audio

- Removed the knee branch from `wdrc_process`, leaving a fixed gain at
  `WDRC_GAIN_CEIL`. Injected deliberately to confirm the suite detects a
  real behavioural regression. It did: `test_compression_curve` failed and
  named the fault, while every other test passed.
