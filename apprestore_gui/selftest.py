"""Headless diagnostics for a built bundle: ``AppRestore --self-test``.

Prints one JSON document (and optionally writes it to a file) describing
whether the frozen bundle can actually talk to an iPhone: package metadata,
pymobiledevice3 imports, a usbmux client, the subprocess dispatcher, ipatool,
and a real ``ipatool auth info`` on the hidden ConPTY/pty (no Apple ID).
No window is created.  Exit code 0 means every required check passed.
"""

from __future__ import annotations

import json
import os
import platform
import re
import sys
import tempfile
import traceback
from importlib import metadata as package_metadata
from pathlib import Path
from typing import Any, Callable


def _check(name: str, fn: Callable[[], Any], *, required: bool = True) -> dict[str, Any]:
    try:
        detail = fn()
        return {"name": name, "ok": True, "required": required, "detail": detail}
    except Exception as exc:  # noqa: BLE001 - diagnostics must never crash
        return {
            "name": name,
            "ok": False,
            "required": required,
            "error": f"{type(exc).__name__}: {exc}",
            "trace": traceback.format_exc(limit=4)[-1500:],
        }


def _metadata_version() -> dict[str, str]:
    from apprestore_core import __version__

    installed = package_metadata.version("apprestore")
    if installed != __version__:
        raise RuntimeError(f"metadata {installed} != runtime {__version__}")
    return {"apprestore": installed}


def _pmd3_imports() -> dict[str, str]:
    import pymobiledevice3.lockdown  # noqa: F401
    import pymobiledevice3.services.afc  # noqa: F401
    import pymobiledevice3.services.installation_proxy  # noqa: F401
    import pymobiledevice3.usbmux  # noqa: F401

    return {"pymobiledevice3": package_metadata.version("pymobiledevice3")}


def _pmd3_cli_import() -> str:
    from pymobiledevice3.__main__ import main  # noqa: F401

    return "pymobiledevice3.__main__.main importable"


def _usbmux_client() -> dict[str, Any]:
    """List devices in-process.  No usbmuxd / no device is fine, ImportError is not."""

    from apprestore_core.tools import AppRestoreTools

    try:
        udids = AppRestoreTools()._list_udids_in_process(timeout=15)
    except ImportError:
        raise
    except Exception as exc:  # noqa: BLE001
        name = type(exc).__name__
        # Connection refused / no daemon / no devices: the client itself works.
        return {"devices": [], "note": f"usbmux not reachable or no devices ({name}: {exc})"}
    return {"devices": udids}


def _dispatcher() -> dict[str, Any]:
    """Run the bundle as a pymobiledevice3 subprocess, as the core does when frozen."""

    from apprestore_core.command import Runner
    from apprestore_core.frozen import RUN_MODULE_FLAG, is_frozen

    if is_frozen():
        command = [sys.executable, RUN_MODULE_FLAG, "pymobiledevice3", "version"]
    else:
        command = [sys.executable, "-I", "-m", "pymobiledevice3", "version"]
    result = Runner().run(command, timeout=120)
    if result.returncode != 0:
        raise RuntimeError(
            f"exit {result.returncode}: {(result.stderr or result.stdout).strip()[:400]}"
        )
    return {"command": command[1:], "stdout": result.stdout.strip()[:200]}


def _ipatool() -> dict[str, str]:
    from apprestore_core.paths import resolve_tool
    from apprestore_core.tools import AppRestoreTools

    path = resolve_tool("ipatool")
    if not path:
        raise FileNotFoundError("ipatool not found next to the app or on PATH")
    ok, detail = AppRestoreTools()._ipatool_check(path)
    if not ok:
        raise RuntimeError(detail)
    return {"path": path, "detail": detail}


def _gui_resources() -> dict[str, Any]:
    from apprestore_gui.theme import resources_dir

    root = resources_dir()
    fonts = sorted(p.name for p in (root / "fonts").glob("*.ttf"))
    icons = sorted(p.name for p in (root / "icons").glob("*.svg"))
    if not fonts or not icons:
        raise FileNotFoundError(f"resources missing under {root}")
    return {"fonts": fonts, "icons": len(icons)}


def _app_icon() -> dict[str, Any]:
    from apprestore_gui.theme import resources_dir

    root = resources_dir() / "icons"
    found = sorted(p.name for p in root.glob("app-icon-*.png"))
    if not found:
        raise FileNotFoundError(f"app icon missing under {root}")
    return {"files": found}


def _winpty_import() -> str:
    from apprestore_gui.auth_pty import _load_pty_process

    pty = _load_pty_process()
    if pty is None:
        raise ImportError("winpty (pywinpty) is not bundled")
    return f"{pty.__module__}.{pty.__name__}"


def _windows_console_host() -> str:
    """OpenConsole.exe must sit next to conpty.dll.

    The hidden keychain terminal uses ConPTY. Without this host, Windows
    starts conhost.exe, which crashes and ipatool never opens (0xc0000142).
    """

    import winpty

    host = Path(winpty.__file__).resolve().parent / "OpenConsole.exe"
    if not host.is_file():
        raise FileNotFoundError(f"OpenConsole.exe is missing next to {host.parent}")
    return str(host)


def _posix_pty_import() -> str:
    import fcntl  # noqa: F401
    import pty  # noqa: F401
    import termios  # noqa: F401

    from apprestore_gui.auth_pty import _load_pty_process

    pty_process = _load_pty_process()
    if pty_process is None:
        raise ImportError("no pty for the Apple ID login")
    return f"{pty_process.__module__}.{pty_process.__name__}"


# Exit codes of a console host that could not start or died at once:
# STATUS_DLL_INIT_FAILED (the 06.10 bug) and STATUS_CONTROL_C_EXIT (what
# a build without OpenConsole.exe returns on the GitHub Windows runner).
_CONSOLE_HOST_DIED = {
    code: name
    for value, name in ((0xC0000142, "0xc0000142"), (0xC000013A, "0xc000013a"))
    for code in (value, value - (1 << 32))
}
_PATH_LIKE = re.compile(r"(?:[A-Za-z]:[\\/]|/(?:Users|home|var|private|tmp)/)[^\s\"']*")
_EMAIL_LIKE = re.compile(r"[^\s@\"']+@[^\s@\"']+")


def _redact(text: str) -> str:
    """Keep the ipatool message readable without paths, user names or e-mails."""

    text = _PATH_LIKE.sub("<path>", text)
    return _EMAIL_LIKE.sub("<email>", text)


def _ipatool_json(transcript: str) -> dict[str, Any] | None:
    """The JSON log line ``ipatool --format json`` printed, as a dict.

    A terminal may break a long line, so a second try drops line breaks
    (JSON itself never needs them).
    """

    from apprestore_gui.auth_pty import extract_json_document

    for text in (transcript, transcript.replace("\r", "").replace("\n", "")):
        document = extract_json_document(text)
        if not document:
            continue
        try:
            payload = json.loads(document)
        except ValueError:
            continue
        if isinstance(payload, dict):
            return payload
    return None


def _ipatool_auth_info_hidden_terminal() -> dict[str, Any]:
    """Run the real ``ipatool auth info --format json`` the way the window logs in.

    Windows: through pywinpty's ConPTY, which needs the bundled
    OpenConsole.exe (without it the console host dies and ipatool never
    runs). macOS: through the POSIX pty. No Apple ID is used: HOME and
    USERPROFILE point at an empty folder, ``--non-interactive`` makes
    ipatool fail instead of asking anything, and no passphrase is sent.

    The answer is judged by ipatool's JSON, not by words: a non-zero exit
    with ``{"level": "error", "error": "..."}`` means "not signed in", the
    expected and passing result; exit 0 with ``"success": true`` means a
    real session exists, whose data is never copied into the report.
    """

    from apprestore_core.paths import resolve_tool
    from apprestore_gui.auth_pty import run_pty_command

    ipatool = resolve_tool("ipatool")
    if not ipatool:
        raise FileNotFoundError("ipatool not found next to the app or on PATH")
    with tempfile.TemporaryDirectory(prefix="apprestore-selftest-") as home:
        env = {"HOME": home, "USERPROFILE": home}
        if sys.platform == "win32":
            env["HOMEDRIVE"], env["HOMEPATH"] = os.path.splitdrive(home)
        result = run_pty_command(
            [ipatool, "--non-interactive", "auth", "info", "--format", "json"],
            passphrase="",
            timeout=90,
            env=env,
        )
    code = result.returncode
    transcript = (result.stderr or result.stdout or "").strip()
    terminal = "ConPTY" if sys.platform == "win32" else "pty"
    if code in _CONSOLE_HOST_DIED:
        raise RuntimeError(
            f"ipatool did not start on the hidden {terminal}: exit {_CONSOLE_HOST_DIED[code]} "
            "(console host failed; is OpenConsole.exe bundled?)"
        )
    if code == 127:
        raise RuntimeError(f"could not start ipatool on the hidden {terminal}: {_redact(transcript)[:300]}")
    if code == 124:
        raise TimeoutError(f"ipatool auth info did not finish on the hidden {terminal}")
    payload = _ipatool_json(transcript)
    if payload is None:
        raise RuntimeError(
            f"ipatool exited {code:#x} on the hidden {terminal} without a readable JSON answer "
            f"({len(transcript)} chars of output)"
        )
    detail: dict[str, Any] = {"terminal": terminal, "exit": code}
    if code == 0:
        if payload.get("success") is not True:
            raise RuntimeError("ipatool auth info exited 0 without \"success\": true")
        # A real session exists on this machine. Never copy its account data.
        detail["session"] = "signed in (details withheld)"
        return detail
    error = payload.get("error")
    if payload.get("level") != "error" or not isinstance(error, str) or not error.strip():
        raise RuntimeError(
            f"ipatool exited {code} on the hidden {terminal} but its JSON is not an error "
            f"(keys: {sorted(payload)})"
        )
    detail["session"] = "not signed in (expected)"
    detail["ipatool_error"] = _redact(error)[:300]
    return detail


def _qt_import() -> str:
    from PySide6 import QtCore, QtSvg, QtWidgets  # noqa: F401

    return QtCore.qVersion()


def _doctor() -> list[dict[str, Any]]:
    from apprestore_core.tools import AppRestoreTools

    return [check.to_dict() for check in AppRestoreTools().doctor()]


def run_self_test(output: Path | None = None) -> int:
    from apprestore_core.frozen import is_frozen

    checks = [
        _check("metadata", _metadata_version),
        _check("pymobiledevice3 import", _pmd3_imports),
        _check("pymobiledevice3 cli import", _pmd3_cli_import),
        _check("usbmux client", _usbmux_client),
        _check("pymobiledevice3 subprocess dispatch", _dispatcher),
        _check("ipatool", _ipatool),
        _check("gui resources", _gui_resources),
        _check("app icon", _app_icon),
        _check("qt import", _qt_import),
        _check("doctor", _doctor, required=False),
    ]
    if sys.platform == "win32":
        checks.insert(-1, _check("winpty (Apple ID login)", _winpty_import))
        checks.insert(-1, _check("hidden terminal host", _windows_console_host))
    else:
        checks.insert(-1, _check("pty (Apple ID login)", _posix_pty_import))
    checks.insert(
        -1, _check("ipatool auth info on the hidden terminal", _ipatool_auth_info_hidden_terminal)
    )
    report = {
        "ok": all(c["ok"] or not c["required"] for c in checks),
        "frozen": is_frozen(),
        "executable": sys.executable,
        "python": platform.python_version(),
        "platform": platform.platform(),
        "checks": checks,
    }
    text = json.dumps(report, ensure_ascii=False, indent=2, default=str)
    if output is not None:
        output.write_text(text, encoding="utf-8")
    stream = sys.stdout
    if stream is not None:
        try:
            stream.write(text + "\n")
            stream.flush()
        except (OSError, ValueError):
            pass
    return 0 if report["ok"] else 1
