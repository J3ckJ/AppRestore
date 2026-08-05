#!/usr/bin/env bash
# User-space AppRestore installer for macOS release payloads.

set -euo pipefail
umask 077
PATH='/usr/bin:/bin:/usr/sbin:/sbin'
export PATH

APPRESTORE_VERSION="0.2.0"
PYTHON_VERSION="3.12.13"
PYTHON_BUILD="20260804"
PYTHON_MACOS_X64_SHA256="23c1069b954060a875cce80a2d98afe9ca20b8e5244cf8df6c9475497d78bc4c"
PYTHON_MACOS_ARM64_SHA256="b00971ee829e39965e2bda5585666dfdcc74bd1bd97f4b75071b3b05cecf52fd"
IPATOOL_VERSION="2.3.1"
IPATOOL_MACOS_AMD64_SHA256="43a4b0206af94fab2e4a4bf344ff16ac3825b6c733692fcfc0cfd81af93d9df3"
IPATOOL_MACOS_ARM64_SHA256="f2e58e9d3ece196654e7b9dfcc2748cfdfbee4c5009c7f3d840640d8a1136500"
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
PYTHON_ARCHIVE=""
IPATOOL_ARCHIVE=""
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
  if [[ -n "$PYTHON_ARCHIVE" && -f "$PYTHON_ARCHIVE" && \
    "$PYTHON_ARCHIVE" == "$APP_SUPPORT_DIR"/.python-download.* ]]; then
    rm -f -- "$PYTHON_ARCHIVE" || warn \
      "Не удалось удалить архив Python: $PYTHON_ARCHIVE"
  fi
  if [[ -n "$IPATOOL_ARCHIVE" && -f "$IPATOOL_ARCHIVE" && \
    "$IPATOOL_ARCHIVE" == "$APP_SUPPORT_DIR"/.ipatool-download.* ]]; then
    rm -f -- "$IPATOOL_ARCHIVE" || warn \
      "Не удалось удалить архив ipatool: $IPATOOL_ARCHIVE"
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

python_is_supported() {
  "$1" -c 'import sys; raise SystemExit(not ((3, 10) <= sys.version_info[:2] < (3, 14)))' \
    >/dev/null 2>&1
}

recover_orphaned_backup() {
  local candidate backup_parent
  local -a candidates=()

  if [[ -e "$VENV_DIR" || -L "$VENV_DIR" ]]; then
    return 0
  fi
  shopt -s nullglob
  for candidate in "$APP_SUPPORT_DIR"/.venv-backup.*/venv; do
    if venv_is_managed "$candidate"; then
      candidates+=("$candidate")
    fi
  done
  shopt -u nullglob

  if [[ "${#candidates[@]}" -eq 0 ]]; then
    return 0
  fi
  [[ "${#candidates[@]}" -eq 1 ]] || die \
    "найдено несколько managed backup без live runtime; требуется ручная проверка"
  candidate="${candidates[0]}"
  backup_parent="$(cd "$(dirname "$candidate")" && pwd -P)"
  mv "$candidate" "$VENV_DIR"
  rmdir "$backup_parent" 2>/dev/null || warn \
    "Восстановлен runtime, но backup-каталог остался: $backup_parent"
  note "Восстановлен рабочий runtime после прерванного обновления."
}

cleanup_orphaned_staging() {
  local candidate

  shopt -s nullglob
  for candidate in "$APP_SUPPORT_DIR"/.venv-staging.*; do
    if venv_is_managed "$candidate"; then
      rm -rf -- "$candidate" || die \
        "не удалось удалить orphan staging: $candidate"
    else
      warn "Не удаляю staging без exact marker: $candidate"
    fi
  done
  shopt -u nullglob
}

install_pinned_python() {
  local destination="$1"
  local machine asset_arch archive_name archive_url expected_sha actual_sha
  local member member_count=0

  machine="$(/usr/bin/uname -m)"
  case "$machine" in
    arm64)
      asset_arch="aarch64"
      expected_sha="$PYTHON_MACOS_ARM64_SHA256"
      ;;
    x86_64)
      asset_arch="x86_64"
      expected_sha="$PYTHON_MACOS_X64_SHA256"
      ;;
    *) die "неподдерживаемая архитектура macOS для Python: $machine" ;;
  esac

  archive_name="cpython-$PYTHON_VERSION+$PYTHON_BUILD-$asset_arch-apple-darwin-install_only_stripped.tar.gz"
  archive_url="https://github.com/astral-sh/python-build-standalone/releases/download/$PYTHON_BUILD/${archive_name/+/%2B}"
  PYTHON_ARCHIVE="$(mktemp "$APP_SUPPORT_DIR/.python-download.XXXXXXXX")"

  note "Скачивание проверенного CPython $PYTHON_VERSION ($machine)…"
  /usr/bin/curl \
    --fail \
    --silent \
    --show-error \
    --location \
    --proto '=https' \
    --tlsv1.2 \
    --output "$PYTHON_ARCHIVE" \
    "$archive_url"
  actual_sha="$(/usr/bin/shasum -a 256 "$PYTHON_ARCHIVE")"
  actual_sha="${actual_sha%% *}"
  [[ "$actual_sha" == "$expected_sha" ]] || die \
    "SHA-256 Python не совпал: ожидался $expected_sha, получен $actual_sha"

  while IFS= read -r member; do
    member_count=$((member_count + 1))
    case "$member" in
      python/*) ;;
      *) die "архив Python содержит путь вне ожидаемого корня" ;;
    esac
    case "/$member/" in
      *"/../"*|*"//"*) die "архив Python содержит небезопасный путь" ;;
    esac
  done < <(/usr/bin/tar -tzf "$PYTHON_ARCHIVE")
  [[ "$member_count" -ge 100 ]] || die \
    "архив Python содержит неожиданно мало файлов"

  /usr/bin/tar \
    --strip-components 1 \
    -xzf "$PYTHON_ARCHIVE" \
    -C "$destination"
  [[ -f "$destination/bin/python3.12" && \
    ! -L "$destination/bin/python3.12" && \
    -x "$destination/bin/python3.12" ]] || die \
    "архив Python не создал ожидаемый интерпретатор"
  python_is_supported "$destination/bin/python3.12" || die \
    "проверенный runtime не соответствует Python 3.10–3.13"
  rm -f -- "$PYTHON_ARCHIVE"
  PYTHON_ARCHIVE=""
}

validate_dependency_payload() {
  local path

  for path in \
    "$SCRIPT_DIR/requirements/build.lock" \
    "$SCRIPT_DIR/requirements/runtime.lock" \
    "$SCRIPT_DIR/requirements/wheels/hexdump-3.3-py3-none-any.whl"; do
    [[ -f "$path" && ! -L "$path" ]] || die \
      "не найден обычный dependency payload без symlink: $path"
  done
  [[ -d "$SCRIPT_DIR/requirements/wheels" && \
    ! -L "$SCRIPT_DIR/requirements/wheels" ]] || die \
    "ожидался обычный каталог wheels без symlink"
}

install_pinned_ipatool() {
  local python="$1"
  local destination="$2"
  local machine asset_arch archive_name archive_url expected_sha actual_sha

  machine="$(/usr/bin/uname -m)"
  case "$machine" in
    arm64)
      asset_arch="arm64"
      expected_sha="$IPATOOL_MACOS_ARM64_SHA256"
      ;;
    x86_64)
      asset_arch="amd64"
      expected_sha="$IPATOOL_MACOS_AMD64_SHA256"
      ;;
    *) die "неподдерживаемая архитектура macOS для ipatool: $machine" ;;
  esac

  archive_name="ipatool-$IPATOOL_VERSION-macos-$asset_arch.tar.gz"
  archive_url="https://github.com/majd/ipatool/releases/download/v$IPATOOL_VERSION/$archive_name"
  IPATOOL_ARCHIVE="$(mktemp "$APP_SUPPORT_DIR/.ipatool-download.XXXXXXXX")"

  note "Скачивание проверенного ipatool $IPATOOL_VERSION ($machine)…"
  /usr/bin/curl \
    --fail \
    --silent \
    --show-error \
    --location \
    --proto '=https' \
    --tlsv1.2 \
    --output "$IPATOOL_ARCHIVE" \
    "$archive_url"
  actual_sha="$(/usr/bin/shasum -a 256 "$IPATOOL_ARCHIVE")"
  actual_sha="${actual_sha%% *}"
  [[ "$actual_sha" == "$expected_sha" ]] || die \
    "SHA-256 ipatool не совпал: ожидался $expected_sha, получен $actual_sha"

  "$python" - "$IPATOOL_ARCHIVE" "$destination" \
    "bin/ipatool-$IPATOOL_VERSION-macos-$asset_arch" <<'PY'
import os
import sys
import tarfile

archive_path, destination, expected_name = sys.argv[1:]
created = False
try:
    with tarfile.open(archive_path, "r:gz") as archive:
        members = archive.getmembers()
        if len(members) != 1:
            raise SystemExit("архив ipatool должен содержать ровно один файл")
        member = members[0]
        if member.name != expected_name or not member.isfile():
            raise SystemExit("архив ipatool содержит неожиданный объект")
        if not 0 < member.size <= 64 * 1024 * 1024:
            raise SystemExit("размер бинарного файла ipatool недопустим")
        source = archive.extractfile(member)
        if source is None:
            raise SystemExit("не удалось прочитать ipatool из архива")
        descriptor = os.open(
            destination,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL,
            0o700,
        )
        created = True
        with source, os.fdopen(descriptor, "wb") as output:
            remaining = member.size
            while remaining:
                block = source.read(min(1024 * 1024, remaining))
                if not block:
                    raise SystemExit("архив ipatool завершился раньше времени")
                output.write(block)
                remaining -= len(block)
            if source.read(1):
                raise SystemExit("ipatool длиннее заявленного размера")
            output.flush()
            os.fsync(output.fileno())
except BaseException:
    if created:
        try:
            os.unlink(destination)
        except OSError:
            pass
    raise
PY
  chmod 700 "$destination"
  rm -f -- "$IPATOOL_ARCHIVE"
  IPATOOL_ARCHIVE=""
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
  local runtime_bin="$1"
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
    printf '%s\n' 'unset PYTHONPATH PYTHONHOME PYTHONSTARTUP'
    printf 'export PATH=%q:"$PATH"\n' "$runtime_bin"
    printf 'exec %q -X utf8 -I -m apprestore_core.cli "$@"\n' "$entrypoint"
  } >"$TEMP_WRAPPER"
  chmod 755 "$TEMP_WRAPPER"
  mv -f "$TEMP_WRAPPER" "$COMMAND_PATH"
  TEMP_WRAPPER=""
  if [[ "$command_existed" != true ]]; then
    CREATED_COMMAND=true
  fi
}

main() {
  local python installed_version

  trap cleanup EXIT
  trap 'exit 129' HUP
  trap 'exit 130' INT
  trap 'exit 143' TERM

  require_macos_user
  prepare_install_directories
  recover_orphaned_backup
  cleanup_orphaned_staging
  validate_dependency_payload
  if [[ -e "$COMMAND_PATH" || -L "$COMMAND_PATH" ]]; then
    command_is_managed || die \
      "$COMMAND_PATH уже существует и не принадлежит AppRestore"
  fi

  if [[ -e "$VENV_DIR" || -L "$VENV_DIR" ]]; then
    [[ -d "$VENV_DIR" && ! -L "$VENV_DIR" ]] || die \
      "ожидалось обычное Python-окружение без symlink: $VENV_DIR"
    venv_is_managed "$VENV_DIR" || die \
      "существующий venv не содержит exact managed marker: $VENV_DIR"
  fi

  note "Подготовка отдельного проверенного Python runtime…"
  VENV_STAGING="$(mktemp -d "$APP_SUPPORT_DIR/.venv-staging.XXXXXXXX")"
  install_pinned_python "$VENV_STAGING"
  printf '%s' "$VENV_MARKER_VALUE" >"$VENV_STAGING/$VENV_MARKER_NAME"
  chmod 600 "$VENV_STAGING/$VENV_MARKER_NAME"
  python="$VENV_STAGING/bin/python3.12"
  install_pinned_ipatool "$python" "$VENV_STAGING/bin/ipatool"
  "$python" -m pip install \
    --disable-pip-version-check \
    --no-input \
    --force-reinstall \
    --require-hashes \
    --only-binary=:all: \
    --no-deps \
    --find-links "$SCRIPT_DIR/requirements/wheels" \
    --requirement "$SCRIPT_DIR/requirements/build.lock" \
    --requirement "$SCRIPT_DIR/requirements/runtime.lock"
  "$python" -m pip install \
    --disable-pip-version-check \
    --no-input \
    --force-reinstall \
    --no-index \
    --no-deps \
    --no-build-isolation \
    "$SCRIPT_DIR"

  installed_version="$("$python" -X utf8 -I -m apprestore_core.cli --version)"
  [[ "$installed_version" == "$APPRESTORE_VERSION" ]] || die \
    "ожидалась версия $APPRESTORE_VERSION, установлена $installed_version"

  if [[ -d "$VENV_DIR" ]]; then
    VENV_BACKUP="$(mktemp -d "$APP_SUPPORT_DIR/.venv-backup.XXXXXXXX")"
    mv "$VENV_DIR" "$VENV_BACKUP/venv"
    OLD_VENV_MOVED=true
  fi
  mv "$VENV_STAGING" "$VENV_DIR"
  VENV_STAGING=""
  NEW_VENV_ACTIVE=true

  write_command_wrapper "$VENV_DIR/bin"
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
