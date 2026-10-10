"""Thin adapter over Макс's ``license_guard``: one extra operation.

``license_guard`` (copied unchanged from maks-share) does the price/limit
check and writes journal lines; its ``ACQUIRED_STATUSES`` counts
``acquired``, ``acquired_download_failed`` and ``purchase_uncertain``.

The gate writes ``acquired`` right after a successful ``ipatool purchase``
and, if the download that follows fails, turns that same line into
``acquired_download_failed``. ``license_guard`` has no "update a line"
function, so ``update_status`` here rewrites that one line in place (atomic
replace), unless ``license_guard`` grows its own ``update_status``.
"""

from __future__ import annotations

import json
import os
import tempfile
import threading
from pathlib import Path
from typing import Any

from . import license_guard
from .license_guard import Verdict, check_can_acquire, read_counts

__all__ = [
    "ACQUIRED",
    "ACQUIRED_DOWNLOAD_FAILED",
    "PURCHASE_UNCERTAIN",
    "Verdict",
    "check_can_acquire",
    "read_counts",
    "record",
    "update_status",
]

ACQUIRED = "acquired"
ACQUIRED_DOWNLOAD_FAILED = "acquired_download_failed"
# purchase answered with a network error/timeout: Apple may have recorded it.
PURCHASE_UNCERTAIN = "purchase_uncertain"

_LOCK = threading.Lock()


def record(
    track_id: str,
    *,
    bundle_id: str = "",
    storefront: str = "",
    price: float | None = None,
    mode: str | None = "gui",
    status: str = ACQUIRED,
    journal_path: Path,
) -> dict[str, Any]:
    """``license_guard.record_acquire`` under the same lock as ``update_status``."""

    with _LOCK:
        entry = license_guard.record_acquire(
            track_id,
            bundle_id=bundle_id,
            storefront=storefront,
            price=price,
            mode=mode,
            status=status,
            journal_path=journal_path,
        )
    try:
        os.chmod(journal_path, 0o600)
    except OSError:
        pass
    return entry


def update_status(entry: dict[str, Any], status: str, *, journal_path: Path) -> bool:
    """Set ``status`` on the journal line ``entry`` (matched by time + track_id)."""

    native = getattr(license_guard, "update_status", None)
    if callable(native):
        return bool(native(entry, status, journal_path=journal_path))
    path = Path(journal_path)
    with _LOCK:
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except FileNotFoundError:
            return False
        key = (str(entry.get("time", "")), str(entry.get("track_id", "")))
        for index in range(len(lines) - 1, -1, -1):
            try:
                item = json.loads(lines[index])
            except json.JSONDecodeError:
                continue
            if not isinstance(item, dict):
                continue
            if (str(item.get("time", "")), str(item.get("track_id", ""))) != key:
                continue
            item["status"] = status
            lines[index] = json.dumps(item, ensure_ascii=False)
            handle, temporary = tempfile.mkstemp(prefix=".licenses-", dir=str(path.parent))
            try:
                with os.fdopen(handle, "w", encoding="utf-8") as stream:
                    stream.write("\n".join(lines) + "\n")
                os.chmod(temporary, 0o600)
                os.replace(temporary, path)
            except BaseException:
                try:
                    os.unlink(temporary)
                except OSError:
                    pass
                raise
            return True
        return False
