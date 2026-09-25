# Security

This framework drives real hardware, and can optionally send planning
inputs to an AI model. Two things matter: the credentials it can be given,
and the exposure of the device under test. In the proof of concept that
device is an nRF52840 board reached over Bluetooth LE, which accepts
commands from anything in radio range.

## Credentials

The AI planner is optional and off by default. When it is enabled it needs an
API key for the model engine — Anthropic or Google Gemini, chosen with
`HA_ENGINE`. Each reads its own variable: `ANTHROPIC_API_KEY` (or
`ANTHROPIC_AUTH_TOKEN`) and `GEMINI_API_KEY` (or `GOOGLE_API_KEY`).

**Where a key may live**

- the application's own **API key** box, which holds it for the life of the
  window and writes it nowhere
- an environment variable in your shell session
- a secret store in CI (`Settings -> Secrets and variables -> Actions`)

**Where a key may never live**

- any file inside the project folder
- any file the application writes. It writes logs and generated test
  modules, and no key to any of them: there is no credential store of its
  own anywhere on the machine
- a test fixture, a notebook, a log, or a screenshot
- a commit message or a PR description

The application reads no `.env` and keeps no credential store of its own. A
key typed into its window is held in that running process, passed to the
runs it starts, and gone when the window closes. No key is written to disk,
so there is nothing to find afterwards and nothing to clean up.

That is the strength of the design rather than a gap in it. The project
folder is what gets zipped, committed and shared, and a credential in it
travels with it; a key that was never written down cannot travel, cannot be
recovered from a shared machine, and cannot be committed.

Environment variables are per-process, which is what makes this hold. What
the application sets reaches only itself and the runs it launches. It
cannot change your shell session or Windows, and none of it outlives the
process.

CI needs no key at all: every step in `.github/workflows/regression.yml`
that runs the pipeline sets `HA_AI=rules`, and the default is the rules
planner in any case, which calls no model. A CI job that does want the
model has no window to type into, so it takes the key from a repository
secret exposed as an environment variable.

**Where the Gemini key is sent.** `GEMINI_API_BASE` sets the host the Gemini
client posts to, and the key travels with the request in the
`x-goog-api-key` header. Left alone it is Google's own endpoint
(`https://generativelanguage.googleapis.com/v1beta`). Pointing it elsewhere
hands that host both the credential and the prompt, so set it only to a
proxy you control.

### If a key is committed

Deleting the file is **not** sufficient. The key stays in the history of every
clone and every fork, and it keeps working until it is revoked.

1. Revoke it at the provider first — for Anthropic,
   <https://console.anthropic.com/settings/keys>; for Gemini, delete the
   key in Google AI Studio
2. Issue a replacement and keep it outside the repository
3. Only then rewrite the history (`git filter-repo`, or delete and recreate the
   repository if it is young enough)
4. Force-push, and tell anyone holding a clone to re-clone

Step 1 is the one that actually protects the account. Steps 3 and 4 are
housekeeping.

### The scanner

```bash
python src/tools/scan_secrets.py --path .
```

Exit code 0 means clean, 1 means something was found. It runs early in CI —
after the checkout and the install, and before any test — because a leaked
credential is worth failing for even when everything else is green.

It reads the **working tree only**. A clean result says nothing about what is
already in the history — use a history scanner for that.

To run it before every commit:

```bash
printf '#!/bin/sh\npython src/tools/scan_secrets.py --path . --quiet\n' \
  > .git/hooks/pre-commit
chmod +x .git/hooks/pre-commit
```

The scanner enumerates through `git ls-files`, so it sees tracked and
untracked files but not ignored ones. Without git it walks the tree
instead, which does read ignored files — but both routes still skip
`.env`, `build/`, `dist/`, `venv/` and `_internal/`. A session key never
reaches the tree at all — it lives in memory and in the process
environment, never on disk — so there is nothing for the scanner to find,
which is the point.

## The proof-of-concept device

This section is specific to the nRF52840 proof of concept. A different
target brings its own exposure; review it the same way.

### Unauthenticated audio writes

The audio characteristic accepts writes with no pairing and no encryption.
That is deliberate for a bench: an unattended run must not need a pairing
step, and the "attacker" is a test script on the same desk.

It is **not** acceptable in a product. Anything within radio range can push
samples into the DSP.

The default is now explicit rather than accidental, and the firmware says so
at boot:

```
WARNING: audio write is unauthenticated (HA_OPEN_WRITE=1).
         Bench only. Build -DHA_OPEN_WRITE=0 to require encryption.
```

To require an encrypted link instead:

```bash
west build -b nrf52840dk/nrf52840 --pristine -- -DHA_OPEN_WRITE=0
```

The board target is the hardware-model-v2 name, with a slash. `HA_OPEN_WRITE`
is a cached CMake option: it persists in the build directory until it is set
again, so a later build that does not name it keeps the 0 cached here. Pass
`-DHA_OPEN_WRITE=1` explicitly for bench builds, or give each option its own
build directory.

The host must then bond before streaming. Expect the first connection after
flashing to fail until it does.

Check what you built rather than trusting the command: the boot banner
above appears only on an open-write build, and the build id changes with
the option.

### Static identity address

The firmware pins its Bluetooth identity (`HA_STATIC_ADDR` in
`src/NRF_Firmware/src/main.c`) to get around Windows' GATT cache: a host
that has already met the previous address keeps serving handles from its
cached copy of the database, so every change to the attribute table is
given a fresh address.

It does not identify a board. The host scans by name, and every board flashed
with this image advertises the same address, so on a bench with several they
cannot be told apart. It is not a security control either — a static address is
easier to track, not harder. Remove it for anything that leaves the lab.

### Lab network

The harness assumes the bench is trusted. If the runner machine is shared,
treat the artefact directories as readable by anyone with access to it:
`src/regression/logs/` and `src/regression/artifacts/` contain device telemetry,
test output, and the change descriptions the planner was given.

## What the LLM path sends

Only when `HA_AI=llm` is set. The planner's prompt contains:

- the device metrics and their values, with unmeasured ones named
- the change description — derived from the release note, the firmware
  image or the source tree, or passed with `--change`
- the risk engine's score and flagged test names
- the last five runs: build id, verdict, intensity, scenarios, and the names
  of the tests that failed

With `HA_AI=llm` the model also writes the requirement tests, so a second
prompt is sent for that, and it contains:

- the requirement document, in full
- the release-note section for the build under test, or the whole note
  when no heading names that build

Which generator writes those cases is `HA_CASE_SOURCE`, and its default is
`auto`: the model. `HA_AI=llm` gates all of it, so with the default
`HA_AI=rules` neither prompt is built and no document leaves the machine,
whatever `HA_CASE_SOURCE` says and whatever credentials happen to be
exported.

Neither prompt contains firmware source, audio samples, or credentials. If
your requirements or your change descriptions are sensitive — a release
note can be — the rules planner is the default and sends nothing
anywhere.

## Reporting a problem

Email <srinivasa.hc@prajnaanatech.com>. Please do not open a public issue for
anything credential-related.
