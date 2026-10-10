"""Free-license gate for «Поставить», «Получить» and «Сохранить копии».

Taking a free license on «Поставить» is intended (Евгений's decision), but
only under conditions:

1. First try without a license (``acquire=False``). If the app is already on
   the Apple ID, nothing else happens.
2. Only when Apple answers "license is required": public iTunes lookup of the
   price in the account's country (``auth info``; first country of the list
   only when ipatool does not say). Unknown or non-zero price is a refusal.
3. Shared limit (5 in 24 h, 15 total) from ``apprestore_core.license_guard``
   over ``licenses_acquired.jsonl``.
4. The window is told «бесплатное приложение будет добавлено на ваш Apple ID».
5. A separate ``ipatool purchase`` (R2: ``download`` never carries
   ``--purchase``), journal line ``acquired`` right after it, then a plain
   download; if that fails the line becomes ``acquired_download_failed``.

No Qt here, so the gate is testable without PySide6.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable, TypeVar

from apprestore_core.license_guard import (
    DEFAULT_DAILY_LIMIT,
    DEFAULT_TOTAL_LIMIT,
    default_journal_path,
)
from apprestore_core.license_journal import (
    ACQUIRED,
    ACQUIRED_DOWNLOAD_FAILED,
    Verdict,
    check_can_acquire,
    record,
    update_status,
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
    """Shared with bench: ``license_guard.default_journal_path()``."""

    return default_journal_path()


def lookup_offer(store_id: str, countries: tuple[str, ...] | None = None) -> dict[str, object] | None:
    from apprestore_core.catalog import lookup_itunes_offer

    if countries:
        return lookup_itunes_offer(store_id, countries=countries)
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
    attempt: Callable[[], T],
    *,
    tools: Any,
    acquire: bool = True,
    lookup: Callable[[str, tuple[str, ...] | None], dict[str, object] | None] | None = None,
    journal: Path | None = None,
    notify: Callable[[str], None] | None = None,
    mode: str = "gui",
) -> T:
    """Read-only ``attempt()``; only if the license is missing and the gate
    allows it: ``tools.purchase_license`` → journal ``acquired`` →
    ``attempt()`` again (still a plain download, never ``--purchase``)."""

    try:
        return attempt()
    except Exception as exc:
        if not acquire or not is_license_missing(str(exc)):
            raise
        missing = exc

    store_id = str(store_id or "").strip()
    country = ""
    try:
        country = str(tools.account_country() or "").strip().lower()
    except Exception:  # noqa: BLE001 - unknown country: fallback list
        country = ""
    offer: dict[str, object] | None = None
    if store_id.isdigit():
        try:
            offer = (lookup or lookup_offer)(store_id, (country,) if country else None)
        except Exception:  # noqa: BLE001 - no price means no license
            offer = None
    price = offer.get("price") if offer else None
    path = journal or journal_path()
    verdict = check_can_acquire(store_id, price, journal_path=path)
    if not verdict.allowed:
        raise LicenseDenied(refusal_text(verdict), verdict) from missing

    if notify is not None:
        notify(LICENSE_NOTICE)
    # A failed purchase gives no license: nothing to journal, error goes up.
    tools.purchase_license(store_id=store_id)
    entry = record(
        store_id,
        bundle_id=str((offer or {}).get("bundleId") or ""),
        storefront=str((offer or {}).get("country") or country),
        price=float(price) if price is not None else None,
        mode=mode,
        status=ACQUIRED,
        journal_path=path,
    )
    try:
        return attempt()
    except BaseException:
        update_status(entry, ACQUIRED_DOWNLOAD_FAILED, journal_path=path)
        raise
