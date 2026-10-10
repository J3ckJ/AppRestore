"""Free-license gate for «Поставить», «Получить» and «Сохранить копии».

Taking a free license on «Поставить» is intended (Евгений's decision), but
only under conditions:

1. First try without a license (``acquire=False``). If the app is already on
   the Apple ID, nothing else happens.
2. Only when Apple answers "license is required": public iTunes lookup of the
   price. Unknown or non-zero price is a refusal with a clear message.
3. Shared limit (5 in 24 h, 15 total) from ``apprestore_core.license_guard``
   over ``licenses_acquired.jsonl``.
4. The window is told «бесплатное приложение будет добавлено на ваш Apple ID»
   before the retry with a license.
5. A successful license is appended to the journal (no Apple ID, no tokens).

No Qt here, so the gate is testable without PySide6.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Callable, TypeVar

from apprestore_core.license_guard import (
    DEFAULT_DAILY_LIMIT,
    DEFAULT_TOTAL_LIMIT,
    Verdict,
    check_can_acquire,
    record_acquire,
)
from apprestore_gui.errors import NOT_OWNED_TEXT, is_license_missing

T = TypeVar("T")

LICENSE_NOTICE = "Бесплатное приложение будет добавлено на ваш Apple ID."


class LicenseDenied(RuntimeError):
    """The gate refused before anything was sent with ``--purchase``."""

    def __init__(self, message: str, verdict: Verdict | None = None) -> None:
        super().__init__(message)
        self.verdict = verdict


def journal_path() -> Path:
    """``$APPRESTORE_LICENSE_JOURNAL`` or ``~/.apprestore/licenses_acquired.jsonl``."""

    configured = os.environ.get("APPRESTORE_LICENSE_JOURNAL")
    if configured:
        return Path(configured).expanduser()
    return Path.home() / ".apprestore" / "licenses_acquired.jsonl"


def lookup_offer(store_id: str) -> dict[str, object] | None:
    from apprestore_core.catalog import lookup_itunes_offer

    return lookup_itunes_offer(store_id)


def refusal_text(verdict: Verdict) -> str:
    reason = verdict.reason.casefold()
    if "платное" in reason:
        return (
            f"{NOT_OWNED_TEXT.split('.')[0]}. Это платное приложение: "
            "AppRestore получает только бесплатные."
        )
    if "цена" in reason:
        return (
            f"{NOT_OWNED_TEXT.split('.')[0]}. Apple не показывает его цену "
            "(возможно, оно снято), поэтому лицензию не берём."
        )
    if "лимит" in reason:
        return (
            "Лимит бесплатных лицензий исчерпан: "
            f"за сутки {verdict.used_today}/{DEFAULT_DAILY_LIMIT}, "
            f"всего {verdict.used_total}/{DEFAULT_TOTAL_LIMIT}. "
            "Это приложение пока не добавляем."
        )
    return f"{NOT_OWNED_TEXT.split('.')[0]}. Лицензию не берём: {verdict.reason}."


def run_with_free_license(
    store_id: str,
    attempt: Callable[[bool], T],
    *,
    acquire: bool = True,
    lookup: Callable[[str], dict[str, object] | None] | None = None,
    journal: Path | None = None,
    notify: Callable[[str], None] | None = None,
) -> T:
    """``attempt(False)``, and only if the license is missing and the gate
    allows it, ``attempt(True)`` (the path that sends ``--purchase``)."""

    try:
        return attempt(False)
    except Exception as exc:
        if not acquire or not is_license_missing(str(exc)):
            raise
        missing = exc

    store_id = str(store_id or "").strip()
    offer: dict[str, object] | None = None
    if store_id.isdigit():
        try:
            offer = (lookup or lookup_offer)(store_id)
        except Exception:  # noqa: BLE001 - no price means no license
            offer = None
    price = offer.get("price") if offer else None
    path = journal or journal_path()
    verdict = check_can_acquire(store_id, price, journal_path=path)
    if not verdict.allowed:
        raise LicenseDenied(refusal_text(verdict), verdict) from missing

    if notify is not None:
        notify(LICENSE_NOTICE)
    result = attempt(True)
    record_acquire(
        store_id,
        bundle_id=str((offer or {}).get("bundleId") or ""),
        storefront=str((offer or {}).get("country") or ""),
        journal_path=path,
    )
    return result
