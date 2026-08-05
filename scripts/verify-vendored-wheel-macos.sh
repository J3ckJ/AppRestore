#!/usr/bin/env bash
# Rebuild the vendored hexdump wheel with the exact managed macOS runtime.

set -euo pipefail
umask 077

PYTHON_VERSION="3.12.13"
PYTHON_BUILD="20260804"
PYTHON_MACOS_X64_SHA256="23c1069b954060a875cce80a2d98afe9ca20b8e5244cf8df6c9475497d78bc4c"
PYTHON_MACOS_ARM64_SHA256="b00971ee829e39965e2bda5585666dfdcc74bd1bd97f4b75071b3b05cecf52fd"

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"
TEMP_ROOT="${RUNNER_TEMP:-${TMPDIR:-/tmp}}"
WORK_DIR="$(mktemp -d "$TEMP_ROOT/AppRestore-wheel-check.XXXXXXXX")"
PYTHON_ROOT="$WORK_DIR/runtime"
ARCHIVE_PATH="$WORK_DIR/python.tar.gz"

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
  *)
    printf 'Unsupported macOS architecture: %s\n' "$machine" >&2
    exit 1
    ;;
esac

archive_name="cpython-$PYTHON_VERSION+$PYTHON_BUILD-$asset_arch-apple-darwin-install_only_stripped.tar.gz"
archive_url="https://github.com/astral-sh/python-build-standalone/releases/download/$PYTHON_BUILD/${archive_name/+/%2B}"

/usr/bin/curl \
  --fail \
  --silent \
  --show-error \
  --location \
  --proto '=https' \
  --tlsv1.2 \
  --output "$ARCHIVE_PATH" \
  "$archive_url"

actual_sha="$(/usr/bin/shasum -a 256 "$ARCHIVE_PATH")"
actual_sha="${actual_sha%% *}"
if [[ "$actual_sha" != "$expected_sha" ]]; then
  printf 'CPython SHA-256 mismatch: expected %s, got %s\n' \
    "$expected_sha" "$actual_sha" >&2
  exit 1
fi

member_count=0
while IFS= read -r member; do
  member_count=$((member_count + 1))
  case "$member" in
    python/*) ;;
    *)
      printf 'Unexpected CPython archive path: %s\n' "$member" >&2
      exit 1
      ;;
  esac
  case "/$member/" in
    *"/../"*|*"//"*)
      printf 'Unsafe CPython archive path: %s\n' "$member" >&2
      exit 1
      ;;
  esac
done < <(/usr/bin/tar -tzf "$ARCHIVE_PATH")
if (( member_count < 100 )); then
  printf 'CPython archive contains too few files: %s\n' "$member_count" >&2
  exit 1
fi

/bin/mkdir -p "$PYTHON_ROOT"
/usr/bin/tar \
  --strip-components 1 \
  -xzf "$ARCHIVE_PATH" \
  -C "$PYTHON_ROOT"

exact_python="$PYTHON_ROOT/bin/python3.12"
if [[ ! -x "$exact_python" || -L "$exact_python" ]]; then
  printf 'Pinned CPython executable is missing or unsafe\n' >&2
  exit 1
fi

"$exact_python" -m pip install \
  --require-hashes \
  --only-binary=:all: \
  --no-deps \
  -r "$ROOT_DIR/requirements/wheel-build.lock"
"$exact_python" "$ROOT_DIR/scripts/rebuild-vendored-wheel.py" --check
