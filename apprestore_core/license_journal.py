"""Thin adapter over Макс's ``license_guard`` (copied unchanged from maks-share).

``license_guard`` does the price/limit check, writes journal lines, counts
``acquired``, ``acquired_download_failed`` and ``purchase_uncertain``, and
owns the cross-process lock ``<journal>.lock`` (``journal_lock``,
``acquire_and_record``). There is no second lock here: ``acquire_and_record``
below maps the gate's purchase onto Макс's callback contract, and
``update_status`` takes Макс's ``journal_lock``.

The journal is append-only (Лена): lines are never rewritten or removed.
The gate writes ``acquired`` right after a successful ``ipatool purchase``;
if the download that follows fails, ``update_status`` appends an amendment
through Макс's ``license_guard.record_amend``:
``{id, time, track_id, status, amends: <uuid of that line>, reason}``.
Links are by uuid only (legacy lines without ``id`` are never amended, only
voided explicitly with ``voids``). ``license_guard`` counts the chain's last
status (``acquired`` → ``acquired_download_failed`` still counts once); the
amendment line itself is not an extra license; ``voids`` cancels.
"""

from __future__ import annotations

import os
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


def update_status(
    target_id: str,
    status: str,
    *,
    track_id: str = "",
    reason: str = "",
    journal_path: Path,
) -> dict[str, Any] | None:
    """Append ``amends: <target_id>`` with the chain's new ``status``.

    ``target_id`` is the ``id`` of the line ``acquire_and_record`` returned.
    Delegates to Макс's ``license_guard.record_amend`` (same ``journal_lock``):
    earlier lines stay byte-for-byte unchanged, the file only grows. Returns
    the appended line, or ``None`` (nothing written) when ``target_id`` is
    empty or not in the journal.
    """

    if not target_id:
        return None
    path = Path(journal_path)
    with _LOCK:
        entry = license_guard.record_amend(target_id, status, reason, track_id=track_id, journal_path=path)
    if entry is not None:
        _private(path)
    return entry


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
