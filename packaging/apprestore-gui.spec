# -*- mode: python ; coding: utf-8 -*-
# PyInstaller spec for AppRestore GUI (Windows/macOS CI).

import sys
from pathlib import Path

block_cipher = None
root = Path(SPECPATH).resolve().parent
datas = [
    (str(root / "apprestore_gui" / "resources"), "apprestore_gui/resources"),
]

hidden = ["PySide6.QtCore", "PySide6.QtGui", "PySide6.QtWidgets", "apprestore_core", "apprestore_gui"]

a = Analysis(
    [str(root / "apprestore_gui" / "app.py")],
    pathex=[str(root)],
    binaries=[],
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
        icon=None,
        bundle_identifier="ru.j3ckj.apprestore",
        info_plist={"NSHighResolutionCapable": True},
    )
