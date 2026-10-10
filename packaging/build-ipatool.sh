#!/usr/bin/env bash
# Build one ipatool archive from the pinned upstream commit that follows
# Apple authentication redirects (HTTP 301, 302, 307 and 308).
#
# Official v2.6.0 treats only 302 as a redirect, so a Store pod that answers
# 301 makes login fail before the password is checked. There is no newer
# GitHub release, so AppRestore ships this build and checks its archive hash.
#
# On top of that commit AppRestore applies its own patches from
# packaging/patches/ (each pinned by SHA-256 below):
#   0001-ipatool-auth-info-country.patch - `auth info` also prints the
#   signed-in account's raw storeFront and its ISO countryCode. Read-only.
#   0002-ipatool-list-purchases-all.patch - `list-purchases --all` returns the
#   whole purchase history in one call; without the flag nothing changes.
#
# The archive is reproducible: packaging/pack_ipatool.py writes it with a fixed
# mtime (SOURCE_DATE_EPOCH, else the commit time of $commit), uid/gid 0 and a
# gzip header without name or time. Same Go toolchain + same commit and
# patches = same SHA-256. The Go toolchain is pinned below (go1.25.0, the same
# version build-ipatool.yml installs); override with IPATOOL_GOTOOLCHAIN.
#
# linux-amd64 is only for local checks; AppRestore does not ship it.
#
# Usage: packaging/build-ipatool.sh windows-amd64|macos-arm64|macos-amd64|linux-amd64
set -euo pipefail

asset="${1:?asset required: windows-amd64, macos-arm64, macos-amd64 or linux-amd64}"
commit="cde7d00355e152714377b953ec57438626d3cb5a"
version="2.6.0"
go_toolchain="${IPATOOL_GOTOOLCHAIN:-go1.25.0}"
# Use exactly this toolchain (downloaded by go if the local one differs).
export GOTOOLCHAIN="$go_toolchain"
root="$(cd "$(dirname "$0")/.." && pwd)"
out="${IPATOOL_OUT:-$root/dist/ipatool}"
mkdir -p "$out"

case "$asset" in
  windows-amd64)
    goos="windows"
    goarch="amd64"
    suffix=".exe"
    cgo="0"
    ;;
  macos-arm64)
    goos="darwin"
    goarch="arm64"
    suffix=""
    cgo="1"
    ;;
  macos-amd64)
    goos="darwin"
    goarch="amd64"
    suffix=""
    cgo="1"
    ;;
  linux-amd64)
    goos="linux"
    goarch="amd64"
    suffix=""
    cgo="1"
    ;;
  *)
    echo "unknown ipatool asset: $asset" >&2
    exit 1
    ;;
esac

name="ipatool-${version}-${asset}${suffix}"
work="$(mktemp -d)"
trap 'rm -rf "$work"' EXIT

git init "$work/src"
git -C "$work/src" remote add origin https://github.com/majd/ipatool.git
git -C "$work/src" fetch --depth 1 origin "$commit"
git -C "$work/src" checkout --detach FETCH_HEAD
test "$(git -C "$work/src" rev-parse HEAD)" = "$commit"

sha256_of() {
  if command -v sha256sum >/dev/null 2>&1; then
    sha256sum "$1" | cut -d' ' -f1
  else
    shasum -a 256 "$1" | cut -d' ' -f1
  fi
}

# name:sha256 of every patch, applied in this order.
patches=(
  "0001-ipatool-auth-info-country.patch:05d87977a554102c9b036306ec2c125febaa62433d2a080d588527a8225b7fb8"
  "0002-ipatool-list-purchases-all.patch:025d9919871dba636a80559614a3ca402c37cf2be6965fb7a3203f4088e54445"
)
for entry in "${patches[@]}"; do
  patch_name="${entry%%:*}"
  patch_sha="${entry##*:}"
  patch_file="$root/packaging/patches/$patch_name"
  actual_sha="$(sha256_of "$patch_file")"
  if [[ "$actual_sha" != "$patch_sha" ]]; then
    echo "ipatool patch $patch_name SHA-256 mismatch: expected $patch_sha, got $actual_sha" >&2
    exit 1
  fi
  git -C "$work/src" apply --check --whitespace=nowarn "$patch_file"
  git -C "$work/src" apply --whitespace=nowarn "$patch_file"
done

go_reported="$(cd "$work/src" && go env GOVERSION)"
if [[ "$go_reported" != "$go_toolchain" ]]; then
  echo "go toolchain is $go_reported, expected $go_toolchain" >&2
  exit 1
fi
echo "go toolchain: $go_reported"

stage="$work/stage"
mkdir -p "$stage/bin"

export GOOS="$goos"
export GOARCH="$goarch"
export CGO_ENABLED="$cgo"
if [[ "$cgo" == "1" && "$goos" == "darwin" ]]; then
  export CGO_CFLAGS="-mmacosx-version-min=10.15"
  export CGO_LDFLAGS="-mmacosx-version-min=10.15"
else
  unset CGO_CFLAGS CGO_LDFLAGS || true
fi

(
  cd "$work/src"
  go build \
    -trimpath \
    -buildvcs=false \
    -ldflags="-X github.com/majd/ipatool/v2/cmd.version=${version}" \
    -o "$stage/bin/$name" \
    .
)

# The first two sentences exist only after the redirect fix (v2.6.0 does not
# contain them). The exported appstore.CountryCodeFromStoreFront symbol exists
# only after the auth info country patch ("countryCode" alone is already in
# v2.6.0, so it cannot serve as a marker; the build does not strip symbols).
# The --all sentence exists only after the list-purchases --all patch.
for marker in \
  "too many authentication redirects" \
  "unsupported authentication redirect status" \
  "appstore.CountryCodeFromStoreFront" \
  "--all cannot be combined with --page or --max-results"
do
  if ! grep -a -F -q -e "$marker" "$stage/bin/$name"; then
    echo "built ipatool is missing an expected fix: $marker" >&2
    exit 1
  fi
done

if [[ -n "${SOURCE_DATE_EPOCH:-}" ]]; then
  mtime="$SOURCE_DATE_EPOCH"
else
  mtime="$(git -C "$work/src" log -1 --format=%ct "$commit")"
fi
if [[ ! "$mtime" =~ ^[0-9]+$ ]]; then
  echo "bad archive mtime: $mtime" >&2
  exit 1
fi

python_bin=""
for candidate in python3 python; do
  if command -v "$candidate" >/dev/null 2>&1 && "$candidate" -c 'import sys; sys.exit(sys.version_info < (3, 8))' >/dev/null 2>&1; then
    python_bin="$candidate"
    break
  fi
done
if [[ -z "$python_bin" ]]; then
  echo "python 3.8+ is required to pack the archive" >&2
  exit 1
fi

archive="$out/ipatool-${version}-${asset}.tar.gz"
rm -f "$archive"
"$python_bin" "$root/packaging/pack_ipatool.py" \
  --binary "$stage/bin/$name" \
  --name "$name" \
  --mtime "$mtime" \
  --out "$archive"

listing="$(tar -tzf "$archive")"
if [[ "$listing" != "bin/$name" ]]; then
  echo "unexpected ipatool archive layout:" >&2
  printf '%s\n' "$listing" >&2
  exit 1
fi

host_os="$(go env GOHOSTOS)"
host_arch="$(go env GOHOSTARCH)"
if [[ "$goos" == "$host_os" && "$goarch" == "$host_arch" ]]; then
  chmod +x "$stage/bin/$name" || true
  reported="$("$stage/bin/$name" --version 2>&1 || true)"
  printf '%s\n' "$reported"
  if [[ "$reported" != *"2.6.0"* ]]; then
    echo "ipatool --version did not report 2.6.0" >&2
    exit 1
  fi
fi

if command -v sha256sum >/dev/null 2>&1; then
  sha256sum "$archive"
else
  shasum -a 256 "$archive"
fi
echo "built $archive from $commit"
