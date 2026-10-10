"""4b on an ipatool without AppRestore's patches 0001/0003 (Ника's texts).

Free licenses are not taken then (the gate's preflight refuses anyway): removed
apps that are not on the account are marked «Нужен дополнительный компонент»
with «Как установить»; technical details only behind «Подробнее». Offloaded and
already purchased apps go as usual. No consent sheet, no error screen: one
quiet line under the main button.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import replace
from pathlib import Path

from apprestore_gui.ui4b.catalog import ACTION_NONE, ACTION_STORE, GROUP_REGION, GROUP_REMOVED, RestoreItem

COMPONENT_NOTE = "Нужен дополнительный компонент"
HOWTO_LINK = "Как установить"
DETAILS_LINK = "Подробнее"
QUIET_TEXT = "Удалённые из App Store пока не вернуть: нужен дополнительный компонент."
#: The quiet line under the button (02/01 spec): the link is inline, bold.
QUIET_LINE = f'{QUIET_TEXT} <a href="{HOWTO_LINK}"><b>{HOWTO_LINK}</b></a>' 


def details_text(missing: Iterable[str]) -> str:
    """«Подробнее» — the only place with technical words (02-picker §2a)."""

    missing = tuple(missing)
    what = {
        "0001": "без дополнения 0001 (страна аккаунта: цену приложения проверить нельзя)",
        "0002": "без дополнения 0002 (весь список покупок одним запросом)",
        "0003": "без дополнения 0003 (пароль связки ключей через stdin)",
    }
    parts = [what[name] for name in missing if name in what] or ["без нужных дополнений"]
    return (
        "Для удалённых из App Store AppRestore использует ipatool со своими дополнениями. "
        "Сейчас установлен ipatool " + " и ".join(parts) + ", поэтому новые бесплатные лицензии "
        "не берутся. Сгруженные и уже купленные приложения это не затрагивает."
    )


def needs_component(item: RestoreItem, owned: set[str] | None) -> bool:
    if not item.store_id or (owned is not None and item.store_id in owned):
        return False
    # region items are only a classification of removed ones (region_probe is off)
    return item.group == GROUP_REGION or (item.group == GROUP_REMOVED and item.action == ACTION_STORE)


def mark(items: Iterable[RestoreItem], owned: Iterable[str] | None, missing: Iterable[str]) -> list[RestoreItem]:
    """Removed apps not on the account become unselectable with the note."""

    items = list(items)
    if not tuple(missing):
        return items
    owned_ids = None if owned is None else {str(s) for s in owned}
    return [
        # region_probe is off without 0001: the region group is folded back (hidden)
        replace(item, group=GROUP_REMOVED, action=ACTION_NONE, note=COMPONENT_NOTE)
        if needs_component(item, owned_ids) else item
        for item in items
    ]


def howto_path() -> Path | None:
    """packaging/BUILD-ipatool.md (Макс) or docs/RUN-FROM-SOURCE.md; None in a build."""

    from apprestore_core.paths import project_root

    docs = Path(project_root()) / "docs"
    for path in (Path(project_root()) / "packaging" / "BUILD-ipatool.md", docs / "RUN-FROM-SOURCE.md"):
        if path.is_file():
            return path
    return None
