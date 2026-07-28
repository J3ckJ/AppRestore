from __future__ import annotations

import hashlib
import importlib.util
import os
import shutil
import subprocess
import sys
import threading
import zipfile
from contextlib import contextmanager
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Iterator

import pytest


ROOT = Path(__file__).resolve().parents[1]
BUILD_SCRIPT = ROOT / "scripts" / "build-release.py"
TEMPLATE = ROOT / "scripts" / "install.ps1.in"

SPEC = importlib.util.spec_from_file_location(
    "apprestore_build_release",
    BUILD_SCRIPT,
)
assert SPEC is not None and SPEC.loader is not None
BUILD_RELEASE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(BUILD_RELEASE)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _powershell_literal(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def _available_powershells() -> list[str]:
    result: list[str] = []
    for name in ("powershell.exe", "pwsh.exe"):
        resolved = shutil.which(name)
        if resolved and resolved.lower() not in {
            existing.lower() for existing in result
        }:
            result.append(resolved)
    return result


POWERSHELLS = _available_powershells()


class _QuietRequestHandler(SimpleHTTPRequestHandler):
    def log_message(self, format: str, *args: object) -> None:
        del format, args


@contextmanager
def _serve_directory(directory: Path) -> Iterator[str]:
    handler = partial(_QuietRequestHandler, directory=str(directory))
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        host, port = server.server_address[:2]
        yield f"http://{host}:{port}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def _copy_release_inputs(destination: Path) -> None:
    for relative_name in (
        list(BUILD_RELEASE.ROOT_FILES) + list(BUILD_RELEASE.SCRIPT_FILES)
    ):
        source = ROOT / relative_name
        target = destination / relative_name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)

    for directory_name in ("apprestore_core", "tests"):
        for source in sorted((ROOT / directory_name).glob("*.py")):
            target = destination / directory_name / source.name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)


def _make_fake_release(
    archive_path: Path,
    *,
    version: str,
    traversal_entry: bool = False,
) -> None:
    root_name = f"AppRestore-{version}"
    fake_installer = rf"""
#requires -Version 5.1
$ErrorActionPreference = "Stop"
$InstallRoot = Join-Path $env:LOCALAPPDATA "Programs\AppRestore"
$BinTarget = Join-Path $InstallRoot "bin"
New-Item -ItemType Directory -Path $BinTarget -Force | Out-Null
$Wrapper = @'
@echo off
if /I "%~1"=="--version" (
  echo {version}
  exit /b 0
)
exit /b 0
'@
$Utf8WithoutBom = New-Object System.Text.UTF8Encoding($false)
[System.IO.File]::WriteAllText(
    (Join-Path $BinTarget "apprestore.cmd"),
    $Wrapper,
    $Utf8WithoutBom
)
""".lstrip()

    with zipfile.ZipFile(
        archive_path,
        "w",
        compression=zipfile.ZIP_DEFLATED,
    ) as archive:
        archive.writestr(f"{root_name}/install-windows.ps1", fake_installer)
        archive.writestr(f"{root_name}/README.md", "test payload\n")
        if traversal_entry:
            archive.writestr(
                f"{root_name}/../../escaped.txt",
                "must not be extracted\n",
            )


def _render_test_bootstrap(
    path: Path,
    *,
    version: str,
    archive_sha256: str,
) -> None:
    rendered = BUILD_RELEASE.render_bootstrap(
        version=version,
        archive_url=(
            "https://invalid.example.test/"
            f"AppRestore-{version}-source.zip"
        ),
        archive_sha256=archive_sha256,
        template_path=TEMPLATE,
    )
    path.write_text(rendered, encoding="utf-8", newline="\n")


def _run_bootstrap(
    *,
    powershell: str,
    working_root: Path,
    bootstrap: Path,
    archive_url: str,
    expect_success: bool,
) -> subprocess.CompletedProcess[str]:
    runtime_temp = working_root / "runtime-temp"
    local_app_data = working_root / "local-app-data"
    runtime_temp.mkdir(parents=True, exist_ok=True)
    local_app_data.mkdir(parents=True, exist_ok=True)
    runner = working_root / "run-bootstrap.ps1"

    if expect_success:
        postcondition = r"""
$ExpectedBin = Join-Path $env:LOCALAPPDATA "Programs\AppRestore\bin"
$ExpectedCommand = Join-Path $ExpectedBin "apprestore.cmd"
$FirstPath = ($env:Path -split ";")[0].Trim().Trim('"').TrimEnd("\")
if (-not [string]::Equals(
    $FirstPath,
    $ExpectedBin.TrimEnd("\"),
    [System.StringComparison]::OrdinalIgnoreCase
)) {
    throw "bootstrap did not update the current process PATH"
}
$Resolved = Get-Command "apprestore" -ErrorAction Stop
if (-not [string]::Equals(
    [string]$Resolved.Source,
    $ExpectedCommand,
    [System.StringComparison]::OrdinalIgnoreCase
)) {
    throw "apprestore resolves to an unexpected command"
}
$Version = (& $Resolved.Source "--version" | Out-String).Trim()
if ($Version -ne "9.9.9") {
    throw "unexpected version after bootstrap: $Version"
}
Write-Output "APPRESTORE_BOOTSTRAP_E2E_OK"
"""
    else:
        postcondition = ""

    runner.write_text(
        (
            '$ErrorActionPreference = "Stop"\n'
            f"$BootstrapText = Get-Content -LiteralPath "
            f"{_powershell_literal(str(bootstrap))} -Raw\n"
            "Invoke-Expression $BootstrapText\n"
            f"{postcondition}\n"
        ),
        encoding="utf-8",
        newline="\n",
    )

    environment = os.environ.copy()
    environment.update(
        {
            "APPRESTORE_BOOTSTRAP_ARCHIVE_URL": archive_url,
            "LOCALAPPDATA": str(local_app_data),
            "TEMP": str(runtime_temp),
            "TMP": str(runtime_temp),
        }
    )
    result = subprocess.run(
        [
            powershell,
            "-NoLogo",
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(runner),
        ],
        cwd=working_root,
        env=environment,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert not list(runtime_temp.glob("AppRestore-bootstrap-*"))
    return result


def test_rendered_bootstrap_has_pinned_fields_and_only_url_override() -> None:
    digest = "a5" * 32
    rendered = BUILD_RELEASE.render_bootstrap(
        version="1.2.3",
        archive_url=(
            "https://downloads.example.test/"
            "AppRestore-1.2.3-source.zip"
        ),
        archive_sha256=digest,
        template_path=TEMPLATE,
    )

    assert "$AppRestoreVersion = '1.2.3'" in rendered
    assert (
        "$DefaultArchiveUrl = "
        "'https://downloads.example.test/AppRestore-1.2.3-source.zip'"
    ) in rendered
    assert f"$ExpectedArchiveSha256 = '{digest}'" in rendered
    assert "APPRESTORE_BOOTSTRAP_ARCHIVE_URL" in rendered
    assert "APPRESTORE_BOOTSTRAP_ARCHIVE_SHA" not in rendered
    assert "@@APPRESTORE_" not in rendered


@pytest.mark.parametrize(
    ("version", "url", "digest"),
    [
        ("1.2", "https://example.test/a.zip", "a" * 64),
        ("1.2.3", "http://example.test/a.zip", "a" * 64),
        ("1.2.3", "https://user@example.test/a.zip", "a" * 64),
        ("1.2.3", "https://example.test/a.zip", "not-a-hash"),
    ],
)
def test_render_bootstrap_rejects_unpinned_metadata(
    version: str,
    url: str,
    digest: str,
) -> None:
    with pytest.raises(ValueError):
        BUILD_RELEASE.render_bootstrap(
            version=version,
            archive_url=url,
            archive_sha256=digest,
            template_path=TEMPLATE,
        )


def test_release_build_is_deterministic_and_generates_bootstrap(
    tmp_path: Path,
) -> None:
    release_root = tmp_path / "AppRestore"
    _copy_release_inputs(release_root)
    script = release_root / "scripts" / "build-release.py"

    first = subprocess.run(
        [sys.executable, str(script)],
        cwd=release_root,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert first.returncode == 0, first.stdout + first.stderr

    archive = release_root / "dist" / "AppRestore-0.1.3-source.zip"
    bootstrap = release_root / "dist" / "install.ps1"
    checksums = release_root / "dist" / "SHA256SUMS.txt"
    assert archive.is_file()
    assert bootstrap.is_file()
    assert checksums.is_file()

    archive_digest = _sha256(archive)
    bootstrap_digest = _sha256(bootstrap)
    bootstrap_text = bootstrap.read_text(encoding="utf-8")
    assert f"$ExpectedArchiveSha256 = '{archive_digest}'" in bootstrap_text
    assert (
        "https://github.com/J3ckJ/AppRestore/releases/download/v0.1.3/"
        "AppRestore-0.1.3-source.zip"
    ) in bootstrap_text
    assert checksums.read_text(encoding="utf-8") == (
        f"{archive_digest}  {archive.name}\n"
        f"{bootstrap_digest}  {bootstrap.name}\n"
    )

    with zipfile.ZipFile(archive) as release_zip:
        names = release_zip.namelist()
    assert "AppRestore-0.1.3/CONTRIBUTING.md" in names
    assert "AppRestore-0.1.3/SECURITY.md" in names
    assert "AppRestore-0.1.3/scripts/install.ps1.in" in names
    assert "AppRestore-0.1.3/dist/install.ps1" not in names

    first_hashes = (_sha256(archive), _sha256(bootstrap))
    second = subprocess.run(
        [sys.executable, str(script)],
        cwd=release_root,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert second.returncode == 0, second.stdout + second.stderr
    assert (_sha256(archive), _sha256(bootstrap)) == first_hashes


@pytest.mark.skipif(
    not POWERSHELLS,
    reason="Windows PowerShell or pwsh is required for bootstrap E2E",
)
@pytest.mark.parametrize("powershell", POWERSHELLS)
def test_bootstrap_local_override_installs_and_updates_same_process_path(
    tmp_path: Path,
    powershell: str,
) -> None:
    version = "9.9.9"
    archive = tmp_path / f"AppRestore-{version}-source.zip"
    bootstrap = tmp_path / "install.ps1"
    _make_fake_release(archive, version=version)
    _render_test_bootstrap(
        bootstrap,
        version=version,
        archive_sha256=_sha256(archive),
    )

    with _serve_directory(tmp_path) as base_url:
        result = _run_bootstrap(
            powershell=powershell,
            working_root=tmp_path,
            bootstrap=bootstrap,
            archive_url=f"{base_url}/{archive.name}",
            expect_success=True,
        )

    assert result.returncode == 0, result.stdout + result.stderr
    assert "APPRESTORE_BOOTSTRAP_E2E_OK" in result.stdout


@pytest.mark.skipif(
    not POWERSHELLS,
    reason="Windows PowerShell or pwsh is required for bootstrap E2E",
)
@pytest.mark.parametrize("powershell", POWERSHELLS)
def test_bootstrap_url_override_cannot_bypass_pinned_sha(
    tmp_path: Path,
    powershell: str,
) -> None:
    version = "9.9.9"
    archive = tmp_path / f"AppRestore-{version}-source.zip"
    bootstrap = tmp_path / "install.ps1"
    _make_fake_release(archive, version=version)
    _render_test_bootstrap(
        bootstrap,
        version=version,
        archive_sha256="0" * 64,
    )

    with _serve_directory(tmp_path) as base_url:
        result = _run_bootstrap(
            powershell=powershell,
            working_root=tmp_path,
            bootstrap=bootstrap,
            archive_url=f"{base_url}/{archive.name}",
            expect_success=False,
        )

    assert result.returncode != 0
    assert "SHA-256 mismatch" in (result.stdout + result.stderr)
    assert not (
        tmp_path
        / "local-app-data"
        / "Programs"
        / "AppRestore"
        / "bin"
        / "apprestore.cmd"
    ).exists()


@pytest.mark.skipif(
    not POWERSHELLS,
    reason="Windows PowerShell or pwsh is required for bootstrap E2E",
)
@pytest.mark.parametrize("powershell", POWERSHELLS)
def test_bootstrap_rejects_traversal_even_when_archive_hash_is_pinned(
    tmp_path: Path,
    powershell: str,
) -> None:
    version = "9.9.9"
    archive = tmp_path / f"AppRestore-{version}-source.zip"
    bootstrap = tmp_path / "install.ps1"
    _make_fake_release(
        archive,
        version=version,
        traversal_entry=True,
    )
    _render_test_bootstrap(
        bootstrap,
        version=version,
        archive_sha256=_sha256(archive),
    )

    with _serve_directory(tmp_path) as base_url:
        result = _run_bootstrap(
            powershell=powershell,
            working_root=tmp_path,
            bootstrap=bootstrap,
            archive_url=f"{base_url}/{archive.name}",
            expect_success=False,
        )

    assert result.returncode != 0
    assert "traversal" in (result.stdout + result.stderr).lower()
    assert not (tmp_path / "escaped.txt").exists()
