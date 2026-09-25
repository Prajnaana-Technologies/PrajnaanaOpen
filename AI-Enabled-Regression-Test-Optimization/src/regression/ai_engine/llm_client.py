# Copyright (c) 2026 Prajnaana Technologies Pvt. Ltd.
# Original author: Dhanya Shree S
# SPDX-License-Identifier: MIT
#
# Licensed under the MIT License. See LICENSE in the project root.

"""Anthropic client for the test-planning layer.

One of the project's two model clients -- gemini_client.py is the other --
used by the planner and by the requirement-case generator. It is optional:
nothing here runs unless HA_AI=llm is set, so the default install needs no API
key, no network, and no anthropic package.

Environment variables
---------------------
ANTHROPIC_API_KEY  credential (an `ant auth login` profile also works)
HA_LLM_MODEL       model id      (default: claude-opus-5)
HA_LLM_EFFORT      low | medium | high | xhigh | max  (default: low)
HA_LLM_FALLBACKS   1 to allow a server-side fallback on refusal (default: 1)
"""

import json
import os

DEFAULT_MODEL = "claude-opus-5"
DEFAULT_EFFORT = "low"
FALLBACK_BETA = "server-side-fallback-2026-07-01"

# The request below uses adaptive thinking and structured outputs. An SDK
# older than this rejects them at call time. requirements-llm.txt pins the
# same floor.
MIN_SDK_VERSION = "0.105"


def _version(text):
    """A comparable version tuple from a string like "0.105.2"."""
    parts = []

    for piece in str(text).split("."):
        digits = "".join(c for c in piece if c.isdigit())
        parts.append(int(digits) if digits else 0)

    return tuple(parts)


def model_name():
    return os.getenv("HA_LLM_MODEL", DEFAULT_MODEL)


def _effort():
    return os.getenv("HA_LLM_EFFORT", DEFAULT_EFFORT)


def _fallbacks_enabled():
    return os.getenv("HA_LLM_FALLBACKS", "1") not in ("0", "false", "no")


def availability():
    """Return (ok, reason); the reason names why the LLM path is closed."""
    try:
        import anthropic  # noqa: F401
    except ImportError:
        return False, "the 'anthropic' package is not installed (pip install anthropic)"

    installed = getattr(anthropic, "__version__", "0")

    if _version(installed) < _version(MIN_SDK_VERSION):
        return False, (
            "anthropic {} is too old for this request (needs {} or newer); "
            "pip install -r requirements-llm.txt".format(
                installed, MIN_SDK_VERSION)
        )

    if not (os.getenv("ANTHROPIC_API_KEY") or os.getenv("ANTHROPIC_AUTH_TOKEN")):
        # An `ant auth login` profile also works, so a missing environment
        # variable is not fatal on its own. What settles it is whether the
        # client resolved a credential from somewhere.
        #
        # "Did the constructor raise" is not that question. Anthropic() is
        # happy to be built with nothing and defers the complaint to the
        # first request.
        try:
            client = _client()
        except Exception as exc:  # noqa: BLE001 - availability must not raise
            return False, "Anthropic client could not be built: {}".format(exc)

        if not (getattr(client, "api_key", None)
                or getattr(client, "auth_token", None)):
            return False, "no Anthropic credentials found (set ANTHROPIC_API_KEY)"

    return True, "ready"


def is_available():
    return availability()[0]


def _client():
    import anthropic

    return anthropic.Anthropic()


def _create(client, **kwargs):
    """Send the request, preferring server-side refusal fallbacks when allowed.

    `fallbacks` travels in extra_body. The API accepts it as a request
    field, but it is not a named parameter of the SDK's create() and that
    signature takes no **kwargs, so passing it by name raises TypeError.
    TypeError is not a BadRequestError, so it propagates rather than
    downgrading: the planner would catch it and silently fall back to the
    rules, and HA_AI=llm would never reach the model.
    """
    if _fallbacks_enabled():
        try:
            return client.beta.messages.create(
                betas=[FALLBACK_BETA],
                extra_body={"fallbacks": "default"},
                **kwargs
            )
        except Exception as exc:
            # Accounts without the beta enabled get a 400. The plain call
            # below sends the same parameters without the fallback hint, so
            # downgrade rather than fail the run. TypeError is listed only as
            # a guard: availability() already refuses SDKs older than
            # MIN_SDK_VERSION, which is where a missing parameter would come
            # from.
            if type(exc).__name__ not in ("BadRequestError", "TypeError"):
                raise

    return client.messages.create(**kwargs)


# The planner sends no max_tokens of its own, so this is its ceiling. A
# reasoning model spends its budget thinking before it emits anything, so a
# ceiling as low as 2048 truncates the JSON mid-object and the whole reply
# is discarded as malformed. ai_cases asks for the same headroom explicitly;
# the planner runs on the same models and needs it just as much.
DEFAULT_MAX_TOKENS = 8192


def complete_json(system, prompt, schema, max_tokens=DEFAULT_MAX_TOKENS):
    """One request constrained to return JSON matching schema.

    Raises on any transport, credential or refusal failure -- callers are
    expected to fall back to the rule-based path.
    """
    client = _client()

    response = _create(
        client,
        model=model_name(),
        max_tokens=max_tokens,
        system=system,
        thinking={"type": "adaptive"},
        output_config={
            "effort": _effort(),
            "format": {"type": "json_schema", "schema": schema},
        },
        messages=[{"role": "user", "content": prompt}],
    )

    if response.stop_reason == "refusal":
        detail = getattr(response, "stop_details", None)
        raise RuntimeError(
            "model declined the request ({})".format(
                getattr(detail, "category", "unspecified")
            )
        )

    text = next(b.text for b in response.content if b.type == "text")

    return json.loads(text)
