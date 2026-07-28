#!/usr/bin/env bash
# AppRestore launcher for macOS.

set -euo pipefail
umask 077

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
APP_SUPPORT_DIR="$HOME/Library/Application Support/AppRestore"
VENV_DIR="$APP_SUPPORT_DIR/venv"
export APPRESTORE_IPA_DIR="${APPRESTORE_IPA_DIR:-$APP_SUPPORT_DIR/ipas}"
export APPRESTORE_CACHE_DIR="${APPRESTORE_CACHE_DIR:-$HOME/Library/Caches/AppRestore}"

RED='\033[0;31m'
GREEN='\033[0;32m'
CYAN='\033[0;36m'
BOLD='\033[1m'
DIM='\033[2m'
NC='\033[0m'

die() {
  printf "%b\n" "${RED}Ошибка:${NC} $*" >&2
  exit 1
}

find_brew() {
  if command -v brew >/dev/null 2>&1; then
    command -v brew
  elif [[ -x /opt/homebrew/bin/brew ]]; then
    printf '%s\n' /opt/homebrew/bin/brew
  elif [[ -x /usr/local/bin/brew ]]; then
    printf '%s\n' /usr/local/bin/brew
  else
    return 1
  fi
}

python_is_supported() {
  "$1" -c 'import sys; raise SystemExit(sys.version_info < (3, 10))' \
    >/dev/null 2>&1
}

find_python() {
  local candidate
  for candidate in \
    "$VENV_DIR/bin/python" \
    "$(command -v python3.12 2>/dev/null || true)" \
    "$(command -v python3 2>/dev/null || true)"; do
    [[ -n "$candidate" && -x "$candidate" ]] || continue
    if python_is_supported "$candidate"; then
      printf '%s\n' "$candidate"
      return 0
    fi
  done
  return 1
}

run_core() {
  local python
  python=$(find_python) || die \
    "нужен Python 3.10+. Запусти: $(basename "$0") setup"
  mkdir -p "$APPRESTORE_IPA_DIR" "$APPRESTORE_CACHE_DIR"
  chmod 700 "$APP_SUPPORT_DIR" "$APPRESTORE_IPA_DIR" \
    "$APPRESTORE_CACHE_DIR" 2>/dev/null || true
  if [[ -d "$VENV_DIR/bin" ]]; then
    export PATH="$VENV_DIR/bin:$PATH"
  fi
  exec "$python" "$SCRIPT_DIR/apprestore.py" "$@"
}

setup() {
  local brew python
  brew=$(find_brew) || die \
    "не найден Homebrew. Установи его с https://brew.sh и повтори setup"

  printf "%b\n" "${BOLD}${CYAN}Установка зависимостей AppRestore…${NC}"
  "$brew" install python@3.12 ipatool
  python=$(command -v python3.12 2>/dev/null || true)
  if [[ -z "$python" ]]; then
    python="$("$brew" --prefix python@3.12)/bin/python3.12"
  fi
  [[ -x "$python" ]] || die "Homebrew не предоставил Python 3.12"

  mkdir -p "$APP_SUPPORT_DIR" "$APPRESTORE_IPA_DIR" "$APPRESTORE_CACHE_DIR"
  chmod 700 "$APP_SUPPORT_DIR" "$APPRESTORE_IPA_DIR" \
    "$APPRESTORE_CACHE_DIR" 2>/dev/null || true
  "$python" -m venv "$VENV_DIR"
  "$VENV_DIR/bin/python" -m pip install --disable-pip-version-check \
    --upgrade pip
  "$VENV_DIR/bin/python" -m pip install --disable-pip-version-check \
    --upgrade "$SCRIPT_DIR"

  export PATH="$VENV_DIR/bin:$PATH"
  printf "%b\n" "${GREEN}Зависимости установлены.${NC}"
  printf "%b\n" "${DIM}Пароль Apple ID и 2FA вводятся только в самом ipatool.${NC}"
  "$VENV_DIR/bin/python" "$SCRIPT_DIR/apprestore.py" doctor
}

show_header() {
  clear 2>/dev/null || true
  printf "%b" "${BOLD}${CYAN}"
  cat <<'EOF'
     _                ____           _
    / \   _ __  _ __ |  _ \ ___  ___| |_ ___  _ __ ___
   / _ \ | '_ \| '_ \| |_) / _ \/ __| __/ _ \| '__/ _ \
  / ___ \| |_) | |_) |  _ <  __/\__ \ || (_) | | |  __/
 /_/   \_\ .__/| .__/|_| \_\___||___/\__\___/|_|  \___|
         |_|   |_|
EOF
  printf "%b\n" "${NC}${DIM}Телефон → сгруженные приложения → скачать IPA → вернуть${NC}"
  printf '%s\n' "────────────────────────────────────────────"
}

pause_menu() {
  printf '\n'
  read -r -p "Enter, чтобы продолжить…" _ || true
}

menu() {
  while true; do
    show_header
    cat <<'EOF'
  1) Восстановить сгруженные приложения
  2) Проверить зависимости
  3) Показать подключённые устройства
  4) Показать сгруженные приложения
  5) Найти локальные IPA
  6) Скачать IPA по bundle ID
  7) Проверить и установить локальный IPA
  8) Войти в Apple ID через ipatool
  9) Установить/обновить зависимости
  0) Выход
EOF
    printf '\n'
    read -r -p "Выбор: " choice || return 0
    case "$choice" in
      1) "$0" restore || true; pause_menu ;;
      2) "$0" doctor || true; pause_menu ;;
      3) "$0" devices || true; pause_menu ;;
      4) "$0" offloaded || true; pause_menu ;;
      5) "$0" scan || true; pause_menu ;;
      6)
        read -r -p "Bundle ID: " bundle_id
        [[ -n "$bundle_id" ]] && "$0" download "$bundle_id" || true
        pause_menu
        ;;
      7)
        read -r -p "Путь к IPA: " ipa_path
        [[ -n "$ipa_path" ]] && "$0" install "$ipa_path" || true
        pause_menu
        ;;
      8) "$0" auth || true; pause_menu ;;
      9) "$0" setup || true; pause_menu ;;
      0) return 0 ;;
      *) printf "%b\n" "${RED}Неверный пункт.${NC}"; pause_menu ;;
    esac
  done
}

case "${1:-}" in
  "") menu ;;
  setup) setup ;;
  wizard)
    shift
    run_core restore "$@"
    ;;
  -h|--help|help)
    if python=$(find_python); then
      exec "$python" "$SCRIPT_DIR/apprestore.py" --help
    fi
    cat <<EOF
AppRestore

  $(basename "$0") setup       установить зависимости
  $(basename "$0") restore     мастер восстановления
  $(basename "$0") doctor      проверить окружение

Для остальных команд нужен Python 3.10+.
EOF
    ;;
  *) run_core "$@" ;;
esac
