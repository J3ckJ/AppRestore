from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from apprestore_core.command import Runner
from apprestore_core.models import CommandResult
from apprestore_core.tools import AppRestoreTools


class RecordingRunner:
    def __init__(self) -> None:
        self.calls: list[tuple[tuple[str, ...], dict[str, object]]] = []
        self.returncode = 0
        self.stdout = ""
        self.stderr = ""

    def run(self, args: list[str], **kwargs: object) -> CommandResult:
        command = tuple(args)
        self.calls.append((command, kwargs))
        return CommandResult(
            command,
            self.returncode,
            self.stdout,
            self.stderr,
        )


class RunnerTests(unittest.TestCase):
    def test_metacharacters_are_passed_as_literal_argument(self) -> None:
        marker = "value;echo SHOULD_NOT_RUN"
        result = Runner().run(
            [sys.executable, "-c", "import sys; print(sys.argv[1])", marker],
            check=True,
        )
        self.assertEqual(result.stdout.strip(), marker)


class ToolArgumentTests(unittest.TestCase):
    def setUp(self) -> None:
        self.runner = RecordingRunner()
        self.tools = AppRestoreTools(self.runner)  # type: ignore[arg-type]

    @patch("apprestore_core.tools.resolve_tool", return_value="ipatool")
    def test_login_delegates_all_secrets_to_interactive_ipatool(
        self,
        _resolve: object,
    ) -> None:
        self.tools.ipatool_login("owner@example.com")
        args = self.runner.calls[0][0]
        self.assertEqual(
            args,
            (
                "ipatool",
                "auth",
                "login",
                "--email",
                "owner@example.com",
            ),
        )
        self.assertNotIn("--password", args)
        self.assertNotIn("--auth-code", args)
        self.assertNotIn("--keychain-passphrase", args)
        self.assertEqual(self.runner.calls[0][1].get("capture"), False)

    @patch("apprestore_core.tools.resolve_tool", return_value="ipatool")
    def test_purchase_is_explicit_and_not_used_for_store_id(
        self,
        _resolve: object,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "out.ipa"
            self.tools.download_ipa(output, store_id="123", purchase=False)
            store_args = self.runner.calls[-1][0]
            self.assertNotIn("--purchase", store_args)
            self.assertEqual(self.runner.calls[-1][1].get("capture"), False)

            self.tools.download_ipa(
                output,
                bundle_id="com.example.alpha",
                purchase=True,
            )
            bundle_args = self.runner.calls[-1][0]
            self.assertIn("--purchase", bundle_args)

    @patch("apprestore_core.tools.resolve_tool", return_value="ipatool")
    def test_login_marks_session_authenticated(self, _resolve: object) -> None:
        self.assertTrue(self.tools.ipatool_authenticated())
        self.assertEqual(
            self.runner.calls[-1][0],
            ("ipatool", "auth", "info"),
        )
        self.assertEqual(self.runner.calls[-1][1].get("capture"), False)

        self.tools._ipatool_session_authenticated = False
        self.runner.calls.clear()
        self.tools.ipatool_login("owner@example.com")
        self.assertTrue(self.tools._ipatool_session_authenticated)
        before = len(self.runner.calls)
        self.assertTrue(self.tools.ipatool_authenticated())
        self.assertEqual(len(self.runner.calls), before)

    @patch("apprestore_core.tools.resolve_tool", return_value="ipatool")
    def test_auth_info_captures_and_parses_json(self, _resolve: object) -> None:
        self.runner.stdout = '{"email":"owner@example.com","success":true}'

        payload = self.tools.ipatool_auth_info()

        self.assertEqual(
            payload,
            {"email": "owner@example.com", "success": True},
        )
        self.assertEqual(
            self.runner.calls[-1][0],
            ("ipatool", "--format", "json", "auth", "info"),
        )
        self.assertEqual(self.runner.calls[-1][1].get("capture"), True)
        self.assertTrue(self.tools._ipatool_session_authenticated)

    @patch.dict(
        os.environ,
        {"APPRESTORE_IPATOOL_KEYCHAIN_PASSPHRASE": "must-not-leak"},
    )
    @patch("apprestore_core.tools.resolve_tool", return_value="ipatool")
    def test_legacy_environment_passphrase_is_ignored(
        self,
        _resolve: object,
    ) -> None:
        tools = AppRestoreTools(self.runner)  # type: ignore[arg-type]

        tools.download_ipa(Path("out.ipa"), store_id="1", purchase=False)

        args = self.runner.calls[-1][0]
        self.assertNotIn("--keychain-passphrase", args)
        self.assertNotIn("must-not-leak", args)

    @patch(
        "apprestore_core.tools.resolve_tool",
        side_effect=lambda name: {
            "winget": r"C:\Program Files\WindowsApps\winget.exe",
            "powershell": r"C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe",
        }.get(name),
    )
    def test_elevated_winget_propagates_child_exit_code(
        self,
        _resolve: object,
    ) -> None:
        self.runner.returncode = 23

        self.assertFalse(self.tools._winget_install_apple_bridge())

        elevated_args = self.runner.calls[-1][0]
        powershell_code = elevated_args[-1]
        self.assertIn("-PassThru", powershell_code)
        self.assertIn("exit $process.ExitCode", powershell_code)
        self.assertIn(
            r"C:\Program Files\WindowsApps\winget.exe",
            powershell_code,
        )

    @patch("apprestore_core.tools.importlib.util.find_spec", return_value=object())
    @patch("apprestore_core.tools.platform.system", return_value="Windows")
    def test_windows_uses_relocatable_pymobiledevice_module_command(
        self,
        _platform_system: object,
        _find_spec: object,
    ) -> None:
        self.runner.stdout = '["00008020-test"]\n'

        self.assertEqual(self.tools.list_udids(), ["00008020-test"])

        self.assertEqual(
            self.runner.calls[-1][0],
            (
                sys.executable,
                "-m",
                "pymobiledevice3",
                "usbmux",
                "list",
                "--simple",
                "--usb",
            ),
        )


if __name__ == "__main__":
    unittest.main()
