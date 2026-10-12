"""Plain-language journal for the 4b window.

The current install queue, then licenses Apple has issued in this program.
No Apple ID, password, token, storefront, or price.
"""

from __future__ import annotations

from pathlib import Path

from apprestore_core.license_gate import journal_path
from apprestore_core.license_guard import _live_entries, _read_entries_unlocked

_LICENSE_STATUS = {
    "acquired": "Лицензия получена",
    "acquired_download_failed": "Лицензия есть, скачать не удалось",
    "purchase_uncertain": "Неясно, выдала ли Apple лицензию",
}

_LICENSE_LIMIT = 30


def _when(value: object) -> str:
    text = str(value or "").strip()
    if len(text) >= 16 and text[4] == "-" and text[10] in "T ":
        return text[:16].replace("T", " ")
    return ""


def read_license_rows(path: Path | None = None) -> list[dict[str, object]]:
    """Live license rows after voids and amends. [] when the file is missing."""

    target = journal_path() if path is None else path
    try:
        rows = _read_entries_unlocked(Path(target))
    except OSError:
        return []
    return [dict(row) for row in _live_entries(rows)]


def journal_rows(
    queue_rows: list[dict[str, object]],
    license_rows: list[dict[str, object]],
) -> list[dict[str, str]]:
    """Current run first, then the newest licenses. Unknown statuses are skipped."""

    rows: list[dict[str, str]] = []
    for row in queue_rows:
        rows.append(
            {
                "name": str(row.get("name") or "Приложение"),
                "detail": str(row.get("detail") or ""),
                "kind": "run",
            }
        )
    licenses: list[dict[str, str]] = []
    for entry in license_rows:
        status = _LICENSE_STATUS.get(str(entry.get("status") or ""))
        if not status:
            continue
        bundle = str(entry.get("bundle_id") or "").strip()
        track = str(entry.get("track_id") or "").strip()
        name = bundle or (f"приложение {track}" if track else "приложение")
        when = _when(entry.get("time"))
        licenses.append(
            {
                "name": name,
                "detail": f"{status} · {when}" if when else status,
                "kind": "license",
            }
        )
    rows.extend(reversed(licenses[-_LICENSE_LIMIT:]))
    return rows


def journal_view(
    *,
    open_: bool,
    queue_rows: list[dict[str, object]],
    license_rows: list[dict[str, object]] | None = None,
) -> dict[str, object]:
    """Sheet payload. The license file is read only while the sheet is open."""

    if license_rows is None:
        license_rows = read_license_rows() if open_ else []
    return {
        "open": bool(open_),
        "title": "Журнал",
        "close": "Закрыть",
        "empty": "Пока записей нет. Здесь ход текущей установки и лицензии, которые Apple выдала в этом окне.",
        "rows": journal_rows(queue_rows, license_rows),
    }
