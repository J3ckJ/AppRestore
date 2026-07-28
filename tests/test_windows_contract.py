from __future__ import annotations

import ast
import contextlib
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
EXPECTED_VERSION = "0.1.3"


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


class VersionContractTests(unittest.TestCase):
    def test_all_release_versions_are_0_1_3(self) -> None:
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
            shutil.copy2(ROOT / "apprestore.ps1", launcher)
            (source_root / "install-windows.ps1").write_text(
                r"""
Set-StrictMode -Version Latest
$entryPoint = Join-Path $env:LOCALAPPDATA `
    "Programs\AppRestore\.venv\Scripts\apprestore.exe"
New-Item -ItemType Directory -Path (Split-Path $entryPoint) -Force |
    Out-Null
Copy-Item `
    -LiteralPath (Join-Path $env:SystemRoot "System32\cmd.exe") `
    -Destination $entryPoint `
    -Force
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
