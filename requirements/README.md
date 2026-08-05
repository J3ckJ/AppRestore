# Reproducible Python dependencies

`runtime.lock` is the universal, hash-locked runtime for CPython 3.10–3.13 on
Windows and macOS. `build.lock` pins the local-project build backend, while
`test.lock` pins pytest and its test-only dependencies. `wheel-build.lock` is a
separate, exact toolchain used only to reproduce the vendored `hexdump` wheel.
Installers consume `build.lock` and `runtime.lock` with
`--require-hashes --only-binary=:all:` and then install AppRestore itself with
`--no-deps --no-build-isolation`.

Regenerate the locks from the repository root with uv 0.12.1 and the commands
recorded in their headers. The `--exclude-newer` cutoff is intentional: changing
it is a dependency update and requires the complete platform matrix.

## Vendored hexdump wheel

`pymobiledevice3` depends on `hexdump==3.3`, for which PyPI only publishes a
source ZIP. To keep installers from executing a live build backend, this folder
contains a deterministic `py3-none-any` wheel built from that source.

- PyPI source: `hexdump-3.3.zip` (vendored at
  `requirements/sources/hexdump-3.3.zip` for offline rebuilds)
- source SHA-256: `d781a43b0c16ace3f9366aade73e8ad3a7bd5137d58f0b45ab2d3f54876f20db`
- wheel SHA-256: `2041be582c1021ec900d7496e204553b1f7bd0c650b6d0f294a6d413125d8acb`
- license declared by the source package: Public Domain
- wheel metadata generator: `setuptools 83.0.0`

The wheel is still verified by `runtime.lock`; `--find-links` does not bypass
hash checking.

The wheel is reproducible byte-for-byte from the pinned source on CPython
3.12.13 with `wheel-build.lock`. The recipe fixes the build epoch, verifies the
exact source and wheel member sets plus `RECORD`, then repacks the result as a
canonical uncompressed ZIP with normalized metadata line endings, a regenerated
`RECORD`, fixed timestamps and fixed permissions. Avoiding ZIP compression
makes the final bytes independent of the platform's zlib build.

Use a disposable CPython 3.12.13 environment:

```bash
python -m pip install --require-hashes --only-binary=:all: --no-deps \
  -r requirements/wheel-build.lock
python scripts/rebuild-vendored-wheel.py --check
```

`--check` is the release gate and does not modify the repository. For an
intentional dependency update, use `--write`, review the wheel diff, and update
the single `hexdump` hash in `runtime.lock` in the same change.
`--download` independently fetches and verifies the official PyPI source; the
default release gate is networkless and uses the byte-identical vendored ZIP.

## macOS Python bootstrap

The macOS installer does not require Homebrew. It downloads the pinned
`python-build-standalone` CPython 3.12.13 build `20260804` from its official
GitHub release and verifies the architecture-specific SHA-256 embedded in
`install-macos.sh` before extraction. Dependencies and AppRestore are installed
inside staging; the previous live runtime is moved only after all smoke checks
pass.
