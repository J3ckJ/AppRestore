"""Observe normal, authorized license requests without keeping private output."""

from __future__ import annotations

import json
import re

from .download_probe import positive_id

OUTCOMES = frozenset({
    "granted", "already_owned", "refused", "uncertain",
    "authentication_required", "subscription_required",
})


def purchase_diagnostics(output: str, store_id: str) -> dict[str, object]:
    expected = positive_id(store_id)
    attempts: list[dict[str, object]] = []
    for line in output.splitlines():
        try:
            row = json.loads(line)
            if not isinstance(row, dict) or "purchaseAttempt" not in row or positive_id(row.get("appId")) != expected:
                continue
        except (ValueError, TypeError):
            continue
        raw = row["purchaseAttempt"]
        if not isinstance(raw, dict):
            continue
        pricing, outcome = raw.get("pricingParameters"), raw.get("outcome")
        if not isinstance(pricing, str) or pricing not in {"STDQ", "GAME"}:
            continue
        if not isinstance(outcome, str) or outcome not in OUTCOMES:
            outcome = "uncertain"
        clean: dict[str, object] = {"pricingParameters": pricing, "outcome": outcome}
        status, failure = raw.get("httpStatus"), raw.get("failureType")
        if type(status) is int and 100 <= status <= 599:
            clean["httpStatus"] = status
        if isinstance(failure, str) and re.fullmatch(r"-?[0-9]{1,10}", failure):
            clean["failureType"] = failure
        # Success requires a normal HTTP response, not an ambiguous 500.
        if outcome == "granted" and (status != 200 or failure):
            clean["outcome"] = "uncertain"
        attempts.append(clean)
        if len(attempts) >= 8:
            break
    return {"appId": expected, "attempts": attempts}
