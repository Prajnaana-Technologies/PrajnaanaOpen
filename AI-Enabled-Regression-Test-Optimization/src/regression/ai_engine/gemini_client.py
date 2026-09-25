# Copyright (c) 2026 Prajnaana Technologies Pvt. Ltd.
# Original author: Dhanya Shree S
# SPDX-License-Identifier: MIT
#
# Licensed under the MIT License. See LICENSE in the project root.

"""Google Gemini as a model engine, over the Generative Language API.

The second adapter, and the free one. Stdlib only: one POST of a JSON
body, so depending on google's SDK would add a bundled dependency to the
packaged build for no behaviour.

    HA_ENGINE=gemini            select this engine
    GEMINI_API_KEY=...          credential (GOOGLE_API_KEY is also accepted)
    HA_GEMINI_MODEL=...         override the model below

Gemini's wire format is its own: a systemInstruction beside contents rather
than a system message in the list, and generationConfig where llm_client --
the only other client -- puts top-level fields.

The part that actually needs care is the schema. Gemini's responseSchema is
an OpenAPI 3.0 subset, not JSON Schema -- it rejects additionalProperties,
which planner.PLAN_SCHEMA sets to False to keep the model from inventing
fields. Sending the schema unchanged is a 400, so it is translated rather
than passed through, and what the translation drops is stated in
_to_response_schema. planner.RegressionPlan enforces the closed scenario
vocabulary afterwards regardless, which is what makes dropping it safe.
"""

import json
import os
import time
import urllib.error
import urllib.request

KEY_ENV = "GEMINI_API_KEY"
ALT_KEY_ENV = "GOOGLE_API_KEY"
MODEL_ENV = "HA_GEMINI_MODEL"
BASE_ENV = "GEMINI_API_BASE"

# Google retires model aliases on its own schedule. When this one goes the
# request fails with a message naming it, and MODEL_ENV overrides it without
# a code change -- see https://ai.google.dev/gemini-api/docs/models.
DEFAULT_MODEL = "gemini-3.6-flash"

DEFAULT_BASE = "https://generativelanguage.googleapis.com/v1beta"

TIMEOUT_SECONDS = 60

# A 503 means the model is busy and says so. Try again rather than treating
# a busy minute as an outage -- but only a few times, and only for 503.
BUSY_ATTEMPTS = 3
BUSY_BACKOFF_SECONDS = 2.5

# No version suffix: nothing in this project carries a version for a
# trailing "/1.1" to refer to.
USER_AGENT = "AI-Enabled-Regression-Test-Optimization"

REQUIRED_KEYS = ("intensity", "scenarios", "reasoning")

# Keys Gemini's OpenAPI subset understands. Everything else is dropped.
SCHEMA_KEYS = ("type", "format", "description", "nullable", "enum",
               "items", "properties", "required")


def _key():
    return os.getenv(KEY_ENV) or os.getenv(ALT_KEY_ENV) or ""


def model_name():
    return (os.getenv(MODEL_ENV) or DEFAULT_MODEL).strip()


def _base():
    return (os.getenv(BASE_ENV) or DEFAULT_BASE).rstrip("/")


def availability():
    """(bool, reason). Presence of a credential only -- no request is made.

    Same reasoning as llm_client.availability: the dashboard redraws this
    line often, so it must not make a network call. A rejected key shows up
    when a request is actually made, where the caller reports it and falls
    back to its own deterministic path.
    """
    if not _key():
        return False, ("no Gemini credential found (set {}, or enter it in "
                       "the API key box)".format(KEY_ENV))

    return True, "ready"


def _to_response_schema(schema):
    """JSON Schema -> the OpenAPI subset responseSchema accepts.

    Drops additionalProperties, which Gemini rejects outright. That field is
    what stops a model inventing extra keys, so losing it is only acceptable
    because nothing downstream reads an unexpected key: plan_with_llm takes
    three by name and RegressionPlan intersects the scenarios with the known
    vocabulary. Unknown keys are ignored, not trusted.
    """
    if not isinstance(schema, dict):
        return schema

    out = {}

    for key in SCHEMA_KEYS:
        if key not in schema:
            continue

        value = schema[key]

        if key == "properties" and isinstance(value, dict):
            out[key] = {name: _to_response_schema(sub)
                        for name, sub in value.items()}
        elif key == "items":
            out[key] = _to_response_schema(value)
        else:
            out[key] = value

    return out


def _post(model, body):
    """POST once, retrying only while the model reports itself busy.

    Raises on any other failure, and on a 503 that outlasts the attempts.
    """
    for attempt in range(1, BUSY_ATTEMPTS + 1):
        try:
            return _post_once(model, body)
        except urllib.error.HTTPError as exc:
            if exc.code != 503 or attempt == BUSY_ATTEMPTS:
                raise

            wait = BUSY_BACKOFF_SECONDS * attempt

            print("[gemini] busy (503); trying again in {:.0f}s "
                  "({} of {})".format(wait, attempt, BUSY_ATTEMPTS - 1))

            time.sleep(wait)


def _post_once(model, body):
    """One POST, returning the decoded response. Raises on any failure."""
    request = urllib.request.Request(
        "{}/models/{}:generateContent".format(_base(), model),
        data=json.dumps(body).encode("utf-8"),
        headers={
            "x-goog-api-key": _key(),
            "Content-Type": "application/json",
            # Named rather than left as urllib's "Python-urllib/3.x".
            # A content delivery network in front of a model API can
            # reject the default agent with a 403. Naming the application
            # is also the honest thing to send.
            "User-Agent": USER_AGENT,
        },
        method="POST",
    )

    with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response:
        return json.loads(response.read().decode("utf-8"))


def _schema_message(system, schema):
    """Without responseSchema the reply is JSON of any shape. State the shape."""
    return (
        "{}\n\nReply with a single JSON object and nothing else. It must "
        "match this JSON Schema exactly:\n{}".format(
            system, json.dumps(schema, indent=2))
    )


# A reasoning model spends its budget thinking before it emits anything, so
# a ceiling as low as 2048 truncates the JSON mid-object and the whole reply
# is discarded as malformed. ai_cases asks for the same headroom explicitly;
# the planner runs on the same models and needs it just as much.
DEFAULT_MAX_TOKENS = 8192


def complete_json(system, prompt, schema, max_tokens=DEFAULT_MAX_TOKENS):
    """One request constrained to return JSON matching schema.

    Raises on any transport, credential or shape failure -- the caller
    catches that and falls back to its own deterministic path: the rules
    for the planner, the derived cases for ai_cases.
    """
    if not _key():
        raise RuntimeError(
            "{} is not set; cannot call Gemini".format(KEY_ENV))

    model = model_name()

    def body(config):
        return {
            "systemInstruction": {"parts": [{"text": system}]},
            "contents": [{"role": "user", "parts": [{"text": prompt}]}],
            "generationConfig": config,
        }

    strict = {
        "temperature": 0,
        "maxOutputTokens": max_tokens,
        "responseMimeType": "application/json",
        "responseSchema": _to_response_schema(schema),
    }

    try:
        payload = _post(model, body(strict))
    except urllib.error.HTTPError as exc:
        # 400 is what a model that cannot honour responseSchema returns --
        # but it is also what Google returns for a bad key, as
        # API_KEY_INVALID, so the body decides which it is. 429 (free-tier
        # limit) and 5xx are real failures too.
        detail = _body_of(exc)

        if exc.code != 400 or _is_bad_key(detail):
            raise RuntimeError("Gemini request failed ({} {}): {}".format(
                exc.code, exc.reason, detail)) from exc

        loose = {
            "temperature": 0,
            "maxOutputTokens": max_tokens,
            "responseMimeType": "application/json",
        }

        try:
            payload = _post(model, {
                "systemInstruction": {
                    "parts": [{"text": _schema_message(system, schema)}]},
                "contents": [{"role": "user", "parts": [{"text": prompt}]}],
                "generationConfig": loose,
            })
        except urllib.error.HTTPError as retry_exc:
            raise RuntimeError("Gemini request failed ({} {}): {}".format(
                retry_exc.code, retry_exc.reason,
                _body_of(retry_exc))) from retry_exc

    return _plan_from(payload, _required_of(schema))


def _is_bad_key(detail):
    """True when Google's error body says the credential is the problem.

    Matched on the text because the status code does not distinguish it:
    both a rejected key and a schema Gemini cannot honour arrive as 400.
    """
    lowered = (detail or "").lower()

    return ("api_key_invalid" in lowered
            or "api key not valid" in lowered
            or "api_key_service_blocked" in lowered)


def _body_of(exc):
    """The error text Google returns, which names a retired model by name."""
    try:
        return exc.read().decode("utf-8", "replace")[:400]
    except Exception:  # noqa: BLE001 - a failed read must not mask the error
        return "no response body"


def _required_of(schema):
    """The keys this particular request asked for.

    Read from this request's own schema, because complete_json is shared by
    every caller. A fixed constant naming the planner's three keys rejects
    the case generator's {"cases": [...]} as "missing intensity, scenarios,
    reasoning", and the run falls back to the rules -- the model answered
    and nothing used it.
    """
    required = (schema or {}).get("required")

    if isinstance(required, (list, tuple)) and required:
        return tuple(required)

    return REQUIRED_KEYS


def _plan_from(payload, required=None):
    """The reply's JSON object, or a raised error naming what was wrong.

    Shared with the case generator, so the object is a plan only when the
    planner asked for one; the messages therefore say "reply".
    """
    blocked = (payload.get("promptFeedback") or {}).get("blockReason")

    if blocked:
        raise RuntimeError("Gemini blocked the prompt ({})".format(blocked))

    candidates = payload.get("candidates") or []

    if not candidates:
        raise RuntimeError("Gemini returned no candidates: {}".format(
            json.dumps(payload)[:300]))

    candidate = candidates[0]
    finish = candidate.get("finishReason")

    if finish == "MAX_TOKENS":
        raise RuntimeError(
            "Gemini stopped at the token limit, so the JSON is truncated")

    if finish and finish not in ("STOP", "FINISH_REASON_UNSPECIFIED"):
        raise RuntimeError("Gemini stopped early ({})".format(finish))

    parts = (candidate.get("content") or {}).get("parts") or []
    text = "".join(part.get("text", "") for part in parts)

    try:
        data = json.loads(text)
    except ValueError as exc:
        raise RuntimeError(
            "Gemini did not return JSON: {!r}".format(text[:200])) from exc

    if not isinstance(data, dict):
        raise RuntimeError(
            "Gemini returned {}, not a JSON object".format(type(data).__name__))

    missing = [key for key in (required or REQUIRED_KEYS)
               if key not in data]

    if missing:
        raise RuntimeError(
            "Gemini's reply is missing {}: {}".format(
                ", ".join(missing), json.dumps(data)[:200]))

    return data
