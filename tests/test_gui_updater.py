from __future__ import annotations

import hashlib
import io
import json
import os
import subprocess
import sys
import time
import zipfile
from pathlib import Path

import pytest

from apprestore_gui import updater

DL = "https://github.com/J3ckJ/AppRestore/releases/download/v0.3.0/"


def _release(tag="v0.3.0", assets=None, body="* Новое окно"):
    if assets is None:
        assets = [
            {"name": "AppRestore-GUI-Windows.zip", "browser_download_url": DL + "AppRestore-GUI-Windows.zip", "size": 10},
            {"name": "AppRestore-GUI-macOS.zip", "browser_download_url": DL + "AppRestore-GUI-macOS.zip", "size": 11},
            {"name": "SHA256SUMS.txt", "browser_download_url": DL + "SHA256SUMS.txt", "size": 1},
        ]
    return {"tag_name": tag, "body": body, "html_url": "https://github.com/J3ckJ/AppRestore/releases/tag/" + tag,
            "draft": False, "prerelease": False, "assets": assets}


def _fetcher(payload):
    calls = []

    def fetch(url):
        calls.append(url)
        assert url == updater.LATEST_URL
        return json.dumps(payload).encode()

    fetch.calls = calls
    return fetch


def test_parse_version_and_compare():
    assert updater.parse_version("v0.2.4") == (0, 2, 4)
    assert updater.parse_version("0.10.0") > updater.parse_version("0.9.9")
    with pytest.raises(updater.UpdateError):
        updater.parse_version("latest")


@pytest.mark.parametrize("platform,name", [("win32", "AppRestore-GUI-Windows.zip"), ("darwin", "AppRestore-GUI-macOS.zip")])
def test_check_finds_newer_release_for_platform(platform, name):
    fetch = _fetcher(_release())
    info = updater.check_for_update("0.2.4", fetch=fetch, platform=platform)
    assert fetch.calls == [updater.LATEST_URL]
    assert info.newer and info.installable
    assert info.latest == "0.3.0"
    assert info.asset_name == name
    assert info.asset_url == DL + name
    assert info.checksums_url == DL + "SHA256SUMS.txt"
    assert "Новое окно" in info.notes


@pytest.mark.parametrize("tag", ["v0.2.4", "v0.2.3"])
def test_same_or_older_release_is_not_offered(tag):
    info = updater.check_for_update("0.2.4", fetch=_fetcher(_release(tag=tag)), platform="win32")
    assert not info.newer
    assert not info.installable


def test_newer_release_without_gui_asset_is_not_installable():
    assets = [{"name": "SHA256SUMS.txt", "browser_download_url": DL + "SHA256SUMS.txt"}]
    info = updater.check_for_update("0.2.4", fetch=_fetcher(_release(assets=assets)), platform="win32")
    assert info.newer and not info.installable
    assert info.page_url.endswith("/v0.3.0")


def test_foreign_download_urls_are_ignored():
    assets = [
        {"name": "AppRestore-GUI-Windows.zip", "browser_download_url": "https://evil.example/AppRestore-GUI-Windows.zip"},
        {"name": "SHA256SUMS.txt", "browser_download_url": DL + "SHA256SUMS.txt"},
    ]
    info = updater.check_for_update("0.2.4", fetch=_fetcher(_release(assets=assets)), platform="win32")
    assert info.asset_url is None and not info.installable


def test_network_error_is_user_facing():
    def broken(url):
        raise OSError("offline")

    with pytest.raises(updater.UpdateError, match="GitHub"):
        updater.check_for_update("0.2.4", fetch=broken, platform="win32")


def test_parse_checksums_formats():
    a, b = "a" * 64, "B" * 64
    sums = updater.parse_checksums(f"{a}  AppRestore-GUI-Windows.zip\n{b} *AppRestore-GUI-macOS.zip\njunk\n")
    assert sums == {"AppRestore-GUI-Windows.zip": a, "AppRestore-GUI-macOS.zip": b.lower()}


def _info_for(data: bytes, platform="win32", digest=None):
    name = updater.ASSET_BY_PLATFORM[platform]
    digest = digest or hashlib.sha256(data).hexdigest()
    info = updater.check_for_update("0.2.4", fetch=_fetcher(_release()), platform=platform)
    sums = f"{digest}  {name}\n{'0' * 64}  apprestore-0.3.0.tar.gz\n".encode()

    def fetch(url):
        assert url == DL + "SHA256SUMS.txt"
        return sums

    def opener(url):
        assert url == DL + name
        return io.BytesIO(data)

    return info, fetch, opener


def test_download_verifies_sha256(tmp_path):
    data = b"zip-bytes" * 1000
    info, fetch, opener = _info_for(data)
    seen = []
    path = updater.download_update(info, tmp_path, fetch=fetch, opener=opener, progress=lambda d, t: seen.append(d))
    assert path.read_bytes() == data
    assert seen and seen[-1] == len(data)


def test_download_rejects_wrong_sha256(tmp_path):
    info, fetch, opener = _info_for(b"tampered", digest="f" * 64)
    with pytest.raises(updater.UpdateError, match="контрольная сумма"):
        updater.download_update(info, tmp_path, fetch=fetch, opener=opener)
    assert list(tmp_path.iterdir()) == []


def _zip(path: Path, files: dict[str, bytes]) -> Path:
    with zipfile.ZipFile(path, "w") as archive:
        for name, data in files.items():
            info = zipfile.ZipInfo(name)
            info.external_attr = (0o755 << 16)
            archive.writestr(info, data)
    return path


def _frozen_layout(tmp_path: Path, platform: str):
    root = tmp_path / "Apps"
    if platform == "win32":
        app = root / "AppRestore"
        (app).mkdir(parents=True)
        (app / "AppRestore.exe").write_text("old")
        exe = app / "AppRestore.exe"
    else:
        app = root / "AppRestore.app"
        (app / "Contents" / "MacOS").mkdir(parents=True)
        exe = app / "Contents" / "MacOS" / "AppRestore"
        exe.write_text("old")
    return app, exe


def test_stage_update_windows_layout(tmp_path):
    app, exe = _frozen_layout(tmp_path, "win32")
    archive = _zip(tmp_path / "u.zip", {"AppRestore/AppRestore.exe": b"new", "AppRestore/_internal/x.dll": b"x"})
    info = updater.check_for_update("0.2.4", fetch=_fetcher(_release()), platform="win32")
    staged = updater.stage_update(archive, info, platform="win32", executable=str(exe))
    assert staged.target_app == app
    assert staged.staging_dir.parent == app.parent
    assert (staged.new_app / "AppRestore.exe").read_bytes() == b"new"


def test_stage_update_rejects_zip_slip(tmp_path):
    _, exe = _frozen_layout(tmp_path, "win32")
    archive = _zip(tmp_path / "u.zip", {"../evil.txt": b"x", "AppRestore/AppRestore.exe": b"new"})
    info = updater.check_for_update("0.2.4", fetch=_fetcher(_release()), platform="win32")
    with pytest.raises(updater.UpdateError, match="небезопасный"):
        updater.stage_update(archive, info, platform="win32", executable=str(exe))
    assert not (tmp_path / "evil.txt").exists()
    assert not list((tmp_path / "Apps").glob(".AppRestore-update-*"))


def test_stage_update_rejects_wrong_layout(tmp_path):
    _, exe = _frozen_layout(tmp_path, "darwin")
    archive = _zip(tmp_path / "u.zip", {"Something/else": b"x"})
    info = updater.check_for_update("0.2.4", fetch=_fetcher(_release()), platform="darwin")
    with pytest.raises(updater.UpdateError, match="нет программы"):
        updater.stage_update(archive, info, platform="darwin", executable=str(exe))


def test_installed_app_path_requires_app_bundle_on_macos(tmp_path):
    with pytest.raises(updater.UpdateError):
        updater.installed_app_path("darwin", str(tmp_path / "bin" / "x" / "AppRestore"))
    assert updater.installed_app_path("win32", r"C:\Apps\AppRestore\AppRestore.exe".replace("\\", os.sep)).name == "AppRestore"


def test_windows_script_waits_checks_health_and_rolls_back():
    script = updater.WINDOWS_SWAP_SCRIPT
    assert "swap script start" in script
    assert script.index("swap script start") < script.index("Stop-Process")
    assert "old process still running, stopping it" in script
    assert 'ProcessName -ne "AppRestore"' in script
    assert "--update-health-check" in script
    assert "rolling back" in script
    assert script.index("Rename-Item -LiteralPath $Target") < script.index("Move-Item -LiteralPath $New")
    assert script.index('Log "swap done"') < script.index("Remove-Item -LiteralPath $Staging")
    assert "Remove-Item -LiteralPath $Backup" in script


def test_windows_launch_command_is_hidden_without_detaching_powershell(tmp_path, monkeypatch):
    app, exe = _frozen_layout(tmp_path, "win32")
    staging = tmp_path / "Apps" / ".AppRestore-update-x"
    (staging / "AppRestore").mkdir(parents=True)
    staged = updater.StagedUpdate(info=None, staging_dir=staging, new_app=staging / "AppRestore", target_app=app)
    monkeypatch.setattr("apprestore_core.paths.resolve_windows_system_tool", lambda name: r"C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe")
    monkeypatch.setattr(updater, "update_log_path", lambda: tmp_path / "update.log")
    calls = []
    cmd = updater.launch_swap(staged, pid=1234, platform="win32", popen=lambda c, **kw: calls.append((c, kw)))
    flags = calls[0][1]["creationflags"]
    assert flags == updater.WINDOWS_SWAP_CREATIONFLAGS
    assert flags & 0x08000000  # CREATE_NO_WINDOW
    assert not flags & 0x00000008  # DETACHED_PROCESS exits PowerShell 5.1 before -File
    assert "-File" in cmd and "1234" in cmd and str(app) in cmd
    script = tmp_path / "apply-update.ps1"
    assert script.is_file()
    assert not (staging / "apply-update.ps1").exists()
    assert script.read_text(encoding="utf-8-sig").startswith("param(")


@pytest.mark.skipif(sys.platform != "win32", reason="Windows PowerShell launch flags")
def test_windows_swap_flags_actually_run_the_script(tmp_path):
    script = tmp_path / "probe.ps1"
    log = tmp_path / "probe.log"
    script.write_text(
        "param([string]$Log)\r\nAdd-Content -LiteralPath $Log -Value started\r\n",
        encoding="utf-8-sig",
    )
    subprocess.Popen(
        [
            r"C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe",
            "-NoProfile",
            "-NonInteractive",
            "-ExecutionPolicy",
            "Bypass",
            "-WindowStyle",
            "Hidden",
            "-File",
            str(script),
            "-Log",
            str(log),
        ],
        creationflags=updater.WINDOWS_SWAP_CREATIONFLAGS,
        close_fds=True,
    )
    deadline = time.time() + 15
    while time.time() < deadline and not log.exists():
        time.sleep(0.1)
    assert log.is_file()
    assert "started" in log.read_text(encoding="utf-8")


posix_only = pytest.mark.skipif(os.name == "nt", reason="POSIX swap script")


def _bundle(path: Path, health_rc: int, marker: str) -> None:
    binary = path / "Contents" / "MacOS" / "AppRestore"
    binary.parent.mkdir(parents=True)
    binary.write_text(f"#!/bin/sh\necho {marker}\nexit {health_rc}\n")
    binary.chmod(0o755)


def _run_swap(tmp_path: Path, health_rc: int):
    target = tmp_path / "AppRestore.app"
    _bundle(target, 0, "old")
    staging = tmp_path / ".AppRestore-update-test"
    _bundle(staging / "AppRestore.app", health_rc, "new")
    log = tmp_path / "update.log"
    sleeper = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(0.6)"])
    staged = updater.StagedUpdate(info=None, staging_dir=staging, new_app=staging / "AppRestore.app", target_app=target)
    relaunched = tmp_path / "relaunched"
    procs = []

    def popen(cmd, **kw):
        kw.pop("stdout", None), kw.pop("stderr", None)
        proc = subprocess.Popen(cmd, **kw)
        procs.append(proc)
        return proc

    os.environ["APPRESTORE_CACHE_DIR"] = str(tmp_path / "cache")
    try:
        updater.launch_swap(staged, pid=sleeper.pid, platform="darwin", relaunch=f"touch {relaunched}", popen=popen)
        assert target.exists()  # still old while the app is running
        sleeper.wait()
        assert procs[0].wait(timeout=30) == (0 if health_rc == 0 else 1)
    finally:
        os.environ.pop("APPRESTORE_CACHE_DIR", None)
    return target, staging, relaunched


@posix_only
def test_posix_swap_replaces_app_after_exit(tmp_path):
    target, staging, relaunched = _run_swap(tmp_path, health_rc=0)
    assert "new" in (target / "Contents" / "MacOS" / "AppRestore").read_text()
    assert not staging.exists()
    assert not list(tmp_path.glob("AppRestore.app.old-*"))
    assert relaunched.exists()


@posix_only
def test_posix_swap_rolls_back_when_new_version_is_broken(tmp_path):
    target, staging, relaunched = _run_swap(tmp_path, health_rc=3)
    assert "old" in (target / "Contents" / "MacOS" / "AppRestore").read_text()
    assert not staging.exists()
    assert not list(tmp_path.glob("AppRestore.app.old-*"))
    assert relaunched.exists()


def test_health_check_passes_in_dev_env():
    pytest.importorskip("PySide6")
    pytest.importorskip("pymobiledevice3")
    assert updater.health_check() == 0
