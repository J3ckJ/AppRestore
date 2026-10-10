# Карта ipatool в AppRestore (M-1)

Как AppRestore собирает и вызывает ipatool, чем наша сборка отличается от
релиза 2.6.0, где именно мы разбираем текст вывода (хрупкие места) и где уже
можно перейти на структурированный `--format json`.

> Правила направления: только Apple ID самого пользователя и официальный способ
> получения лицензии у Apple. Без пиратства и обхода DRM. `--purchase` — только
> с явного согласия владельца Apple ID и только для бесплатных приложений.
> Каждый новый механизм проходит правовую проверку у Лены.

## 1. Что мы берём из upstream

- Источник: `majd/ipatool`, **коммит `cde7d00`** (2026‑10‑06), не релиз. Версия,
  которую бинарник сообщает, остаётся `2.6.0` (ldflags в
  `packaging/build-ipatool.sh`).
- Сборка: `packaging/build-ipatool.sh <windows-amd64|macos-arm64|macos-amd64>`.
  Go‑сборка с `-trimpath -buildvcs=false`, для macOS включён CGO с
  `-mmacosx-version-min=10.15`. После сборки скрипт проверяет, что в бинарнике
  есть строки `too many authentication redirects` и
  `unsupported authentication redirect status` — это маркеры, которых в 2.6.0 нет.
- Доставка: `packaging/fetch_ipatool.py` качает готовый архив из нашего
  pre‑release `ipatool-2.6.0-redirect` на GitHub и сверяет SHA‑256
  (`ARCHIVE_SHA256`). `resolve_tool("ipatool")` ищет бинарник рядом с
  `sys.executable` (замороженный GUI), затем в PATH.
- ipatool ≥ 2.4 подписывает вход через SAP: при первом входе качается и
  кэшируется Unicorn‑рантайм (см. `apprestore_core/tools.py`,
  `_ipatool_sap_runtime_check`). Этого рантайма нет в релизе — это нормально.

## 2. Отличия нашей сборки `cde7d00` от релиза `2.6.0`

`cde7d00` — это `v2.6.0..cde7d00` = 16 коммитов (и он же вошёл в вышедший позже
`v2.7.0`; наша сборка — по сути ранний 2.7.0 под именем 2.6.0). Главное
(`git log v2.6.0..cde7d00`, diff ~5330/‑257 строк):

| Область | Что изменилось относительно 2.6.0 | Чем полезно нам |
|---|---|---|
| **Вход: редиректы** | Обрабатываются 301/302/307/308 (2.6.0 знал только 302), запросы подписаны и сохраняются между редиректами, нормализация первого URL входа | Ради этого и собираем: pod, отвечающий 301, больше не ломает вход до проверки пароля |
| **Вход: устойчивость** | `bound authentication requests and retry transport failures`, выбор стабильного machine identity по сетевым адаптерам, восстановление «затёртых» MAC на macOS 27 | Меньше случайных сбоев входа |
| **Вход: телефон/сторфронт** | Логин по номеру телефона; отдельный storefront‑шаг при входе | Шире охват аккаунтов |
| **2FA** | `normalize 2fa input and clarify verification errors` | Понятнее ошибки кода |
| **Скачивание: kbsync** | Новый механизм `kbsync` + кэш для надёжной докачки, переиспользование учётных данных между повторами скачивания | Надёжнее большие загрузки |
| **Старые/делистнутые** | Фоллбэк на consumer‑каталоги для lookup версий iOS, Apple Arcade через catalog API, докачка делистнутых tvOS | Пригодится для M‑2 (старые версии, удалённые приложения) |
| **MCP** | Новая команда `ipatool mcp` (stdio) с инструментами search/download/list‑versions/get‑version‑metadata/list‑purchases/purchase. В 2.6.0 её НЕТ | Альтернатива разбору текста (см. §5) |
| **HTTP** | Явные таймауты запроса/ответа, типизированные transport‑ошибки | Нужно для тайм‑аутов на шаг в M‑4 |

Своих патчей в Go‑код у нас пока нет — мы только фиксируем коммит и пересобираем.

## 3. Какие команды и флаги вызывает AppRestore

Все вызовы идут через `AppRestoreTools._ipatool_cmd(...)` + `Runner.run(...)`
(`apprestore_core/command.py`). Прокси прокидывается через `HTTP(S)_PROXY`
(`_ipatool_env`), т.к. Go не читает системный прокси.

| Где в коде | Команда | Разбор результата |
|---|---|---|
| `tools.py ipatool_authenticated` | `auth info` | только по `returncode` |
| `tools.py ipatool_auth_info` | `--format json auth info` | **JSON** (`parse_json_output`) |
| `tools.py ipatool_login` | `auth login --email <e>` | интерактивно, по `returncode` |
| `tools.py ipatool_revoke` | `auth revoke` | по `returncode` |
| `tools.py download_ipa` | `download (--app-id\|--bundle-identifier) --output [--purchase]` | текст (`_public_tool_failure`) |
| `tools.py search_apps` | `search <q> --limit N --format json` | **JSON** |
| `auth_pty.py` (login/keychain) | `auth login …`, `--non-interactive --format json auth info` | **текст PTY** (пароль/2FA/passphrase по словам) |
| `shelf_probe.py` | `download --app-id <id> --output … [--purchase]` | текст, ловится `downloading N%` |

`--purchase` добавляется только когда `acquire_license=True` и только отдельной
попыткой (`service.py _build_download_attempts`): «never an implicit retry».

## 4. Хрупкие места: где разбирается текст вывода

Это основной технический долг направления — вход и пробы завязаны на английские
фразы и на формат прогресс‑бара, которые Apple/ipatool могут поменять.

1. **`apprestore_gui/auth_pty.py` — весь интерактивный вход.** Промпты ловятся по
   подстрокам:
   - пароль: `_PASSWORD_HINTS = ("password","passwd","пароль")` (стр. ~272);
   - 2FA: `_CODE_HINTS` (`2fa`, `verification code`, `auth code`, `код подтвержд`…)
     (стр. ~274);
   - passphrase связки: `_PASSPHRASE_HINTS = ("passphrase","keychain","связк")`
     (стр. ~284).
   Исход входа — `_explain_login_failure` (стр. ~310): ветки по `http 301`,
   `http 204`, `failed to get bag`, `tls handshake timeout`, `invalid password`.
   Разблокировка связки — `classify_keychain_unlock` (стр. ~379): `could not be
   found`, `handle is invalid`, `invalid/incorrect/decrypt`. `probe_keychain`
   (стр. ~598) различает `in/locked/out` по `passphrase`/`handle is invalid`.
   **Хрупко:** любая смена формулировок ломает автоответы на промпты.
2. **`apprestore_gui/shelf_probe.py` — проба лицензии.** `classify_offer`
   (стр. ~21) читает `passphrase is required`, `failed to purchase`,
   `license is required`, `temporarily unavailable`, и — главное — считает
   загрузку начавшейся по регэкспу `_PERCENT = downloading\s+[1-9]\d*\s*%`
   (стр. ~18). **Хрупко вдвойне:** и тексты, и формат прогресс‑бара. Плюс
   правовой вопрос — использует `--purchase` (к Лене).
3. **`apprestore_gui/quick_session.py`** — прогресс установки по
   `_PROGRESS = downloading\s+(\d+)\s*%` (стр. 35). Тот же риск формата.
4. **`apprestore_gui/errors.py`** — перевод ошибок ipatool на русский целиком по
   спискам подстрок (`_NETWORK`, `_LICENSE`, `_REGION`, `_AUTH`, `_DEVICE`,
   `error="…"` через regex). Большой, но изолированный словарь; обновлять при
   смене формулировок Apple/ipatool.
5. **`apprestore_core/tools.py _public_tool_failure`** (стр. ~39) — вытаскивает
   `error="…"` из лога download; если шаблон не совпал, берёт последние 500
   символов. Это единственное, что видит пользователь при неудачном скачивании.

## 5. Где уже можно перейти на `--format json`

ipatool отдаёт zerolog‑JSON (по объекту в строке; поле `error` при ошибке,
`success`/`output`/`externalVersionIdentifiers` при успехе). Переводимо сейчас:

- **`auth info`** — уже JSON (`ipatool_auth_info`). Эталон.
- **`search`** — уже JSON.
- **`download`** — поддерживает `--format json`: вместо `_public_tool_failure`
  читать поле `error` и `output`/`success`. `bench_restore.py` уже так делает
  (`ipatool_error`, `ipatool_events`).
- **`list-versions`** — `externalVersionIdentifiers` в JSON (нужно для старых
  версий, M‑2/M‑4).
- **`purchase`** — `alreadyOwned`/`success` в JSON (для шага бесплатной лицензии).
- **`auth info` для статуса связки** — `probe_keychain` уже зовёт с `--format
  json`, но решение берёт по тексту; можно по JSON + returncode.

**Нельзя убрать текст:** интерактивные промпты `auth login` (пароль/2FA/
passphrase) — ipatool спрашивает их в stdin, структурированного канала нет. Это
останется в `auth_pty.py`; задача — сузить разбор до самого промпта и покрыть
записанными расшифровками (план M‑3, `ipatool_api.py`). Альтернатива на будущее —
команда `ipatool mcp` (появилась в нашей сборке), но она тоже требует уже
открытой сессии.

## 6. Таблица шагов пути «вход → установка»

| Шаг | Команда ipatool | Формат сейчас | Ошибки (коды) | Быстрый выигрыш |
|---|---|---|---|---|
| Вход | `auth login --email` | текст (PTY) | bad_credentials, auth_redirect, keychain_locked, network | редиректы уже чинит cde7d00; таймаут на шаг (M‑4) |
| Проверка сессии | `auth info` / `--format json auth info` | **JSON** | not_authenticated, token_expired, keychain_locked | не запускать лишний `auth info` после входа (уже сделано) |
| Поиск / lookup | `search --format json`; iTunes Lookup в ядре | **JSON** | app_not_found, region | кэш lookup/иконок (M‑4); storefront‑фоллбэк (M‑2) |
| Лицензия | `download … --purchase` (сейчас как проба) | текст + `downloading %` | license_required, purchase_refused, subscription_required | отдельный `purchase` + `--format json`, только free, с лимитом |
| Скачивание | `download --output` | текст | network, timeout, disk_full | `--format json`; докачка (kbsync уже в сборке); 2–3 параллельно (M‑4) |
| Установка | pymobiledevice3 (вне ipatool) | — | device_locked, device_not_trusted, disk_full | хук Димы (D‑2); понятные сообщения |

## 7. Главные находки (кратко)

1. Наша «2.6.0» — фактически ранний 2.7.0 (`cde7d00`): кроме редиректов входа
   мы уже получили kbsync‑докачку, таймауты HTTP, вход по телефону и MCP. Это
   стоит использовать в M‑4, а не изобретать заново.
2. Самое хрупкое — `auth_pty.py` (промпты по словам) и `shelf_probe.py`/
   `quick_session.py` (прогресс по `downloading N%`). Смена формата ipatool
   ломает вход и прогресс; нужно сузить разбор и покрыть расшифровками.
3. `download`, `list-versions`, `purchase` уже умеют `--format json` — переход
   убирает разбор строк почти везде, кроме интерактивных промптов входа.
4. Быстрые выигрыши по скорости: кэш lookup/иконок (повторный поиск < 0,5 c),
   докачка через kbsync вместо скачивания заново, таймаут на каждый шаг (нет
   зависаний без сообщения), 2–3 параллельные загрузки.
5. Правовой флаг Лене: `shelf_probe.py` использует `--purchase` как пробу —
   успех может добавить лицензию на аккаунт. В bench шаг лицензии вынесен в
   отдельный `purchase`, только free (price==0 по lookup), за флагом и с лимитом.

> Базовые цифры (медиана/p90/доля сбоев по шагам) снимаются скриптом
> `scripts/bench_restore.py` после того, как Евгений выберет Apple ID; таблица —
> к ср 14.10 (прогон на реальном телефоне у Лены/Евгения).
