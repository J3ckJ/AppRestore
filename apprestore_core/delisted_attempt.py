"""Isolated adapter: «удалённое не на аккаунте» (decision 10.10.2026, Облачко).

When Apple's lookup in the account's country gives no price (UNKNOWN) and
``region_probe`` — and only region_probe, never a GUI guess or the built-in
list of ``delisted_search`` — classified the app as DELISTED or NOT_IN_REGION,
the GUI offers «Поставить»: consent screen (the app counts in K), then the
license gate (limit 5/24 h, 15 total), Apple's refusal → error-apple-rejected,
no retry.

The price rule itself lives in Макс's ``license_guard`` (``_check_unlocked``
refuses ``price is None``). Макс adds an explicit path there: price unknown +
delisted/not-in-region flag → attempt allowed, limit counted, journal line only
if Apple grants. Until his ``license_guard`` with that path is vendored:

* :func:`guard_kwargs` returns ``{}`` — nothing changes in the gate, the guard
  still refuses unknown prices («цена не подтверждена…»);
* the only call site is ``license_gate.run_with_free_license`` →
  ``license_journal.acquire_and_record(**fields)`` →
  ``license_guard.acquire_and_record(...)``; the keyword name below is a
  placeholder until Макс posts his API.

Conditions (LEGAL §1.14, Лена ok):

* flag only from region_probe DELISTED / NOT_IN_REGION;
* if ANY lookup (account or reference storefront) showed price > 0 → no button,
  no attempt (:func:`record_prices` collects every price the GUI saw);
* one attempt per app per session (:func:`mark_attempted`, memory only);
* the feature is OFF until Макс's guard takes the flag (:func:`enabled`).

The registry is memory only (never on disk).
"""

from __future__ import annotations

import inspect
import threading
from collections.abc import Mapping

#: region_probe statuses (``RegionStatus.value``) that allow the attempt.
ATTEMPT_STATUSES = frozenset({"delisted", "not_in_region"})
#: PLACEHOLDER keyword for Макс's license_guard.acquire_and_record (pending).
GUARD_PARAM = "store_status"

_lock = threading.Lock()
_flags: dict[str, str] = {}
_paid_seen: set[str] = set()
_attempted: set[str] = set()


def status_value(status: object) -> str:
    raw = getattr(status, "value", status)
    return str(raw or "").strip().lower()


def is_attempt_status(status: object) -> bool:
    return status_value(status) in ATTEMPT_STATUSES


def record_region_probe(statuses: Mapping[str, object]) -> None:
    """region_probe answers (store id → RegionStatus). Other statuses clear the flag."""

    with _lock:
        for sid, status in statuses.items():
            key = str(sid)
            if is_attempt_status(status):
                _flags[key] = status_value(status)
            else:
                _flags.pop(key, None)


def flag_for(store_id: str) -> str:
    with _lock:
        return _flags.get(str(store_id), "")


def record_prices(prices: Mapping[str, object]) -> None:
    """Every price any lookup showed (account or reference storefront)."""

    with _lock:
        for sid, price in prices.items():
            try:
                value = float(price)  # type: ignore[arg-type]
            except (TypeError, ValueError):
                continue
            if value > 0:
                _paid_seen.add(str(sid))


def paid_seen(store_id: str) -> bool:
    with _lock:
        return str(store_id) in _paid_seen


def mark_attempted(store_ids: object) -> None:
    with _lock:
        _attempted.update(str(s) for s in store_ids)  # type: ignore[union-attr]


def attempted(store_id: str) -> bool:
    with _lock:
        return str(store_id) in _attempted


def forget_flags() -> None:
    """Sign out: the gate flags go; «already attempted» stays for the session."""

    with _lock:
        _flags.clear()


def forget_all() -> None:
    with _lock:
        _flags.clear()
        _paid_seen.clear()
        _attempted.clear()


def enabled() -> bool:
    """Feature switch: ON only once Макс's license_guard takes the flag."""

    return guard_supports()


def may_offer(store_id: str, status: object, price: object = None) -> bool:
    """«Поставить» for an app not on the account with an unknown price."""

    if price is not None or not is_attempt_status(status):
        return False
    if paid_seen(store_id) or attempted(store_id):
        return False
    return enabled()


def guard_supports() -> bool:
    """True once Макс's license_guard.acquire_and_record takes the flag."""

    try:
        from apprestore_core import license_guard

        return GUARD_PARAM in inspect.signature(license_guard.acquire_and_record).parameters
    except Exception:  # noqa: BLE001
        return False


def guard_kwargs(store_id: str, price: object) -> dict[str, str]:
    """Extra keyword for acquire_and_record: only for an unknown price and a
    region_probe flag, and only if the guard knows the keyword."""

    if price is not None:
        return {}
    flag = flag_for(store_id)
    if not flag or not guard_supports():
        return {}
    return {GUARD_PARAM: flag}
