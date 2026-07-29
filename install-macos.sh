#!/usr/bin/env bash
# User-space AppRestore installer for macOS release payloads.

set -euo pipefail
umask 077

APPRESTORE_VERSION="0.1.4"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
APP_SUPPORT_DIR="$HOME/Library/Application Support/AppRestore"
VENV_DIR="$APP_SUPPORT_DIR/venv"
BIN_DIR="$HOME/.local/bin"
COMMAND_PATH="$BIN_DIR/apprestore"
MANAGED_MARKER="# Managed by AppRestore install-macos.sh"
VENV_MARKER_NAME=".apprestore-managed"
VENV_MARKER_VALUE="AppRestore managed venv v1"
TEMP_WRAPPER=""
CREATED_COMMAND=false
VENV_STAGING=""
VENV_BACKUP=""
NEW_VENV_ACTIVE=false
OLD_VENV_MOVED=false
INSTALL_COMPLETE=false

RED='\033[0;31m'
GREEN='\033[0;32m'
CYAN='\033[0;36m'
DIM='\033[2m'
NC='\033[0m'

die() {
  printf "%b\n" "${RED}Ошибка:${NC} $*" >&2
  exit 1
}

note() {
  printf "%b\n" "${CYAN}$*${NC}"
}

warn() {
  printf "%b\n" "${DIM}$*${NC}" >&2
}

marker_is_exact() {
  local marker_path="$1"
  local expected_value="$2"
  local marker_size

  [[ -f "$marker_path" && ! -L "$marker_path" ]] || return 1
  marker_size="$(LC_ALL=C wc -c <"$marker_path" | tr -d '[:space:]')" || \
    return 1
  [[ "$marker_size" -eq "${#expected_value}" ]] || return 1
  grep -Fqx "$expected_value" "$marker_path"
}

command_is_managed() {
  [[ -f "$COMMAND_PATH" && ! -L "$COMMAND_PATH" ]] && \
    grep -Fqx "$MANAGED_MARKER" "$COMMAND_PATH"
}

venv_is_managed() {
  local candidate="$1"
  [[ -d "$candidate" && ! -L "$candidate" ]] && \
    marker_is_exact "$candidate/$VENV_MARKER_NAME" "$VENV_MARKER_VALUE"
}

cleanup() {
  local status=$?
  trap - EXIT HUP INT TERM

  if [[ -n "$TEMP_WRAPPER" && -f "$TEMP_WRAPPER" ]]; then
    rm -f -- "$TEMP_WRAPPER" || warn \
      "Не удалось удалить временный launcher: $TEMP_WRAPPER"
  fi
  if [[ -n "$VENV_STAGING" && -d "$VENV_STAGING" && \
    "$VENV_STAGING" == "$APP_SUPPORT_DIR"/.venv-staging.* ]]; then
    rm -rf -- "$VENV_STAGING" || warn \
      "Не удалось удалить staging venv: $VENV_STAGING"
  fi
  if [[ "$status" -ne 0 && "$INSTALL_COMPLETE" != true ]]; then
    if [[ "$CREATED_COMMAND" == true ]] && command_is_managed; then
      rm -f -- "$COMMAND_PATH" || warn \
        "Не удалось удалить незавершённый launcher: $COMMAND_PATH"
    fi
    if [[ "$NEW_VENV_ACTIVE" == true && \
      "$VENV_DIR" == "$APP_SUPPORT_DIR/venv" ]]; then
      if venv_is_managed "$VENV_DIR"; then
        if ! rm -rf -- "$VENV_DIR"; then
          warn "Не удалось удалить незавершённый venv: $VENV_DIR"
        fi
      else
        warn "Не удаляю venv без exact marker после ошибки: $VENV_DIR"
      fi
    fi
    if [[ "$OLD_VENV_MOVED" == true && \
      -d "$VENV_BACKUP/venv" && ! -e "$VENV_DIR" ]]; then
      if mv "$VENV_BACKUP/venv" "$VENV_DIR"; then
        OLD_VENV_MOVED=false
      fi
    fi
  fi
  if [[ -n "$VENV_BACKUP" && -d "$VENV_BACKUP" && \
    "$VENV_BACKUP" == "$APP_SUPPORT_DIR"/.venv-backup.* ]]; then
    if [[ "$OLD_VENV_MOVED" == true && -d "$VENV_BACKUP/venv" ]]; then
      warn "Сохраняю backup предыдущего venv: $VENV_BACKUP"
    else
      rmdir "$VENV_BACKUP" 2>/dev/null || true
    fi
  fi
  exit "$status"
}

require_macos_user() {
  [[ "$(uname -s)" == "Darwin" ]] || die "этот установщик предназначен для macOS"
  [[ "${EUID:-$(id -u)}" -ne 0 ]] || die \
    "не запускай установщик через sudo или от root"
  [[ -n "${HOME:-}" && "$HOME" == /* && "$HOME" != "/" ]] || die \
    "HOME должен указывать на пользовательский каталог"
  [[ -d "$HOME" ]] || die "пользовательский каталог HOME не найден"
}

assert_directory_within_home() {
  local directory="$1"
  local home_real directory_real

  home_real="$(cd "$HOME" && pwd -P)"
  directory_real="$(cd "$directory" && pwd -P)"
  case "$directory_real" in
    "$home_real"|"$home_real"/*) ;;
    *) die "каталог установки вышел за пределы HOME: $directory_real" ;;
  esac
}

prepare_install_directories() {
  local library="$HOME/Library"
  local application_support="$library/Application Support"
  local local_root="$HOME/.local"

  [[ -d "$library" && ! -L "$library" ]] || die \
    "ожидался обычный каталог без symlink: $library"

  if [[ -e "$application_support" || -L "$application_support" ]]; then
    [[ -d "$application_support" && ! -L "$application_support" ]] || die \
      "ожидался обычный каталог без symlink: $application_support"
  else
    mkdir "$application_support"
  fi
  assert_directory_within_home "$application_support"

  if [[ -e "$APP_SUPPORT_DIR" || -L "$APP_SUPPORT_DIR" ]]; then
    [[ -d "$APP_SUPPORT_DIR" && ! -L "$APP_SUPPORT_DIR" ]] || die \
      "ожидался обычный каталог без symlink: $APP_SUPPORT_DIR"
  else
    mkdir "$APP_SUPPORT_DIR"
  fi
  assert_directory_within_home "$APP_SUPPORT_DIR"
  chmod 700 "$APP_SUPPORT_DIR" 2>/dev/null || true

  if [[ -e "$local_root" || -L "$local_root" ]]; then
    [[ -d "$local_root" && ! -L "$local_root" ]] || die \
      "ожидался обычный каталог без symlink: $local_root"
  else
    mkdir "$local_root"
  fi
  assert_directory_within_home "$local_root"

  if [[ -e "$BIN_DIR" || -L "$BIN_DIR" ]]; then
    [[ -d "$BIN_DIR" && ! -L "$BIN_DIR" ]] || die \
      "ожидался обычный каталог без symlink: $BIN_DIR"
  else
    mkdir "$BIN_DIR"
  fi
  assert_directory_within_home "$BIN_DIR"
  chmod 700 "$BIN_DIR" 2>/dev/null || true
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

find_brew_python() {
  local brew="$1"
  local prefix candidate

  prefix="$("$brew" --prefix python@3.12)"
  for candidate in \
    "$prefix/bin/python3.12" \
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

configure_future_path() {
  local profile path_line
  path_line='export PATH="$HOME/.local/bin:$PATH"'

  case "${SHELL:-}" in
    */zsh) profile="$HOME/.zprofile" ;;
    */bash) profile="$HOME/.bash_profile" ;;
    *) profile="$HOME/.profile" ;;
  esac

  if [[ -L "$profile" ]]; then
    warn "Не изменяю symlink $profile."
    warn "Добавь вручную: $path_line"
    return 0
  fi
  if [[ -e "$profile" && ! -f "$profile" ]]; then
    warn "Не изменяю нестандартный объект $profile."
    warn "Добавь вручную: $path_line"
    return 0
  fi
  if [[ -f "$profile" ]] && grep -Fqx "$path_line" "$profile"; then
    return 0
  fi

  {
    printf '\n%s\n' "# AppRestore user command"
    printf '%s\n' "$path_line"
  } >>"$profile"
}

write_command_wrapper() {
  local brew_bin="$1"
  local entrypoint="$VENV_DIR/bin/python"
  local command_existed=false

  [[ -x "$entrypoint" ]] || die "Python interpreter AppRestore не создан"
  if [[ -e "$COMMAND_PATH" || -L "$COMMAND_PATH" ]]; then
    command_existed=true
    if ! command_is_managed; then
      die "$COMMAND_PATH уже существует и не принадлежит AppRestore"
    fi
  fi

  TEMP_WRAPPER="$(mktemp "$BIN_DIR/.apprestore.XXXXXXXX")"
  {
    printf '%s\n' '#!/usr/bin/env bash'
    printf '%s\n' "$MANAGED_MARKER"
    printf '%s\n' 'set -euo pipefail'
    printf 'export PATH=%q:"$PATH"\n' "$brew_bin"
    printf 'exec %q -m apprestore_core.cli "$@"\n' "$entrypoint"
  } >"$TEMP_WRAPPER"
  chmod 755 "$TEMP_WRAPPER"
  mv -f "$TEMP_WRAPPER" "$COMMAND_PATH"
  TEMP_WRAPPER=""
  if [[ "$command_existed" != true ]]; then
    CREATED_COMMAND=true
  fi
}

main() {
  local brew brew_bin python installed_version

  trap cleanup EXIT
  trap 'exit 129' HUP
  trap 'exit 130' INT
  trap 'exit 143' TERM

  require_macos_user
  prepare_install_directories
  brew="$(find_brew)" || die \
    "не найден Homebrew. Установи его по инструкции https://brew.sh и повтори команду"
  brew_bin="$(cd "$(dirname "$brew")" && pwd -P)"

  note "Установка зависимостей AppRestore через Homebrew…"
  HOMEBREW_NO_ENV_HINTS=1 "$brew" install python@3.12 ipatool
  python="$(find_brew_python "$brew")" || die \
    "Homebrew не предоставил совместимый Python 3.10+"

  if [[ -e "$VENV_DIR" || -L "$VENV_DIR" ]]; then
    [[ -d "$VENV_DIR" && ! -L "$VENV_DIR" ]] || die \
      "ожидалось обычное Python-окружение без symlink: $VENV_DIR"
    venv_is_managed "$VENV_DIR" || die \
      "существующий venv не содержит exact managed marker: $VENV_DIR"
  fi

  note "Подготовка отдельного Python-окружения…"
  VENV_STAGING="$(mktemp -d "$APP_SUPPORT_DIR/.venv-staging.XXXXXXXX")"
  "$python" -m venv --copies "$VENV_STAGING"
  printf '%s' "$VENV_MARKER_VALUE" >"$VENV_STAGING/$VENV_MARKER_NAME"
  chmod 600 "$VENV_STAGING/$VENV_MARKER_NAME"

  if [[ -d "$VENV_DIR" ]]; then
    VENV_BACKUP="$(mktemp -d "$APP_SUPPORT_DIR/.venv-backup.XXXXXXXX")"
    mv "$VENV_DIR" "$VENV_BACKUP/venv"
    OLD_VENV_MOVED=true
  fi
  mv "$VENV_STAGING" "$VENV_DIR"
  VENV_STAGING=""
  NEW_VENV_ACTIVE=true

  "$VENV_DIR/bin/python" -m pip install \
    --disable-pip-version-check \
    --no-input \
    --upgrade \
    --force-reinstall \
    "$SCRIPT_DIR"

  installed_version="$(
    "$VENV_DIR/bin/python" -m apprestore_core.cli --version
  )"
  [[ "$installed_version" == "$APPRESTORE_VERSION" ]] || die \
    "ожидалась версия $APPRESTORE_VERSION, установлена $installed_version"
  write_command_wrapper "$brew_bin"
  installed_version="$("$COMMAND_PATH" --version)"
  [[ "$installed_version" == "$APPRESTORE_VERSION" ]] || die \
    "launcher $COMMAND_PATH вернул неожиданную версию $installed_version"
  if ! configure_future_path; then
    warn "Не удалось автоматически обновить профиль shell."
    warn 'Добавь вручную: export PATH="$HOME/.local/bin:$PATH"'
  fi

  NEW_VENV_ACTIVE=false
  INSTALL_COMPLETE=true
  if [[ "$OLD_VENV_MOVED" == true && -d "$VENV_BACKUP/venv" ]]; then
    if rm -rf -- "$VENV_BACKUP/venv"; then
      OLD_VENV_MOVED=false
    else
      warn "AppRestore установлен, но старый venv сохранён: $VENV_BACKUP"
    fi
  fi
  if [[ "$OLD_VENV_MOVED" != true && \
    -n "$VENV_BACKUP" && -d "$VENV_BACKUP" ]]; then
    if rmdir "$VENV_BACKUP"; then
      VENV_BACKUP=""
    else
      warn "Пустой backup-каталог оставлен для проверки: $VENV_BACKUP"
    fi
  fi
  printf "%b\n" "${GREEN}AppRestore ${installed_version} установлен.${NC}"
  printf '%s\n' "Команда: $COMMAND_PATH"
  printf '%s\n' \
    'Для текущего терминала выполни: export PATH="$HOME/.local/bin:$PATH"'
  printf '%s\n' \
    "В новых окнах терминала PATH будет настроен автоматически."
  printf "%b\n" \
    "${DIM}Пароль Apple ID и 2FA вводятся только в самом ipatool.${NC}"
}

main "$@"
