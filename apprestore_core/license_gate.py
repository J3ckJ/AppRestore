"""The one license gate for every front end (Qt Quick, Widgets, CLI).

``ipatool purchase`` is an App Store transaction that cannot be undone.
AppRestore sends it only from here, and only like this:

1. A read-only ``attempt()`` first. If the app is already on the Apple ID,
   nothing else happens.
2. Only when Apple answers "license is required": public iTunes lookup of the
   price in the account's country (``auth info``; the first country of the
   list only when ipatool does not say). Unknown or non-zero price: refusal.
3. Shared limit, 5 in 24 h and 15 total, over ``licenses_acquired.jsonl``
   (Макс's ``license_guard`` via ``license_journal``).
4. ``notify(LICENSE_NOTICE)`` — «бесплатное приложение будет добавлено…».
5. A separate ``ipatool purchase`` with a one-shot ``PurchaseGrant`` (R2:
   ``download`` never carries ``--purchase``).
   * success → journal ``acquired`` right away;
   * network error / timeout / unknown answer → journal ``purchase_uncertain``
     (counts toward the limit: the transaction may have gone through);
   * explicit refusal from Apple (paid, unavailable, not signed in, …) →
     no journal line, the error goes up.
6. The read-only ``attempt()`` again; if it fails the journal line becomes
   ``acquired_download_failed``.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable, TypeVar

from .command import CommandError
from .error_signal import _AUTH, _LICENSE, _REGION, is_license_missing
from .license_guard import DEFAULT_DAILY_LIMIT, DEFAULT_TOTAL_LIMIT, default_journal_path
from .license_journal import (
    ACQUIRED,
    ACQUIRED_DOWNLOAD_FAILED,
    PURCHASE_UNCERTAIN,
    Verdict,
    check_can_acquire,
    record,
    update_status,
)
from .purchase_grant import _mint
from .service import AppRestoreError

T = TypeVar("T")

LICENSE_NOTICE = "Бесплатное приложение будет добавлено на ваш Apple ID."
_NOT_OWNED = "Этого приложения нет на вашем Apple ID (нет лицензии)"

# Apple (or ipatool before sending anything) said no: no transaction happened.
_EXPLICIT_REFUSAL = _AUTH + _REGION + _LICENSE + (
    "failed to purchase",
    "purchasing paid apps",
    "paid app",
    "app not found",
    "failed to find",
    "is not installed",
    "command not found",
    "subscription",
)


class LicenseDenied(AppRestoreError):
    """The gate refused before anything was sent to Apple."""

    def __init__(self, message: str, verdict: Verdict | None = None) -> None:
        super().__init__(message)
        self.verdict = verdict


def journal_path() -> Path:
    """Shared with bench: ``license_guard.default_journal_path()``."""

    return default_journal_path()


def lookup_offer(store_id: str, countries: tuple[str, ...] | None = None) -> dict[str, object] | None:
    from .catalog import lookup_itunes_offer

    if countries:
        return lookup_itunes_offer(store_id, countries=countries)
    return lookup_itunes_offer(store_id)


def refusal_text(verdict: Verdict) -> str:
    reason = verdict.reason.casefold()
    if "платное" in reason:
        return f"{_NOT_OWNED}. Это платное приложение: AppRestore получает только бесплатные."
    if "цена" in reason:
        return (
            f"{_NOT_OWNED}. Apple не показывает его цену "
            "(возможно, оно снято), поэтому лицензию не берём."
        )
    if "лимит" in reason:
        return (
            "Лимит бесплатных лицензий исчерпан: "
            f"за сутки {verdict.used_today}/{DEFAULT_DAILY_LIMIT}, "
            f"всего {verdict.used_total}/{DEFAULT_TOTAL_LIMIT}. "
            "Это приложение пока не добавляем."
        )
    return f"{_NOT_OWNED}. Лицензию не берём: {verdict.reason}."


def purchase_outcome(exc: BaseException) -> str:
    """``refused`` (Apple clearly said no) or ``uncertain`` (may have gone through)."""

    if isinstance(exc, (ValueError, LicenseDenied)):
        return "refused"
    text = str(exc).casefold()
    if isinstance(exc, CommandError) and "command not found" in text:
        return "refused"
    if isinstance(exc, CommandError):
        return "uncertain"  # timeout or a broken run: the answer was lost
    if any(hint in text for hint in _EXPLICIT_REFUSAL):
        return "refused"
    return "uncertain"


def run_with_free_license(
    store_id: str,
    attempt: Callable[[], T],
    *,
    tools: Any,
    acquire: bool = True,
    lookup: Callable[[str, tuple[str, ...] | None], dict[str, object] | None] | None = None,
    journal: Path | None = None,
    notify: Callable[[str], None] | None = None,
    mode: str | None = "gui",
) -> T:
    """See the module docstring. ``attempt`` must never take a license itself."""

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
    fields = {
        "bundle_id": str((offer or {}).get("bundleId") or ""),
        "storefront": str((offer or {}).get("country") or country),
        "price": float(price) if price is not None else None,
        "mode": mode,
        "journal_path": path,
    }
    try:
        tools.purchase_license(store_id, grant=_mint(store_id))
    except Exception as exc:
        if purchase_outcome(exc) == "uncertain":
            record(store_id, status=PURCHASE_UNCERTAIN, **fields)
        raise
    entry = record(store_id, status=ACQUIRED, **fields)
    try:
        return attempt()
    except BaseException:
        update_status(entry, ACQUIRED_DOWNLOAD_FAILED, journal_path=path)
        raise
