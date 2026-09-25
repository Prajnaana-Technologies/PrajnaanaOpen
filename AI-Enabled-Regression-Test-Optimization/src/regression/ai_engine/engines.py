# Copyright (c) 2026 Prajnaana Technologies Pvt. Ltd.
# Original author: Dhanya Shree S
# SPDX-License-Identifier: MIT
#
# Licensed under the MIT License. See LICENSE in the project root.

"""Which model plans a regression run, and the seam for adding another.

The proposal describes policy-driven selection across several vendors.
This is the seam that makes that possible: one interface, a registry, a
selection policy, and an adapter per vendor. Two are implemented --
Anthropic and Google. The Anthropic one wraps llm_client; the Gemini one is
stdlib-only, a single POST of a JSON body.

The rest are named and refused rather than hidden, which is the point of
the registry: a name with no adapter behind it should say so, not silently
plan with whichever engine happened to be the default. Registering an adapter
that is not written would be worse still -- it turns a known limitation into
a runtime failure at the moment someone depends on it.

    HA_ENGINE=anthropic     (default)
    HA_ENGINE=gemini        free tier, Generative Language API

Adding an engine is three steps: write a class satisfying the Engine
interface below, register it in ENGINES, and delete its entry from
NOT_IMPLEMENTED. Nothing else in the pipeline changes.
"""

import os

from regression.ai_engine import gemini_client, llm_client

ENGINE_ENV = "HA_ENGINE"

DEFAULT_ENGINE = "anthropic"


class EngineNotAvailable(RuntimeError):
    """The requested engine exists as a name but cannot be used."""


class Engine(object):
    """What the planning layer needs from any model provider.

    Deliberately small. Everything the planner does -- prompt construction,
    schema enforcement, fallback to rules -- stays provider-independent, so a
    second adapter is a thin object and not a second pipeline.
    """

    name = "abstract"
    vendor = "none"

    # The environment variable this engine reads its credential from. The
    # dashboard's key box asks the engine rather than holding its own copy
    # of the mapping.
    key_env = ""

    def available(self):
        """(bool, reason). Never raises: the caller falls back on False."""
        raise NotImplementedError

    def complete_json(self, system, prompt, schema, max_tokens=None):
        """Return a dict matching schema, or raise."""
        raise NotImplementedError

    def model_name(self):
        raise NotImplementedError

    def describe(self):
        ok, reason = self.available()

        return "{} ({}) via {}: {}".format(
            self.name, self.model_name(), self.vendor,
            "ready" if ok else reason,
        )


class AnthropicEngine(Engine):
    """Claude, through the existing llm_client.

    A thin wrapper on purpose -- llm_client already handles credentials,
    schema enforcement and effort, and duplicating that here would create two
    places for the same bug to live.
    """

    name = "anthropic"
    vendor = "Anthropic"
    key_env = "ANTHROPIC_API_KEY"

    def available(self):
        try:
            return llm_client.availability()
        except Exception as exc:  # noqa: BLE001 - availability must never raise
            return False, "availability check failed: {}".format(exc)

    def complete_json(self, system, prompt, schema, max_tokens=None):
        kwargs = {"system": system, "prompt": prompt, "schema": schema}

        if max_tokens is not None:
            kwargs["max_tokens"] = max_tokens

        return llm_client.complete_json(**kwargs)

    def model_name(self):
        try:
            return llm_client.model_name()
        except Exception:  # noqa: BLE001
            return llm_client.DEFAULT_MODEL


class GeminiEngine(Engine):
    """Google Gemini, through the Generative Language API.

    The client translates planner.PLAN_SCHEMA into the OpenAPI subset
    Gemini accepts; see gemini_client._to_response_schema for what that
    drops and why dropping it is safe here.
    """

    name = "gemini"
    vendor = "Google"
    key_env = gemini_client.KEY_ENV

    def available(self):
        try:
            return gemini_client.availability()
        except Exception as exc:  # noqa: BLE001 - availability must never raise
            return False, "availability check failed: {}".format(exc)

    def complete_json(self, system, prompt, schema, max_tokens=None):
        kwargs = {"system": system, "prompt": prompt, "schema": schema}

        if max_tokens is not None:
            kwargs["max_tokens"] = max_tokens

        return gemini_client.complete_json(**kwargs)

    def model_name(self):
        try:
            return gemini_client.model_name()
        except Exception:  # noqa: BLE001
            return gemini_client.DEFAULT_MODEL


# Engines with a working adapter.
ENGINES = {
    AnthropicEngine.name: AnthropicEngine,
    GeminiEngine.name: GeminiEngine,
}

# Named in the proposal, no adapter written. Listed so the error says
# "not implemented" rather than "unknown", which are different problems with
# different fixes.
NOT_IMPLEMENTED = {
    "openai": "GPT-4.x / GPT-4o -- no adapter written",
    "copilot": "GitHub Copilot -- no adapter written",
    "codex": "OpenAI Codex -- deprecated upstream; no adapter written",
}


def available_engines():
    """Names with a working adapter."""
    return sorted(ENGINES)


def policy_name():
    """The engine this run would use, from the selection policy."""
    return (os.getenv(ENGINE_ENV) or DEFAULT_ENGINE).strip().lower()


def get_engine(name=None):
    """Instantiate an engine by name, honouring the policy by default."""
    name = (name or policy_name()).lower()

    if name in ENGINES:
        return ENGINES[name]()

    if name in NOT_IMPLEMENTED:
        raise EngineNotAvailable(
            "engine {!r} is named in the design but has no adapter: {}. "
            "Implemented engines: {}.".format(
                name, NOT_IMPLEMENTED[name], ", ".join(available_engines()))
        )

    raise EngineNotAvailable(
        "unknown engine {!r}. Implemented: {}. Named but unimplemented: "
        "{}.".format(
            name, ", ".join(available_engines()), ", ".join(sorted(NOT_IMPLEMENTED)))
    )


def describe_all():
    """One line per engine, implemented or not. Used by the GUI and logs."""
    lines = []

    for name in available_engines():
        lines.append(ENGINES[name]().describe())

    for name in sorted(NOT_IMPLEMENTED):
        lines.append("{}: not implemented -- {}".format(name, NOT_IMPLEMENTED[name]))

    return lines


def main(argv=None):
    # An argument parser, so --help and a misspelled flag behave as
    # expected.
    import argparse

    parser = argparse.ArgumentParser(
        description="Show which planning engine the policy selects")

    parser.parse_args(argv)

    print("Policy selects:", policy_name())
    print()

    for line in describe_all():
        print(" ", line)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
