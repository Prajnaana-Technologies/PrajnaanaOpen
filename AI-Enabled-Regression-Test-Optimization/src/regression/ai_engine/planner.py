# Copyright (c) 2026 Prajnaana Technologies Pvt. Ltd.
# Original author: Dhanya Shree S
# SPDX-License-Identifier: MIT
#
# Licensed under the MIT License. See LICENSE in the project root.

"""Decides how hard to test and which scenarios to run.

Two interchangeable implementations behind one function:

    HA_AI=rules   threshold rules (default) -- offline, free, deterministic
    HA_AI=llm     the selected engine reads the metrics and the change and
                  picks a plan (engines.py; Anthropic or Gemini)

The rules path is the default on purpose: a fresh clone, and CI, must work
with no API key and no network. HA_AI is the whole switch -- it gates the
model-written requirement cases in ai_cases.py as well as this -- so the LLM
path is strictly opt-in, and any failure in it degrades back to the rules
rather than failing the run.
"""

import json
import os
from dataclasses import dataclass, field

from regression.change_detection.risk_engine import METRIC_TESTS, METRIC_THRESHOLDS

# Every test name the risk engine can flag. A full regression passes
# risk_tests=None, meaning nothing was narrowed away, so it has to expand to
# all of them rather than to none. See suggest_scenarios.
ALL_RISK_TESTS = sorted({name for names in METRIC_TESTS.values()
                         for name in names})

MODE_ENV = "HA_AI"
RULES = "rules"
LLM = "llm"
MODES = (RULES, LLM)

INTENSITIES = ("low", "medium", "high")

INTENSITY_PACKETS = {"low": 20, "medium": 50, "high": 80}
MAX_PACKETS = 80

# The only scenarios that have a test to emit. The templates live in
# scenario_tests.SCENARIO_TESTS, which generate_tests imports and writes
# out; the model is constrained to this same vocabulary so it cannot invent
# a scenario that silently does nothing.
#
# Every name here must have a template there. A scenario in the vocabulary
# that generates nothing is worse than one that is missing: the plan claims
# coverage the run does not deliver. There is a test that holds the two
# lists in step.
KNOWN_SCENARIOS = (
    "memory_pressure",
    "power_stress",
    "long_duration_stability",
    "stress_stability",
    "reconnect_stress",
    "fault_link_drop",
    "concurrency_stream_and_read",
    "pairing_bonding",
)

PLAN_SCHEMA = {
    "type": "object",
    "properties": {
        "intensity": {"type": "string", "enum": list(INTENSITIES)},
        "scenarios": {
            "type": "array",
            "items": {"type": "string", "enum": list(KNOWN_SCENARIOS)},
        },
        "reasoning": {"type": "string"},
    },
    "required": ["intensity", "scenarios", "reasoning"],
    "additionalProperties": False,
}

# The limits quoted to the model are the risk engine's, read at import, so
# the prompt cannot drift from the thresholds the rest of the run applies.
SYSTEM_PROMPT = """\
You select regression tests for embedded firmware on a hardware bench. The \
target in this proof of concept is an nRF52840 running Zephyr firmware whose \
audio compressor stands in for a hearing aid, reached over Bluetooth LE.

Running the whole suite on every build is too slow, so you choose the smallest \
set of tests that would actually catch a regression in the change described.

The metrics, and the limit the risk engine applies to each:
- power: supply current in mA while streaming; over {power} mA is a battery \
regression. The board cannot measure it without an external analyzer, so it \
is often unmeasured.
- memory: system work-queue stack high-water in KiB (not heap; the firmware \
never allocates at run time). The stack is 4 KiB; over {memory} KiB it is \
close to overflowing.
- sync: binaural sync error in ms between a left and a right device; over \
{sync} ms is audible. Unmeasured on a one-board bench.
- retry: link drops during this run that the host did not ask for; a healthy \
board reports 0, and over {retry} means an unstable link.

An unmeasured metric is unknown: it is neither a breach nor a healthy reading.

Intensity sets how many back-to-back streams the stress scenarios run and how \
many reconnect cycles they make. Prefer "low" unless the metrics, the change \
or recent failures justify more; every step up costs bench time. Choose only \
scenarios that the evidence supports, and say briefly why in the reasoning \
field.""".format(**METRIC_THRESHOLDS)


# Whitespace a model reaches for that a console may not be able to encode.
# A narrow no-break space in a sentence meant for a person carries nothing
# the ordinary space does not.
EXOTIC_SPACES = {
    "\u00a0": " ",      # no-break space
    "\u2007": " ",      # figure space
    "\u2009": " ",      # thin space
    "\u202f": " ",      # narrow no-break space
    "\u2060": "",       # word joiner
    "\ufeff": "",       # zero-width no-break space
    "\u200b": "",       # zero-width space
    "\u2011": "-",      # non-breaking hyphen
    "\u2013": "-",      # en dash
    "\u2014": "--",     # em dash
    "\u2018": "'", "\u2019": "'",
    "\u201c": '"', "\u201d": '"',
}


def plain_text(text):
    """A model's prose with the characters a console cannot print removed.

    Applied to every plan, from the rules as well as a model, so there is
    one answer rather than one per engine.
    """
    if not text:
        return text

    for exotic, plain in EXOTIC_SPACES.items():
        text = text.replace(exotic, plain)

    return text


@dataclass
class RegressionPlan:
    """What to generate. Produced by either the rules or the LLM."""

    intensity: str
    scenarios: list
    reasoning: str
    source: str
    packets: int = 0
    params: dict = field(default_factory=dict)
    # Filled in by generate_tests: how many tests this plan generated
    # against how many the full vocabulary would have, and which were left
    # out. This is what the test_reduction KPI measures.
    selection: dict = field(default_factory=dict)

    def __post_init__(self):
        if self.intensity not in INTENSITIES:
            raise ValueError("bad intensity: {!r}".format(self.intensity))

        self.reasoning = plain_text(self.reasoning)

        self.scenarios = sorted(set(self.scenarios) & set(KNOWN_SCENARIOS))

        # enforce_safe_limits: a generated test can never overload the device.
        self.packets = min(INTENSITY_PACKETS[self.intensity], MAX_PACKETS)
        self.params = {"packets": self.packets}


def get_mode(mode=None):
    """Resolve the planning mode from the argument, then the environment."""
    name = (mode or os.getenv(MODE_ENV) or RULES).strip().lower()

    if name not in MODES:
        raise ValueError(
            "Unknown {}={!r}. Expected one of: {}".format(
                MODE_ENV, name, ", ".join(MODES)
            )
        )

    return name


# --------------------------------------------------------------------------
# Rule-based planner (default)
# --------------------------------------------------------------------------

def measured(metrics, name):
    """The metric's value, or None when the device did not report it.

    metrics.get(name, 0) is wrong here: an unmeasured metric would read as 0,
    which is indistinguishable from a healthy device and quietly lowers the
    intensity. read_metrics() names what it could not measure under
    "unmeasured"; honour that.
    """
    if name in (metrics.get("unmeasured") or ()):
        return None

    return metrics.get(name)


def breaches(metrics, name):
    """True when the device reported the metric and it is over the limit.

    The limit is the risk engine's, so the planner and the risk score agree
    on what counts as a breach.
    """
    value = measured(metrics, name)

    return value is not None and value > METRIC_THRESHOLDS[name]


def decide_intensity(metrics, change_info):
    change_info = str(change_info).lower()

    if "bluetooth" in change_info:
        return "high"

    if breaches(metrics, "power"):
        return "high"

    if breaches(metrics, "memory"):
        return "medium"

    # An unmeasured metric cannot clear a threshold, so it raises the
    # intensity -- but only one step. The risk engine has already scored it
    # as risk and widened the slice.
    if measured(metrics, "power") is None or measured(metrics, "memory") is None:
        return "medium"

    return "low"


# Words in the change description that make a scenario worth running. A
# scenario reachable only through the LLM path is effectively unreachable:
# the rules planner is the default, so it must be able to select every
# scenario in the vocabulary. tests/test_scenarios.py checks that it can.
#
# Wide enough for prose, not only for the subsystem phrases firmware_diff
# emits ("audio dsp", "bluetooth link"). A release note is written by a
# person, who says "removed the knee from the WDRC compressor" rather than
# "audio dsp". The domain terms below are what makes a release note usable
# as an input.
WORD_CHARS = "abcdefghijklmnopqrstuvwxyz0123456789_"

CHANGE_TRIGGERS = {
    "pairing_bonding": (
        "pair", "bond", "security", "encrypt", "smp", "auth", "key",
    ),
    "fault_link_drop": (
        "bluetooth", "connection", "reconnect", "disconnect", "link",
        "suspend", "resume", "gatt", "characteristic", "advertis", "notif",
    ),
    "concurrency_stream_and_read": (
        "concurren", "thread", "schedul", "timing", "interrupt", "workqueue",
        "bluetooth", "audio", "dsp", "wdrc", "compress", "codec", "gain",
    ),
    "long_duration_stability": (
        # Not a bare "long": free prose says "no longer" far more often than
        # it says "long run", and a bare "long" selects an endurance
        # scenario for every one of them.
        "leak", "lifecycle", "long-duration", "long duration", "longevity",
        "soak", "endurance",
    ),
    "stress_stability": (
        "throughput", "buffer", "stress", "overflow", "ring",
    ),
    "power_stress": (
        "power", "battery", "current", "sleep", "idle",
    ),
    "memory_pressure": (
        "memory", "heap", "stack", "alloc",
    ),
}


def _mentions(text, word):
    """True when `word` starts a word in `text`.

    Anchored at a word boundary rather than found anywhere, because a plain
    substring search reads "during" as "ring" and "belong" as "long". That
    is tolerable for a generated phrase like "audio dsp"; against a release
    note written in English it selects scenarios nobody asked for.

    Still a prefix match at that boundary, so "schedul" catches scheduler
    and scheduling, and "advertis" catches advertising.
    """
    start = 0

    while True:
        found = text.find(word, start)

        if found < 0:
            return False

        if found == 0 or text[found - 1] not in WORD_CHARS:
            return True

        start = found + 1


def triggered_by_change(change_info):
    """Scenarios whose keywords appear in the change description."""
    text = str(change_info).lower()

    return [
        scenario for scenario, words in CHANGE_TRIGGERS.items()
        if any(_mentions(text, word) for word in words)
    ]


def suggest_scenarios(change_info, metrics, risk_tests=None, risk_score=0):
    """The scenarios one plan runs.

    risk_tests is the flagged list from the risk engine: the flagged names
    for a targeted slice, ["connection"] for a minimal one, and None for a
    full regression, where nothing was narrowed away. None therefore means
    every flagged test, not no flagged test.

    Reading None as "none flagged" would give a score-6 run fewer scenarios
    than a score-3 run -- three where a score of 3 gives six -- because the
    flagged block below is the widest route into the scenario list.
    Everything it can add is also reachable another way: reconnect_stress
    through risk_score >= 3, the rest through CHANGE_TRIGGERS. An empty list
    still means none: callers that know nothing was flagged pass [].
    """
    flagged = ALL_RISK_TESTS if risk_tests is None else risk_tests

    scenarios = []

    if measured(metrics, "memory") is None or breaches(metrics, "memory"):
        scenarios.append("memory_pressure")

    if measured(metrics, "power") is None or breaches(metrics, "power"):
        scenarios.append("power_stress")

    if flagged:
        if "memory_leak" in flagged:
            scenarios.append("long_duration_stability")

        if "stability" in flagged:
            scenarios.append("stress_stability")
            scenarios.append("reconnect_stress")

        # A flagged connection or retry means the link itself is suspect, so
        # exercise losing it mid-transfer rather than only cycling it cleanly.
        if "connection" in flagged or "retry" in flagged:
            scenarios.append("fault_link_drop")

        # Anything touching the link is also a candidate for the interaction
        # class: audio and telemetry competing for the same radio time.
        if "connection" in flagged or "sync" in flagged:
            scenarios.append("concurrency_stream_and_read")

    if risk_score >= 3:
        scenarios.append("reconnect_stress")

    # What the change says it touched. This is the only route to the security
    # scenarios -- no device metric indicates that pairing code changed.
    scenarios.extend(triggered_by_change(change_info))

    return sorted(set(scenarios))


def plan_with_rules(metrics, change_info, risk_tests=None, risk_score=0):
    intensity = decide_intensity(metrics, change_info)
    scenarios = suggest_scenarios(change_info, metrics, risk_tests, risk_score)

    return RegressionPlan(
        intensity=intensity,
        scenarios=scenarios,
        reasoning="the risk engine's power and memory limits, plus the "
                  "words in the change description (an unmeasured power or "
                  "memory reading raises the intensity one step)",
        source=RULES,
    )


# --------------------------------------------------------------------------
# LLM planner (opt in with HA_AI=llm)
# --------------------------------------------------------------------------

def _flagged_for_prompt(risk_tests):
    """How the flagged list reads in the prompt.

    None is a full regression -- nothing was narrowed away -- so it means
    every flagged test, the same as in suggest_scenarios. An empty list is
    a caller saying nothing was flagged, and says so.
    """
    if risk_tests is None:
        return "{} (full regression: nothing was excluded)".format(
            sorted(ALL_RISK_TESTS))

    return sorted(risk_tests) if risk_tests else "none"


def build_prompt(metrics, change_info, risk_tests=None, risk_score=0, history=None):
    parts = [
        "Device metrics from this build. A metric listed under \"unmeasured\" "
        "was not reported by the device: treat it as unknown, never as good.",
        json.dumps(metrics, indent=2, sort_keys=True),
        "",
        "Firmware change under test:",
        str(change_info),
        "",
        # Same reading of None as suggest_scenarios: nothing was narrowed
        # away, so every flagged test applies.
        "Risk engine output: score {}, flagged tests {}".format(
            risk_score, _flagged_for_prompt(risk_tests)
        ),
    ]

    if history:
        parts += [
            "",
            "Recent runs, oldest first. A test that failed recently is "
            "evidence for keeping its area in the plan:",
            json.dumps(history[-5:], indent=2, sort_keys=True),
        ]

    parts += [
        "",
        "Choose the regression intensity and the scenarios to generate.",
    ]

    return "\n".join(parts)


def plan_with_llm(metrics, change_info, risk_tests=None, risk_score=0,
                  history=None, engine=None):
    """Ask the selected engine for a plan.

    The request goes through the engine chosen by HA_ENGINE, not straight to
    the Anthropic client. Calling llm_client directly would leave the
    registry, the selection policy and the GUI's engine box deciding
    nothing: HA_ENGINE=openai would be accepted from the command line and
    the run planned by the Anthropic engine anyway. engines.get_engine
    refuses a name it has no adapter for, which is the behaviour the
    registry exists to provide.
    """
    from regression.ai_engine import engines

    engine = engine or engines.get_engine()

    prompt = build_prompt(metrics, change_info, risk_tests, risk_score, history)

    data = engine.complete_json(
        system=SYSTEM_PROMPT,
        prompt=prompt,
        schema=PLAN_SCHEMA,
    )

    return RegressionPlan(
        intensity=data["intensity"],
        scenarios=data["scenarios"],
        reasoning=data["reasoning"],
        source=LLM,
    )


# --------------------------------------------------------------------------
# Entry point
# --------------------------------------------------------------------------

def plan_regression(metrics, change_info, risk_tests=None, risk_score=0,
                    history=None, mode=None):
    """Return a RegressionPlan, degrading to the rules if the LLM cannot run."""
    resolved = get_mode(mode)

    if resolved == RULES:
        return plan_with_rules(metrics, change_info, risk_tests, risk_score)

    from regression.ai_engine import engines

    try:
        engine = engines.get_engine()
    except engines.EngineNotAvailable as exc:
        # A name with no adapter is a different problem from a missing key,
        # and saying so beats planning with a model nobody asked for.
        print("[planner] {}={} -- {} -- using rules".format(
            engines.ENGINE_ENV, engines.policy_name(), exc
        ))
        return plan_with_rules(metrics, change_info, risk_tests, risk_score)

    ok, reason = engine.available()

    if not ok:
        print("[planner] {}=llm requested but {} -- using rules".format(
            MODE_ENV, reason
        ))
        return plan_with_rules(metrics, change_info, risk_tests, risk_score)

    try:
        plan = plan_with_llm(metrics, change_info, risk_tests, risk_score,
                             history, engine=engine)

        print("[planner] {} ({}) chose {} {}".format(
            engine.name, engine.model_name(), plan.intensity,
            plan.scenarios or "[]"
        ))

        return plan

    except Exception as exc:
        print("[planner] LLM planning failed ({}: {}) -- using rules".format(
            type(exc).__name__, exc
        ))
        return plan_with_rules(metrics, change_info, risk_tests, risk_score)
