"""What can be restored, as one flat list of :class:`RestoreItem`.

Three groups, in the order the 4b picker shows them:

* ``removed``   — gone from the phone and from the App Store; comes back from
  Apple's servers on the user's own Apple ID (license gate) or from a local IPA;
* ``region``    — Apple does not give it to this account's region; shown, never
  selectable (only a local IPA helps);
* ``offloaded`` — iOS offloaded it, the icon is still on the phone.

Sizes are honest lower bounds or unknown: a local IPA's file size is the
compressed app, so the installed app takes at least that much. Without an IPA
the size is unknown until download («размер узнаем при скачивании»).
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, replace
from pathlib import Path

from apprestore_core.models import MissingApp, OffloadedApp

GROUP_REMOVED = "removed"
from apprestore_core.region_probe import RegionStatus  # noqa: E402

from .store_labels import caption, region_group_title  # noqa: E402

GROUP_REGION = "region"
GROUP_OFFLOADED = "offloaded"
#: Offline only: removed + region folded into one unchecked group (Ника §6).
GROUP_NOPHONE = "nophone"
GROUP_ORDER: tuple[str, ...] = (GROUP_REMOVED, GROUP_NOPHONE, GROUP_REGION, GROUP_OFFLOADED)
GROUP_TITLES: dict[str, str] = {
    GROUP_REMOVED: "Удалённые из App Store",
    GROUP_NOPHONE: "Нет на iPhone · не проверено",
    GROUP_REGION: region_group_title(),  # region_probe + Ника's overrides (store_labels)
    GROUP_OFFLOADED: "Сгруженные",
}

#: How an item is put back on the device.
ACTION_OFFLOADED = "offloaded"  # QuickSession.restore (iOS redownload / IPA fallback)
ACTION_STORE = "store"  # QuickSession.installStore → license_gate
ACTION_IPA = "ipa"  # QuickSession.installSaved (local file)
ACTION_NONE = "none"


_SHORT_CUTS = (":", "—", " – ", " - ")


def short_name_of(name: str) -> str:
    """«Сбер» from «Сбер: банк и кошелёк» (спека: до «:», «—», « - »)."""

    text = (name or "").strip()
    for cut in _SHORT_CUTS:
        head = text.split(cut, 1)[0].strip()
        if head:
            text = head
    return text


@dataclass(frozen=True)
class RestoreItem:
    key: str
    name: str
    group: str
    action: str
    developer: str = ""
    store_id: str = ""
    bundle_id: str = ""
    #: Lower bound in bytes; None = unknown until download.
    size_bytes: int | None = None
    #: Short label under the phone icon («Сбер»); defaults to the name.
    short_name: str = ""
    ipa_path: str = ""
    #: Why it cannot be chosen (region group).
    note: str = ""

    @property
    def selectable(self) -> bool:
        return self.group != GROUP_REGION and self.action != ACTION_NONE

    @property
    def label(self) -> str:
        return self.short_name or short_name_of(self.name)

    @property
    def icon_keys(self) -> tuple[str, ...]:
        return tuple(key for key in (self.store_id, self.bundle_id) if key)


def _ipa_size(path: Path | None) -> int | None:
    if path is None:
        return None
    try:
        size = Path(path).stat().st_size
    except OSError:
        return None
    return size if size > 0 else None


def from_offloaded(app: OffloadedApp, size_hint: int | None = None) -> RestoreItem:
    name = app.name.strip() or app.bundle_id
    size = size_hint if size_hint and size_hint > 0 else _ipa_size(app.local_ipa)
    return RestoreItem(
        key=app.bundle_id,
        name=name,
        group=GROUP_OFFLOADED,
        action=ACTION_OFFLOADED,
        store_id=str(app.store_id or ""),
        bundle_id=app.bundle_id,
        size_bytes=size,
    )


def from_missing(
    app: MissingApp,
    *,
    region_blocked: bool = False,
    region_name: str = "",
    size_hint: int | None = None,
) -> RestoreItem | None:
    """None when there is no way back (no store id and no local IPA)."""

    name = app.name.strip() or app.bundle_id or str(app.store_id or "")
    store_id = str(app.store_id or "")
    ipa = str(app.local_ipa) if app.local_ipa else ""
    size = size_hint if size_hint and size_hint > 0 else _ipa_size(app.local_ipa)
    key = app.bundle_id or f"store:{store_id}" or ipa
    if region_blocked:
        where = caption(RegionStatus.NOT_IN_REGION)  # one source, no country name in the caption
        return RestoreItem(
            key=key, name=name, group=GROUP_REGION, action=ACTION_NONE if not ipa else ACTION_IPA,
            store_id=store_id, bundle_id=app.bundle_id, size_bytes=size, ipa_path=ipa, note=where,
        )
    if store_id:
        action = ACTION_STORE
    elif ipa:
        action = ACTION_IPA
    else:
        return None
    return RestoreItem(
        key=key,
        name=name,
        group=GROUP_REMOVED,
        action=action,
        store_id=store_id,
        bundle_id=app.bundle_id,
        size_bytes=size,
        ipa_path=ipa,
    )


def build_items(
    offloaded: Iterable[OffloadedApp] = (),
    missing: Iterable[MissingApp] = (),
    *,
    region_blocked: Iterable[str] = (),
    region_name: str = "",
    size_hints: Mapping[str, int] | None = None,
    short_names: Mapping[str, str] | None = None,
    developers: Mapping[str, str] | None = None,
) -> list[RestoreItem]:
    """One list, de-duplicated by key (an app is either offloaded or missing)."""

    blocked = {str(value) for value in region_blocked if str(value)}
    hints = dict(size_hints or {})
    shorts = dict(short_names or {})
    devs = dict(developers or {})
    items: list[RestoreItem] = []
    seen: set[str] = set()

    def hint(*keys: str) -> int | None:
        for key in keys:
            if key and key in hints:
                return hints[key]
        return None

    for app in offloaded:
        item = from_offloaded(app, hint(str(app.store_id or ""), app.bundle_id))
        if item.key in seen:
            continue
        seen.add(item.key)
        items.append(item)
    for app in missing:
        if app.bundle_id and app.bundle_id in seen:
            continue
        store_id = str(app.store_id or "")
        item = from_missing(
            app,
            region_blocked=bool(store_id and store_id in blocked) or app.bundle_id in blocked,
            region_name=region_name,
            size_hint=hint(store_id, app.bundle_id),
        )
        if item is None or item.key in seen:
            continue
        seen.add(item.key)
        items.append(item)

    out: list[RestoreItem] = []
    for item in items:
        extra: dict[str, str] = {}
        for key in item.icon_keys:
            if key in shorts and not item.short_name:
                extra["short_name"] = shorts[key]
            if key in devs and not item.developer:
                extra["developer"] = devs[key]
        out.append(replace(item, **extra) if extra else item)
    return out
