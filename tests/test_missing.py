"""Tests for missing/deleted app discovery and restore path."""

from __future__ import annotations

import plistlib
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from apprestore_core.catalog import (
    installed_bundle_ids,
    load_imazing_app_records,
    parse_missing_apps,
)
from apprestore_core.cli import _resolve_missing_targets
from apprestore_core.ipa import read_ipa_metadata
from apprestore_core.known_apps import remember_known_app
from apprestore_core.models import MissingApp
from apprestore_core.service import AppRestoreService

from tests.helpers import make_ipa


class MissingCatalogTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_excludes_installed_and_keeps_history_only(self) -> None:
        local = read_ipa_metadata(make_ipa(self.root / "gone.ipa", bundle_id="com.example.gone"))
        installed = {"com.example.still"}
        records = {
            "com.example.still": {
                "bundle_id": "com.example.still",
                "name": "Still",
                "version": "2.0",
                "store_id": "111",
            },
            "com.example.history": {
                "bundle_id": "com.example.history",
                "name": "History",
                "version": "1.2",
                "store_id": "222",
            },
        }
        apps = parse_missing_apps(
            installed=installed,
            imazing_records=records,
            local_ipas=[local],
        )
        bundle_ids = {app.bundle_id for app in apps}
        self.assertEqual(bundle_ids, {"com.example.history", "com.example.gone"})
        history = next(app for app in apps if app.bundle_id == "com.example.history")
        self.assertEqual(history.store_id, "222")
        self.assertEqual(history.source, "imazing")
        gone = next(app for app in apps if app.bundle_id == "com.example.gone")
        self.assertEqual(gone.source, "local-ipa")
        self.assertEqual(gone.local_ipa, local.path)

    def test_installed_bundle_ids_include_placeholders(self) -> None:
        payload = {
            "com.example.full": {
                "CFBundleIdentifier": "com.example.full",
                "ApplicationType": "User",
            },
            "com.example.placeholder": {
                "CFBundleIdentifier": "com.example.placeholder",
                "IsPlaceholder": True,
            },
        }
        self.assertEqual(
            installed_bundle_ids(payload),
            {"com.example.full", "com.example.placeholder"},
        )

    def test_load_imazing_app_records(self) -> None:
        plist_path = self.root / "Apps.plist"
        with plist_path.open("wb") as handle:
            plistlib.dump(
                {
                    "com.example.alpha": {
                        "CFBundleIdentifier": "com.example.alpha",
                        "CFBundleDisplayName": "Alpha",
                        "CFBundleShortVersionString": "3.1",
                        "itemId": 555,
                    }
                },
                handle,
            )
        records = load_imazing_app_records([plist_path])
        self.assertEqual(records["com.example.alpha"]["name"], "Alpha")
        self.assertEqual(records["com.example.alpha"]["store_id"], "555")


class MissingCliHelpersTests(unittest.TestCase):
    def test_resolve_selection_and_manual_bundle(self) -> None:
        apps = [
            MissingApp("com.example.a", "A", source="imazing"),
            MissingApp("com.example.b", "B", source="local-ipa"),
        ]
        selected = _resolve_missing_targets("2", apps)
        self.assertEqual([app.bundle_id for app in selected], ["com.example.b"])
        manual = _resolve_missing_targets("com.example.manual", apps)
        self.assertEqual(manual[0].bundle_id, "com.example.manual")
        self.assertEqual(manual[0].source, "manual")
        by_store = _resolve_missing_targets("id6472732558", apps)
        self.assertEqual(by_store[0].store_id, "6472732558")

    def test_bare_name_triggers_search(self) -> None:
        service = mock.Mock()
        service.search_apps.return_value = [
            {
                "storeId": "6472732558",
                "bundleId": "com.example.homuz",
                "name": "Homuz",
                "source": "itunes",
            }
        ]
        service.find_local.return_value = None
        with (
            mock.patch("apprestore_core.cli.remember_known_app"),
            mock.patch("builtins.input", return_value="1"),
        ):
            targets = _resolve_missing_targets("Homuz", [], service=service)
        self.assertEqual(len(targets), 1)
        self.assertEqual(targets[0].store_id, "6472732558")
        self.assertEqual(targets[0].bundle_id, "com.example.homuz")
        service.search_apps.assert_called_once_with("Homuz")

    def test_search_prefix_still_works(self) -> None:
        service = mock.Mock()
        service.search_apps.return_value = []
        with mock.patch("builtins.input", return_value=""):
            targets = _resolve_missing_targets(
                "search домклик",
                [],
                service=service,
            )
        self.assertEqual(targets, [])
        service.search_apps.assert_called_once_with("домклик")


class MissingServiceTests(unittest.TestCase):
    def test_restore_missing_skips_device_redownload(self) -> None:
        service = AppRestoreService.__new__(AppRestoreService)
        service.tools = mock.Mock()
        service.tools.ipatool_authenticated.return_value = True
        service.download = mock.Mock(return_value=Path("x.ipa"))  # type: ignore[method-assign]
        metadata = mock.Mock()
        metadata.bundle_id = "com.example.x"
        metadata.name = "App"
        metadata.version = "1.0"
        service.install = mock.Mock(return_value=metadata)  # type: ignore[method-assign]
        service.resolve_missing_store_id = mock.Mock(return_value="999")  # type: ignore[method-assign]

        with tempfile.TemporaryDirectory() as temporary:
            known_path = Path(temporary) / "known-apps.json"
            with mock.patch(
                "apprestore_core.service.remember_known_app",
                side_effect=lambda **kwargs: remember_known_app(
                    path=known_path,
                    **kwargs,
                ),
            ):
                app = MissingApp("com.example.x", "X", store_id="999", source="manual")
                status = AppRestoreService.restore_missing(service, "UDID", app)

        self.assertIn("installed", status)
        service.tools.device_request_redownload.assert_not_called()
        service.download.assert_called_once_with(
            "com.example.x",
            "999",
            lookup_store_id=False,
        )


if __name__ == "__main__":
    unittest.main()
