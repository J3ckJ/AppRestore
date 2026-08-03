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

> **Статус:** beta. Версия 0.1.5.  
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
| **Сгруженные** | placeholder-ярлык | local IPA → redownload → `ipatool` → установка |
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

## Что умеет 0.1.5

- интерактивное меню с ASCII-логотипом — можно не помнить CLI;
- два класса restore: сгруженные и полностью удалённые;
- загрузка по **App Store ID / URL**, даже если bundle ID неизвестен;
- поиск по имени: **iTunes + IPA Filezone + веб** (`apps.apple.com`);
- память найденных ID в `%LOCALAPPDATA%\AppRestore\known-apps.json`;
- алиасы для переименованных приложений (Домклик → ДКлик, Сбер → исторический ID);
- структурная проверка IPA до установки;
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
curl -fsSL https://github.com/J3ckJ/AppRestore/releases/latest/download/install.sh | /bin/bash
export PATH="$HOME/.local/bin:$PATH"
apprestore
```

Bootstrap скачивает versioned source ZIP, сверяет **SHA-256** и ставит AppRestore
в user-scope. Перезапуск терминала обычно не нужен.

Канонический релиз:
[`v0.1.5`](https://github.com/J3ckJ/AppRestore/releases/tag/v0.1.5) ·
[все релизы](https://github.com/J3ckJ/AppRestore/releases)

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
./install-macos.sh
export PATH="$HOME/.local/bin:$PATH"
apprestore
```

---

## Типичный день с меню

1. Подключите разблокированный iPhone по USB, подтвердите «Доверять».
2. Запустите `apprestore`.
3. При необходимости: **A** — вход в Apple ID через `ipatool`.
4. **1** — вернуть сгруженные.  
   **2** — вернуть удалённые без ярлыка: номер из списка, `id…`, URL или `search Homuz`.  
   **S** — найти App Store ID по имени (iTunes / архив / веб).

Примеры ввода в пункте `2`:

```text
6472732558
id6472732558
https://apps.apple.com/ru/app/homuz/id6472732558
search домклик
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
            └─ known-apps.json → следующий раз уже в списке
```

Команда:

```powershell
apprestore search "СберСпасибо"
```

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
```

---

## Требования

- Windows 10/11 x64 или macOS;
- разблокированный iPhone по USB + «Доверять этому компьютеру»;
- на Windows: Apple Mobile Device Support / Apple Devices / iTunes;
- интернет для bootstrap, поиска и `ipatool`;
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

Перед установкой IPA:

- только `.ipa`, без symlink;
- ровно один `Payload/*.app/Info.plist`;
- `CFBundleIdentifier` из plist, не из имени файла;
- SHA-256 + повторная проверка перед install.

Пароль Apple ID / 2FA / keychain passphrase **не** проходят через argv AppRestore —
их спрашивает сам `ipatool` в терминале.

Не публикуйте IPA, UDID, пароли и сырые логи в issue.

Подробнее: [SECURITY.md](./SECURITY.md).

---

## Где лежат данные

| | Windows | macOS |
|---|---|---|
| Программа | `%LOCALAPPDATA%\Programs\AppRestore` | `~/Library/Application Support/AppRestore/venv` |
| IPA | `%USERPROFILE%\AppRestore\ipas` | `~/Library/Application Support/AppRestore/ipas` |
| Кэш / known-apps | `%LOCALAPPDATA%\AppRestore` | `~/Library/Caches` / Application Support |
| Авторизация | хранилище `ipatool` | хранилище `ipatool` |

Переменные: `APPRESTORE_IPA_DIR`, `APPRESTORE_CACHE_DIR`,
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

Отдельного uninstaller пока нет — удалите venv/IPA/кэш вручную, если нужно.
Homebrew-пакеты других программ не трогаем.

---

## Если что-то пошло не так

| Симптом | Что сделать |
|---|---|
| `apprestore` не найден | новое окно терминала или полный путь к launcher |
| iPhone не виден | USB data-кабель, Trust, `apprestore doctor` / `setup` |
| `ipatool` снова просит passphrase | нормально для нового процесса на Windows |
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

Python 3.10+.

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -e .
python -m pip install pytest
python -m unittest discover -s tests -v
python scripts\build-release.py
```

`dist/` не коммитится. В релиз уходят:

```text
AppRestore-0.1.5-source.zip
install.ps1
install.sh
SHA256SUMS.txt
```

---

## Ссылки

- [Releases](https://github.com/J3ckJ/AppRestore/releases)
- [v0.1.5](https://github.com/J3ckJ/AppRestore/releases/tag/v0.1.5)
- [CHANGELOG](./CHANGELOG.md)
- [SECURITY](./SECURITY.md)
- [CONTRIBUTING](./CONTRIBUTING.md)
- [Issues](https://github.com/J3ckJ/AppRestore/issues)

## Лицензия

Copyright © 2026 J3ckJ · `GPL-3.0-or-later` · см. [LICENSE](./LICENSE) и
[THIRD_PARTY_NOTICES.md](./THIRD_PARTY_NOTICES.md).
