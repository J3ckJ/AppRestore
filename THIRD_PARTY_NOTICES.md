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

## ipatool 2.3.1

- Назначение: авторизация и загрузка IPA, доступных учётной записи App Store.
- Проект: <https://github.com/majd/ipatool>
- Лицензия upstream: MIT.
- Текст лицензии: <https://github.com/majd/ipatool/blob/v2.3.1/LICENSE>

Windows-установщик загружает официальный x64-архив релиза v2.3.1 с GitHub и принимает его только при SHA-256:

```text
8e986ed9320f205bcd1fd24640ec46a5b92ff346425aff28d1103e57d2fdcadb
```

## Apple

Apple, iPhone, iOS, App Store и iTunes — товарные знаки Apple Inc. AppRestore не является продуктом Apple и не аффилирован с Apple Inc.
