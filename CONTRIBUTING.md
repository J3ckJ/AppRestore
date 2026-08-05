# Как внести вклад в AppRestore

Спасибо за интерес к проекту. Изменения должны сохранять главный контракт:
пользователь устанавливает AppRestore одной командой, а следующей командой
`apprestore` открывает рабочее меню в том же PowerShell.

## Перед началом

- Для ошибок используйте GitHub issue form.
- Уязвимости отправляйте по инструкции в [SECURITY.md](./SECURITY.md).
- Не прикладывайте IPA, Apple ID credentials, 2FA-коды, passphrase, токены,
  приватные ключи и необработанные логи с UDID.
- Для крупного изменения сначала откройте issue и согласуйте поведение.

## Локальная среда

Требуется Python 3.10–3.13. Python 3.14 пока не поддерживается.

### Windows

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --require-hashes --only-binary=:all: --no-deps --find-links requirements\wheels -r requirements\build.lock
python -m pip install --require-hashes --only-binary=:all: --no-deps --find-links requirements\wheels -r requirements\runtime.lock
python -m pip install --require-hashes --only-binary=:all: --no-deps -r requirements\test.lock
python -m pip install --editable . --no-deps --no-build-isolation
```

### macOS

```bash
python3 -m venv .venv
. .venv/bin/activate
python -m pip install --require-hashes --only-binary=:all: --no-deps \
  --find-links requirements/wheels -r requirements/build.lock
python -m pip install --require-hashes --only-binary=:all: --no-deps \
  --find-links requirements/wheels -r requirements/runtime.lock
python -m pip install --require-hashes --only-binary=:all: --no-deps \
  -r requirements/test.lock
python -m pip install --editable . --no-deps --no-build-isolation
```

Для реальной работы с устройством также нужны `ipatool` и системный
Apple USB-мост. Большинство unit-тестов не требует подключённого iPhone.

## Обязательные проверки

```powershell
python -m pytest -q
python -m compileall -q apprestore.py apprestore_core scripts tests
```

На Windows тесты должны проходить и в Windows PowerShell 5.1, и в PowerShell
7, если оба хоста доступны.

Для изменений installer/bootstrap дополнительно выполните:

```powershell
python scripts\build-release.py
```

Если меняется vendored `hexdump` wheel, используйте отдельное окружение с
CPython 3.12.13 и точный toolchain:

```powershell
python -m pip install --require-hashes --only-binary=:all: --no-deps -r requirements\wheel-build.lock
python scripts\rebuild-vendored-wheel.py --check
```

`--write` допустим только для осознанного обновления wheel вместе с его hash в
`requirements\runtime.lock`; подробности — в `requirements\README.md`.

Проверьте, что:

- версия одинакова в package, installer, README, changelog, builder и тестах;
- `dist/install.ps1` содержит versioned GitHub Release URL;
- SHA-256 архива закреплён внутри bootstrap;
- `dist/SHA256SUMS.txt` совпадает с созданными файлами;
- ZIP не содержит IPA, `.env`, кэши, виртуальные окружения или крупные
  локальные файлы.

`dist/` не коммитится. Version bump, tag и публикацию release assets выполняет
сопровождающий проекта.

## Требования к изменениям

- Не запускайте внешние команды через shell-интерполяцию.
- Не передавайте password, 2FA или passphrase через argv/env.
- Не ослабляйте точную проверку bundle ID.
- Не выбирайте IPA по времени изменения или принципу «последний файл».
- Новое поведение должно сопровождаться regression-тестом.
- Сообщения пользователю должны объяснять безопасное следующее действие.
- Не меняйте опубликованный version tag или bytes существующего release asset.

Старайтесь делать pull request небольшим и посвящённым одной задаче. В
описании укажите мотивацию, платформы, выполненные проверки и пользовательские
изменения.

## Лицензия вкладов

Отправляя изменение, вы соглашаетесь распространять свой вклад на условиях
`GPL-3.0-or-later`, как и остальную часть AppRestore.
