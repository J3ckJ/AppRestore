"""«Настройки» of the 4b window (02-picker §6b): «Искать в архиве» and
«Проверить обновления». Texts only; QSettings lives in ``window.py``."""

from __future__ import annotations

from typing import Any

#: QSettings("AppRestore", "AppRestore") key; default on (LEGAL §1.12 п.2).
ARCHIVE_KEY = "search/archive"
ARCHIVE_DEFAULT = True

TITLE = "Настройки"
CLOSE = "Закрыть"
SEARCH_TITLE = "Поиск"
ARCHIVE_SWITCH = "Искать в архиве"
#: Лена, LEGAL.md §1.12 п.2 — дословно (подпись переключателя и сноска «Найти»).
ARCHIVE_NOTE = (
    "Поиск удалённых приложений идёт через публичный архив Internet Archive (web.archive.org). "
    "Туда отправляется только текст запроса, ваш Apple ID — нет."
)
UPDATES_TITLE = "Обновления"
CHECK_UPDATES = "Проверить обновления"
#: Same words as the old window (main_window.check_updates).
UPDATE_BUSY = "Проверяю новую версию на GitHub…"
UPDATE_FAILED = "Не удалось проверить обновления. Проверьте интернет и повторите."

#: The Windows-only quiet link on the home screen, last after «Apple ID».
SETTINGS_LINK = "Настройки"


def update_status(info: Any) -> str:
    """Text after updater.check_for_update (old window's wording)."""

    if info is None:
        return UPDATE_FAILED
    if not getattr(info, "newer", False):
        return f"У вас последняя версия ({info.current}). На GitHub сейчас {info.latest}."
    return f"Доступна версия {info.latest}."


def settings_view(*, open_: bool, archive: bool, update_text: str, update_busy: bool) -> dict[str, object]:
    return {
        "open": open_,
        "title": TITLE,
        "close": CLOSE,
        "searchTitle": SEARCH_TITLE,
        "archiveSwitch": ARCHIVE_SWITCH,
        "archive": archive,
        "archiveNote": ARCHIVE_NOTE,
        "updatesTitle": UPDATES_TITLE,
        "checkUpdates": CHECK_UPDATES,
        "updateBusy": update_busy,
        "updateStatus": update_text,
    }
