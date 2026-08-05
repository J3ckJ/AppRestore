from __future__ import annotations

import ast
import base64
import contextlib
import hashlib
import io
import os
import re
import shutil
import subprocess
import tempfile
import time
import unittest
import zlib
from pathlib import Path
from unittest.mock import patch

from apprestore_core import __version__
from apprestore_core import cli


ROOT = Path(__file__).resolve().parents[1]
EXPECTED_VERSION = "0.2.0"

# Exact bytes from the immutable v0.1.3 release, compressed so the release
# source archive can verify and exercise the one-time legacy upgrade without
# depending on a Git checkout or historical tags.
_LEGACY_V013_COMPRESSED = {
    "apprestore.ps1": """
        eNrdVu9qG0cQ/35PMVxEkIrvsDGB4OBQxZETFcUSPjVpsU17ldfWwWn3srcXR5SAk1Bov9S09GtoodDPbmIliu3a
        r7B6hbxAX6GzuzrpTv4XN4VCBIK73dm5md/85rfz98HxFU4eJgEnMTj3CY8DRuGaO2OtLHTWQyJuBXQ9oJvF0poV
        +dzvFC3A30pDPRNBePG+HyZkkbPOMun4AUXbMt9MOoSKGOahIHhC8Kw+FAuO2ytra4VyFC2TWDBORsZWybI8IhwP
        jVriHlsn43hqvkBrq1DhnPFyS+Bag5MNwgltEfyK7QkW2ZZ1BeTPMHg+eDp4JnflX/JIvpQHsodP6vkt4GtvsD14
        KvuyD0HkC8ZC0BavZX/wDHBvR+6h6eEU2oLcl7va0wEuvZI9+CagrlW4lVBEZh2RwU9/xgLqNHzRhkLD81o8iMQy
        YwJstLWtYAOKTQzdWDi1ABHzQ2OecaO3m92IwAKjAlEkvATfatAKhD6a0wcwzcyZG6MN23pimZcvm3frS583F68r
        2xk7u1qtV5YW6rerS3fUXiI2nOuIlwpvWJW1ublqvJSEYZ0/aGOYXuS3SFF7qNUXyrVyo3G73CyX0rhEm7MtsOWv
        ClEE8HAI8+5gB7IHANd6gPgda7M9/JuK7LoqcKtQpbHwQ0xKwzYPK143FqTjVuuuyg7jukPEIgam3gz7ihnQJwME
        u8HZJpIzXh2TzC4hu8ZfqlDBuw30IfIFzIdiu4/Q+aqpabzqRxE33lzymCC4Ndbywwt95klxgcvUGz/PR2CMnC3s
        TLYVu1GMpcbeyAZQoAjYuew7M/oxF2vE3xjRMO/+rNPG9hINouw/vEnet1GU3RPNuuWEKqlSuuWxhLdIkxOlJGfF
        Ma5MHh+NsUYbHPIwB5Pj03U4/UNp3A84fsO5y2KEQ/6eky2UHkCZOkChwvc/c2LWx44ak/vd9h8ms6uZMEfAOhSx
        Ll6U1jkEyGKcNn0u1MEPGNA+yDe4YPT1exU4muxMqe4/UjqKkqrt9zCtcehDcUAxkG+1NBhZMGU6hXaXZtxER/+P
        lCNhTC7gyn9RpY8HsGxnUZJNLPWIt/1PyJk3gJfOtnw1eG4uIWQmshLebf+iGki9qhtqV+7jUx8ZqkiLpDO2P0JR
        tRryNyPGcVtzEjp+q+6V3LS5MtB+etoAo8zI40CgPJa9ZuWLahNv3IpKBSP9zQwZ8tA0yrGaUkyTYwvIY3mE/aIv
        SN1aL9OhRaWACfZx9TuMe093/75OEEVBJa+vFJxKjLq83zWUyTXqminlfJ044fwM+qUC8WKirTM9P6c2+mdNaWp8
        yMvFlLHPhTwcHRpd0Wa05ie01dZ3Jk4KzgLrdFQ32WimblVwMlMjeEGItQq7ip4BTcgky/Iu07SuTm64BhBwZsFp
        wdcjgttBJ2JcQNyNbwD3g5iAGWcqyIvitGoj3MKS6bn2q4BuMLg5D8XZKZiZLoESCZgp2RmPn9wcXuZpF+bIpYOe
        Pk2iX2A7vNbQm8hh1p2ZHt0oBvSe7OXU9rw0T1Lgkj0wdH2ySmr1X1fqRIVGIX+EZUlz++BaXDYamBhO+kpU0zPK
        9FgPAii8qGFmM6tyrm39A58ojbw=
    """,
    "uninstall-windows.ps1": """
        eNq1V91uGkcUvt+nGG1QAxJL24veOKoUiklN6wQEJKlkR9EaBnvbZZfMDk6iylJiN00qp7Ei9aoX7UVfgLhBJrHB
        r7D7JH2FnjOzP7Ms2EmlgvjZmZ0z5+c733z7z+n5FUYfDC1GPWLcocyzXId8Ufpc26j0uzblX1lO13K2863hYOAy
        7rV23KHdbTC3Qz2PfElynA1pkVRcp2exfq0/MDschvU1a3tHL9zTBiYz+3mNwGvDe2jxzs69XGPItultj7JVk5ta
        QdNalBstzqwOv+l2aeLHusmpx7VclTGXlTscxhqM9iijTofiLi3uDnRNs3okv+GBAWf73spKzbs1tO06u7tjcdoC
        h2g+R53dlfV6pbxebjRWy+1yoUB+FE7xHeY+JLr/pz8Onvhj/ww+U3iPgiOiLiAwNib+zD8Xt72Fz6m4dVTStT1N
        y9Ucj5u23XRdjH+j9djjtF+q1UsNk++AV19TfgPcwiuZjvw3ruUYeE0y7hEdMrwNmfM2y4NBE5LgMqoXIFe56qMB
        7XDa/b83wpwaDuyRJLb6YGjanjSqxluUI6pncihyriUsVFxAB7M81wFbdQawMu3atgMbVkyPapmS/BHs++/9kX+y
        QoKfMd/BSxIcQOpHYeonUCNRluApFObYP4cZnB0H+zBEgp/8if8e5iZQMpwfYWWDQ/+MoF2wjoZm/t/wOSNJ+LKg
        vaEjAEfKHiCVG7dc3qTgv0cbkE4e+qrAWwTcwGvKKcvfNJ2uCeYeR00CzRDfFqY0h0URo9AE+INJb4MXslrGOgCY
        mbYsHX5HKZIVgNSCcai3If5mbyfGDZd1aLwEzefFulKZgwtbQ45tvwWuqkC6Ydk0mYdiqYEXiOFQ8pnqycKiQa9g
        GebrFTyF9yEMvPcnBOpzCj+wZBY8lyPnWCkx8ARWzPx32HNv4GLkv0GLeNeKjE6PHdjT5DeUbUm1QvgroNVyQG1t
        E5gI20hpEbWR9S3L0bVc2CPVR5bHkfSWlShlvkn77i4NlyIIetA8VMvdHgAuqFgQD2Jh0rtE+c2aabQkM5dSVJxP
        NSTR48RPgn3om1RzC7tpN1K7QB4peBV6cIUAOc6gjU6x054EB1CqqWybBe2YbqwR8U/g4hzmnoIXjXJ7jYhaT4Jf
        ZKPC+JGAiqTU4GXUoXj5Dmb3oXEnSMylJfnA3loU0pI8xaDRBdZwxxPJDrCV8ABCBZAB8tBdvZgsgGgFEkVGMSUi
        0uPgBd4tA0mFr2R9UwBJdvqeFtZbPQbjetcGZtt17TQmE6jq1gBYxbVL9BHVL+WMyJiYaz8eULJOzZ7au9KTS1IV
        Rh88D15DdFigMRGFn4YXoVNKsmSGsa4zyO9xlDTMC9KDLPRB8IqIDMp+nyBYgOonMAypsymprSZNXpinnE+S+Mwh
        RMvorvsDTd0iolsvt9rV72rtSn21upi88HWXQeKMuyZzgJnjNIfHPiLlrYAiqoS3yFsStCcQ/vHCGPzJNSLEwkyu
        Alzto6mxbJxngtKmkgGhGWDmOGouzEtJT3m4t4jrcgieEO4bVWfXYq7Tpw6XIkAZuAPHrrll07wueBPoAVfq8pBX
        28bAkyDngHwQiYo3iNEJQIQjJTnJsdFicJbazOpXnW5e34xY5ls64C26jU4gcV5PMNWDrjA7O7B/OE8sB52JQjK8
        gW0B3q/pmcLnKjY1nWgZeBD+FfvnC/Lnqn61kHVIBcYF6mbpbsXs9HxKsrd8lAxSFxYWATWK9kJ8pEi+q+Akny6J
        8T2QjEiyzBr+0z4wQfPWi8pMZujyHGiZiOcw3boY0xl3YpTHDZM9ZkPEpw6VyAU5uEhbqRLBaNLOEHSGqrUkl6y5
        HuA3OQPi0zJ47U9LREolQRDvJDVMhfY5hJMHr2HmDOhhiiuKBLh3XyihQyGI5L3IHuLcwZNKyFY8t5eLCdUv/y/1
        6IbtFUcFtcknIfzMri04xlFzxyyWPAgpwmFJFqRiPwhPTxmH8AIYMJVaqcMvOCpXac8c2hwOgXVri5lCan/A89D8
        M9Fa/WZV9XATuN8LYVNI7VQBwqL/bZPsg1dGksnvhBejeMOTH/r0ej4bczHtXaqBLhbDafspRZDilqXqIm2gIHop
        TdQfIixiwkhMZSlUB8iPhZAQckKqMAQPmRO6l4i6Od2i1CBNvInqmGPg5aQwV65FvKCe3ws0h/97SjyL56MTAoBp
        VlvterN6v9Yo31+tNT9VhirlyloVB2MRAo2LViagJMYy6qjlQvY5Cl5J3X1Rw8JWRZCzwa/BC4IWFqm0o0j3Zcgg
        OAz1S4pxfsPNwVrwjARHUJzlTxHAM0khYVoSpJGiAeH9v6cQXEk=
    """,
    "apprestore.py": """
        eNoVyTEOgzAMQNHdpzB0gaUZulVi5AQcwEqRUS3VTuSYqty+8Ie/vFuX9ubpJZbYvliPeBd7wOZFMdfq3KI403ru
        vn4ERWvxQM1iACAbEllWJsJpwp7oAqL+CXjmWRrjcrRgnX8Sw6XDOMIfds8nZQ==
    """,
    "apprestore_core/__init__.py": """
        eNpTUlJyLsovLtYtyEksScsvylVIzi9KVQCyFBwLCoJSi0uAXD0lJSUurvj4stSi4sz8vPh4BVsFJQM9Qz1jJS4A
        zrEUPQ==
    """,
    "pyproject.toml": """
        eNpdkVFLwzAUhd/zK0IexYaOPgyEDkSLDKaO7cGHUkaW3Lpo28Qk3dy/92aRbdqHQO85OffeL/V21J3K/NEH6Bvi
        4GvUDjwtac08hNEGYzo/K6dTnvOcNST5t0J+wqDQduXiJ23TQxCMkNo68wEyNGQQPUSnsBajg3HAyB6c12aI5ZxP
        eMGIAi+dtuG3ukpOatq2M0KBovp1TTGi01JEk6etMz3FIN1qlOfLe9rqDmfHgF7I6MYJ3/SgzMFzhrsJlQZZVfeP
        zxXvFTsvnNlj2KXOs7Lgk5wRbASDP114Wi6ygueZcVknArizmKWOkdZi/lC9rCtEpMAiGxikThKh+DF77M0W3Qr2
        eLcoy0lcPGe3pLnA4omBb8gF1l90G4kHl52+64UeIufInl+9gsW3Ee/geYurN+Swg1NGzTjOpgfZjSr9/wu9QRm+
        L3JAzcfiD7iTtkE=
    """,
}


def _legacy_v013_bytes(path: str) -> bytes:
    encoded = "".join(_LEGACY_V013_COMPRESSED[path].split())
    return zlib.decompress(base64.b64decode(encoded, validate=True))


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
$functionNames = @(
    "Assert-AppRestorePlainTree",
    "Assert-AppRestoreManagedInstall",
    "Assert-AppRestoreRecoverableInstall",
    "Invoke-AppRestoreBackupRecovery",
    "Invoke-AppRestoreInstallTransaction"
)
foreach ($functionName in $functionNames) {
    $functionAst = $ast.Find(
        {
            param($Node)
            $Node -is [System.Management.Automation.Language.FunctionDefinitionAst] -and
                $Node.Name -eq $functionName
        },
        $true
    )
    if ($null -eq $functionAst) {
        throw "Installer function was not found: $functionName"
    }
    Invoke-Expression $functionAst.Extent.Text
}
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

    def test_installer_python_discovery_never_trusts_caller_path(self) -> None:
        installer = (ROOT / "install-windows.ps1").read_text(encoding="utf-8")
        compatible = installer.split(
            "function Find-CompatiblePython {",
            1,
        )[1].split("\n}", 1)[0]
        fallback = installer.split(
            "function Install-CompatiblePython {",
            1,
        )[1].split("\n}", 1)[0]

        self.assertNotIn("Get-Command", compatible)
        self.assertNotIn("$env:Path", compatible)
        self.assertNotIn('"py.exe"', compatible)
        self.assertIn(
            '$KnownPythonRoot = Join-Path $KnownLocalAppData "Programs\\Python"',
            compatible,
        )
        self.assertIn("Assert-PlainDirectory", compatible)
        self.assertIn("[System.IO.FileAttributes]::ReparsePoint", compatible)
        self.assertIn("$Winget = Find-TrustedWinget", fallback)
        self.assertIn("Invoke-WebRequest", fallback)
        self.assertIn("$PythonInstallerSha256", fallback)
        self.assertIn("Get-FileHash", fallback)
        self.assertIn('"TargetDir=$PythonTarget"', fallback)

        selection = "$SelectedPython = Find-CompatiblePython"
        install = "Install-CompatiblePython"
        first_selection = installer.index(selection)
        self.assertGreater(installer.index(install, first_selection), first_selection)
        self.assertGreater(installer.rindex(selection), installer.index(install))

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
            (
                r'"%~dp0..\.venv\Scripts\python.exe" '
                r'-X utf8 -I -m apprestore_core.cli %*'
            ),
            payload,
        )
        command_wrapper = payload.rsplit("$CommandWrapper = @'", 1)[1].split(
            "'@",
            1,
        )[0]
        for variable in ("PYTHONPATH", "PYTHONHOME", "PYTHONSTARTUP"):
            self.assertIn(f'set "{variable}="', command_wrapper)
            self.assertIn(f'"{variable}"', launcher)
        self.assertIn(" -I -m apprestore_core.cli %*", command_wrapper)
        self.assertNotIn("%PYTHONPATH%", command_wrapper)
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

    def test_winget_is_resolved_from_a_verified_microsoft_package(self) -> None:
        installer = (ROOT / "install-windows.ps1").read_text(encoding="utf-8")
        trusted_resolver = installer.split(
            "function Find-TrustedWinget {",
            1,
        )[1].split("\n}", 1)[0]

        self.assertIn("RegistryHive]::LocalMachine", trusted_resolver)
        self.assertIn("RegistryView]::Registry64", trusted_resolver)
        self.assertIn('"ProgramFilesDir"', trusted_resolver)
        self.assertIn(
            '(Join-Path $ProgramFilesRoot "WindowsApps")',
            trusted_resolver,
        )
        self.assertIn(
            "RegistryHive]::CurrentUser",
            trusted_resolver,
        )
        self.assertIn(
            "CurrentVersion\\AppModel\\Repository\\Packages",
            trusted_resolver,
        )
        self.assertIn(
            '"^Microsoft\\.DesktopAppInstaller_"',
            trusted_resolver,
        )
        self.assertIn('"Microsoft.DesktopAppInstaller"', trusted_resolver)
        self.assertIn(
            "Microsoft.PowerShell.Security\\Get-AuthenticodeSignature",
            trusted_resolver,
        )
        self.assertIn("SignatureStatus]::Valid", trusted_resolver)
        self.assertIn("$Identity.Publisher", trusted_resolver)
        self.assertIn("$Signature.SignerCertificate.Subject", trusted_resolver)
        self.assertEqual(
            installer.count("$Winget = Find-TrustedWinget"),
            2,
            "Python and Apple dependencies must use the same trusted resolver",
        )

    def test_installer_never_elevates_or_uses_path_resolved_system_tools(
        self,
    ) -> None:
        installer = (ROOT / "install-windows.ps1").read_text(encoding="utf-8")

        self.assertNotIn('Get-Command "winget.exe"', installer)
        self.assertNotIn("$Winget.Source", installer)
        self.assertNotRegex(installer, r"(?i)-Verb\s+RunAs\b")
        self.assertNotRegex(installer, r"(?im)^\s*&\s*sc\.exe\b")
        self.assertIn(
            "Microsoft.PowerShell.Management\\Get-Service",
            installer,
        )
        self.assertIn(
            "Microsoft.PowerShell.Management\\Start-Service",
            installer,
        )

    def test_installer_holds_a_per_user_mutex_for_the_full_install(self) -> None:
        installer = (ROOT / "install-windows.ps1").read_text(encoding="utf-8")

        mutex = '$InstallerMutexName = "Local\\AppRestore.Install."'
        early_recovery = "Invoke-AppRestoreBackupRecovery `"
        selected_python = "$SelectedPython = Find-CompatiblePython"
        transaction = "Invoke-AppRestoreInstallTransaction `"
        release = "$InstallerMutex.ReleaseMutex()"
        self.assertIn(mutex, installer)
        self.assertIn("AbandonedMutexException", installer)
        self.assertIn("-InstallMutexAlreadyHeld", installer)
        self.assertIn("-BackupRecoveryAlreadyPerformed", installer)
        self.assertLess(installer.index(mutex), installer.index(early_recovery))
        self.assertLess(
            installer.index(early_recovery),
            installer.index(selected_python),
        )
        self.assertLess(installer.index(selected_python), installer.index(transaction))
        self.assertLess(installer.index(transaction), installer.rindex(release))

    def test_recursive_temp_cleanup_is_parent_and_reparse_confined(self) -> None:
        scripts = {
            "payload": (ROOT / "install-windows.ps1").read_text(
                encoding="utf-8"
            ),
            "bootstrap": (ROOT / "scripts" / "install.ps1.in").read_text(
                encoding="utf-8"
            ),
        }
        for name, script in scripts.items():
            with self.subTest(script=name):
                self.assertIn("$TempParent", script)
                self.assertIn("$SystemTempRoot", script)
                self.assertIn("$NestedTempReparse", script)
                self.assertIn(
                    "[System.IO.FileAttributes]::ReparsePoint",
                    script,
                )
                self.assertIn("-ErrorAction Stop", script)
                self.assertIn(
                    '$TempSuffix -match "^[0-9a-fA-F]{32}$"',
                    script,
                )
                self.assertIn(
                    "Remove-Item -LiteralPath $ResolvedTempRoot -Recurse -Force",
                    script,
                )
    def test_python_dependencies_are_hash_locked_and_local_build_is_offline(
        self,
    ) -> None:
        installer = (ROOT / "install-windows.ps1").read_text(encoding="utf-8")

        self.assertIn('"requirements\\runtime.lock"', installer)
        self.assertIn('"requirements\\build.lock"', installer)
        self.assertIn(
            '"requirements\\wheels\\hexdump-3.3-py3-none-any.whl"',
            installer,
        )
        self.assertGreaterEqual(installer.count("--require-hashes"), 2)
        self.assertGreaterEqual(installer.count("--only-binary=:all:"), 2)
        self.assertIn("--no-build-isolation", installer)
        self.assertIn("--no-index", installer)
        self.assertIn("--no-deps", installer)

    def test_python_314_is_not_selected_as_a_supported_runtime(self) -> None:
        installer = (ROOT / "install-windows.ps1").read_text(encoding="utf-8")
        compatible = installer.split(
            "function Find-CompatiblePython {",
            1,
        )[1].split("\n}", 1)[0]

        self.assertIn("(3, 10) <= sys.version_info < (3, 14)", compatible)

    def test_source_launcher_supports_only_python_310_through_313(self) -> None:
        launcher = (ROOT / "apprestore.ps1").read_text(encoding="utf-8")

        self.assertIn("(3, 10) <= sys.version_info < (3, 14)", launcher)
        self.assertIn('@("-3.13", "-3.12", "-3.11", "-3.10")', launcher)
        self.assertIn("Python версии 3.10–3.13", launcher)
        self.assertNotIn("sys.version_info >= (3, 10)", launcher)

    def test_legacy_v013_fingerprint_is_bound_to_real_tag_bytes(self) -> None:
        expected_paths = (
            "apprestore.ps1",
            "uninstall-windows.ps1",
            "apprestore.py",
            "apprestore_core/__init__.py",
            "pyproject.toml",
        )
        expected_hashes = {
            path: hashlib.sha256(_legacy_v013_bytes(path)).hexdigest()
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
        runtime = root / ".venv" / "Scripts"
        runtime.mkdir(parents=True)
        (runtime / "python.exe").write_bytes(b"managed python fixture")

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
            target.write_bytes(_legacy_v013_bytes(tagged_path))
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

    def test_early_recovery_survives_a_failing_prerequisite(self) -> None:
        hosts = _powershell_hosts()
        if not hosts:
            self.skipTest("PowerShell is not available")

        for host in hosts:
            with (
                self.subTest(host=Path(host).name),
                tempfile.TemporaryDirectory(
                    prefix="apprestore-early-recovery-"
                ) as directory,
            ):
                programs = Path(directory) / "Programs"
                backup = programs / "AppRestore.backup-crashed"
                self._write_managed_install(backup, "0.1.2")

                result = _run_transaction_probe(
                    host,
                    Path(directory),
                    r"""
$Programs = Join-Path $env:APPRESTORE_TRANSACTION_ROOT "Programs"
$Live = Join-Path $Programs "AppRestore"
$CrashedBackup = Join-Path $Programs "AppRestore.backup-crashed"
$FailingPrerequisite = {
    throw "intentional prerequisite failure"
}
try {
    Invoke-AppRestoreBackupRecovery `
        -InstallRoot $Live `
        -ManagedMarkerName ".apprestore-managed" `
        -ManagedMarkerValue "AppRestore managed installation v1"
    & $FailingPrerequisite
    throw "failing prerequisite unexpectedly succeeded"
}
catch {
    if ($_.Exception.Message -ne "intentional prerequisite failure") {
        throw
    }
}
if (-not (Test-Path -LiteralPath $Live -PathType Container)) {
    throw "live install was still missing after prerequisite failure"
}
$Version = ((& (Join-Path $Live "bin\apprestore.cmd") "--version") |
    Out-String).Trim()
if ($LASTEXITCODE -ne 0 -or $Version -ne "0.1.2") {
    throw "recovered live install is not runnable"
}
if (Test-Path -LiteralPath $CrashedBackup) {
    throw "backup was not atomically promoted to live"
}
Write-Output "EARLY_RECOVERY_BEFORE_PREREQUISITE_OK"
""",
                )
                self.assertEqual(
                    result.returncode,
                    0,
                    result.stdout + result.stderr,
                )
                self.assertIn(
                    "EARLY_RECOVERY_BEFORE_PREREQUISITE_OK",
                    result.stdout,
                )

    def test_missing_live_recovers_one_managed_backup_before_prepare(self) -> None:
        hosts = _powershell_hosts()
        if not hosts:
            self.skipTest("PowerShell is not available")

        for host in hosts:
            with (
                self.subTest(host=Path(host).name),
                tempfile.TemporaryDirectory(
                    prefix="apprestore-transaction-recovery-"
                ) as directory,
            ):
                programs = Path(directory) / "Programs"
                backup = programs / "AppRestore.backup-crashed"
                staging = programs / "AppRestore.staging-new"
                self._write_managed_install(backup, "0.1.2")
                self._write_managed_install(staging, "0.1.3")

                result = _run_transaction_probe(
                    host,
                    Path(directory),
                    r"""
$Programs = Join-Path $env:APPRESTORE_TRANSACTION_ROOT "Programs"
$Live = Join-Path $Programs "AppRestore"
$Staging = Join-Path $Programs "AppRestore.staging-new"
$Backup = Join-Path $Programs "AppRestore.backup-new"
Invoke-AppRestoreInstallTransaction `
    -StagingRoot $Staging `
    -InstallRoot $Live `
    -BackupRoot $Backup `
    -ManagedMarkerName ".apprestore-managed" `
    -ManagedMarkerValue "AppRestore managed installation v1" `
    -PrepareStaging {
        param($Path)
        $Recovered = ((& (Join-Path $Live "bin\apprestore.cmd") "--version") |
            Out-String).Trim()
        if ($LASTEXITCODE -ne 0 -or $Recovered -ne "0.1.2") {
            throw "managed backup was not recovered before prepare"
        }
    } `
    -VerifyStaging { param($Path) } `
    -VerifyInstallation { param($Path) }
$Version = ((& (Join-Path $Live "bin\apprestore.cmd") "--version") |
    Out-String).Trim()
if ($LASTEXITCODE -ne 0 -or $Version -ne "0.1.3") {
    throw "new live install was not committed after recovery"
}
if (@(Get-ChildItem -LiteralPath $Programs -Filter "AppRestore.backup-*").Count) {
    throw "backup remained after recovered update"
}
Write-Output "CRASH_RECOVERY_OK"
""",
                )
                self.assertEqual(
                    result.returncode,
                    0,
                    result.stdout + result.stderr,
                )
                self.assertIn("CRASH_RECOVERY_OK", result.stdout)

    def test_missing_live_with_multiple_backups_fails_closed(self) -> None:
        hosts = _powershell_hosts()
        if not hosts:
            self.skipTest("PowerShell is not available")

        for host in hosts:
            with (
                self.subTest(host=Path(host).name),
                tempfile.TemporaryDirectory(
                    prefix="apprestore-transaction-ambiguous-"
                ) as directory,
            ):
                programs = Path(directory) / "Programs"
                first = programs / "AppRestore.backup-first"
                second = programs / "AppRestore.backup-second"
                staging = programs / "AppRestore.staging-new"
                self._write_managed_install(first, "0.1.1")
                self._write_managed_install(second, "0.1.2")
                staging.mkdir()

                result = _run_transaction_probe(
                    host,
                    Path(directory),
                    r"""
$Programs = Join-Path $env:APPRESTORE_TRANSACTION_ROOT "Programs"
$Live = Join-Path $Programs "AppRestore"
$Staging = Join-Path $Programs "AppRestore.staging-new"
$Backup = Join-Path $Programs "AppRestore.backup-new"
$PrepareMarker = Join-Path $Programs "prepare-called.txt"
try {
    Invoke-AppRestoreInstallTransaction `
        -StagingRoot $Staging `
        -InstallRoot $Live `
        -BackupRoot $Backup `
        -ManagedMarkerName ".apprestore-managed" `
        -ManagedMarkerValue "AppRestore managed installation v1" `
        -PrepareStaging {
            param($Path)
            Set-Content -LiteralPath $PrepareMarker -Value "unsafe"
        } `
        -VerifyStaging { param($Path) } `
        -VerifyInstallation { param($Path) }
    throw "ambiguous recovery unexpectedly succeeded"
}
catch {
    if ($_.Exception.Message -notmatch "backup") {
        throw
    }
}
if (Test-Path -LiteralPath $Live) {
    throw "ambiguous recovery created a live directory"
}
if (Test-Path -LiteralPath $PrepareMarker) {
    throw "prepare ran before ambiguity was rejected"
}
if (Test-Path -LiteralPath $Staging) {
    throw "staging was not cleaned after fail-closed recovery"
}
if (-not (Test-Path -LiteralPath (Join-Path $Programs "AppRestore.backup-first"))) {
    throw "first recovery candidate was changed"
}
if (-not (Test-Path -LiteralPath (Join-Path $Programs "AppRestore.backup-second"))) {
    throw "second recovery candidate was changed"
}
Write-Output "AMBIGUOUS_RECOVERY_REFUSED_OK"
""",
                )
                self.assertEqual(
                    result.returncode,
                    0,
                    result.stdout + result.stderr,
                )
                self.assertIn(
                    "AMBIGUOUS_RECOVERY_REFUSED_OK",
                    result.stdout,
                )

    def test_missing_live_never_recovers_an_unmanaged_backup(self) -> None:
        hosts = _powershell_hosts()
        if not hosts:
            self.skipTest("PowerShell is not available")

        for host in hosts:
            with (
                self.subTest(host=Path(host).name),
                tempfile.TemporaryDirectory(
                    prefix="apprestore-transaction-unmanaged-backup-"
                ) as directory,
            ):
                programs = Path(directory) / "Programs"
                backup = programs / "AppRestore.backup-unmanaged"
                staging = programs / "AppRestore.staging-new"
                backup.mkdir(parents=True)
                (backup / "private.txt").write_text(
                    "preserve",
                    encoding="utf-8",
                )
                staging.mkdir()

                result = _run_transaction_probe(
                    host,
                    Path(directory),
                    r"""
$Programs = Join-Path $env:APPRESTORE_TRANSACTION_ROOT "Programs"
$Live = Join-Path $Programs "AppRestore"
$Staging = Join-Path $Programs "AppRestore.staging-new"
$Candidate = Join-Path $Programs "AppRestore.backup-unmanaged"
try {
    Invoke-AppRestoreInstallTransaction `
        -StagingRoot $Staging `
        -InstallRoot $Live `
        -BackupRoot (Join-Path $Programs "AppRestore.backup-new") `
        -ManagedMarkerName ".apprestore-managed" `
        -ManagedMarkerValue "AppRestore managed installation v1" `
        -PrepareStaging { param($Path) throw "prepare must not run" } `
        -VerifyStaging { param($Path) } `
        -VerifyInstallation { param($Path) }
    throw "unmanaged recovery unexpectedly succeeded"
}
catch {
    if ($_.Exception.Message -notmatch "backup") {
        throw
    }
}
if (Test-Path -LiteralPath $Live) {
    throw "unmanaged backup became live"
}
if ((Get-Content -LiteralPath (Join-Path $Candidate "private.txt") -Raw) -ne
    "preserve") {
    throw "unmanaged backup was changed"
}
if (Test-Path -LiteralPath $Staging) {
    throw "staging was not cleaned after unmanaged recovery refusal"
}
Write-Output "UNMANAGED_BACKUP_REFUSED_OK"
""",
                )
                self.assertEqual(
                    result.returncode,
                    0,
                    result.stdout + result.stderr,
                )
                self.assertIn("UNMANAGED_BACKUP_REFUSED_OK", result.stdout)

    def test_named_mutex_serializes_two_installer_processes(self) -> None:
        hosts = _powershell_hosts()
        if not hosts:
            self.skipTest("PowerShell is not available")

        for host in hosts:
            with (
                self.subTest(host=Path(host).name),
                tempfile.TemporaryDirectory(
                    prefix="apprestore-transaction-mutex-"
                ) as directory,
            ):
                root = Path(directory)
                programs = root / "Programs"
                live = programs / "AppRestore"
                first_staging = programs / "AppRestore.staging-first"
                second_staging = programs / "AppRestore.staging-second"
                first_ready = programs / "first-holds-mutex.txt"
                second_ready = programs / "second-entered-prepare.txt"
                self._write_managed_install(live, "0.1.2")
                self._write_managed_install(first_staging, "0.1.3")
                self._write_managed_install(second_staging, "0.1.4")

                first_body = r"""
$Programs = Join-Path $env:APPRESTORE_TRANSACTION_ROOT "Programs"
Invoke-AppRestoreInstallTransaction `
    -StagingRoot (Join-Path $Programs "AppRestore.staging-first") `
    -InstallRoot (Join-Path $Programs "AppRestore") `
    -BackupRoot (Join-Path $Programs "AppRestore.backup-first") `
    -ManagedMarkerName ".apprestore-managed" `
    -ManagedMarkerValue "AppRestore managed installation v1" `
    -PrepareStaging {
        param($Path)
        Set-Content `
            -LiteralPath (Join-Path $Programs "first-holds-mutex.txt") `
            -Value "ready"
        Start-Sleep -Milliseconds 2500
    } `
    -VerifyStaging { param($Path) } `
    -VerifyInstallation { param($Path) }
Write-Output "FIRST_INSTALL_OK"
"""
                second_body = r"""
$Programs = Join-Path $env:APPRESTORE_TRANSACTION_ROOT "Programs"
Invoke-AppRestoreInstallTransaction `
    -StagingRoot (Join-Path $Programs "AppRestore.staging-second") `
    -InstallRoot (Join-Path $Programs "AppRestore") `
    -BackupRoot (Join-Path $Programs "AppRestore.backup-second") `
    -ManagedMarkerName ".apprestore-managed" `
    -ManagedMarkerValue "AppRestore managed installation v1" `
    -PrepareStaging {
        param($Path)
        Set-Content `
            -LiteralPath (Join-Path $Programs "second-entered-prepare.txt") `
            -Value "ready"
    } `
    -VerifyStaging { param($Path) } `
    -VerifyInstallation { param($Path) }
Write-Output "SECOND_INSTALL_OK"
"""

                def write_runner(name: str, body: str) -> Path:
                    runner = root / name
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
                    return runner

                environment = os.environ.copy()
                environment.update(
                    {
                        "APPRESTORE_INSTALLER_PATH": str(
                            ROOT / "install-windows.ps1"
                        ),
                        "APPRESTORE_TRANSACTION_ROOT": str(root),
                    }
                )

                def launch(runner: Path) -> subprocess.Popen[str]:
                    return subprocess.Popen(
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
                        stdout=subprocess.PIPE,
                        stderr=subprocess.PIPE,
                        text=True,
                        encoding="utf-8",
                        errors="replace",
                        env=environment,
                    )

                first_process = launch(
                    write_runner("transaction-first.ps1", first_body)
                )
                second_process: subprocess.Popen[str] | None = None
                try:
                    deadline = time.monotonic() + 10
                    while not first_ready.exists() and time.monotonic() < deadline:
                        if first_process.poll() is not None:
                            break
                        time.sleep(0.05)
                    self.assertTrue(
                        first_ready.exists(),
                        "first installer never acquired the mutex",
                    )

                    second_process = launch(
                        write_runner("transaction-second.ps1", second_body)
                    )
                    time.sleep(0.4)
                    self.assertIsNone(
                        second_process.poll(),
                        "second installer exited while the first held the mutex",
                    )
                    self.assertFalse(
                        second_ready.exists(),
                        "second installer entered prepare before mutex release",
                    )

                    first_stdout, first_stderr = first_process.communicate(
                        timeout=20
                    )
                    second_stdout, second_stderr = second_process.communicate(
                        timeout=20
                    )
                finally:
                    for process in (first_process, second_process):
                        if process is not None and process.poll() is None:
                            process.kill()
                            process.communicate(timeout=5)

                self.assertEqual(
                    first_process.returncode,
                    0,
                    first_stdout + first_stderr,
                )
                self.assertEqual(
                    second_process.returncode,
                    0,
                    second_stdout + second_stderr,
                )
                self.assertIn("FIRST_INSTALL_OK", first_stdout)
                self.assertIn("SECOND_INSTALL_OK", second_stdout)
                self.assertTrue(second_ready.exists())
                self.assertIn(
                    "echo 0.1.4",
                    (live / "bin" / "apprestore.cmd").read_text(
                        encoding="utf-8"
                    ),
                )
                self.assertFalse(list(programs.glob("AppRestore.backup-*")))

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
                "installation belongs to explicit `apprestore setup`/menu item B"
            ),
        )
        menu_text = stdout.getvalue()
        self.assertIn(r"/_/   \_\ .__/| .__/", menu_text)
        self.assertIn("Телефон → сгруженные / удалённые", menu_text)
        self.assertIn("Сгруженные — список и восстановление", menu_text)
        self.assertIn("Локальные IPA — найти / скачать / установить", menu_text)
        self.assertNotIn("Поиск приложения в App Store", menu_text)
        self.assertNotIn("Traceback", stderr.getvalue())


if __name__ == "__main__":
    unittest.main()
