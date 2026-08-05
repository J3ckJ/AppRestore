# Выпуск AppRestore

Релиз AppRestore публикует только GitHub Actions из неизменяемого
semver-тега вида `vMAJOR.MINOR.PATCH`. Вручную загружать `install.ps1`,
`install.sh` или source ZIP в GitHub Release не нужно.

## 1. Подготовить версию

Работайте в отдельной ветке. Обновите одну и ту же версию без префикса `v` в:

- `pyproject.toml`;
- `apprestore_core/__init__.py`;
- `install-windows.ps1`;
- `install-macos.sh`;
- `scripts/build-release.py`.

Добавьте датированную запись в `CHANGELOG.md`. Не заявляйте в changelog о
функции, которую не проверяет код или тест.

Builder сам отвергает рассинхронизацию этих версий:

```powershell
python scripts\build-release.py
```

## 2. Проверить до слияния

Минимальный локальный gate на Windows:

Команды воспроизведения wheel выполняются в отдельном окружении CPython
3.12.13; остальные проверки поддерживают Python 3.10–3.13.

```powershell
python -m pytest -q
python -m compileall -q apprestore.py apprestore_core scripts tests
python -m pip install --require-hashes --only-binary=:all: --no-deps -r requirements\wheel-build.lock
python scripts\rebuild-vendored-wheel.py --check
python scripts\build-release.py
Get-Content dist\SHA256SUMS.txt
```

На macOS дополнительно:

```bash
/bin/bash -n apprestore.sh install-macos.sh scripts/install.sh.in
python -m pytest -q
python scripts/build-release.py
(cd dist && shasum -a 256 -c SHA256SUMS.txt)
```

Перед слиянием должны пройти все jobs обычного `CI`. Каталог `dist/` в commit
не включается: release-workflow собирает assets заново из tagged source.

## 3. Создать тег

После слияния убедитесь, что локальный `main` совпадает с `origin/main` и
рабочее дерево чистое:

```powershell
git switch main
git pull --ff-only origin main
git status --short
python apprestore.py --version
```

Если команда печатает, например, `0.2.0`, создайте аннотированный тег на этом
же commit:

```powershell
git tag -a v0.2.0 -m "AppRestore 0.2.0"
git push origin v0.2.0
```

Не используйте `--force` и не переносите уже отправленный release-тег на другой
commit.

## 4. Что делает release gate

`.github/workflows/release.yml` последовательно:

1. проверяет строгий формат тега, равенство tag ↔ runtime version и наличие
   tagged commit в `origin/main`;
2. запускает полный test suite на Windows (Python 3.10, 3.12.13 и 3.13) и macOS;
3. проверяет PowerShell/Bash syntax;
4. воспроизводит vendored wheel из закреплённого source и toolchain;
5. дважды собирает все четыре assets и сравнивает их побайтно;
6. проверяет, что распакованный source ZIP без `.git` воспроизводит сборку;
7. проверяет ZIP и `SHA256SUMS.txt`;
8. передаёт ровно эти assets в publish-job;
9. создаёт draft release, загружает assets и только затем делает релиз
   публичным.

Publish-job отказывается перезаписывать уже существующий релиз с тем же тегом.
Любой failed/cancelled job блокирует публикацию.

## 5. Проверить опубликованный релиз

На странице релиза должны быть ровно:

```text
AppRestore-<version>-source.zip
install.ps1
install.sh
SHA256SUMS.txt
```

После публикации проверьте установку на чистом пользовательском профиле обеих
платформ и команды:

```text
apprestore --version
apprestore doctor
```

Версия должна совпасть с тегом. Installer URL внутри обоих bootstrap-файлов
должен указывать на versioned asset этого же релиза, а не на ветку `main`.

## Неудачный релиз

Не заменяйте assets и не переиспользуйте опубликованный тег. Добавьте заметное
предупреждение в описание проблемного релиза, исправьте причину в новой ветке и
выпустите следующую patch-версию через тот же gate. Так установленный bootstrap
и опубликованные SHA-256 остаются проверяемой историей, а не меняющейся целью.
