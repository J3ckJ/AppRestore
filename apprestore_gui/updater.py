"""Check GitHub releases and update the frozen GUI in one click.

Flow (nothing is installed without the user's click):
1. ``check_for_update()`` reads ``releases/latest`` of J3ckJ/AppRestore
   (public API, no token) and compares the tag with the running version.
2. ``download_update()`` downloads the GUI asset for this OS and verifies its
   SHA-256 against ``SHA256SUMS.txt`` from the same release.
3. ``stage_update()`` unpacks it next to the installed app (same volume) and
   checks the layout.
4. ``launch_swap()`` starts a tiny detached script that waits for this process
   to exit, renames the old app aside, moves the new one in, runs the new
   binary's ``--update-health-check`` and rolls back on any failure, then
   relaunches the app.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import time
import urllib.request
import zipfile
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Any, Callable

REPO = "J3ckJ/AppRestore"
LATEST_URL = f"https://api.github.com/repos/{REPO}/releases/latest"
DOWNLOAD_PREFIX = f"https://github.com/{REPO}/releases/download/"
CHECKSUMS_NAME = "SHA256SUMS.txt"
USER_AGENT = "AppRestoreGUI-updater (+https://github.com/J3ckJ/AppRestore)"
HEALTH_FLAG = "--update-health-check"
MAX_ASSET_BYTES = 1024 * 1024 * 1024
ASSET_BY_PLATFORM = {
    "win32": "AppRestore-GUI-Windows.zip",
    "darwin": "AppRestore-GUI-macOS.zip",
}

Fetch = Callable[[str], bytes]
Progress = Callable[[int, int], None]


class UpdateError(RuntimeError):
    """User-facing update failure (message is shown in the window)."""


@dataclass(frozen=True)
class UpdateInfo:
    current: str
    latest: str
    tag: str
    notes: str
    page_url: str
    asset_name: str | None = None
    asset_url: str | None = None
    asset_size: int = 0
    checksums_url: str | None = None

    @property
    def newer(self) -> bool:
        return parse_version(self.latest) > parse_version(self.current)

    @property
    def installable(self) -> bool:
        return bool(self.newer and self.asset_url and self.checksums_url)


@dataclass
class StagedUpdate:
    info: UpdateInfo
    staging_dir: Path
    new_app: Path
    target_app: Path
    extra: dict[str, Any] = field(default_factory=dict)


def parse_version(text: str) -> tuple[int, int, int]:
    match = re.fullmatch(r"v?(\d+)\.(\d+)\.(\d+)", (text or "").strip())
    if not match:
        raise UpdateError(f"непонятный номер версии: {text!r}")
    return tuple(int(part) for part in match.groups())  # type: ignore[return-value]


def _http_get(url: str, timeout: float = 30.0) -> bytes:
    request = urllib.request.Request(
        url,
        headers={"User-Agent": USER_AGENT, "Accept": "application/vnd.github+json"},
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310
        return response.read()


def asset_name_for(platform: str | None = None) -> str | None:
    return ASSET_BY_PLATFORM.get(platform or sys.platform)


def _trusted_download_url(url: str) -> bool:
    return isinstance(url, str) and url.startswith(DOWNLOAD_PREFIX)


def check_for_update(
    current: str | None = None,
    *,
    fetch: Fetch | None = None,
    platform: str | None = None,
) -> UpdateInfo:
    if current is None:
        from apprestore_core import __version__ as current
    fetch = fetch or _http_get
    try:
        payload = json.loads(fetch(LATEST_URL).decode("utf-8"))
    except UpdateError:
        raise
    except Exception as exc:  # noqa: BLE001
        raise UpdateError(f"не удалось связаться с GitHub: {exc}") from exc
    if not isinstance(payload, dict) or payload.get("draft") or payload.get("prerelease"):
        raise UpdateError("GitHub вернул неожиданный ответ о последней версии")
    tag = str(payload.get("tag_name") or "")
    latest = tag[1:] if tag.startswith("v") else tag
    parse_version(latest)
    wanted = asset_name_for(platform)
    asset_url = None
    asset_size = 0
    checksums_url = None
    for asset in payload.get("assets") or []:
        if not isinstance(asset, dict):
            continue
        name = asset.get("name")
        url = asset.get("browser_download_url")
        if not _trusted_download_url(url):
            continue
        if name == wanted:
            asset_url = url
            asset_size = int(asset.get("size") or 0)
        elif name == CHECKSUMS_NAME:
            checksums_url = url
    return UpdateInfo(
        current=current,
        latest=latest,
        tag=tag,
        notes=str(payload.get("body") or "").strip(),
        page_url=str(payload.get("html_url") or f"https://github.com/{REPO}/releases/latest"),
        asset_name=wanted if asset_url else None,
        asset_url=asset_url,
        asset_size=asset_size,
        checksums_url=checksums_url,
    )


def parse_checksums(text: str) -> dict[str, str]:
    result: dict[str, str] = {}
    for line in text.splitlines():
        match = re.fullmatch(r"([0-9a-fA-F]{64})\s+\*?(\S.*)", line.strip())
        if match:
            result[match.group(2).strip()] = match.group(1).lower()
    return result


def download_update(
    info: UpdateInfo,
    directory: Path,
    *,
    fetch: Fetch | None = None,
    progress: Progress | None = None,
    opener: Callable[[str], Any] | None = None,
) -> Path:
    """Download the asset and verify SHA-256; returns the verified zip path."""

    if not info.installable or not info.asset_name:
        raise UpdateError("для вашей системы в этом релизе нет сборки программы")
    fetch = fetch or _http_get
    try:
        sums = parse_checksums(fetch(str(info.checksums_url)).decode("utf-8", "replace"))
    except Exception as exc:  # noqa: BLE001
        raise UpdateError(f"не удалось скачать {CHECKSUMS_NAME}: {exc}") from exc
    expected = sums.get(info.asset_name)
    if not expected:
        raise UpdateError(f"в {CHECKSUMS_NAME} нет суммы для {info.asset_name}")

    directory.mkdir(parents=True, exist_ok=True)
    target = directory / info.asset_name
    partial = target.with_suffix(target.suffix + ".part")
    digest = hashlib.sha256()
    total = info.asset_size
    done = 0
    open_url = opener or (
        lambda url: urllib.request.urlopen(  # noqa: S310
            urllib.request.Request(url, headers={"User-Agent": USER_AGENT}), timeout=60
        )
    )
    try:
        with open_url(str(info.asset_url)) as response, open(partial, "wb") as out:
            while True:
                chunk = response.read(1024 * 256)
                if not chunk:
                    break
                done += len(chunk)
                if done > MAX_ASSET_BYTES:
                    raise UpdateError("файл обновления подозрительно большой")
                digest.update(chunk)
                out.write(chunk)
                if progress:
                    progress(done, total)
    except UpdateError:
        partial.unlink(missing_ok=True)
        raise
    except Exception as exc:  # noqa: BLE001
        partial.unlink(missing_ok=True)
        raise UpdateError(f"загрузка прервалась: {exc}") from exc
    actual = digest.hexdigest()
    if actual != expected:
        partial.unlink(missing_ok=True)
        raise UpdateError(
            "контрольная сумма не совпала, файл не используется "
            f"(ожидалась {expected[:12]}…, получена {actual[:12]}…)"
        )
    partial.replace(target)
    return target


def installed_app_path(platform: str | None = None, executable: str | None = None) -> Path:
    """Folder (Windows) or .app bundle (macOS) of the running frozen app."""

    platform = platform or sys.platform
    exe = Path(executable or sys.executable)
    if platform == "win32":
        return exe.parent
    if platform == "darwin":
        bundle = exe.parent.parent.parent
        if bundle.suffix != ".app":
            raise UpdateError("программа запущена не из AppRestore.app")
        return bundle
    raise UpdateError("обновление в один клик есть только на Windows и macOS")


def _safe_members(archive: zipfile.ZipFile) -> None:
    for member in archive.infolist():
        name = member.filename
        path = PurePosixPath(name.replace("\\", "/"))
        if path.is_absolute() or ".." in path.parts or re.match(r"^[A-Za-z]:", name):
            raise UpdateError(f"в архиве небезопасный путь: {name}")


def extract_zip(zip_path: Path, destination: Path, platform: str | None = None) -> None:
    platform = platform or sys.platform
    with zipfile.ZipFile(zip_path) as archive:
        _safe_members(archive)
        if platform == "darwin" and Path("/usr/bin/ditto").exists():
            # ditto keeps symlinks and exec bits inside the .app bundle.
            subprocess.run(
                ["/usr/bin/ditto", "-x", "-k", str(zip_path), str(destination)],
                check=True,
                capture_output=True,
                timeout=600,
            )
            return
        for member in archive.infolist():
            archive.extract(member, destination)
            mode = (member.external_attr >> 16) & 0o777
            if mode and not member.is_dir():
                os.chmod(destination / member.filename, mode)


def stage_update(
    zip_path: Path,
    info: UpdateInfo,
    *,
    platform: str | None = None,
    executable: str | None = None,
) -> StagedUpdate:
    platform = platform or sys.platform
    target = installed_app_path(platform, executable)
    parent = target.parent
    if not os.access(parent, os.W_OK):
        raise UpdateError(
            f"нет прав на запись в {parent}. Переместите AppRestore в свою папку "
            "или обновите вручную со страницы релиза."
        )
    staging = Path(tempfile.mkdtemp(prefix=".AppRestore-update-", dir=parent))
    try:
        extract_zip(zip_path, staging, platform)
        if platform == "win32":
            new_app = staging / "AppRestore"
            binary = new_app / "AppRestore.exe"
        else:
            new_app = staging / "AppRestore.app"
            binary = new_app / "Contents" / "MacOS" / "AppRestore"
        if not binary.is_file():
            raise UpdateError("в архиве обновления нет программы AppRestore")
        if platform != "win32":
            binary.chmod(binary.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    except Exception:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    return StagedUpdate(info=info, staging_dir=staging, new_app=new_app, target_app=target)


POSIX_SWAP_SCRIPT = r"""#!/bin/sh
# AppRestore updater: swap the .app after the old process exits, roll back on failure.
PID="$1"; TARGET="$2"; NEW="$3"; STAGING="$4"; LOG="$5"; RELAUNCH="$6"
BACKUP="$TARGET.old-$$"
log() { echo "$(date '+%Y-%m-%d %H:%M:%S') $*" >> "$LOG"; }
i=0
while kill -0 "$PID" 2>/dev/null; do
  i=$((i+1)); [ "$i" -gt 240 ] && { log "old process did not exit"; exit 1; }
  sleep 0.5
done
log "swap start: $TARGET"
if ! mv "$TARGET" "$BACKUP"; then log "cannot move old app aside"; rm -rf "$STAGING"; exit 1; fi
if ! mv "$NEW" "$TARGET"; then
  log "cannot move new app in; rolling back"; mv "$BACKUP" "$TARGET"; rm -rf "$STAGING"; exit 1
fi
BIN="$TARGET/Contents/MacOS/AppRestore"
[ -x "$BIN" ] || BIN="$TARGET/AppRestore"
if ! "$BIN" __HEALTH__ >> "$LOG" 2>&1; then
  log "health check failed; rolling back"
  rm -rf "$TARGET"; mv "$BACKUP" "$TARGET"; rm -rf "$STAGING"
  [ -n "$RELAUNCH" ] && sh -c "$RELAUNCH" >/dev/null 2>&1
  exit 1
fi
rm -rf "$BACKUP" "$STAGING"
log "swap done"
[ -n "$RELAUNCH" ] && sh -c "$RELAUNCH" >/dev/null 2>&1
exit 0
""".replace("__HEALTH__", HEALTH_FLAG)


WINDOWS_SWAP_SCRIPT = r"""param(
  [int]$ProcessId, [string]$Target, [string]$New, [string]$Staging, [string]$Log
)
$ErrorActionPreference = "Stop"
function Log($m) { Add-Content -LiteralPath $Log -Value ("{0} {1}" -f (Get-Date -Format s), $m) }
try { Wait-Process -Id $ProcessId -Timeout 120 -ErrorAction SilentlyContinue } catch {}
Start-Sleep -Milliseconds 500
$Backup = "$Target.old-$PID"
Log "swap start: $Target"
$moved = $false
for ($i = 0; $i -lt 20 -and -not $moved; $i++) {
  try {
    Rename-Item -LiteralPath $Target -NewName (Split-Path $Backup -Leaf)
    $moved = $true
  } catch {
    Start-Sleep -Seconds 1
  }
}
if (-not $moved) {
  Log "cannot move old app aside (files still in use)"
  Remove-Item -LiteralPath $Staging -Recurse -Force -ErrorAction SilentlyContinue
  Start-Process -FilePath (Join-Path $Target "AppRestore.exe")
  exit 1
}
try {
  Move-Item -LiteralPath $New -Destination $Target
  $Exe = Join-Path $Target "AppRestore.exe"
  $Health = Start-Process -FilePath $Exe -ArgumentList "__HEALTH__" -Wait -PassThru -WindowStyle Hidden
  if ($Health.ExitCode -ne 0) { throw "health check exit code $($Health.ExitCode)" }
} catch {
  Log "update failed, rolling back: $_"
  if (Test-Path -LiteralPath $Target) { Remove-Item -LiteralPath $Target -Recurse -Force -ErrorAction SilentlyContinue }
  Rename-Item -LiteralPath $Backup -NewName (Split-Path $Target -Leaf)
  Remove-Item -LiteralPath $Staging -Recurse -Force -ErrorAction SilentlyContinue
  Start-Process -FilePath (Join-Path $Target "AppRestore.exe")
  exit 1
}
Remove-Item -LiteralPath $Backup -Recurse -Force -ErrorAction SilentlyContinue
Remove-Item -LiteralPath $Staging -Recurse -Force -ErrorAction SilentlyContinue
Log "swap done"
Start-Process -FilePath (Join-Path $Target "AppRestore.exe")
exit 0
""".replace("__HEALTH__", HEALTH_FLAG)


def update_log_path() -> Path:
    try:
        from apprestore_core.paths import cache_dir

        root = cache_dir()
    except Exception:  # noqa: BLE001
        root = Path(tempfile.gettempdir())
    root.mkdir(parents=True, exist_ok=True)
    return root / "update.log"


def launch_swap(
    staged: StagedUpdate,
    *,
    pid: int | None = None,
    platform: str | None = None,
    relaunch: str | None = None,
    popen: Callable[..., Any] = subprocess.Popen,
) -> list[str]:
    """Start the detached swap script; the caller must quit right after."""

    platform = platform or sys.platform
    pid = os.getpid() if pid is None else pid
    log = update_log_path()
    if platform == "win32":
        script = staged.staging_dir / "apply-update.ps1"
        script.write_text(WINDOWS_SWAP_SCRIPT, encoding="utf-8-sig")
        from apprestore_core.paths import resolve_windows_system_tool

        powershell = resolve_windows_system_tool("powershell") or "powershell.exe"
        command = [
            powershell,
            "-NoProfile",
            "-NonInteractive",
            "-ExecutionPolicy",
            "Bypass",
            "-WindowStyle",
            "Hidden",
            "-File",
            str(script),
            "-ProcessId",
            str(pid),
            "-Target",
            str(staged.target_app),
            "-New",
            str(staged.new_app),
            "-Staging",
            str(staged.staging_dir),
            "-Log",
            str(log),
        ]
        flags = 0x00000008 | 0x00000200 | 0x08000000  # DETACHED | NEW_GROUP | NO_WINDOW
        popen(command, creationflags=flags, close_fds=True)
    else:
        script = staged.staging_dir / "apply-update.sh"
        script.write_text(POSIX_SWAP_SCRIPT, encoding="utf-8")
        script.chmod(0o700)
        if relaunch is None:
            relaunch = f"/usr/bin/open {shlex_quote(str(staged.target_app))}"
        command = [
            "/bin/sh",
            str(script),
            str(pid),
            str(staged.target_app),
            str(staged.new_app),
            str(staged.staging_dir),
            str(log),
            relaunch,
        ]
        popen(
            command,
            start_new_session=True,
            close_fds=True,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    return command


def shlex_quote(value: str) -> str:
    import shlex

    return shlex.quote(value)


def health_check() -> int:
    """Run by the updater against the freshly installed bundle: imports only."""

    try:
        from importlib import metadata

        import apprestore_core.tools  # noqa: F401
        import pymobiledevice3.usbmux  # noqa: F401
        from PySide6 import QtWidgets  # noqa: F401

        from apprestore_core import __version__
        from apprestore_gui.theme import resources_dir

        if not (resources_dir() / "fonts").is_dir():
            return 3
        if metadata.version("apprestore") != __version__:
            return 4
    except Exception:  # noqa: BLE001
        return 2
    return 0


def wait_seconds(seconds: float) -> None:  # pragma: no cover - tiny helper for UI
    time.sleep(seconds)
