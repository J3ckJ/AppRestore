# AppRestore: правовая и security-проверка (L-1)

**Автор:** Лена (QA и правовой ревьюер) · **Дата:** 10.10.2026, МСК  
**Что проверено:** черновик PR #8 (`cursor/redesign-704e`, коммит `43347fa`), PR #11 (`ipatool/bench`, коммит `a0276ba`), исследование Макса M-2 (`/workspace/apprestore/maks-research/app-store-mechanisms.md`), исходники ipatool `cde7d00` (Account с полем `Password`, `buyProduct` только при `price=0`).  
**Статус:** черновик для решения Евгения. Это **не юридическое заключение**: я не юрист по праву США, РФ или ЕС, гарантий нет. Где написано «уточнить», нужно решение Евгения, а при желании — консультация юриста.

Разбор региона/storefront: [`docs/research/region.md`](research/region.md).

Короткая версия для Евгения: [`docs/drafts/legal-note-ru.md`](drafts/legal-note-ru.md).

---

## 1. Рамка

### 1.1. Источники (только публичные, просмотрены 10.10.2026)

| Документ | Ссылка | Что важно |
|---|---|---|
| Apple Media Services Terms and Conditions (US, «Last Updated: September 14, 2026») | https://www.apple.com/legal/internet-services/itunes/us/terms.html | A (Home Country), B (Transaction, в т.ч. бесплатный), C (Account), F (Usage Rules), G (Termination), H (Downloads/Redownload), J (Availability), M (Family Sharing), O (App Store + Standard EULA) |
| То же для России | https://www.apple.com/legal/internet-services/itunes/ru/terms.html | Для RU-аккаунтов — своя редакция; цитаты ниже по US-версии, RU сверить перед релизом (**уточнить**) |
| Лицензия ipatool | https://github.com/majd/ipatool/blob/main/LICENSE | MIT © 2021 Majd Alfhaily; совместима с GPL-3.0 при сохранении copyright (проверить `THIRD_PARTY_NOTICES.md`) |
| Лицензия AppRestore | `LICENSE` (GPL-3.0) | §§ 15–16: без гарантий, без ответственности автора |

### 1.2. Цитаты из условий Apple (US-версия, дословно)

- **A. Home Country:** «Our Services are available for your use in your country or territory of residence (“Home Country”). By creating an account for use of the Services in a particular country or territory you are specifying it as your Home Country.»
- **B. Бесплатное = сделка:** «You can acquire Content on our Services for free or for a charge, either of which is referred to as a “Transaction.”» и «All Transactions are final.» → `buyProduct` / `--purchase` для бесплатного — такая же сделка, отменить нельзя.
- **C. Account:** «you are responsible for maintaining its confidentiality and security.»
- **F. Usage Rules** (нарушение = «material breach»):
  - «You may access our Services only using Apple’s software, and may not modify or use modified versions of such software.»
  - «You may not tamper with or circumvent any security technology included with the Services or Content.»
  - «You may not use any software, device, automated process, or any similar or equivalent manual process to scrape, copy, or perform measurement, analysis, or monitoring of, any portion of the Content or Services.»
  - «You may not manipulate play counts, downloads, ratings, or reviews via any means — such as (i) using a bot, script, or automated process…»
- **G. Termination:** «If you fail, or Apple suspects that you have failed, to comply … Apple may, without notice to you: (i) terminate this Agreement and/or your Apple Account …»
- **H. Downloads / Redownload:** «You may be able to redownload previously acquired Content (“Redownload”) to your devices that are signed in with the same Apple Account» и «Content may not be available for Redownload if that Content is no longer offered on our Services.»
- **J. Availability:** «Certain Services and Content available to you in your Home Country may not be available to you when traveling outside of your Home Country.»
- **M. Family Sharing:** «All Family members must share the same Home Country.» «Not all Content … and some previously acquired Apps, are eligible for Purchase Sharing.»
- **O. Standard EULA (a):** лицензия «nontransferable … on any Apple-branded products that you own or control»; запрет reverse-engineer / modify. **(g)** экспортные ограничения США, в т.ч. SDN List.

### 1.3. Выводы для проекта (честно)

1. **Неофициальный клиент — серая зона.** П. F «only using Apple’s software» прямо про это. ipatool (и AppRestore поверх него) — не ПО Apple; плюс SAP/kbsync запускает куски CommerceKit/CoreFP в Unicorn. Устранить кодом нельзя — только описать и не усугублять. Практический риск: ограничение/блокировка Apple ID (п. G), не массовый иск. Публично известных массовых блокировок за ipatool не видела — это не гарантия.
2. **По смыслу условий допустимо:** redownload уже приобретённого на этом Apple ID (п. H) и явное, по желанию пользователя, получение бесплатного, которое Apple сама предлагает в его Home Country (п. B).
3. **Точно нельзя:** обход DRM/FairPlay, чужие аккаунты, платные без оплаты, подмена страны (A/M), массовые автоматические пробы каталога/лицензий (F), накрутка загрузок.
4. **Санкционный контекст (аккуратно, без гарантий).** Полка `popular_apps.py` — банки РФ, VK, MAX, убранные Apple из RU App Store; для части банков это связано с санкциями США (OFAC). П. O(g) EULA запрещает экспорт приложения лицам из SDN и в страны эмбарго. AppRestore не раздаёт IPA: файл отдаёт сервер Apple на Apple ID с лицензией. Но полка «вот приложения, которые Apple убрала» — **позиционирование продукта**. Риск для автора: жалоба Apple/GitHub, удаление репо/релизов. Риск для пользователя: блокировка Apple ID или отзыв redownload (п. H/J). Санкционную оценку я дать не могу — **уточнить у юриста, если полка остаётся**.

### 1.4. Базовые правила продукта (обязательные для всех PR)

| # | Правило | Где проверять |
|---|---|---|
| R1 | Только свой Apple ID пользователя. | README, экран входа, тексты |
| R2 | `ipatool download` **никогда** не несёт `--purchase`. Лицензия — отдельный шаг `ipatool purchase`. | `tools.py::download_ipa`, `shelf_probe.py`, `bench_restore.py` |
| R3 | Лицензию берём **только** для `price == 0` по lookup **до** вызова; для кнопки «Поставить» — по клику пользователя + текст у кнопки или разовое согласие при первом использовании; **не** фоном пачкой. Журнал + лимит. | `service.py`, `quick_session.py`, QML |
| R4 | Никаких фоновых/пачечных проб лицензий без пользователя. | `quick_session.py::_probe_shelf` |
| R5 | Storefront/страну не подменяем; VPN-«смену региона» не советуем. | `errors.py`, тексты |
| R6 | Пароль Apple ID, 2FA, passphrase связки, токены, cookies, DSID, email, UDID не в логах, «Журнале» GUI, отчётах, скриншотах, git. | `auth_pty.py`, `main_window.py::log`, bench |
| R7 | Выход = удалить live-сессию ipatool **и** копию в vault. | `quick_session.py::signOut`, `main_window.py::_revoke` |
| R8 | Дисклеймер в README и в программе: «не связан с Apple», «неофициальный клиент, Apple может ограничить Apple ID», «свой Apple ID», «бесплатное получение — сделка, отменить нельзя». | README (частично есть), QML |

---

## 2. Разбор PR #8 (`cursor/redesign-704e`)

Легенда: ✅ можно · ⚠️ можно при условиях · ⛔ нельзя (в текущем виде) · ❓ уточнить.

### 2.1. `apprestore_gui/shelf_probe.py` — ⛔ нельзя в текущем виде

**Что делает код (факт):**
- `probe_store()` сначала `_attempt(..., purchase=False)` (`ipatool download --app-id …`), при «license is required» **сразу** `_attempt(..., purchase=True)` — тот же `download` с `--purchase` (стр. 43–51, 73–75).
- Остановка на первом проценте (`stop_when` / `_PERCENT`) **не спасает**: при `--purchase` ipatool сначала делает `buyProduct`, потом качает. К моменту «1 %» лицензия уже на аккаунте. `granted` = «лицензия выдана».
- `quick_session.py::_probe_shelf` (~1210–1236) гоняет это по **всем 33** `POPULAR_APPS` в фоне (до 15 мин ожидания сессии), пишет в `%TEMP%/apprestore-shelf-check.txt` и stdout. Включается `APPRESTORE_PROBE_SHELF=1` — отладка, но код в сборке.
- Цена не проверяется до вызова: защита от платных — только отказ ipatool («purchasing paid apps is not supported»). Для delisted lookup цены нет → теоретический риск неясного ответа сервера.
- Нарушает R2 (purchase внутри download), R3 (нет согласия на каждое приложение), R4 (пачка без UI).

**Вердикт:** ⛔ нельзя — это **не** то же самое, что кнопка «Поставить» (§2.5.A).  
Здесь лицензии добавляются **без действия пользователя** по пачке приложений (п. B + п. F AMS Terms). Продуктовый один клик «Поставить» Евгений оставил; фоновую пробу — нет.

**Риск:** незаметное добавление лицензий пачкой; шум на аккаунте (в т.ч. delisted банки); automated process (п. F).  
**Правка (конкретно):**
1. В `shelf_probe.py` **удалить** ветку `purchase=True`. Проба = только `download` без `--purchase` (или `list-versions` / `list-purchases`). Исходы: `licensed` / `needs-license` / `unavailable` / `closed` / `error`. Никакого `granted`.
2. `_probe_shelf` — только read-only при env-флаге; в релизе не документировать. Либо вынести в `scripts/`.
3. Путь «Поставить» (§2.5.A) может по-прежнему брать бесплатную лицензию **после клика пользователя** — это отдельно.

### 2.2. `apprestore_gui/popular_apps.py` — ⚠️ можно при условиях

**Что делает:** статичный список ~33 adamId (банки РФ, VK, MAX, Mail.ru…) с пометками «последняя известная карточка до снятия». Иконки с сайтов банков при отсутствии artwork Apple. Сам файл **не** вызывает Apple и **не** меняет лицензию.

**Вердикт:** ⚠️ можно при условиях.  
**Риск:** позиционирование «восстановим убранные банками/VK» + санкционный контекст (§1.3 п.4); ложные ожидания (без лицензии Apple не отдаст — Макс проверил: lookup/MDM по 33 id × 7 стран = пусто).  
**Правка:**
1. В UI у каждой карточки полки: «Поставим, только если это приложение уже было на вашем Apple ID. Новую лицензию для снятых Apple обычно не выдаёт.»
2. Кнопка полки **никогда** не зовёт `--purchase` / `acquire_license=True` (сейчас QML `installStore` всегда `acquire=True` — см. §2.5). Для полки: только `acquire=False`; при 9610 — честное «на этом Apple ID лицензии нет».
3. Не обещать в README/постах «вернём любой банк из списка».
4. ❓ Евгению: оставить полку как есть / сузить (только соцсети) / убрать до консультации юриста.

### 2.3. `apprestore_gui/account_vault.py` — ⚠️ можно при условиях

**Что делает:** копирует файлы live-сессии ipatool (`account`, `cookies`, `kbsync-v1`) в `~/.apprestore/keychains/<email>/`. Пароль Apple ID в vault **не пишется отдельно**. Но в исходнике ipatool `pkg/appstore/account.go` структура `Account` содержит поле `Password` — оно лежит **внутри** зашифрованного blob `account` (FileBackend keyring + passphrase). Копия = копия уже зашифрованного blob, не plaintext.

**Вердикт:** ⚠️ можно при условиях.  
**Риск:** несколько сессий = больше поверхность кражи с диска; перепутать активный аккаунт; в `main_window._revoke` **нет** вызова `forget_session` (в `quick_session.signOut` — есть) → при выходе из старого GUI копия в vault остаётся; нет `chmod 0o600` / `0o700` на файлы vault (в отличие от `service.py` library).  
**Правка:**
1. `account_vault.py`: после `_copy_session` выставить `chmod 0o700` на каталог и `0o600` на файлы; `vault_root().mkdir(mode=0o700)`.
2. `main_window._revoke`: после `revoke()` вызывать `forget_session(email)` для активного email (как в `quick_session.signOut`).
3. Не хранить passphrase в файле; в памяти на сессию — ок (уже так).
4. UI: при переключении аккаунта явно показывать «сейчас: email@…».
5. Документировать в SECURITY.md: что в blob, права на файлы, что делает «Выйти».

### 2.4. `auth_pty.py` / вход — ⚠️ можно при условиях

**Факт:** пароль/2FA/passphrase идут в stdin pty, не в argv/env (`tools.py::_ipatool_cmd` это комментирует). `_visible_output` маскирует секреты в transcript. На POSIX `_login_posix` передаёт `on_output(text)` **до** маскировки — если GUI пишет сырой вывод в лог, секрет может утечь. Windows/ConPTY-путь маскирует до `on_output`.

**Правка:** в `_login_posix` пропускать через `_visible_output` перед `on_output`; не писать email в verbose-логи; после успеха не оставлять password/code в полях ввода (проверить QML/Widgets).

### 2.5. Два разных сценария с лицензией (не смешивать)

#### 2.5.A. Кнопка «Поставить» с авто-получением бесплатной лицензии — ⚠️ можно при условиях

**Решение Евгения (10.10.2026):** осознанное продуктовое решение — если лицензии нет, кнопка «Поставить» сама получает бесплатное приложение на Apple ID пользователя (как «Get» в App Store). Сценарий в один клик **не ломаем**.

| Место | Поведение сейчас | Вердикт |
|---|---|---|
| `tools.py::download_ipa` | `--purchase` только при `purchase=True` | ⚠️ ок как API; лучше со временем вынести в отдельный `ipatool purchase` (как bench PR #11), но не блокер |
| `service.py::_build_download_attempts` | `--purchase` только при `acquire_license=True` | ✅ |
| `main_window` галочка «Получить бесплатно» | По умолчанию выкл. | ✅ совместимо; в новом GUI — текст/разовое согласие вместо обязательной галочки каждый раз |
| `quick_session.installStore` / QML «Поставить» | `acquire=True` | ⚠️ можно при условиях ниже |
| `quick_session._save_copies` | `acquire_license=True` всегда | ⚠️ лучше `False`: сохранение копии с телефона ≠ установка с полки; не должно молча плодить лицензии |

**Минимальные условия (не ломают один клик):**
1. **Только бесплатные:** перед `purchase` / `download --purchase` — lookup цены; при `price > 0` или цене неизвестной для *новой* лицензии — отказ, платные никогда. (Уже owned без новой лицензии — качаем как сейчас.)
2. **Понятный текст рядом с кнопкой** *или* **одноразовое согласие при первом использовании** (не каждый клик): «Если приложения ещё нет на вашем Apple ID, оно будет получено бесплатно на ваш Apple ID — как кнопка Get в App Store. Это нельзя отменить.»
3. **Журнал** взятых лицензий (adamId, bundleId, имя, storefront, время, результат) **без** Apple ID.
4. **Лимит** как подтверждённый для bench: 5/сутки и 15 всего (настраиваемо).
5. Для **полки delisted** (§2.2): авто-purchase почти наверняка получит отказ Apple; UI не должен обещать успех. Имеет смысл после первого `needs-license` на delisted не долбить purchase повторно.

#### 2.5.B. Фоновая массовая проба `shelf_probe` с `--purchase` — ⛔ нельзя

Это **другое**, не сценарий «Поставить».

- `shelf_probe.probe_store` при «license is required» сам вызывает `download --purchase` (стр. 43–51, 73–75).
- `quick_session._probe_shelf` гоняет это по **всем ~33** `POPULAR_APPS` в фоне при `APPRESTORE_PROBE_SHELF=1`, без нажатия пользователя на конкретное приложение.
- Успех = лицензии добавлены на аккаунт пачкой, без согласия и без одного клика «Поставить».

**Вердикт:** ⛔ нельзя (подтверждено решением Евгения 10.10: фоновая проба ≠ продуктовый один клик).  
**Правка:** убрать `--purchase` из `shelf_probe` полностью; проба только read-only. Env-флаг не документировать как фичу релиза. Подробности — §2.1.

### 2.6. Секреты в логах — ⚠️ почти ок, добить

| Место | Статус |
|---|---|
| `auth_pty._visible_output` | маскирует password/code/passphrase |
| POSIX login `on_output` | сырой вывод до маскировки — починить |
| `main_window.log` | не пишет пароли; может писать имена приложений — ок |
| `shelf_probe` stdout | пишет name + storeId — ок; не пишет email |
| `account_bindings` | хранит udid→email в `~/.apprestore/device-accounts.json` без chmod 0o600 — починить |
| Bench PR #11 | `mask_text` / `mask_obj`, download без `--purchase`, журнал без Apple ID — ✅ образец |

### 2.7. Сводка правок по PR #8 (для Димы / Макса)

1. `shelf_probe.py` — убрать `--purchase` полностью (фоновая проба).  
2. Кнопка «Поставить» — **оставляем** авто-license; добавить: `price==0`, текст/разовое согласие, журнал, лимит (§2.5.A).  
3. `_save_copies` — предпочтительно `acquire_license=False`.  
4. `account_vault` + bindings — chmod; `_revoke` → `forget_session`.  
5. `_login_posix` — маскировать до `on_output`.  
6. Дисклеймер на экране входа (R8).  
7. Полка — честный текст; авто-purchase с полки не обещать успех на delisted.

---

## 3. Таблица механизмов (M-2 Макса / PR #11)

Источник: `maks-research/app-store-mechanisms.md` (10.10.2026). Колонка заполнена Леной.

| # | Механизм | Вердикт | Условия / правка | Риск |
|---|---|---|---|---|
| 1 | Цепочка download: ent/download (kbsync) → volumeStore → redownload → updateProduct | ✅ | Уже внутри `ipatool download`; не терять при обновлении; метрики в bench | Технический (SAP ~1.2 ГБ); правовой в рамках серой зоны неофициального клиента (§1.3 п.1) |
| 2 | `list-versions` + `download --external-version-id` | ✅ | Только для приложений с лицензией на этом Apple ID; UI: «версия, которую Apple ещё отдаёт вам», без обещания всех старых билдов | Ложные ожидания; 9610 без лицензии |
| 3 | MDM / consumer lookup (`uclient-api`) | ✅ | Только неаутентифицированный каталог (иконки, min iOS, device family, appExtVrsId); кэш; не для delisted | П. F «monitoring» — единичный lookup по действию пользователя ок; не долбить пачкой в фоне |
| 4 | `buyProduct` / `--purchase` (бесплатная лицензия) | ⚠️ | По клику «Поставить»: `price==0`, текст/разовое согласие, журнал, лимит (§2.5.A). Фоновая проба — ⛔ (§2.5.B). Предпочтительно отдельный `ipatool purchase` | Сделка (B); 5002/2059/3038; delisted почти всегда отказ |
| 5 | `list-purchases` (Purchase DAAP) | ✅ | Только свой аккаунт; «Мои покупки» / сверка с полкой; не светить токены; кэш локально | Сессия + SAP; неполнота списка |
| 6 | HTTP Range / докачка | ✅ | Не удалять `.tmp` при ошибке; ретраи транспорта; метрика resume в bench | Нет правового; CDN может игнорировать Range |
| 7 | `--format json` + каталог ошибок | ✅ | Слой `ipatool_api.py`; verbose metadata (заголовки сессии) **не** в логи | Утечка заголовков при verbose |
| 8 | Вход / 2FA / токен / refresh-session | ⚠️ | Секреты только через pty; маскировка; не логировать email; backoff при 429; не долбить пароль | Блокировка при частых неудачных логинах (G); пароль в keychain blob |
| 9 | Несколько Apple ID (vault / state-dir) | ⚠️ | Условия §2.3; не подменять storefront «чужой страной» | Путаница аккаунтов; кража с диска |
| 10 | Платформы / device family (`--platform iphone\|ipad\|…`) | ✅ | Выбор по реальной модели устройства; iPad-пакет — для iPad; не как обход региона | Путать platform с регионом нельзя |
| 11 | Delisted: когда Apple ещё отдаёт | ⚠️ | Только при уже имеющейся лицензии; UI без гарантий; матрица D на аккаунте Евгения перед обещаниями | Ложные обещания; полка — §2.2 |
| 12 | Storefront / регион | ⛔ подмена заголовка · ✅ честные ошибки · см. [`research/region.md`](research/region.md) | Подмена `X-Apple-Store-Front` в клиенте **технически частична и обычно бесполезна** (storefront с логина, токен/DSID привязаны к аккаунту; в нашей линии нет `--country`). Легитимно: официальная смена страны Apple ID или второй свой Apple ID. UI: «страну программа не меняет» | AMS Terms A/M; риск блокировки; ложные ожидания |
| 13 | Параллельность / очередь загрузок | ⚠️ | Лимит 2–3; не параллелить login; общий rate-limit на auth (429) | 429; гонки vault |
| 14 | Публичный iTunes Search/Lookup | ✅ | Уже есть; кэш; для delisted бесполезен | Нет |
| 15 | HTML `apps.apple.com` / serialized-server-data | ⚠️ | Запасной источник appExtVrsId/иконок по действию пользователя; не массовый scrape (F) | Вёрстка меняется; п. F scrape |
| 16 | Зеркало SAP `swdist.apple.com` | ✅ | Тот же официальный пакет Apple; fallback при недоступном swcdn | Нет правового |
| 17 | Каталог кодов ошибок | ✅ | Маппинг в `errors.py`; без сырых CustomerMessage с PII в лог | Путаница 5002 (purchase vs volumeStore) |
| 18 | Machine auth / VPP / backgroundUpdate | ⛔ | Не внедрять без отдельного «да» Евгения; VPP — корпоративные лицензии, не наш сценарий | Серая зона, чужие лицензии |
| 19 | `--dry-run` / превью без файла | ✅ | Эмуляция через list-versions / metadata **без** `buyProduct`; не путать с shelf_probe на purchase | Путаница с пробой лицензии |
| 20 | DRM / FairPlay / чужие аккаунты / платный buy без оплаты / подмена StoreFront | ⛔ | Сознательно вне проекта (рамка брифа) | Пиратство / обход защиты (F) |

### 3.1. Особый фокус (по запросу)

- **Смена storefront/региона:** подмена заголовка в клиенте — ⛔ / технически обычно бесполезна (см. [`research/region.md`](research/region.md)). Легитимно: официальная смена страны Apple ID или второй свой аккаунт. UI: «страну магазина программа не меняет».
- **iPad-вариант (`--platform ipad`):** ✅ можно — это выбор семейства пакета под устройство пользователя, не смена региона. Связать с моделью из lockdown.
- **list-purchases:** ✅ основа «Мои покупки» и честной полки.
- **list-versions + externalVersionId:** ✅ главный запрос сообщества; только при лицензии.
- **MDM lookup:** ✅ для живых карточек; для полки delisted бесполезен.
- **Range-докачка:** ✅ только надёжность, права не затрагивает.

---

## 4. Security-чеклист (к L-4, черновик)

| Проверка | Сейчас | Нужно |
|---|---|---|
| download без `--purchase` по умолчанию | да (`acquire_license` gate) | probe и QML не обходят |
| Пароль не в argv/env | да | держать |
| Маскировка в transcript | Windows ок; POSIX — чинить | |
| Vault без plaintext пароля | да (шифроblob ipatool) | chmod; forget при выходе из Widgets |
| Выход чистит сессию | Quick: да; Widgets: revoke без forget_session | починить |
| Bindings udid→email | файл есть | chmod 0o600 |
| SECURITY.md версии | таблица 0.2.x | обновить на 0.3.x |
| Bench без секретов | PR #11 ок | образец для GUI-логов |

---

## 5. Блокеры и открытые решения

1. **shelf_probe с `--purchase`** — ⛔ не в релиз, пока purchase убран из фоновой пробы (§2.1 / §2.5.B).  
2. **«Поставить» + авто-license** — решение Евгения: оставляем; до releasable добить `price==0`, текст/разовое согласие, журнал, лимит (§2.5.A).  
3. **Полка банков** — продуктовое/санкционное: A / B / C (§2.2).  
4. **Живые прогоны A–G и гипотезы region.md** — после входа Евгения; без фоновых пачек purchase.  
5. **RU-редакция AMS Terms** — сверить перед релизом с дисклеймером.  
6. **Подмена storefront** — не внедрять; разбор: [`docs/research/region.md`](research/region.md).

---

## 6. История

| Дата | Что |
|---|---|
| 10.10.2026 | L-1: рамка, разбор PR #8, таблица 20 механизмов M-2, записка Евгению |
| 10.10.2026 | Уточнение Евгения: «Поставить»+авто-license → ⚠️ при условиях; shelf_probe → ⛔; добавлен `research/region.md` |
