from __future__ import annotations

import os
import sys
import tempfile
import types
import unittest
from contextlib import redirect_stderr
from pathlib import Path
from typing import Any
from unittest.mock import Mock, patch

from apprestore_core import __version__
from apprestore_core.command import CommandError, Runner
from apprestore_core.models import CommandResult, DeviceAppState
from apprestore_core.tools import IPATOOL_VERSION, AppRestoreTools, InstallRequestState


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

    def test_machine_output_routes_child_stdout_to_stderr(self) -> None:
        with tempfile.TemporaryFile(mode="w+", encoding="utf-8") as error_stream:
            with redirect_stderr(error_stream):
                result = Runner().run(
                    [sys.executable, "-c", "print('child progress')"],
                    capture=False,
                    output_to_stderr=True,
                    timeout=30,
                )
            error_stream.seek(0)
            rendered = error_stream.read()

        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout, "")
        self.assertIn("child progress", rendered)

    def test_timeout_output_is_always_normalized_to_text(self) -> None:
        script = (
            "import sys, time; "
            "sys.stdout.buffer.write(b'partial-\\xff'); sys.stdout.flush(); "
            "sys.stderr.buffer.write(b'failure-\\xfe'); sys.stderr.flush(); "
            "time.sleep(60)"
        )
        with self.assertRaises(CommandError) as caught:
            Runner().run([sys.executable, "-c", script], timeout=0.5)

        self.assertIsInstance(caught.exception.result.stdout, str)
        self.assertIsInstance(caught.exception.result.stderr, str)
        self.assertIn("partial-", caught.exception.result.stdout)
        self.assertIn("failure-", caught.exception.result.stderr)


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
        self.assertEqual(self.runner.calls[-1][1].get("timeout"), 60)
        self.assertTrue(self.tools._ipatool_session_authenticated)

    @patch("apprestore_core.tools.resolve_tool", return_value="ipatool")
    def test_auth_info_rejects_malformed_or_unsuccessful_json(
        self,
        _resolve: object,
    ) -> None:
        for output in (
            "not-json",
            "[]",
            "{}",
            '{"success":false}',
            '{"success":false,"success":true}',
        ):
            with self.subTest(output=output):
                self.runner.stdout = output
                self.tools._ipatool_session_authenticated = False

                self.assertIsNone(self.tools.ipatool_auth_info())
                self.assertFalse(self.tools._ipatool_session_authenticated)

    def test_ipatool_version_requires_an_exact_version_token(self) -> None:
        for output in (
            "ipatool 12.3.10",
            "ipatool 2.3.10",
            f"ipatool {IPATOOL_VERSION}-beta",
        ):
            with self.subTest(output=output):
                self.runner.stdout = output
                ok, _detail = self.tools._ipatool_check("ipatool")
                self.assertFalse(ok)

        self.runner.stdout = f"ipatool v{IPATOOL_VERSION}"
        ok, _detail = self.tools._ipatool_check("ipatool")
        self.assertTrue(ok)

    @patch("apprestore_core.tools.resolve_windows_system_tool", return_value=None)
    def test_apple_service_never_falls_back_to_path_sc(self, _resolve: object) -> None:
        ok, detail = self.tools._apple_mobile_device_service()

        self.assertFalse(ok)
        self.assertIn("trusted System32", detail)
        self.assertEqual(self.runner.calls, [])

    @patch(
        "apprestore_core.tools.resolve_windows_system_tool",
        return_value=r"C:\Windows\System32\sc.exe",
    )
    def test_apple_service_uses_numeric_state_not_localized_word(
        self,
        _resolve: object,
    ) -> None:
        self.runner.stdout = "СОСТОЯНИЕ : 4  РАБОТАЕТ"

        ok, detail = self.tools._apple_mobile_device_service()

        self.assertTrue(ok)
        self.assertIn("running", detail)
        self.assertEqual(self.runner.calls[-1][1].get("timeout"), 15)

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
        "apprestore_core.tools.resolve_windows_system_tool",
        return_value=r"C:\Users\owner\AppData\Local\Microsoft\WindowsApps\winget.exe",
    )
    def test_winget_is_never_retried_through_elevated_powershell(
        self,
        _resolve: object,
    ) -> None:
        self.runner.returncode = 23

        self.assertFalse(self.tools._winget_install_apple_bridge())

        self.assertEqual(len(self.runner.calls), 1)
        args = self.runner.calls[0][0]
        self.assertEqual(
            args[0],
            r"C:\Users\owner\AppData\Local\Microsoft\WindowsApps\winget.exe",
        )
        self.assertNotIn("Start-Process", " ".join(args))
        self.assertNotIn("RunAs", " ".join(args))

    @patch("apprestore_core.tools.importlib.util.find_spec", return_value=object())
    @patch("apprestore_core.tools.platform.system", return_value="Windows")
    def test_windows_uses_isolated_relocatable_pymobiledevice_command(
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
                "-I",
                "-m",
                "pymobiledevice3",
                "usbmux",
                "list",
                "--simple",
                "--usb",
            ),
        )
        self.assertEqual(self.runner.calls[-1][1].get("timeout"), 60)

    @patch("apprestore_core.tools.importlib.util.find_spec", return_value=object())
    @patch("apprestore_core.tools.platform.system", return_value="Darwin")
    def test_macos_uses_isolated_relocatable_pymobiledevice_command(
        self,
        _platform_system: object,
        _find_spec: object,
    ) -> None:
        # install-macos.sh installs into a sibling ".venv-staging.*" directory
        # and moves it into place afterwards, which leaves the pip console
        # launcher pointing at the now-gone staging path. Invoking the
        # interpreter with -m sidesteps that broken launcher, same as Windows.
        self.runner.stdout = '["00008020-test"]\n'

        self.assertEqual(self.tools.list_udids(), ["00008020-test"])

        self.assertEqual(
            self.runner.calls[-1][0],
            (
                sys.executable,
                "-I",
                "-m",
                "pymobiledevice3",
                "usbmux",
                "list",
                "--simple",
                "--usb",
            ),
        )
        self.assertEqual(self.runner.calls[-1][1].get("timeout"), 60)

    def test_device_info_removes_terminal_controls_and_newlines(self) -> None:
        self.runner.stdout = (
            '{"DeviceName":"\\u001b[31mWork\\nPhone\\u001b[0m",'
            '"ProductVersion":"17.5\\rspoofed"}'
        )
        with patch.object(
            self.tools,
            "_pymobiledevice3_cmd",
            return_value=["pymobiledevice3"],
        ):
            device = self.tools.device_info("UDID")

        self.assertEqual(device.name, "Work Phone")
        self.assertEqual(device.ios_version, "17.5 spoofed")

    def test_device_app_state_distinguishes_absent_unknown_and_installed(self) -> None:
        with patch.object(
            self.tools,
            "_list_apps_with_metadata",
            side_effect=[
                {},
                RuntimeError("usb disconnected"),
                {
                    "com.example.app": {
                        "CFBundleIdentifier": "com.example.app",
                        "ApplicationType": "User",
                    }
                },
            ],
        ):
            self.assertIs(
                self.tools.device_app_state("UDID", "com.example.app"),
                DeviceAppState.ABSENT,
            )
            self.assertIs(
                self.tools.device_app_state("UDID", "com.example.app"),
                DeviceAppState.UNKNOWN,
            )
            self.assertIs(
                self.tools.device_app_state("UDID", "com.example.app"),
                DeviceAppState.INSTALLED,
            )

    def test_device_app_state_distinguishes_offloaded_and_downloading(self) -> None:
        with patch.object(
            self.tools,
            "_list_apps_with_metadata",
            side_effect=[
                {
                    "com.example.app": {
                        "CFBundleIdentifier": "com.example.app",
                        "IsPlaceholder": True,
                    }
                },
                {
                    "com.example.app": {
                        "CFBundleIdentifier": "com.example.app",
                        "IsPlaceholder": True,
                        "DownloadState": "Downloading",
                    }
                },
            ],
        ):
            self.assertIs(
                self.tools.device_app_state("UDID", "com.example.app"),
                DeviceAppState.OFFLOADED,
            )
            self.assertIs(
                self.tools.device_app_state("UDID", "com.example.app"),
                DeviceAppState.DOWNLOADING,
            )

    def test_device_app_state_rejects_conflicts_and_uses_exact_states(self) -> None:
        with patch.object(
            self.tools,
            "_list_apps_with_metadata",
            side_effect=[
                {
                    "com.example.app": {
                        "CFBundleIdentifier": "com.example.other",
                        "ApplicationType": "User",
                    }
                },
                {
                    "com.example.app": {
                        "CFBundleIdentifier": "com.example.app",
                        "ApplicationType": "User",
                        "IsDownloading": True,
                    }
                },
                {
                    "com.example.app": {
                        "CFBundleIdentifier": "com.example.app",
                        "IsPlaceholder": True,
                        "InstallState": "NotInstalled",
                    }
                },
            ],
        ):
            self.assertIs(
                self.tools.device_app_state("UDID", "com.example.app"),
                DeviceAppState.UNKNOWN,
            )
            self.assertIs(
                self.tools.device_app_state("UDID", "com.example.app"),
                DeviceAppState.DOWNLOADING,
            )
            self.assertIs(
                self.tools.device_app_state("UDID", "com.example.app"),
                DeviceAppState.OFFLOADED,
            )

    def test_device_snapshot_requests_only_one_lightweight_record(self) -> None:
        with patch.object(
            self.tools,
            "_list_apps_with_metadata",
            return_value={
                "com.example.app": {
                    "CFBundleIdentifier": "com.example.app",
                    "CFBundleShortVersionString": "1.2.3",
                    "ApplicationType": "User",
                }
            },
        ) as lookup:
            snapshot = self.tools.device_app_snapshot("UDID", "com.example.app")

        self.assertEqual(snapshot, (DeviceAppState.INSTALLED, "1.2.3"))
        lookup.assert_called_once_with(
            "UDID",
            timeout=15,
            bundle_id="com.example.app",
            include_store_metadata=False,
        )

    def test_lightweight_lookup_omits_store_blobs_and_filters_bundle(self) -> None:
        observed: dict[str, object] = {}

        class AsyncContext:
            def __init__(self, value: object) -> None:
                self.value = value

            async def __aenter__(self) -> object:
                return self.value

            async def __aexit__(self, *_args: object) -> None:
                return None

        class FakeInstallationProxy:
            async def lookup(self, options: dict[str, object]) -> dict[str, object]:
                observed.update(options)
                return {}

        async def create_using_usbmux(**_kwargs: object) -> AsyncContext:
            return AsyncContext(object())

        lockdown = types.ModuleType("pymobiledevice3.lockdown")
        lockdown.create_using_usbmux = create_using_usbmux  # type: ignore[attr-defined]
        installation = types.ModuleType(
            "pymobiledevice3.services.installation_proxy"
        )
        installation.InstallationProxyService = (  # type: ignore[attr-defined]
            lambda _lockdown: AsyncContext(FakeInstallationProxy())
        )
        package = types.ModuleType("pymobiledevice3")
        package.__path__ = []  # type: ignore[attr-defined]
        services = types.ModuleType("pymobiledevice3.services")
        services.__path__ = []  # type: ignore[attr-defined]

        with patch.dict(
            sys.modules,
            {
                "pymobiledevice3": package,
                "pymobiledevice3.lockdown": lockdown,
                "pymobiledevice3.services": services,
                "pymobiledevice3.services.installation_proxy": installation,
            },
        ):
            self.tools._list_apps_with_metadata(
                "UDID",
                bundle_id="com.example.app",
                include_store_metadata=False,
            )

        self.assertEqual(observed["BundleIDs"], ["com.example.app"])
        attributes = observed["ReturnAttributes"]
        self.assertIsInstance(attributes, list)
        self.assertNotIn("iTunesMetadata", attributes)
        self.assertNotIn("ApplicationSINF", attributes)

    def test_direct_install_disables_library_progress_in_json_mode(self) -> None:
        observed: dict[str, object] = {}

        class AsyncContext:
            def __init__(self, value: object) -> None:
                self.value = value

            async def __aenter__(self) -> object:
                return self.value

            async def __aexit__(self, *_args: object) -> None:
                return None

        class FakeAfc:
            async def makedirs(self, _path: str) -> None:
                return None

            async def push(
                self,
                _source: str,
                _destination: str,
                *,
                progress_bar: bool,
            ) -> None:
                observed["progress_bar"] = progress_bar

            async def rm_single(self, _path: str, *, force: bool) -> None:
                observed["removed"] = force

        class FakeInstallationProxy:
            async def send_package(self, *_args: object) -> None:
                observed["installed"] = True

        async def create_using_usbmux(**_kwargs: object) -> AsyncContext:
            return AsyncContext(object())

        lockdown = types.ModuleType("pymobiledevice3.lockdown")
        lockdown.create_using_usbmux = create_using_usbmux  # type: ignore[attr-defined]
        afc = types.ModuleType("pymobiledevice3.services.afc")
        afc.AfcService = lambda _lockdown: AsyncContext(FakeAfc())  # type: ignore[attr-defined]
        installation = types.ModuleType(
            "pymobiledevice3.services.installation_proxy"
        )
        installation.InstallationProxyService = (  # type: ignore[attr-defined]
            lambda _lockdown: AsyncContext(FakeInstallationProxy())
        )
        package = types.ModuleType("pymobiledevice3")
        package.__path__ = []  # type: ignore[attr-defined]
        services = types.ModuleType("pymobiledevice3.services")
        services.__path__ = []  # type: ignore[attr-defined]

        tools = AppRestoreTools(self.runner, json_output=True)  # type: ignore[arg-type]
        with patch.dict(
            sys.modules,
            {
                "pymobiledevice3": package,
                "pymobiledevice3.lockdown": lockdown,
                "pymobiledevice3.services": services,
                "pymobiledevice3.services.afc": afc,
                "pymobiledevice3.services.installation_proxy": installation,
            },
        ):
            result = tools.install_ipa("UDID", Path("example.ipa"))

        self.assertIs(result, InstallRequestState.COMPLETED)
        self.assertIs(observed["progress_bar"], False)
        self.assertIs(observed["installed"], True)
        self.assertIs(observed["removed"], True)

    def test_direct_install_classifies_failures_around_request_boundary(self) -> None:
        def classify(failure: str) -> InstallRequestState:
            class AsyncContext:
                def __init__(self, value: object) -> None:
                    self.value = value

                async def __aenter__(self) -> object:
                    return self.value

                async def __aexit__(self, *_args: object) -> None:
                    return None

            class FakeAfc:
                async def makedirs(self, _path: str) -> None:
                    return None

                async def push(
                    self,
                    _source: str,
                    _destination: str,
                    *,
                    progress_bar: bool,
                ) -> None:
                    del progress_bar
                    if failure == "push":
                        raise RuntimeError("USB lost before upload")

                async def rm_single(self, _path: str, *, force: bool) -> None:
                    del force

            class FakeInstallationProxy:
                async def send_package(self, *_args: object) -> None:
                    if failure == "send":
                        raise RuntimeError("USB lost after send began")

            async def create_using_usbmux(**_kwargs: object) -> AsyncContext:
                return AsyncContext(object())

            lockdown = types.ModuleType("pymobiledevice3.lockdown")
            lockdown.create_using_usbmux = create_using_usbmux  # type: ignore[attr-defined]
            afc = types.ModuleType("pymobiledevice3.services.afc")
            afc.AfcService = lambda _lockdown: AsyncContext(FakeAfc())  # type: ignore[attr-defined]
            installation = types.ModuleType(
                "pymobiledevice3.services.installation_proxy"
            )
            installation.InstallationProxyService = (  # type: ignore[attr-defined]
                lambda _lockdown: AsyncContext(FakeInstallationProxy())
            )
            package = types.ModuleType("pymobiledevice3")
            package.__path__ = []  # type: ignore[attr-defined]
            services = types.ModuleType("pymobiledevice3.services")
            services.__path__ = []  # type: ignore[attr-defined]

            with patch.dict(
                sys.modules,
                {
                    "pymobiledevice3": package,
                    "pymobiledevice3.lockdown": lockdown,
                    "pymobiledevice3.services": services,
                    "pymobiledevice3.services.afc": afc,
                    "pymobiledevice3.services.installation_proxy": installation,
                },
            ):
                return self.tools.install_ipa("UDID", Path("example.ipa"))

        self.assertIs(
            classify("push"),
            InstallRequestState.FAILED_BEFORE_REQUEST,
        )
        self.assertIs(
            classify("send"),
            InstallRequestState.INDETERMINATE,
        )

    def test_runtime_doctor_detects_metadata_drift(self) -> None:
        distribution = Mock()
        distribution.version = "0.1.4"
        distribution.read_text.return_value = None
        with patch(
            "apprestore_core.tools.package_metadata.distribution",
            return_value=distribution,
        ):
            check = self.tools._runtime_provenance_check()

        self.assertFalse(check.ok)
        self.assertTrue(check.required)
        self.assertIn("metadata=0.1.4", check.detail)

    def test_runtime_doctor_marks_editable_checkout_as_warning(self) -> None:
        distribution = Mock()
        distribution.version = __version__
        distribution.read_text.return_value = (
            '{"url":"file:///source","dir_info":{"editable":true}}'
        )
        with patch(
            "apprestore_core.tools.package_metadata.distribution",
            return_value=distribution,
        ):
            check = self.tools._runtime_provenance_check()

        self.assertFalse(check.ok)
        self.assertFalse(check.required)
        self.assertIn("editable source=", check.detail)


def _without_proxy_environment() -> Any:
    """Убрать прокси-переменные только на время теста, не портя процесс."""
    environment = {
        name: value
        for name, value in os.environ.items()
        if name.lower() not in {"https_proxy", "http_proxy"}
    }
    return patch.dict(os.environ, environment, clear=True)


class IpatoolProxyEnvironmentTests(unittest.TestCase):
    def setUp(self) -> None:
        self.runner = RecordingRunner()
        self.tools = AppRestoreTools(self.runner)  # type: ignore[arg-type]

    @patch("apprestore_core.tools.windows_system_proxy")
    def test_explicit_environment_proxy_is_never_overridden(
        self,
        system_proxy: Mock,
    ) -> None:
        system_proxy.return_value = ("http://127.0.0.1:9", "localhost")
        with patch.dict(
            os.environ,
            {"HTTPS_PROXY": "http://proxy.example:3128"},
            clear=False,
        ):
            self.assertEqual(self.tools._resolve_ipatool_proxy_env(), {})
        system_proxy.assert_not_called()

    @patch("apprestore_core.tools.proxy_is_reachable", return_value=False)
    @patch("apprestore_core.tools.windows_system_proxy")
    def test_unreachable_system_proxy_is_not_injected(
        self,
        system_proxy: Mock,
        _reachable: Mock,
    ) -> None:
        # Системный прокси часто указывает на выключенный VPN-клиент.
        system_proxy.return_value = ("http://127.0.0.1:10809", "localhost")
        with _without_proxy_environment():
            self.assertEqual(self.tools._resolve_ipatool_proxy_env(), {})

    @patch("apprestore_core.tools.proxy_is_reachable", return_value=True)
    @patch("apprestore_core.tools.windows_system_proxy")
    def test_reachable_system_proxy_reaches_ipatool(
        self,
        system_proxy: Mock,
        _reachable: Mock,
    ) -> None:
        system_proxy.return_value = ("http://127.0.0.1:10809", "localhost,::1")
        with _without_proxy_environment():
            resolved = self.tools._resolve_ipatool_proxy_env()

        self.assertEqual(
            resolved,
            {
                "HTTP_PROXY": "http://127.0.0.1:10809",
                "HTTPS_PROXY": "http://127.0.0.1:10809",
                "NO_PROXY": "localhost,::1",
            },
        )

    @patch("apprestore_core.tools.proxy_is_reachable", return_value=False)
    @patch("apprestore_core.tools.windows_system_proxy", return_value=None)
    @patch("apprestore_core.tools.macos_system_proxy")
    def test_unreachable_macos_system_proxy_is_not_injected(
        self,
        system_proxy: Mock,
        _windows_system_proxy: Mock,
        _reachable: Mock,
    ) -> None:
        # Системный прокси в Network preferences часто указывает на
        # выключенный VPN-клиент.
        system_proxy.return_value = ("http://127.0.0.1:10809", "localhost")
        with _without_proxy_environment():
            self.assertEqual(self.tools._resolve_ipatool_proxy_env(), {})

    @patch("apprestore_core.tools.proxy_is_reachable", return_value=True)
    @patch("apprestore_core.tools.windows_system_proxy", return_value=None)
    @patch("apprestore_core.tools.macos_system_proxy")
    def test_reachable_macos_system_proxy_reaches_ipatool(
        self,
        system_proxy: Mock,
        _windows_system_proxy: Mock,
        _reachable: Mock,
    ) -> None:
        system_proxy.return_value = ("http://127.0.0.1:10809", "localhost,::1")
        with _without_proxy_environment():
            resolved = self.tools._resolve_ipatool_proxy_env()

        self.assertEqual(
            resolved,
            {
                "HTTP_PROXY": "http://127.0.0.1:10809",
                "HTTPS_PROXY": "http://127.0.0.1:10809",
                "NO_PROXY": "localhost,::1",
            },
        )

    @patch("apprestore_core.tools.resolve_tool", return_value="ipatool")
    def test_every_networked_ipatool_call_carries_the_proxy(
        self,
        _resolve: Mock,
    ) -> None:
        proxy = {"HTTPS_PROXY": "http://127.0.0.1:10809"}
        self.tools._ipatool_proxy_env = proxy

        self.tools.ipatool_login("owner@example.com")
        with tempfile.TemporaryDirectory() as directory:
            self.tools.download_ipa(
                Path(directory) / "out.ipa",
                bundle_id="com.example.alpha",
            )
        self.runner.stdout = "[]"
        self.tools.search_apps("alpha")

        self.assertTrue(self.runner.calls)
        for command, keywords in self.runner.calls:
            with self.subTest(command=command[:2]):
                self.assertEqual(keywords.get("env"), proxy)


if __name__ == "__main__":
    unittest.main()
