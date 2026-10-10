"""Лицензионный гард AppRestore: можно ли взять бесплатную лицензию сейчас.

Автономный модуль без сторонних зависимостей (только стандартная библиотека).
Общий для GUI (Дима) и для bench (scripts/bench_restore.py): оба считают лимиты
по одному журналу, пишут один формат строки и используют ОДНУ межпроцессную
блокировку файла журнала, чтобы счётчик не гонялся.

Политика (жёсткая):
  * лицензию берём ТОЛЬКО для бесплатных приложений: price == 0 (цену берём из
    lookup ДО вызова purchase, по стране аккаунта). Платные — отказ.
  * не больше `daily_limit` лицензий за 24 часа и `total_limit` всего
    (по умолчанию 5 и 15); при превышении — отказ.
  * журнал licenses_acquired.jsonl — одна JSON-строка на взятую лицензию. БЕЗ
    Apple ID, паролей, токенов, UDID. Поля см. record_acquire().
  * `download` НИКОГДА не вызывается с `--purchase`; лицензия — отдельный шаг
    `ipatool purchase`.

Путь журнала (общий у GUI и bench):
    переменная окружения APPRESTORE_LICENSE_JOURNAL, иначе
    ~/.apprestore/licenses_acquired.jsonl  (см. default_journal_path()).

Межпроцессная блокировка:
    journal_lock(journal_path) — контекст-менеджер на отдельном файле
    `<journal>.lock` (fcntl.flock на POSIX, msvcrt на Windows). read_counts,
    check_can_acquire и record_acquire берут эту блокировку сами. Для атомарной
    операции «проверить лимит → purchase → записать» используйте
    acquire_and_record(), который держит блокировку на всё время.

Как подключить в GUI (installStore / кнопка «Получить») — атомарно:

    from license_guard import acquire_and_record

    def do_purchase() -> str:
        # вызвать реальный `ipatool purchase`. Вернуть:
        #   "acquired"           — purchase прошёл, лицензия добавлена;
        #   "purchase_uncertain" — упал по сети/таймауту, неясно (считаем в лимит);
        #   False/"failed"       — Apple отказала, лицензии нет (в журнал НЕ пишем).
        ...

    res = acquire_and_record(track_id, price, purchase=do_purchase,
                             bundle_id=bundle_id, storefront=storefront, mode="gui")
    if not res.allowed:
        show_error(res.reason)              # лимит/платное/нет цены
    elif not res.recorded:
        show_error("Apple не выдала лицензию")
    else:
        run_ipatool_download(track_id)      # обычный download, БЕЗ --purchase

Проверка лимита + purchase + запись проходят под одной блокировкой, поэтому GUI и
bench на одном журнале не превысят лимит.
"""

from __future__ import annotations

import contextlib
import datetime as dt
import json
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterator

try:  # POSIX (Linux, macOS)
    import fcntl
except ImportError:  # pragma: no cover - не-POSIX
    fcntl = None  # type: ignore[assignment]

try:  # Windows
    import msvcrt
except ImportError:  # pragma: no cover - не-Windows
    msvcrt = None  # type: ignore[assignment]

__all__ = ["Verdict", "AcquireResult", "check_can_acquire", "record_acquire",
           "read_counts", "acquire_and_record", "journal_lock",
           "default_journal_path", "DEFAULT_DAILY_LIMIT", "DEFAULT_TOTAL_LIMIT",
           "ACQUIRED_STATUSES"]

DEFAULT_DAILY_LIMIT = 5
DEFAULT_TOTAL_LIMIT = 15
JOURNAL_ENV = "APPRESTORE_LICENSE_JOURNAL"

# Статусы, при которых лицензия считается ВЗЯТОЙ (учитывается в лимите): успешная
# сделка, «взяли, но скачивание упало», и «purchase упал по сети/таймауту и неясно,
# прошла ли сделка» — при сомнении безопаснее считать (лицензия могла добавиться).
ACQUIRED_STATUSES = frozenset({"acquired", "acquired_download_failed", "purchase_uncertain"})


def default_journal_path() -> Path:
    """Тот же путь, что у GUI: $APPRESTORE_LICENSE_JOURNAL или ~/.apprestore/licenses_acquired.jsonl."""

    override = os.environ.get(JOURNAL_ENV)
    if override:
        return Path(override).expanduser()
    return Path.home() / ".apprestore" / "licenses_acquired.jsonl"


def _resolve(journal_path: Path | str | None) -> Path:
    return default_journal_path() if journal_path is None else Path(journal_path)


# ------------------------------------------------------------- блокировка файла


@contextlib.contextmanager
def journal_lock(journal_path: Path | str | None = None) -> Iterator[None]:
    """Эксклюзивная межпроцессная блокировка журнала через `<journal>.lock`.

    Блокируем отдельный lock-файл рядом, а не сам журнал, чтобы запись/ротация
    журнала не конфликтовали с дескриптором блокировки. На POSIX — fcntl.flock,
    на Windows — msvcrt.locking. Без обеих (экзотическая платформа) деградируем
    до отсутствия межпроцессной блокировки, но код продолжает работать.

    НЕ переиспользуйте вложенно в одном потоке: это один и тот же файловый замок,
    повторный захват заблокируется. Публичные функции устроены так, что не
    вкладывают журнальную блокировку друг в друга.
    """

    path = _resolve(journal_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    lock_path = path.with_name(path.name + ".lock")
    handle = open(lock_path, "a+")  # noqa: SIM115 - закрываем в finally
    try:
        _lock(handle)
        try:
            yield
        finally:
            _unlock(handle)
    finally:
        handle.close()


def _lock(handle) -> None:
    if fcntl is not None:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
    elif msvcrt is not None:  # pragma: no cover - Windows
        handle.seek(0)
        while True:
            try:
                msvcrt.locking(handle.fileno(), msvcrt.LK_LOCK, 1)
                return
            except OSError:
                time.sleep(0.1)  # LK_LOCK сам ретраит ~10с, затем пробуем снова


def _unlock(handle) -> None:
    if fcntl is not None:
        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
    elif msvcrt is not None:  # pragma: no cover - Windows
        try:
            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        except OSError:
            pass


@dataclass
class Verdict:
    allowed: bool
    reason: str
    used_today: int
    used_total: int

    def __bool__(self) -> bool:
        return self.allowed


@dataclass
class AcquireResult:
    allowed: bool            # прошла ли проверка лимита/цены
    recorded: bool           # записана ли лицензия в журнал
    status: str | None       # статус записи ("acquired"/"purchase_uncertain"/...) или None
    reason: str
    used_today: int
    used_total: int
    entry: dict[str, Any] | None = None

    def __bool__(self) -> bool:
        return self.recorded


def _now_utc() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


# ------------------------------------------------------------- чтение/подсчёт (без блокировки)


def _read_entries_unlocked(path: Path) -> list[dict[str, Any]]:
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

    Старые строки без поля ``status`` — это всегда взятая лицензия, считаются.
    Новые — по множеству ACQUIRED_STATUSES.
    """

    status = entry.get("status")
    if not status:
        return True
    return str(status) in ACQUIRED_STATUSES


def _read_counts_unlocked(path: Path, now: Callable[[], dt.datetime]) -> tuple[int, int]:
    entries = [e for e in _read_entries_unlocked(path) if _counts_toward_limit(e)]
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


def _check_unlocked(track_id: Any, price: Any, path: Path, daily_limit: int,
                    total_limit: int, now: Callable[[], dt.datetime]) -> Verdict:
    used_today, used_total = _read_counts_unlocked(path, now)
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


def _record_unlocked(track_id: Any, bundle_id: str | None, storefront: str | None,
                     path: Path, status: str, price: Any, mode: str | None,
                     app_id: Any, now: Callable[[], dt.datetime]) -> dict[str, Any]:
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
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(entry, ensure_ascii=False) + "\n")
    return entry


# ------------------------------------------------------------- публичный API (с блокировкой)


def read_counts(journal_path: Path | str | None = None, *,
                now: Callable[[], dt.datetime] = _now_utc) -> tuple[int, int]:
    """(использовано за 24 ч, использовано всего) среди ВЗЯТЫХ лицензий. Под блокировкой."""

    path = _resolve(journal_path)
    with journal_lock(path):
        return _read_counts_unlocked(path, now)


def check_can_acquire(track_id: Any, price: Any, *,
                      journal_path: Path | str | None = None,
                      daily_limit: int = DEFAULT_DAILY_LIMIT,
                      total_limit: int = DEFAULT_TOTAL_LIMIT,
                      now: Callable[[], dt.datetime] = _now_utc) -> Verdict:
    """Можно ли взять лицензию прямо сейчас. Не меняет журнал. Под блокировкой.

    Внимание о гонке: отдельный check + отдельный purchase + отдельный record НЕ
    атомарны между собой. Для атомарности используйте acquire_and_record().
    """

    path = _resolve(journal_path)
    with journal_lock(path):
        return _check_unlocked(track_id, price, path, daily_limit, total_limit, now)


def record_acquire(track_id: Any, bundle_id: str | None = None,
                   storefront: str | None = None, *,
                   journal_path: Path | str | None = None,
                   status: str = "acquired",
                   price: Any = None, mode: str | None = None,
                   app_id: Any = None,
                   now: Callable[[], dt.datetime] = _now_utc) -> dict[str, Any]:
    """Дописать взятую лицензию в журнал (под блокировкой). Возвращает строку.

    Единый формат строки (для GUI и bench):
      time       — ISO 8601, UTC;
      track_id   — основной ключ (числовой App Store ID, строкой);
      app_id     — алиас track_id (совместимость со старым bench);
      bundle_id  — bundle, если известен, иначе "";
      storefront — витрина/страна, если известна, иначе "";
      status     — "acquired" | "acquired_download_failed" | "purchase_uncertain";
      price      — цена из lookup (обычно 0.0) или null;
      mode       — "gui" | "mock" | "real" | ... или null.

    Никаких Apple ID, паролей, токенов, UDID.
    """

    path = _resolve(journal_path)
    with journal_lock(path):
        return _record_unlocked(track_id, bundle_id, storefront, path, status,
                                price, mode, app_id, now)


def _status_from_purchase(outcome: Any) -> str | None:
    """Из результата purchase-колбэка получить статус записи или None (не писать).

    Принимает: True/"ok"/"acquired" → "acquired"; "purchase_uncertain"/"uncertain"
    → "purchase_uncertain"; любой статус из ACQUIRED_STATUSES → он сам;
    False/None/"failed"/"refused" → None (лицензии нет, в журнал не пишем).
    """

    if outcome is True:
        return "acquired"
    if outcome in (False, None):
        return None
    text = str(outcome).strip().lower()
    if text in ("ok", "success", "acquired"):
        return "acquired"
    if text in ("uncertain", "purchase_uncertain"):
        return "purchase_uncertain"
    if text in ACQUIRED_STATUSES:
        return text
    return None  # "failed", "refused" и прочее — лицензия не взята


def acquire_and_record(track_id: Any, price: Any, *,
                       purchase: Callable[[], Any],
                       journal_path: Path | str | None = None,
                       daily_limit: int = DEFAULT_DAILY_LIMIT,
                       total_limit: int = DEFAULT_TOTAL_LIMIT,
                       bundle_id: str | None = None,
                       storefront: str | None = None,
                       mode: str | None = None,
                       app_id: Any = None,
                       now: Callable[[], dt.datetime] = _now_utc) -> AcquireResult:
    """Атомарно: проверить лимит → purchase() → записать. Всё под одной блокировкой.

    `purchase` — колбэк, выполняющий реальный `ipatool purchase` (или mock).
    Его результат трактуется _status_from_purchase(). Если проверка не прошла,
    purchase НЕ вызывается. Если purchase вернул «нет лицензии» — запись не
    делается. Блокировка файла журнала удерживается на всё время (включая
    purchase), поэтому параллельные GUI и bench не превысят лимит.
    """

    path = _resolve(journal_path)
    with journal_lock(path):
        verdict = _check_unlocked(track_id, price, path, daily_limit, total_limit, now)
        if not verdict.allowed:
            return AcquireResult(False, False, None, verdict.reason,
                                 verdict.used_today, verdict.used_total, None)
        outcome = purchase()
        status = _status_from_purchase(outcome)
        if status is None:
            return AcquireResult(True, False, None,
                                 "purchase не выдал лицензию",
                                 verdict.used_today, verdict.used_total, None)
        entry = _record_unlocked(track_id, bundle_id, storefront, path, status,
                                 price, mode, app_id, now)
        used_today, used_total = _read_counts_unlocked(path, now)
        return AcquireResult(True, True, status, "ok", used_today, used_total, entry)
