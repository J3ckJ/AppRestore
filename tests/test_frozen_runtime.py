"""Frozen (PyInstaller) runtime: device discovery must not spawn ``sys.executable -m``.

In a frozen bundle ``sys.executable`` is AppRestore.exe.  The old code ran
``AppRestore.exe -I -m pymobiledevice3 usbmux list``: the GUI's argparse
rejected ``-I`` and the GUI reported "no iPhone" although the phone was
connected.
"""

from __future__ import annotations

import importlib.machinery
import sys
import types
from dataclasses import dataclass

import pytest

from apprestore_core import frozen
from apprestore_core.command import windows_creationflags
from apprestore_core.models import CommandResult
from apprestore_core.tools import AppRestoreTools

FAKE_EXE = r"C:\Users\someone\Downloads\AppRestore-windows\AppRestore.exe"


@dataclass
class _FakeMuxDevice:
    devid: int
    serial: str
    connection_type: str

    @property
    def is_usb(self) -> bool:
        return self.connection_type == "USB"


class _FakeLockdown:
    def __init__(self, serial: str) -> None:
        self.all_values = {
            "DeviceName": "Eugene's iPhone",
            "ProductVersion": "18.2",
            "UniqueDeviceID": serial,
        }

    async def __aenter__(self) -> "_FakeLockdown":
        return self

    async def __aexit__(self, *exc: object) -> None:
        return None


class _ExplodingRunner:
    def __init__(self) -> None:
        self.calls: list[tuple[str, ...]] = []

    def run(self, args, **kwargs) -> CommandResult:  # noqa: ANN001
        command = tuple(str(a) for a in args)
        self.calls.append(command)
        raise AssertionError(f"device discovery must not spawn a process: {command}")


def _package(name: str) -> types.ModuleType:
    module = types.ModuleType(name)
    module.__spec__ = importlib.machinery.ModuleSpec(name, loader=None, is_package=True)
    module.__path__ = []  # type: ignore[attr-defined]
    return module


@pytest.fixture
def fake_pmd3(monkeypatch: pytest.MonkeyPatch) -> dict[str, list]:
    calls: dict[str, list] = {"list_devices": [], "lockdown": []}

    async def list_devices(usbmux_address=None):  # noqa: ANN001
        calls["list_devices"].append(usbmux_address)
        return [
            _FakeMuxDevice(1, "00008110-000A1B2C3D4E5F60", "USB"),
            _FakeMuxDevice(2, "00008110-000A1B2C3D4E5F60", "USB"),
            _FakeMuxDevice(3, "WIFI-ONLY-UDID", "Network"),
        ]

    async def create_using_usbmux(serial=None, connection_type=None, **kwargs):  # noqa: ANN001
        calls["lockdown"].append((serial, connection_type))
        return _FakeLockdown(serial)

    root = _package("pymobiledevice3")
    usbmux = types.ModuleType("pymobiledevice3.usbmux")
    usbmux.list_devices = list_devices  # type: ignore[attr-defined]
    lockdown = types.ModuleType("pymobiledevice3.lockdown")
    lockdown.create_using_usbmux = create_using_usbmux  # type: ignore[attr-defined]
    main_mod = types.ModuleType("pymobiledevice3.__main__")

    def fake_main() -> None:
        calls.setdefault("main_argv", []).append(list(sys.argv))
        raise SystemExit(0)

    main_mod.main = fake_main  # type: ignore[attr-defined]
    root.usbmux = usbmux  # type: ignore[attr-defined]
    root.lockdown = lockdown  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "pymobiledevice3", root)
    monkeypatch.setitem(sys.modules, "pymobiledevice3.usbmux", usbmux)
    monkeypatch.setitem(sys.modules, "pymobiledevice3.lockdown", lockdown)
    monkeypatch.setitem(sys.modules, "pymobiledevice3.__main__", main_mod)
    return calls


@pytest.fixture
def frozen_windows(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", FAKE_EXE)
    monkeypatch.setattr("apprestore_core.tools.platform.system", lambda: "Windows")


def test_frozen_list_udids_is_in_process(frozen_windows, fake_pmd3) -> None:
    runner = _ExplodingRunner()
    tools = AppRestoreTools(runner=runner)  # type: ignore[arg-type]

    assert tools.list_udids() == ["00008110-000A1B2C3D4E5F60"]
    assert runner.calls == []
    assert fake_pmd3["list_devices"] == [None]


def test_frozen_device_info_is_in_process(frozen_windows, fake_pmd3) -> None:
    runner = _ExplodingRunner()
    tools = AppRestoreTools(runner=runner)  # type: ignore[arg-type]

    device = tools.device_info("00008110-000A1B2C3D4E5F60")
    assert device.name == "Eugene's iPhone"
    assert device.ios_version == "18.2"
    assert runner.calls == []
    assert fake_pmd3["lockdown"] == [("00008110-000A1B2C3D4E5F60", "USB")]


def test_frozen_service_devices_never_calls_sys_executable_m(frozen_windows, fake_pmd3) -> None:
    from apprestore_core.service import AppRestoreService

    runner = _ExplodingRunner()
    service = AppRestoreService.__new__(AppRestoreService)
    service.tools = AppRestoreTools(runner=runner)  # type: ignore[arg-type]

    devices = service.devices()
    assert [d.udid for d in devices] == ["00008110-000A1B2C3D4E5F60"]
    assert devices[0].name == "Eugene's iPhone"
    assert not any("-m" in call for call in runner.calls)


def test_frozen_pymobiledevice3_command_uses_dispatcher(frozen_windows, fake_pmd3) -> None:
    command = AppRestoreTools()._pymobiledevice3_cmd("apps", "list")
    assert command[0] == FAKE_EXE
    assert command[1] == frozen.RUN_MODULE_FLAG
    assert command[2:] == ["pymobiledevice3", "apps", "list"]
    assert "-m" not in command
    assert "-I" not in command


def test_unfrozen_command_still_uses_interpreter(monkeypatch, fake_pmd3) -> None:
    monkeypatch.delattr(sys, "frozen", raising=False)
    monkeypatch.setattr("apprestore_core.tools.platform.system", lambda: "Windows")
    command = AppRestoreTools()._pymobiledevice3_cmd("usbmux", "list")
    assert command[:4] == [sys.executable, "-I", "-m", "pymobiledevice3"]


def test_frozen_doctor_detail_names_bundle(frozen_windows, fake_pmd3, monkeypatch) -> None:
    monkeypatch.setattr(
        "apprestore_core.tools.package_metadata.version", lambda name: "10.1.0"
    )
    tools = AppRestoreTools()
    monkeypatch.setattr(tools, "_ipatool_check", lambda exe: (True, "ok"))
    monkeypatch.setattr(tools, "_apple_mobile_device_service", lambda: (True, "running"))
    monkeypatch.setattr(tools, "_apple_usbmux_port", lambda: (True, "listening"))
    checks = {c.name: c for c in tools.doctor()}
    detail = checks["pymobiledevice3"].detail
    assert "bundled pymobiledevice3 10.1.0" in detail
    assert " -m " not in detail


@pytest.mark.parametrize(
    "argv, expected",
    [
        ([frozen.RUN_MODULE_FLAG, "pymobiledevice3", "usbmux", "list"], ("pymobiledevice3", ["usbmux", "list"])),
        (["-I", "-m", "pymobiledevice3", "version"], ("pymobiledevice3", ["version"])),
        (["-m", "pymobiledevice3"], ("pymobiledevice3", [])),
        (["--demo"], None),
        ([], None),
        (["--self-test", "--output", "x.json"], None),
    ],
)
def test_parse_dispatch(argv, expected) -> None:
    assert frozen.parse_dispatch(argv) == expected


def test_dispatch_runs_pymobiledevice3_main(fake_pmd3, monkeypatch) -> None:
    monkeypatch.setattr(sys, "argv", ["AppRestore.exe"])
    code = frozen.maybe_dispatch([frozen.RUN_MODULE_FLAG, "pymobiledevice3", "usbmux", "list"])
    assert code == 0
    assert fake_pmd3["main_argv"] == [["pymobiledevice3", "usbmux", "list"]]


def test_dispatch_refuses_arbitrary_modules(capsys) -> None:
    assert frozen.maybe_dispatch(["-m", "os"]) == 2
    assert "refusing" in capsys.readouterr().err
    with pytest.raises(ValueError):
        frozen.frozen_module_command("os")


def test_gui_entry_dispatches_before_qt(fake_pmd3, monkeypatch) -> None:
    from apprestore_gui import app

    monkeypatch.setattr(sys, "argv", ["AppRestore.exe"])
    assert app.main(["-I", "-m", "pymobiledevice3", "version"]) == 0
    assert fake_pmd3["main_argv"] == [["pymobiledevice3", "version"]]


def test_windowed_parent_hides_child_consoles() -> None:
    base = windows_creationflags(no_console=False)
    hidden = windows_creationflags(no_console=True)
    assert base & 0x00000200  # CREATE_NEW_PROCESS_GROUP
    assert not base & 0x08000000
    assert hidden & 0x08000000  # CREATE_NO_WINDOW
    assert hidden & 0x00000200


def test_winpty_import_name() -> None:
    import ast
    from pathlib import Path

    source = (Path(__file__).parents[1] / "apprestore_gui" / "auth_pty.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    imported = {
        node.module
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module
    }
    assert "winpty" in imported
    assert "pywinpty" not in imported


def test_bundle_ipatool_pins_match_installers() -> None:
    import importlib.util
    from pathlib import Path

    from apprestore_core.tools import IPATOOL_VERSION, IPATOOL_WINDOWS_AMD64_ARCHIVE_SHA256

    root = Path(__file__).parents[1]
    spec = importlib.util.spec_from_file_location("fetch_ipatool", root / "packaging" / "fetch_ipatool.py")
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)

    assert module.IPATOOL_VERSION == IPATOOL_VERSION
    assert module.ARCHIVE_SHA256["windows-amd64"] == IPATOOL_WINDOWS_AMD64_ARCHIVE_SHA256
    mac = (root / "install-macos.sh").read_text(encoding="utf-8")
    assert f'IPATOOL_MACOS_ARM64_SHA256="{module.ARCHIVE_SHA256["macos-arm64"]}"' in mac
    assert f'IPATOOL_MACOS_AMD64_SHA256="{module.ARCHIVE_SHA256["macos-amd64"]}"' in mac


def test_windowed_app_without_console_gets_null_streams(monkeypatch):
    """Double-clicked AppRestore.exe has sys.stdout=None; imports must not crash."""

    from apprestore_core import frozen

    monkeypatch.setattr(sys, "stdout", None)
    monkeypatch.setattr(sys, "__stdout__", None)
    monkeypatch.setattr(sys, "stderr", None)
    fixed = frozen.ensure_std_streams()
    assert {"stdout", "stderr"} <= set(fixed)
    assert sys.stdout.fileno() >= 0
    assert sys.__stdout__ is sys.stdout
    sys.stdout.write("ignored")
    assert frozen.ensure_std_streams() == []


def test_gui_entry_repairs_streams_before_dispatch(monkeypatch):
    from apprestore_gui import app

    seen = {}

    def fake_dispatch(argv):
        seen["stdout"] = sys.stdout
        return 0

    monkeypatch.setattr(sys, "stdout", None)
    monkeypatch.setattr("apprestore_core.frozen.maybe_dispatch", fake_dispatch)
    assert app.main(["--apprestore-run-module", "pymobiledevice3", "version"]) == 0
    assert seen["stdout"] is not None
