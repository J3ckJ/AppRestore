"""«Восстановить из истории»: the signed-in Apple ID's whole purchase list.

The rows come from ``list-purchases --all`` (``purchases.py``). This module
does not talk to Apple and does not buy anything. «Восстановить» is the usual
restore of an app that is already on the account.
"""

from __future__ import annotations

import re

TITLE = "Восстановить из истории"
CLOSE = "Закрыть"
ACTION = "Восстановить"
PLACEHOLDER = "Название в истории покупок"
SIGNIN_TEXT = "Войдите в Apple ID, чтобы увидеть историю покупок"
SIGNIN_LINK = "Войти"
EMPTY_TEXT = "В этом Apple ID пока нет покупок"
NO_MATCH = "В истории нет такого названия"
LOADING = "Загружаем историю покупок…"
FAILED = "Историю покупок не удалось обновить."


def _purchase_word(count: int) -> str:
    n = abs(count) % 100
    tail = n % 10
    if 11 <= n <= 14 or tail == 0 or tail >= 5:
        return "покупок"
    if tail == 1:
        return "покупка"
    return "покупки"


def _norm(text: object) -> str:
    return str(text or "").casefold().replace("ё", "е")


def _date(value: object) -> str:
    text = str(value or "")[:10]
    match = re.fullmatch(r"(\d{4})-(\d{2})-(\d{2})", text)
    if match is None:
        return ""
    year, month, day = match.groups()
    return f"{day}.{month}.{year}"


def _rows(items: list[dict[str, object]], query: str) -> list[dict[str, object]]:
    needle = _norm(query).strip()
    seen: set[str] = set()
    picked: list[dict[str, object]] = []
    for item in items:
        store_id = str(item.get("trackId") or item.get("track_id") or "").strip()
        if not store_id.isdigit() or store_id in seen:
            continue
        name = str(item.get("name") or "").strip() or str(item.get("bundleId") or "").strip() or "Приложение"
        bundle = str(item.get("bundleId") or item.get("bundle_id") or "")
        if needle and needle not in _norm(name) and needle not in _norm(bundle) and needle not in store_id:
            continue
        seen.add(store_id)
        raw_date = str(item.get("date") or item.get("purchase_date") or "")[:10]
        shown = _date(raw_date)
        picked.append({
            "kind": "app",
            "storeId": store_id,
            "bundleId": bundle,
            "name": name,
            "developer": f"куплено {shown}" if shown else "",
            "action": ACTION,
            "enabled": True,
            "_date": raw_date,
        })
    picked.sort(key=lambda row: _norm(row["name"]))
    picked.sort(key=lambda row: str(row["_date"]), reverse=True)
    for row in picked:
        row.pop("_date", None)
    return picked


def history_view(
    items: list[dict[str, object]],
    *,
    signed_in: bool,
    busy: bool = False,
    failed: bool = False,
    query: str = "",
) -> dict[str, object]:
    """The sheet. Unsigned: no rows from a previous account."""

    rows = _rows(items, query) if signed_in else []
    if not signed_in:
        empty, empty_link = SIGNIN_TEXT, SIGNIN_LINK
    elif busy and not rows:
        empty, empty_link = "", ""
    elif query.strip() and not rows:
        empty, empty_link = NO_MATCH, ""
    elif not rows:
        empty, empty_link = EMPTY_TEXT, ""
    else:
        empty, empty_link = "", ""
    count = len(rows)
    if not signed_in:
        lead = ""
    elif busy and count:
        lead = f"Загружено {count}. История ещё обновляется."
    elif busy:
        lead = LOADING
    elif failed and count:
        lead = f"{FAILED} Показано сохранённое: {count}."
    elif failed:
        lead = FAILED
    elif query.strip() and count:
        lead = f"Найдено {count}"
    elif count:
        lead = f"{count} {_purchase_word(count)} в истории"
    else:
        lead = ""
    return {
        "title": TITLE,
        "close": CLOSE,
        "placeholder": PLACEHOLDER,
        "lead": lead,
        "spinner": LOADING if busy and rows else "",
        "empty": empty,
        "emptyLink": empty_link,
        "rows": rows,
    }
