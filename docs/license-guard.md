# license_guard — LEGAL.md §1.14: удалённое приложение с неизвестной ценой

Основание (Лена, `LEGAL.md` §1.14, коммит `80fa48f`): в `cde7d00` `buyProduct` всегда шлёт
`price=0`, платное приложение по такому запросу Apple не выдаёт. Авторитет цены — сервер Apple.
Поэтому для удалённых из App Store приложений, у которых lookup цены не дал, разрешена
одна попытка через обычный путь. Для всех остальных случаев поведение гарда прежнее.

## Как включается исключение

Только явным флагом `region_unavailable=True` (строго `bool True`; `1`, `"DELISTED"` и т.п.
не принимаются) **и** `price=None`. Флаг ставит вызывающий код ТОЛЬКО если `region_probe`
вернул `DELISTED` или `NOT_IN_REGION`.

| price | флаг | результат |
|---|---|---|
| `None` | выкл | отказ «цена не подтверждена» (как раньше) |
| `None` | вкл | разрешено, `Verdict.region_exception=True` |
| `> 0` из любого lookup (аккаунт или эталонная витрина) | любой | отказ «платное» |
| `0` | любой | обычный путь, без `price_source` |
| мусор / NaN / inf / bool | любой | отказ «цена не подтверждена» (NaN раньше проходил как бесплатное — закрыто) |

Вызывающий код обязан передать известную цену > 0 из ЛЮБОГО lookup, даже если витрина аккаунта
цены не дала. Для `NOT_IN_REGION` это главный случай (Лена, усл. 2).

## Порядок (не изменился)

`acquire_and_record`: проверка `region_session` → preflight (патченый ipatool) → `journal_lock` →
track_id / цена / сессия / лимиты 15 всего и 5 за сутки → отметка попытки в сессии → `purchase()` →
запись. Экран согласия и шлюз в GUI идут до вызова, как раньше. Гард никакую цену в запрос не
передаёт: `purchase()` без аргументов, ipatool шлёт фиксированный `price=0`.

## Журнал (только этот путь)

| исход `purchase()` | запись | в лимит |
|---|---|---|
| `True` / `"acquired"` / `"ok"` / `"success"` | `status: "acquired"`, `price: null`, `price_source: "apple_fixed_0"` | да |
| `False` / `"refused"` / `"failed"` / `"apple_refused"` / `"rejected"` (явный отказ, непустой FailureType) | нет; `AcquireResult(allowed=True, recorded=False, code="apple_refused")` | нет |
| всё остальное: `None`, `"purchase_uncertain"`, незнакомая строка | `status: "purchase_uncertain"`, `price_source: "apple_fixed_0"` | да |
| исключение из `purchase()` | `purchase_uncertain` + `price_source`, исключение пробрасывается | да |

Разница с обычным путём: там `None`/незнакомая строка значат «не пишем». Здесь неясное
считается в лимит (Лена, усл. 5). Поправки `record_amend` / `record_void` работают как обычно
(например `purchase_uncertain` → `refused` после проверки истории покупок).

У обычных записей поля `price_source` нет, формат строки прежний.

## Одна попытка на приложение за сессию

`RegionAttemptSession` хранит состояние только в памяти, в журнал не пишет. Один объект на
сессию Apple ID, после «Выйти» или смены аккаунта `reset()` или новый объект. На пути §1.14
`region_session` обязателен (без него `code="region_session_required"`, preflight и журнал не
трогаются). Попытка отмечается прямо перед `purchase()` при любом исходе. Если проверки
отказали (лимит, preflight, платное), попытка не отмечается. Повтор: `allowed=False`,
`code="region_already_attempted"`, `purchase()` не вызывается. `check_can_acquire(...,
region_session=s)` даёт тот же отказ, по нему GUI может гасить кнопку.

## API

```python
check_can_acquire(track_id, price, *, journal_path=None, daily_limit=5, total_limit=15,
                  now=_now_utc, region_unavailable: bool = False,
                  region_session: RegionAttemptSession | None = None) -> Verdict
acquire_and_record(track_id, price, *, purchase, preflight=None, journal_path=None,
                   daily_limit=5, total_limit=15, bundle_id=None, storefront=None, mode=None,
                   app_id=None, now=_now_utc, region_unavailable: bool = False,
                   region_session: RegionAttemptSession | None = None) -> AcquireResult
Verdict(allowed, reason, used_today, used_total, code=None, region_exception=False)
RegionAttemptSession(): attempted(track_id), mark(track_id), reset(), len()
PRICE_SOURCE_APPLE_FIXED_0 = "apple_fixed_0"
APPLE_REFUSED = "apple_refused"; REGION_ALREADY_ATTEMPTED = "region_already_attempted"
REGION_SESSION_REQUIRED = "region_session_required"
```

Все новые параметры имеют значения по умолчанию, старые вызовы работают как раньше.

## Тесты

`test_license_guard.py`: 100 passed (было 56, +44 на §1.14). Весь `maks-share`: 281 passed.
