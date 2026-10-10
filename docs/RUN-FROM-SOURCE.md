# Запуск AppRestore (окно 4b) из исходников

Для проверки ветки до выпуска сборки. Окно 4b — интерфейс по умолчанию
(`--ui quick`); старое окно Qt Quick — `--ui quick-legacy`, окно на виджетах —
`--ui widgets`.

## Что нужно

| | Windows 10/11 x64 | macOS (Apple Silicon или Intel) |
|---|---|---|
| Python | 3.10–3.13, 64 бит, с python.org (галочка «Add to PATH») | 3.10–3.13 (python.org или Homebrew) |
| Git | Git for Windows | `xcode-select --install` |
| Связь с iPhone | приложение «Устройства Apple» (Apple Devices) из Microsoft Store **или** iTunes с сайта Apple: ставят драйвер и службу Apple Mobile Device | ничего, usbmuxd встроен в систему |
| ipatool | собранный с патчами AppRestore (см. ниже) | то же |
| Go (только чтобы собрать ipatool) | 1.25 | 1.25 |

iPhone подключается кабелем; при первом подключении на телефоне нажмите
«Доверять» и введите код.

## 1. Исходники и окружение

```bash
git clone https://github.com/J3ckJ/AppRestore.git
cd AppRestore
git switch <ветка интеграции>
```

macOS:

```bash
python3 -m venv .venv
. .venv/bin/activate
python -m pip install --require-hashes --only-binary=:all: -r requirements/build.lock
python -m pip install --require-hashes --only-binary=:all: -r requirements/runtime.lock
python -m pip install --no-deps --no-build-isolation -e .
python -m pip install -r requirements/gui-build.txt   # PySide6 6.9.3 (+ pyinstaller, pytest-qt)
```

Windows (PowerShell):

```powershell
py -3.13 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --require-hashes --only-binary=:all: -r requirements\build.lock
python -m pip install --require-hashes --only-binary=:all: -r requirements\runtime.lock
python -m pip install --no-deps --no-build-isolation -e .
python -m pip install -r requirements\gui-build.txt   # PySide6 6.9.3 и pywinpty для входа в Apple ID
```

## 2. ipatool с патчами

Окну нужен ipatool 2.6.0 (коммит `cde7d003…`, следует редиректам Apple) с
патчами из `packaging/patches/`, каждый закреплён SHA-256 в
`packaging/build-ipatool.sh`:

- `0001` — `auth info` отдаёт страну аккаунта (без неё группы
  «Нет в App Store вашей страны» скрыты);
- `0002` — `list-purchases --all` (вся история покупок одним вызовом;
  без него — постранично, медленнее);
- `0003` — пароль связки ключей через `--keychain-passphrase-stdin` (без
  патча программа не передаёт пароль ни в аргументах, ни в переменных
  окружения и попросит ввести его вручную).

Сборка (macOS, Linux или Git Bash/WSL на Windows; нужны Go 1.25 и git):

```bash
bash packaging/build-ipatool.sh macos-arm64     # или macos-amd64 / windows-amd64
```

Распакуйте получившийся архив и положите `ipatool` (`ipatool.exe`) в папку
`bin/` в корне репозитория — программа ищет его там первым, затем рядом с
Python и в PATH.

`packaging/fetch_ipatool.py` скачивает опубликованную сборку
`ipatool-2.6.0-redirect`; проверьте, что её хэши совпадают с текущими
патчами — архив, собранный до `0001–0003`, для 4b не подходит.

## 3. Запуск

```bash
python -m apprestore_gui.app            # окно 4b
QT_QPA_PLATFORM=offscreen python scripts/ui4b_screenshots.py   # экраны 4b на фейковых данных, PNG в screenshots/
python -m apprestore_gui.app --self-test --output selftest.json   # проверка окружения
```

`--self-test` проверяет pymobiledevice3, usbmux, ipatool, Qt и загрузку
окна 4b (`ui4b qml (default UI)`); в отчёте не должно быть `false`, кроме
`usbmux client` при отключённом телефоне.

Тесты:

```bash
python -m pip install --require-hashes -r requirements/test.lock
QT_QPA_PLATFORM=offscreen python -m pytest tests --ignore=tests/test_gui_update_ui.py --ignore=tests/test_gui_updater.py
```

## Частые ошибки

| Что видно | Причина и что делать |
|---|---|
| `No module named PySide6` | не установлен `requirements/gui-build.txt` или запущен не тот Python (активируйте `.venv`) |
| `ipatool not found next to the app or on PATH` | нет `bin/ipatool(.exe)` — шаг 2 |
| «нужен ipatool с патчем --keychain-passphrase-stdin» | ipatool без `0003`; соберите заново по шагу 2 |
| Телефон не виден (Windows) | нет службы Apple Mobile Device: поставьте «Устройства Apple» или iTunes, переподключите кабель, нажмите «Доверять» |
| Телефон не виден (macOS) | разблокируйте iPhone и нажмите «Доверять»; кабель с передачей данных |
| `metadata X != runtime Y` в self-test | установлены старые метаданные пакета: повторите `pip install --no-deps --no-build-isolation -e .` |
| Окно не открывается в Windows по RDP / в виртуалке | Qt Quick нужен OpenGL/Direct3D: `set QT_QUICK_BACKEND=software` перед запуском |
| Pip ругается на хэши | ставьте только по `*.lock` с `--require-hashes --only-binary=:all:`, Python 3.10–3.13 |

Пароль Apple ID программа передаёт только ipatool; журнал лицензий и кэш
покупок лежат в папке данных пользователя и в репозиторий не попадают.
