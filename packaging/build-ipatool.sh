#!/usr/bin/env bash
# Build one ipatool archive from the pinned upstream commit that follows
# Apple authentication redirects (HTTP 301, 302, 307 and 308).
#
# Official v2.6.0 treats only 302 as a redirect, so a Store pod that answers
# 301 makes login fail before the password is checked. There is no newer
# GitHub release, so AppRestore ships this build and checks its archive hash.
#
# Usage: packaging/build-ipatool.sh windows-amd64|macos-arm64|macos-amd64
set -euo pipefail

asset="${1:?asset required: windows-amd64, macos-arm64 or macos-amd64}"
commit="cde7d00355e152714377b953ec57438626d3cb5a"
version="2.6.0"
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

stage="$work/stage"
mkdir -p "$stage/bin"

export GOOS="$goos"
export GOARCH="$goarch"
export CGO_ENABLED="$cgo"
if [[ "$cgo" == "1" ]]; then
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

# These sentences exist only after the redirect fix. v2.6.0 does not contain them.
for marker in \
  "too many authentication redirects" \
  "unsupported authentication redirect status"
do
  if ! grep -a -F -q "$marker" "$stage/bin/$name"; then
    echo "built ipatool is missing the login redirect fix: $marker" >&2
    exit 1
  fi
done

archive="$out/ipatool-${version}-${asset}.tar.gz"
rm -f "$archive"
COPYFILE_DISABLE=1 tar -C "$stage" -czf "$archive" "bin/$name"

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
