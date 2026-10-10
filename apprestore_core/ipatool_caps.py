"""What the installed ipatool can do, asked once per binary for the whole app.

Today only one question: does it take the keychain passphrase from stdin
(``--keychain-passphrase-stdin``, AppRestore's ipatool patch 0003)? The GUI's
KeychainRunner (downloads, installs) and the runner behind ``ipatool_api``
share this cached probe, so the app never disagrees with itself.

The probe is ``ipatool --help``: no secret is involved, and the child gets
``command.child_env()`` like every other ipatool process.
"""

from __future__ import annotations

import subprocess
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
