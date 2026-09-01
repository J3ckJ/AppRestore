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

## ipatool 2.5.0

- Назначение: авторизация и загрузка IPA, доступных учётной записи App Store.
- Проект: <https://github.com/majd/ipatool>
- Лицензия upstream: MIT.
- Текст лицензии: <https://github.com/majd/ipatool/blob/v2.5.0/LICENSE>

Windows-установщик загружает официальный x64-архив релиза v2.5.0 с GitHub и принимает его только при SHA-256:

```text
d7494be51097e4ab132c5f2453a1ccafa56fffe5379a1ac0366e0997bbda6df8
```

macOS-установщик принимает архивы того же релиза только при SHA-256:

```text
8d6c42230215e8a9dc939b537ae7bb2db75f5b3bec62a52b2c8bb1fe08d8d272  macos-amd64
1b8bbf14e717ef6827a78e6dcb67bd096f3aa8ff9a13b433cd26ac0527640341  macos-arm64
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
