# Запуск AppRestore (окно 4b) из исходников

Для проверки ветки до выпуска сборки. Окно 4b — `--ui quick` (в собранной
программе это интерфейс по умолчанию; из исходников флаг нужен явно, иначе
откроется окно на виджетах). Старое окно Qt Quick — `--ui quick-legacy`.

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
git switch cursor/redesign-704e
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

Окну нужен ipatool 2.6.0 (коммит `cde7d003…`) с патчами AppRestore из
`packaging/patches/` (каждый закреплён SHA-256 в `packaging/build-ipatool.sh`;
0003 — версия v2, `76fdf028…`). Подробности — `packaging/BUILD-ipatool.md`.

Опубликованная сборка `ipatool-2.6.0-redirect`, которую скачивает
`packaging/fetch_ipatool.py`, собрана **до** патчей: в её `ipatool.exe` нет
маркеров 0001, 0002 и 0003 (проверено). Для 4b она не годится.

### Путь А: собрать самому (bootstrap-скрипты Макса)

Скрипт сам ставит Go 1.25.0 в `build/ipatool-bootstrap/` (с проверкой SHA-256
архива с go.dev; системный Go не трогает), собирает ipatool с патчами,
проверяет маркеры, кладёт бинарник в `bin/` и печатает его SHA-256.

macOS (нужны git и Xcode Command Line Tools — для cgo нужен clang):

```bash
bash packaging/bootstrap-ipatool.sh            # архитектура этого Mac
```

Windows (PowerShell, нужен git):

```powershell
powershell -ExecutionPolicy Bypass -File packaging\bootstrap-ipatool.ps1
```

Честно о проверке: скрипт `.sh` прогнан на Linux (linux-amd64): его
`bin/ipatool` совпал байт в байт с результатом `build-ipatool.sh linux-amd64`
(SHA-256 `e58be6376923af0c170a13e15d4b117f9db2d4c60f3894a0b42b03b38c454436`).
Ветку для macOS и запуск `.ps1` на настоящем Windows ещё никто не проверял.
Сборка воспроизводимая: `packaging/build-ipatool.sh windows-amd64` дважды дал
один и тот же архив, SHA-256 `9c5bb265727abf014cb1e64f29ab1f17afd86025edfdaf21adca522c5596c7a2`
(`ipatool.exe` внутри — `b5932fae9a030f3fec5c2de670f8716bdbf09f05c986c398bf512fdf81ff0f2f`).
Под macOS кросс-сборка с Linux невозможна: ipatool для macOS собирается с cgo
(связка ключей), нужен Mac.

### Путь Б: готовый бинарник

Если бинарник передали отдельно, положите его в `bin/` в корне репозитория
(`bin/ipatool` или `bin\ipatool.exe`; программа ищет там первым, папка в
`.gitignore`) и сверьте SHA-256 с тем, что прислали:

```bash
shasum -a 256 bin/ipatool                      # macOS
```

```powershell
Get-FileHash bin\ipatool.exe -Algorithm SHA256   # Windows
```

На macOS после копирования: `chmod +x bin/ipatool` и, если Gatekeeper
блокирует, `xattr -d com.apple.quarantine bin/ipatool`.

### Что будет со старым ipatool (без патчей)

| Что | Со старым `ipatool-2.6.0-redirect` |
|---|---|
| Сгруженные приложения | возвращаются как обычно |
| Уже купленные на вашем Apple ID | возвращаются как обычно |
| Свой файл IPA | ставится как обычно |
| Удалённые из App Store, которых нет на аккаунте (нужна новая бесплатная лицензия) | **не работают**: без 0001 страна аккаунта неизвестна, цену проверить нельзя; шлюз отказывает до покупки (`PREFLIGHT_BLOCKED`), журнал и лимит не трогаются. В окне строки серые: «Нужен дополнительный компонент · Как установить» |
| Группа «Нет в App Store вашей страны» (`region_probe`) | выключена, группа скрыта |
| Список покупок | без 0002 грузится постранично по 100 — медленнее, но полностью |
| Пароль связки ключей | без 0003 идёт через скрытый терминал (как вводит человек), **никогда** не в аргументах и не в переменных окружения |
| −128 «магазин не совпадает» | распознаётся как обычно |

## 3. Запуск

```bash
python -m apprestore_gui.app --ui quick   # окно 4b (из исходников по умолчанию открывается окно на виджетах)
QT_QPA_PLATFORM=offscreen python scripts/ui4b_screenshots.py   # экраны 4b на фейковых данных, PNG в screenshots/
python -m apprestore_gui.app --self-test --output selftest.json   # проверка окружения
```

Окно `--ui quick` работает на настоящих данных: телефон через pymobiledevice3
(приложения, сгруженные, место), ipatool_api (сессия, покупки, страна
аккаунта), кэш покупок, `license_guard`, `icons_cache`, `region_probe`. Фейковые
данные — только в тестах, `--self-test` и `scripts/ui4b_screenshots.py`.
Без телефона и без входа окно показывает «Подключите iPhone» (проверка:
`QT_QPA_PLATFORM=offscreen python scripts/ui4b_live_shot.py out.png --onboarded`).

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
| «Нужен дополнительный компонент» у удалённых | ipatool без патчей — шаг 2 (путь А или Б) |
| bootstrap: `cgo: C compiler "clang" not found` (macOS) | `xcode-select --install` |
| Телефон не виден (Windows) | нет службы Apple Mobile Device: поставьте «Устройства Apple» или iTunes, переподключите кабель, нажмите «Доверять» |
| Телефон не виден (macOS) | разблокируйте iPhone и нажмите «Доверять»; кабель с передачей данных |
| `metadata X != runtime Y` в self-test | установлены старые метаданные пакета: повторите `pip install --no-deps --no-build-isolation -e .` |
| Окно не открывается в Windows по RDP / в виртуалке | Qt Quick нужен OpenGL/Direct3D: `set QT_QUICK_BACKEND=software` перед запуском |
| Pip ругается на хэши | ставьте только по `*.lock` с `--require-hashes --only-binary=:all:`, Python 3.10–3.13 |

Пароль Apple ID программа передаёт только ipatool; журнал лицензий и кэш
покупок лежат в папке данных пользователя и в репозиторий не попадают.
