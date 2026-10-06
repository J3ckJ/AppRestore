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

**AppRestore** возвращает на iPhone приложения, которые система сгрузила или
удалила, когда в App Store уже нет удобной кнопки «Загрузить». Работает локально
на вашем компьютере: USB, ваш Apple ID, без обхода DRM.

> **Статус:** beta · Версия 0.2.4. Windows и macOS · проект не связан с Apple Inc.

## Возможности

- короткое меню: сгруженные, удалённые без ярлыка, локальные IPA, диагностика;
- загрузка по bundle ID, App Store ID или ссылке `apps.apple.com`;
- поиск по имени (iTunes, IPA Filezone, веб), если ID неизвестен;
- проверка IPA перед установкой и подтверждение результата на телефоне;
- локальная история найденных App Store ID;
- `apprestore doctor` для проверки зависимостей и сети.

## Как это работает

```text
iPhone по USB
   │
   ├─ сгружено (ярлык-placeholder)
   │     → свой IPA / штатный redownload iOS / загрузка через ipatool
   │
   └─ удалено полностью (нет иконки)
         → история / store ID / поиск → скачать IPA → установить
```

Установщик ставит AppRestore и проверенный **ipatool 2.6.0**. Пароль Apple ID
спрашивает сам `ipatool` в консоли; AppRestore его не хранит и не передаёт в
аргументах командной строки.

## Требования

- **Windows 10/11 x64** или **macOS** (Apple Silicon или Intel);
- iPhone по **USB**, с разблокированным экраном и доверием к компьютеру;
- для варианта «Для терминала»: Python 3.10-3.13 (bootstrap ставит свой, если нужно);
  программе с окном Python не нужен;
- интернет для установки, поиска и входа в Apple ID;
- Apple ID, у которого есть право на нужное приложение (или уже свой законный IPA).

## Установка

Канонический источник: [GitHub Releases](https://github.com/J3ckJ/AppRestore/releases/latest).
Есть два варианта, выберите один:

| Вариант | Кому подходит | Что скачать |
|---|---|---|
| **Программа с окном** | обычная работа мышкой | `AppRestore-GUI-Windows.zip` или `AppRestore-GUI-macOS.zip` |
| **Для терминала** | меню в консоли, скрипты | `install.ps1` или `install.sh` (одна команда) |

Суммы SHA-256 всех файлов релиза лежат в `SHA256SUMS.txt`.

### Программа с окном

**Windows 10/11 x64**

1. Скачайте `AppRestore-GUI-Windows.zip` со страницы релиза.
2. Распакуйте в свою папку, например `C:\Users\<вы>\AppRestore`
   (не в `Program Files`, иначе обновление в один клик не сможет заменить файлы).
3. Запустите `AppRestore\AppRestore.exe`. Если Windows SmartScreen предупредит
   о неизвестном издателе: **Подробнее** → **Выполнить в любом случае**.

**macOS (Apple Silicon)**

1. Скачайте `AppRestore-GUI-macOS.zip`, откройте его: появится `AppRestore.app`.
2. Перенесите `AppRestore.app` в «Программы».
3. Первый запуск: правый клик по программе → **Открыть** → **Открыть**
   (сборка не нотарифицирована Apple). На Mac с Intel используйте вариант
   «Для терминала».

Внутри уже есть всё нужное: pymobiledevice3 и проверенный ipatool 2.6.0, Python
ставить не надо. Проверить сумму вручную:

```powershell
Get-FileHash -Algorithm SHA256 .\AppRestore-GUI-Windows.zip
```

```bash
shasum -a 256 AppRestore-GUI-macOS.zip
```

**Обновление:** «Настройки» → **Проверить обновления**. Программа покажет, что
нового, и спросит разрешения. После нажатия **Обновить** она скачает новую
сборку, сверит SHA-256 по `SHA256SUMS.txt`, закроется, заменит себя и
запустится снова. Если новая версия не запустится, вернётся прежняя.

### Для терминала

Bootstrap скачивает versioned source ZIP, сверяет **SHA-256** и ставит AppRestore
в user-scope.

#### Windows (одна строка)

```powershell
irm https://github.com/J3ckJ/AppRestore/releases/latest/download/install.ps1 | iex
apprestore
```

#### macOS (одна строка)

```bash
curl -fsSL https://github.com/J3ckJ/AppRestore/releases/latest/download/install.sh | /bin/bash && export PATH="$HOME/.local/bin:$PATH"
apprestore
```

#### Сначала посмотреть установщик и хеш

**Windows:**

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

Сверьте хеш с `SHA256SUMS.txt` того же релиза.

**macOS:** скачайте `install.sh` и `SHA256SUMS.txt` со страницы релиза, сверьте
`shasum -a 256 install.sh`, прочитайте скрипт, затем запустите его.

#### Из исходников релиза

```powershell
.\install-windows.ps1
apprestore
```

```bash
./install-macos.sh && export PATH="$HOME/.local/bin:$PATH"
apprestore
```

Обновление: повторите ту же bootstrap-команду. Установщик собирает новую версию
в staging и только потом подменяет текущую.

## Быстрый старт

**Программа с окном:** подключите iPhone по USB, нажмите **Доверять** на телефоне
и откройте AppRestore. На «Обзоре» видно телефон и сгруженные приложения; если
iPhone не виден, откройте «Проверки».

**Для терминала:**

1. Подключите iPhone по USB, разблокируйте, нажмите **Доверять**.
2. Запустите `apprestore` без аргументов: откроется меню.
3. При необходимости: **A** - вход в Apple ID, **B** - зависимости (`doctor`/`setup`).
4. **1** - сгруженные (есть ярлык-placeholder).
5. **2** - удалённые без ярлыка (поиск по имени, store ID или URL).

Полезные команды (из `--help`):

```text
apprestore --version
apprestore doctor
apprestore devices
apprestore offloaded
apprestore missing
apprestore auth --email you@example.com
apprestore search "название"
apprestore download com.example.app
apprestore download --store-id 1234567890
apprestore install path\to\app.ipa
apprestore restore
apprestore restore-missing
apprestore restore-missing --store-id 1234567890
```

На Windows Apple USB bridge ставится через `apprestore setup`, если `doctor`
его не видит.

## Частые вопросы

**Телефон не виден.** Проверьте кабель, разблокировку и «Доверять этому
компьютеру». Затем `apprestore devices` и `apprestore doctor`.

**Спрашивает код 2FA.** Это нормально: код вводит `ipatool` в том же окне
терминала. AppRestore пароль и 2FA не логирует.

**Приложения больше нет в App Store.** Если лицензия на Apple ID жива, часто
помогает загрузка по **store ID** или URL страницы. Иначе нужен свой ранее
сохранённый IPA.

**`ipatool` снова просит passphrase.** На Windows новый процесс `ipatool`
иногда снова спрашивает passphrase keychain. Это поведение upstream, не баг
меню AppRestore.

**Первый вход в Apple ID долго молчит.** `ipatool` 2.4+ при первом входе
скачивает SAP/Unicorn-рантайм. Подождите несколько минут; прогресс смотрите в
`apprestore doctor` (проверки SAP runtime / SAP assets).

**Ошибка сети / TLS к Apple.** AppRestore может подставить системный HTTPS-прокси
(Windows или macOS), только если он реально слушает. Явные `HTTP_PROXY` /
`HTTPS_PROXY` всегда важнее.

## Безопасность и приватность

- пароль Apple ID, 2FA и keychain passphrase **не** передаются через argv/env
  AppRestore; их читает интерактивный `ipatool`;
- в репозиторий и релизы не входят ваши IPA, бэкапы и данные устройства;
- локально пишутся: установленные бинари, кэш `ipatool` (SAP), каталог IPA
  библиотеки и небольшой файл известных App Store ID;
- `--acquire-license` нужен явно, если разрешаете `ipatool --purchase`;
- в issue не прикладывайте IPA, UDID, email, пароли и сырые логи.

Подробнее: [SECURITY.md](./SECURITY.md).

## Ограничения

- только официальный путь через ваш Apple ID и законные IPA;
- DRM не обходится, пиратские IPA не цель проекта;
- Linux как целевая платформа установки не поддерживается;
- успех для delisted-приложений зависит от лицензии и ответов Apple;
- beta: проверяйте актуальный релиз и changelog.

## Лицензия и сторонние компоненты

AppRestore: [GNU GPL v3](./LICENSE).

Ключевые зависимости установщика:

- **ipatool 2.6.0** (MIT) - вход в Apple ID и загрузка IPA;
- **pymobiledevice3** - USB к iPhone;
- **Unicorn 2.1.4** - SAP-рантайм, который `ipatool` качает сам при первом входе.

Полный список и SHA-256 архивов: [THIRD_PARTY_NOTICES.md](./THIRD_PARTY_NOTICES.md).

## Участие и выпуск

- вклад: [CONTRIBUTING.md](./CONTRIBUTING.md);
- как выпускать релиз: [docs/RELEASING.md](./docs/RELEASING.md);
- история изменений: [CHANGELOG.md](./CHANGELOG.md).

В релизе всегда четыре ассета: `AppRestore-<version>-source.zip`, `install.ps1`,
`install.sh`, `SHA256SUMS.txt`.

---

## English summary

**AppRestore** restores offloaded or deleted iOS apps to an iPhone over USB from
Windows or macOS, using your own Apple ID and the verified **ipatool 2.6.0**
download path. It does not bypass DRM.

Install from
[GitHub Releases](https://github.com/J3ckJ/AppRestore/releases/latest):

```powershell
irm https://github.com/J3ckJ/AppRestore/releases/latest/download/install.ps1 | iex
```

```bash
curl -fsSL https://github.com/J3ckJ/AppRestore/releases/latest/download/install.sh | /bin/bash
```

Then run `apprestore`, trust the computer on the phone, use menu item **A** to
sign in when needed, **1** for offloaded placeholders, **2** for fully removed
apps. Passwords stay inside interactive `ipatool`; AppRestore does not put them
on argv. See `apprestore --help` and [SECURITY.md](./SECURITY.md).
