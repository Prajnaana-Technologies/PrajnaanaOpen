# Copyright (c) 2026 Prajnaana Technologies Pvt. Ltd.
# Original author: Dhanya Shree S
# SPDX-License-Identifier: MIT
#
# Licensed under the MIT License. See LICENSE in the project root.

"""Supply current from an external power analyzer.

Why this is not firmware telemetry: a board cannot measure its own supply
current, because the measuring circuit is powered by the thing it is trying
to measure. Current has to come from an instrument in series with the supply
-- a Nordic PPK II on this bench.

So there is no automatic path. The instrument produces a reading, and this
module is where that reading enters the pipeline:

    HA_POWER_MA=41.2                    a single figure you measured
    HA_POWER_CSV=path/to/ppk2_export.csv   an exported capture, averaged

With neither set, power stays unmeasured. That is the correct outcome -- the
risk engine scores an unmeasured metric as risk, and inventing a plausible
number here is exactly the habit that made every build score alike before.

    python -m regression.instrumentation.power_analyzer
"""

import csv
import os
import statistics

POWER_MA_ENV = "HA_POWER_MA"
POWER_CSV_ENV = "HA_POWER_CSV"

# Column names a PPK II export may use for current, in preference order.
# The tool has changed its header across versions, so match rather than assume.
CURRENT_COLUMNS = (
    "current(ua)",
    "current (ua)",
    "current_ua",
    "current(ma)",
    "current (ma)",
    "current_ma",
    "current",
)

# Samples below this are the instrument's noise floor rather than the device.
NOISE_FLOOR_UA = 1.0


class PowerReadError(ValueError):
    """The configured power source could not be read."""


def _column_scale(name):
    """Multiplier converting a column's unit to milliamps."""
    return 0.001 if "ua" in name.replace(" ", "").replace("_", "") else 1.0


def read_csv_average(path):
    """Mean current in mA from an exported capture.

    Averages rather than takes a peak: a regression in idle draw is a shift
    in the average, and a peak is dominated by radio bursts that say more
    about the test than about the firmware.
    """
    if not os.path.exists(path):
        raise PowerReadError("no such capture: {}".format(path))

    with open(path, newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle)

        if not reader.fieldnames:
            raise PowerReadError("{} has no header row".format(path))

        lookup = {name.strip().lower(): name for name in reader.fieldnames}

        column = None

        for candidate in CURRENT_COLUMNS:
            if candidate in lookup:
                column = lookup[candidate]
                break

        if column is None:
            raise PowerReadError(
                "no current column in {} (found: {})".format(
                    path, ", ".join(reader.fieldnames))
            )

        scale = _column_scale(column.lower())

        values = []

        for row in reader:
            raw = (row.get(column) or "").strip()

            if not raw:
                continue

            try:
                value = float(raw)
            except ValueError:
                continue

            if scale == 0.001 and value < NOISE_FLOOR_UA:
                continue

            values.append(value * scale)

    if not values:
        raise PowerReadError("{} contains no usable samples".format(path))

    return round(statistics.fmean(values), 3)


def read_power_ma():
    """Supply current in mA, or None when no instrument reading is configured.

    None is a valid, expected answer. Callers must treat it as unmeasured
    rather than substituting a default.
    """
    direct = os.getenv(POWER_MA_ENV)

    if direct:
        try:
            return float(direct)
        except ValueError:
            raise PowerReadError(
                "{}={!r} is not a number".format(POWER_MA_ENV, direct)
            )

    capture = os.getenv(POWER_CSV_ENV)

    if capture:
        return read_csv_average(capture)

    return None


def merge_into(metrics):
    """Add a measured power reading to a metrics dict, in place.

    Removes "power" from the unmeasured list when a reading is supplied, so
    the risk engine scores it as evidence rather than as a gap.
    """
    try:
        value = read_power_ma()
    except PowerReadError as exc:
        print("Power analyzer:", exc)
        return metrics

    if value is None:
        return metrics

    metrics["power"] = value

    unmeasured = metrics.get("unmeasured")

    if isinstance(unmeasured, list) and "power" in unmeasured:
        unmeasured.remove("power")

    print("Power analyzer: {:.3f} mA (external instrument)".format(value))

    return metrics


def source_description():
    """Where a reading would come from right now, for logs and the GUI."""
    if os.getenv(POWER_MA_ENV):
        return "fixed value ({}={})".format(POWER_MA_ENV, os.getenv(POWER_MA_ENV))

    if os.getenv(POWER_CSV_ENV):
        return "capture ({}={})".format(POWER_CSV_ENV, os.getenv(POWER_CSV_ENV))

    return "none configured; power will be unmeasured"


def main(argv=None):
    # An argument parser, so that --help prints help and exits 0.
    import argparse

    parser = argparse.ArgumentParser(
        description="Report the configured power source and its reading")

    parser.parse_args(argv)

    print("Power source:", source_description())

    try:
        value = read_power_ma()
    except PowerReadError as exc:
        print("Error:", exc)
        return 1

    if value is None:
        print("No reading. Set {} or {} to supply one.".format(
            POWER_MA_ENV, POWER_CSV_ENV))
        return 1

    print("Current: {:.3f} mA".format(value))

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
