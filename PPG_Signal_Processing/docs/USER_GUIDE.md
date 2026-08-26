# User guide

How to run `ppg_analysis`, what every option does, and how to read what comes out.

For *why* the algorithm behaves as it does, see [`DESIGN.md`](DESIGN.md). This
file is the operating manual.

## Build

```sh
make
```

That is the whole thing. It builds `./ppg_analysis` with `-O2` and no arguments
are needed — `make` on its own **is** the normal build, and the only one most
users ever need. Portable C99; the only dependency is `libm`.

### The other targets, and when you would want one

None of these replaces `make`. They exist for specific situations:

| target | produces | differs how | when |
|:---|:---|:---|:---|
| `make` | `ppg_analysis` | `-O2`, no debug symbols | **normal use** |
| `make strict` | `ppg_analysis` — **same name, overwrites it** | adds `-Werror -Wshadow`: every warning becomes an error | before proposing a change, as a gate. Identical code, stricter compiler |
| `make debug` | `ppg_analysis_debug` | `-O0 -g` | stepping in a debugger. `-O0` stops the optimiser from folding or discarding variables, so what gdb shows matches the source; at `-O2` a variable may simply not exist |
| `make asan` | `ppg_analysis_asan` | `-O1 -g` + AddressSanitizer and UndefinedBehaviorSanitizer | hunting a crash or a wrong answer. Instruments every memory access and reports out-of-bounds, use-after-free, and undefined behaviour at the moment it happens |
| `make clean` | — | removes binaries **and** any CSVs left in the directory | |
| `make help` | — | prints the same summary from the Makefile itself | |

`debug` and `asan` write to **different names**, so they sit alongside the normal
binary rather than replacing it. `strict` does not — it writes `ppg_analysis`,
because it is the same program built with a stricter compiler, not a different
one.

The instrumented builds are larger and slower, but only by a small factor —
both are comfortably fast enough to run whole recordings, so there is no reason
to avoid them on performance grounds. Run `asan` with
`ASAN_OPTIONS=detect_leaks=0` — the program holds its buffers until exit by
design, so leak checking reports them at teardown and says nothing useful.

---

## Run

**The program writes its CSVs into the current working directory.** Run it from
a scratch directory, not from the source tree:

```sh
mkdir -p run && cd run
../ppg_analysis -i <recording.txt> -nu 1 -c 0 -s adult
```

### Input format

One sample per line, nothing else — no header, no timestamp column:

```
2069
2187
2213
```

Values may be integers (ADC counts) or decimals; `-nu` tells the program which.

---

## Options

`-h` or `--help` prints the option list and exits **0** — that is the path to use
in a script. Running `./ppg_analysis` with no arguments prints the same list but
reaches it by *failing*: it reports that it cannot open the default input and
exits non-zero. Both show the options; only `-h` is a success path. The patient-type
and detector rows in that list are generated from the tables in the source, so
they cannot go stale.

Options are matched on the **whole token**, and each except `-v` takes exactly
one argument.

| option | values | default | meaning |
|:---|:---|:---|:---|
| `-i <file>` | path, or `-` / `stdin` | `ppg_data.txt` | recording to analyse; `-` or `stdin` reads samples from standard input |
| `-nu <scale>` | positive integer, or `auto` | `auto` | multiplier applied to every sample as it is read |
| `-p <on\|off>` | `on` \| `off` | `off` | `on` streams `index,raw,smoothed,foot,peak` per sample to stdout for a live plot, and moves the whole console log to stderr |
| `-r <rate>` | 20–1000 | `125` | sampling rate in Hz |
| `-c <n>` | ≥ 0 | `2048` | samples to process; **`0` means the whole file** |
| `-s <subject>` | `neonate` \| `child` \| `adult` | `adult` | patient type |
| `-d <detector>` | `terma` \| `ims` | from `-s` | override the beat detector |
| `-o <prefix>` | string | none | prefixes `ppg_analysis.csv` and `RR_Data.csv` so successive runs do not overwrite each other |
| `-v`, `--version` | — | — | print the version and exit |

A malformed numeric argument is refused rather than silently coerced:

```
$ ./ppg_analysis -i rec.txt -c abc
ppg_analysis: ** -c abc is not a whole number. Use 0 for the whole file.
```

### `-nu` — you no longer have to get this right

`-nu` multiplies each sample before it is used. It exists because the input is
plain text with no declared scale, and **getting it wrong used to be the most
common way to get nothing out**: with `-nu 1` on a decimal recording every
sample truncates to `0` and no beat is ever found.

It is now decided for you. Omit `-nu` — or pass `-nu auto` — and the scale is
taken from the **text** of the first block:

| input looks like | auto picks | why |
|:---|:---|:---|
| `2069` — integer ADC counts | `1` | no `.` anywhere, so the counts are already usable and are left exactly alone |
| `0.61035` — decimals, 5 digits | `10^6` | five fractional digits, then raised until the recording clears 10⁵ counts full-scale |
| `5.00000e-01` — exponent form | `10^6` | the digit count stops at the `e`; the magnitude check makes up the difference |

The run says what it chose, and on what evidence:

```
Input scale: -nu 1000000 (auto: decimal text, 5 fractional digits, max |v| = 0.6217 over 1024 samples)
```

An explicit `-nu <n>` turns the decision off — someone who names a number means
that number.

**Why deciding it is safe.** Every amplitude constant in the tree is a *ratio*,
never an absolute count: the IMS thresholds multiply an amplitude measured from
the signal itself, TERMA's `alpha` is `beta * z` on the signal's own mean, and
`RR_MIN_PEAK_PROMINENCE` and `RR_HARMONIC_RATIO` are spectral ratios. So the
analysis is scale-invariant above the truncation floor, and the failure is
asymmetric — under-scaling is fatal, over-scaling is free until an `int32_t`
will not hold the sample. Auto therefore does not try to guess the format; it
takes as much resolution as the headroom allows.

Measured on `bidmc_01` by sweeping `-nu 1000 / 10000 / 100000 / 1000000` — 622,
6217, 62170 and 621700 counts full-scale:

| scales compared | HR, meanNN, SDNN, pNN50, beat count | HRV RMSSD |
|:---|:---|:---|
| 6217 vs 62170 counts | identical, 0 of 57 windows differ | 24 windows differ, worst 0.073 % |
| 62170 vs 621700 counts | identical | identical |
| 622 counts vs any | **degrades** | **degrades** |

RMSSD is a root-mean-square of *successive* differences, so a fiducial moved one
sample by integer rounding shows up in it twice — which is why the floor is 10⁵
counts and not 10⁴. Below about 10³ counts the truncation destroys real signal,
and that is the failure the floor exists to prevent.

### `-i -` / `-i stdin` — reading from a pipe

`-i -` and `-i stdin` both read samples from standard input, so *something* has
to feed it:

```bash
./ppg_analysis -i - -r 125 -s adult -c 0 < rec.txt     # a shell redirect
sensor_process | ./ppg_analysis -i stdin -r 125        # a live device
```

The scale probe works here too: it decides at the end of the first block rather
than rewinding, because a pipe cannot be rewound.

**With nothing on stdin** the run gets an immediate end-of-file: zero samples,
no beats, and the `NO BEATS DETECTED` banner — which reads like a signal problem
and is not one. Either feed the pipe, or pass a filename.

### `-p on` — the live plot stream

`-p on` puts everything a live display needs on stdout, and moves the entire
console log to stderr so that stream carries nothing else. **Three row shapes**,
told apart by their first character:

```
1204,435970,-138,0,1                 per sample   -- starts with a digit or sign
H,1.400,93                           per beat     -- starts with 'H'
R,17.088,21.973,...,90,...,2.694,26  per window   -- starts with 'R'
```

The `H` row exists because the window row does not appear until the respiratory
estimator makes its first report, and that is far later than a heart rate is
known: on `bidmc_04` the second beat lands at 1.4 s and the first window row at
16.8 s. A display fed only by the window row shows no heart rate for fifteen
seconds while the beats it is already drawing march past. The `H` row carries
the time and the rate and nothing else -- the variability figures genuinely need
history, and stay on the window row where `HRV_n` says what they rest on. It
arrives about 1.5 times a second against 125 sample rows, so it is not on the
hot path.

| row | when | fields |
|:---|:---|:---|
| `index,raw,smoothed,foot,peak` | every sample | 5 |
| `R,` + 13 of the RR_Data columns, then eleven more | every analysis window, ~8.2 s | 25 |

Sample rows stay **untagged** on purpose: they are 99.99 % of the stream, so
their framing is the only per-line cost the format has. Dispatching on the first
character costs the hot path nothing, and keeps the sample rows byte-identical
to the trace CSV's own columns (see the `cmp` check below).

The `R` row is a **subset** of the CSV row, not a copy of it: it carries the
values a display shows and no others. Where the two overlap they are identical —
`Time`, `AVG_RR`, `Method`, `N_used`, `Spread_bpm`, both `RRV_*` values,
`RRV_intervals`, `HR_bpm` and the four `HRV_*` metrics — so a reader needs **no
file access at all** for anything it displays.

Eleven fields follow the thirteen, all **appended** rather than inserted so a
reader written against a shorter row keeps working:

| field | |
|:---|:---|
| window seconds | how much signal this estimate used |
| full window seconds | what it is growing towards |
| `HRV_n` | intervals the HRV figures rest on |
| `AM_RR`, `BW_RR`, `FM_RR` | the three surrogate estimates |
| `AM_q`, `BW_q`, `FM_q` | their spectral prominences |
| agreement threshold, prominence floor | the two limits they are judged by |

The two window figures exist because the respiratory estimator reports
**progressively** — it starts at `RR_PROG_MIN_PTS` and doubles its window until
it reaches the subject's full one — so a display can say how much longer instead
of showing a bare "warming up" that is indistinguishable from a stall.

The surrogates and their prominences are what make a `DECLINED` window
explicable rather than merely announced. **The two thresholds travel with them
because both are per-subject**: a reader that hard-coded the adult values would
quietly mis-explain every neonatal window. With all eight, the engine's verdict
is reproducible at the far end rather than asserted — which is how the shipped
display says *"Searching · BW differs"* instead of just *"Searching"*.

One caution when reading that phrase: it names the estimate that stands **apart
from the other two**, which is not the same as naming the one that is wrong.
When two surrogates lock onto the same artefact they agree with each other, and
the one that differs is the correct one. `TD_RR` remains off the stream; it does
not vote, and `RR_Data.csv` has it.

`sample` is the **smoothed** signal, because that is what the detectors were
handed and what their marks belong on; `foot` and `peak` are flags, and lose
nothing by being flags — the trace's marker columns are `smoothed_sample` or
`0`, so on that scale the amplitude a flag replaces is the sample already on the
row.

Both halves are checkable against the files they mirror:

```bash
./ppg_analysis -i rec.txt -r 125 -s adult -c 0 -p on -o run > plot.txt

# the sample rows are exactly columns 1, 4, 5, 6 of the trace
grep -v '^R,' plot.txt > samples.txt
awk -F, 'NR>2 { gsub(/ /,""); print $1","$4","($5!=0?1:0)","($6!=0?1:0) }' \
    run_ppg_analysis.csv > check.txt
cmp samples.txt check.txt       # silent

# the R rows are exactly RR_Data.csv
grep '^R,' plot.txt | sed 's/^R,//' > metrics.txt
tail -n +2 run_RR_Data.csv | tr -d '\r' > rrfile.txt
cmp metrics.txt rrfile.txt      # silent
```

`-p off` is the default and leaves a standalone run byte-for-byte as it was.
See `PIPE_INTERFACE.md` and `Monitor/python_plot/` for the reader.

### `-s` — the patient type is a knob, not a build

One binary serves all three. `-s` selects the rate bands, the analysis window,
and which detector runs by default:

| `-s` | RR band | HR band | window / segment | default detector |
|:---|:---|:---|:---|:---|
| `neonate` | 22–66 /min | 90–181 bpm | 512 / 128 | Karlen IMS |
| `child` | 11–53 /min | 43–156 bpm | 1024 / 256 | Elgendi TERMA |
| `adult` | 4–30 /min | 43–104 bpm | 1024 / 512 | Elgendi TERMA |

Every one of those numbers is cited — see [`../CREDITS.md`](../CREDITS.md).

**Set this correctly.** The band is a prior the algorithm reasons from; the
wrong one is the single largest source of avoidable error.

There is a sanity check, and it prints at start-up when the measured heart rate
falls outside the range expected for the declared type:

```
RR band: ** WARNING ** measured median HR 110 bpm is outside the 43-104 bpm expected for ADULT (over 18 years).
          Check the declared subject band -- the wrong band
          is the largest single source of RR error.
```

It is checked at start-up on the first beats, and again on the settled heart rate
as the analysis runs, warning at most once. The second check matters: `-s` and
`-r` both scale the beat detector, so a badly wrong value can mis-tune detection
in the very way that drags the *early* beats into the expected range. Judging on
the settled rate catches that; judging only on the first few does not.

**It is still a sanity check, not a guarantee.** It compares one number against a
population range, so a subject whose true rate sits inside the wrong band's range
will not be flagged. Set `-s` correctly regardless.

There is deliberately **no option to set a band directly.** A band supplied on
the command line would be an uncited number overriding a cited one, and could
silently contradict `-s`. Turn the knob instead.

### `-d` — overriding the detector

For comparison only. Which detector is better depends on the patient: TERMA
leads on adults, IMS on neonates. `-s` already picks the better one; `-d` exists
to measure the other. The measured comparison is in
[`RESULTS.md`](RESULTS.md).

---

## What to expect

### Everything below is measured in seconds of SIGNAL, not run time

The program runs **orders of magnitude faster than real time** — minutes of
recording are processed in a fraction of a second. Where this guide says "the
first estimate arrives at 17 s", that means *after 17 seconds' worth of recorded
samples have been consumed*, not 17 seconds of waiting.

### Heart rate is available almost immediately; respiratory rate is not

**HR and HRV** are computed from the beats themselves, so the first heart rate
appears after the first beat interval — about a second of signal. Each beat
prints one line as it is found:

```
IBI = 80 samples = 640 ms, HR = 93 bpm
```

**RR and RRV** need a window of *breaths* before a spectrum means anything. At
adult rates the first respiratory estimate comes after roughly **17–26 s of
signal**, and rows before that carry `AVG_RR = -1`.

One consequence worth knowing: **`RR_Data.csv` gains a row only when an analysis
window closes**, so its first row appears at ~17 s even though HR was known long
before. That first row does carry a valid `HR_bpm` — it is the respiratory
columns, not the cardiac ones, that are waiting.

Early respiratory rows are marked with a **`_PROV`** suffix on `Method` — a
provisional estimate from a partly-filled window. They are measurably less
accurate than settled rows — the two are scored separately in
[`RESULTS.md`](RESULTS.md) — and should be treated as provisional.

*`MAE`, `F1` and the other accuracy terms used here are defined in
[`DESIGN.md`](DESIGN.md), under "Reading the numbers".*

### Some windows report nothing, on purpose

Roughly 10 % of adult windows and about half of neonatal ones are **declined**.
This is by design: when the three respiratory surrogates disagree by more than
the allowed spread, the window is marked `DECLINED` and `AVG_RR` is `-1` rather
than reporting a number the data does not support. The allowed spread is **4 /min
for adults** — Karlen's figure — and scales with the band width for the other
patient types, reaching about 6.5 /min for children and 6.8 for neonates, because
a wider band scatters proportionally more.

A declined window is not a failure. A wrong number presented without
qualification is worse than no number.

### Sentinel values

**`-1` means "not reportable"**, everywhere it appears — `AVG_RR`, the two
`RRV_*` value fields, the four `HRV_*` value fields. It never means "zero".
`RRV_intervals` and `HRV_n` are counts, not measurements, and are never `-1`.

The `RRV_*` fields are not all-or-nothing: `RRV_RMSSD_ms` can read `-1` while
`RRV_SD_ms` carries a value. RMSSD is built from *successive* differences, so it
needs two breath intervals that are genuinely adjacent; when the interval gate
has dropped everything in between, the spread is still measurable but the
successive difference is not.

---

## Output

Five files, written to the current directory.

### Why there is more than one file

The three groups are **not** alternative views of the same table. Each sits at a
different point in the pipeline, and — because the pipeline changes sampling rate
twice — each has a different number of rows. They cannot be merged without either
padding the results out to one row per sample or throwing away the sample detail.

`RR_Data.csv` carries **conclusions**. One row per analysis window, and every
field in it is a *derived measurement*: the respiratory rate finally reported,
how it was arrived at, heart rate, the HRV and RRV figures, and the quality
fields that say how much to trust the row. Nothing in it is a signal. This is
the file you read to get answers, and for normal use it is the only one you need.

`ppg_analysis.csv` carries **the signal itself**, one row per input sample, as it
looked at each stage of conditioning — raw as read, after the Chebyshev
band-pass, after smoothing. It is the only place the filter's effect is visible.
You open it when a recording gives an answer you distrust and you need to see
whether the front end mangled the waveform, and you plot it rather than read it.

`INTP_RR_{peak,foot,ibi}.csv` carry **the three respiratory surrogates on the
uniform grid the spectral stage actually consumes** — the intermediate signals,
after beat-sampling and resampling but before the PSD. (Why they have to be
resampled at all, with a figure, is in [`ppg_arch.md`](ppg_arch.md).) They exist to answer "why
did the fusion pick that rate", because the surrogate columns in `RR_Data.csv`
give only each surrogate's final answer, not the waveform behind it.

So: `RR_Data.csv` is what the program concluded, `ppg_analysis.csv` is what it
was given, and the `INTP_RR_*` files are what it reasoned over in between.

| file | one row per | rate | rows for a 480 s recording |
|:---|:---|:---|---:|
| `ppg_analysis.csv` | input sample | 125 Hz (`-r`) | 60,003 |
| `INTP_RR_peak.csv`, `INTP_RR_foot.csv`, `INTP_RR_fm.csv` | grid point | 15.625 Hz | ~7,485 |
| `RR_Data.csv` | analysis window | one per 8.2 s of signal | 58 |

Those row counts are just the rate conversions, and they are the clearest way to
see why the files are separate. They are counted from a real BIDMC recording,
which carries 60,001 samples over its 480 s rather than exactly 480 × 125, and
they include each file's header line (and, for `ppg_analysis.csv`, its `#
recording:` comment).

**`-o <prefix>` renames only `ppg_analysis.csv` and `RR_Data.csv`.** The three
`INTP_RR_*` files keep constant names, so two runs in the same directory with
different prefixes each keep their own prefixed pair, but the surrogate files are
whichever run finished last. Use a separate directory per run if you need those.

`ppg_analysis.csv` also dominates the output — a few MB against a few kB for the
results, since it is one row per sample. Worth knowing before a long recording;
there is currently no switch to suppress it.

### `RR_Data.csv`

```
Time(sec),AM_RR,BW_RR,FM_RR,AM_q,BW_q,FM_q,TD_RR,AVG_RR,Method,N_used,
Spread_bpm,RRV_SD_ms,RRV_RMSSD_ms,RRV_intervals,HR_bpm,HRV_meanNN_ms,
HRV_SDNN_ms,HRV_RMSSD_ms,HRV_pNN50_pct,HRV_n,Version=1.1.0
```

| column | meaning |
|:---|:---|
| `Time(sec)` | end of the analysis window |
> **`RR` here means RESPIRATORY RATE, not the beat interval.** In HRV literature
> "RR interval" is the beat-to-beat R-peak to R-peak interval; in this program
> `RR` is always breaths per minute, and beat intervals are called IBI or NN.
> So `AVG_RR` and `HRV_meanNN_ms` are not comparable and are not meant to be —
> one is a rate in /min, the other an interval in ms. A row reading
> `AVG_RR 20.3` and `HRV_meanNN_ms 665.7` is consistent: 665.7 ms is one beat,
> which is 60000 / 665.7 = 90 bpm, and that is the `HR_bpm` in the same row.
> `RRV_*` follows the same rule — it is **respiratory**-rate variability, not
> R-R variability.
>
> **For heart-rate variation, read `HRV_SDNN_ms` and `HRV_RMSSD_ms`** — SDNN for
> the overall spread, RMSSD for beat-to-beat change — with `HRV_n` beside them
> as the quality channel. `HRV_meanNN_ms` is a mean, not a spread.

| **`AVG_RR`** | **the reported respiratory rate, /min. This is the answer.** `-1` if not reportable |
| `AM_RR`, `BW_RR`, `FM_RR` | the three surrogate estimates that fed the fusion |
| `AM_q`, `BW_q`, `FM_q` | peak prominence of each — how far the spectral peak stands above its background |
| `TD_RR` | time-domain breath count, reported for comparison. **Does not vote** in the fusion |
| `Method` | how `AVG_RR` was arrived at — see below |
| `N_used` | how many surrogates were used |
| `Spread_bpm` | standard deviation of the three surrogates — a per-window uncertainty. Large means low confidence. **Breaths/min, despite the `_bpm` suffix that `HR_bpm` uses for beats/min** |
| `RRV_SD_ms`, `RRV_RMSSD_ms` | breath-interval variability. **Derived and indicative**, no validated reference |
| `RRV_intervals` | number of accepted **intervals**, not breaths — *n* breaths give *n*−1 intervals |
| `HR_bpm` | heart rate |
| `HRV_meanNN_ms`, `HRV_SDNN_ms`, `HRV_RMSSD_ms`, `HRV_pNN50_pct` | HRV over a rolling 300-beat window. **Indicative, not Task Force conformant** |
| `HRV_n` | how many NN intervals this row's HRV was computed from. The window holds 300 beats and only **unrepaired** intervals enter it, so this is what survived artifact rejection, not simply how long the run has been going. **Early** in a recording a small value just means the window has not filled; **late** in one it means intervals are being rejected and the figure rests on the fragments that survived |

### Displaying HRV and RRV

They are two different measurements of two different organs, and every field
belongs to exactly one of them. Nothing in one panel should be compared with
anything in the other.

**Heart-rate variability — the beat series**

| show | field | note |
|:---|:---|:---|
| rate | `HR_bpm` | beats/min |
| mean interval | `HRV_meanNN_ms` | ms. A **mean**, not a variability — label it "mean NN", never "HRV mean" |
| overall variability | **`HRV_SDNN_ms`** | ms. The general-purpose HRV figure |
| short-term variability | **`HRV_RMSSD_ms`** | ms. Beat-to-beat change |
| | `HRV_pNN50_pct` | % of successive intervals differing by more than 50 ms |
| how many intervals it is based on | `HRV_n` | show it beside the values, or hold the panel back until it is large enough to trust |

**Respiratory-rate variability — the breath series**

| show | field | note |
|:---|:---|:---|
| rate | `AVG_RR` | breaths/min. `-1` means not reportable |
| spread | **`RRV_SD_ms`** | ms, breath-to-breath intervals |
| short-term | **`RRV_RMSSD_ms`** | ms |
| how many intervals it is based on | `RRV_intervals` | accepted intervals, not breaths |
| confidence | `Spread_bpm`, `Method` | breaths/min, and how the rate was arrived at |

Three rules for either panel. Suppress any value reading `-1` rather than
plotting it — it is a sentinel, not a measurement. Show the interval count
beside the figure, because a variability computed over three intervals and one
computed over three hundred should not look alike on screen. And carry the
caveats: the `HRV_*` fields are **indicative, not Task Force conformant**, taken
over a rolling 300-beat window, and the `RRV_*` fields have **no validated
reference** at all.

### Reading `Method`

| value | meaning |
|:---|:---|
| `ALL_THREE` | all three surrogates agreed — highest confidence |
| `TWO_AGREE` | two agreed, one was outvoted |
| `TD_HARM` | the spectral estimate looked like a harmonic; the breath count was preferred |
| `TD_SUBHARM` | a sub-harmonic lock was caught and corrected against the breath count |
| `DECLINED` | surrogates disagreed too much to report |
| `REJECTED` | no credible spectral peak — the window was unusable |
| *any* `_PROV` | provisional: the window was not yet full |

### `ppg_analysis.csv`

The per-sample trace. The first line is a `#` comment naming the recording, so a
trace can always be traced back to its input; the header follows.

```
# recording: <path>
Index,InputSample,Chebyshev,Smoothed,ppg_foot,ppg_peak,interp_foot,interp_peak,interp_fm,AM-signal,Version=1.1.0
```

| column | meaning |
|:---|:---|
| `Index` | sample number from the start of the recording. Divide by `-r` for the time in seconds |
| `InputSample` | the sample **as read**, after `-nu` scaling. Your input, unmodified otherwise |
| `Chebyshev` | after the Chebyshev Type II band-pass, **before** smoothing |
| `Smoothed` | the same sample after the 40 ms moving average — this is what the detector actually decides on |
| `ppg_foot` | the smoothed value where a pulse onset was detected, and 0 on every other row |
| `ppg_peak` | the smoothed value where a signal peak was detected, and 0 on every other row |
| `interp_foot` | the foot (BW) surrogate track, held at its last value between beats |
| `interp_peak` | the peak (AM) surrogate track, likewise |
| `interp_fm` | the FM surrogate track, likewise |
| `AM-signal` | `interp_peak − interp_foot`, the pulse amplitude. **Diagnostic only** — see the note below |

> **The trace lags the input, and stops short of its end.** A detector names a
> peak and an onset only once the signal after them has arrived, so a row cannot
> be written until the detector can no longer reach back to it. The wait is one
> beat plus the detector's reporting lag — 93 samples typically and 187 at worst
> for an adult at 125 Hz, 22 and 336 for a neonate — and it is asked of the
> detector rather than assumed, so it follows the rate and the patient type. The
> samples still inside that reach when the input ends carry no decided marks and
> are not written, which is why the trace is shorter than the recording.

> **A long stretch with neither mark is announced on the console**, naming the
> sample range and its duration. Runs shorter than five of the subject's slowest
> beats are ordinary gaps between pulses and pass without comment; anything
> longer means no pulse was found there, which is a finding about the recording
> rather than a gap in the tooling.

> **`Chebyshev` and `Smoothed` both carry a constant offset.** A constant pedestal is
> added after the band-pass so the conditioned stream stays comfortably positive
> in integer arithmetic. It is the same constant on every sample, so it shifts
> both columns equally and changes no rate, interval or amplitude difference. To
> see the moving average's effect on its own, subtract one column from the other —
> the pedestal cancels. The two are directly comparable row by row: the average is
> stored at the middle of its own window, so `Chebyshev` and `Smoothed` in the same
> row describe the same sample.

> **`interp_fm` is not the peak-to-peak beat interval.** FM is Liu's
> pulse-interval modulation: the interval between successive points of *maximal
> upslope*, not between peaks. It is a beat-interval-like quantity measured at a
> different landmark, so it will not match the `HRV_*` columns in `RR_Data.csv`,
> which are built from peak-to-peak intervals. The two are close but not the same
> series, and only the peak-to-peak one is HRV.

The three `interp_*` columns are step-like rather than smooth: a surrogate only
gains a new value when a beat produces one, so between beats the column holds the
previous value. They are all zero until the first beats arrive, which is the
truth rather than a gap. These columns are on the **sample** axis; the
`INTP_RR_*` files carry the same three quantities on the grid axis after
resampling, and it is the grid version that determines the result.

> **`AM-signal` does not feed the analysis.** It is computed at the point of
> writing the trace and used nowhere else. The AM surrogate that reaches the PSD
> is the `interp_peak` track on its own — detrended and normalised inside the
> spectral stage, which removes the baseline and leaves the peak track's
> fluctuation as the amplitude modulation. `AM-signal` is offered as a
> human-readable pulse-amplitude column for plotting; do not read it as the
> signal the fusion scored.

### `INTP_RR_peak.csv`, `INTP_RR_foot.csv`, `INTP_RR_fm.csv`

The three surrogates on the uniform 15.625 Hz grid — the last stage before the
spectral estimate. Identical structure, one per surrogate:

```
Index,Peak_raw,Peak_Mvg,Version=1.1.0
Index,Foot_raw,Foot_Mvg,Version=1.1.0
Index,FM_raw,FM_Mvg,Version=1.1.0
```

| column | meaning |
|:---|:---|
| `Index` | grid point, at `RR_INTP_GRID_HZ` (15.625 Hz at the 125 Hz design point), **not** the input sample index. Each row is labelled at the centre of the window the moving average covered, so `*_raw` and `*_Mvg` describe the same instant and the three files align with each other |
| `*_raw` | the interpolated surrogate value at that grid point, before smoothing. **"raw" here means un-smoothed SURROGATE — it is not the raw PPG sample**, which lives in `InputSample` in `ppg_analysis.csv` |
| `*_Mvg` | after the moving average. **This is the series the Welch PSD runs on** — the one that decides the reported rate |

Plot `*_Mvg` against time when a window reports `DECLINED` or an implausible
rate: a surrogate that is flat, dominated by drift, or oscillating at a multiple
of the others explains the outcome directly, and the `AM_q`/`BW_q`/`FM_q`
prominence columns in `RR_Data.csv` are the numeric summary of the same thing.

---

## The version stamp

Every CSV carries the version that produced it as the **last field of its header
row**, and the same version is printed in the banner at the top of each run's log:

```
/* ************************************************************************** */
/*                             ppg_analysis 1.1.0                             */
/*                  Prajnaana Technologies, Bengaluru, India                  */
/* ************************************************************************** */
```

It sits on the header line, once, rather than being repeated on every data row:
the version is a property of the whole file, and stamping it 60 000 times would
say nothing extra while inflating the trace. Data rows are unchanged, so a reader
that splits on the header and parses the rest sees exactly the columns documented
above.

The banner is printed **before any option is parsed**, so a run that fails on a
bad argument or a missing file still leaves a log naming its version — those are
the runs most worth tracing.

The version is set in the source (`PPG_ANALYSIS_VERSION` in `ppg_common.h`).
There is deliberately no build-time override: one would let identical sources
report two different versions, which is exactly what this stamp exists to rule
out.

---

## Limits worth knowing before you rely on the numbers

- **The reported heart rate can come out at exactly twice the truth.** This is
  the one to know about, because it is silent: doubling a bradycardic patient
  lands *inside* the expected range, so no warning prints and the row looks
  healthy. If a reported HR is close to twice a rate you would expect for the
  patient, distrust it — and **check `-s` first**, because that is the dominant
  cause. Across 68 short recordings analysed under a single declared category,
  11 of the 58 with a reference read high; re-declaring each to the category its
  rate belongs to removed 9 and broke none. Two remained, inside the correct
  band. Evidence: [`DESIGN.md`](DESIGN.md), "Dicrotic doubling".
- **HRV is indicative, not standard-conformant.** The window is 300 beats — 173
  to 418 s depending on rate — against the 300 s short-term standard, which it
  therefore straddles rather than satisfies.
- **RRV is derived.** Against manual breath intervals it reads systematically
  high, with wide per-recording variation. It has no validated reference and is
  not comparable with a published norm — treat it as a trend within one subject.
  The measured ratio is in [`RESULTS.md`](RESULTS.md).
- **Neonatal RR coverage is roughly half the windows.** The surrogates genuinely
  disagree more at neonatal rates, and the gate declines those windows correctly
  rather than guessing. The measured figure and the evidence that the declines are
  episodic rather than chronic are in [`RESULTS.md`](RESULTS.md).
- **Very slow breathing is hard.** At 6 /min, four harmonics fall inside the
  adult search band.

Full figures are in [`RESULTS.md`](RESULTS.md); the reasoning behind them is in
[`DESIGN.md`](DESIGN.md).

> **This is not a medical device.** Its outputs are not validated for clinical
> decision-making.
