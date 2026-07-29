from __future__ import annotations

import ast
import contextlib
import hashlib
import io
import os
import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from apprestore_core import __version__
from apprestore_core import cli


ROOT = Path(__file__).resolve().parents[1]
EXPECTED_VERSION = "0.1.4"


def _python_assignment(path: Path, name: str) -> str:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    for node in tree.body:
        if isinstance(node, ast.Assign):
            if any(
                isinstance(target, ast.Name) and target.id == name
                for target in node.targets
            ):
                if isinstance(node.value, ast.Constant) and isinstance(
                    node.value.value, str
                ):
                    return node.value.value
        if (
            isinstance(node, ast.AnnAssign)
            and isinstance(node.target, ast.Name)
            and node.target.id == name
            and isinstance(node.value, ast.Constant)
            and isinstance(node.value.value, str)
        ):
            return node.value.value
    raise AssertionError(f"{name} is not a string literal in {path}")


def _powershell_hosts() -> list[str]:
    hosts: list[str] = []
    seen: set[str] = set()
    for name in ("powershell.exe", "pwsh.exe", "pwsh"):
        executable = shutil.which(name)
        if not executable:
            continue
        key = os.path.normcase(os.path.abspath(executable))
        if key not in seen:
            hosts.append(executable)
            seen.add(key)
    return hosts


def _transaction_function_loader() -> str:
    return r"""
$tokens = $null
$parseErrors = $null
$ast = [System.Management.Automation.Language.Parser]::ParseFile(
    $env:APPRESTORE_INSTALLER_PATH,
    [ref]$tokens,
    [ref]$parseErrors
)
if ($parseErrors.Count -gt 0) {
    throw "Could not parse install-windows.ps1."
}
$functionAst = $ast.Find(
    {
        param($Node)
        $Node -is [System.Management.Automation.Language.FunctionDefinitionAst] -and
            $Node.Name -eq "Invoke-AppRestoreInstallTransaction"
    },
    $true
)
if ($null -eq $functionAst) {
    throw "Transaction function was not found."
}
Invoke-Expression $functionAst.Extent.Text
"""


def _run_transaction_probe(
    host: str,
    root: Path,
    body: str,
) -> subprocess.CompletedProcess[str]:
    runner = root / "transaction-probe.ps1"
    runner.write_text(
        (
            '$ErrorActionPreference = "Stop"\n'
            "Set-StrictMode -Version Latest\n"
            f"{_transaction_function_loader()}\n"
            f"{body}\n"
        ),
        encoding="utf-8",
        newline="\n",
    )
    environment = os.environ.copy()
    environment.update(
        {
            "APPRESTORE_INSTALLER_PATH": str(ROOT / "install-windows.ps1"),
            "APPRESTORE_TRANSACTION_ROOT": str(root),
        }
    )
    return subprocess.run(
        [
            host,
            "-NoLogo",
            "-NoProfile",
            "-NonInteractive",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(runner),
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=environment,
        timeout=30,
        check=False,
    )


class VersionContractTests(unittest.TestCase):
    def test_all_release_versions_match_expected(self) -> None:
        project_section = (
            (ROOT / "pyproject.toml")
            .read_text(encoding="utf-8")
            .split("[project]", 1)[1]
            .split("\n[", 1)[0]
        )
        project_match = re.search(
            r'(?m)^\s*version\s*=\s*"([^"]+)"\s*$',
            project_section,
        )
        self.assertIsNotNone(project_match, "project.version is missing")

        installer_text = (ROOT / "install-windows.ps1").read_text(
            encoding="utf-8"
        )
        installer_match = re.search(
            r'(?m)^\s*\$AppRestoreVersion\s*=\s*"([^"]+)"\s*$',
            installer_text,
        )
        self.assertIsNotNone(
            installer_match,
            "$AppRestoreVersion is missing from install-windows.ps1",
        )
        macos_installer_match = re.search(
            r'(?m)^\s*APPRESTORE_VERSION="([^"]+)"\s*$',
            (ROOT / "install-macos.sh").read_text(encoding="utf-8"),
        )
        self.assertIsNotNone(
            macos_installer_match,
            "APPRESTORE_VERSION is missing from install-macos.sh",
        )

        readme_match = re.search(
            r"(?m)^.*\bВерсия\s+([0-9]+\.[0-9]+\.[0-9]+)\b",
            (ROOT / "README.md").read_text(encoding="utf-8"),
        )
        self.assertIsNotNone(
            readme_match,
            "README.md does not state the current AppRestore version",
        )

        changelog_match = re.search(
            r"(?m)^##\s+([0-9]+\.[0-9]+\.[0-9]+)\b",
            (ROOT / "CHANGELOG.md").read_text(encoding="utf-8"),
        )
        self.assertIsNotNone(
            changelog_match,
            "CHANGELOG.md does not contain a release heading",
        )

        versions = {
            "Python package": __version__,
            "pyproject.toml": project_match.group(1),
            "install-windows.ps1": installer_match.group(1),
            "install-macos.sh": macos_installer_match.group(1),
            "scripts/build-release.py": _python_assignment(
                ROOT / "scripts" / "build-release.py", "VERSION"
            ),
            "README.md": readme_match.group(1),
            "CHANGELOG.md latest release": changelog_match.group(1),
        }
        self.assertEqual(
            set(versions.values()),
            {EXPECTED_VERSION},
            f"release version drift: {versions}",
        )


class PowerShellSyntaxTests(unittest.TestCase):
    def test_every_powershell_script_parses_without_ast_errors(self) -> None:
        hosts = _powershell_hosts()
        if not hosts:
            self.skipTest("PowerShell is not available")

        parse_command = r"""
$tokens = $null
$parseErrors = $null
[void][System.Management.Automation.Language.Parser]::ParseFile(
    $env:APPRESTORE_AST_TARGET,
    [ref]$tokens,
    [ref]$parseErrors
)
if ($parseErrors.Count -gt 0) {
    foreach ($parseError in $parseErrors) {
        [Console]::Error.WriteLine($parseError.Message)
    }
    exit 1
}
"""
        scripts = sorted(ROOT.glob("*.ps1"))
        self.assertTrue(scripts, "no PowerShell scripts found")

        for host in hosts:
            for script in scripts:
                with self.subTest(host=Path(host).name, script=script.name):
                    environment = os.environ.copy()
                    environment["APPRESTORE_AST_TARGET"] = str(script)
                    result = subprocess.run(
                        [
                            host,
                            "-NoLogo",
                            "-NoProfile",
                            "-NonInteractive",
                            "-Command",
                            parse_command,
                        ],
                        capture_output=True,
                        text=True,
                        encoding="utf-8",
                        errors="replace",
                        env=environment,
                        timeout=30,
                        check=False,
                    )
                    self.assertEqual(
                        result.returncode,
                        0,
                        (
                            f"{Path(host).name} could not parse {script.name}\n"
                            f"stdout:\n{result.stdout}\n"
                            f"stderr:\n{result.stderr}"
                        ),
                    )


class InstallerDependencyContractTests(unittest.TestCase):
    def test_python_fallback_is_pinned_and_tar_is_not_required(self) -> None:
        installer = (ROOT / "install-windows.ps1").read_text(encoding="utf-8")
        self.assertIn(
            '$PythonInstallerVersion = "3.12.10"',
            installer,
        )
        self.assertIn(
            '$PythonInstallerSha256 = '
            '"67b5635e80ea51072b87941312d00ec8927c4db9ba18938f7ad2d27b328b95fb"',
            installer,
        )
        self.assertIn("www.python.org/ftp/python/", installer)
        self.assertNotIn('Get-Command "tar.exe"', installer)
        self.assertIn("import tarfile", installer)

    def test_windows_installers_use_known_folder_and_relocatable_wrapper(
        self,
    ) -> None:
        payload = (ROOT / "install-windows.ps1").read_text(encoding="utf-8")
        bootstrap = (ROOT / "scripts" / "install.ps1.in").read_text(
            encoding="utf-8"
        )
        uninstaller = (ROOT / "uninstall-windows.ps1").read_text(
            encoding="utf-8"
        )
        launcher = (ROOT / "apprestore.ps1").read_text(encoding="utf-8")

        for name, script in (
            ("payload", payload),
            ("bootstrap", bootstrap),
            ("uninstaller", uninstaller),
            ("launcher", launcher),
        ):
            with self.subTest(script=name):
                self.assertIn(
                    "[Environment]::GetFolderPath(",
                    script,
                )
                self.assertNotIn("$env:LOCALAPPDATA", script)

        self.assertIn(
            r'"%~dp0..\.venv\Scripts\python.exe" -m apprestore_core.cli %*',
            payload,
        )
        self.assertIn("$ManagedInstallMarkerName", payload)
        self.assertIn("$ManagedInstallMarkerValue", payload)
        self.assertLess(
            payload.index(
                'Ensure-PlainDirectory `\n'
                '    -Path $ProgramsRoot `\n'
                '    -Label "Каталог программ пользователя"'
            ),
            payload.index("$SelectedPython = Find-CompatiblePython"),
            "Programs must be created and reparse-checked before Python bootstrap",
        )
        install_python = payload.split(
            "function Install-CompatiblePython {",
            1,
        )[1].split("\n}", 1)[0]
        self.assertLess(
            install_python.index("$PythonProgramsRoot"),
            install_python.index("$Winget ="),
            "Programs\\Python\\Python312 must be checked before winget/fallback",
        )

    def test_legacy_v013_fingerprint_is_bound_to_real_tag_bytes(self) -> None:
        expected_paths = (
            "apprestore.ps1",
            "uninstall-windows.ps1",
            "apprestore.py",
            "apprestore_core/__init__.py",
            "pyproject.toml",
        )
        expected_hashes = {
            path: hashlib.sha256(
                subprocess.check_output(
                    ["git", "show", f"v0.1.3:{path}"],
                    cwd=ROOT,
                )
            ).hexdigest()
            for path in expected_paths
        }
        payload = (ROOT / "install-windows.ps1").read_text(encoding="utf-8")
        uninstaller = (ROOT / "uninstall-windows.ps1").read_text(
            encoding="utf-8"
        )
        for path, digest in expected_hashes.items():
            with self.subTest(path=path):
                self.assertIn(digest, payload)
                self.assertIn(digest, uninstaller)
        self.assertIn(
            r'"%~dp0..\.venv\Scripts\apprestore.exe" %*',
            payload,
        )
        self.assertIn(
            r'"%~dp0..\.venv\Scripts\apprestore.exe" %*',
            uninstaller,
        )


@unittest.skipUnless(os.name == "nt", "Windows transaction contract")
class WindowsInstallerTransactionTests(unittest.TestCase):
    marker_name = ".apprestore-managed"
    marker_value = "AppRestore managed installation v1"

    def _write_managed_install(self, root: Path, version: str) -> None:
        (root / "bin").mkdir(parents=True)
        (root / self.marker_name).write_text(
            self.marker_value,
            encoding="utf-8",
        )
        (root / "bin" / "apprestore.cmd").write_text(
            (
                "@echo off\n"
                'if /I "%~1"=="--version" (\n'
                f"  echo {version}\n"
                "  exit /b 0\n"
                ")\n"
                "exit /b 7\n"
            ),
            encoding="utf-8",
        )

    def _write_legacy_v013_install(self, root: Path) -> None:
        tagged_files = {
            "apprestore.ps1": "apprestore.ps1",
            "uninstall-windows.ps1": "uninstall-windows.ps1",
            "src/apprestore.py": "apprestore.py",
            "src/apprestore_core/__init__.py": "apprestore_core/__init__.py",
            "src/pyproject.toml": "pyproject.toml",
        }
        for relative, tagged_path in tagged_files.items():
            target = root / Path(relative)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(
                subprocess.check_output(
                    ["git", "show", f"v0.1.3:{tagged_path}"],
                    cwd=ROOT,
                )
            )
        wrapper = (
            "@echo off\n"
            "setlocal\n"
            'set "PATH=%~dp0;%PATH%"\n'
            'set "PYTHONUTF8=1"\n'
            'set "PYTHONIOENCODING=utf-8"\n'
            '"%~dp0..\\.venv\\Scripts\\apprestore.exe" %*\n'
            "exit /b %ERRORLEVEL%"
        )
        (root / "bin").mkdir(parents=True)
        (root / "bin" / "apprestore.cmd").write_text(
            wrapper,
            encoding="utf-8",
            newline="\n",
        )
        (root / "bin" / "ipatool.exe").write_bytes(b"legacy ipatool fixture")
        scripts = root / ".venv" / "Scripts"
        scripts.mkdir(parents=True)
        (scripts / "python.exe").write_bytes(b"legacy python fixture")
        (scripts / "apprestore.exe").write_bytes(b"legacy launcher fixture")

    def test_preparation_failure_preserves_old_command_and_version(self) -> None:
        hosts = _powershell_hosts()
        if not hosts:
            self.skipTest("PowerShell is not available")

        for host in hosts:
            with (
                self.subTest(host=Path(host).name),
                tempfile.TemporaryDirectory(
                    prefix="apprestore-transaction-prepare-"
                ) as directory,
            ):
                programs = Path(directory) / "Programs"
                live = programs / "AppRestore"
                staging = programs / "AppRestore.staging-test"
                backup = programs / "AppRestore.backup-test"
                self._write_managed_install(live, "0.1.2")
                staging.mkdir()

                result = _run_transaction_probe(
                    host,
                    Path(directory),
                    r"""
$Programs = Join-Path $env:APPRESTORE_TRANSACTION_ROOT "Programs"
$Live = Join-Path $Programs "AppRestore"
$Staging = Join-Path $Programs "AppRestore.staging-test"
$Backup = Join-Path $Programs "AppRestore.backup-test"
try {
    Invoke-AppRestoreInstallTransaction `
        -StagingRoot $Staging `
        -InstallRoot $Live `
        -BackupRoot $Backup `
        -ManagedMarkerName ".apprestore-managed" `
        -ManagedMarkerValue "AppRestore managed installation v1" `
        -PrepareStaging { param($Path) throw "forced preparation failure" } `
        -VerifyStaging { param($Path) throw "must not verify" } `
        -VerifyInstallation { param($Path) throw "must not commit" }
    throw "transaction unexpectedly succeeded"
}
catch {
    if ($_.Exception.Message -notmatch "forced preparation failure") {
        throw
    }
}
$OldCommand = Join-Path $Live "bin\apprestore.cmd"
$OldVersion = ((& $OldCommand "--version") | Out-String).Trim()
if ($LASTEXITCODE -ne 0 -or $OldVersion -ne "0.1.2") {
    throw "old command/version was not preserved: '$OldVersion'"
}
if (Test-Path -LiteralPath $Staging) {
    throw "failed staging was not cleaned"
}
if (Test-Path -LiteralPath $Backup) {
    throw "backup must not exist before a swap"
}
Write-Output "PREPARATION_ROLLBACK_OK"
""",
                )
                self.assertEqual(
                    result.returncode,
                    0,
                    result.stdout + result.stderr,
                )
                self.assertIn("PREPARATION_ROLLBACK_OK", result.stdout)

    def test_live_verification_failure_restores_old_command_and_version(
        self,
    ) -> None:
        hosts = _powershell_hosts()
        if not hosts:
            self.skipTest("PowerShell is not available")

        for host in hosts:
            with (
                self.subTest(host=Path(host).name),
                tempfile.TemporaryDirectory(
                    prefix="apprestore-transaction-live-"
                ) as directory,
            ):
                programs = Path(directory) / "Programs"
                live = programs / "AppRestore"
                staging = programs / "AppRestore.staging-test"
                self._write_managed_install(live, "0.1.2")
                self._write_managed_install(staging, "0.1.3")

                result = _run_transaction_probe(
                    host,
                    Path(directory),
                    r"""
$Programs = Join-Path $env:APPRESTORE_TRANSACTION_ROOT "Programs"
$Live = Join-Path $Programs "AppRestore"
$Staging = Join-Path $Programs "AppRestore.staging-test"
$Backup = Join-Path $Programs "AppRestore.backup-test"
try {
    Invoke-AppRestoreInstallTransaction `
        -StagingRoot $Staging `
        -InstallRoot $Live `
        -BackupRoot $Backup `
        -ManagedMarkerName ".apprestore-managed" `
        -ManagedMarkerValue "AppRestore managed installation v1" `
        -PrepareStaging {
            param($Path)
            if (-not (Test-Path -LiteralPath (Join-Path $Live "bin\apprestore.cmd"))) {
                throw "old command disappeared before staging preparation"
            }
        } `
        -VerifyStaging {
            param($Path)
            $Version = ((& (Join-Path $Path "bin\apprestore.cmd") "--version") |
                Out-String).Trim()
            if ($LASTEXITCODE -ne 0 -or $Version -ne "0.1.3") {
                throw "staging verification failed"
            }
            if (-not (Test-Path -LiteralPath (Join-Path $Live "bin\apprestore.cmd"))) {
                throw "old command disappeared before the swap"
            }
        } `
        -VerifyInstallation { param($Path) throw "forced live verification failure" }
    throw "transaction unexpectedly succeeded"
}
catch {
    if ($_.Exception.Message -notmatch "forced live verification failure") {
        throw
    }
}
$OldCommand = Join-Path $Live "bin\apprestore.cmd"
$OldVersion = ((& $OldCommand "--version") | Out-String).Trim()
if ($LASTEXITCODE -ne 0 -or $OldVersion -ne "0.1.2") {
    throw "old command/version was not restored: '$OldVersion'"
}
if (Test-Path -LiteralPath $Staging) {
    throw "staging remained after rollback"
}
if (Test-Path -LiteralPath $Backup) {
    throw "backup remained after successful rollback"
}
Write-Output "LIVE_ROLLBACK_OK"
""",
                )
                self.assertEqual(
                    result.returncode,
                    0,
                    result.stdout + result.stderr,
                )
                self.assertIn("LIVE_ROLLBACK_OK", result.stdout)

    def test_real_v013_tag_fixture_is_accepted_for_one_time_upgrade(
        self,
    ) -> None:
        hosts = _powershell_hosts()
        if not hosts:
            self.skipTest("PowerShell is not available")

        for host in hosts:
            with (
                self.subTest(host=Path(host).name),
                tempfile.TemporaryDirectory(
                    prefix="apprestore-transaction-legacy-"
                ) as directory,
            ):
                programs = Path(directory) / "Programs"
                live = programs / "AppRestore"
                staging = programs / "AppRestore.staging-test"
                self._write_legacy_v013_install(live)
                self._write_managed_install(staging, "0.1.3")

                result = _run_transaction_probe(
                    host,
                    Path(directory),
                    r"""
$Programs = Join-Path $env:APPRESTORE_TRANSACTION_ROOT "Programs"
$Live = Join-Path $Programs "AppRestore"
$Staging = Join-Path $Programs "AppRestore.staging-test"
$Backup = Join-Path $Programs "AppRestore.backup-test"
Invoke-AppRestoreInstallTransaction `
    -StagingRoot $Staging `
    -InstallRoot $Live `
    -BackupRoot $Backup `
    -ManagedMarkerName ".apprestore-managed" `
    -ManagedMarkerValue "AppRestore managed installation v1" `
    -PrepareStaging { param($Path) } `
    -VerifyStaging { param($Path) } `
    -VerifyInstallation { param($Path) }
$Version = ((& (Join-Path $Live "bin\apprestore.cmd") "--version") |
    Out-String).Trim()
if ($LASTEXITCODE -ne 0 -or $Version -ne "0.1.3") {
    throw "new command did not replace the legacy fixture"
}
if (Test-Path -LiteralPath $Backup) {
    throw "legacy backup was not cleaned"
}
Write-Output "LEGACY_V013_ACCEPTED_OK"
""",
                )
                self.assertEqual(
                    result.returncode,
                    0,
                    result.stdout + result.stderr,
                )
                self.assertIn("LEGACY_V013_ACCEPTED_OK", result.stdout)

    def test_backup_cleanup_failure_is_nonfatal_after_verified_commit(
        self,
    ) -> None:
        hosts = _powershell_hosts()
        if not hosts:
            self.skipTest("PowerShell is not available")

        for host in hosts:
            with (
                self.subTest(host=Path(host).name),
                tempfile.TemporaryDirectory(
                    prefix="apprestore-transaction-cleanup-"
                ) as directory,
            ):
                programs = Path(directory) / "Programs"
                live = programs / "AppRestore"
                staging = programs / "AppRestore.staging-test"
                self._write_managed_install(live, "0.1.2")
                self._write_managed_install(staging, "0.1.3")

                result = _run_transaction_probe(
                    host,
                    Path(directory),
                    r"""
function Remove-Item {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $true)][string]$LiteralPath,
        [switch]$Recurse,
        [switch]$Force
    )
    if ([IO.Path]::GetFileName($LiteralPath).StartsWith(
        "AppRestore.backup-",
        [StringComparison]::Ordinal
    )) {
        throw "forced backup cleanup failure"
    }
    Microsoft.PowerShell.Management\Remove-Item `
        -LiteralPath $LiteralPath `
        -Recurse:$Recurse `
        -Force:$Force
}
$Programs = Join-Path $env:APPRESTORE_TRANSACTION_ROOT "Programs"
$Live = Join-Path $Programs "AppRestore"
$Staging = Join-Path $Programs "AppRestore.staging-test"
$Backup = Join-Path $Programs "AppRestore.backup-test"
Invoke-AppRestoreInstallTransaction `
    -StagingRoot $Staging `
    -InstallRoot $Live `
    -BackupRoot $Backup `
    -ManagedMarkerName ".apprestore-managed" `
    -ManagedMarkerValue "AppRestore managed installation v1" `
    -PrepareStaging { param($Path) } `
    -VerifyStaging { param($Path) } `
    -VerifyInstallation { param($Path) }
$Version = ((& (Join-Path $Live "bin\apprestore.cmd") "--version") |
    Out-String).Trim()
if ($LASTEXITCODE -ne 0 -or $Version -ne "0.1.3") {
    throw "verified new command was not retained"
}
if (-not (Test-Path -LiteralPath $Backup -PathType Container)) {
    throw "failed backup cleanup did not preserve the backup"
}
Write-Output "BACKUP_CLEANUP_NONFATAL_OK"
""",
                )
                self.assertEqual(
                    result.returncode,
                    0,
                    result.stdout + result.stderr,
                )
                self.assertIn("BACKUP_CLEANUP_NONFATAL_OK", result.stdout)

    def test_unowned_directory_is_never_replaced(self) -> None:
        hosts = _powershell_hosts()
        if not hosts:
            self.skipTest("PowerShell is not available")

        for host in hosts:
            with (
                self.subTest(host=Path(host).name),
                tempfile.TemporaryDirectory(
                    prefix="apprestore-transaction-unowned-"
                ) as directory,
            ):
                programs = Path(directory) / "Programs"
                live = programs / "AppRestore"
                staging = programs / "AppRestore.staging-test"
                for relative in (
                    "apprestore.ps1",
                    "uninstall-windows.ps1",
                    "src/apprestore.py",
                    "src/apprestore_core/__init__.py",
                    "src/pyproject.toml",
                    "bin/apprestore.cmd",
                    "bin/ipatool.exe",
                    ".venv/Scripts/python.exe",
                    ".venv/Scripts/apprestore.exe",
                ):
                    fake_file = live / Path(relative)
                    fake_file.parent.mkdir(parents=True, exist_ok=True)
                    fake_file.write_text("presence-only fake", encoding="utf-8")
                (live / "private.txt").write_text("do not delete", encoding="utf-8")
                staging.mkdir()

                result = _run_transaction_probe(
                    host,
                    Path(directory),
                    r"""
$Programs = Join-Path $env:APPRESTORE_TRANSACTION_ROOT "Programs"
$Live = Join-Path $Programs "AppRestore"
$Staging = Join-Path $Programs "AppRestore.staging-test"
$Backup = Join-Path $Programs "AppRestore.backup-test"
$Rejected = $false
try {
    Invoke-AppRestoreInstallTransaction `
        -StagingRoot $Staging `
        -InstallRoot $Live `
        -BackupRoot $Backup `
        -ManagedMarkerName ".apprestore-managed" `
        -ManagedMarkerValue "AppRestore managed installation v1" `
        -PrepareStaging { param($Path) } `
        -VerifyStaging { param($Path) } `
        -VerifyInstallation { param($Path) }
    throw "transaction unexpectedly succeeded"
}
catch {
    $Rejected = $true
}
if (-not $Rejected) {
    throw "unowned directory was not refused"
}
if ((Get-Content -LiteralPath (Join-Path $Live "private.txt") -Raw) -ne
    "do not delete") {
    throw "unowned directory was changed"
}
if (Test-Path -LiteralPath $Backup) {
    throw "unowned directory was moved"
}
Write-Output "UNOWNED_REFUSED_OK"
""",
                )
                self.assertEqual(
                    result.returncode,
                    0,
                    result.stdout + result.stderr,
                )
                self.assertIn("UNOWNED_REFUSED_OK", result.stdout)

    def test_regular_file_at_install_path_is_never_replaced(self) -> None:
        hosts = _powershell_hosts()
        if not hosts:
            self.skipTest("PowerShell is not available")

        for host in hosts:
            with (
                self.subTest(host=Path(host).name),
                tempfile.TemporaryDirectory(
                    prefix="apprestore-transaction-file-"
                ) as directory,
            ):
                programs = Path(directory) / "Programs"
                programs.mkdir()
                live = programs / "AppRestore"
                staging = programs / "AppRestore.staging-test"
                live.write_text("do not delete", encoding="utf-8")
                staging.mkdir()

                result = _run_transaction_probe(
                    host,
                    Path(directory),
                    r"""
$Programs = Join-Path $env:APPRESTORE_TRANSACTION_ROOT "Programs"
$Live = Join-Path $Programs "AppRestore"
$Staging = Join-Path $Programs "AppRestore.staging-test"
$Backup = Join-Path $Programs "AppRestore.backup-test"
$Rejected = $false
try {
    Invoke-AppRestoreInstallTransaction `
        -StagingRoot $Staging `
        -InstallRoot $Live `
        -BackupRoot $Backup `
        -ManagedMarkerName ".apprestore-managed" `
        -ManagedMarkerValue "AppRestore managed installation v1" `
        -PrepareStaging { param($Path) } `
        -VerifyStaging { param($Path) } `
        -VerifyInstallation { param($Path) }
    throw "transaction unexpectedly succeeded"
}
catch {
    $Rejected = $true
}
if (-not $Rejected) {
    throw "regular file at reserved path was not refused"
}
if ((Get-Content -LiteralPath $Live -Raw) -ne "do not delete") {
    throw "regular file at the reserved path was changed"
}
if (Test-Path -LiteralPath $Backup) {
    throw "regular file at the reserved path was moved"
}
Write-Output "REGULAR_FILE_REFUSED_OK"
""",
                )
                self.assertEqual(
                    result.returncode,
                    0,
                    result.stdout + result.stderr,
                )
                self.assertIn("REGULAR_FILE_REFUSED_OK", result.stdout)


@unittest.skipUnless(os.name == "nt", "Windows launcher contract")
class WindowsLauncherContractTests(unittest.TestCase):
    def test_successful_installer_is_not_poisoned_by_stale_last_exit_code(
        self,
    ) -> None:
        hosts = _powershell_hosts()
        if not hosts:
            self.skipTest("PowerShell is not available")

        with tempfile.TemporaryDirectory(prefix="apprestore-launcher-") as directory:
            test_root = Path(directory)
            source_root = test_root / "source"
            local_app_data = test_root / "profile" / "AppData" / "Local"
            source_root.mkdir(parents=True)
            local_app_data.mkdir(parents=True)

            launcher = source_root / "apprestore.ps1"
            launcher_text = (ROOT / "apprestore.ps1").read_text(encoding="utf-8")
            known_folder_block = """$KnownLocalAppData = [Environment]::GetFolderPath(
    [Environment+SpecialFolder]::LocalApplicationData
)
if ([string]::IsNullOrWhiteSpace($KnownLocalAppData)) {
    throw "Windows Known Folder LocalApplicationData недоступен."
}
$KnownLocalAppData = [System.IO.Path]::GetFullPath($KnownLocalAppData)
"""
            test_folder_block = (
                "# Test-only Known Folder isolation.\n"
                "$KnownLocalAppData = "
                "[System.IO.Path]::GetFullPath($env:LOCALAPPDATA)\n"
            )
            self.assertEqual(launcher_text.count(known_folder_block), 1)
            launcher.write_text(
                launcher_text.replace(known_folder_block, test_folder_block),
                encoding="utf-8",
            )
            (source_root / "install-windows.ps1").write_text(
                r"""
Set-StrictMode -Version Latest
$entryPoint = Join-Path $env:LOCALAPPDATA `
    "Programs\AppRestore\bin\apprestore.cmd"
New-Item -ItemType Directory -Path (Split-Path $entryPoint) -Force |
    Out-Null
[System.IO.File]::WriteAllText(
    $entryPoint,
    "@echo off`r`nexit /b 0`r`n"
)
""".lstrip(),
                encoding="utf-8",
            )

            command = r"""
$global:LASTEXITCODE = 1060
& $env:APPRESTORE_TEST_LAUNCHER `
    -AppRestoreArguments @("/d", "/c", "exit 0")
"""
            for host in hosts:
                with self.subTest(host=Path(host).name):
                    environment = os.environ.copy()
                    environment["LOCALAPPDATA"] = str(local_app_data)
                    environment["APPRESTORE_TEST_LAUNCHER"] = str(launcher)
                    result = subprocess.run(
                        [
                            host,
                            "-NoLogo",
                            "-NoProfile",
                            "-NonInteractive",
                            "-ExecutionPolicy",
                            "Bypass",
                            "-Command",
                            command,
                        ],
                        capture_output=True,
                        text=True,
                        encoding="utf-8",
                        errors="replace",
                        env=environment,
                        timeout=30,
                        check=False,
                    )
                    self.assertEqual(
                        result.returncode,
                        0,
                        (
                            "launcher treated a successful PowerShell installer "
                            "as native exit code 1060\n"
                            f"stdout:\n{result.stdout}\n"
                            f"stderr:\n{result.stderr}"
                        ),
                    )

    def test_installer_adds_command_to_current_process_path(self) -> None:
        installer = (ROOT / "install-windows.ps1").read_text(encoding="utf-8")
        self.assertIn(
            "$ProcessContainsBin",
            installer,
            "installer must de-duplicate the current process PATH",
        )
        self.assertRegex(
            installer,
            re.compile(
                r'\$env:Path\s*=\s*\$BinTarget\s*\+\s*";"\s*\+\s*\$env:Path',
                re.IGNORECASE,
            ),
            (
                "after installation, `apprestore` must be available in the "
                "same PowerShell process"
            ),
        )


class MenuStartupContractTests(unittest.TestCase):
    def test_opening_menu_never_runs_dependency_setup(self) -> None:
        class RecordingTools:
            def __init__(self) -> None:
                self.ensure_calls = 0

            def windows_bridge_ready(self) -> bool:
                return False

            def ensure_windows_bridge(self) -> list[str]:
                self.ensure_calls += 1
                return ["must not be called while opening the menu"]

        class MenuService:
            def __init__(self) -> None:
                self.tools = RecordingTools()

        service = MenuService()

        def menu_input(prompt: str = "") -> str:
            return "0" if "Выбор" in prompt else ""

        stdout = io.StringIO()
        stderr = io.StringIO()
        with (
            patch("apprestore_core.cli.sys.platform", "win32"),
            patch("apprestore_core.cli._clear_screen"),
            patch("builtins.input", side_effect=menu_input),
            contextlib.redirect_stdout(stdout),
            contextlib.redirect_stderr(stderr),
        ):
            result = cli._run_menu(service)  # type: ignore[arg-type]

        self.assertEqual(result, 0)
        self.assertEqual(
            service.tools.ensure_calls,
            0,
            (
                "opening `apprestore` must show the menu first; dependency "
                "installation belongs to explicit `apprestore setup`/menu item 9"
            ),
        )
        self.assertIn(r"/_/   \_\ .__/| .__/", stdout.getvalue())
        self.assertIn("Телефон → сгруженные приложения", stdout.getvalue())
        self.assertNotIn("Traceback", stderr.getvalue())


if __name__ == "__main__":
    unittest.main()
