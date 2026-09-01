from __future__ import annotations

import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from apprestore_core.paths import (
    imazing_apps_dirs,
    imazing_catalog_candidates,
    macos_system_proxy,
    resolve_windows_system_tool,
)


class WindowsPathTests(unittest.TestCase):
    def test_imazing_paths_follow_redirected_appdata(self) -> None:
        environment = {
            "APPDATA": r"D:\Profiles\Alice\Roaming",
            "LOCALAPPDATA": r"E:\Profiles\Alice\Local",
        }
        with (
            patch.dict(os.environ, environment, clear=False),
            patch("apprestore_core.paths.platform.system", return_value="Windows"),
        ):
            app_dirs = imazing_apps_dirs()
            catalogs = imazing_catalog_candidates()

        self.assertEqual(
            app_dirs,
            [
                Path(environment["APPDATA"]) / "iMazing" / "Library" / "Apps",
                Path(environment["APPDATA"])
                / "DigiDNA"
                / "iMazing"
                / "Library"
                / "Apps",
                Path(environment["LOCALAPPDATA"])
                / "DigiDNA"
                / "iMazing"
                / "Library"
                / "Apps",
            ],
        )
        self.assertEqual(
            catalogs,
            [
                Path(environment["APPDATA"])
                / "iMazing"
                / "Library"
                / "Apps.plist",
                Path(environment["APPDATA"])
                / "DigiDNA"
                / "iMazing"
                / "Library"
                / "Apps.plist",
                Path(environment["LOCALAPPDATA"])
                / "DigiDNA"
                / "iMazing"
                / "Library"
                / "Apps.plist",
            ],
        )

    def test_privileged_tool_resolver_ignores_path_and_app_bin(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            program_files = root / "Program Files"
            package_name = (
                "Microsoft.DesktopAppInstaller_1.29.280.0_"
                "x64__8wekyb3d8bbwe"
            )
            package = program_files / "WindowsApps" / package_name
            package.mkdir(parents=True)
            trusted = package / "winget.exe"
            trusted.write_bytes(b"signed winget")
            (package / "AppxManifest.xml").write_text(
                "<Package><Identity "
                'Name="Microsoft.DesktopAppInstaller" '
                'Publisher="CN=Microsoft Corporation, O=Microsoft Corporation, '
                'L=Redmond, S=Washington, C=US" '
                'Version="1.29.280.0" /></Package>',
                encoding="utf-8",
            )
            system_root = root / "Windows"
            powershell = (
                system_root
                / "System32"
                / "WindowsPowerShell"
                / "v1.0"
                / "powershell.exe"
            )
            powershell.parent.mkdir(parents=True)
            powershell.write_bytes(b"trusted powershell")
            malicious = Path(temporary) / "malicious"
            malicious.mkdir()
            (malicious / "winget.exe").write_bytes(b"malicious")
            with (
                patch.dict(
                    os.environ,
                    {
                        "PATH": str(malicious),
                        "SystemRoot": str(system_root),
                    },
                    clear=False,
                ),
                patch(
                    "apprestore_core.paths.platform.system",
                    return_value="Windows",
                ),
                patch(
                    "apprestore_core.paths._windows_program_files_root",
                    return_value=program_files,
                ),
                patch(
                    "apprestore_core.paths._windows_directory",
                    return_value=system_root,
                ),
                patch(
                    "apprestore_core.paths._registered_desktop_app_installer_packages",
                    return_value=[package_name],
                ),
                patch(
                    "apprestore_core.paths._microsoft_authenticode_valid",
                    return_value=True,
                ) as signature_check,
            ):
                resolved = resolve_windows_system_tool("winget")

            self.assertEqual(resolved, str(trusted.absolute()))
            signature_check.assert_called_once()

    def test_privileged_tool_resolver_rejects_bad_winget_signature(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            program_files = root / "Program Files"
            package_name = (
                "Microsoft.DesktopAppInstaller_1.0.0.0_"
                "x64__8wekyb3d8bbwe"
            )
            package = program_files / "WindowsApps" / package_name
            package.mkdir(parents=True)
            (package / "winget.exe").write_bytes(b"not signed")
            (package / "AppxManifest.xml").write_text(
                "<Package><Identity "
                'Name="Microsoft.DesktopAppInstaller" '
                'Publisher="CN=Microsoft Corporation, O=Microsoft Corporation, '
                'L=Redmond, S=Washington, C=US" '
                'Version="1.0.0.0" /></Package>',
                encoding="utf-8",
            )
            powershell = (
                root
                / "Windows"
                / "System32"
                / "WindowsPowerShell"
                / "v1.0"
                / "powershell.exe"
            )
            powershell.parent.mkdir(parents=True)
            powershell.write_bytes(b"trusted powershell")
            with (
                patch.dict(
                    os.environ,
                    {"SystemRoot": str(root / "Windows")},
                    clear=False,
                ),
                patch("apprestore_core.paths.platform.system", return_value="Windows"),
                patch(
                    "apprestore_core.paths._windows_program_files_root",
                    return_value=program_files,
                ),
                patch(
                    "apprestore_core.paths._windows_directory",
                    return_value=root / "Windows",
                ),
                patch(
                    "apprestore_core.paths._registered_desktop_app_installer_packages",
                    return_value=[package_name],
                ),
                patch(
                    "apprestore_core.paths._microsoft_authenticode_valid",
                    return_value=False,
                ),
            ):
                self.assertIsNone(resolve_windows_system_tool("winget"))


class MacOSProxyTests(unittest.TestCase):
    _SCUTIL_OUTPUT = """<dictionary> {
  ExceptionsList : <array> {
    0 : *.local
    1 : 169.254/16
  }
  FTPPassive : 1
  HTTPEnable : 0
  HTTPSEnable : 1
  HTTPSPort : 8443
  HTTPSProxy : 10.0.0.5
  ProxyAutoConfigEnable : 0
}
"""

    def test_enabled_https_proxy_is_parsed_with_exceptions(self) -> None:
        completed = subprocess.CompletedProcess(
            args=["scutil", "--proxy"],
            returncode=0,
            stdout=self._SCUTIL_OUTPUT,
        )
        with (
            patch("apprestore_core.paths.platform.system", return_value="Darwin"),
            patch("apprestore_core.paths.shutil.which", return_value="/usr/sbin/scutil"),
            patch("apprestore_core.paths.subprocess.run", return_value=completed),
        ):
            result = macos_system_proxy()

        self.assertEqual(
            result,
            ("http://10.0.0.5:8443", "localhost,127.0.0.1,::1,*.local,169.254/16"),
        )

    def test_disabled_https_proxy_returns_none(self) -> None:
        disabled_output = self._SCUTIL_OUTPUT.replace(
            "HTTPSEnable : 1", "HTTPSEnable : 0"
        )
        completed = subprocess.CompletedProcess(
            args=["scutil", "--proxy"],
            returncode=0,
            stdout=disabled_output,
        )
        with (
            patch("apprestore_core.paths.platform.system", return_value="Darwin"),
            patch("apprestore_core.paths.shutil.which", return_value="/usr/sbin/scutil"),
            patch("apprestore_core.paths.subprocess.run", return_value=completed),
        ):
            self.assertIsNone(macos_system_proxy())

    def test_non_macos_platform_never_shells_out(self) -> None:
        with (
            patch("apprestore_core.paths.platform.system", return_value="Linux"),
            patch("apprestore_core.paths.subprocess.run") as run,
        ):
            self.assertIsNone(macos_system_proxy())
        run.assert_not_called()


if __name__ == "__main__":
    unittest.main()
