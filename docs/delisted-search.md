# delisted_search: поиск удалённых приложений по названию

Макс, 10.10.2026. Модуль `delisted_search.py`, только stdlib, тесты в `test_delisted_search.py` (58, без сети). Обновлено 10.10.2026 вечером под LEGAL.md §1.13: `BUILTIN_STRICT=True` по умолчанию, у записей уровня A есть архивная копия поста, новое поле `developer_is_bank`, «сириус» кириллицей находит «Cириус» и в архиве.

Задача Евгения: во вкладке «Найти» человек вписывает «Сириус» (или «втб», «альфа», «dn,») и находит удалённое приложение, а не только по номеру или ссылке.

## Что делает

1. **Встроенный список** (`BUILTIN`). Работает без сети и сразу. В нём удалённые банковские приложения и приложения, ссылку на которые публиковал сам банк. По умолчанию (`BUILTIN_STRICT=True`, §1.13) работает только уровень A — 4 записи. Учитываются синонимы, регистр, ё/е, дефисы, гомоглифы (в App Store «Cириус» начинается с латинской C), неверная раскладка («dn,» → «втб», «cbhbec» → «сириус», «ыиук» → «sber»), транслит («sirius»), опечатки (difflib, первая буква должна совпадать, запрос от 4 символов).
2. **Запасной поиск через Wayback** (Internet Archive), витрины `ru` и `us`. CDX по префиксу `apps.apple.com/<cc>/app/<slug>`. Slug: как ввели; тот же с латинской первой буквой-двойником (`сириус` → `cириус`: «Cириус» в App Store записан с латинской C, и кириллический префикс его не находит, проверено на живом CDX 10.10.2026); исправленная раскладка. Не больше `MAX_SLUGS = 3`. Дальше потом сырой снимок страницы (`/web/<ts>id_/…`). Со снимка берём название, разработчика и иконку из JSON-LD `SoftwareApplication`. Bundle берём только когда он структурно привязан к этому track_id (Ember shoebox 2019–2024, Ember data store, Svelte `serialized-server-data` 2025+). Иначе `None`: на странице полно чужих bundleId из полок «похожие».

На выходе только метаданные. Установка дальше идёт обычным путём через Apple ID пользователя и `license_guard`. Найденный id ещё не значит, что приложение можно установить.

## API

```python
from delisted_search import search_delisted, DelistedSearch, DelistedHit, HitSource

hits = search_delisted("втб")                          # только встроенный список, без сети
hits = search_delisted("сириус", allow_network=True,     # + Wayback, только при «Искать в архиве»
                       storefronts=("ru", "us"))
```

`DelistedHit` (frozen dataclass, есть `.to_dict()`):

| поле | тип | смысл |
|---|---|---|
| `track_id` | int | Adam ID |
| `name` | str | название в App Store как есть («Cириус» с латинской C) |
| `developer` | str \| None | продавец в App Store |
| `bundle_id` | str \| None | только если однозначно привязан |
| `icon_url` | str \| None | только `https://isN-ssl.mzstatic.com/image/thumb/…/512x512bb.png` |
| `source` | `HitSource` | `builtin` \| `wayback` |
| `confidence` | float 0..1 | builtin до 1.0; wayback не выше 0.8 (без снимка не выше 0.5) |
| `brand` | str \| None | продукт банка, только для поиска. **В UI не показывать** (02-picker.md §6b) |
| `storefront` | str \| None | витрина снимка (wayback) |
| `snapshot` | str \| None | дата снимка YYYYMMDD (wayback) |
| `developer_is_bank` | bool \| None | builtin: `True` — разработчик сам банк; `False` — не банк, GUI пишет второй строкой «Ссылку на это приложение публиковал банк» (§1.13 п.2). Wayback: `None`, второй строки нет |

`enum HitSource(str)`: `BUILTIN = "builtin"`, `WAYBACK = "wayback"`.

Порядок: сначала встроенный список. Если в нём нет результата с `confidence ≥ 0.85` и `allow_network=True`, идём в Wayback и добавляем результаты без дублей по track_id, builtin выше. Номер или ссылка (`472951966`, `id…`, `https://…`) возвращают `[]`: это обычный путь «Найти».

Класс `DelistedSearch(fetcher=…, clock=…, sleep=…, min_interval_s=1.0, max_snapshots=5, strict=None)` подменяет HTTP в тестах. `fetcher(url, headers, timeout) -> bytes`, ошибки бросает как `WaybackError(status)`. Ещё есть `search_builtin(query)`, `builtin_entries(strict)` и `parse_snapshot(raw, track_id)`.

`allow_network=False` по умолчанию. Запрос пользователя уходит в Internet Archive (§1.11), поэтому GUI передаёт `True` только при включённом переключателе «Искать в архиве» в настройках и только запасным путём после App Store, покупок и встроенного списка. Раскрытие — этот переключатель и сноска под выдачей (§1.12); экрана согласия нет.

## Рамка безопасности (как region_probe)

- Свой opener urllib без HTTPCookieProcessor и auth-хендлеров, заголовков ровно два (`User-Agent: AppRestore-delisted-search/1`, `Accept`). Модуль не импортирует `ipatool_api` и `license_guard`, не знает про сессию, cookie, токен, DSID и витрину аккаунта.
- Хосты: только `web.archive.org`. Из результатов CDX берём только `https://apps.apple.com/<cc>/app/<slug>/id<N>`. Мусорные URL (в CDX встречаются строки с вшитым JSON-конфигом Apple, включая media-токен) отбрасываются и никуда не выводятся.
- Не чаще 1 запроса в секунду на весь модуль. Одна повторная попытка на 429/503 с паузой 5 с. Не больше 5 снимков на поиск, не больше 3 вариантов slug (как ввели, с латинской первой буквой-двойником, исправленная раскладка).
- Кэш в памяти: CDX 24 ч (пустой ответ 6 ч), снимки 7 дней. Ошибки не кэшируются.
- Витрины только `ru` и `us`, не больше двух. Перебора стран нет (§1.10).
- Лог: только счётчики (hits/requests/errors). Нет текста запроса, track_id и тел ответов.
- Никаких ссылок на файлы: текстовые поля со ссылками или `.ipa` обнуляются, иконка проходит только по белому шаблону mzstatic. IPA-каталоги не используются и не упоминаются.

Цена по времени: худший случай Wayback-поиска — 2 витрины × 3 slug + 5 снимков ≈ 11 запросов ≈ 11–12 с (для «сириус» по двум витринам — 4 CDX + до 5 снимков). В GUI его нужно запускать асинхронно и показывать прогресс.

## Встроенный список: правило и таблица

Правило Лены (10.10.2026): пару «приложение → банк» вносим только при подтверждении от **официального источника самого банка** (сайт или официальный канал) со ссылкой и датой. Плюс track_id и developer сверены по публичному lookup или снимку Wayback. Нет подтверждения — нет пары.

Есть два уровня. `link_verified=True`: ссылка банка прослеживается до этого track_id. `link_verified=False`: банк сам назвал приложение («в App Store приложение называется «Сириус»»), но его ссылка ведёт на свою промо-страницу или смартлинк, который сейчас до id не довести. Тогда id и developer взяты из снимка Wayback с точным названием и той же датой, и такое приложение в архиве единственное. **Решение Лены, LEGAL.md §1.13 (`fa62402`):** `BUILTIN_STRICT = True` по умолчанию, работает только уровень A. Запись уровня A обязана иметь `post_url` (пост или страница банка), `archive_url` (снимок Wayback именно этого поста) и `checked_date`; `builtin_entries()` проверяет это (`tier_a_ok`) и запись без архивной копии не отдаёт. Уровень B лежит в данных, но выключен.

### Уровень A: архивные копии (сверено 10.10.2026)

| track_id | Источник банка (`post_url`) | Архивная копия (`archive_url`) | Что в копии | `developer_is_bank` |
|---|---|---|---|---|
| ВТБ Онлайн 472951966 | https://www.vtb.ru/personal/online-servisy/vtb-online/ (официальный сайт) | https://web.archive.org/web/20200101102720/https://www.vtb.ru/personal/online-servisy/vtb-online/ | ссылка `itunes.apple.com/ru/app/id472951966` | True (VTB Bank (PJSC)) |
| Альфа-Банк 353127685 | https://alfabank.ru/everyday/online/ (официальный сайт) | https://web.archive.org/web/20200106203459/https://alfabank.ru/everyday/online/ | ссылка `itunes.apple.com/ru/app/id353127685` | True (AO ALFA-BANK) |
| Делим Вместе 6739035108 | https://t.me/AlfaBank/2896 | https://web.archive.org/web/20250711101351/https://t.me/s/AlfaBank/2896 | текст «Установите на айфон приложение Делим вместе» и ссылка `trk.mail.ru/c/g5rzb1`; её снимок https://web.archive.org/web/20250711070157/https://trk.mail.ru/c/g5rzb1 — 302 на `apps.apple.com/ru/app/id6739035108` | False (Eyup KECIYOKUSU) |
| Drive Transit 6760469916 | https://t.me/tbank/10591 | https://web.archive.org/web/20260421032214/https://t.me/tbank/10591 | текст «…называется Drive Transit» и ссылка `l.tbank.ru/tg_ios`; её снимок https://web.archive.org/web/20260421024133/https://l.tbank.ru/tg_ios — 302 на `apps.apple.com/app/id6760469916` | False (Amitabh Kulkarni) |

У ВТБ и Альфы источник — страница официального сайта, а не пост в канале (у оригиналов прямую ссылку давал сайт). Если по §1.13 нужен именно пост, это вопрос к Лене.

### «Сириус» (6749962031): остаётся уровнем B

Искал 10.10.2026 официальный пост ВТБ с прямой ссылкой на `id6749962031`: поиск по каналу t.me/s/bankvtb (`Сириус`, `6749962031`, `App Store`), посты 3591, 3593 (05.06.2026) и 3785 (11.09.2026), Wayback CDX по `vtb.ru/promo/ustanovite-vtbonline-ios*`, веб-поиск по `id6749962031`. **Поста с прямой ссылкой нет.** Посты 3591/3593 ведут на `vtb.ru/promo/ustanovite-vtbonline-ios/` (копия поста: https://web.archive.org/web/20260605085420/https://t.me/bankvtb/3591). У этой страницы в архиве есть только снимок от 13.09.2026: на нём «Установите ВТБ Онлайн в отделениях ВТБ», ссылки на App Store нет. Июньской версии в архиве нет. Поэтому «Сириус» во встроенный список не входит и находится только через поиск в архиве (при включённом переключателе). Этот путь проверен на живом Wayback: запрос «сириус» по витрине `us` вернул первым 6749962031 «Cириус», Sergei Smirnov, снимок 20260605.

Для всех 9 записей публичный iTunes lookup по id в `ru` и `us` вернул `resultCount: 0`, то есть приложения удалены. Проверено 10.10.2026.

| # | Приложение в App Store (track_id) | Разработчик в App Store | Чем является | Уровень | Чем подтверждено |
|---|---|---|---|---|---|
| 1 | ВТБ Онлайн (472951966), bundle ru.vtb24.mobilebanking.iphone | VTB Bank (PJSC) | ВТБ Онлайн (оригинал) | link ✅ | Официальный сайт vtb.ru/personal/online-servisy/vtb-online/ (снимок Wayback 01.01.2020) ссылается на itunes.apple.com/ru/app/id472951966. Страница приложения, Wayback 28.11.2021 |
| 2 | Альфа-Банк (353127685), com.alfabank.app | AO ALFA-BANK | Альфа-Банк (оригинал) | link ✅ | Официальный сайт alfabank.ru/everyday/online/ (Wayback 06.01.2020) ссылается на itunes.apple.com/ru/app/id353127685. Страница приложения, Wayback 08.03.2021 |
| 3 | Делим Вместе (6739035108), com.splitactivities.app | Eyup KECIYOKUSU | Альфа-Банк | link ✅ | https://t.me/AlfaBank/2896, **11.07.2025** 09:00 МСК: «Установите на айфон приложение Делим вместе». Ссылка поста trk.mail.ru/c/g5rzb1 → apps.apple.com/ru/app/id6739035108 (проверено 10.10.2026). Снимок Wayback 15.07.2025 с `mt_click_id=mt-g5rzb1…` |
| 4 | Drive Transit (6760469916), com.delivery.drive.almagul.daurbayeva | Amitabh Kulkarni | Т-Банк | link ✅ | https://t.me/tbank/10591, **20.04.2026**: «наше новое приложение для iOS, которое называется Drive Transit: l.tbank.ru/tg_ios». Архив этой ссылки (Wayback 21.04.2026) — 302 на apps.apple.com/app/id6760469916. Снимок страницы 14.04.2026 |
| 5 | Cириус (6749962031), com.sm.FlowTime | Sergei Smirnov | ВТБ Онлайн | name | https://t.me/bankvtb/3591, **05.06.2026** 08:00 МСК: «ВТБ Онлайн вернулся на iOS… в App Store приложение называется «Сириус»». Ссылка на vtb.ru/promo/ustanovite-vtbonline-ios/, июньская версия не сохранена. Wayback 05.06.2026 10:46 МСК: apps.apple.com/us/app/cириус/id6749962031, единственный такой slug. Ещё https://t.me/bankvtb/3785 (11.09.2026): «Если у вас установлен «Сириус»». Совпадает с прессой (bg.ru 05.06.2026: та же ссылка id6749962031 и разработчик Sergei Smirnov) |
| 6 | Активы Онлайн (6742457200), com.assetsonline.ios | Aidai Zamirbekova | СберБанк Онлайн | name | https://t.me/sberbank/4469, **25.08.2025**: «Скачайте новое приложение АКТИВЫ ОНЛАЙН в App Store». https://t.me/sberbank/4475 (25.08.2025): «новое приложение Сбера — АКТИВЫ ОНЛАЙН». Ссылка — смартлинк sberbank.com/sms/active. Wayback 25.08.2025: единственное приложение с таким slug |
| 7 | DriveInvest (6748446597), id.drive.invest | Patrick Warsito | СберИнвестиции | name | https://t.me/sberbank/4490, **28.08.2025** 09:45 МСК: «DRIVEINVEST недоступно в App Store. Наше новое приложение теперь там не скачать». Wayback 28.08.2025 12:22 МСК: единственный такой slug |
| 8 | My Products (6747703692), com.myproducts.myproductsapp | Paul Carney | Альфа-Инвестиции | name | https://t.me/AlfaBank/2998, **20.08.2025**: «Наше приложение My Products до сих пор не удалили». Ссылка alfa.me/SIF801 сейчас 403, в архиве заглушка. Wayback 28.08.2025: единственное приложение с точным названием «My Products» в 2025 году |
| 9 | Office-Capital (6744684419), com.dreamgoods.officecapital | Yauheni Pazniak | Т-Инвестиции | name | https://t.me/tbank/9439, **24.06.2025**: «Т-Инвестиции выпустили новое официальное приложение в App Store. Обновленная версия называется Office-Capital». Ссылка tbinv.onelink.me. Wayback 21.06.2025: единственный такой slug |

Официальные каналы: @bankvtb (ВТБ, 490K), @AlfaBank (984K), @sberbank (739K), @tbank (332K). Каналы открывались через публичный веб-просмотр t.me/s/…, без входа.

### Не включено (подтверждения по правилу нет), только Wayback-поиск по названию

- **СберБанк Онлайн (492224193)** и **Тинькофф (455652438)**: оригиналы, на снимках Wayback разработчик сам банк («Сбербанк России», «Tinkoff Bank»). Но ссылки с официального сайта на этот id в архиве не нашлось (проверены страницы sberbank.ru 2020 и tinkoff.ru 2019–2021). По запросу «сбербанк онлайн» Wayback-поиск находит 492224193 первым.
- **Toastmas (Т-Банк, 6774629936)**: официальный пост https://t.me/tbank/10833 (23.06.2026), но в архиве нет снимка со статусом 200, поэтому разработчика не сверить. Ссылка t.tb.ru/public_june сейчас ведёт на «app_unavailable».
- **«Семейный Онлайн», «Актив» (Сбер, 2026)**: Сбер в официальном канале сообщил только об удалении (t.me/sberbank/5852, 5874). track_id в архиве не нашёлся.
- **Kafario (ВТБ Мои Инвестиции, 6803519628)**: официальный пост https://t.me/bankvtb/3828 (02.10.2026) с прямой ссылкой. Но приложение сейчас **живое** (lookup: SAMUEL COMPANY LIMITED), его найдёт обычный поиск App Store. В список удалённых не вносил.
- Клоны 2022–2023 («Баланс Онлайн», «Прайм баланс», «Всё просто», «СБОЛ», «Инвестр», «Деньги пришли/всем/в порядке/есть»): о них знаю только из прессы, официальных постов банков с названием не нашёл. Не включены.

## Как обновлять список

1. Найти официальный пост банка с названием (t.me/s/<канал>?q=<название>) и записать ссылку с датой.
2. Найти id: Wayback CDX по slug, сверить, что совпадают название, дата и единственность.
3. Снять разработчика и bundle через `parse_snapshot` со снимка.
4. Проверить lookup `ru`/`us` = 0.
5. Добавить `BuiltinEntry` с `evidence`, `link_verified`, `developer_is_bank`, `post_url`, `archive_url` (снимок Wayback самого поста; если его нет, сохранить через web.archive.org/save вручную) и `checked_date`. Тесты `test_builtin_data_integrity` и `test_tier_a_has_post_archive_copy_and_date` это проверяют.
