# Copyright (c) 2026 Prajnaana Technologies Pvt. Ltd.
# Original author: Dhanya Shree S
# SPDX-License-Identifier: MIT
#
# Licensed under the MIT License. See LICENSE in the project root.

"""Make the host suite independent of the machine it runs on.

The planning tests stub llm_client and assert which planner ran, so the
ambient settings a real setup leaves lying about are cleared before every
test:

  * HA_ENGINE picks the adapter engines.get_engine() returns.
  * GEMINI_API_KEY and ANTHROPIC_API_KEY decide availability().
  * HA_AI picks the planner.
  * HA_CASE_SOURCE and HA_AI_CASE_LIMIT steer who writes the requirement
    cases and how many are kept.

Anything that needs one of these variables sets it itself with monkeypatch,
which still works -- this only clears the ambient value first.
"""

import os

import pytest

# Every variable that steers planning, generation or credentials. A test
# that wants one sets it explicitly.
NEUTRALISED = (
    "HA_AI",
    "HA_CASE_SOURCE",
    "HA_AI_CASE_LIMIT",
    "HA_ENGINE",
    "HA_LLM_MODEL",
    "HA_LLM_EFFORT",
    "HA_LLM_FALLBACKS",
    "ANTHROPIC_API_KEY",
    "ANTHROPIC_AUTH_TOKEN",
    "GEMINI_API_KEY",
    "GOOGLE_API_KEY",
    "HA_GEMINI_MODEL",
    "GEMINI_API_BASE",
    "HA_BUILD_ID",
    "HA_REQUIREMENTS",
    "HA_RELEASE_NOTE",
    "HA_REQUIRE_APPROVAL",
    "HA_REQUIRE_TELEMETRY",
)


@pytest.fixture(autouse=True)
def _clean_environment(monkeypatch):
    """Clear the steering variables before every test in this directory."""
    for name in NEUTRALISED:
        monkeypatch.delenv(name, raising=False)


def pytest_report_header(config):
    present = [name for name in NEUTRALISED if os.getenv(name)]

    if present:
        return "neutralised for these tests: " + ", ".join(present)

    return None
