"""Download the pinned ipatool build and place it next to the GUI bundle.

Official ipatool 2.6.0 follows only HTTP 302 during Apple ID login. Apple
sometimes answers 301, and that login fails before the password is checked.
These archives are AppRestore's build of majd/ipatool commit
``IPATOOL_SOURCE_COMMIT``, which follows 301, 302, 307 and 308. The reported
version stays 2.6.0. The SHA-256 values match the installers
(install-windows.ps1 / install-macos.sh); tests/test_frozen_runtime.py keeps
them in sync. ``resolve_tool("ipatool")`` looks next to ``sys.executable``
first, so a frozen app finds this copy.
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
# majd/ipatool commit that follows authentication redirects. Not a GitHub release.
IPATOOL_SOURCE_COMMIT = "cde7d00355e152714377b953ec57438626d3cb5a"
ARCHIVE_BASE = "https://github.com/J3ckJ/AppRestore/releases/download/ipatool-2.6.0-redirect"
ARCHIVE_SHA256 = {
    "windows-amd64": "639d9cd7f22cea2975fd8263b0a8cbd7a36e7ee742e4c3e3a910b85f05b4df14",
    "macos-arm64": "faf98ef8067f1ef4783123d00561b4ece10dc7419631eb9555d88a1da2f61f3f",
    "macos-amd64": "576bde4baf04365fcdea46eb7f3e5bb6ea143d0d9e53d6e2711136cc608aac84",
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
    url = f"{ARCHIVE_BASE}/ipatool-{IPATOOL_VERSION}-{asset}.tar.gz"
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
    license_url = (
        "https://raw.githubusercontent.com/majd/ipatool/"
        f"{IPATOOL_SOURCE_COMMIT}/LICENSE"
    )
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
