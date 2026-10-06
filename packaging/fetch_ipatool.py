"""Download the pinned official ipatool and place it next to the GUI bundle.

Used by .github/workflows/gui-build.yml.  The SHA-256 values are the same
archive hashes the installers pin (install-windows.ps1 / install-macos.sh);
tests/test_frozen_runtime.py keeps them in sync.  ``resolve_tool("ipatool")``
looks next to ``sys.executable`` first, so a frozen app finds this copy.
"""

from __future__ import annotations

import argparse
import hashlib
import os
import platform
import stat
import sys
import tarfile
import tempfile
import urllib.request
from pathlib import Path

IPATOOL_VERSION = "2.6.0"
ARCHIVE_SHA256 = {
    "windows-amd64": "3ee48adc7c4aa84a8cc8ff9399d387c25f9b8593b2c212da29e966047a08ad21",
    "macos-arm64": "2f03bbe36def30943597164865991197c1f585a8dd19781542031c76e9f5346f",
    "macos-amd64": "6b9dcb890c9dad1961fd59827a1ac88687757a6f731a0079c00f443cd1a74e01",
}
MAX_BINARY_BYTES = 64 * 1024 * 1024


def current_asset() -> str:
    machine = platform.machine().lower()
    if sys.platform == "win32":
        return "windows-amd64"
    if sys.platform == "darwin":
        return "macos-arm64" if machine in ("arm64", "aarch64") else "macos-amd64"
    raise SystemExit(f"unsupported platform for the GUI bundle: {sys.platform}")


def fetch(asset: str, destinations: list[Path]) -> None:
    expected = ARCHIVE_SHA256[asset]
    url = (
        f"https://github.com/majd/ipatool/releases/download/v{IPATOOL_VERSION}/"
        f"ipatool-{IPATOOL_VERSION}-{asset}.tar.gz"
    )
    with tempfile.TemporaryDirectory() as tmp:
        archive = Path(tmp) / "ipatool.tar.gz"
        with urllib.request.urlopen(url, timeout=120) as response:  # noqa: S310 - pinned https URL
            archive.write_bytes(response.read())
        actual = hashlib.sha256(archive.read_bytes()).hexdigest()
        if actual != expected:
            raise SystemExit(f"ipatool SHA-256 mismatch: expected {expected}, got {actual}")
        suffix = ".exe" if asset.startswith("windows") else ""
        expected_leaf = f"ipatool-{IPATOOL_VERSION}-{asset}{suffix}"
        with tarfile.open(archive, "r:gz") as tar:
            members = [m for m in tar.getmembers() if m.isfile()]
            if len(members) != 1 or Path(members[0].name).name != expected_leaf:
                raise SystemExit(
                    f"unexpected ipatool archive layout: {[m.name for m in tar.getmembers()]}"
                )
            member = members[0]
            if not 0 < member.size <= MAX_BINARY_BYTES:
                raise SystemExit("ipatool binary size is out of range")
            source = tar.extractfile(member)
            assert source is not None
            data = source.read()
    name = f"ipatool{suffix}"
    for destination in destinations:
        destination.mkdir(parents=True, exist_ok=True)
        target = destination / name
        target.write_bytes(data)
        if not suffix:
            target.chmod(target.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
        print(f"ipatool {IPATOOL_VERSION} ({asset}) -> {target}")
    # ipatool is MIT licensed: ship its license text next to the binary.
    license_url = f"https://raw.githubusercontent.com/majd/ipatool/v{IPATOOL_VERSION}/LICENSE"
    with urllib.request.urlopen(license_url, timeout=60) as response:  # noqa: S310
        license_text = response.read()
    for destination in destinations:
        (destination / "ipatool-LICENSE.txt").write_bytes(license_text)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dest", action="append", required=True, type=Path)
    parser.add_argument("--asset", choices=sorted(ARCHIVE_SHA256), default=None)
    args = parser.parse_args()
    fetch(args.asset or current_asset(), args.dest)
    return 0


if __name__ == "__main__":
    os.umask(0o022)
    raise SystemExit(main())
