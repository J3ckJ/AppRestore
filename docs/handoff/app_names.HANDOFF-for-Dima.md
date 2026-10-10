# HANDOFF Диме: app_names (имена плиток `missing`)

Файлы: `maks-share/app_names.py`, тесты `maks-share/test_app_names.py` (68 passed; `test_region_probe.py` 51, `test_delisted_search.py` 60, весь maks-share 375 passed).
Дока: `maks-share/app-names.md`. Рамка: LEGAL.md §1.10. `region_probe.py` и `delisted_search.py` не менялись.

## API
```python
from app_names import display_names, country_chain, region_from_locale, AppNames

display_names(ids: Iterable[int|str] = (), *,
              bundle_ids: Iterable[str] = (),
              countries: Iterable[str] | None = None,   # явный порядок, 1–3 ISO2, US сам не добавляется
              country: str | None = None,               # ровно одна витрина
              account_country: str | None = None,       # ISO2 Apple ID, только после входа
              device_locale: str | None = None,         # Locale iPhone как есть, напр. "en_RU"
              ) -> dict[str, str]                        # входной токен строкой -> trackName

AppNames(probe: RegionProbe | None = None, *, batch_size=100, positive_ttl_s=86400,
         negative_ttl_s=21600, builtin_fallback=True).display_names(...)       # то же; probe=None -> region_probe.default_probe()
country_chain(account_country=None, device_locale=None) -> tuple[str, ...]   # напр. ("ru", "us")
region_from_locale("en_RU") -> "ru"
```
Можно задать либо `countries`/`country`, либо `account_country`/`device_locale`. Смешать → `ValueError`.
Обычный путь из GUI: `account_country` + `device_locale`.

## Ключи результата
- Ключ — ровно то, что ты передал, строкой: `ids=[284882215]` → `{"284882215": "Facebook"}`,
  `bundle_ids=["com.Foo.Bar"]` → `{"com.Foo.Bar": "…"}`. Так и при разбивке на пачки, и при переходе к следующей витрине.
- Маппинг обратно: `name = out.get(bundle_id) or out.get(str(store_id))`.
- Что lookup не нашёл ни в одной витрине, дозаполняется из встроенного списка `delisted_search` (уровень A, по
  `track_id` и `bundleId`). Это без сети: web.archive.org не трогается никогда, переключатель «Искать в архиве» здесь не нужен.
  Имя от Apple не перезаписывается.
- Не найденного нет в словаре вообще (не `""`, не `None`). Тогда запасная цепочка Ники (`01` стр. 228):
  `display_names()` (lookup + встроенный список) → `iTunesMetadata` → покупки → «Приложение» в самом конце. Пока ищем, подпись пустая.

## Витрины: до и после входа
```python
locale = await lockdown.get_locale()     # pymobiledevice3: Locale из com.apple.international, напр. "en_RU"
# до входа
names = display_names(ids, bundle_ids=bids, device_locale=locale)                 # регион iPhone → US
# после входа
cc = client.account_info().country_code                                            # "RU" (как для region_probe)
names = display_names(ids, bundle_ids=bids, account_country=cc, device_locale=locale)  # аккаунт → регион iPhone → US
```
- Регион iPhone — это **регион из Locale (после `_`), не язык**: `ru_RU` → `ru`, `en_RU` → `ru`, `zh-Hans_CN` → `cn`.
  Если в Locale только язык (`en`) или его нет вообще, сразу идёт `US`. Можно передавать `None` или `""`.
- Не путать с `RegionInfo` из lockdown (`LL/A`, `RS/A`): это регион модели, а не страна.
- `account_country` — ISO2 в любом регистре. Невалидное значение (None, storefront-id) просто пропускается, исключения нет.
- Язык имён задаёт витрина: RU-витрина отдаёт русские названия.
- Следующая витрина спрашивается только для ещё не найденных. Ошибка сети в витрине не кэшируется,
  эти ключи идут к следующей.

## Пачки, частота, кэш
- id и bundleId уходят отдельными запросами, по 100 в пачке (`?id=a,b,c` / `?bundleId=a,b,c`). Сопоставление по полю, не по индексу.
- Лимитер общий с `region_probe` (`default_probe()`): ≤1 запрос/с на процесс для обоих модулей.
  Худший случай — 3 × ⌈N/100⌉ запросов. **Звать из воркера, не из UI-потока.** Сначала плитки на экране, потом остальные.
- Кэш имён: 24 ч на «нашлось», 6 ч на «нет». id-запросы заодно прогревают кэш `region_probe`, и `classify_region`
  по тем же id и витринам потом в сеть не ходит.
- После выхода или смены аккаунта: `app_names.default_resolver().clear_cache()` (и `region_probe.default_probe().clear_cache()`).
- Без сети не звать. Если звать, ошибки не бросаются: ключи просто не попадут в результат.
- Больше 2000 ключей за вызов → `ValueError`. Подавать только недостающие приложения пользователя.

## Тесты, которые стоит добавить у тебя
- Плитка `missing` берёт имя из `display_names()` раньше `iTunesMetadata`. Пока идёт поиск, подпись пустая, и только в самом конце «Приложение».
- До входа `account_country` не передаётся, после входа передаётся.
- Вызов идёт не из UI-потока.
