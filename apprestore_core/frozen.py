"""Helpers for running AppRestore from a frozen (PyInstaller) bundle.

In a frozen build ``sys.executable`` is the bundled application itself
(``AppRestore.exe`` / ``AppRestore.app/Contents/MacOS/AppRestore``), not a
Python interpreter.  ``[sys.executable, "-m", "pymobiledevice3", ...]``
therefore starts the GUI again instead of pymobiledevice3.  The bundle
handles a narrow, explicit dispatch flag before any GUI code runs, and the
core prefers in-process pymobiledevice3 calls when frozen.
"""

from __future__ import annotations

import os
import sys
from collections.abc import Sequence

RUN_MODULE_FLAG = "--apprestore-run-module"

# Only modules the bundle is expected to expose as a subprocess.  Never turn
# the dispatcher into a generic "run arbitrary module" entry point.
DISPATCHABLE_MODULES = frozenset({"pymobiledevice3"})

_PYTHON_FLAGS = frozenset({"-I", "-u", "-E", "-s", "-S", "-B", "-X", "utf8"})


def is_frozen() -> bool:
    return bool(getattr(sys, "frozen", False))


def frozen_module_command(module: str, *parts: str) -> list[str]:
    """Command that makes a frozen bundle run ``module`` as a CLI."""

    if module not in DISPATCHABLE_MODULES:
        raise ValueError(f"module is not dispatchable from the bundle: {module}")
    return [sys.executable, RUN_MODULE_FLAG, module, *parts]


def parse_dispatch(argv: Sequence[str]) -> tuple[str, list[str]] | None:
    """Return ``(module, args)`` when argv asks the bundle to run a module.

    Accepts the explicit ``--apprestore-run-module MODULE ...`` form and, as a
    safety net for any remaining interpreter-style call, ``[-I] -m MODULE ...``.
    """

    args = list(argv)
    if len(args) >= 2 and args[0] == RUN_MODULE_FLAG:
        return args[1], args[2:]
    index = 0
    while index < len(args) and args[index] in _PYTHON_FLAGS:
        index += 1
    if index + 1 < len(args) and args[index] == "-m":
        return args[index + 1], args[index + 2 :]
    return None


def _ensure_stdio() -> None:
    # A windowed (no console) bundle started without redirected handles has
    # sys.stdout/sys.stderr set to None; CLI code would crash on print().
    devnull = None
    for name in ("stdout", "stderr"):
        if getattr(sys, name) is None:
            if devnull is None:
                devnull = open(os.devnull, "w", encoding="utf-8")  # noqa: SIM115
            setattr(sys, name, devnull)


def run_module(module: str, args: Sequence[str]) -> int:
    if module not in DISPATCHABLE_MODULES:
        _ensure_stdio()
        print(f"AppRestore: refusing to run module {module!r}", file=sys.stderr)
        return 2
    _ensure_stdio()
    sys.argv = [module, *args]
    try:
        if module == "pymobiledevice3":
            from pymobiledevice3.__main__ import main as pmd3_main

            pmd3_main()
    except SystemExit as exc:
        code = exc.code
        if code is None:
            return 0
        if isinstance(code, int):
            return code
        print(code, file=sys.stderr)
        return 1
    return 0


def maybe_dispatch(argv: Sequence[str] | None = None) -> int | None:
    """Run a dispatched module and return its exit code, or ``None``."""

    if argv is None:
        argv = sys.argv[1:]
    parsed = parse_dispatch(argv)
    if parsed is None:
        return None
    module, args = parsed
    return run_module(module, args)


def ensure_std_streams() -> list[str]:
    """Give a windowed (no console) frozen app real stdio objects.

    A GUI-subsystem AppRestore.exe started by double click, by Explorer or by
    another program without inherited handles gets ``sys.stdout`` (and
    friends) set to ``None``.  Libraries such as pymobiledevice3's CLI call
    ``sys.stdout.fileno()`` at import time and crash.  Point missing streams
    at the null device instead.  Returns the names that were replaced.
    """

    import os

    fixed: list[str] = []
    for name, mode in (("stdin", "r"), ("stdout", "w"), ("stderr", "w")):
        if getattr(sys, name, None) is None:
            stream = open(os.devnull, mode, encoding="utf-8")  # noqa: SIM115 - lives for the process
            setattr(sys, name, stream)
            if getattr(sys, f"__{name}__", None) is None:
                setattr(sys, f"__{name}__", stream)
            fixed.append(name)
    return fixed
