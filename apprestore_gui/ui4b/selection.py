"""State of the «Что вернуть» window: rail, search, sort, check marks.

Plain Python; ``qt_bridge.PickerModel`` turns :meth:`Selection.rows` into a
list model and forwards clicks back here.
"""

from __future__ import annotations

import html
import re
from collections.abc import Iterable
from dataclasses import dataclass

from dataclasses import replace

from apprestore_gui.ui4b.catalog import (
    ACTION_NONE,
    ACTION_OFFLOADED,
    GROUP_NOPHONE,
    GROUP_ORDER,
    GROUP_REGION,
    GROUP_REMOVED,
    GROUP_TITLES,
    RestoreItem,
    short_name_of,
)
from apprestore_gui.ui4b.formatting import format_size
from apprestore_gui.ui4b.space import UNKNOWN_SPACE, DeviceSpace, SpacePlan, plan_space

SORT_SIZE = "size"
SORT_NAME = "name"
RAIL_ALL = "all"

CHECK_ON = "on"
CHECK_OFF = "off"
CHECK_MIXED = "mixed"
CHECK_DISABLED = "disabled"

_STORE_URL = re.compile(r"(?:apps\.apple\.com/\S*?/id|^id)(\d{5,})", re.IGNORECASE)


@dataclass(frozen=True)
class Match:
    """Where the query hit the name (for the highlight)."""

    start: int = -1
    length: int = 0


OFFLINE_ROW_NOTE = "не проверено"
#: Лена 10.10: offloaded ones the iPhone downloads itself (no license, no
#: gate), so they can be restored offline; everything through the gate cannot.
OFFLINE_FOOTER = "Нет интернета: сейчас можно вернуть только сгруженные."
OFFLINE_RAIL_SUB = "проверим, когда будет сеть"


def parse_query(text: str) -> tuple[str, str]:
    """(casefolded words, store id) from what the user typed.

    A number or an apps.apple.com link searches by store id.
    """

    raw = (text or "").strip()
    found = _STORE_URL.search(raw)
    if found:
        return "", found.group(1)
    if raw.isdigit():
        return "", raw
    return raw.casefold(), ""


def match_item(item: RestoreItem, query: str) -> Match | None:
    words, store_id = parse_query(query)
    if store_id:
        return Match() if item.store_id == store_id else None
    if not words:
        return Match()
    position = item.name.casefold().find(words)
    if position >= 0:
        return Match(position, len(words))
    if words in item.developer.casefold() or words in item.bundle_id.casefold():
        return Match()
    return None


def highlight_html(name: str, match: Match | None, mark_color: str) -> str:
    """Name as rich text with the query marked; everything else escaped."""

    if match is None or match.start < 0 or match.length <= 0:
        return html.escape(name)
    head = html.escape(name[: match.start])
    hit = html.escape(name[match.start : match.start + match.length])
    tail = html.escape(name[match.start + match.length :])
    return f'{head}<span style="background-color:{mark_color}">{hit}</span>{tail}'


def default_selection(items: Iterable[RestoreItem]) -> set[str]:
    """Removed-from-App-Store apps are why the user came; offloaded ones are opt-in."""

    return {item.key for item in items if item.group == GROUP_REMOVED and item.selectable}


class Selection:
    def __init__(
        self,
        items: Iterable[RestoreItem] = (),
        *,
        space: DeviceSpace = UNKNOWN_SPACE,
        selected: Iterable[str] | None = None,
        mark_color: str = "#f6ebe4",
    ) -> None:
        self._items: list[RestoreItem] = []
        self._by_key: dict[str, RestoreItem] = {}
        self._selected: set[str] = set()
        self.sort = SORT_SIZE
        self.rail = RAIL_ALL
        self.query = ""
        self.space = space
        self.mark_color = mark_color
        #: No internet: the list is what the phone and the cache know, unchecked.
        self.offline = False
        self._source: list[RestoreItem] = []
        self.set_items(items, selected=selected)

    # -- data ------------------------------------------------------------------

    @property
    def items(self) -> list[RestoreItem]:
        return list(self._items)

    def set_items(self, items: Iterable[RestoreItem], *, selected: Iterable[str] | None = None) -> None:
        """New list; marks survive for keys that are still there."""

        first = not self._items
        self._source = list(items)
        self._items = self._derive(self._source)
        self._by_key = {item.key: item for item in self._items}
        if selected is not None:
            wanted = set(selected)
        elif first:
            wanted = default_selection(self._items)
        else:
            wanted = set(self._selected)
        self._selected = {key for key in wanted if key in self._by_key and self._by_key[key].selectable}
        if self.rail != RAIL_ALL and self.rail not in GROUP_ORDER:
            self.rail = RAIL_ALL

    def _derive(self, items: list[RestoreItem]) -> list[RestoreItem]:
        if not self.offline:
            return list(items)
        # Without internet the store status is unknown: removed and region fold
        # into «Нет на iPhone», unchecked and not selectable (they need the gate).
        return [
            item
            if item.action == ACTION_OFFLOADED
            else replace(item, group=GROUP_NOPHONE, action=ACTION_NONE, note=OFFLINE_ROW_NOTE)
            for item in items
        ]

    def set_offline(self, offline: bool) -> None:
        """Marks of store apps are parked while offline and come back with the network."""

        offline = bool(offline)
        if offline == self.offline:
            return
        self.offline = offline
        before = set(self._selected)
        self._items = self._derive(self._source)
        self._by_key = {item.key: item for item in self._items}
        if offline:
            self._parked = {k for k in before if k in self._by_key and not self._by_key[k].selectable}
            wanted = before
        else:
            wanted = before | getattr(self, "_parked", set())
            self._parked = set()
        self._selected = {k for k in wanted if k in self._by_key and self._by_key[k].selectable}
        if self.rail != RAIL_ALL and not any(i.group == self.rail for i in self._items):
            self.rail = RAIL_ALL

    def set_space(self, space: DeviceSpace) -> None:
        self.space = space

    # -- marks -------------------------------------------------------------------

    def is_selected(self, key: str) -> bool:
        return key in self._selected

    def set_selected(self, key: str, on: bool) -> bool:
        item = self._by_key.get(key)
        if item is None or not item.selectable:
            return False
        if on:
            self._selected.add(key)
        else:
            self._selected.discard(key)
        return True

    def toggle(self, key: str) -> bool:
        return self.set_selected(key, not self.is_selected(key))

    def _visible_selectable(self, group: str | None = None) -> list[RestoreItem]:
        return [
            item
            for item in self._visible_items()
            if item.selectable and (group is None or item.group == group)
        ]

    def select_visible(self, on: bool = True) -> None:
        """«Выбрать всё» / «Снять всё»: what the rail and search show now."""

        for item in self._visible_selectable():
            self.set_selected(item.key, on)

    def toggle_group(self, group: str) -> None:
        """Header check box: mixed or off → all on; all on → all off (visible rows)."""

        state = self.group_state(group)
        if state == CHECK_DISABLED:
            return
        on = state != CHECK_ON
        for item in self._visible_selectable(group):
            self.set_selected(item.key, on)

    def group_state(self, group: str) -> str:
        rows = self._visible_selectable(group)
        if not rows:
            return CHECK_DISABLED
        chosen = sum(1 for item in rows if item.key in self._selected)
        if chosen == 0:
            return CHECK_OFF
        if chosen == len(rows):
            return CHECK_ON
        return CHECK_MIXED

    # -- filters -----------------------------------------------------------------

    def set_sort(self, sort: str) -> None:
        if sort in (SORT_SIZE, SORT_NAME):
            self.sort = sort

    def set_rail(self, rail: str) -> None:
        if rail == RAIL_ALL or rail in GROUP_ORDER:
            self.rail = rail

    def set_query(self, query: str) -> None:
        self.query = query or ""

    def _sorted(self, items: list[RestoreItem]) -> list[RestoreItem]:
        if self.sort == SORT_NAME:
            return sorted(items, key=lambda item: item.name.casefold())
        # Biggest first; unknown sizes after the known ones, then by name.
        return sorted(
            items,
            key=lambda item: (item.size_bytes is None, -(item.size_bytes or 0), item.name.casefold()),
        )

    def _visible_items(self) -> list[RestoreItem]:
        return [
            item
            for item in self._items
            if (self.rail == RAIL_ALL or item.group == self.rail) and match_item(item, self.query) is not None
        ]

    # -- views -------------------------------------------------------------------

    def selected_items(self) -> list[RestoreItem]:
        """In list order (groups, then the current sort): the install order."""

        out: list[RestoreItem] = []
        for group in GROUP_ORDER:
            out.extend(
                self._sorted([item for item in self._items if item.group == group and item.key in self._selected])
            )
        return out

    def search_note(self) -> str:
        """«Совпадения по названию и разработчику (Google LLC).» under the results."""

        words, store_id = parse_query(self.query)
        if not words and not store_id:
            return ""
        visible = self._visible_items()
        if store_id:
            return "Совпадение по номеру из App Store." if visible else ""
        by_dev = [item.developer for item in visible if item.developer and words in item.developer.casefold()]
        if by_dev and len(set(by_dev)) == 1:
            return f"Совпадения по названию и разработчику ({html.escape(by_dev[0])})."
        if visible:
            return "Совпадения по названию и разработчику."
        return "В списке ничего не нашлось."

    def plan(self) -> SpacePlan:
        return plan_space(self.selected_items(), self.space)

    def group_total(self, group: str) -> int:
        return sum(1 for item in self._items if item.group == group)

    def rail_rows(self) -> list[dict[str, object]]:
        visible = self._visible_items_ignoring_rail()
        rows: list[dict[str, object]] = []
        chosen_all = sum(1 for item in visible if item.key in self._selected)
        rows.append(
            {
                "key": RAIL_ALL,
                "title": "Все",
                "count": len(visible),
                "sub": f"выбрано {chosen_all}",
                "zero": not visible,
                "on": self.rail == RAIL_ALL,
            }
        )
        for group in GROUP_ORDER:
            in_group = [item for item in visible if item.group == group]
            hideable = group in (GROUP_REGION, GROUP_NOPHONE) or (self.offline and group == GROUP_REMOVED)
            if hideable and not any(i.group == group for i in self._items):
                continue  # Ника: no classification / empty → no group at all (no «0»)
            if group == GROUP_NOPHONE:
                sub = OFFLINE_RAIL_SUB
            elif group == GROUP_REGION:
                sub = "нельзя выбрать"
            elif not in_group:
                sub = "—"
            else:
                sub = f"выбрано {sum(1 for item in in_group if item.key in self._selected)}"
            rows.append(
                {
                    "key": group,
                    "title": GROUP_TITLES[group],
                    "count": len(in_group),
                    "sub": sub,
                    "zero": not in_group,
                    "on": self.rail == group,
                }
            )
        # Concept order in the rail: all, removed, offloaded, region.
        order = {RAIL_ALL: 0, GROUP_REMOVED: 1, GROUP_NOPHONE: 1, "offloaded": 2, GROUP_REGION: 3}
        rows.sort(key=lambda row: order.get(str(row["key"]), 9))
        return rows

    def _visible_items_ignoring_rail(self) -> list[RestoreItem]:
        return [item for item in self._items if match_item(item, self.query) is not None]

    def rows(self) -> list[dict[str, object]]:
        """Flat rows for the list: a header per group, then its apps."""

        searching = bool(self.query.strip())
        visible = self._visible_items()
        out: list[dict[str, object]] = []
        for group in GROUP_ORDER:
            in_group = self._sorted([item for item in visible if item.group == group])
            if not in_group:
                continue
            total = self.group_total(group)
            state = self.group_state(group)
            if group == GROUP_NOPHONE:
                action = ""
            elif group == GROUP_REGION:
                action = "Подробнее"
            elif searching:
                action = "Снять найденные" if state == CHECK_ON else "Выбрать найденные"
            elif state == CHECK_ON:
                action = "Снять группу"
            else:
                action = f"Выбрать все {len(in_group)}"
            out.append(
                {
                    "kind": "header",
                    "key": f"group:{group}",
                    "group": group,
                    "title": GROUP_TITLES[group],
                    "countText": f"{len(in_group)} из {total}" if searching and len(in_group) != total else str(total),
                    "check": state,
                    "action": action,
                }
            )
            for item in in_group:
                match = match_item(item, self.query)
                checked = item.key in self._selected
                out.append(
                    {
                        "kind": "app",
                        "key": item.key,
                        "group": group,
                        "name": item.name,
                        "shortName": short_name_of(item.name),
                        "nameHtml": highlight_html(item.name, match, self.mark_color),
                        "developer": item.developer,
                        "storeId": item.store_id,
                        "bundleId": item.bundle_id,
                        "sizeText": format_size(item.size_bytes),
                        "sizeKnown": item.size_bytes is not None,
                        "note": item.note
                        if item.note
                        else ("" if item.size_bytes is not None else "размер узнаем при скачивании"),
                        "unverified": item.group == GROUP_NOPHONE,
                        "hasIpaHint": group == GROUP_REGION,
                        "check": CHECK_DISABLED if not item.selectable else (CHECK_ON if checked else CHECK_OFF),
                        "selected": checked,
                        "selectable": item.selectable,
                    }
                )
        return out
