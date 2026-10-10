"""app_names — имена для плиток «недостающих» приложений по публичному lookup.

Нужен там, где имени нет ни на телефоне (приложения на нём нет), ни в кэше покупок
(ещё не вошли): плитка `missing` получает trackName из анонимного
GET https://itunes.apple.com/lookup?id=a,b,c&country=xx (или ?bundleId=a,b,c).
Работает и до входа в Apple ID, и после.

Правовая рамка — та же, что у region_probe (LEGAL.md §1.10):

1. АНОНИМНО. Сеть, кэш присутствия и ограничение частоты — из region_probe
   (общий RegionProbe): тот же opener urllib без HTTPCookieProcessor, ровно два
   заголовка (region_probe.HEADERS: User-Agent и Accept), в URL только
   `id`/`bundleId` и `country`. Модуль не импортирует ipatool_api / license_guard,
   не видит сессию, cookie, токен, DSID, X-Apple-Store-Front. Витрина аккаунта
   приходит снаружи как ISO2-код, регион iPhone — как строка Locale с телефона.
2. ТОЛЬКО СПИСОК ПОЛЬЗОВАТЕЛЯ. На вход — track_id / bundleId недостающих
   приложений пользователя (телефон, iTunesMetadata, покупки). Ни поиска, ни
   обхода каталога, ни генерации id; не больше MAX_IDS_PER_CALL ключей за вызов.
3. ФИКСИРОВАННЫЕ ВИТРИНЫ, БЕЗ ПЕРЕБОРА. Не больше MAX_COUNTRIES (3) стран:
   после входа — витрина аккаунта → регион iPhone → US; до входа — регион
   iPhone → US. Следующая страна спрашивается только для ещё не найденных.
   Кэш с TTL, не чаще 1 запроса в region_probe.MIN_INTERVAL_S (общий лимитер
   с region_probe на процесс), пачки по BATCH_SIZE (100).
4. ЗАПАСНОЕ ИМЯ ИЗ ВСТРОЕННОГО СПИСКА. Что публичный lookup не нашёл ни в одной
   витрине (удалённые отовсюду), дозаполняется по track_id / bundleId из
   delisted_search.builtin_entries() — встроенный проверенный список уровня A
   (LEGAL.md §1.13, BUILTIN_STRICT). Это чтение константы: без сети, в
   web.archive.org запрос не уходит никогда (Wayback-путь delisted_search не
   вызывается). Имя, которое вернул Apple, не перезаписывается.
5. ТОЛЬКО ТЕКСТ. Результат — имена (trackName). Модуль ничего не покупает,
   не скачивает, не предлагает действий.

Ответ lookup не сохраняет порядок и молча опускает ненайденные id, поэтому
результаты сопоставляются по trackId / bundleId, а не по индексу. Ключ в выходном
словаре — ровно тот токен, который передал вызывающий (строкой).

Логи: только счётчики, без id, без стран и без тел ответов.
"""
from __future__ import annotations

import json
import logging
import re
import threading
import urllib.parse
from dataclasses import dataclass
from typing import Iterable, Optional

try:
    from apprestore_core import region_probe as _rp
except ImportError:
    import region_probe as _rp
try:
    from apprestore_core import delisted_search as _ds
except ImportError:
    import delisted_search as _ds

log = logging.getLogger("apprestore.app_names")

LOOKUP_URL = _rp.LOOKUP_URL
HEADERS = _rp.HEADERS                 # те же два заголовка, что у region_probe
BATCH_SIZE = 100                      # живой тест 10.10: 150 ок, 200 обрезается
FALLBACK_COUNTRY = "US"               # последняя витрина цепочки
MAX_COUNTRIES = 3                     # аккаунт + регион iPhone + US
MAX_IDS_PER_CALL = _rp.MAX_IDS_PER_CALL
POSITIVE_TTL_S = _rp.POSITIVE_TTL_S
NEGATIVE_TTL_S = _rp.NEGATIVE_TTL_S

_ISO2 = re.compile(r"^[A-Za-z]{2}$")
_BUNDLE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9.\-_]{0,254}$")


# --- страны -------------------------------------------------------------------
def norm_storefront(value: object) -> Optional[str]:
    """ISO2 витрины ('RU', 'ru', ' us ') -> 'ru' (для параметра country=). Иначе None."""
    if not isinstance(value, str):
        return None
    c = value.strip()
    return c.lower() if _ISO2.match(c) else None


def region_from_locale(locale: object) -> Optional[str]:
    """Регион (НЕ язык) из Locale iPhone -> код витрины в нижнем регистре.

    'ru_RU' -> 'ru', 'en_RU' -> 'ru', 'zh-Hans_CN' -> 'cn', 'zh_Hant_TW' -> 'tw',
    'en_US@calendar=gregorian' -> 'us'. Без региональной части ('en', 'ru', '')
    или с нечисловым-нестрановым регионом ('en_001') -> None.
    """
    if not isinstance(locale, str):
        return None
    s = locale.strip().split("@", 1)[0].split(".", 1)[0]
    if "_" not in s:
        return None                    # голый язык ('en') — региона нет
    region = s.rsplit("_", 1)[1]
    return region.lower() if _ISO2.match(region) else None


def country_chain(account_country: object = None, device_locale: object = None
                  ) -> tuple[str, ...]:
    """Порядок витрин (lower ISO2), дубли схлопываются.

    После входа (account_country задан): витрина аккаунта → регион iPhone → US.
    До входа (account_country=None): регион iPhone → US.
    Невалидные значения пропускаются (имена — не критичный путь).
    """
    out: list[str] = []
    for c in (norm_storefront(account_country), region_from_locale(device_locale),
              FALLBACK_COUNTRY.lower()):
        if c and c not in out:
            out.append(c)
    return tuple(out)


def _explicit_countries(countries: Iterable[object]) -> tuple[str, ...]:
    out: list[str] = []
    for raw in countries:
        c = norm_storefront(raw)
        if c is None:
            raise ValueError("country must be ISO 3166-1 alpha-2, e.g. 'RU'")
        if c not in out:
            out.append(c)
    if not 1 <= len(out) <= MAX_COUNTRIES:
        raise ValueError(f"нужно 1–{MAX_COUNTRIES} витрины, без перебора (§1.10 п.3)")
    return tuple(out)


# --- ключи --------------------------------------------------------------------
def _key_of(raw: object) -> str:
    return str(raw)                    # ключ выхода — ровно переданный токен строкой


def _norm_bundle(raw: object) -> Optional[str]:
    if not isinstance(raw, str):
        return None
    b = raw.strip()
    return b.casefold() if _BUNDLE.match(b) else None


def _norm_name(value: object) -> Optional[str]:
    if not isinstance(value, str):
        return None
    n = " ".join(value.split())
    return n or None


def builtin_names() -> tuple[dict[int, str], dict[str, str]]:
    """Встроенный список delisted_search (только уровень A при BUILTIN_STRICT):
    ({track_id: name}, {bundleId.casefold(): name}). Без сети."""
    by_id: dict[int, str] = {}
    by_bundle: dict[str, str] = {}
    for e in _ds.builtin_entries():          # strict=None -> BUILTIN_STRICT (§1.13)
        name = _norm_name(e.name)
        if name is None:
            continue
        by_id.setdefault(int(e.track_id), name)
        b = _norm_bundle(e.bundle_id) if e.bundle_id else None
        if b is not None:
            by_bundle.setdefault(b, name)
    return by_id, by_bundle


@dataclass
class _NameEntry:
    name: Optional[str]                # None — подтверждённо нет в этой витрине
    expires: float


class AppNames:
    """Резолвер имён поверх RegionProbe: его fetcher, lock, clock, rate-limit и кэш.

    probe=None -> region_probe.default_probe() (общий лимитер на процесс).
    """

    def __init__(self, probe: Optional["_rp.RegionProbe"] = None, *,
                 batch_size: int = BATCH_SIZE,
                 positive_ttl_s: float = POSITIVE_TTL_S,
                 negative_ttl_s: float = NEGATIVE_TTL_S,
                 builtin_fallback: bool = True) -> None:
        self.probe = probe if probe is not None else _rp.default_probe()
        self._batch = max(1, min(int(batch_size), BATCH_SIZE))
        self._pos_ttl = positive_ttl_s
        self._neg_ttl = negative_ttl_s
        self.builtin_fallback = bool(builtin_fallback)
        # (kind, norm_key, country) -> _NameEntry; kind: "id" | "bundle"
        self._cache: dict[tuple[str, object, str], _NameEntry] = {}
        self.requests_made = 0

    # --- кэш -------------------------------------------------------------------
    def _cached(self, k: tuple[str, object, str]) -> Optional[_NameEntry]:
        e = self._cache.get(k)
        if e is None:
            return None
        if self.probe._clock() >= e.expires:
            del self._cache[k]
            return None
        return e

    def _store(self, k: tuple[str, object, str], name: Optional[str]) -> None:
        ttl = self._pos_ttl if name is not None else self._neg_ttl
        self._cache[k] = _NameEntry(name, self.probe._clock() + ttl)

    def clear_cache(self) -> None:
        with self.probe._lock:
            self._cache.clear()

    # --- сеть ------------------------------------------------------------------
    def _lookup(self, kind: str, keys: list, country: str) -> dict:
        """Одна пачка. kind='id' -> {int: (name, price)}, 'bundle' -> {casefold: name}."""
        param = "id" if kind == "id" else "bundleId"
        query = urllib.parse.urlencode({param: ",".join(str(k) for k in keys),
                                        "country": country})
        self.probe._throttle()          # общий с region_probe лимитер
        self.requests_made += 1
        raw = self.probe._fetch(f"{LOOKUP_URL}?{query}", HEADERS, self.probe._timeout)
        try:
            data = json.loads(raw.decode("utf-8") if isinstance(raw, bytes) else raw)
            results = data["results"]
            if not isinstance(results, list):
                raise TypeError
        except (ValueError, KeyError, TypeError, UnicodeDecodeError):
            raise _rp.LookupError_("bad JSON") from None
        wanted = set(keys)
        found: dict = {}
        for item in results:            # по полю, не по индексу: порядок не гарантирован
            if not isinstance(item, dict):
                continue
            if kind == "id":
                k = _rp._norm_track_id(item.get("trackId"))
            else:
                b = item.get("bundleId")
                k = b.strip().casefold() if isinstance(b, str) else None
            if k not in wanted or k in found:
                continue
            name = _norm_name(item.get("trackName"))
            if kind == "id":
                found[k] = (name, _rp._norm_price(item.get("price")))
            elif name is not None:
                found[k] = name
        return found

    def _resolve_kind(self, kind: str, keys: list, chain: tuple[str, ...]) -> tuple[dict, int]:
        names: dict = {}
        errors = 0
        pending = list(keys)
        for country in chain:
            if not pending:
                break
            todo = []
            for k in pending:
                hit = self._cached((kind, k, country))
                if hit is None:
                    todo.append(k)
                elif hit.name is not None:
                    names[k] = hit.name
            for i in range(0, len(todo), self._batch):
                chunk = todo[i:i + self._batch]
                try:
                    found = self._lookup(kind, chunk, country)
                except _rp.LookupError_:
                    errors += 1             # не кэшируем, эти ключи — к следующей стране
                    continue
                for k in chunk:
                    if kind == "id":
                        present = k in found
                        name, price = found.get(k, (None, None))
                        # тот же факт «есть/нет в витрине» — в кэш region_probe
                        self.probe._store(k, country.upper(), present, price)
                    else:
                        name = found.get(k)
                    self._store((kind, k, country), name)
                    if name is not None:
                        names[k] = name
            pending = [k for k in pending if k not in names]
        return names, errors

    # --- API -------------------------------------------------------------------
    def display_names(self, ids: Iterable[object] = (), *,
                      bundle_ids: Iterable[object] = (),
                      countries: Optional[Iterable[object]] = None,
                      country: Optional[str] = None,
                      account_country: Optional[str] = None,
                      device_locale: Optional[str] = None) -> dict[str, str]:
        """Входной токен (строкой) -> trackName. Не найденные нигде — отсутствуют.

        Сначала публичный lookup по цепочке витрин; что осталось пустым —
        по track_id / bundleId из встроенного списка delisted_search (уровень A,
        без сети, web.archive.org не трогается). Apple-имя не перезаписывается.

        Витрины (одно из):
          * account_country / device_locale -> country_chain(): после входа
            аккаунт → регион iPhone → US, до входа регион iPhone → US;
          * countries=[...] — явный порядок (1–3 ISO2, без автодобавления US);
          * country='RU' — ровно одна витрина.
        """
        if (countries is not None) + (country is not None) > 1 or (
                (countries is not None or country is not None)
                and (account_country is not None or device_locale is not None)):
            raise ValueError("задайте либо countries/country, либо account_country/device_locale")
        if countries is not None:
            chain = _explicit_countries(countries)
        elif country is not None:
            chain = _explicit_countries([country])
        else:
            chain = country_chain(account_country, device_locale)

        id_keys: dict[int, list[str]] = {}
        b_keys: dict[str, list[str]] = {}
        for raw in ids:
            tid = _rp._norm_track_id(raw)
            if tid is not None:
                ks = id_keys.setdefault(tid, [])
                if _key_of(raw) not in ks:
                    ks.append(_key_of(raw))
        for raw in bundle_ids:
            b = _norm_bundle(raw)
            if b is not None:
                ks = b_keys.setdefault(b, [])
                if _key_of(raw) not in ks:
                    ks.append(_key_of(raw))
        if len(id_keys) + len(b_keys) > MAX_IDS_PER_CALL:
            raise ValueError("слишком много ключей: только список пользователя (§1.10 п.2)")
        if not id_keys and not b_keys:
            return {}

        out: dict[str, str] = {}
        with self.probe._lock:
            before = self.requests_made
            errors = 0
            for kind, keymap in (("id", id_keys), ("bundle", b_keys)):
                if not keymap:
                    continue
                names, err = self._resolve_kind(kind, list(keymap), chain)
                errors += err
                for k, name in names.items():
                    for token in keymap[k]:
                        out[token] = name
            # Запасное имя: только для ещё пустых, Apple-имя не трогаем. Без сети.
            filled = 0
            if self.builtin_fallback:
                by_id, by_bundle = builtin_names()
                for keymap, index in ((id_keys, by_id), (b_keys, by_bundle)):
                    for k, tokens in keymap.items():
                        name = index.get(k)
                        if name is None:
                            continue
                        for token in tokens:
                            if token not in out:
                                out[token] = name
                                filled += 1
            log.info("app_names: %d keys, %d resolved (%d builtin), %d requests, %d errors",
                     len(id_keys) + len(b_keys), len(out), filled,
                     self.requests_made - before, errors)
        return out


_default: Optional[AppNames] = None
_default_lock = threading.Lock()


def default_resolver() -> AppNames:
    """Общий AppNames поверх region_probe.default_probe() (один лимитер на процесс)."""
    global _default
    probe = _rp.default_probe()
    with _default_lock:
        if _default is None or _default.probe is not probe:
            _default = AppNames(probe)
        return _default


def display_names(ids: Iterable[object] = (), *,
                  bundle_ids: Iterable[object] = (),
                  countries: Optional[Iterable[object]] = None,
                  country: Optional[str] = None,
                  account_country: Optional[str] = None,
                  device_locale: Optional[str] = None) -> dict[str, str]:
    """Обёртка над общим AppNames. См. AppNames.display_names()."""
    return default_resolver().display_names(
        ids, bundle_ids=bundle_ids, countries=countries, country=country,
        account_country=account_country, device_locale=device_locale)
