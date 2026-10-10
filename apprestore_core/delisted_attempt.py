"""Isolated adapter: «удалённое не на аккаунте» (decision 10.10.2026, Облачко).

When Apple's lookup in the account's country gives no price (UNKNOWN) and
``region_probe`` — and only region_probe, never a GUI guess or the built-in
list of ``delisted_search`` — classified the app as DELISTED or NOT_IN_REGION,
the GUI offers «Поставить»: consent screen (the app counts in K), then the
license gate (limit 5/24 h, 15 total), Apple's refusal → error-apple-rejected,
no retry.

The price rule itself lives in Макс's ``license_guard`` (license_guard-1.14
handoff): ``acquire_and_record(track_id, None, region_unavailable=True,
region_session=RegionAttemptSession())`` — attempt allowed, limit counted, the
journal line (``price: null``, ``price_source: "apple_fixed_0"``) only if Apple
grants or the answer is unclear; an explicit refusal → ``code="apple_refused"``,
no line, no limit. The only call site is ``license_gate.run_with_free_license`` →
``license_journal.acquire_and_record(**fields)`` → ``license_guard.acquire_and_record``;
:func:`guard_kwargs` adds the two keywords there.

Conditions (LEGAL §1.14, Лена ok):

* flag only from region_probe DELISTED / NOT_IN_REGION;
* if ANY lookup (account or reference storefront) showed price > 0 → no button,
  no attempt (:func:`record_prices` collects every price the GUI saw);
* one attempt per app per Apple ID session (:func:`mark_attempted` in the GUI and
  Макс's ``RegionAttemptSession`` in the guard; both reset on «Выйти» / account switch);
* the feature is on only with a guard that knows ``region_unavailable`` (:func:`enabled`).

The registry is memory only (never on disk).
"""

from __future__ import annotations

import inspect
import threading
from collections.abc import Mapping

#: region_probe statuses (``RegionStatus.value``) that allow the attempt.
ATTEMPT_STATUSES = frozenset({"delisted", "not_in_region"})
#: Макс's license_guard.acquire_and_record keyword (§1.14); passed exactly True.
GUARD_PARAM = "region_unavailable"

_lock = threading.Lock()
_flags: dict[str, str] = {}
_paid_seen: set[str] = set()
_attempted: set[str] = set()
_account = ""
_session: object | None = None


def session() -> object:
    """The one RegionAttemptSession of this Apple ID session (memory only)."""

    global _session
    with _lock:
        if _session is None:
            from apprestore_core.license_guard import RegionAttemptSession

            _session = RegionAttemptSession()
        return _session


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


def reset_session() -> None:
    """«Выйти» / another Apple ID: flags, attempts and the guard's session go.
    Prices seen stay (a price > 0 is a fact about the app, not the account)."""

    with _lock:
        _flags.clear()
        _attempted.clear()
        if _session is not None:
            _session.reset()  # type: ignore[attr-defined]


def on_account(account: str | None) -> None:
    """Called with the signed-in Apple ID (or None): a switch resets the session."""

    global _account
    key = str(account or "").strip().casefold()
    with _lock:
        changed = key != _account
        _account = key
    if changed:
        reset_session()


def forget_all() -> None:
    global _account
    reset_session()
    with _lock:
        _paid_seen.clear()
        _account = ""


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
    if not flag or paid_seen(store_id) or not guard_supports():
        return {}
    return {GUARD_PARAM: True, "region_session": session()}
