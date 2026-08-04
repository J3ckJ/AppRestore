from __future__ import annotations

import io
import json
import os
import subprocess
import sys
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import patch

from apprestore_core import __version__, cli
from apprestore_core.models import Device, OffloadedApp
from apprestore_core.service import AppRestoreError


class AuthenticatedTools:
    def ipatool_authenticated(self) -> bool:
        return True


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


if __name__ == "__main__":
    unittest.main()
