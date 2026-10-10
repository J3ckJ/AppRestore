"""Лицензионный гард AppRestore: можно ли взять бесплатную лицензию сейчас.

Автономный модуль без зависимостей (только стандартная библиотека). Логика
общая для GUI (Дима) и для bench (scripts/bench_restore.py): оба считают лимиты
по одному журналу и пишут один формат строки.

Политика (жёсткая):
  * лицензию берём ТОЛЬКО для бесплатных приложений: price == 0 (цену берём из
    lookup ДО вызова purchase, по стране аккаунта). Платные — отказ.
  * не больше `daily_limit` лицензий за 24 часа и `total_limit` всего
    (по умолчанию 5 и 15); при превышении — отказ.
  * журнал licenses_acquired.jsonl — одна JSON-строка на взятую лицензию. БЕЗ
    Apple ID, паролей, токенов, UDID. Поля см. record_acquire().
  * `download` НИКОГДА не вызывается с `--purchase`; лицензия — отдельный шаг
    `ipatool purchase`, и только после allowed=True. Если purchase прошёл, а
    скачивание упало — запись всё равно делается (status="acquired_download_failed"),
    потому что лицензия на аккаунт уже добавлена и должна считаться в лимите.

Путь журнала (общий у GUI и bench):
    переменная окружения APPRESTORE_LICENSE_JOURNAL, иначе
    ~/.apprestore/licenses_acquired.jsonl  (см. default_journal_path()).

LEGAL: получение лицензии меняет историю аккаунта — только бесплатные, с согласия
владельца Apple ID и в пределах лимита. Каждый механизм — к правовой проверке Лены.

Как подключить в GUI (installStore / кнопка «Получить»):

    from license_guard import check_can_acquire, record_acquire, default_journal_path

    journal = default_journal_path()   # ~/.apprestore/licenses_acquired.jsonl или $APPRESTORE_LICENSE_JOURNAL

    # 1. ДО purchase: price берём из lookup по стране аккаунта; track_id — числовой App Store ID.
    verdict = check_can_acquire(track_id, price, journal_path=journal)
    if not verdict.allowed:
        show_error(verdict.reason)           # напр. «лимит лицензий за сутки исчерпан: 5/5»
        return

    # 2. Только теперь — ipatool purchase (без --purchase у download!).
    purchase_ok = run_ipatool_purchase(track_id)

    # 3. Затем обычный download БЕЗ --purchase. По итогу — запись в журнал.
    if purchase_ok:
        download_ok = run_ipatool_download(track_id)   # без --purchase
        record_acquire(track_id, bundle_id=bundle_id, storefront=storefront,
                       price=price, journal_path=journal,
                       status="acquired" if download_ok else "acquired_download_failed")

check_can_acquire не меняет журнал; record_acquire только дописывает строку.
"""

from __future__ import annotations

import datetime as dt
import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

__all__ = ["Verdict", "check_can_acquire", "record_acquire", "read_counts",
           "default_journal_path", "DEFAULT_DAILY_LIMIT", "DEFAULT_TOTAL_LIMIT",
           "ACQUIRED_STATUSES"]

DEFAULT_DAILY_LIMIT = 5
DEFAULT_TOTAL_LIMIT = 15
JOURNAL_ENV = "APPRESTORE_LICENSE_JOURNAL"

# Статусы, при которых лицензия считается ВЗЯТОЙ (учитывается в лимите). И успешная
# установка, и «взяли, но скачивание упало» — лицензия на аккаунт уже добавлена.
ACQUIRED_STATUSES = frozenset({"acquired", "acquired_download_failed"})


def default_journal_path() -> Path:
    """Общий путь журнала: $APPRESTORE_LICENSE_JOURNAL или ~/.apprestore/licenses_acquired.jsonl."""

    override = os.environ.get(JOURNAL_ENV)
    if override:
        return Path(override).expanduser()
    return Path.home() / ".apprestore" / "licenses_acquired.jsonl"


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


def _resolve(journal_path: Path | str | None) -> Path:
    return default_journal_path() if journal_path is None else Path(journal_path)


def _read_entries(journal_path: Path | str | None) -> list[dict[str, Any]]:
    path = _resolve(journal_path)
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


def _counts_toward_limit(entry: dict[str, Any]) -> bool:
    """Запись учитывается в лимите, если лицензия была взята.

    Старые строки без поля ``status`` — это всегда взятая лицензия (так писал
    прежний формат), поэтому считаются. Новые — по множеству ACQUIRED_STATUSES.
    """

    status = entry.get("status")
    if not status:
        return True
    return str(status) in ACQUIRED_STATUSES


def read_counts(journal_path: Path | str | None = None, *,
                now: Callable[[], dt.datetime] = _now_utc) -> tuple[int, int]:
    """(использовано за последние 24 ч, использовано всего) среди ВЗЯТЫХ лицензий."""

    entries = [e for e in _read_entries(journal_path) if _counts_toward_limit(e)]
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
                      journal_path: Path | str | None = None,
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
                   journal_path: Path | str | None = None,
                   status: str = "acquired",
                   price: Any = None, mode: str | None = None,
                   app_id: Any = None,
                   now: Callable[[], dt.datetime] = _now_utc) -> dict[str, Any]:
    """Дописать взятую лицензию в журнал. Возвращает записанную строку.

    Единый формат строки (для GUI и bench):
      time       — ISO 8601, UTC;
      track_id   — основной ключ (числовой App Store ID, строкой);
      app_id     — алиас track_id (совместимость со старым bench);
      bundle_id  — bundle, если известен, иначе "";
      storefront — витрина/страна, если известна, иначе "";
      status     — "acquired" | "acquired_download_failed";
      price      — цена из lookup (обычно 0.0) или null;
      mode       — "gui" | "mock" | "real" | ... или null.

    Никаких Apple ID, паролей, токенов, UDID.
    """

    track = str(track_id) if track_id is not None else ""
    alias = str(app_id) if app_id is not None else track
    entry = {
        "time": now().isoformat(timespec="seconds"),
        "track_id": track,
        "app_id": alias,
        "bundle_id": bundle_id or "",
        "storefront": storefront or "",
        "status": status,
        "price": _coerce_price(price),
        "mode": mode,
    }
    path = _resolve(journal_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(entry, ensure_ascii=False) + "\n")
    return entry
