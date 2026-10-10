"""Read-only check: is one store id already on this Apple ID?

A running ``ipatool download`` (never with ``--purchase``) is killed at the
first percent so the IPA is not kept and nothing is installed. When Apple
answers "license is required", the app is simply not on this Apple ID: the
probe reports ``not-owned`` and never tries to get a license (LEGAL.md §2.1,
R2/R4). Taking a license is a separate, user-confirmed action guarded by
``apprestore_core.license_guard``.

Outcomes: ``licensed``, ``not-owned``, ``unavailable``, ``closed``, ``error``.
"""

from __future__ import annotations

import re
import shutil
import tempfile
from pathlib import Path

from apprestore_core.tools import AppRestoreTools, ToolUnavailable

_PERCENT = re.compile(r"downloading\s+[1-9]\d*\s*%")

PROBE_OUTCOMES = ("licensed", "not-owned", "unavailable", "closed", "error")


def classify_offer(text: str) -> str:
    """One word for an ipatool transcript. ``pending`` means keep waiting."""

    low = (text or "").casefold()
    if "passphrase is required" in low or "handle is invalid" in low or "not authenticated" in low:
        return "closed"
    if "temporarily unavailable" in low or "unavailable in this region" in low:
        return "unavailable"
    if "license is required" in low or "license not found" in low:
        return "not-owned"
    if _PERCENT.search(low):
        return "offers"
    if "err error" in low or "success=false" in low:
        return "error"
    return "pending"


def probe_store(tools: AppRestoreTools, store_id: str) -> str:
    """One of ``PROBE_OUTCOMES``. Never changes the Apple ID."""

    kind = _attempt(tools, store_id)
    if kind == "offers":
        return "licensed"
    return kind if kind in PROBE_OUTCOMES else "error"


def probe_args(tools: AppRestoreTools, store_id: str, output: Path) -> list[str]:
    """The only ipatool command line the probe ever runs."""

    args = tools._ipatool_cmd("download", "--app-id", store_id, "--output", str(output))
    if "--purchase" in args:  # pragma: no cover - guard against future edits
        raise AssertionError("shelf probe must never carry --purchase")
    return args


def _attempt(tools: AppRestoreTools, store_id: str) -> str:
    runner = tools.runner
    directory = Path(tempfile.mkdtemp(prefix="apprestore-probe-"))
    output = directory / "download.ipa"
    previous_output = getattr(runner, "on_output", None)
    previous_stop = getattr(runner, "stop_when", None)
    parts: list[str] = []

    def on_output(text: str) -> None:
        parts.append(text)

    def stop_when(transcript: str) -> bool:
        return classify_offer(transcript) != "pending"

    if hasattr(runner, "on_output"):
        runner.on_output = on_output
    if hasattr(runner, "stop_when"):
        runner.stop_when = stop_when
    try:
        args = probe_args(tools, store_id, output)
        try:
            result = runner.run(
                args,
                capture=True,
                timeout=45,
                env=tools._ipatool_env(),
            )
        except (ToolUnavailable, OSError) as exc:
            return classify_offer(str(exc))
        text = f"{result.stderr}\n{result.stdout}\n{''.join(parts)}"
        kind = classify_offer(text)
        return "error" if kind == "pending" else kind
    finally:
        if hasattr(runner, "on_output"):
            runner.on_output = previous_output
        if hasattr(runner, "stop_when"):
            runner.stop_when = previous_stop
        shutil.rmtree(directory, ignore_errors=True)
