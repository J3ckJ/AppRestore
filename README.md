# AppRestore

[![CI](https://github.com/J3ckJ/AppRestore/actions/workflows/ci.yml/badge.svg)](https://github.com/J3ckJ/AppRestore/actions/workflows/ci.yml)
[![Release](https://img.shields.io/github/v/release/J3ckJ/AppRestore)](https://github.com/J3ckJ/AppRestore/releases/latest)
[![License: GPL v3](https://img.shields.io/badge/License-GPLv3-blue.svg)](./LICENSE)

```text
     _                ____           _
    / \   _ __  _ __ |  _ \ ___  ___| |_ ___  _ __ ___
   / _ \ | '_ \| '_ \| |_) / _ \/ __| __/ _ \| '__/ _ \
  / ___ \| |_) | |_) |  _ <  __/\__ \ || (_) | | |  __/
 /_/   \_\ .__/| .__/|_| \_\___||___/\__\___/|_|  \___|
         |_|   |_|
Телефон → сгруженные / удалённые → скачать IPA → вернуть
```

**AppRestore** — локальный инструмент, который возвращает приложения на iPhone,
когда App Store и сам телефон уже «забыли» удобный путь обратно.

> **Статус:** beta · Версия 0.2.2. Проверяйте актуальный релиз в GitHub Releases.
> Windows + macOS · USB · ваш Apple ID · без обхода DRM.  
> Проект не связан с Apple Inc.

---

## Зачем это нужно

iOS умеет **сгружать** приложения: ярлык остаётся, бинарник исчезает.  
А бывает хуже: ярлык тоже снесли, страница в App Store умерла, а вам приложение
ещё нужно — и лицензия в аккаунте, скорее всего, жива.

AppRestore закрывает оба сценария из одного меню:

| Сценарий | Что на телефоне | Как возвращаем |
|---|---|---|
| **Сгруженные** | placeholder-ярлык | local IPA / штатный redownload / `ipatool` fallback |
| **Удалённые без ярлыка** | ничего | история / ID / поиск → IPA → установка |

```text
USB iPhone
   │
   ├─ меню 1: offloaded placeholders
   │     └─ local IPA / device redownload / App Store ID → install
   │
   └─ меню 2: missing (нет иконки)
         └─ iMazing · known-apps · store ID/URL · search → download → install
```

---

## Что умеет текущая beta

- короткое меню `1`–`4` / `A` / `B` — сгруженные, удалённые, IPA, диагностика;
- два класса restore: сгруженные и полностью удалённые;
- в пункте `2` имя без `search` сразу идёт в поиск;
- загрузка по **App Store ID / URL**, даже если bundle ID неизвестен;
- поиск по имени: **iTunes + IPA Filezone + веб** (`apps.apple.com`);
- локальная история подтверждённых приложений и App Store ID;
- алиасы для переименованных приложений (Домклик → ДКлик, Сбер → исторический ID);
- структурная проверка IPA до установки;
- приватная staging-копия IPA и подтверждение результата самим iPhone;
- SQLite-индекс локальной библиотеки: повторный scan не открывает неизменённые IPA;
- `--purchase` только после явного `--acquire-license`;
- JSON для диагностики и автоматизации.

---

## Быстрый старт

### Windows

```powershell
irm https://github.com/J3ckJ/AppRestore/releases/latest/download/install.ps1 | iex
apprestore
```

### macOS

```bash
curl -fsSL https://github.com/J3ckJ/AppRestore/releases/latest/download/install.sh | /bin/bash && export PATH="$HOME/.local/bin:$PATH"
apprestore
```

Bootstrap скачивает versioned source ZIP, сверяет **SHA-256** и ставит AppRestore
в user-scope. Первая строка устанавливает программу, вторая запускает её;
перезапуск терминала обычно не нужен.

Канонический источник установщиков — только
[GitHub Releases](https://github.com/J3ckJ/AppRestore/releases/latest).

### Хотите прочитать установщик до запуска

```powershell
$installer = Join-Path $env:TEMP "apprestore-install.ps1"
Invoke-WebRequest `
  -Uri "https://github.com/J3ckJ/AppRestore/releases/latest/download/install.ps1" `
  -OutFile $installer
Get-FileHash -Algorithm SHA256 -LiteralPath $installer
notepad $installer
& $installer
apprestore
```

### Из исходников

```powershell
Set-ExecutionPolicy -Scope Process Bypass
.\install-windows.ps1
apprestore
```

```bash
./install-macos.sh && export PATH="$HOME/.local/bin:$PATH"
apprestore
```

### Обновление и повторная установка

Повторите ту же bootstrap-команду из «Быстрого старта». Установщик сначала
полностью собирает и проверяет новую managed-версию в staging, затем заменяет
текущую. Если загрузка или установка оборвётся, предыдущая рабочая версия
останется доступной либо будет восстановлена при следующем запуске installer.

---

## Типичный день с меню

1. Подключите разблокированный iPhone по USB, подтвердите «Доверять».
2. Запустите `apprestore`.
3. При необходимости: **A** — Apple ID, **B** — зависимости.

| Пункт | Действие |
|---|---|
| `1` | Сгруженные — список и восстановление |
| `2` | Удалённые — список, ID / URL / имя (поиск) и восстановление |
| `3` | Локальные IPA — сканировать / скачать / установить |
| `4` | Устройства и краткая диагностика |
| `A` / `B` | Вход в Apple ID / настройка зависимостей |

В пункте `2` можно ввести номер из списка, App Store ID/URL, bundle ID или просто имя:

```text
6472732558
id6472732558
https://apps.apple.com/ru/app/homuz/id6472732558
домклик
com.example.app
```

---

## Как работает restore

### Сгруженные (меню `1`)

1. Локальный IPA с точным bundle ID.  
2. Штатный redownload placeholder на iPhone.  
3. Иначе App Store ID с устройства / iMazing / lookup.  
4. `ipatool download` → проверка Info.plist → установка.

### Удалённые без ярлыка (меню `2`)

Placeholder уже нет — iOS нечего «перекачать».  
Источники кандидатов: iMazing `Apps.plist`, локальные IPA, `known-apps.json`.  
Дальше только local IPA или download по store ID / bundle ID.

**Важно про delisted-приложения:**  
витрина может быть пустой, а download по числовому ID — ещё работать, если у
Apple ID осталась лицензия. Это не гарантия и зависит от Apple.

---

## Поиск ID, когда страницы уже нет

```text
имя приложения
   │
   ├─ iTunes Search (живые витрины)
   ├─ IPA Filezone (архивный каталог)
   └─ веб-поиск ссылок apps.apple.com   ← как «нагуглить id вручную»
            │
            └─ выбранное приложение → known-apps.json → следующий раз в списке
```

Команда:

```powershell
apprestore search "СберСпасибо"
```

Поисковый запрос может отправляться в Apple iTunes Search, IPA Filezone и
веб-поиск. Локальные IPA при поиске никуда не загружаются. Простой просмотр
результатов не меняет историю: запись появляется только после вашего выбора
или подтверждённого download/restore.

---

## CLI

| Команда | Назначение |
|---|---|
| `apprestore` | Интерактивное меню |
| `apprestore --version` | Версия |
| `apprestore doctor` / `setup` | Диагностика / USB-мост (Windows) |
| `apprestore devices` | Подключённые iPhone |
| `apprestore scan` | Локальные IPA |
| `apprestore offloaded` | Сгруженные |
| `apprestore missing` | Удалённые / отсутствующие |
| `apprestore search [TERM]` | Поиск ID по имени |
| `apprestore auth [--email]` | Вход `ipatool` |
| `apprestore download BUNDLE_ID [--store-id ID]` | Скачать IPA |
| `apprestore install FILE.ipa` | Проверить и установить |
| `apprestore restore` | Мастер сгруженных |
| `apprestore restore-missing [--store-id ID]` | Мастер удалённых без ярлыка |

```powershell
apprestore --json devices
apprestore restore-missing --store-id 6472732558
apprestore search домклик
apprestore download com.example.app --store-id 123456789
apprestore download com.example.app --acquire-license  # только с явного согласия
apprestore restore --select 1 --skip-device-redownload
```

`--skip-device-redownload` нужен только после неудачного штатного redownload:
сначала убедитесь на iPhone, что прежняя загрузка действительно остановилась.
Флаг сразу переходит к IPA-пути и не запускает второй запрос iOS параллельно.

В JSON-режиме глобальный `--json` ставится перед командой; интерактивный ввод
отключён, поэтому для restore обязательно передавайте `--select`, `--bundle-id`
или `--store-id`. Любая ошибка возвращается одним JSON-документом и ненулевым
кодом процесса.

---

## Требования

- Windows 10/11 x64 или macOS;
- Python 3.10–3.13 (установщик при необходимости подготовит Python 3.12);
- разблокированный iPhone по USB + «Доверять этому компьютеру»;
- на Windows: Apple Mobile Device Support / Apple Devices / iTunes;
- интернет для bootstrap, поиска и `ipatool` (первый вход в Apple ID
  дополнительно качает SAP-рантайм `ipatool`, ~20 МБ, один раз);
- Apple ID, у которого есть право на приложение (или свой законный IPA).

---

## Безопасность — коротко и по делу

AppRestore **не** обходит DRM, подпись и региональные правила App Store.  
Он проверяет структуру IPA и ставит то, на что у вашей учётки есть доступ
(или что вы уже легально сохранили локально). Решение об установке принимает iOS.

Онлайн-bootstrap:

- качает только pinned source ZIP;
- сверяет SHA-256 до установки;
- режет path traversal / symlink / слишком большие архивы.
- ставит Python-зависимости только из hash-locked набора.

Перед установкой IPA:

- только `.ipa`, без symlink;
- ровно один `Payload/*.app/Info.plist`;
- `CFBundleIdentifier` из plist, не из имени файла;
- копирование через no-follow в приватный staging с SHA-256 за один проход;
- backend получает только staging-снимок, а не исходный изменяемый путь;
- успех возвращается только после того, как iPhone подтвердил установленный bundle ID.

Пароль Apple ID / 2FA / keychain passphrase **не** проходят через argv AppRestore —
их спрашивает сам `ipatool` в терминале.

С версии 2.4 `ipatool` подписывает авторизацию App Store через SAP, а подписчик
исполняется в эмуляторе Unicorn. Библиотеку эмулятора качает и сверяет по
SHA-256 сам `ipatool` — AppRestore её не распространяет и не подменяет.

Не публикуйте IPA, UDID, пароли и сырые логи в issue.

Подробнее: [SECURITY.md](./SECURITY.md).

---

## Где лежат данные

| | Windows | macOS |
|---|---|---|
| Программа | `%LOCALAPPDATA%\Programs\AppRestore` | `~/Library/Application Support/AppRestore/venv` |
| IPA | `%USERPROFILE%\AppRestore\ipas` | `~/Library/Application Support/AppRestore/ipas` |
| Кэш + IPA-индекс | `%LOCALAPPDATA%\AppRestore` | `~/Library/Caches/AppRestore` |
| История | `%LOCALAPPDATA%\AppRestore\known-apps.json` | `~/Library/Application Support/AppRestore/known-apps.json` |
| Авторизация | хранилище `ipatool` | хранилище `ipatool` |
| SAP-рантайм `ipatool` | `%LOCALAPPDATA%\ipatool\unicorn` | `~/Library/Caches/ipatool/unicorn` |

Переменные: `APPRESTORE_IPA_DIR`, `APPRESTORE_CACHE_DIR`, `APPRESTORE_DATA_DIR`,
`APPRESTORE_EXTRA_IPA_DIRS`, `APPRESTORE_IMAZING_PLIST`, `APPRESTORE_KNOWN_APPS`.

---

## Удаление

### Windows

```powershell
$localData = [Environment]::GetFolderPath(
  [Environment+SpecialFolder]::LocalApplicationData
)
& (Join-Path $localData "Programs\AppRestore\uninstall-windows.ps1")
```

Полная очистка данных: добавьте `-PurgeUserData`.  
Сначала можно `-WhatIf`.

### macOS

Отдельного uninstaller пока нет. Управляемый runtime находится в
`~/Library/Application Support/AppRestore/venv`; установщик не ставит Homebrew
и не изменяет системный Python.

---

## Если что-то пошло не так

| Симптом | Что сделать |
|---|---|
| `apprestore` не найден | новое окно терминала или полный путь к launcher |
| iPhone не виден | USB data-кабель, Trust, `apprestore doctor` / `setup` |
| `ipatool` снова просит passphrase | нормально для нового процесса на Windows |
| вход в Apple ID долго молчит | первый вход качает SAP-рантайм `ipatool`; дайте ему несколько минут |
| вход падает до запроса пароля | нет доступа к сети для SAP-рантайма; проверьте `apprestore doctor` |
| `TLS handshake timeout` при входе или загрузке | DPI тормозит хендшейк дольше таймаута Go; поднимите прокси/VPN — AppRestore подставит системный прокси сам, если он слушает |
| поиск пустой | delisted → нужен ID/URL; попробуйте `search` и веб-варианты имени |
| download не идёт | нет лицензии / регион / сервер Apple; положите свой IPA в библиотеку |

```powershell
apprestore doctor
apprestore setup
apprestore devices
apprestore scan
```

---

## Разработка

Python 3.10–3.13.

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --require-hashes --only-binary=:all: --no-deps --find-links requirements\wheels -r requirements\build.lock
python -m pip install --require-hashes --only-binary=:all: --no-deps --find-links requirements\wheels -r requirements\runtime.lock
python -m pip install --require-hashes --only-binary=:all: --no-deps -r requirements\test.lock
python -m pip install --editable . --no-deps --no-build-isolation
python -m pytest -q
python scripts\build-release.py
```

`dist/` не коммитится. В релиз уходят:

```text
AppRestore-<version>-source.zip
install.ps1
install.sh
SHA256SUMS.txt
```

Публикация выполняется только tag-workflow после Windows/macOS тестов,
проверки соответствия tag ↔ version и повторяемой сборки. Пошаговый процесс:
[docs/RELEASING.md](./docs/RELEASING.md).

---

## Ссылки

- [Releases](https://github.com/J3ckJ/AppRestore/releases)
- [CHANGELOG](./CHANGELOG.md)
- [SECURITY](./SECURITY.md)
- [CONTRIBUTING](./CONTRIBUTING.md)
- [Issues](https://github.com/J3ckJ/AppRestore/issues)

## Лицензия

Copyright © 2026 J3ckJ · `GPL-3.0-or-later` · см. [LICENSE](./LICENSE) и
[THIRD_PARTY_NOTICES.md](./THIRD_PARTY_NOTICES.md).
