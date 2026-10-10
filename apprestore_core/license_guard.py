"""Лицензионный гард AppRestore: можно ли взять бесплатную лицензию сейчас.

Автономный модуль без зависимостей (только стандартная библиотека). Логика
вынесена из scripts/bench_restore.py (ветка ipatool/bench), чтобы GUI (Дима) и
bench считали лимиты одинаково по одному журналу.

Политика (жёсткая):
  * лицензию берём ТОЛЬКО для бесплатных приложений: price == 0 (цену берём из
    lookup ДО вызова purchase). Платные — отказ.
  * не больше `daily_limit` лицензий за последние 24 часа и `total_limit` всего
    (по умолчанию 5 и 15); при превышении — отказ.
  * журнал licenses_acquired.jsonl — одна JSON-строка на взятую лицензию:
    time (ISO, UTC), track_id, bundle_id, storefront. БЕЗ Apple ID, паролей,
    токенов, UDID.
  * `download` НИКОГДА не вызывается с `--purchase`; лицензия — отдельный шаг
    `ipatool purchase`, и только после allowed=True.

LEGAL: получение лицензии меняет историю аккаунта — только бесплатные, с согласия
владельца Apple ID и в пределах лимита. Каждый механизм — к правовой проверке Лены.

Как подключить в GUI (installStore / кнопка «Получить»):

    from license_guard import check_can_acquire, record_acquire

    JOURNAL = Path.home() / ".apprestore" / "licenses_acquired.jsonl"

    # 1. ДО purchase: price берём из lookup (iTunes/ipatool), track_id — числовой App Store ID.
    verdict = check_can_acquire(track_id, price, journal_path=JOURNAL)
    if not verdict.allowed:
        show_error(verdict.reason)           # например «лимит за сутки исчерпан: 5/5»
        return

    # 2. Только теперь — ipatool purchase (без --purchase у download!).
    run_ipatool_purchase(track_id)

    # 3. После успешного purchase — записать в журнал (для следующего подсчёта лимита).
    record_acquire(track_id, bundle_id=bundle_id, storefront=storefront, journal_path=JOURNAL)

check_can_acquire не меняет журнал; record_acquire только дописывает строку.
Оба потокобезопасны на уровне процесса (используйте один путь журнала).
"""

from __future__ import annotations

import datetime as dt
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

__all__ = ["Verdict", "check_can_acquire", "record_acquire", "read_counts",
           "DEFAULT_DAILY_LIMIT", "DEFAULT_TOTAL_LIMIT"]

DEFAULT_DAILY_LIMIT = 5
DEFAULT_TOTAL_LIMIT = 15


@dataclass
class Verdict:
    allowed: bool
    reason: str
    used_today: int
    used_total: int

    def __bool__(self) -> bool:  # удобно: `if check_can_acquire(...):`
        return self.allowed


def _now_utc() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def _read_entries(journal_path: Path | str) -> list[dict[str, Any]]:
    path = Path(journal_path)
    if not path.is_file():
        return []
    entries: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            item = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(item, dict):
            entries.append(item)
    return entries


def read_counts(journal_path: Path | str, *,
                now: Callable[[], dt.datetime] = _now_utc) -> tuple[int, int]:
    """(использовано за последние 24 ч, использовано всего) по журналу."""

    entries = _read_entries(journal_path)
    since = now() - dt.timedelta(hours=24)
    used_today = 0
    for item in entries:
        raw = str(item.get("time", ""))
        try:
            when = dt.datetime.fromisoformat(raw)
        except ValueError:
            used_today += 1  # битая/без даты запись считается свежей: лимит строже
            continue
        if when.tzinfo is None:
            when = when.replace(tzinfo=dt.timezone.utc)
        if when >= since:
            used_today += 1
    return used_today, len(entries)


def _coerce_price(price: Any) -> float | None:
    if price is None:
        return None
    try:
        return float(price)
    except (TypeError, ValueError):
        return None


def check_can_acquire(track_id: Any, price: Any, *,
                      journal_path: Path | str,
                      daily_limit: int = DEFAULT_DAILY_LIMIT,
                      total_limit: int = DEFAULT_TOTAL_LIMIT,
                      now: Callable[[], dt.datetime] = _now_utc) -> Verdict:
    """Можно ли взять лицензию на `track_id` с ценой `price` прямо сейчас.

    Не меняет журнал. Порядок проверок: есть track_id → цена известна → цена==0 →
    лимит всего → лимит за сутки. Первый сработавший запрет и возвращается.
    """

    used_today, used_total = read_counts(journal_path, now=now)

    track = str(track_id).strip() if track_id is not None else ""
    if not track or not track.isdigit():
        return Verdict(False, "нет числового App Store ID (track_id) для purchase",
                       used_today, used_total)

    value = _coerce_price(price)
    if value is None:
        return Verdict(False, "цена не подтверждена через lookup — лицензию не берём",
                       used_today, used_total)
    if value > 0:
        return Verdict(False, f"приложение платное (price={value}) — лицензию не берём",
                       used_today, used_total)

    if used_total >= total_limit:
        return Verdict(False, f"лимит лицензий исчерпан: всего {used_total}/{total_limit}",
                       used_today, used_total)
    if used_today >= daily_limit:
        return Verdict(False, f"лимит лицензий за сутки исчерпан: {used_today}/{daily_limit}",
                       used_today, used_total)

    return Verdict(True, "ok", used_today, used_total)


def record_acquire(track_id: Any, bundle_id: str | None = None,
                   storefront: str | None = None, *,
                   journal_path: Path | str,
                   now: Callable[[], dt.datetime] = _now_utc) -> dict[str, Any]:
    """Дописать взятую лицензию в журнал. Возвращает записанную строку.

    Пишет только безопасные поля: time (ISO, UTC), track_id, bundle_id,
    storefront. Никаких Apple ID, паролей, токенов, UDID.
    """

    entry = {
        "time": now().isoformat(timespec="seconds"),
        "track_id": str(track_id) if track_id is not None else "",
        "bundle_id": bundle_id or "",
        "storefront": storefront or "",
    }
    path = Path(journal_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(entry, ensure_ascii=False) + "\n")
    return entry
