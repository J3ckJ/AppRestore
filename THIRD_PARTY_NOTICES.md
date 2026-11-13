# Сторонние компоненты

AppRestore использует или устанавливает следующие сторонние проекты. Их собственные лицензии и уведомления сохраняют силу.

## pymobiledevice3 10.1.0

- Назначение: обнаружение iOS-устройств и установка IPA через протоколы Apple Mobile Device.
- Проект: <https://github.com/doronz88/pymobiledevice3>
- Лицензия upstream: GNU General Public License 3.0.
- Текст лицензии: <https://github.com/doronz88/pymobiledevice3/blob/v10.1.0/LICENSE>

Пакет и его транзитивные зависимости устанавливаются по hash-locked файлу
`requirements/runtime.lock`.

## hexdump 3.3

- Назначение: транзитивная зависимость `pymobiledevice3`;
- Проект: <https://bitbucket.org/techtonik/hexdump/>;
- Лицензия upstream: Public Domain.

PyPI публикует только source ZIP, поэтому репозиторий содержит его проверенную
копию `requirements/sources/hexdump-3.3.zip` и воспроизводимо собранный
universal wheel `requirements/wheels/hexdump-3.3-py3-none-any.whl`. Их SHA-256
и рецепт зафиксированы в `requirements/README.md` и
`requirements/runtime.lock`.

## ipatool 2.8.1

- Назначение: авторизация и загрузка IPA, доступных учётной записи App Store.
- Проект: <https://github.com/majd/ipatool>
- Лицензия upstream: MIT.
- Текст лицензии: <https://github.com/majd/ipatool/blob/3e84853e2e5d60540a5022b64895d496910144eb/LICENSE>
- Сборка: v2.8.1, коммит `3e84853e2e5d60540a5022b64895d496910144eb`, плюс патчи AppRestore из `packaging/patches/`. Установщики берут архивы этой сборки и принимают их только при SHA-256 ниже.

Windows:

```text
0c5165292d7d17371e9401599c00f4bbc615e75cf9265ffa586266336c48beee
```

macOS:

```text
3506c9b524f4263f12cdbebfd05652390cab9f1ec6490ff31c7b2e8121fba7b8  macos-amd64
cdfb5ee00fe26928783df2fcfc1480b604d4d261b08c0a6e04699da76dea9e92  macos-arm64
```

## Unicorn Engine 2.1.4

- Назначение: `ipatool` 2.4+ подписывает авторизацию App Store через SAP, и
  подписчик исполняется в эмуляторе Unicorn.
- Проект: <https://github.com/unicorn-engine/unicorn>
- Лицензия upstream: GNU General Public License 2.0.

AppRestore не распространяет Unicorn. Библиотеку скачивает сам `ipatool` при
первом входе в Apple ID, сверяет по своим SHA-256 и кладёт в пользовательский
кэш (`%LOCALAPPDATA%\ipatool\unicorn` / `~/Library/Caches/ipatool/unicorn`).

## Apple

Apple, iPhone, iOS, App Store и iTunes — товарные знаки Apple Inc. AppRestore не является продуктом Apple и не аффилирован с Apple Inc.
