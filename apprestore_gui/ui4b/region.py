"""Accepting the store classification (Макс's classifier) into the 4b model.

Макс's module answers per app: ``AVAILABLE`` / ``NOT_IN_REGION`` / ``DELISTED``
/ ``UNKNOWN`` (input: track id + the account's country; anonymous public
lookup in 1–2 fixed reference storefronts, cached — LEGAL §1.10). Here we only
take the result: no lookups, no guessing.

* ``NOT_IN_REGION`` → group «Нет в App Store вашей страны», caption only;
* ``DELISTED`` → stays in «Удалённые», caption «удалено из App Store»;
* ``UNKNOWN`` / ``AVAILABLE`` / no classifier → nothing changes. With no
  classification that group stays empty and is not shown at all.

The captions live here and nowhere else (Макс sends his, Ника finalises).
Captions are labels only: no «buy it there», no region switch, no hints where
to get a file, no links.
"""

from __future__ import annotations

import enum
from collections.abc import Callable, Iterable, Mapping
from dataclasses import replace

from .catalog import ACTION_NONE, ACTION_STORE, GROUP_REGION, GROUP_REMOVED, RestoreItem


from apprestore_core.region_probe import RegionStatus

from .store_labels import caption

#: The region-group link and its explanation (Лена): only a file the user already has.
IPA_LINK = "Поставить из файла на компьютере…"
IPA_MORE = "Если у вас сохранился собственный файл, например из старой резервной копии, его можно поставить с этого компьютера."

# Compatibility name for the earlier adapter.
StoreStatus = RegionStatus


def to_status(value: object) -> RegionStatus:
    """Anything from the classifier (enum member, its name or value) → RegionStatus."""

    if isinstance(value, RegionStatus):
        return value
    raw = getattr(value, "name", None) or getattr(value, "value", None) or value
    text = str(raw or "").strip().lower()
    for status in RegionStatus:
        if text in (str(status.value).lower(), status.name.lower()):
            return status
    return RegionStatus.UNKNOWN


def apply_statuses(items: Iterable[RestoreItem], statuses: Mapping[str, object]) -> list[RestoreItem]:
    """Only labels and the region group; no guessing for UNKNOWN (it stays where it was)."""

    out: list[RestoreItem] = []
    for item in items:
        if not item.store_id or item.store_id not in statuses:
            out.append(item)
            continue
        status = to_status(statuses.get(item.store_id))
        if item.action == ACTION_STORE and status is RegionStatus.NOT_IN_REGION:
            # decision 10.10 (Облачко): selectable, goes through consent + gate
            out.append(replace(item, group=GROUP_REGION, note=caption(status), store_status=status.value))
        elif item.group == GROUP_REMOVED and status is RegionStatus.DELISTED:
            out.append(replace(item, note=item.note or caption(status), store_status=status.value))
        elif item.group == GROUP_REMOVED and status is RegionStatus.UNKNOWN:
            out.append(replace(item, note=item.note or caption(status)))
        else:
            out.append(item)
    return out


def load_classifier() -> Callable[[list[str], str], Mapping[object, object]] | None:
    """Макс's ``region_probe.classify_region_detailed`` (status + known reference
    price, §1.14 п.2), else ``classify_region`` (batch: track ids + account country)."""

    try:
        from apprestore_core import region_probe
    except Exception:  # noqa: BLE001
        return None
    return getattr(region_probe, "classify_region_detailed", None) or getattr(region_probe, "classify_region", None)


def classify(classifier: Callable[..., Mapping[object, object]] | None, store_ids: list[str],
             country: str | None, *, online: bool) -> dict[str, object]:
    """Worker-thread call. No network / no country / no ids → {} (groups hidden)."""

    ids = [sid for sid in store_ids if str(sid).isdigit()]
    if classifier is None or not online or not country or not ids:
        return {}
    try:
        result = classifier(ids, country)
    except Exception:  # noqa: BLE001 - ValueError on a bad country, network: nothing shown
        return {}
    out: dict[str, object] = {}
    prices: dict[str, object] = {}
    for k, v in dict(result).items():
        status = getattr(v, "status", v)  # RegionResult or a bare RegionStatus
        out[str(k)] = to_status(status)
        known = getattr(v, "known_price", None)
        if known is not None:
            prices[str(k)] = known
    if prices:
        from apprestore_core.delisted_attempt import record_prices

        record_prices(prices)  # any > 0 → no «Поставить», guard refuses «платное»
    return out
