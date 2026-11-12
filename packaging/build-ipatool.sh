#!/usr/bin/env bash
# Build one ipatool archive from upstream majd/ipatool v2.8.1.
#
# Apple changed App Store sign-in around 6 Nov 2026: the old flow used by
# ipatool <= 2.7 now gets an empty HTTP 204 from buy.itunes.apple.com.
# v2.8.0/2.8.1 restored authentication (local Anisette generation, Store
# provisioning and AMD proofs) and fixed delisted redownloads upstream.
#
# On top of v2.8.1 AppRestore applies its own patches from packaging/patches/
# (each pinned by SHA-256 below, applied in this order):
#   0001-ipatool-auth-info-country.patch - `auth info` also prints the
#   signed-in account's raw storeFront and its ISO countryCode (offline map
#   from 2.6.0, because 2.8 discovers storefronts over the network). Read-only.
#   0002-ipatool-list-purchases-all.patch - `list-purchases --all` returns the
#   whole purchase history in one call; without the flag nothing changes.
#   0003-ipatool-keychain-passphrase-env-stdin.patch - keychain passphrase via
#   --keychain-passphrase-stdin (AppRestore uses only this; never argv/env).
#   0004-ipatool-readonly-download-probe.patch - compare download channels
#   without purchasing/downloading and without private response data.
#   0005-ipatool-purchase-locker.patch - `list-purchases --all` also merges
#   this account's own iTunes purchase locker (visible and hidden) on the
#   account storefront. A locker failure leaves the DAAP list unchanged.
#   0006-ipatool-product-post-redirects.patch - preserve product POSTs across
#   bounded, validated Apple redirects; the Store AMD proof is recomputed for
#   every hop. Diagnostics use the same signed path as real downloads.
#   0007-ipatool-purchase-evidence.patch - distinguish confirmed acquisition,
#   ownership, refusal and uncertain responses; emit safe per-attempt evidence.
#   0008-ipatool-unavailable-redownload-update-probe.patch - an empty volumeStore
#   2059 may still use the read-only redownload channel; probe-download also
#   inspects an unpinned updateProduct channel.
#
# Dropped with 2.8.1 (covered upstream): the 2.6.0 delisted-redownload patch
# (upstream unpinned entitlement downloads + identity checks) and the unpinned
# updateProduct download fallback (upstream deliberately keeps delisted
# recovery to redownload + ent/download).
#
# The archive is reproducible: packaging/pack_ipatool.py writes it with a fixed
# mtime (SOURCE_DATE_EPOCH, else the commit time of $commit), uid/gid 0 and a
# gzip header without name or time. Same Go toolchain + same commit and
# patches = same SHA-256. The Go toolchain is pinned below (go1.26.5, the same
# version upstream v2.8.1 and build-ipatool.yml use); override with IPATOOL_GOTOOLCHAIN.
#
# linux-amd64 is only for local checks; AppRestore does not ship it.
#
# Usage: packaging/build-ipatool.sh windows-amd64|macos-arm64|macos-amd64|linux-amd64
set -euo pipefail

asset="${1:?asset required: windows-amd64, macos-arm64, macos-amd64 or linux-amd64}"
commit="3e84853e2e5d60540a5022b64895d496910144eb"
version="2.8.1"
go_toolchain="${IPATOOL_GOTOOLCHAIN:-go1.26.5}"
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
  "0001-ipatool-auth-info-country.patch:8ed499c69be593789cc3fd05de4a220f8779be102ec5bc0a3e3b653346cfda6d"
  "0002-ipatool-list-purchases-all.patch:035e6760ffdec8aedfe957bc3a3ea82dc2c0a6bfc7054afc1c6fa4eefb6e8e32"
  "0003-ipatool-keychain-passphrase-env-stdin.patch:d1eb59ff504e1986735e46e9a1b4c81901b22ee746a1eb545a952e68a1aac701"
  "0004-ipatool-readonly-download-probe.patch:cbe7fe7e62d417f8e729498adaf81196a5996eba0bee0259e329cf9647200837"
  "0005-ipatool-purchase-locker.patch:f4d63cd207469c19cc06d2a0d48b67ace0df665ae3a9a3c73c0036200b9637d5"
  "0006-ipatool-product-post-redirects.patch:1d9c10872990e9fff6802de101726ddbad13f26fedd2054ba202dae8f6691251"
  "0007-ipatool-purchase-evidence.patch:e4b25df28cc2e8c7e01aae855d556c8d49e8437ffb0590e56ed515d64088632e"
  "0008-ipatool-unavailable-redownload-update-probe.patch:fcfb39ecab336f7a865c0b7beea1b859ded469e333acba9a31c0f5a96684850d"
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

# The delisted fallback and identity/platform guards must pass with the same
# patches that ship. Mocks are generated locally; no Apple account is needed.
(
  cd "$work/src"
  go generate ./pkg/...
  go test ./pkg/appstore ./cmd
)

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

# Markers: the first two prove the 2.8 Store authentication (AMD provisioning)
# is present; the rest prove each AppRestore patch shipped. The exported
# appstore.CountryCodeFromStoreFront symbol exists only after patch 0001
# (the build does not strip symbols).
for marker in \
  "store AMD state is not provisioned" \
  "unsupported authentication redirect status" \
  "appstore.CountryCodeFromStoreFront" \
  "--all cannot be combined with --page or --max-results" \
  "keychain-passphrase-stdin" \
  "Inspect download channels without purchase, asset download or private response data" \
  "vt=lockerData" \
  "invalid Apple product redirect" \
  "purchase response unconfirmed" \
  "update_unpinned"
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
  if [[ "$reported" != *"$version"* ]]; then
    echo "ipatool --version did not report $version" >&2
    exit 1
  fi
fi

if command -v sha256sum >/dev/null 2>&1; then
  sha256sum "$archive"
else
  shasum -a 256 "$archive"
fi
echo "built $archive from $commit"
