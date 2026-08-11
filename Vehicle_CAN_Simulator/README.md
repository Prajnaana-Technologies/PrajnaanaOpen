# AI Vehicle CAN Simulator

A vehicle simulator that drives itself along **real road routes** and emits
**realistic CAN bus traffic** while it does — so you can develop and test
automotive electronics without a car, a driver, or a road.

You give it a start and a destination. It fetches the real driving route,
then simulates a car travelling that route end to end, producing live speed,
engine RPM, temperature, steering angle, climate and weather values, and
transmitting them as CAN frames over PCAN hardware or a virtual bus.

Think of it as a flight simulator for a car, where the computer drives and
you watch the signals.

---

## Why it exists

Testing automotive ECUs needs a believable, continuously changing stream of
vehicle data. Recording real drives is slow and hard to vary; hand-written
scripts produce data that doesn't behave like a car.

This project generates that stream from actual road geometry — speed limits,
curves, gradients and weather all influence how the simulated car drives.

---

## Features

- **Real routing** — any start/destination pair, using OpenStreetMap data
- **Road-aware driving** — respects speed limits, slows for curves and gradients
- **Live weather** — rain and snow reduce grip, so the car corners slower
- **LSTM-based dynamics** — a trained model produces engine/thermal behaviour
- **Per-vehicle profiles** — 22 brands, each with its own physics profile and
  optionally its own trained model
- **Real CAN output** — PCAN hardware, or a virtual bus when no hardware is present
- **Brand-accurate DBCs** — real reverse-engineered databases for Toyota,
  Hyundai/Kia and Tesla; synthetic ones for the rest
- **Optional AI driver** — Gemini, Claude, OpenAI or DeepSeek can make the
  speed decisions, or use the built-in rule-based logic with no key at all

---

## Requirements

- **Python 3.12** (built and tested on 3.12.10; must include tcl/tk for the UI)
- Windows 10/11 64-bit — the UI and PCAN integration are Windows-oriented
- Internet connection — for routing, road data and weather
- *Optional:* PEAK PCAN hardware plus the [PEAK driver](https://www.peak-system.com).
  Without it the simulator falls back to a virtual CAN bus and still runs.
- *Optional:* an API key from Gemini / Claude / OpenAI / DeepSeek for AI mode

---

## Install

```bash
git clone <your-repo-url>
cd vehicle_ai_sim_hybrid_new

python -m venv .venv
.venv\Scripts\activate          # Windows
# source .venv/bin/activate     # Linux/macOS

pip install -r requirements.txt
```

> **Versions in `requirements.txt` are pinned deliberately.** Unpinned installs
> pull newer releases that break the packaged build — see the note on
> `tkintermapview` in that file before bumping anything.

## Run

```bash
python main.py
```

1. Type a start and destination — suggestions appear as you type
2. Choose AI mode (needs your own key) or rule-based (needs nothing)
3. Optionally pick a brand/model and a top-speed limit
4. Press **START**

One tab shows the route on a map with a moving car; another shows the full
live dashboard. The run ends by itself on arrival.

---

## Project structure

```
main.py                  entry point
config.py                simulation constants and CAN settings
app_paths.py             path resolution (works frozen and unfrozen)
simulation.py            main simulation loop
vehicles.py              vehicle catalogue, profiles, model keys
can_codec.py             DBC encode/decode
dbc_registry.py          maps a brand to its DBC and signal layout

ai/
  lstm_thread.py         LSTM inference -> vehicle dynamics
  ai_providers.py        Gemini / Claude / OpenAI / DeepSeek clients
  ai_thread.py       AI speed-decision loop

can_bus/
  bus_manager.py         PCAN or virtual bus setup
  can_sender.py          encodes and transmits frames
  can_receiver.py        receives and decodes frames

maps/
  route_generator.py     geocoding + routing
  road_data.py           speed limits, road class, elevation
  weather.py             live weather -> grip factor
  map_thread.py          position along the route

ui/
  control_window.py      main window and dashboard
  route_input.py         start/destination entry
  interrupt_window.py

shared/vehicle_state.py  shared state between threads

training/                offline model training (not part of the running app)
  train_lstm.py            generic LSTM -> models/
  train_all_vehicles.py    per-car models -> models/cars/
  vehicle_trainer.py       shared training helpers

dbc/
  powertrain.dbc         master DBC (all brands merged)
  brands/                22 generated per-brand DBCs
  opendbc/               3 real DBCs from opendbc (MIT - see CREDITS.md)

models/
  vehicle_lstm.keras     generic model (committed)
  cars/                  per-car models (generated, git-ignored)

reports/                 project documentation
```

---

## Tools

| Command | Purpose |
|---|---|
| `python training/train_lstm.py` | Train the generic LSTM on synthetic data |
| `python training/train_all_vehicles.py` | Train a model per vehicle profile into `models/cars/` |
| `pyinstaller vehicle_sim.spec` | Build the standalone Windows application |

Run these from the project root — the scripts resolve `models/` relative to the
root, not to `training/`.

Training data is generated synthetically in `train_lstm.py` — no external
dataset is required or used.

---

## Configuration

Simulation constants live in `config.py`:

```python
CAN_INTERFACE = "pcan"          # or "virtual"
CAN_CHANNEL   = "PCAN_USBBUS1"
CAN_BITRATE   = 500000
DT            = 0.1             # simulation timestep (s)
```

API keys are entered in the UI. A `.env` file is optional — copy
`.env.example` to `.env` if you want a key pre-filled.

### Using your own CAN database

`app_paths.resource()` looks **next to the executable first**, then inside the
bundle. So you can drop your own `dbc/` folder beside `AIVehicleCANSimulator.exe`
(or in the project root when running from source) and it overrides the shipped
one — no rebuild needed.

> ⚠️ **A DBC allows only one message per CAN arbitration ID.** If you add a
> message whose ID is already used by another message in the same file, the
> duplicate is **silently dropped** — no error, no warning. It simply never
> transmits.

Practical limits when authoring a database for the simulator:

| Constraint | Detail |
|---|---|
| One message per ID | Duplicates are discarded; the **first** message claiming an ID wins |
| Standard IDs | `0x000`–`0x7FF` — 2,048 unique IDs maximum. Beyond that a message must use an extended 29-bit ID. |
| Current master file | `dbc/powertrain.dbc` already defines **116 messages across 116 unique IDs** |
| Brand ID blocks | Each brand occupies a small contiguous block (e.g. MG `0x658–0x65C`, BMW `0x660–0x664`). Pick IDs outside these to avoid collisions. |
| Multiplexed messages | Skipped by the transmitter — they need a mux selector the simulator does not set |
| Unmapped signals | Only signals named in the brand's signal map are transmitted; the rest are filled with safe defaults |

**If a message you added never appears on the bus, an ID collision is the first
thing to check.** Compare your IDs against those already defined in
`dbc/powertrain.dbc`.

---

## Network services

The simulator calls four public services at runtime:

| Purpose | Service |
|---|---|
| Place name → coordinates | Nominatim (OpenStreetMap) |
| Driving route | OSRM |
| Road attributes | Overpass (OpenStreetMap) |
| Weather and elevation | Open-Meteo |

These are **free community services with usage limits**. They are fine for
individual use. If you deploy this widely, or in a commercial setting, please
self-host or use a paid provider rather than loading the public instances —
see their respective usage policies.

**Privacy:** the route and coordinates you enter are sent to these services,
and in AI mode driving context is sent to your chosen AI provider. Nothing is
sent anywhere else, and the project collects no telemetry.

---

## Known limitations

- Windows-oriented — the UI and PCAN path are not tested on Linux/macOS
- The four service addresses are currently hardcoded in `maps/`
- TensorFlow is bundled solely to run inference on a small LSTM, and accounts
  for roughly 82% of the packaged build. Replacing it with onnxruntime would
  cut the build by about 90% — a worthwhile future contribution.

---

## Credits

This project includes work from **opendbc** (MIT), **OpenStreetMap** (ODbL),
**Open-Meteo** (CC-BY 4.0), and **python-can** (LGPL-3.0), among others.

See [CREDITS.md](CREDITS.md) for the full list and licence notices.

Vehicle manufacturer names are trademarks of their respective owners and are
used only to identify which CAN signal layout is being simulated. **This
project is not affiliated with or endorsed by any vehicle manufacturer.**

---

## Licence

**MIT** — see [LICENSE](LICENSE). Copyright © 2026 Prajnaana Technologies Pvt. Ltd.

You may use, copy, modify, merge, publish, distribute, sublicense and sell
copies of this software, for any purpose including commercial, provided the
copyright notice and the licence text are kept with it.

Two notices accompany the licence without modifying it:

- **Safety** — this is a simulation and test tool. It is **not** for
  safety-critical systems, certification, or use in or on any vehicle on
  public roads. It transmits on a CAN bus and must not be connected to a
  moving vehicle.
- **Third-party components** — opendbc (MIT), OpenStreetMap (ODbL),
  Open-Meteo (CC-BY 4.0) and python-can (LGPL-3.0) keep their own licences.
  See [CREDITS.md](CREDITS.md).
