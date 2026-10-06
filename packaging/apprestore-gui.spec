# -*- mode: python ; coding: utf-8 -*-
# PyInstaller spec for AppRestore GUI (Windows/macOS CI).

import importlib.util
import sys
from pathlib import Path

from PyInstaller.utils.hooks import collect_all, collect_submodules, copy_metadata

block_cipher = None
root = Path(SPECPATH).resolve().parent
datas = [
    (str(root / "apprestore_gui" / "resources"), "apprestore_gui/resources"),
    (str(root / "LICENSE"), "."),
    (str(root / "THIRD_PARTY_NOTICES.md"), "."),
]
binaries = []

hidden = [
    "PySide6.QtCore",
    "PySide6.QtGui",
    "PySide6.QtWidgets",
    "PySide6.QtSvg",
    *collect_submodules("apprestore_core"),
    *collect_submodules("apprestore_gui"),
]

# importlib.metadata is used by the doctor (AppRestore runtime) and by
# pymobiledevice3 and its dependencies at import time.  PyInstaller does not
# ship *.dist-info unless asked, which produced "package metadata missing".
def _metadata_tree(*roots):
    """copy_metadata(recursive=True) that warns instead of failing on a gap."""

    from importlib import metadata as importlib_metadata

    from packaging.requirements import Requirement

    seen = set()
    collected = []
    stack = list(roots)
    while stack:
        name = stack.pop()
        key = name.lower().replace("_", "-")
        if key in seen:
            continue
        seen.add(key)
        try:
            collected += copy_metadata(name)
            requires = importlib_metadata.requires(name) or []
        except Exception as exc:  # noqa: BLE001
            print(f"WARNING: no package metadata for {name}: {exc}")
            continue
        for raw in requires:
            requirement = Requirement(raw)
            if requirement.marker and not requirement.marker.evaluate({"extra": ""}):
                continue
            stack.append(requirement.name)
    return collected


datas += _metadata_tree("apprestore", "pymobiledevice3")

# pymobiledevice3 loads its CLI subcommands and services lazily and ships
# data files (resources/*); static analysis alone misses them.
for package in ("pymobiledevice3",):
    pkg_datas, pkg_binaries, pkg_hidden = collect_all(package)
    datas += pkg_datas
    binaries += pkg_binaries
    hidden += pkg_hidden

if sys.platform == "win32":
    hidden += ["winpty"]
    # ConPTY loads OpenConsole.exe from the same folder as conpty.dll.
    # Without it, Windows uses conhost.exe, which crashes (0xc0000142)
    # when the window sends the keychain passphrase to ipatool.
    _winpty = importlib.util.find_spec("winpty")
    if _winpty is not None and _winpty.origin:
        _winpty_dir = Path(_winpty.origin).parent
        for _name in ("OpenConsole.exe", "winpty-agent.exe"):
            _tool = _winpty_dir / _name
            if _tool.is_file():
                binaries.append((str(_tool), "winpty"))

a = Analysis(
    [str(root / "apprestore_gui" / "app.py")],
    pathex=[str(root)],
    binaries=binaries,
    datas=datas,
    hiddenimports=hidden,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)
pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

def _app_version() -> str:
    import re as _re

    text = (root / "apprestore_core" / "__init__.py").read_text(encoding="utf-8")
    match = _re.search(r'__version__\s*=\s*"([^"]+)"', text)
    return match.group(1) if match else "0.0.0"


exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="AppRestore",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    icon=str(root / "packaging" / "icons" / ("AppRestore.icns" if sys.platform == "darwin" else "AppRestore.ico")),
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="AppRestore",
)

if sys.platform == "darwin":
    app = BUNDLE(
        coll,
        name="AppRestore.app",
        icon=str(root / "packaging" / "icons" / "AppRestore.icns"),
        bundle_identifier="ru.j3ckj.apprestore",
        version=_app_version(),
        info_plist={
            "NSHighResolutionCapable": True,
            "CFBundleDisplayName": "AppRestore",
            "CFBundleShortVersionString": _app_version(),
            "CFBundleVersion": _app_version(),
        },
    )
