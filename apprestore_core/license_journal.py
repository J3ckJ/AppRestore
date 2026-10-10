"""Thin adapter over Макс's ``license_guard`` (copied unchanged from maks-share).

``license_guard`` does the price/limit check, writes journal lines, counts
``acquired``, ``acquired_download_failed`` and ``purchase_uncertain``, and
owns the cross-process lock ``<journal>.lock`` (``journal_lock``,
``acquire_and_record``). There is no second lock here: ``acquire_and_record``
below maps the gate's purchase onto Макс's callback contract, and
``update_status`` takes Макс's ``journal_lock``.

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
from typing import Any, Callable

from . import license_guard
from .license_guard import Verdict, check_can_acquire, read_counts

__all__ = [
    "ACQUIRED",
    "ACQUIRED_DOWNLOAD_FAILED",
    "PURCHASE_UNCERTAIN",
    "PurchaseRefused",
    "Verdict",
    "acquire_and_record",
    "check_can_acquire",
    "read_counts",
    "record",
    "update_status",
]

ACQUIRED = "acquired"
ACQUIRED_DOWNLOAD_FAILED = "acquired_download_failed"
# purchase answered with a network error/timeout: Apple may have recorded it.
PURCHASE_UNCERTAIN = "purchase_uncertain"

_LOCK = threading.RLock()


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
    # Same <journal>.lock as Макс's acquire_and_record / record_acquire / bench.
    with _LOCK, license_guard.journal_lock(path):
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


class PurchaseRefused(Exception):
    """The purchase callback's signal: Apple (or ipatool before sending
    anything) clearly said no, so no transaction happened and nothing is
    journaled. Any other exception, a timeout included, is ``purchase_uncertain``.
    """


def acquire_and_record(
    track_id: str,
    price: float | None,
    purchase: Callable[[], object],
    *,
    bundle_id: str = "",
    storefront: str = "",
    mode: str | None = "gui",
    journal_path: Path,
) -> tuple[Verdict, dict[str, Any] | None]:
    """Limit check → ``purchase()`` → journal line, as one locked step.

    Runs Макс's ``license_guard.acquire_and_record``, which holds
    ``<journal>.lock`` (flock / msvcrt) across all three. Returns
    ``(verdict, entry)``; ``entry is None`` means the limit/price said no and
    ``purchase`` was not called.

    Макс's callback contract is a return value ("acquired" /
    "purchase_uncertain" / "refused"); an exception escaping the callback
    would leave NO journal line. So the callback here never raises: it catches
    everything, ``PurchaseRefused`` → "refused" (not journaled), any other
    exception — timeout, network, crash, Ctrl+C — → "purchase_uncertain"
    (journaled, Лена's "uncertain on any exception"). The exception is
    re-raised after the lock is released.
    """

    caught: list[BaseException] = []

    def callback() -> str:
        try:
            purchase()
        except PurchaseRefused as exc:
            caught.append(exc)
            return "refused"
        except BaseException as exc:  # noqa: BLE001 - re-raised below
            caught.append(exc)
            return PURCHASE_UNCERTAIN
        return ACQUIRED

    result = license_guard.acquire_and_record(
        track_id,
        price,
        purchase=callback,
        journal_path=journal_path,
        bundle_id=bundle_id,
        storefront=storefront,
        mode=mode,
    )
    if result.recorded:
        _private(journal_path)
    if caught:
        raise caught[0]
    verdict = Verdict(result.allowed, result.reason, result.used_today, result.used_total)
    if not result.allowed:
        return verdict, None
    if not result.recorded:  # pragma: no cover - callback only says refused via exception
        raise PurchaseRefused(result.reason)
    return verdict, result.entry


def _private(journal_path: Path) -> None:
    try:
        os.chmod(journal_path, 0o600)
    except OSError:
        pass
