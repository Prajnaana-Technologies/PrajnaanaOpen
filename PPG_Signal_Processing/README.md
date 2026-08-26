# ppg_analysis

Heart rate, heart-rate variability, respiratory rate and respiratory-rate
variability from a single photoplethysmogram channel.

Portable C99, no dependencies beyond `libm`, ~6 000 lines. One binary serves
neonates through adults — the patient type is a runtime setting, not a build.

```
                    ┌─────────┐   beats    ┌──────────┐   HR, HRV
   PPG ─── filter ──┤ detector├───────────►│ analysis ├──────────►
   125 Hz           └─────────┘            └────┬─────┘
                                                │ AM · BW · FM surrogates
                                                ▼
                                          ┌──────────┐   RR, RRV
                                          │ spectral ├──────────►
                                          └──────────┘
```

## Scope — read this first

**This is a reference implementation, not a product.**

It is published to show a working, fully-cited approach to the problem, and to
be a starting point for engineers building their own. Taking it into a product
means doing the work below, not just compiling it:

- **Re-derive and re-validate the parameters for your population and sensor.**
  The cited constants come from published data — respiratory and heart rate
  bands from paediatric and adult population studies, filter and detector
  parameters from their source papers. Those are *population* values measured on
  *particular* cohorts. A different patient group, a different PPG site, or a
  different optical front end can move them, and the citation that justifies a
  value for one cohort does not justify it for another.

- **Expect to re-tune for your hardware.** The validation data comes from
  bedside patient monitors: clean signal, stable contact, 125 Hz. A wearable or
  reflective sensor has different noise, motion artefact and amplitude
  behaviour, and the beat detector is where that will show first.

- **Validate on your own data.** Testing here is limited and its scope is stated
  precisely rather than summarised: **12 annotated adult recordings** from BIDMC
  with breath and ECG ground truth, **2 full-length neonatal recordings**, and
  **68 short recordings** from MIMIC-III Waveform. No paediatric ground truth
  was available at all, so the `child` setting is derived but unvalidated.
  There has been no clinical evaluation of any kind.

The accuracy figures in [`docs/RESULTS.md`](docs/RESULTS.md) are what was
measured on that data. They are not a claim about how the algorithm will behave
on yours.

> **This is not a medical device.** It has not been cleared or approved by any
> regulatory authority, and its outputs are not validated for clinical
> decision-making.

---

## Quick start

```sh
make
mkdir -p run && cd run
../ppg_analysis -i <recording.txt> -nu 1 -c 0 -s adult
```

One sample per line, no header. `-nu 1` reads integer ADC counts; `-nu 10000`
reads decimals with five digits — **getting this wrong is the usual reason a run
finds no beats.** `-s` selects the patient type and must match the subject.

The main output is `RR_Data.csv`, one row per analysis window, with `AVG_RR` as
the reported respiratory rate.

**Every option, what to expect, and how to read the output columns:
[`docs/USER_GUIDE.md`](docs/USER_GUIDE.md).**

## What makes this different

**Every constant traces to a paper — or says plainly that it doesn't.**
`filter_bands.h` opens with a rule the codebase actually enforces: *no number in
it is chosen by judgement — each one is traceable to a named paper or clinical
standard, cited beside it. If a value cannot be sourced it does not belong here.*
Each source is named beside the value it justifies — Fleming for the age-dependent
rate bands, Liang for the filter topology, Elgendi and Karlen for the detectors,
Charlton and Liu for the respiratory surrogates, the ESC/NASPE Task Force for HRV,
and so on. [`CREDITS.md`](CREDITS.md) indexes every one, with DOI links.

**And where it doesn't, that is stated and credited too.** Thirteen mechanisms were
designed for this implementation rather than taken from a paper — subject-scaled
detector windows, 1/f whitening, the agreement gate, the warm-up schedule and
more. Each is marked `DEVELOPER'S IMPROVEMENT` at the point it is defined, and
they are listed in [`CREDITS.md`](CREDITS.md).

**Limitations are stated, not disclaimed.** Each one below says what it bounds
and why; the measurements behind them are in
[`docs/RESULTS.md`](docs/RESULTS.md).

---

## Measured performance

Twelve annotated adult recordings from the BIDMC dataset. Beat detection scored
against ECG lead II R-peaks; respiratory rate against manual breath
annotations.

**Every measured figure is in [`docs/RESULTS.md`](docs/RESULTS.md)** — respiratory
rate against the manual breath annotations, beat detection against the ECG
reference, both detectors, the neonatal pair, per-recording tables, and how to
reproduce all of it.

It is deliberately the *only* place those numbers appear. Quoting them here as
well would mean two files to update when the algorithm is tuned, and the second
one is the one that silently goes stale.

What the figures will tell you, in words:

- Respiratory rate is accurate to well under 1 /min on settled windows, and the
  windows it declines are declined on purpose.
- **Coverage is below 100 % by design.** A window whose three respiratory
  surrogates disagree too much is declined rather than reported, following
  Karlen's smart-fusion criterion. The threshold scales with the patient's band
  width and is anchored to reproduce Karlen's 4 /min exactly on adults. A wrong
  number presented without qualification is worse than no number.
- Beat detection is strong on adults and the weaker detector is never the one
  selected for them; on neonates the ranking reverses, which is why the choice is
  made at run time.

**What the metrics mean** — `F1`, `MAE`, `within-2`, `coverage`, `settled`,
`beat match` — is defined once in [`docs/DESIGN.md`](docs/DESIGN.md), under
"Terms and abbreviations".
The scope and limits of the testing are in *Scope* above.

**Every duration quoted anywhere in this project is signal time** — seconds of
*recorded signal consumed*, never execution time. A "first report at 30 s" means
after 30 seconds' worth of samples have been read, not 30 seconds of waiting. The
program runs orders of magnitude faster than real time, so the two are never the
same number.

---

## One binary, three patient types

Rate bands, analysis window length and the default beat detector all change with
the patient, and are selected at run time:

```sh
./ppg_analysis -i rec.txt -s neonate        # neonate | child | adult
./ppg_analysis -i rec.txt -s adult -d ims   # override the detector, for comparison
```

Both detectors are always linked and dispatched at run time, because **which one
is better depends on the patient**: TERMA leads on adults, while IMS is clearly
better on neonates. One compile-time choice cannot serve both, and shipping two
binaries is what the patient knob exists to avoid.

The bands each type selects, and their citations, are in
[`docs/USER_GUIDE.md`](docs/USER_GUIDE.md).

---

## Live monitor

`Monitor/` holds a desktop application that turns this engine's output into a
patient-monitor style display: the raw and detection waveforms side by side with
the detector's onset and peak marks on them, and cards for heart rate,
respiratory rate, respiratory variability, the three HRV measures, and what the
engine did with the current window.

It reads **no files**. It launches the engine itself and consumes the plot
stream described under `-p on` in
[`docs/USER_GUIDE.md`](docs/USER_GUIDE.md) — per-sample rows at the sampling
rate, a per-beat row carrying the heart rate, and a per-window row with the
respiratory and variability figures — so what is on screen is what the engine
just emitted, not a file re-read after the fact. The heart rate has its own row
because it is known from the second beat, and waiting for the window row would
keep it off the display for another fifteen seconds.

A respiratory rate is **held through a window the engine declines**, with its
age shown beside it, and expires after one analysis window. That is deliberate:
declines arrive in runs, and blanking a vital sign for a minute while every
other card keeps updating tells a reader less than an ageing number does. It is
not a delay — a window that reports is shown as soon as it arrives, unmarked.

Nothing waits on the respiratory estimator that does not have to. **Heart rate
is on screen from the first frame**, carried on its own per-beat row rather than
on the window row that does not exist until a respiratory rate does. While that
one is still filling, the status card says how much longer — *"Warming up 16 of
66 s"* — and when a window is declined it says which surrogate stood apart. The
HRV figures carry the interval count they rest on, and the top bar names the
recording on screen.

Two synthesized recordings ship with it, adult and neonatal, so it can be run
without obtaining the third-party datasets. It needs Python with PySide6 and
pyqtgraph; the engine itself needs neither, and nothing in `src/` depends on it.

### Running it

A recent Debian or Ubuntu marks its system Python as externally managed and
will refuse to install into it, so the two packages go in a virtual
environment. PySide6 is not in apt at all:

```bash
python3 -m venv ~/ppgmon
~/ppgmon/bin/pip install PySide6 pyqtgraph

# from this directory -- it finds and builds the engine itself
~/ppgmon/bin/python Monitor/python_plot/ppg_plot.py -i Monitor/synthesized_sample_adult.txt -r 125 -s adult -c 0
```

`--outdir` puts the engine's CSVs somewhere other than the current directory,
`--exe` names an engine to use instead of building one, and `--no-build` stops
it invoking `make`. Nothing else is needed: the PySide6 wheel carries its own
Qt, including the Wayland and X11 platform plugins, so it runs on a normal
desktop session without further system packages.

| document | |
|:---|:---|
| [`Monitor/User_Guide_PPG_SignalMonitoring.pdf`](Monitor/User_Guide_PPG_SignalMonitoring.pdf) | installing and running the monitor, and what each card shows |
| [`Monitor/TechDoc_PPG_SignalMonitoring.pdf`](Monitor/TechDoc_PPG_SignalMonitoring.pdf) | how it is built and how it joins the two row shapes on the stream |

## Known limitations

Stated plainly because they bound what the outputs mean:

- **The reported heart rate can come out at twice the truth.** It is the most
  serious limitation here and the most dangerous shape a rate error can take:
  doubling a bradycardic patient lands *inside* the expected band, so no warning
  fires and the result looks healthy. **The dominant cause is a declared-subject
  mismatch, not a dicrotic wave** — with one category declared for all 68 short
  recordings, 11 of the 58 with a reference read high; declaring the category
  each recording's rate belongs to removes 9 of those and introduces none.
  **Two remain**, both reading 2.05 × their reference inside the correct band,
  and both source detectors' own defences against a dicrotic wave are already
  implemented. The evidence, including why the second peak does not have
  dicrotic morphology, is in [`docs/DESIGN.md`](docs/DESIGN.md),
  "Dicrotic doubling".
- **HRV is indicative, not Task Force conformant** — the rolling window
  straddles the 300 s short-term standard rather than satisfying it.
- **RRV is derived and indicative**, with no validated reference.
- **Neonatal RR coverage is roughly half the windows** — the quality gate
  correctly declines those where the surrogates disagree. Variability is
  reportable on far more of them, because a window can supply a rhythm without
  supplying a rate it stands behind.
- **Very slow breathing is not handled** — at 6 /min four harmonics fall inside
  the adult search band, the surrogates lock onto different ones, and the
  cohort's single 6 /min recording now reports a rate on 9 % of its windows.

Each is quantified in [`docs/USER_GUIDE.md`](docs/USER_GUIDE.md); the numbers
behind them are in [`docs/RESULTS.md`](docs/RESULTS.md) and the reasoning in
[`docs/DESIGN.md`](docs/DESIGN.md).

---

## Documentation

| file | |
|:---|:---|
| [`CHANGELOG.md`](CHANGELOG.md) | what changed in each release, and what it measured |
| [`docs/RESULTS.md`](docs/RESULTS.md) | **every measured figure, in one place** — the authoritative source when the algorithm is tuned |
| [`docs/ppg_arch.md`](docs/ppg_arch.md) | module map and how a sample flows through the pipeline |
| [`docs/DESIGN.md`](docs/DESIGN.md) | the engineering record — every design decision and the measurement that justifies it |
| [`docs/FIDUCIAL_INTERFACE.md`](docs/FIDUCIAL_INTERFACE.md) | the beat-detector contract — the detector is the one pluggable stage, so it is the one with a normative interface |
| [`docs/USER_GUIDE.md`](docs/USER_GUIDE.md) | how to build and run it, every option, and how to read the output |
| [`CREDITS.md`](CREDITS.md) | every research paper and dataset used, with DOI links, and the people who contributed |
| [`Monitor/User_Guide_PPG_SignalMonitoring.pdf`](Monitor/User_Guide_PPG_SignalMonitoring.pdf) | the live monitor: installing it, running it, and reading its display |
| [`Monitor/TechDoc_PPG_SignalMonitoring.pdf`](Monitor/TechDoc_PPG_SignalMonitoring.pdf) | the live monitor: how it is built and how it consumes the plot stream |

## Layout

```
PPG_Signal_Processing/
├── src/         seven translation units — see docs/ppg_arch.md
├── include/     ppg_common.h · ppg_fiducial.h · filter_bands.h
├── docs/        user guide, results, architecture, engineering record, contract
│   └── img/     generated figures (plain SVG, no drawing tool required)
├── Monitor/     the live display, its two documents, and two sample recordings
├── Makefile     `make` builds it — other targets are situational
├── LICENSE      Apache-2.0
└── NOTICE       attribution and the not-a-medical-device statement
```

## Reproducing the measured figures

The validation harness is a development tool and is not shipped here, and
neither are the recordings it scores against — they are third-party datasets,
see [`CREDITS.md`](CREDITS.md) for where to obtain them. The procedure it
follows is stated so the figures are auditable rather than asserted: copy
`src/`, `include/` and the `Makefile` to a scratch directory, insert **one
output-only line** printing each beat's peak index on the common tail of
`ppg_on_peak()`, assert by content digest that this is the only difference,
rebuild with `make strict`, run the fourteen recordings, and score beats
against ECG lead II R-peaks and respiratory rate against the manual breath
annotations, by the `beat match` criterion in [`docs/DESIGN.md`](docs/DESIGN.md).
The tree itself is never modified.

## Acknowledgements

Full credits — every source, dataset and contributor — are in
[`CREDITS.md`](CREDITS.md).

We gratefully acknowledge **Dr. Arathy R** for sharing her expertise in medical
signal processing and for providing valuable technical advisory feedback in a
personal capacity during the development of the PPG signal-processing
algorithms.

## Licence and attribution

Apache-2.0 — see [`LICENSE`](LICENSE). Copyright 2026 Prajnaana Technologies
Pvt. Ltd.

No dataset is distributed here. The reported figures were measured on the BIDMC
PPG and Respiration Dataset (ODC-BY) and the MIMIC-III Waveform Database (ODbL),
both Open Access on PhysioNet; required citations are in [`NOTICE`](NOTICE).

**This is not a medical device** — see *Scope* at the top.
