# AppRestore

[![CI](https://github.com/J3ckJ/AppRestore/actions/workflows/ci.yml/badge.svg)](https://github.com/J3ckJ/AppRestore/actions/workflows/ci.yml)
[![Release](https://img.shields.io/github/v/release/J3ckJ/AppRestore)](https://github.com/J3ckJ/AppRestore/releases/latest)
[![License: GPL v3](https://img.shields.io/badge/License-GPLv3-blue.svg)](./LICENSE)

AppRestore помогает вернуть сгруженные (`offloaded`) приложения на iPhone:
находит подходящий локальный IPA, просит устройство повторно загрузить
приложение либо загружает доступный учётной записи пакет через `ipatool`,
проверяет его внутренние метаданные и передаёт на устройство через
`pymobiledevice3`.

Одно Python-ядро работает на Windows и macOS. Интерактивный режим открывается
фирменным ASCII-логотипом и не требует знания всех CLI-команд.

> **Статус:** beta. Версия 0.1.3.
>
> AppRestore не обходит DRM, подпись, региональные ограничения или правила
> App Store. Приложение должно быть доступно вашей учётной записи либо у вас
> должна быть законно полученная локальная копия IPA. Окончательное решение об
> установке принимает iOS. Проект не связан с Apple Inc.

```text
     _                ____           _
    / \   _ __  _ __ |  _ \ ___  ___| |_ ___  _ __ ___
   / _ \ | '_ \| '_ \| |_) / _ \/ __| __/ _ \| '__/ _ \
  / ___ \| |_) | |_) |  _ <  __/\__ \ || (_) | | |  __/
 /_/   \_\ .__/| .__/|_| \_\___||___/\__\___/|_|  \___|
         |_|   |_|
```

## Возможности

- интерактивное меню без обязательных аргументов;
- поиск подключённых по USB iPhone;
- обнаружение сгруженных приложений;
- рекурсивный поиск локальных IPA;
- чтение bundle ID из `Payload/*.app/Info.plist`;
- точное сопоставление bundle ID с учётом регистра;
- попытка штатной повторной загрузки приложения самим iPhone;
- загрузка доступного IPA через `ipatool`;
- установка проверенного локального IPA через `pymobiledevice3`;
- выбор нескольких приложений: `1,3-5` или `all`;
- JSON для диагностических и основных неинтерактивных команд.

## Быстрый старт на Windows

### Требования

- Windows 10 или 11 x64;
- Windows PowerShell 5.1 или PowerShell 7;
- интернет-соединение;
- разблокированный iPhone, подключённый по USB;
- подтверждённый запрос «Доверять этому компьютеру?»;
- Apple Mobile Device Support, Apple Devices либо iTunes для USB-связи.

Если 64-битный Python 3.10+ отсутствует, установщик пытается поставить Python
3.12 для текущего пользователя через `winget`, а затем — через закреплённый
официальный installer с python.org.

### Установка и запуск

Откройте обычный PowerShell:

```powershell
irm https://github.com/J3ckJ/AppRestore/releases/latest/download/install.ps1 | iex
apprestore
```

Первая команда скачивает bootstrap, который загружает versioned source-архив,
сверяет закреплённый SHA-256 и устанавливает AppRestore для текущего
пользователя. Вторая команда сразу открывает меню в **том же PowerShell** —
перезапуск терминала не нужен.

Канонический релиз
[`v0.1.3`](https://github.com/J3ckJ/AppRestore/releases/tag/v0.1.3), архивы и
checksums опубликованы в
[GitHub Releases](https://github.com/J3ckJ/AppRestore/releases). Bootstrap
закрепляет versioned URL и SHA-256 архива, поэтому подмена release asset
обнаруживается до запуска установщика.

Совместимое зеркало E.L System Tools сохраняет прежнюю короткую команду:

```powershell
irm https://el-system-tools.j3ckj.chatgpt.site/install/apprestore.ps1 | iex
apprestore
```

Зеркало должно раздавать тот же versioned bootstrap, что и GitHub Release.
Если его версия или SHA-256 отличается от опубликованных release assets,
используйте канонический GitHub URL.

### Сначала скачать и прочитать установщик

`irm ... | iex` исполняет полученный из сети код. Для проверяемого сценария
скачайте файл отдельно:

```powershell
$installer = Join-Path $env:TEMP "apprestore-install.ps1"
Invoke-WebRequest `
  -Uri "https://github.com/J3ckJ/AppRestore/releases/latest/download/install.ps1" `
  -OutFile $installer
Get-FileHash -Algorithm SHA256 -LiteralPath $installer
notepad $installer
```

После просмотра:

```powershell
& $installer
apprestore
```

### Установка из исходников

```powershell
Set-ExecutionPolicy -Scope Process Bypass
.\install-windows.ps1
apprestore
```

Установщик:

1. работает только с `%LOCALAPPDATA%\Programs\AppRestore`;
2. создаёт отдельное Python-окружение;
3. устанавливает точно `pymobiledevice3==10.1.0`;
4. загружает официальный `ipatool v2.3.1` для Windows x64 и проверяет SHA-256
   архива;
5. добавляет только launcher-каталог в пользовательский и текущий `PATH`;
6. при необходимости пытается настроить Apple USB-мост через `winget`.

Отключить изменение `PATH`:

```powershell
.\install-windows.ps1 -NoPathUpdate
& "$env:LOCALAPPDATA\Programs\AppRestore\apprestore.ps1"
```

Отключить автоматическую настройку Apple USB-моста:

```powershell
.\install-windows.ps1 -SkipAppleBridge
```

Исходный launcher также устанавливает или обновляет пользовательскую копию,
а затем передаёт ей аргументы:

```powershell
.\apprestore.ps1
.\apprestore.ps1 doctor
```

Обычный запуск открывает меню и сам по себе не инициирует установку драйвера
или запрос UAC. Настройка запускается явно через пункт меню либо
`apprestore setup`.

## Быстрый старт на macOS

Требуются Homebrew, интернет-соединение и доверенный iPhone по USB.

```bash
chmod +x apprestore.sh
./apprestore.sh setup
./apprestore.sh
```

`setup` устанавливает необходимые Homebrew-компоненты, создаёт отдельное
окружение в `~/Library/Application Support/AppRestore/venv`, устанавливает
Python-зависимости и запускает диагностику.

Если Homebrew отсутствует, сначала установите его по официальной инструкции
на [brew.sh](https://brew.sh).

## Как проходит восстановление

Для каждого выбранного приложения AppRestore:

1. ищет локальный IPA с точно совпадающим bundle ID;
2. если его нет, просит iPhone выполнить штатную повторную загрузку;
3. если приложение остаётся сгруженным, ищет App Store ID в метаданных
   устройства, каталоге iMazing и публичном lookup App Store;
4. при необходимости запускает интерактивный вход через `ipatool`;
5. загружает IPA по App Store ID или bundle ID;
6. проверяет внутренний `Info.plist` и точное соответствие bundle ID;
7. повторно проверяет файл непосредственно перед установкой;
8. передаёт IPA на выбранное устройство.

Удалённое из App Store приложение иногда удаётся получить по числовому
App Store ID, если оно осталось в истории покупок. Это не гарантируется и
зависит от региона, учётной записи и серверов Apple.

Сгруженное приложение нельзя извлечь с iPhone как полноценный IPA: его
исполняемый бинарник уже удалён. Практические источники — собственная резервная
копия, библиотека iMazing или повторная загрузка через App Store/`ipatool`.

## Модель безопасности

AppRestore выполняет структурные проверки, но не является антивирусом и не
подтверждает происхождение стороннего IPA.

Перед использованием программа:

- принимает только файл с расширением `.ipa`;
- не следует символическим ссылкам;
- требует ровно один верхнеуровневый
  `Payload/<имя>.app/Info.plist`;
- отклоняет зашифрованные и неоднозначные ZIP-структуры;
- читает `CFBundleIdentifier` из внутреннего plist, а не из имени файла;
- требует точное совпадение ожидаемого bundle ID с учётом регистра;
- вычисляет SHA-256;
- проверяет размер, время изменения и идентичность файла;
- повторно читает метаданные перед установкой.

При ручной установке рекомендуется явно указывать ожидаемый bundle ID:

```powershell
apprestore install ".\MyApp.ipa" `
  --expect-bundle-id "com.example.MyApp"
```

Скачивание выполняется во временном изолированном каталоге. Пакет переносится
в библиотеку только после успешной проверки. AppRestore не подставляет
«самый новый IPA» при ошибке загрузки.

Внешние команды запускаются массивом аргументов без shell-интерполяции.

Пароль Apple ID, 2FA-код и passphrase локального хранилища не передаются через
аргументы AppRestore или переменные окружения. Их запрашивает непосредственно
`ipatool` в интерактивном терминале.

## Командная строка

На Windows используйте `apprestore`. При запуске из macOS checkout заменяйте
его на `./apprestore.sh`.

| Команда | Назначение |
|---|---|
| `apprestore` | Открыть интерактивное меню |
| `apprestore --version` | Показать версию |
| `apprestore doctor` | Проверить зависимости и USB-мост |
| `apprestore setup` | На Windows настроить Apple USB-мост и повторить диагностику |
| `apprestore devices` | Показать подключённые USB-устройства |
| `apprestore scan` | Найти и структурно проверить локальные IPA |
| `apprestore offloaded [--udid UDID]` | Показать сгруженные приложения |
| `apprestore auth [--email EMAIL]` | Выполнить интерактивный вход через `ipatool` |
| `apprestore auth --revoke` | Отозвать авторизацию `ipatool` |
| `apprestore download BUNDLE_ID [--store-id ID]` | Скачать и проверить IPA |
| `apprestore install FILE.ipa [--expect-bundle-id ID]` | Проверить и установить IPA |
| `apprestore restore [--select 1,3-5]` | Запустить мастер восстановления |

Глобальные параметры ставятся перед подкомандой:

```text
--ipa-dir PATH
--cache-dir PATH
--json
```

Примеры:

```powershell
apprestore --json devices
apprestore --json scan
apprestore --ipa-dir "D:\IPA Library" scan
apprestore restore --udid "DEVICE-UDID" --select "1,3-5"
apprestore download "com.example.MyApp" --store-id "123456789"
```

JSON реализован для `doctor`, `setup`, `devices`, `scan`, `offloaded`,
`download` и `install`. Интерактивное меню не используется вместе с `--json`;
`auth` и `restore` остаются интерактивными или человекочитаемыми.

## Данные и приватность

| Данные | Windows | macOS |
|---|---|---|
| Программа / окружение | `%LOCALAPPDATA%\Programs\AppRestore` | `~/Library/Application Support/AppRestore/venv` |
| Библиотека IPA | `%USERPROFILE%\AppRestore\ipas` | `~/Library/Application Support/AppRestore/ipas` |
| Кэш | `%LOCALAPPDATA%\AppRestore` | `~/Library/Caches/AppRestore` |
| Авторизация | Хранилище `ipatool` | Хранилище `ipatool` |

Дополнительно сканируются библиотеки iMazing/DigiDNA, `Downloads`, iTunes
Mobile Applications и каталоги из `APPRESTORE_EXTRA_IPA_DIRS`.

Пути можно настроить:

| Переменная | Назначение |
|---|---|
| `APPRESTORE_IPA_DIR` | Основная библиотека IPA |
| `APPRESTORE_CACHE_DIR` | Каталог кэша |
| `APPRESTORE_EXTRA_IPA_DIRS` | Дополнительные каталоги поиска |
| `APPRESTORE_IMAZING_PLIST` | Явный путь к `Apps.plist` iMazing |

Ограничительные права на создаваемые файлы применяются в режиме best effort и
не заменяют корректные ACL, шифрование диска и резервное копирование.

IPA из App Store может содержать идентификатор Apple ID в
`iTunesMetadata.plist`. Не публикуйте IPA и не прикладывайте их к issue.
JSON-вывод может содержать UDID, локальные пути и bundle ID — очищайте
диагностику от персональных данных.

Собственной телеметрии у AppRestore нет. Во время работы возможны обращения к
Apple/App Store и `ipatool`, а во время установки — к GitHub Releases,
совместимому зеркалу проекта, PyPI, `winget`, Homebrew и python.org.

## Удаление

### Windows

Предварительный просмотр:

```powershell
& "$env:LOCALAPPDATA\Programs\AppRestore\uninstall-windows.ps1" -WhatIf
```

Обычное удаление сохраняет IPA, кэш и авторизацию `ipatool`:

```powershell
& "$env:LOCALAPPDATA\Programs\AppRestore\uninstall-windows.ps1"
```

Полная очистка дополнительно отзывает авторизацию и предлагает удалить
стандартные каталоги данных:

```powershell
& "$env:LOCALAPPDATA\Programs\AppRestore\uninstall-windows.ps1" -PurgeUserData
```

Каталоги, заданные через `APPRESTORE_IPA_DIR` и `APPRESTORE_CACHE_DIR`,
автоматически не удаляются.

### macOS

Автоматического uninstaller пока нет. Окружение, библиотека IPA и кэш
расположены отдельно; перед ручным удалением сохраните нужные IPA.
Homebrew-пакеты могут использоваться другими программами и автоматически не
удаляются.

## Устранение неполадок

### `apprestore` не найден

При `-NoPathUpdate` используйте:

```powershell
& "$env:LOCALAPPDATA\Programs\AppRestore\apprestore.ps1"
```

Обычный bootstrap добавляет команду в текущий PowerShell. Другим уже открытым
терминалам может понадобиться новое окно.

### iPhone не обнаружен

1. Разблокируйте устройство.
2. Подтвердите доверие компьютеру.
3. Переподключите USB-кабель с поддержкой передачи данных.
4. Выполните:

```powershell
apprestore doctor
apprestore setup
```

Если настройка USB-моста не удалась, установите Apple Devices или iTunes
вручную и переподключите устройство.

### Подключено несколько iPhone

```powershell
apprestore devices
apprestore restore --udid "DEVICE-UDID"
```

### `ipatool` снова спрашивает passphrase

Официальный `ipatool v2.3.1` на Windows может повторно спрашивать passphrase в
новом процессе. AppRestore намеренно не кэширует и не проксирует этот секрет.

### IPA не скачивается

Возможные причины: приложение удалено из App Store, недоступно в регионе, не
принадлежит учётной записи, App Store ID не определяется или сервер Apple
отклонил запрос. Если есть собственный IPA:

```powershell
apprestore scan
apprestore restore
```

### Bundle ID не совпадает

Переименование файла не меняет bundle ID внутри IPA. Посмотрите фактические
метаданные через `apprestore scan`.

## Разработка

Требуется Python 3.10+.

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -e .
python -m pip install pytest
python -m unittest discover -s tests -v
python -m pytest -q
python -m compileall -q apprestore.py apprestore_core scripts tests
```

`unittest` остаётся быстрым локальным набором, а полный `pytest` дополнительно
выполняет release/bootstrap E2E в доступных PowerShell-хостах.

Воспроизводимый release payload:

```powershell
python scripts\build-release.py
```

Builder создаёт в `dist`:

```text
AppRestore-<version>-source.zip
install.ps1
SHA256SUMS.txt
```

`dist` не коммитится. Артефакты прикладываются к GitHub Release. Версия, URL
архива, закреплённый SHA-256, `CHANGELOG.md` и Git tag должны совпадать.

## Репозиторий и релизы

- [Репозиторий](https://github.com/J3ckJ/AppRestore)
- [Releases](https://github.com/J3ckJ/AppRestore/releases)
- [Релиз v0.1.3](https://github.com/J3ckJ/AppRestore/releases/tag/v0.1.3)
- [История изменений](./CHANGELOG.md)
- [Политика безопасности](./SECURITY.md)
- [Как внести вклад](./CONTRIBUTING.md)
- [Сторонние компоненты](./THIRD_PARTY_NOTICES.md)
- [Сообщить о проблеме](https://github.com/J3ckJ/AppRestore/issues)

Первый GitHub-native релиз `0.1.3` опубликован вместе с one-line installer и
SHA-256 checksums. Опубликованные version tags и release assets нельзя
перезаписывать другими байтами.

При сообщении об ошибке не прикладывайте IPA, пароль Apple ID, 2FA-код,
passphrase, токены или необработанный лог с UDID.

## Лицензия

Copyright © 2026 J3ckJ. Код распространяется под `GPL-3.0-or-later`. См.
[LICENSE](./LICENSE) и [THIRD_PARTY_NOTICES.md](./THIRD_PARTY_NOTICES.md).
