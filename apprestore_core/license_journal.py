"""Thin adapter over Макс's ``license_guard`` for the GUI journal.

Statuses: ``acquired`` (written right after a successful ``ipatool purchase``)
and ``acquired_download_failed`` (the same line, updated when the download that
follows fails). Both count toward the limit: the transaction already happened.

Line format (``track_id`` is the key; ``price``/``mode``/``status`` optional,
as agreed with Макс)::

    {"time": "...+00:00", "track_id": "123", "bundle_id": "com.x",
     "storefront": "ru", "price": 0.0, "mode": "gui", "status": "acquired"}

When ``license_guard.record_acquire`` accepts ``price``/``mode``/``status`` it
is called directly; with the older signature the adapter writes the same line
itself. ``update_status`` rewrites that one line in place (atomic replace),
unless ``license_guard`` grows its own ``update_status``.
"""

from __future__ import annotations

import datetime as dt
import inspect
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
    "Verdict",
    "check_can_acquire",
    "read_counts",
    "record",
    "update_status",
]

ACQUIRED = "acquired"
ACQUIRED_DOWNLOAD_FAILED = "acquired_download_failed"

_LOCK = threading.Lock()


def _record_accepts_extras() -> bool:
    try:
        params = inspect.signature(license_guard.record_acquire).parameters
    except (TypeError, ValueError):
        return False
    return all(name in params for name in ("price", "mode", "status"))


def record(
    track_id: str,
    *,
    bundle_id: str = "",
    storefront: str = "",
    price: float | None = None,
    mode: str = "gui",
    status: str = ACQUIRED,
    journal_path: Path,
) -> dict[str, Any]:
    with _LOCK:
        if _record_accepts_extras():
            return license_guard.record_acquire(
                track_id,
                bundle_id=bundle_id,
                storefront=storefront,
                price=price,
                mode=mode,
                status=status,
                journal_path=journal_path,
            )
        entry: dict[str, Any] = {
            "time": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
            "track_id": str(track_id),
            "bundle_id": bundle_id or "",
            "storefront": storefront or "",
        }
        if price is not None:
            entry["price"] = price
        if mode:
            entry["mode"] = mode
        if status:
            entry["status"] = status
        path = Path(journal_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        fresh = not path.exists()
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(entry, ensure_ascii=False) + "\n")
        if fresh:
            try:
                os.chmod(path, 0o600)
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
