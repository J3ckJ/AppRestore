"""Headless diagnostics for a built bundle: ``AppRestore --self-test``.

Prints one JSON document (and optionally writes it to a file) describing
whether the frozen bundle can actually talk to an iPhone: package metadata,
pymobiledevice3 imports, a usbmux client, the subprocess dispatcher, ipatool.
No window is created.  Exit code 0 means every required check passed.
"""

from __future__ import annotations

import json
import platform
import sys
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
