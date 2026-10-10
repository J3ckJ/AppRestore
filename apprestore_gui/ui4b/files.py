"""«Файлы IPA» in 4b (like 0.3.2's «Библиотека IPA»): the IPA files already on this
computer, «Выгрузить с устройства» and «Выбрать на ПК».

Reuses QuickSession as is: ``libraryFiles`` / ``loadLibrary`` (service.scan_local),
``phoneApps`` / ``saveCopies`` (download_to_library through license_gate, the same
gate as «Поставить»), ``installSaved`` via the controller's usual IPA path. Nothing new
legally: the files are the user's own. Pure view functions, tested without Qt.
"""

from __future__ import annotations

import os
from collections.abc import Iterable, Mapping
from pathlib import Path

from .formatting import format_size

DONE = "Готово"

TITLE = "Файлы IPA"
EXPORT_TITLE = "Выгрузить с устройства"
EXPORT = "Выгрузить с устройства"
PICK = "Выбрать на ПК"
INSTALL = "Поставить"
BACK = "Назад"
EMPTY = "На этом компьютере пока нет файлов IPA. Их можно выгрузить с подключённого телефона или выбрать файл на компьютере."
EXPORT_EMPTY = "Подключите телефон кабелем: здесь появятся приложения, копию которых можно сохранить."
EXPORT_HINT = "Копия сохраняется на этот компьютер, чтобы потом поставить её обратно."


def _size(path: str) -> str:
    try:
        size = os.path.getsize(path)
    except OSError:
        return ""
    return format_size(size) if size > 0 else ""


def library_rows(rows: Iterable[Mapping[str, object]]) -> list[dict[str, str]]:
    """QuickSession.libraryFiles → rows: icon keys, name, version, size."""

    out: list[dict[str, str]] = []
    for r in rows:
        path = str(r.get("path") or "")
        if not path:
            continue
        detail = str(r.get("detail") or "")
        version = "" if detail == Path(path).name else detail
        meta = " · ".join(x for x in (version, _size(path)) if x)
        out.append({
            "key": path,
            "path": path,
            "name": str(r.get("name") or Path(path).stem),
            "meta": meta,
            "storeId": str(r.get("storeId") or ""),
            "bundleId": str(r.get("bundleId") or ""),
        })
    return out


def export_rows(apps: Iterable[Mapping[str, object]], chosen: set[str]) -> list[dict[str, object]]:
    out: list[dict[str, object]] = []
    for a in apps:
        bid = str(a.get("bundleId") or "")
        if not bid:
            continue
        out.append({
            "key": bid,
            "name": str(a.get("name") or "Приложение"),
            "meta": str(a.get("detail") or ""),
            "storeId": str(a.get("storeId") or ""),
            "bundleId": bid,
            "checked": bid in chosen,
        })
    return out


def _root_label(index: int, root: Path, imazing: set[str], downloads: str, itunes: str) -> str:
    key = os.path.normcase(os.path.abspath(root))
    if index == 0:
        return "AppRestore"
    if key in imazing:
        return "iMazing"
    if key == downloads:
        return "«Загрузках»"
    if key == itunes:
        return "iTunes"
    return f"«{root.name or root}»"


def labelled_roots(roots: list[Path]) -> list[tuple[str, Path]]:
    """paths.ipa_search_roots(library) → (label, folder): the folders the scan really uses
    (AppRestore's library first, iMazing, Downloads, iTunes «Mobile Applications», extra)."""

    from apprestore_core.paths import imazing_apps_dirs

    home = Path.home()
    imazing = {os.path.normcase(os.path.abspath(p)) for p in imazing_apps_dirs()}
    downloads = os.path.normcase(os.path.abspath(home / "Downloads"))
    itunes = os.path.normcase(os.path.abspath(home / "Music" / "iTunes" / "iTunes Media" / "Mobile Applications"))
    return [(_root_label(i, r, imazing, downloads, itunes), r) for i, r in enumerate(roots)]


def _join(labels: list[str]) -> str:
    labels = list(dict.fromkeys(labels))
    if not labels:
        return ""
    text = labels[0] if len(labels) == 1 else ", ".join(labels[:-1]) + " и " + labels[-1]
    return "в папке " + text


def where_line(paths: list[str], roots: list[tuple[str, Path]]) -> str:
    """Quiet line under «Файлы IPA»: where the files were found (only folders that
    really hold a listed file); with no files — which folders were looked at."""

    def under(path: str, root: Path) -> bool:
        try:
            return os.path.commonpath([os.path.abspath(path), os.path.abspath(root)]) == os.path.abspath(root)
        except ValueError:
            return False

    if paths:
        found = [label for label, root in roots if any(under(p, root) for p in paths)]
        return f"Нашли на этом компьютере: {_join(found)}" if found else ""
    looked = _join([label for label, _ in roots])
    return f"Ищем на этом компьютере: {looked}" if looked else ""


def files_view(*, open_: bool, mode: str, library: list[dict[str, str]], export: list[dict[str, object]],
               note: str, busy: bool, connected: bool, where: str = "") -> dict[str, object]:
    exporting = mode == "export"
    n = sum(1 for r in export if r.get("checked"))
    if exporting:
        return {
            "open": open_, "mode": "export", "title": EXPORT_TITLE, "close": DONE, "where": "",
            "rows": export, "empty": "" if export else EXPORT_EMPTY, "note": note or EXPORT_HINT,
            "busy": busy, "secondary": BACK, "secondaryEnabled": not busy,
            "primary": f"Сохранить {n}" if n > 1 else "Сохранить", "primaryEnabled": bool(n) and not busy,
        }
    return {
        "open": open_, "mode": "list", "title": TITLE, "close": DONE, "where": where,
        "rows": library, "empty": "" if library else EMPTY, "note": note,
        "busy": busy, "secondary": EXPORT, "secondaryEnabled": connected and not busy,
        "primary": PICK, "primaryEnabled": not busy, "install": INSTALL,
    }
