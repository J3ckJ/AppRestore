# -*- mode: python ; coding: utf-8 -*-
# PyInstaller spec for the UI engine benchmark.
#   BENCH_TARGET=quick|widgets|sidecar   BENCH_ONEFILE=1 (sidecar only)
# Collects pymobiledevice3 the same way packaging/apprestore-gui.spec does,
# so archive sizes include the real core payload.

import os
from pathlib import Path

from PyInstaller.utils.hooks import collect_all, collect_submodules, copy_metadata

here = Path(SPECPATH).resolve()
root = here.parent.parent
target = os.environ.get("BENCH_TARGET", "quick")
onefile = os.environ.get("BENCH_ONEFILE") == "1"

entry = {
    "quick": here / "qt" / "quick_app.py",
    "widgets": here / "qt" / "widgets_app.py",
    "sidecar": here / "sidecar.py",
}[target]
name = {"quick": "bench-quick", "widgets": "bench-widgets", "sidecar": "apprestore-core"}[target]

datas = []
binaries = []
hidden = [*collect_submodules("apprestore_core")]
excludes = []
if target == "quick":
    datas.append((str(here / "qt" / "bench.qml"), "."))
    hidden += ["PySide6.QtQml", "PySide6.QtQuick", "PySide6.QtQuickControls2"]
elif target == "widgets":
    hidden += ["PySide6.QtWidgets"]
else:
    excludes += ["PySide6", "shiboken6", "tkinter"]

for dist in ("apprestore", "pymobiledevice3"):
    try:
        datas += copy_metadata(dist, recursive=True)
    except Exception as exc:  # noqa: BLE001
        print(f"WARNING: metadata for {dist}: {exc}")
pkg_datas, pkg_binaries, pkg_hidden = collect_all("pymobiledevice3")
datas += pkg_datas
binaries += pkg_binaries
hidden += pkg_hidden

a = Analysis(
    [str(entry)],
    pathex=[str(root), str(here)],
    binaries=binaries,
    datas=datas,
    hiddenimports=hidden,
    excludes=excludes,
    noarchive=False,
)
pyz = PYZ(a.pure, a.zipped_data)
console = target == "sidecar"
if onefile:
    exe = EXE(pyz, a.scripts, a.binaries, a.datas, [], name=name, console=console, upx=False)
else:
    exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name=name, console=console, upx=False)
    coll = COLLECT(exe, a.binaries, a.datas, name=name, upx=False)
