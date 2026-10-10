"""App titles for 4b: never a bundle id.

Order (Евгений's live run, 10.10): the device's installation_proxy
CFBundleDisplayName, then CFBundleName (offloaded placeholders keep them; the
core already reads both into ``OffloadedApp.name`` / ``InstalledApp.name`` /
``MissingApp.name`` but falls back to the bundle id — that fallback is treated
as «no name» here); then the list-purchases name (purchases cache); then
delisted_search's built-in list by bundle id; else «Приложение».
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping

FALLBACK = "Приложение"

#: reverse-DNS with at least three parts and no spaces: com.vk.vkclient, ru.sberbankmobile.app
_BUNDLE_LIKE = re.compile(r"^[A-Za-z0-9_-]+(\.[A-Za-z0-9_-]+){2,}$")


def is_bundle_like(text: str, bundle_id: str = "") -> bool:
    value = (text or "").strip()
    if not value:
        return False
    if bundle_id and value.casefold() == bundle_id.strip().casefold():
        return True
    return bool(_BUNDLE_LIKE.match(value))


def usable(text: object, bundle_id: str = "", store_id: str = "") -> str:
    value = " ".join(str(text or "").split())
    if (not value or is_bundle_like(value, bundle_id) or (store_id and value == store_id) or value.isdigit()
            or re.fullmatch(r"App Store \d+", value)):  # service.py's stand-in for a nameless known app
        return ""
    return value


def purchase_names(rows: Iterable[Mapping[str, object]]) -> dict[str, str]:
    """purchases cache rows (trackId / bundleId / name) → key → name."""

    out: dict[str, str] = {}
    for row in rows or ():
        bundle = str(row.get("bundleId") or "")
        track = str(row.get("trackId") or "")
        name = usable(row.get("name"), bundle, track)
        if not name:
            continue
        for key in (track, bundle):
            if key and key not in out:
                out[key] = name
    return out


def builtin_names() -> dict[str, str]:
    """delisted_search built-in list (offline, no network): bundle id / track id → name."""

    try:
        from apprestore_core import delisted_search
    except Exception:  # noqa: BLE001 - not vendored: nothing
        return {}
    out: dict[str, str] = {}
    try:
        entries = delisted_search.builtin_entries()
    except Exception:  # noqa: BLE001
        return {}
    for entry in entries:
        name = usable(getattr(entry, "name", ""))
        if not name:
            continue
        bundle = str(getattr(entry, "bundle_id", "") or "")
        if bundle:
            out.setdefault(bundle, name)
        out.setdefault(str(getattr(entry, "track_id", "") or ""), name)
    return out


def resolve(device_name: object, *, bundle_id: str = "", store_id: str = "",
            purchases: Mapping[str, str] | None = None, builtin: Mapping[str, str] | None = None) -> str:
    name = usable(device_name, bundle_id, store_id)
    if name:
        return name
    for book in (purchases or {}, builtin or {}):
        for key in (store_id, bundle_id):
            if key and book.get(key):
                return book[key]
    return FALLBACK
