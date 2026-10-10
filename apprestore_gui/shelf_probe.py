"""Ask Apple whether one store id can be downloaded, then stop.

A running download is killed at the first percent so the IPA is not kept
and nothing is installed on the iPhone. ``--purchase`` is used only after
Apple says a license is missing: that is the only way to learn whether
Apple will issue one, and a successful answer can add the license.
"""

from __future__ import annotations

import re
import shutil
import tempfile
from pathlib import Path

from apprestore_core.tools import AppRestoreTools, ToolUnavailable

_PERCENT = re.compile(r"downloading\s+[1-9]\d*\s*%")


def classify_offer(text: str) -> str:
    """One word for an ipatool transcript. ``pending`` means keep waiting."""

    low = (text or "").casefold()
    if "passphrase is required" in low or "handle is invalid" in low or "not authenticated" in low:
        return "closed"
    if "failed to purchase" in low:
        return "refused"
    if "temporarily unavailable" in low or "unavailable in this region" in low:
        return "unavailable"
    if "license is required" in low or "license not found" in low:
        return "needs-license"
    if _PERCENT.search(low):
        return "offers"
    if "err error" in low or "success=false" in low:
        return "error"
    return "pending"


def probe_store(tools: AppRestoreTools, store_id: str) -> str:
    """``licensed``, ``granted``, ``refused``, ``unavailable``, ``closed``, or ``error``."""

    kind = _attempt(tools, store_id, purchase=False)
    if kind != "needs-license":
        return "licensed" if kind == "offers" else kind
    bought = _attempt(tools, store_id, purchase=True)
    if bought == "offers":
        return "granted"
    if bought == "needs-license":
        return "refused"
    return bought


def _attempt(tools: AppRestoreTools, store_id: str, *, purchase: bool) -> str:
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
        args = tools._ipatool_cmd("download", "--app-id", store_id, "--output", str(output))
        if purchase:
            args.append("--purchase")
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
