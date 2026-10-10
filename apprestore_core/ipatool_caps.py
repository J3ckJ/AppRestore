"""What the installed ipatool can do, asked once per binary for the whole app.

Today only one question: does it take the keychain passphrase from stdin
(``--keychain-passphrase-stdin``, AppRestore's ipatool patch 0003)? The GUI's
KeychainRunner (downloads, installs) and the runner behind ``ipatool_api``
share this cached probe, so the app never disagrees with itself.

The probe is ``ipatool --help``: no secret is involved, and the child gets
``command.child_env()`` like every other ipatool process.
"""

from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass
import sys
import threading

from .command import child_env

STDIN_FLAG = "--keychain-passphrase-stdin"

_HELP: dict[str, str] = {}
_LOCK = threading.Lock()


def _creationflags() -> int:
    if sys.platform != "win32":
        return 0
    from .command import windows_creationflags

    return windows_creationflags()


def ipatool_help(binary: str) -> str:
    """``ipatool --help`` output (stdout + stderr), cached per binary path."""

    with _LOCK:
        if binary in _HELP:
            return _HELP[binary]
    try:
        completed = subprocess.run(  # noqa: S603 - fixed argv
            [binary, "--help"],
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=15,
            env=child_env(),
            creationflags=_creationflags(),
        )
        text = f"{completed.stdout or ''}{completed.stderr or ''}"
    except (OSError, subprocess.SubprocessError):
        text = ""
    with _LOCK:
        _HELP.setdefault(binary, text)
        return _HELP[binary]


def supports_passphrase_stdin(binary: str) -> bool:
    return STDIN_FLAG in ipatool_help(binary)


def reset_cache() -> None:
    """For tests and after AppRestore replaces its ipatool."""

    with _LOCK:
        _HELP.clear()
        _MARKS.clear()
        _VERSIONS.clear()


#: AppRestore's ipatool patches the license path depends on, and a string that
#: exists in the binary only with that patch (build-ipatool.sh checks the same).
PATCH_MARKERS: dict[str, bytes] = {
    "0001": b"appstore.CountryCodeFromStoreFront",  # auth info: account country
    "0002": b"--all cannot be combined with --page or --max-results",  # list-purchases --all
    "0003": b"keychain-passphrase-stdin",  # keychain passphrase via stdin
}
#: Patches without which no new license is taken (the gate's preflight).
LICENSE_PATCHES = ("0001", "0003")
_MARKS: dict[tuple[str, int, int], tuple[str, ...]] = {}


def _missing_markers(binary: str | None) -> tuple[str, ...]:
    """Patches (``"0001"``, ``"0002"``, ``"0003"``) whose marker is not in ``binary``.

    No binary at all = all of them. Cached per (path, size, mtime); reads the
    file in chunks, starts nothing.
    """

    if not binary:
        return tuple(PATCH_MARKERS)
    try:
        st = os.stat(binary)
    except OSError:
        return tuple(PATCH_MARKERS)
    key = (str(binary), st.st_size, int(st.st_mtime))
    with _LOCK:
        if key in _MARKS:
            return _MARKS[key]
    found: set[str] = set()
    tail = b""
    keep = max(len(m) for m in PATCH_MARKERS.values())
    try:
        with open(binary, "rb") as fh:
            while chunk := fh.read(1 << 20):
                block = tail + chunk
                found.update(name for name, mark in PATCH_MARKERS.items() if mark in block)
                tail = block[-keep:]
    except OSError:
        found = set()
    result = tuple(name for name in PATCH_MARKERS if name not in found)
    with _LOCK:
        _MARKS[key] = result
    return result


def missing_patches(binary: str | None) -> tuple[str, ...]:
    """License-relevant patches (0001, 0003) the binary lacks; () = licenses allowed."""

    return tuple(name for name in _missing_markers(binary) if name in LICENSE_PATCHES)


@dataclass(frozen=True)
class Capabilities:
    """Adapter with the names of Макс's ``IpatoolClient.capabilities()`` until his
    preflight lands in the engine (then this is replaced, not duplicated)."""

    country_info: bool  # 0001: auth info gives the account country
    list_all: bool  # 0002: list-purchases --all (without it: pages, slower)
    passphrase_stdin: bool  # 0003: keychain passphrase via stdin
    version: str = ""

    @property
    def can_acquire_license(self) -> bool:
        return self.country_info and self.passphrase_stdin

    @property
    def missing(self) -> tuple[str, ...]:
        flags = (("0001", self.country_info), ("0002", self.list_all), ("0003", self.passphrase_stdin))
        return tuple(name for name, ok in flags if not ok)


_VERSIONS: dict[str, str] = {}


def ipatool_version(binary: str) -> str:
    """«2.6.0» from ``ipatool --version`` (cached; "" when unknown)."""

    with _LOCK:
        if binary in _VERSIONS:
            return _VERSIONS[binary]
    text = ""
    try:
        completed = subprocess.run(  # noqa: S603 - fixed argv
            [binary, "--version"], stdin=subprocess.DEVNULL, capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=15, env=child_env(), creationflags=_creationflags(),
        )
        words = (completed.stdout or completed.stderr or "").split()
        text = words[-1] if words else ""
    except (OSError, subprocess.SubprocessError):
        text = ""
    with _LOCK:
        _VERSIONS.setdefault(binary, text)
        return _VERSIONS[binary]


def capabilities(binary: str | None) -> Capabilities:
    """Cached per binary (path, size, mtime) via the marker scan; no network."""

    missing = _missing_markers(binary)
    return Capabilities(
        country_info="0001" not in missing,
        list_all="0002" not in missing,
        passphrase_stdin="0003" not in missing,
        version=ipatool_version(binary) if binary and missing != tuple(PATCH_MARKERS) else "",
    )
