# Патченый ipatool для запуска из исходников

Опубликованный архив `ipatool-2.6.0-redirect` собран до патчей 0001–0003.
Для запуска AppRestore из исходников ipatool нужно собрать локально. Это
делает один скрипт: он сам скачивает Go 1.25.0, применяет патчи, собирает
бинарник и кладёт его туда, где программа ищет его первым.

## Запуск

Запускать из корня клона AppRestore. Скрипты лежат в `packaging/` рядом с `build-ipatool.sh`.

**macOS** (Terminal):

```bash
xcode-select --install          # один раз: git и компилятор C (нужен для сборки под macOS)
bash packaging/bootstrap-ipatool.sh
```

**Windows** (PowerShell 5.1 или новее, ставить ничего не нужно):

```powershell
powershell -ExecutionPolicy Bypass -File packaging\bootstrap-ipatool.ps1
```

Первый запуск занимает 1–3 минуты: скачивается Go (около 60 МБ) и
зависимости ipatool. Повторный запуск с теми же патчами ничего не
пересобирает. Пересобрать принудительно можно флагом `--force` (sh) или `-Force` (PowerShell).

## Что на выходе

| | macOS | Windows |
|---|---|---|
| Бинарник | `bin/ipatool` | `bin\ipatool.exe` |
| Лицензия ipatool (MIT) | `bin/ipatool-LICENSE.txt` | `bin\ipatool-LICENSE.txt` |
| Go и кэши сборки | `build/ipatool-bootstrap/` | `build\ipatool-bootstrap\` |

В конце скрипт печатает:

```
marker redirect: OK (too many authentication redirects)
marker 0001: OK (appstore.CountryCodeFromStoreFront)
marker 0002: OK (--all cannot be combined with --page or --max-results)
marker 0003: OK (keychain-passphrase-stdin)
sha256 <64 hex>  …/bin/ipatool
patched ipatool ready: …/bin/ipatool
```

Если хоть одного маркера нет или какой-то хэш не совпал, скрипт
останавливается с ошибкой, и `bin/` при этом не меняется.

Программа ищет `bin/ipatool(.exe)` в корне репозитория первым, затем рядом с
Python и в PATH (`resolve_tool`). Проверить можно так:
`python -m apprestore_gui.app --self-test --output selftest.json`.

## Что проверяет скрипт

1. **Go 1.25.0.** Если `go` в PATH уже версии go1.25.0, скрипт берёт его.
   Иначе скачивает официальный архив с `go.dev/dl` в
   `build/ipatool-bootstrap/` и сверяет SHA-256 с закреплёнными в скрипте
   значениями с go.dev. Системный Go не меняется, `GOPATH` и кэши сборки
   тоже лежат в `build/ipatool-bootstrap/`.
2. **Исходники ipatool.** Берётся `majd/ipatool` на коммите `cde7d00`, это 2.6.0 с
   редиректами 301/302/307/308.
   - macOS и Linux: `git fetch` коммита, затем проверка `rev-parse` внутри `build-ipatool.sh`.
   - Windows: zip коммита с GitHub и проверка SHA-256 всего дерева исходников, git не нужен.
3. **Патчи 0001–0003.** SHA-256 каждого патча закреплён в `build-ipatool.sh`,
   PowerShell-вариант читает их оттуда же. Патчи применяются строго, без
   смещений и fuzz.
4. **Сборка** идёт с теми же флагами, что и в `build-ipatool.sh` (`-trimpath`,
   `-buildvcs=false`, версия 2.6.0). Затем проверяются маркеры в бинарнике и
   `ipatool --version` / `--help`.

Секретов скрипты не читают и не пишут. С Apple ID и связкой ключей не работают.

Состояние: Linux-ветка `.sh` проверена сборкой. Логику `.ps1` проверила
кросс-сборка windows-amd64 в PowerShell 7 на Linux: бинарник побайтно
совпал со сборкой `build-ipatool.sh windows-amd64`. **macOS-ветка и запуск
`.ps1` на настоящем Windows не проверены** (untested).

## Опубликованный (непатченый) бинарник и патченый

По коду `ipatool_api.py` / `region_probe.py` / `license_gate.py` и патчам:

| Функция | Опубликованный `ipatool-2.6.0-redirect` | Нужен патч |
|---|---|---|
| Вход в Apple ID, 2FA, редиректы 301/307/308 | работает | нет (это коммит `cde7d00`, не патч) |
| Проверка сессии, `list-versions`, загрузка уже купленного | работает | нет |
| Распознавание `STORE_MISMATCH` (-128 «Account Not In This Store») | работает: это разбор ответа Apple, от патчей не зависит | нет |
| Страна аккаунта (`auth info` → `storeFront`/`countryCode`) | `account_info().country_code = None` | **0001** |
| ↳ группа «Нет в App Store вашей страны» (`region_probe`) | группа скрыта, так как страны нет | 0001 |
| ↳ цена в стране аккаунта перед взятием лицензии | страны нет, поэтому цену не проверить. По коду (`license_gate.py`) лицензия тогда не берётся | 0001 |
| Вся история покупок одним вызовом (`list-purchases --all`) | работает постранично по 100 (`FLAG_UNSUPPORTED` и автопереход), просто медленнее | 0002 (только скорость) |
| Пароль связки ключей в GUI | GUI вводит его через скрытый терминал (pty/ConPTY). В argv и env пароля нет, это безопасно, просто нужен ручной ввод | 0003 (удобство) |
| Пароль связки ключей в bench и автоматизации | безопасного канала нет. `ipatool_api` в режиме `auto` выдаёт `PASSPHRASE_NO_SECURE_METHOD`. Флаг `--keychain-passphrase` (виден в `ps`) включается только явно, в bench | **0003** (stdin без argv и env) |
| Взятие лицензии (`purchase`) в AppRestore | **не делается**: preflight `IpatoolClient.license_preflight()` блокирует его заранее с текстом «Для возврата приложений нужна сборка ipatool с патчами», в журнал ничего не пишется, лимит не тратится | **нужна патченая сборка** (0001–0003) |

Для полной функциональности нужен патченый бинарник. На опубликованном
`ipatool-2.6.0-redirect` взять лицензию не получается (проверка Димы).

Патченую сборку preflight узнаёт без сети и без доступа к связке ключей, результат кэшируется на клиенте:
- `ipatool list-purchases --all --page 2`: патченый отвечает
  «--all cannot be combined…», старый отвечает `unknown flag: --all`;
- в `ipatool --help` есть флаг `--keychain-passphrase-stdin`.
`storeFront`/`countryCode` в `auth info` засчитываются только как
подтверждение 0001. Если их нет, это ещё не значит, что бинарник старый.

## Частые ошибки

| Что видно | Что делать |
|---|---|
| `no C compiler: run xcode-select --install` (macOS) | выполнить `xcode-select --install` и запустить снова |
| `Go archive SHA-256 mismatch` | архив скачался битым или подменён: удалить `build/ipatool-bootstrap/dl.*` и запустить снова. Если ошибка повторяется, не продолжать |
| `ipatool patch … SHA-256 mismatch` | патчи в `packaging/patches/` не совпадают с веткой: `git status`, вернуть файлы |
| `source tree SHA-256 mismatch` (Windows) | GitHub отдал не тот архив исходников: повторить; если повторяется — сообщить |
| PowerShell: «выполнение сценариев отключено» | запускать через `powershell -ExecutionPolicy Bypass -File …`, как выше |
| Нужно удалить всё, что поставил скрипт | удалить `bin/ipatool*` и `build/ipatool-bootstrap/`. Если не удаляется (кэш модулей Go только для чтения), сначала `chmod -R u+w build/ipatool-bootstrap`, на Windows снять атрибут «только чтение» |

Примечание для репозитория: `bin/` сейчас не в `.gitignore`. Бинарник
нельзя случайно закоммитить, стоит добавить строку `/bin/`.
