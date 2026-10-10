"""region_probe — подпись «нет в регионе» / «удалено из App Store» по публичному lookup.

Правовая рамка: LEGAL.md §1.10 (Лена, 10.10.2026). Как модуль её держит:

1. АНОНИМНО. Обычный GET на https://itunes.apple.com/lookup, собственный opener
   urllib без HTTPCookieProcessor, заголовков ровно два (нейтральный User-Agent и
   Accept). Модуль не импортирует ipatool_api / license_guard, не читает сессию,
   cookie, токен, DSID, X-Apple-Store-Front. Страна передаётся только как
   ISO2-код в параметре `country=` публичного API.
2. ТОЛЬКО СПИСОК ПОЛЬЗОВАТЕЛЯ. classify() принимает track_id снаружи (телефон /
   list-purchases); внутри нет ни поиска, ни обхода каталога, ни генерации id.
   Запрашиваются только переданные id (после дедупликации).
3. ФИКСИРОВАННЫЕ ВИТРИНЫ. Витрина аккаунта + максимум две эталонные из константы
   REFERENCE_STOREFRONTS (по умолчанию US, RU). Больше двух эталонных — ValueError,
   перебора стран нет. Кэш (track_id+страна, TTL) и ограничение частоты
   (не чаще 1 запроса в MIN_INTERVAL_S, id пачками по BATCH_SIZE).
4. ТОЛЬКО ПОДПИСЬ. Результат — RegionStatus + осторожный текст из DEFAULT_LABELS_RU
   (настраивается через make_labels). DELISTED — только если приложения нет ни в
   витрине аккаунта, ни в КАЖДОЙ из двух эталонных; при одной эталонной — UNKNOWN. Модуль ничего
   не покупает и не предлагает действий (никаких «купить там», смены региона, VPN).

Логи: только счётчики (сколько id, сколько запросов, сколько ошибок), без track_id,
без страны аккаунта и без тел ответов.
"""
from __future__ import annotations

import enum
import json
import logging
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Callable, Iterable, Mapping, Optional

log = logging.getLogger("apprestore.region_probe")

LOOKUP_URL = "https://itunes.apple.com/lookup"
# Фиксированные эталонные витрины (§1.10 п.3: одна-две, без перебора).
REFERENCE_STOREFRONTS: tuple[str, ...] = ("US", "RU")
MAX_REFERENCE_STOREFRONTS = 2
# DELISTED — только если нет ни в одной эталонной, и проверено не меньше двух.
MIN_REFS_FOR_DELISTED = 2
BATCH_SIZE = 50                 # id в одном запросе lookup (id=1,2,3…)
MIN_INTERVAL_S = 1.0            # не чаще 1 запроса в секунду
TIMEOUT_S = 10.0
POSITIVE_TTL_S = 24 * 3600      # «есть в витрине» — кэш на сутки
NEGATIVE_TTL_S = 6 * 3600       # «нет в витрине» — кэш на 6 часов
MAX_IDS_PER_CALL = 2000         # защита от использования как обхода каталога
USER_AGENT = "AppRestore-region-probe/1"
HEADERS: Mapping[str, str] = {"User-Agent": USER_AGENT, "Accept": "application/json"}

_ISO2 = re.compile(r"^[A-Z]{2}$")


class RegionStatus(str, enum.Enum):
    AVAILABLE = "available"          # есть в витрине аккаунта — в группу не попадает
    NOT_IN_REGION = "not_in_region"  # нет в витрине аккаунта, есть в эталонной
    DELISTED = "delisted"            # нет ни в аккаунте, ни в эталонных
    UNKNOWN = "unknown"              # сеть/ошибка/неоднозначно — не блокировать


# Только подписи, без действий (§1.10 п.4, R5). Классификация косвенная (1–2
# витрины), поэтому формулировки осторожные. Тексты вынесены: Ника/Дима правят
# здесь или передают overrides в make_labels() / label_ru(..., labels=...).
DEFAULT_LABELS_RU: Mapping[RegionStatus, Optional[str]] = {
    RegionStatus.AVAILABLE: None,
    RegionStatus.NOT_IN_REGION: "Нет в App Store вашей страны",
    RegionStatus.DELISTED: "Удалено из App Store",
    RegionStatus.UNKNOWN: "Не проверено",
}

DEFAULT_GROUP_TITLES_RU: Mapping[RegionStatus, str] = {
    RegionStatus.NOT_IN_REGION: "Нет в App Store вашей страны",
    RegionStatus.DELISTED: "Удалённые из App Store",
}

# Обратная совместимость имён.
LABELS_RU = DEFAULT_LABELS_RU
GROUP_TITLES_RU = DEFAULT_GROUP_TITLES_RU


def _merge(base: Mapping[RegionStatus, object], overrides) -> dict:
    out = dict(base)
    for k, v in (overrides or {}).items():
        out[RegionStatus(k)] = v   # ключ — RegionStatus или его value ("delisted")
    return out


def make_labels(overrides: Optional[Mapping] = None) -> dict[RegionStatus, Optional[str]]:
    """Подписи с правками поверх DEFAULT_LABELS_RU (ключи: RegionStatus или 'delisted' и т.п.)."""
    return _merge(DEFAULT_LABELS_RU, overrides)


def make_group_titles(overrides: Optional[Mapping] = None) -> dict[RegionStatus, str]:
    return _merge(DEFAULT_GROUP_TITLES_RU, overrides)


def label_ru(status: RegionStatus,
             labels: Optional[Mapping[RegionStatus, Optional[str]]] = None) -> Optional[str]:
    return (labels or DEFAULT_LABELS_RU).get(status)


class LookupError_(Exception):
    """Сеть/HTTP/разбор ответа — результат для этой пачки неизвестен."""


# fetcher(url, headers, timeout) -> bytes; подменяется в тестах.
Fetcher = Callable[[str, Mapping[str, str], float], bytes]


def _default_fetcher(url: str, headers: Mapping[str, str], timeout: float) -> bytes:
    # Собственный opener: без HTTPCookieProcessor, без auth-хендлеров.
    opener = urllib.request.build_opener()
    opener.addheaders = []  # убрать дефолтный Python-urllib UA, ставим свои
    req = urllib.request.Request(url, headers=dict(headers), method="GET")
    try:
        with opener.open(req, timeout=timeout) as resp:
            if resp.status != 200:
                raise LookupError_(f"HTTP {resp.status}")
            return resp.read()
    except urllib.error.HTTPError as exc:
        raise LookupError_(f"HTTP {exc.code}") from None
    except (urllib.error.URLError, OSError, ValueError) as exc:
        raise LookupError_(type(exc).__name__) from None


def _norm_country(value: str) -> str:
    c = (value or "").strip().upper()
    if not _ISO2.match(c):
        raise ValueError("country must be ISO 3166-1 alpha-2, e.g. 'US'")
    return c


def _norm_track_id(value: object) -> Optional[int]:
    try:
        n = int(str(value).strip())
    except (TypeError, ValueError):
        return None
    return n if n > 0 else None


@dataclass
class _Entry:
    present: bool
    expires: float


class RegionProbe:
    """Классификатор с кэшем и ограничением частоты. Потокобезопасен."""

    def __init__(self, *, reference_storefronts: Iterable[str] = REFERENCE_STOREFRONTS,
                 fetcher: Optional[Fetcher] = None,
                 clock: Callable[[], float] = time.monotonic,
                 sleep: Callable[[float], None] = time.sleep,
                 min_interval_s: float = MIN_INTERVAL_S,
                 batch_size: int = BATCH_SIZE,
                 positive_ttl_s: float = POSITIVE_TTL_S,
                 negative_ttl_s: float = NEGATIVE_TTL_S,
                 timeout_s: float = TIMEOUT_S) -> None:
        refs: list[str] = []
        for c in reference_storefronts:
            c = _norm_country(c)
            if c not in refs:
                refs.append(c)
        if not 1 <= len(refs) <= MAX_REFERENCE_STOREFRONTS:
            raise ValueError("нужна одна-две фиксированные эталонные витрины (§1.10 п.3)")
        self.reference_storefronts: tuple[str, ...] = tuple(refs)
        self._fetch = fetcher or _default_fetcher
        self._clock = clock
        self._sleep = sleep
        self._min_interval = max(0.0, float(min_interval_s))
        self._batch = max(1, min(int(batch_size), BATCH_SIZE))
        self._pos_ttl = positive_ttl_s
        self._neg_ttl = negative_ttl_s
        self._timeout = timeout_s
        self._cache: dict[tuple[int, str], _Entry] = {}
        self._lock = threading.RLock()
        self._last_request: Optional[float] = None
        self.requests_made = 0  # для тестов/диагностики

    # --- кэш -----------------------------------------------------------------
    def _cached(self, tid: int, country: str) -> Optional[bool]:
        e = self._cache.get((tid, country))
        if e is None:
            return None
        if self._clock() >= e.expires:
            del self._cache[(tid, country)]
            return None
        return e.present

    def _store(self, tid: int, country: str, present: bool) -> None:
        ttl = self._pos_ttl if present else self._neg_ttl
        self._cache[(tid, country)] = _Entry(present, self._clock() + ttl)

    def clear_cache(self) -> None:
        with self._lock:
            self._cache.clear()

    # --- сеть ----------------------------------------------------------------
    def _throttle(self) -> None:
        if self._last_request is not None and self._min_interval > 0:
            wait = self._last_request + self._min_interval - self._clock()
            if wait > 0:
                self._sleep(wait)
        self._last_request = self._clock()

    def _lookup_batch(self, ids: list[int], country: str) -> set[int]:
        query = urllib.parse.urlencode({"id": ",".join(str(i) for i in ids),
                                        "country": country.lower()})
        self._throttle()
        self.requests_made += 1
        raw = self._fetch(f"{LOOKUP_URL}?{query}", HEADERS, self._timeout)
        try:
            data = json.loads(raw.decode("utf-8") if isinstance(raw, bytes) else raw)
            results = data["results"]
            if not isinstance(results, list):
                raise TypeError
        except (ValueError, KeyError, TypeError, UnicodeDecodeError):
            raise LookupError_("bad JSON") from None
        wanted = set(ids)
        found: set[int] = set()
        for item in results:
            if not isinstance(item, dict):
                continue
            tid = _norm_track_id(item.get("trackId"))
            if tid in wanted:
                found.add(tid)
        return found

    def _presence(self, ids: list[int], country: str) -> dict[int, Optional[bool]]:
        """track_id -> True/False (есть/нет в витрине) или None (ошибка)."""
        out: dict[int, Optional[bool]] = {}
        todo: list[int] = []
        for tid in ids:
            hit = self._cached(tid, country)
            if hit is None:
                todo.append(tid)
            else:
                out[tid] = hit
        errors = 0
        for i in range(0, len(todo), self._batch):
            chunk = todo[i:i + self._batch]
            try:
                found = self._lookup_batch(chunk, country)
            except LookupError_:
                errors += 1
                for tid in chunk:
                    out[tid] = None   # ошибки не кэшируем
                continue
            for tid in chunk:
                present = tid in found
                self._store(tid, country, present)
                out[tid] = present
        if errors:
            log.info("region_probe: %d lookup batch(es) failed", errors)
        return out

    # --- API -----------------------------------------------------------------
    def classify(self, track_ids: Iterable[object], account_country: str
                 ) -> dict[int, RegionStatus]:
        """track_id (из списка пользователя) -> RegionStatus.

        account_country — ISO2 страны Apple ID (AccountInfo/auth info countryCode).
        Невалидные track_id пропускаются; невалидная страна -> ValueError.
        """
        account = _norm_country(account_country)
        ids: list[int] = []
        seen: set[int] = set()
        for raw in track_ids:
            tid = _norm_track_id(raw)
            if tid is not None and tid not in seen:
                seen.add(tid)
                ids.append(tid)
        if len(ids) > MAX_IDS_PER_CALL:
            raise ValueError("слишком много track_id: только список пользователя (§1.10 п.2)")
        if not ids:
            return {}
        with self._lock:
            before = self.requests_made
            acc = self._presence(ids, account)
            result: dict[int, RegionStatus] = {}
            pending = [t for t in ids if acc[t] is False]
            for t in ids:
                if acc[t] is True:
                    result[t] = RegionStatus.AVAILABLE
                elif acc[t] is None:
                    result[t] = RegionStatus.UNKNOWN
            # Для DELISTED нужно подтверждённое отсутствие во ВСЕХ эталонных витринах,
            # и их должно быть минимум две (витрина аккаунта засчитывается, только если
            # она сама эталонная). Одной витрины мало -> UNKNOWN (уточнение Лены, §1.10).
            ref_state: dict[int, list[Optional[bool]]] = {t: [] for t in pending}
            if account in self.reference_storefronts:
                for t in pending:
                    ref_state[t].append(False)
            for ref in self.reference_storefronts:
                if ref == account:
                    continue  # аккаунт уже проверен, лишний запрос не нужен
                need = [t for t in pending if True not in ref_state[t]]
                if not need:
                    break
                for t, v in self._presence(need, ref).items():
                    ref_state[t].append(v)
            for t in pending:
                states = ref_state[t]
                if True in states:
                    result[t] = RegionStatus.NOT_IN_REGION
                elif (None not in states and len(states) >= MIN_REFS_FOR_DELISTED
                      and len(states) == len(self.reference_storefronts)):
                    result[t] = RegionStatus.DELISTED
                else:
                    result[t] = RegionStatus.UNKNOWN   # мало данных / ошибка
            log.info("region_probe: %d ids, %d requests", len(ids),
                     self.requests_made - before)
            return result


_default_probe: Optional[RegionProbe] = None
_default_lock = threading.Lock()


def default_probe() -> RegionProbe:
    global _default_probe
    with _default_lock:
        if _default_probe is None:
            _default_probe = RegionProbe()
        return _default_probe


def classify_region(track_ids: Iterable[object], account_country: str
                    ) -> dict[int, RegionStatus]:
    """Удобная обёртка над общим RegionProbe (общий кэш и rate-limit на процесс)."""
    return default_probe().classify(track_ids, account_country)
