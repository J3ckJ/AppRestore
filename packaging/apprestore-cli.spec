# -*- mode: python ; coding: utf-8 -*-
# PyInstaller spec for the standalone AppRestore CLI (Windows/macOS release).
# A console onedir build: no Qt, no Python install needed on the target.

import sys
from pathlib import Path

from PyInstaller.utils.hooks import collect_all, collect_submodules, copy_metadata

root = Path(SPECPATH).resolve().parent
datas = [
    (str(root / "LICENSE"), "."),
    (str(root / "THIRD_PARTY_NOTICES.md"), "."),
]
binaries = []
hidden = [*collect_submodules("apprestore_core")]


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

for package in ("pymobiledevice3",):
    pkg_datas, pkg_binaries, pkg_hidden = collect_all(package)
    datas += pkg_datas
    binaries += pkg_binaries
    hidden += pkg_hidden

a = Analysis(
    [str(root / "packaging" / "apprestore_cli_entry.py")],
    pathex=[str(root)],
    binaries=binaries,
    datas=datas,
    hiddenimports=hidden,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    # The CLI never opens a window.
    excludes=["PySide6", "shiboken6", "apprestore_gui"],
    noarchive=False,
)
pyz = PYZ(a.pure, a.zipped_data)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="apprestore",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=True,
    icon=str(root / "packaging" / "icons" / "AppRestore.ico") if sys.platform == "win32" else None,
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
    name="AppRestore-CLI",
)
