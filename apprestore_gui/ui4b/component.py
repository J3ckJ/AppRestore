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

from apprestore_gui.ui4b.catalog import ACTION_NONE, ACTION_STORE, GROUP_REMOVED, RestoreItem

COMPONENT_NOTE = "Нужен дополнительный компонент"
HOWTO_LINK = "Как установить"
DETAILS_LINK = "Подробнее"
QUIET_LINE = "Для части приложений нужен дополнительный компонент."
_PATCH_WHAT = {
    "0001": "0001 — страна аккаунта (auth info)",
    "0003": "0003 — пароль связки ключей через stdin",
}


def details_text(missing: Iterable[str]) -> str:
    parts = [_PATCH_WHAT.get(name, name) for name in missing]
    return (
        "Нужна сборка ipatool 2.6.0 с патчами AppRestore: " + "; ".join(parts) + ". "
        "Без них программа не берёт бесплатные лицензии. Сгруженные и уже купленные "
        "приложения возвращаются как обычно."
    )


def needs_component(item: RestoreItem, owned: set[str] | None) -> bool:
    return (
        item.group == GROUP_REMOVED
        and item.action == ACTION_STORE
        and bool(item.store_id)
        and (owned is None or item.store_id not in owned)
    )


def mark(items: Iterable[RestoreItem], owned: Iterable[str] | None, missing: Iterable[str]) -> list[RestoreItem]:
    """Removed apps not on the account become unselectable with the note."""

    items = list(items)
    if not tuple(missing):
        return items
    owned_ids = None if owned is None else {str(s) for s in owned}
    return [
        replace(item, action=ACTION_NONE, note=COMPONENT_NOTE) if needs_component(item, owned_ids) else item
        for item in items
    ]


def howto_path() -> Path | None:
    """BUILD-ipatool.md (Макс) or RUN-FROM-SOURCE.md in the checkout; None in a build."""

    from apprestore_core.paths import project_root

    docs = Path(project_root()) / "docs"
    for name in ("BUILD-ipatool.md", "RUN-FROM-SOURCE.md"):
        if (docs / name).is_file():
            return docs / name
    return None
