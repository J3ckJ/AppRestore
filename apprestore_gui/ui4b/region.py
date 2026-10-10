"""Accepting the store classification (Макс's classifier) into the 4b model.

Макс's module answers per app: ``AVAILABLE`` / ``NOT_IN_REGION`` / ``DELISTED``
/ ``UNKNOWN`` (input: track id + the account's country; anonymous public
lookup in 1–2 fixed reference storefronts, cached — LEGAL §1.10). Here we only
take the result: no lookups, no guessing.

* ``NOT_IN_REGION`` → group «Нет в регионе», caption only;
* ``DELISTED`` → stays in «Удалённые», caption «удалено из App Store»;
* ``UNKNOWN`` / ``AVAILABLE`` / no classifier → nothing changes. With no
  classification «Нет в регионе» stays empty and is not shown at all.

The captions live here and nowhere else (Макс sends his, Ника finalises).
Captions are labels only: no «buy it there», no region switch, no hints where
to get a file, no links.
"""

from __future__ import annotations

import enum
from collections.abc import Callable, Iterable, Mapping
from dataclasses import replace

from .catalog import ACTION_NONE, ACTION_STORE, GROUP_REGION, GROUP_REMOVED, RestoreItem


class StoreStatus(str, enum.Enum):
    AVAILABLE = "available"
    NOT_IN_REGION = "not_in_region"
    DELISTED = "delisted"
    UNKNOWN = "unknown"


CAPTIONS: dict[StoreStatus, str] = {
    StoreStatus.NOT_IN_REGION: "нет в App Store вашей страны",
    StoreStatus.DELISTED: "удалено из App Store",
}


def to_status(value: object) -> StoreStatus:
    """Anything from the classifier (enum member, its name or value) → StoreStatus."""

    raw = getattr(value, "name", None) or getattr(value, "value", None) or value
    text = str(raw or "").strip().lower()
    for status in StoreStatus:
        if text in (status.value, status.name.lower()):
            return status
    return StoreStatus.UNKNOWN


def apply_statuses(items: Iterable[RestoreItem], statuses: Mapping[str, object]) -> list[RestoreItem]:
    out: list[RestoreItem] = []
    for item in items:
        status = to_status(statuses.get(item.store_id)) if item.store_id else StoreStatus.UNKNOWN
        if item.action == ACTION_STORE and status is StoreStatus.NOT_IN_REGION:
            out.append(replace(item, group=GROUP_REGION, action=ACTION_NONE, note=CAPTIONS[status]))
        elif item.group == GROUP_REMOVED and status is StoreStatus.DELISTED:
            out.append(replace(item, note=item.note or CAPTIONS[status]))
        else:
            out.append(item)
    return out


def load_classifier() -> Callable[[str, str], object] | None:
    """Макс's classifier if it is there (``apprestore_core.store_status.classify``)."""

    try:
        from apprestore_core import store_status  # type: ignore[attr-defined]
    except Exception:  # noqa: BLE001 - not delivered yet: the group stays hidden
        return None
    func = getattr(store_status, "classify", None)
    return func if callable(func) else None
