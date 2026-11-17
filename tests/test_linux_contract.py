"""Linux paths, frozen subprocesses and update/archive round trips."""
from __future__ import annotations

import importlib.util
import os
import sys
from pathlib import Path

import pytest

from apprestore_core import paths
from apprestore_core.tools import AppRestoreTools


@pytest.fixture
def linux_home(tmp_path, monkeypatch):
    monkeypatch.setattr(paths.platform, "system", lambda: "Linux")
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    for name in ("XDG_CACHE_HOME", "XDG_DATA_HOME", "APPRESTORE_DATA_DIR", "APPRESTORE_CACHE_DIR", "APPRESTORE_IPA_DIR"):
        monkeypatch.delenv(name, raising=False)
    return tmp_path


def test_linux_paths_use_xdg_and_ignore_windows_imazing(linux_home, monkeypatch):
    assert paths.cache_dir() == linux_home / ".cache/AppRestore"
    assert paths.data_dir() == linux_home / ".local/share/AppRestore"
    assert paths.ipa_library_dir() == paths.data_dir() / "ipas"
    assert paths.imazing_apps_dirs() == []
    assert paths.imazing_catalog_candidates() == []
    monkeypatch.setenv("XDG_CACHE_HOME", str(linux_home / "cache"))
    monkeypatch.setenv("XDG_DATA_HOME", str(linux_home / "data"))
    assert paths.cache_dir() == linux_home / "cache/AppRestore"
    assert paths.data_dir() == linux_home / "data/AppRestore"
    monkeypatch.setenv("XDG_CACHE_HOME", "relative")
    monkeypatch.setenv("XDG_DATA_HOME", "relative")
    assert paths.cache_dir() == linux_home / ".cache/AppRestore"
    assert paths.data_dir() == linux_home / ".local/share/AppRestore"


def test_linux_frozen_dispatch_does_not_need_pmd_launcher(linux_home, monkeypatch):
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(importlib.util, "find_spec", lambda _: object())
    monkeypatch.setattr("apprestore_core.tools.resolve_tool", lambda _: None)
    command = AppRestoreTools()._pymobiledevice3_cmd("version")
    assert command == [sys.executable, "--apprestore-run-module", "pymobiledevice3", "version"]


def test_linux_gui_asset_rejects_other_cpus():
    updater = pytest.importorskip("apprestore_gui.updater")
    assert updater.asset_name_for("linux", "x86_64") == "AppRestore-GUI-Linux-x86_64.zip"
    assert updater.asset_name_for("linux", "aarch64") is None
    assert updater.asset_name_for("linux", "i386") is None


def test_linux_archive_update_round_trip(tmp_path, monkeypatch):
    updater = pytest.importorskip("apprestore_gui.updater")
    import zipfile

    archive = tmp_path / "update.zip"
    with zipfile.ZipFile(archive, "w") as z:
        for name in ("AppRestore", "ipatool"):
            entry = zipfile.ZipInfo(f"AppRestore/{name}")
            entry.external_attr = 0o100755 << 16
            z.writestr(entry, b"#!/bin/sh\nexit 0\n")
    target = tmp_path / "installed" / "AppRestore"
    target.mkdir(parents=True)
    staged = updater.stage_update(archive, None, platform="linux", executable=str(target / "AppRestore"))
    assert staged.target_app == target
    assert staged.new_app.name == "AppRestore"
    assert os.access(staged.new_app / "ipatool", os.X_OK)
    calls = []
    monkeypatch.setattr(updater, "update_log_path", lambda: tmp_path / "update.log")
    command = updater.launch_swap(staged, platform="linux", popen=lambda *a, **kw: calls.append(a))
    assert command[-1] == updater.shlex_quote(str(target / "AppRestore"))
    assert calls


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX swap script")
@pytest.mark.parametrize("health_exit", [0, 1])
def test_linux_swap_health_check_and_rollback(tmp_path, health_exit):
    import subprocess

    updater = pytest.importorskip("apprestore_gui.updater")
    target = tmp_path / "installed app"
    new = tmp_path / "staging" / "AppRestore"
    target.mkdir()
    new.mkdir(parents=True)
    (target / "old.txt").write_text("previous version")
    binary = new / "AppRestore"
    binary.write_text(f"#!/bin/sh\nexit {health_exit}\n")
    binary.chmod(0o755)
    script = tmp_path / "swap.sh"
    script.write_text(updater.POSIX_SWAP_SCRIPT)
    result = subprocess.run([
        "/bin/sh", str(script), "99999999", str(target), str(new),
        str(new.parent), str(tmp_path / "update.log"), "",
    ], timeout=10)
    assert result.returncode == health_exit
    assert (target / "old.txt").exists() == bool(health_exit)
    assert (target / "AppRestore").exists() == (not health_exit)
