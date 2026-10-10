#!/usr/bin/env bash
# Build AppRestore's patched ipatool for running from source (macOS / Linux).
#
# The published ipatool archives were built before patches 0001-0003, so a
# checkout run from source needs a local build. This script:
#   1. finds Go 1.25.0 (on PATH or in build/ipatool-bootstrap/), or downloads
#      the official go1.25.0 archive from go.dev into build/ipatool-bootstrap/
#      and checks its SHA-256 against the values pinned below (from
#      https://go.dev/dl/?mode=json&include=all). The system Go is never touched.
#   2. runs packaging/build-ipatool.sh (pinned commit cde7d00, patches 0001-0003
#      checked by SHA-256 and applied with git apply, marker check, archive);
#   3. installs the binary as bin/ipatool (resolve_tool() looks there first),
#      prints its SHA-256 and confirms the markers of patches 0001, 0002, 0003.
#
# Idempotent: a second run with the same inputs keeps the existing binary.
# Go module/build caches live in build/ipatool-bootstrap/ (git-ignored).
# No secrets, no account access; network only to go.dev, github.com and the
# Go module proxy.
#
# Usage: bash packaging/bootstrap-ipatool.sh [--force] [--asset ASSET] [--repo DIR]
#   ASSET: macos-arm64 | macos-amd64 | linux-amd64 (default: this machine)
# Tested: linux-amd64. macOS branch: untested (no Mac available), same steps.
set -euo pipefail

GO_VERSION="go1.25.0"
# Official go.dev SHA-256 of the go1.25.0 archives.
go_sha256() {
  case "$1" in
    darwin-arm64) echo "544932844156d8172f7a28f77f2ac9c15a23046698b6243f633b0a0b00c0749c" ;;
    darwin-amd64) echo "5bd60e823037062c2307c71e8111809865116714d6f6b410597cf5075dfd80ef" ;;
    linux-amd64)  echo "2852af0cb20a13139b3448992e69b868e50ed0f8a1e5940ee1de9e19a123b613" ;;
    linux-arm64)  echo "05de75d6994a2783699815ee553bd5a9327d8b79991de36e38b66862782f54ae" ;;
    *) return 1 ;;
  esac
}

# Strings that exist in the binary only with the given patch (same strings
# build-ipatool.sh checks; see packaging/patches/README.md).
patch_markers=(
  "redirect:too many authentication redirects"
  "0001:appstore.CountryCodeFromStoreFront"
  "0002:--all cannot be combined with --page or --max-results"
  "0003:keychain-passphrase-stdin"
)

force=0
asset=""
root="$(cd "$(dirname "$0")/.." && pwd)"
while [[ $# -gt 0 ]]; do
  case "$1" in
    --force) force=1 ;;
    --asset) asset="${2:?--asset needs a value}"; shift ;;
    --repo) root="$(cd "${2:?--repo needs a value}" && pwd)"; shift ;;
    -h|--help) sed -n '2,25p' "$0"; exit 0 ;;
    *) echo "unknown argument: $1" >&2; exit 2 ;;
  esac
  shift
done

die() { echo "bootstrap-ipatool: $*" >&2; exit 1; }
say() { echo "==> $*"; }

build_script="$root/packaging/build-ipatool.sh"
[[ -f "$build_script" ]] || die "no packaging/build-ipatool.sh under $root (run from the AppRestore checkout or pass --repo)"

sha256_of() {
  if command -v sha256sum >/dev/null 2>&1; then sha256sum "$1" | cut -d' ' -f1
  else shasum -a 256 "$1" | cut -d' ' -f1; fi
}

# --- host ------------------------------------------------------------------
os="$(uname -s)"; arch="$(uname -m)"
case "$os" in
  Darwin) go_os="darwin"; asset_os="macos" ;;
  Linux)  go_os="linux";  asset_os="linux" ;;
  *) die "unsupported OS $os (on Windows use packaging/bootstrap-ipatool.ps1)" ;;
esac
case "$arch" in
  arm64|aarch64) go_arch="arm64" ;;
  x86_64|amd64)  go_arch="amd64" ;;
  *) die "unsupported CPU $arch" ;;
esac
go_dist="$go_os-$go_arch"
[[ -n "$asset" ]] || asset="$asset_os-$go_arch"
case "$asset" in
  macos-arm64|macos-amd64|linux-amd64) ;;
  *) die "asset $asset is not supported here (build-ipatool.sh: macos-arm64, macos-amd64, linux-amd64; windows: use the .ps1)" ;;
esac
suffix=""

for tool in git curl tar; do
  command -v "$tool" >/dev/null 2>&1 || die "$tool is required${os:+ }$( [[ $os == Darwin ]] && echo '(xcode-select --install)')"
done
command -v python3 >/dev/null 2>&1 || command -v python >/dev/null 2>&1 || die "python 3.8+ is required"
# The macOS and Linux builds use cgo (build-ipatool.sh), so a C compiler is needed.
if ! command -v cc >/dev/null 2>&1 && ! command -v clang >/dev/null 2>&1 && ! command -v gcc >/dev/null 2>&1; then
  [[ "$os" == Darwin ]] && die "no C compiler: run xcode-select --install"
  die "no C compiler (install gcc)"
fi

state="$root/build/ipatool-bootstrap"
mkdir -p "$state"

# --- Go 1.25.0 -------------------------------------------------------------
go_version_of() { GOTOOLCHAIN=local "$1" env GOVERSION 2>/dev/null || true; }

go_bin=""
if command -v go >/dev/null 2>&1 && [[ "$(go_version_of "$(command -v go)")" == "$GO_VERSION" ]]; then
  go_bin="$(command -v go)"
  say "using Go from PATH: $go_bin ($GO_VERSION)"
fi
local_go="$state/$GO_VERSION.$go_dist/go/bin/go"
if [[ -z "$go_bin" && -x "$local_go" && "$(go_version_of "$local_go")" == "$GO_VERSION" ]]; then
  go_bin="$local_go"
  say "using local Go: $go_bin"
fi
if [[ -z "$go_bin" ]]; then
  expected="$(go_sha256 "$go_dist")" || die "no pinned Go archive for $go_dist"
  url="https://go.dev/dl/$GO_VERSION.$go_dist.tar.gz"
  dl="$(mktemp -d "$state/dl.XXXXXX")"
  trap 'rm -rf "$dl"' EXIT
  say "downloading $url"
  curl -fL --proto '=https' --tlsv1.2 --retry 3 -o "$dl/go.tar.gz" "$url"
  actual="$(sha256_of "$dl/go.tar.gz")"
  [[ "$actual" == "$expected" ]] || die "Go archive SHA-256 mismatch: expected $expected, got $actual"
  say "Go archive SHA-256 OK ($actual)"
  mkdir -p "$dl/x"
  tar -xzf "$dl/go.tar.gz" -C "$dl/x"
  rm -rf "$state/$GO_VERSION.$go_dist"
  mv "$dl/x" "$state/$GO_VERSION.$go_dist"
  rm -rf "$dl"; trap - EXIT
  go_bin="$local_go"
  [[ "$(go_version_of "$go_bin")" == "$GO_VERSION" ]] || die "downloaded Go does not report $GO_VERSION"
  say "installed $GO_VERSION into $state/$GO_VERSION.$go_dist"
fi

# Isolate the build from the user's Go setup: own PATH entry, caches and
# GOPATH inside build/ipatool-bootstrap/; GOROOT is taken from the binary.
export PATH="$(dirname "$go_bin"):$PATH"
unset GOROOT GOOS GOARCH GOFLAGS CGO_ENABLED || true
export GOPATH="$state/gopath"
export GOMODCACHE="$state/gopath/pkg/mod"
export GOCACHE="$state/gocache"

# --- idempotency -----------------------------------------------------------
target_dir="$root/bin"
target="$target_dir/ipatool$suffix"
stamp="$state/stamp-$asset"
inputs="$( { echo "$GO_VERSION $asset"; sha256_of "$build_script"; for p in "$root"/packaging/patches/0*.patch; do sha256_of "$p"; done; } | tr '\n' ' ')"

has_markers() {
  local entry
  for entry in "${patch_markers[@]}"; do
    grep -a -F -q -e "${entry#*:}" "$1" || return 1
  done
}

if [[ $force -eq 0 && -f "$target" && -f "$stamp" ]] \
   && [[ "$(sed -n 1p "$stamp")" == "$inputs" ]] \
   && [[ "$(sed -n 2p "$stamp")" == "$(sha256_of "$target")" ]] \
   && has_markers "$target"; then
  say "bin/ipatool is already built from the same inputs (use --force to rebuild)"
else
  # --- build (pinned commit, patches, markers, archive) --------------------
  out="$state/out-$asset"
  rm -rf "$out"; mkdir -p "$out"
  say "building $asset with packaging/build-ipatool.sh"
  IPATOOL_OUT="$out" bash "$build_script" "$asset"
  archive="$out/ipatool-2.6.0-$asset.tar.gz"
  [[ -f "$archive" ]] || die "build did not produce $archive"
  ex="$(mktemp -d "$state/ex.XXXXXX")"
  tar -xzf "$archive" -C "$ex"
  built="$ex/bin/ipatool-2.6.0-$asset$suffix"
  [[ -f "$built" ]] || die "unexpected archive layout"
  mkdir -p "$target_dir"
  chmod 0755 "$built"
  mv -f "$built" "$target.tmp"
  mv -f "$target.tmp" "$target"
  rm -rf "$ex"
  # ipatool is MIT licensed; keep its license next to the binary (like fetch_ipatool.py).
  commit="$(sed -n 's/^commit="\([0-9a-f]\{40\}\)"$/\1/p' "$build_script")"
  if [[ -n "$commit" ]]; then
    curl -fsSL --proto '=https' --retry 3 -o "$target_dir/ipatool-LICENSE.txt.tmp" \
      "https://raw.githubusercontent.com/majd/ipatool/$commit/LICENSE" \
      && mv -f "$target_dir/ipatool-LICENSE.txt.tmp" "$target_dir/ipatool-LICENSE.txt" \
      || { rm -f "$target_dir/ipatool-LICENSE.txt.tmp"; echo "warning: could not fetch ipatool LICENSE" >&2; }
  fi
  printf '%s\n%s\n' "$inputs" "$(sha256_of "$target")" > "$stamp"
fi

# --- report ----------------------------------------------------------------
for entry in "${patch_markers[@]}"; do
  label="${entry%%:*}"; marker="${entry#*:}"
  grep -a -F -q -e "$marker" "$target" || die "bin/ipatool lacks the $label marker: $marker"
  echo "marker $label: OK ($marker)"
done
if [[ "$asset" == "$asset_os-$go_arch" ]]; then
  "$target" --version 2>&1 | grep -q '2.6.0' || die "bin/ipatool --version does not report 2.6.0"
  "$target" --help 2>&1 | grep -q -- '--keychain-passphrase-stdin' || die "bin/ipatool --help lacks --keychain-passphrase-stdin"
  echo "ipatool --version: $("$target" --version 2>&1)"
fi
echo "sha256 $(sha256_of "$target")  $target"
echo "patched ipatool ready: $target"
