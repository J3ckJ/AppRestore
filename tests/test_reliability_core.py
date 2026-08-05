from __future__ import annotations

import io
import os
import shutil
import tempfile
import unittest
import zipfile
from contextlib import redirect_stderr
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from apprestore_core.ipa import read_ipa_metadata
from apprestore_core.models import (
    DeviceAppState,
    MissingApp,
    OffloadedApp,
    RedownloadRequestState,
)
from apprestore_core.service import AppRestoreError, AppRestoreService
from apprestore_core.tools import InstallRequestState

from tests.helpers import make_ipa


class DeviceTools:
    def __init__(
        self,
        states: list[DeviceAppState] | None = None,
        versions: list[str | None] | None = None,
        install_result: InstallRequestState = InstallRequestState.COMPLETED,
    ) -> None:
        self.states = list(states or [DeviceAppState.INSTALLED])
        self.versions = list(versions or ["1.0"])
        self.install_result = install_result
        self.install_paths: list[Path] = []

    def install_ipa(self, _udid: str, ipa: Path) -> InstallRequestState:
        self.install_paths.append(ipa)
        return self.install_result

    def device_app_state(self, _udid: str, _bundle_id: str) -> DeviceAppState:
        if len(self.states) > 1:
            return self.states.pop(0)
        return self.states[0]

    def device_app_snapshot(
        self,
        _udid: str,
        _bundle_id: str,
    ) -> tuple[DeviceAppState, str | None]:
        state = self.device_app_state(_udid, _bundle_id)
        if len(self.versions) > 1:
            version = self.versions.pop(0)
        else:
            version = self.versions[0]
        return state, version


class SwappingTools(DeviceTools):
    def __init__(self, source: Path, replacement: Path) -> None:
        super().__init__()
        self.source = source
        self.replacement = replacement
        self.installed_bundle: str | None = None

    def install_ipa(self, _udid: str, ipa: Path) -> InstallRequestState:
        # Simulate an attacker replacing the caller-controlled file at the
        # exact tool boundary.  The private staged snapshot must be unaffected.
        shutil.copyfile(self.replacement, self.source)
        self.installed_bundle = read_ipa_metadata(ipa).bundle_id
        return super().install_ipa(_udid, ipa)


class MutatingStagedTools(DeviceTools):
    def __init__(self, replacement: Path) -> None:
        super().__init__()
        self.replacement = replacement
        self.installed_bytes: bytes | None = None

    def install_ipa(self, _udid: str, ipa: Path) -> InstallRequestState:
        # Keep inode, size, metadata and mtime unchanged: only the final digest
        # can distinguish this hostile snapshot from the verified one.
        before = ipa.stat()
        ipa.parent.chmod(0o700)
        ipa.chmod(0o600)
        shutil.copyfile(self.replacement, ipa)
        os.utime(ipa, ns=(before.st_atime_ns, before.st_mtime_ns))
        self.installed_bytes = ipa.read_bytes()
        return super().install_ipa(_udid, ipa)


class ReliabilityCoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.library = self.root / "library"
        self.cache = self.root / "cache"
        self.remember_patch = mock.patch(
            "apprestore_core.service.remember_known_app"
        )
        self.remember = self.remember_patch.start()

    def tearDown(self) -> None:
        self.remember_patch.stop()
        self.temporary.cleanup()

    def service(self, tools: object) -> AppRestoreService:
        return AppRestoreService(
            tools=tools,  # type: ignore[arg-type]
            library=self.library,
            cache=self.cache,
        )

    def test_install_uses_owned_snapshot_if_source_is_replaced(self) -> None:
        source = make_ipa(self.root / "source.ipa", bundle_id="com.example.good")
        replacement = make_ipa(
            self.root / "replacement.ipa",
            bundle_id="com.example.evil",
        )
        tools = SwappingTools(source, replacement)

        metadata = self.service(tools).install(
            "UDID",
            source,
            expected_bundle_id="com.example.good",
        )

        self.assertEqual(metadata.bundle_id, "com.example.good")
        self.assertEqual(tools.installed_bundle, "com.example.good")
        self.assertEqual(read_ipa_metadata(source).bundle_id, "com.example.evil")
        self.assertNotEqual(tools.install_paths[0], source.resolve())

    def test_install_rejects_mutation_of_the_owned_staged_snapshot(self) -> None:
        source = make_ipa(self.root / "source.ipa", bundle_id="com.example.good")
        with zipfile.ZipFile(source, "a") as archive:
            archive.comment = b"A"
        replacement = self.root / "replacement.ipa"
        shutil.copyfile(source, replacement)
        with zipfile.ZipFile(replacement, "a") as archive:
            archive.comment = b"B"
        self.assertEqual(source.stat().st_size, replacement.stat().st_size)
        self.assertNotEqual(source.read_bytes(), replacement.read_bytes())
        tools = MutatingStagedTools(replacement)

        with self.assertRaisesRegex(AppRestoreError, "staged IPA changed"):
            self.service(tools).install(
                "UDID",
                source,
                expected_bundle_id="com.example.good",
            )

        self.assertEqual(tools.installed_bytes, replacement.read_bytes())

    def test_backend_exit_is_not_success_without_device_postcondition(self) -> None:
        source = make_ipa(self.root / "source.ipa")
        tools = DeviceTools([DeviceAppState.ABSENT])
        service = self.service(tools)
        service.INSTALL_VERIFY_TIMEOUT = 0

        with self.assertRaisesRegex(AppRestoreError, "did not confirm"):
            service.install("UDID", source)

        self.assertEqual(len(tools.install_paths), 1)

    def test_indeterminate_backend_defers_to_device_postcondition(self) -> None:
        source = make_ipa(self.root / "source.ipa")
        tools = DeviceTools(
            [DeviceAppState.ABSENT, DeviceAppState.INSTALLED],
            [None, "1.0"],
            install_result=InstallRequestState.INDETERMINATE,
        )

        metadata = self.service(tools).install("UDID", source)

        self.assertEqual(metadata.bundle_id, "com.example.alpha")
        self.assertEqual(len(tools.install_paths), 1)

    def test_failed_before_request_never_uses_preinstalled_app_as_success(
        self,
    ) -> None:
        source = make_ipa(self.root / "source.ipa")
        tools = DeviceTools(
            [DeviceAppState.INSTALLED],
            ["1.0"],
            install_result=InstallRequestState.FAILED_BEFORE_REQUEST,
        )

        with self.assertRaisesRegex(AppRestoreError, "before it could submit"):
            self.service(tools).install("UDID", source)

        self.assertEqual(len(tools.install_paths), 1)

    def test_indeterminate_install_needs_change_if_app_was_preinstalled(
        self,
    ) -> None:
        source = make_ipa(self.root / "source.ipa")
        tools = DeviceTools(
            [DeviceAppState.INSTALLED],
            ["1.0"],
            install_result=InstallRequestState.INDETERMINATE,
        )
        service = self.service(tools)
        service.INSTALL_VERIFY_TIMEOUT = 0

        with self.assertRaisesRegex(AppRestoreError, "already present"):
            service.install("UDID", source)

    def test_completed_install_rejects_a_known_wrong_device_version(self) -> None:
        source = make_ipa(self.root / "source.ipa")
        tools = DeviceTools(
            [DeviceAppState.INSTALLED],
            ["0.9"],
        )
        service = self.service(tools)
        service.INSTALL_VERIFY_TIMEOUT = 0

        with self.assertRaisesRegex(AppRestoreError, "version 1.0"):
            service.install("UDID", source)

    def test_absent_after_native_redownload_is_never_reported_as_success(self) -> None:
        tools = mock.Mock()
        tools.device_request_redownload.return_value = (
            RedownloadRequestState.INDETERMINATE
        )
        tools.device_app_state.return_value = DeviceAppState.ABSENT
        service = self.service(tools)
        service.REDOWNLOAD_START_TIMEOUT = 0
        app = OffloadedApp("com.example.app", "App", "1.0")

        with self.assertRaisesRegex(AppRestoreError, "refusing a competing"):
            service.restore_offloaded("UDID", app)

        tools.ipatool_authenticated.assert_not_called()

    def test_native_download_requires_installed_state(self) -> None:
        tools = mock.Mock()
        tools.device_request_redownload.return_value = RedownloadRequestState.COMPLETED
        tools.device_app_state.side_effect = [
            DeviceAppState.DOWNLOADING,
            DeviceAppState.INSTALLED,
        ]
        service = self.service(tools)
        app = OffloadedApp("com.example.app", "App", "1.0")

        with mock.patch("apprestore_core.service.time.sleep"):
            status = service.restore_offloaded("UDID", app)

        self.assertIn("confirmed", status)
        tools.ipatool_authenticated.assert_not_called()
        self.remember.assert_called_once_with(
            store_id=None,
            bundle_id="com.example.app",
            name="App",
            version="1.0",
            provenance="native-redownload",
            status="restored",
        )

    def test_native_download_has_global_completion_deadline(self) -> None:
        tools = mock.Mock()
        tools.device_request_redownload.return_value = (
            RedownloadRequestState.INDETERMINATE
        )
        tools.device_app_state.side_effect = [
            DeviceAppState.DOWNLOADING,
            DeviceAppState.OFFLOADED,
        ]
        service = self.service(tools)
        service.REDOWNLOAD_COMPLETE_TIMEOUT = 0.5
        app = OffloadedApp("com.example.app", "App", "1.0")

        with (
            mock.patch("apprestore_core.service.time.monotonic", side_effect=[0, 0, 1]),
            mock.patch("apprestore_core.service.time.sleep"),
            self.assertRaisesRegex(AppRestoreError, "did not finish"),
        ):
            service.restore_offloaded("UDID", app)

        tools.ipatool_authenticated.assert_not_called()

    def test_missing_and_find_local_share_one_inventory_scan(self) -> None:
        ipa_path = make_ipa(self.root / "local.ipa")
        metadata = read_ipa_metadata(ipa_path)
        tools = mock.Mock()
        tools.list_apps.return_value = {}
        service = self.service(tools)

        with (
            mock.patch(
                "apprestore_core.service.scan_ipas",
                return_value=([metadata], []),
            ) as scan,
            mock.patch(
                "apprestore_core.service.load_imazing_app_records",
                return_value={},
            ),
            mock.patch("apprestore_core.service.load_known_apps", return_value=[]),
        ):
            service.missing("UDID")
            found = service.find_local(metadata.bundle_id)

        self.assertEqual(found, metadata.path)
        scan.assert_called_once()

    def test_unconfirmed_search_candidate_is_not_missing_history(self) -> None:
        service = self.service(mock.Mock())
        records = [
            {
                "storeId": "12345678",
                "bundleId": "com.example.candidate",
                "name": "Candidate",
                "status": "candidate",
            },
            {
                "storeId": "87654321",
                "bundleId": "com.example.restored",
                "name": "Restored",
                "status": "restored",
            },
        ]
        with mock.patch(
            "apprestore_core.service.load_known_apps",
            return_value=records,
        ):
            apps = service._merge_known_missing([], set(), [])

        self.assertEqual(
            [app.bundle_id for app in apps],
            ["com.example.restored"],
        )

    def test_bundle_only_restored_history_is_a_missing_candidate(self) -> None:
        service = self.service(mock.Mock())
        records = [
            {
                "storeId": None,
                "bundleId": "com.example.bundle-only",
                "name": "Bundle Only",
                "version": "1.2",
                "status": "restored",
            }
        ]
        with mock.patch(
            "apprestore_core.service.load_known_apps",
            return_value=records,
        ):
            apps = service._merge_known_missing([], set(), [])

        self.assertEqual(len(apps), 1)
        self.assertEqual(apps[0].bundle_id, "com.example.bundle-only")
        self.assertIsNone(apps[0].store_id)
        self.assertEqual(apps[0].source, "known")

    def test_offloaded_local_and_download_paths_record_restored_history(
        self,
    ) -> None:
        metadata = SimpleNamespace(
            bundle_id="com.example.app",
            name="App",
            version="2.0",
        )
        service = self.service(mock.Mock())
        service.install = mock.Mock(return_value=metadata)  # type: ignore[method-assign]

        local = OffloadedApp(
            "com.example.app",
            "App",
            "1.0",
            local_ipa=self.root / "App.ipa",
        )
        status = service.restore_offloaded("UDID", local)
        self.assertIn("local IPA", status)
        self.remember.assert_called_once_with(
            store_id=None,
            bundle_id="com.example.app",
            name="App",
            version="2.0",
            provenance="local-ipa-install",
            status="restored",
        )

        self.remember.reset_mock()
        service.tools.ipatool_authenticated.return_value = True
        service.resolve_store_id = mock.Mock(return_value=None)  # type: ignore[method-assign]
        service.download = mock.Mock(return_value=Path("App.ipa"))  # type: ignore[method-assign]
        remote = OffloadedApp("com.example.app", "App", "1.0")
        status = service.restore_offloaded(
            "UDID",
            remote,
            try_device_redownload=False,
        )
        self.assertIn("installed", status)
        self.remember.assert_called_once_with(
            store_id=None,
            bundle_id="com.example.app",
            name="App",
            version="2.0",
            provenance="device-install",
            status="restored",
        )

    def test_missing_local_records_history_and_history_failure_is_best_effort(
        self,
    ) -> None:
        metadata = SimpleNamespace(
            bundle_id="com.example.missing",
            name="Missing",
            version="3.0",
        )
        service = self.service(mock.Mock())
        service.install = mock.Mock(return_value=metadata)  # type: ignore[method-assign]
        self.remember.side_effect = OSError("history is read-only")
        app = MissingApp(
            "com.example.missing",
            "Missing",
            local_ipa=self.root / "Missing.ipa",
        )

        warning = io.StringIO()
        with redirect_stderr(warning):
            status = service.restore_missing("UDID", app)

        self.assertIn("local IPA", status)
        self.assertIn("history", warning.getvalue())
        self.remember.assert_called_once_with(
            store_id=None,
            bundle_id="com.example.missing",
            name="Missing",
            version="3.0",
            provenance="local-ipa-install",
            status="restored",
        )


if __name__ == "__main__":
    unittest.main()
