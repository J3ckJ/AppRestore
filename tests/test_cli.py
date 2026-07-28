from __future__ import annotations

import io
import json
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import patch

from apprestore_core import cli
from apprestore_core.models import Device, OffloadedApp
from apprestore_core.service import AppRestoreError


class AuthenticatedTools:
    def ipatool_authenticated(self) -> bool:
        return True


class JsonDownloadService:
    def __init__(self, **_kwargs: object) -> None:
        self.tools = AuthenticatedTools()

    def download(self, _bundle_id: str, _store_id: str | None) -> Path:
        print("download progress")
        return Path(r"C:\IPA\Example.ipa")


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
    def test_header_renders_original_logo_without_ansi_when_redirected(self) -> None:
        stdout = io.StringIO()
        with redirect_stdout(stdout):
            cli._print_header(color=False)

        rendered = stdout.getvalue()
        self.assertIn("____", rendered)
        self.assertIn(r"/_/   \_\ .__/| .__/", rendered)
        self.assertIn("Телефон → сгруженные приложения → скачать IPA → вернуть", rendered)
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
