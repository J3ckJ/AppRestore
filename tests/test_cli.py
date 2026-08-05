from __future__ import annotations

import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import Mock, patch

from apprestore_core import __version__, cli
from apprestore_core.models import Device, OffloadedApp
from apprestore_core.service import AppRestoreError


class AuthenticatedTools:
    def ipatool_authenticated(self) -> bool:
        return True


class UnauthenticatedTools:
    def ipatool_authenticated(self) -> bool:
        return False


class JsonDownloadService:
    def __init__(self, **_kwargs: object) -> None:
        self.tools = AuthenticatedTools()
        self.download_by_store_calls: list[str] = []

    def download(self, _bundle_id: str, _store_id: str | None) -> Path:
        print("download progress")
        return Path(r"C:\IPA\Example.ipa")

    def download_by_store_id(self, store_id: str) -> Path:
        self.download_by_store_calls.append(store_id)
        print("download by store progress")
        return Path(r"C:\IPA\Store.ipa")


class RestoreRetryService:
    def __init__(self) -> None:
        self.tools = AuthenticatedTools()
        self.restore_calls: list[bool] = []

    def devices(self) -> list[Device]:
        return [Device("UDID", "Test iPhone", "18.0")]

    def offloaded(self, _udid: str) -> list[OffloadedApp]:
        return [OffloadedApp("com.example.alpha", "Example", "1.0")]

    def missing(self, _udid: str) -> list[object]:
        return []

    def restore_offloaded(
        self,
        _udid: str,
        _app: OffloadedApp,
        *,
        try_device_redownload: bool = True,
    ) -> str:
        self.restore_calls.append(try_device_redownload)
        if len(self.restore_calls) == 1:
            raise AppRestoreError("ipatool is not authenticated")
        return "restored"


class CliRegressionTests(unittest.TestCase):
    def test_json_parse_error_is_one_document_without_service_startup(self) -> None:
        for arguments in (
            ["--json", "download"],
            ["--json", "--definitely-unknown"],
        ):
            with self.subTest(arguments=arguments):
                stdout = io.StringIO()
                stderr = io.StringIO()
                with (
                    patch("apprestore_core.cli.AppRestoreService") as service,
                    redirect_stdout(stdout),
                    redirect_stderr(stderr),
                ):
                    result = cli.main(arguments)

                self.assertEqual(result, 2)
                payload = json.loads(stdout.getvalue())
                self.assertIsInstance(payload.get("error"), str)
                self.assertTrue(payload["error"])
                self.assertEqual(stderr.getvalue(), "")
                service.assert_not_called()

    def test_human_parse_error_uses_stderr_and_exit_code_two(self) -> None:
        stdout = io.StringIO()
        stderr = io.StringIO()
        with redirect_stdout(stdout), redirect_stderr(stderr):
            result = cli.main(["download"])

        self.assertEqual(result, 2)
        self.assertEqual(stdout.getvalue(), "")
        self.assertIn("usage:", stderr.getvalue())
        self.assertIn("error:", stderr.getvalue())

    def test_module_entrypoint_reports_exact_version(self) -> None:
        result = subprocess.run(
            [sys.executable, "-m", "apprestore_core.cli", "--version"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=30,
            check=False,
            env={
                **os.environ,
                "PYTHONUTF8": "1",
                "PYTHONIOENCODING": "utf-8",
            },
        )

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(result.stdout.strip(), __version__)

    def test_module_entrypoint_without_arguments_opens_menu(self) -> None:
        result = subprocess.run(
            [sys.executable, "-m", "apprestore_core.cli"],
            input="0\n",
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=30,
            check=False,
            env={
                **os.environ,
                "PYTHONUTF8": "1",
                "PYTHONIOENCODING": "utf-8",
            },
        )

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("Телефон → сгруженные / удалённые", result.stdout)

    def test_header_renders_original_logo_without_ansi_when_redirected(self) -> None:
        stdout = io.StringIO()
        with redirect_stdout(stdout):
            cli._print_header(color=False)

        rendered = stdout.getvalue()
        self.assertIn("____", rendered)
        self.assertIn(r"/_/   \_\ .__/| .__/", rendered)
        self.assertIn("Телефон → сгруженные / удалённые → скачать IPA → вернуть", rendered)
        self.assertNotIn("\033[", rendered)

    def test_header_colors_logo_only_when_terminal_supports_it(self) -> None:
        stdout = io.StringIO()
        with redirect_stdout(stdout):
            cli._print_header(color=True)

        rendered = stdout.getvalue()
        self.assertTrue(rendered.startswith("\033[1;36m"))
        self.assertIn("\033[2mТелефон", rendered)
        self.assertTrue(rendered.endswith("────────────────────────────────────────────\n"))

    def test_clear_screen_does_not_launch_an_external_command(self) -> None:
        stdout = io.StringIO()
        with redirect_stdout(stdout), patch("apprestore_core.cli.os.system") as system:
            cli._clear_screen()

        system.assert_not_called()
        self.assertEqual(stdout.getvalue(), "")

    def test_json_download_keeps_progress_out_of_stdout(self) -> None:
        stdout = io.StringIO()
        stderr = io.StringIO()
        with (
            patch("apprestore_core.cli.AppRestoreService", JsonDownloadService),
            redirect_stdout(stdout),
            redirect_stderr(stderr),
        ):
            returncode = cli.main(
                ["--json", "download", "com.example.alpha"],
            )

        self.assertEqual(returncode, 0)
        self.assertEqual(
            json.loads(stdout.getvalue()),
            {"path": r"C:\IPA\Example.ipa"},
        )
        self.assertIn("download progress", stderr.getvalue())
        self.assertNotIn("download progress", stdout.getvalue())

    def test_machine_readable_auth_never_prompts_for_email(self) -> None:
        service = Mock()
        service.tools = UnauthenticatedTools()
        with patch("builtins.input") as prompt:
            with self.assertRaisesRegex(AppRestoreError, "pass --email"):
                cli._ensure_auth(service, None, noninteractive=True)
        prompt.assert_not_called()

    def test_json_search_requires_term_without_prompting(self) -> None:
        service = Mock()
        with patch("builtins.input") as prompt:
            with self.assertRaisesRegex(AppRestoreError, "term is required"):
                cli._command_search(service, None, json_output=True)
        prompt.assert_not_called()

    def test_json_normalizes_service_initialization_failure(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            invalid_cache = root / "cache-is-a-file"
            invalid_cache.write_text("not a directory", encoding="utf-8")
            stdout = io.StringIO()
            stderr = io.StringIO()
            with redirect_stdout(stdout), redirect_stderr(stderr):
                returncode = cli.main(
                    [
                        "--json",
                        "--ipa-dir",
                        str(root / "ipas"),
                        "--cache-dir",
                        str(invalid_cache),
                        "doctor",
                    ]
                )

        self.assertEqual(returncode, 1)
        payload = json.loads(stdout.getvalue())
        self.assertIn("error", payload)
        self.assertNotIn("Traceback", stderr.getvalue())

    def test_json_without_command_emits_one_error_document(self) -> None:
        stdout = io.StringIO()
        stderr = io.StringIO()
        with (
            patch("apprestore_core.cli.AppRestoreService", JsonDownloadService),
            redirect_stdout(stdout),
            redirect_stderr(stderr),
        ):
            returncode = cli.main(["--json"])

        self.assertEqual(returncode, 1)
        self.assertEqual(
            json.loads(stdout.getvalue()),
            {"error": "interactive menu does not support --json"},
        )
        self.assertEqual(stderr.getvalue(), "")

    def test_machine_readable_restore_rejects_ambiguous_search(self) -> None:
        service = Mock()
        with patch("builtins.input") as prompt:
            with self.assertRaisesRegex(AppRestoreError, "--store-id"):
                cli._resolve_missing_targets(
                    "Example",
                    [],
                    service=service,
                    noninteractive=True,
                )
        prompt.assert_not_called()
        service.search_apps.assert_not_called()

    def test_download_positional_store_id_uses_store_path(self) -> None:
        created: list[JsonDownloadService] = []

        def make_service(**_kwargs: object) -> JsonDownloadService:
            service = JsonDownloadService()
            created.append(service)
            return service

        stdout = io.StringIO()
        with (
            patch("apprestore_core.cli.AppRestoreService", side_effect=make_service),
            redirect_stdout(stdout),
            redirect_stderr(io.StringIO()),
        ):
            returncode = cli.main(["download", "610003290"])

        self.assertEqual(returncode, 0)
        self.assertEqual(len(created), 1)
        self.assertEqual(created[0].download_by_store_calls, ["610003290"])
        self.assertIn(r"C:\IPA\Store.ipa", stdout.getvalue())

    def test_explicit_invalid_store_id_fails_instead_of_falling_back(self) -> None:
        service = JsonDownloadService()
        stdout = io.StringIO()
        with (
            patch("apprestore_core.cli.AppRestoreService", return_value=service),
            redirect_stdout(stdout),
            redirect_stderr(io.StringIO()),
        ):
            returncode = cli.main(
                [
                    "--json",
                    "download",
                    "com.example.alpha",
                    "--store-id",
                    "not-an-id",
                ]
            )

        self.assertEqual(returncode, 1)
        self.assertEqual(
            json.loads(stdout.getvalue()),
            {"error": "некорректный --store-id"},
        )

    def test_store_shaped_positional_values_fail_instead_of_becoming_bundles(
        self,
    ) -> None:
        for value in ("123", "id00000000"):
            with self.subTest(value=value):
                stdout = io.StringIO()
                with (
                    patch(
                        "apprestore_core.cli.AppRestoreService",
                        return_value=JsonDownloadService(),
                    ),
                    redirect_stdout(stdout),
                    redirect_stderr(io.StringIO()),
                ):
                    returncode = cli.main(["--json", "download", value])

                self.assertEqual(returncode, 1)
                self.assertEqual(
                    json.loads(stdout.getvalue()),
                    {"error": "некорректный App Store ID"},
                )

    def test_bundle_starting_with_id_is_not_misclassified_as_store_id(self) -> None:
        stdout = io.StringIO()
        with (
            patch(
                "apprestore_core.cli.AppRestoreService",
                return_value=JsonDownloadService(),
            ),
            redirect_stdout(stdout),
            redirect_stderr(io.StringIO()),
        ):
            returncode = cli.main(["--json", "download", "identity.app"])

        self.assertEqual(returncode, 0)
        self.assertEqual(
            json.loads(stdout.getvalue()),
            {"path": r"C:\IPA\Example.ipa"},
        )

    def test_empty_explicit_store_id_is_rejected(self) -> None:
        stdout = io.StringIO()
        with (
            patch(
                "apprestore_core.cli.AppRestoreService",
                return_value=JsonDownloadService(),
            ),
            redirect_stdout(stdout),
            redirect_stderr(io.StringIO()),
        ):
            returncode = cli.main(
                [
                    "--json",
                    "download",
                    "com.example.alpha",
                    "--store-id",
                    "",
                ]
            )

        self.assertEqual(returncode, 1)
        self.assertEqual(
            json.loads(stdout.getvalue()),
            {"error": "некорректный --store-id"},
        )

    def test_auth_retry_does_not_request_device_redownload_twice(self) -> None:
        service = RestoreRetryService()
        with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            returncode = cli._command_restore(
                service,  # type: ignore[arg-type]
                udid=None,
                email=None,
                selection="1",
            )

        self.assertEqual(returncode, 0)
        self.assertEqual(service.restore_calls, [True, False])

    def test_skip_device_redownload_is_forwarded_explicitly(self) -> None:
        service = RestoreRetryService()
        service.restore_calls.clear()
        service.restore_offloaded = Mock(return_value="restored")  # type: ignore[method-assign]
        with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            returncode = cli._command_restore(
                service,  # type: ignore[arg-type]
                udid=None,
                email=None,
                selection="1",
                try_device_redownload=False,
            )

        self.assertEqual(returncode, 0)
        service.restore_offloaded.assert_called_once()
        self.assertFalse(
            service.restore_offloaded.call_args.kwargs["try_device_redownload"]
        )

    def test_search_results_do_not_change_restore_history(self) -> None:
        service = Mock()
        service.search_apps.return_value = [
            {
                "storeId": "12345678",
                "bundleId": "com.example.result",
                "name": "Result",
                "source": "itunes",
            }
        ]
        with (
            patch("apprestore_core.cli.remember_known_app") as remember,
            redirect_stdout(io.StringIO()),
        ):
            result = cli._command_search(service, "Result")

        self.assertEqual(result, 0)
        remember.assert_not_called()

    def test_json_restore_emits_one_document_and_sends_progress_to_stderr(self) -> None:
        service = RestoreRetryService()
        stdout = io.StringIO()
        stderr = io.StringIO()
        with (
            patch("apprestore_core.cli.AppRestoreService", return_value=service),
            redirect_stdout(stdout),
            redirect_stderr(stderr),
        ):
            result = cli.main(["--json", "restore", "--select", "1"])

        self.assertEqual(result, 0)
        self.assertEqual(
            json.loads(stdout.getvalue()),
            {"success": True, "exitCode": 0},
        )
        self.assertIn("Device:", stderr.getvalue())
        self.assertNotIn("Device:", stdout.getvalue())

    def test_json_restore_rejects_empty_selection(self) -> None:
        stdout = io.StringIO()
        with (
            patch(
                "apprestore_core.cli.AppRestoreService",
                return_value=RestoreRetryService(),
            ),
            redirect_stdout(stdout),
            redirect_stderr(io.StringIO()),
        ):
            result = cli.main(["--json", "restore", "--select", ""])

        self.assertEqual(result, 1)
        self.assertEqual(
            json.loads(stdout.getvalue()),
            {"error": "selection cannot be empty in machine-readable restore"},
        )

    def test_json_restore_missing_rejects_empty_selection(self) -> None:
        stdout = io.StringIO()
        with (
            patch(
                "apprestore_core.cli.AppRestoreService",
                return_value=RestoreRetryService(),
            ),
            redirect_stdout(stdout),
            redirect_stderr(io.StringIO()),
        ):
            result = cli.main(
                ["--json", "restore-missing", "--select", ""]
            )

        self.assertEqual(result, 1)
        self.assertEqual(
            json.loads(stdout.getvalue()),
            {
                "error": (
                    "selection cannot be empty in machine-readable "
                    "restore-missing"
                )
            },
        )


if __name__ == "__main__":
    unittest.main()
