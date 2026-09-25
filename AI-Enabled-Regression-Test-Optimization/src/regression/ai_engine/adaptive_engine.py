# Copyright (c) 2026 Prajnaana Technologies Pvt. Ltd.
# Original author: Dhanya Shree S
# SPDX-License-Identifier: MIT
#
# Licensed under the MIT License. See LICENSE in the project root.

def adaptive_escalation(test_result, current_intensity):
    """Increase intensity if the test failed."""

    if test_result == "pass":
        return current_intensity

    nxt = {"low": "medium", "medium": "high"}.get(current_intensity)

    if nxt is None:
        print("WARNING Failure at max intensity ({}) -- nothing to escalate to".format(
            current_intensity
        ))
        return current_intensity

    print("WARNING Failure detected -> escalating {} -> {}".format(
        current_intensity, nxt
    ))

    return nxt