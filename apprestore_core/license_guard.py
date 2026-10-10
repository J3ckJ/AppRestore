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

Поправки и гашения (журнал только дописывается):

    record_amend(entry["id"], "acquired")                  # uncertain -> acquired: 1
    record_amend(entry["id"], "refused")                   # uncertain -> refused: 0
    record_amend(entry["id"], "acquired_download_failed")  # acquired -> dl failed: 1
    record_void(entry["id"], "ошибочная запись")           # цепочка: 0, окончательно

В лимит идёт только ПОСЛЕДНИЙ статус цепочки amends (и только из ACQUIRED_STATUSES);
voids исходной гасит всю цепочку. Подробно — комментарий у VOID_STATUS.
"""

from __future__ import annotations

import contextlib
import datetime as dt
import json
import os
import time
import uuid
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
           "default_journal_path", "record_void", "record_amend", "DEFAULT_DAILY_LIMIT",
           "DEFAULT_TOTAL_LIMIT", "ACQUIRED_STATUSES", "VOID_STATUS"]

DEFAULT_DAILY_LIMIT = 5
DEFAULT_TOTAL_LIMIT = 15
JOURNAL_ENV = "APPRESTORE_LICENSE_JOURNAL"

# Статусы, при которых лицензия считается ВЗЯТОЙ (учитывается в лимите): успешная
# сделка, «взяли, но скачивание упало», и «purchase упал по сети/таймауту и неясно,
# прошла ли сделка» — при сомнении безопаснее считать (лицензия могла добавиться).
ACQUIRED_STATUSES = frozenset({"acquired", "acquired_download_failed", "purchase_uncertain"})

# Журнал только APPEND-ONLY (требование Лены): строки не удаляем и не правим.
# Исходная запись лицензии (record_acquire / acquire_and_record) начинает ЦЕПОЧКУ.
# Дальше к ней только ДОПИСЫВАЮТСЯ строки-ссылки (сами в лимит не идут никогда):
#
#   * ``voids``  (status="voided") — ГАСИТ ошибочную запись.
#       - ``voids: <id исходной>`` -> цепочка не считается, окончательно (поздние
#         amends её не оживляют);
#       - ``voids: <id amends-строки>`` -> отменяется ТОЛЬКО эта поправка, цепочка
#         возвращается к предыдущему статусу (ошибочную поправку гасим, а не лицензию);
#       - ``voids: {"time","track_id"}`` -> legacy: гасит старые строки БЕЗ id по паре.
#   * ``amends: <id>`` + ``status`` — МЕНЯЕТ статус цепочки (например
#     purchase_uncertain -> acquired / refused, acquired -> acquired_download_failed).
#     ``<id>`` — id исходной ИЛИ id другой amends-строки той же цепочки (amends на
#     amends). В лимит идёт ПОСЛЕДНИЙ действующий статус цепочки (по порядку строк
#     в файле = по времени, т.к. запись под journal_lock), и только если он в
#     ACQUIRED_STATUSES. Промежуточные статусы и сами amends-строки не считаются.
#     Время для суточного окна — время ИСХОДНОЙ записи.
#     Только по id: старые строки без id поправками не меняются (amends-словарь
#     игнорируется — по паре не догадываемся, только явный voids).
#
# Ссылка на несуществующий id игнорируется (и сама строка не считается).
# Порядок подсчёта: сначала свернуть каждую цепочку до последнего статуса (с учётом
# отменённых поправок), затем применить voids исходных, затем фильтр ACQUIRED_STATUSES.
VOID_STATUS = "voided"
AMEND_FIELD = "amends"
VOID_FIELD = "voids"


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


def _entry_key(entry: dict[str, Any]) -> tuple[str, str]:
    """Legacy-ключ строки для погашения: (time, track_id) как строки.

    Используется ТОЛЬКО для старых записей без поля ``id``.
    """

    return (str(entry.get("time", "")), str(entry.get("track_id", "")))


def _is_link(entry: dict[str, Any]) -> bool:
    """Строка-ссылка (гашение или поправка), а не исходная запись лицензии.

    Любая строка с ``voids``/``amends`` или со status="voided" (даже без корректной
    ссылки) — ссылка: в лимит сама не идёт.
    """

    return (VOID_FIELD in entry or AMEND_FIELD in entry
            or str(entry.get("status") or "") == VOID_STATUS)


def _live_entries(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Исходные записи, идущие в лимит, после сворачивания цепочек voids/amends.

    Возвращает КОПИИ исходных строк с итоговым статусом цепочки (поле ``status``),
    порядок — как в файле. Строки-ссылки сами никогда не возвращаются.
    """

    roots: list[dict[str, Any]] = []
    by_id: dict[str, int] = {}                     # id исходной -> индекс
    by_pair: dict[tuple[str, str], list[int]] = {}  # legacy (без id) -> индексы
    links: list[dict[str, Any]] = []
    for e in rows:
        if _is_link(e):
            links.append(e)
            continue
        index = len(roots)
        roots.append(dict(e))
        eid = e.get("id")
        if isinstance(eid, str) and eid:
            by_id.setdefault(eid, index)
        else:
            by_pair.setdefault(_entry_key(e), []).append(index)

    link_root: dict[str, int] = {}      # id строки-ссылки -> индекс исходной
    amend_ids: set[str] = set()         # id amends-строк (их можно отменить через voids)
    history: dict[int, list[tuple[str, str]]] = {}  # индекс -> [(id поправки, статус)]
    cancelled: set[str] = set()         # id отменённых поправок
    voided: set[int] = set()            # погашенные исходные

    def target(ref: Any) -> int | None:
        if isinstance(ref, str) and ref:
            if ref in by_id:
                return by_id[ref]
            return link_root.get(ref)
        return None

    for e in links:
        index: int | None = None
        if VOID_FIELD in e:
            ref = e.get(VOID_FIELD)
            if isinstance(ref, dict):
                key = (str(ref.get("time", "")), str(ref.get("track_id", "")))
                hit = by_pair.get(key, []) if key != ("", "") else []
                voided.update(hit)
                index = hit[0] if hit else None
            elif isinstance(ref, str) and ref in amend_ids:
                cancelled.add(ref)               # гасим ошибочную поправку, не лицензию
                index = link_root.get(ref)
            else:
                index = target(ref)
                if index is not None and isinstance(ref, str) and ref in by_id:
                    voided.add(index)
                # voids на id другой void-строки: ничего не меняем
        elif AMEND_FIELD in e:
            index = target(e.get(AMEND_FIELD))   # словарь (legacy) намеренно не понимаем
            status = str(e.get("status") or "")
            lid = e.get("id")
            if index is not None and status:
                key = lid if isinstance(lid, str) and lid else f"#anon{len(amend_ids)}"
                history.setdefault(index, []).append((key, status))
                if isinstance(lid, str) and lid:
                    amend_ids.add(lid)
        # status="voided" без ссылки — ничего не гасит
        lid = e.get("id")
        if index is not None and isinstance(lid, str) and lid:
            link_root.setdefault(lid, index)

    live: list[dict[str, Any]] = []
    for i, root in enumerate(roots):
        if i in voided:
            continue
        for amend_id, status in reversed(history.get(i, [])):
            if amend_id not in cancelled:
                root["status"] = status
                break
        if _counts_toward_limit(root):
            live.append(root)
    return live


def _read_counts_unlocked(path: Path, now: Callable[[], dt.datetime]) -> tuple[int, int]:
    entries = _live_entries(_read_entries_unlocked(path))
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
        "id": str(uuid.uuid4()),
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
      id         — uuid4 записи (строка); на него ссылаются `voids` (гашение,
                   record_void) и `amends` (смена статуса, record_amend);
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


def record_void(target_id: Any = None, reason: str = "", *,
                track_id: Any = None,
                legacy_match: tuple[Any, Any] | None = None,
                journal_path: Path | str | None = None,
                now: Callable[[], dt.datetime] = _now_utc) -> dict[str, Any]:
    """Дописать APPEND-ONLY строку status="voided", гасящую ошибочную запись.

    Исходную строку НЕ трогаем (журнал только дописывается). При подсчёте не
    считается ни сама voided-строка, ни погашенная исходная.

    Основной путь (новые записи с ``id``):
        record_void(target_id, reason, track_id=<для читаемости>)
      -> ссылка `voids` = <id исходной> (строка). Погашение однозначно даже если
      две попытки одного track_id записаны в ту же секунду.

    Legacy-путь (старые записи БЕЗ ``id``):
        record_void(None, reason, legacy_match=(orig_time, orig_track_id))
      -> ссылка `voids` = {"time", "track_id"} (словарь), сопоставление по паре.

    Схема voided-строки (для GUI/Димы):
      id       — uuid4 самой voided-строки;
      time     — ISO 8601 UTC, когда погасили;
      track_id — track_id исходной (для читаемости);
      status   — "voided";
      voids    — <id исходной> (строка) ЛИБО {"time","track_id"} (legacy-словарь);
                 <id amends-строки> отменяет только эту поправку;
                 `amends` — НЕ алиас voids: это смена статуса, см. record_amend();
      reason   — человекочитаемая причина.
    """

    if target_id is None and legacy_match is None:
        raise ValueError("record_void: нужен target_id (новый путь) или legacy_match=(time, track_id)")

    path = _resolve(journal_path)
    if target_id is not None:
        voids: Any = str(target_id)
        tid = str(track_id) if track_id is not None else ""
    else:
        orig_time, orig_track_id = legacy_match  # type: ignore[misc]
        voids = {"time": str(orig_time), "track_id": str(orig_track_id)}
        tid = str(track_id if track_id is not None else orig_track_id)
    entry = {
        "id": str(uuid.uuid4()),
        "time": now().isoformat(timespec="seconds"),
        "track_id": tid,
        "status": VOID_STATUS,
        "voids": voids,
        "reason": reason,
    }
    with journal_lock(path):
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(entry, ensure_ascii=False) + "\n")
    return entry


def record_amend(target_id: Any, new_status: str, reason: str = "", *,
                 track_id: Any = None,
                 journal_path: Path | str | None = None,
                 now: Callable[[], dt.datetime] = _now_utc) -> dict[str, Any] | None:
    """Дописать APPEND-ONLY строку ``amends: <id>`` с новым статусом цепочки.

    Пример: purchase упал по таймауту (purchase_uncertain), потом выяснили исход:
        record_amend(entry["id"], "acquired")   # лицензия есть -> считается 1
        record_amend(entry["id"], "refused")    # Apple отказала -> не считается

    ``target_id`` — id исходной записи ИЛИ id другой amends-строки той же
    цепочки. В лимит идёт последний статус цепочки (если он в ACQUIRED_STATUSES),
    сама поправка не считается; суточное окно — по времени исходной записи.
    Исходную строку НЕ трогаем. Только для записей с ``id``: если ``target_id``
    пуст или такого id в журнале нет — ничего не пишем и возвращаем None (старые
    строки без id меняются только гашением record_void(..., legacy_match=...)).
    Проверка id и запись — под одной journal_lock.

    Схема amends-строки:
      id       — uuid4 самой поправки (на неё можно сослаться amends/voids);
      time     — ISO 8601 UTC, когда поправили;
      track_id — track_id исходной (для читаемости) или "";
      status   — новый статус цепочки (любой непустой; в лимит — ACQUIRED_STATUSES);
      amends   — <id цели> (строка);
      reason   — человекочитаемая причина.
    """

    if target_id is None or not str(target_id):
        return None
    status = str(new_status or "").strip()
    if not status:
        raise ValueError("record_amend: нужен непустой new_status")
    path = _resolve(journal_path)
    entry = {
        "id": str(uuid.uuid4()),
        "time": now().isoformat(timespec="seconds"),
        "track_id": str(track_id) if track_id is not None else "",
        "status": status,
        AMEND_FIELD: str(target_id),
        "reason": reason,
    }
    with journal_lock(path):
        known = {e.get("id") for e in _read_entries_unlocked(path)}
        if str(target_id) not in known:
            return None
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(entry, ensure_ascii=False) + "\n")
    return entry


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
