#!/usr/bin/env bash
# Install from this checkout into a private, versioned Linux runtime.
set -euo pipefail
umask 077
export PATH="/usr/bin:/bin:/usr/sbin:/sbin:$PATH"
unset PYTHONPATH PYTHONHOME PYTHONSTARTUP

SOURCE="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
GUI=false
PYTHON=""
MARKER='# Managed by AppRestore install-linux.sh'
STAGE=""
COMPLETE=false
TEMP_FILES=()
PUBLISHED=()

fail() { printf 'Ошибка: %s\n' "$*" >&2; exit 1; }
help() {
  cat <<'EOF'
Установка AppRestore из исходников для Linux x86_64.
  bash install-linux.sh             терминальная версия
  bash install-linux.sh --gui       CLI и графическая версия
  bash install-linux.sh --python /путь/к/python3

Нужны Python 3.10–3.13 с venv, usbmuxd, git, curl, tar, flock и C-компилятор.
Для Ubuntu/KDE neon: sudo apt install python3-venv usbmuxd build-essential git curl
Запускайте установщик обычным пользователем. Он не запускает sudo и не входит
в Apple ID. Окружение и инструменты ставятся в XDG_DATA_HOME/AppRestore/runtimes,
команды — в ~/.local/bin. При обновлении предыдущий runtime сохраняется.
EOF
}
while (($#)); do
  case "$1" in
    --gui) GUI=true ;;
    --python) (($# >= 2)) || fail 'Для --python нужен путь'; PYTHON="$2"; shift ;;
    -h|--help) help; exit 0 ;;
    *) fail "Неизвестный параметр: $1" ;;
  esac
  shift
done
[[ $(uname -s) == Linux ]] || fail 'Этот установщик предназначен для Linux'
[[ $(uname -m) == x86_64 ]] || fail 'Пока проверена только архитектура x86_64'
[[ $EUID != 0 ]] || fail 'Запускайте без sudo, от своего пользователя'
for tool in git curl tar flock usbmuxd; do
  command -v "$tool" >/dev/null || fail "Нужен $tool. См. bash install-linux.sh --help"
done
command -v cc >/dev/null || fail 'Нужен C-компилятор (build-essential/gcc)'
[[ -f "$SOURCE/pyproject.toml" && -f "$SOURCE/requirements/runtime.lock" &&
   -f "$SOURCE/packaging/bootstrap-ipatool.sh" ]] || fail 'Нужен полный каталог исходников AppRestore'

supported_python() {
  "$1" -I -c 'import sys; raise SystemExit(not ((3,10) <= sys.version_info[:2] < (3,14)))' >/dev/null 2>&1
}
if [[ -z "$PYTHON" ]]; then
  for name in python3.12 python3.13 python3.11 python3.10 python3; do
    candidate="$(command -v "$name" || true)"
    if [[ -n "$candidate" ]] && supported_python "$candidate"; then
      PYTHON="$candidate"; break
    fi
  done
fi
[[ -n "$PYTHON" ]] && supported_python "$PYTHON" || fail 'Нужен Python 3.10–3.13'

DATA="${XDG_DATA_HOME:-$HOME/.local/share}/AppRestore"
CACHE="${XDG_CACHE_HOME:-$HOME/.cache}/AppRestore"
BIN="$HOME/.local/bin"
for path in "$DATA" "$CACHE" "$DATA/runtimes" "$BIN"; do
  [[ ! -L "$path" ]] || fail "Каталог не должен быть симлинком: $path"
  mkdir -p -- "$path"
done
chmod 700 "$DATA" "$CACHE" "$DATA/runtimes"
[[ ! -L "$DATA/.install-linux.lock" ]] || fail 'Файл блокировки не должен быть симлинком'
exec 9>"$DATA/.install-linux.lock"
flock -n 9 || fail 'Уже выполняется другая установка AppRestore'

commands=(apprestore)
if "$GUI"; then commands+=(apprestore-gui); fi
check_command() {
  local target="$1"
  if [[ -e "$target" || -L "$target" ]]; then
    [[ -f "$target" && ! -L "$target" ]] && grep -Fqx "$MARKER" "$target" ||
      fail "Не перезаписываю чужую команду: $target. Переместите её перед установкой."
  fi
}
for name in "${commands[@]}"; do check_command "$BIN/$name"; done

cleanup() {
  local status=$?
  trap - EXIT HUP INT TERM
  if ! "$COMPLETE"; then
    for name in "${PUBLISHED[@]}"; do
      if [[ -f "$STAGE/previous/$name" ]]; then
        cp -- "$STAGE/previous/$name" "$BIN/$name"
      else
        rm -f -- "$BIN/$name"
      fi
    done
    if [[ -n "$STAGE" ]]; then rm -rf -- "$STAGE"; fi
  fi
  for path in "${TEMP_FILES[@]}"; do rm -f -- "$path"; done
  exit "$status"
}
trap cleanup EXIT
trap 'exit 129' HUP
trap 'exit 130' INT
trap 'exit 143' TERM
# Keep the actual venv path stable: pip-generated shebangs must not point at a
# staging directory that later disappears. Publish only the small launchers.
STAGE="$(mktemp -d "$DATA/runtimes/linux.XXXXXXXX")"
printf 'AppRestore Linux runtime v1\n' > "$STAGE/.apprestore-managed"
printf 'Создание отдельного окружения…\n'
"$PYTHON" -I -m venv "$STAGE/venv"
RUNTIME="$STAGE/venv/bin/python"
export PATH="$STAGE/venv/bin:$PATH"
PIP=("$RUNTIME" -I -m pip --isolated)
"${PIP[@]}" install --require-hashes --only-binary=:all: --no-deps -r "$SOURCE/requirements/build.lock"
"${PIP[@]}" install --require-hashes --only-binary=:all: --no-deps \
  --find-links "$SOURCE/requirements/wheels" -r "$SOURCE/requirements/runtime.lock"
# Copy only source/package assets: never IPA, account files, caches or the
# caller's venv. The installed wheel no longer depends on the checkout.
"$RUNTIME" -I - "$SOURCE" "$STAGE/source" <<'PY'
import shutil, sys
from pathlib import Path
source, dest = map(Path, sys.argv[1:])
dest.mkdir(mode=0o700)
for name in ('apprestore_core', 'apprestore_gui'):
    shutil.copytree(source/name, dest/name, ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))
for name in ('pyproject.toml', 'README.md', 'LICENSE', 'THIRD_PARTY_NOTICES.md'):
    shutil.copy2(source/name, dest/name)
PY
"${PIP[@]}" install --no-deps --no-build-isolation "$STAGE/source"
if "$GUI"; then
  "${PIP[@]}" install --only-binary=:all: 'PySide6==6.9.3'
fi
printf 'Сборка закреплённого ipatool из исходников…\n'
bash "$SOURCE/packaging/bootstrap-ipatool.sh"
[[ -f "$SOURCE/bin/ipatool" && ! -L "$SOURCE/bin/ipatool" ]] || fail 'Нет собранного ipatool'
cp -- "$SOURCE/bin/ipatool" "$STAGE/venv/bin/ipatool"
cp -- "$SOURCE/bin/ipatool-LICENSE.txt" "$STAGE/venv/bin/ipatool-LICENSE.txt"
chmod 700 "$STAGE/venv/bin/ipatool"
"${PIP[@]}" check
"$RUNTIME" -I -m apprestore_core.cli --version
"$RUNTIME" -I -m pymobiledevice3 version
"$STAGE/venv/bin/ipatool" --version
if "$GUI"; then
  QT_QPA_PLATFORM=offscreen QT_QUICK_BACKEND=software QT_QUICK_CONTROLS_STYLE=Basic \
    "$RUNTIME" -I -m apprestore_gui --self-test > "$STAGE/self-test.json"
fi

mkdir -m 700 "$STAGE/previous"
for name in "${commands[@]}"; do
  target="$BIN/$name"
  check_command "$target"
  if [[ -f "$target" ]]; then cp -p -- "$target" "$STAGE/previous/$name"; fi
  temp="$(mktemp "$BIN/.apprestore-linux.XXXXXXXX")"
  TEMP_FILES+=("$temp")
  {
    printf '#!/bin/bash\n%s\nset -euo pipefail\numask 077\n' "$MARKER"
    printf 'export APPRESTORE_DATA_DIR="${APPRESTORE_DATA_DIR:-%s}"\n' '${XDG_DATA_HOME:-$HOME/.local/share}/AppRestore'
    printf 'export APPRESTORE_CACHE_DIR="${APPRESTORE_CACHE_DIR:-%s}"\n' '${XDG_CACHE_HOME:-$HOME/.cache}/AppRestore'
    printf 'export APPRESTORE_IPA_DIR="${APPRESTORE_IPA_DIR:-$APPRESTORE_DATA_DIR/ipas}"\n'
    printf 'unset PYTHONPATH PYTHONHOME PYTHONSTARTUP IPATOOL_KEYCHAIN_PASSPHRASE APPRESTORE_BENCH_KEYCHAIN_PASSPHRASE\n'
    printf 'mkdir -p "$APPRESTORE_DATA_DIR" "$APPRESTORE_CACHE_DIR" "$APPRESTORE_IPA_DIR"\n'
    printf 'chmod 700 "$APPRESTORE_DATA_DIR" "$APPRESTORE_CACHE_DIR" "$APPRESTORE_IPA_DIR"\n'
    printf 'export PATH=%q:"$PATH"\n' "$STAGE/venv/bin"
    if [[ "$name" == apprestore-gui ]]; then
      printf 'exec %q -I -m apprestore_gui --ui quick "$@"\n' "$RUNTIME"
    else
      printf 'exec %q -I -m apprestore_core.cli "$@"\n' "$RUNTIME"
    fi
  } > "$temp"
  chmod 700 "$temp"
  bash -n "$temp"
  PUBLISHED+=("$name")
  mv -f -- "$temp" "$target"
done
COMPLETE=true
rm -rf -- "$STAGE/previous" "$STAGE/source"
printf 'Готово. Запуск: %s/apprestore\n' "$BIN"
if "$GUI"; then printf 'GUI: %s/apprestore-gui\n' "$BIN"; fi
case ":$PATH:" in
  *":$BIN:"*) ;;
  *) printf 'Добавьте каталог команд в PATH: export PATH="$HOME/.local/bin:$PATH"\n' ;;
esac
printf 'Для связи с iPhone должен работать системный usbmuxd. Вход: apprestore auth\n'
