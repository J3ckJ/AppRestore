"""Tests for App Store ID parsing and known-apps memory."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from apprestore_core.catalog import search_app_catalogs, search_itunes_apps
from apprestore_core.cli import _resolve_missing_targets
from apprestore_core.known_apps import (
    load_known_apps,
    parse_app_store_id,
    remember_known_app,
)
from apprestore_core.models import MissingApp
from apprestore_core.service import AppRestoreService
from apprestore_core.web_discovery import _candidate_ids_from_html


class ParseStoreIdTests(unittest.TestCase):
    def test_digits_id_prefix_and_url(self) -> None:
        self.assertEqual(parse_app_store_id("6472732558"), "6472732558")
        self.assertEqual(parse_app_store_id("id6472732558"), "6472732558")
        self.assertEqual(
            parse_app_store_id(
                "https://apps.apple.com/ru/app/homuz/id6472732558"
            ),
            "6472732558",
        )
        self.assertIsNone(parse_app_store_id("com.example.app"))
        self.assertIsNone(parse_app_store_id("123"))


class KnownAppsMemoryTests(unittest.TestCase):
    def test_remember_and_load(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "known-apps.json"
            remember_known_app(
                store_id="6472732558",
                bundle_id="com.example.homuz",
                name="Homuz",
                version="1.0",
                path=path,
            )
            apps = load_known_apps(path)
            self.assertEqual(len(apps), 1)
            self.assertEqual(apps[0]["storeId"], "6472732558")
            self.assertEqual(apps[0]["bundleId"], "com.example.homuz")


class ResolveMissingTargetsTests(unittest.TestCase):
    def test_store_id_and_url(self) -> None:
        apps: list[MissingApp] = []
        by_id = _resolve_missing_targets("6472732558", apps)
        self.assertEqual(by_id[0].store_id, "6472732558")
        self.assertEqual(by_id[0].bundle_id, "")
        by_url = _resolve_missing_targets(
            "https://apps.apple.com/ru/app/homuz/id6472732558",
            apps,
        )
        self.assertEqual(by_url[0].store_id, "6472732558")


class DownloadByStoreIdTests(unittest.TestCase):
    def test_restore_missing_uses_store_only_path(self) -> None:
        service = AppRestoreService.__new__(AppRestoreService)
        service.restore_by_store_id = mock.Mock(return_value="installed X 1.0")  # type: ignore[method-assign]
        app = MissingApp("", "X", store_id="6472732558", source="manual")
        status = AppRestoreService.restore_missing(service, "UDID", app)
        self.assertEqual(status, "installed X 1.0")
        service.restore_by_store_id.assert_called_once_with("UDID", "6472732558")


class ItunesSearchTests(unittest.TestCase):
    def test_search_parses_track_results(self) -> None:
        payload = {
            "results": [
                {
                    "trackId": 6472732558,
                    "bundleId": "com.example.homuz",
                    "trackName": "Homuz",
                }
            ]
        }
        with mock.patch(
            "apprestore_core.catalog._http_json",
            return_value=payload,
        ):
            apps = search_itunes_apps("Homuz", limit=5, countries=("ru",))
        self.assertEqual(apps[0]["storeId"], "6472732558")
        self.assertEqual(apps[0]["bundleId"], "com.example.homuz")
        self.assertEqual(apps[0]["source"], "itunes")

    def test_catalog_merge_includes_archive_only(self) -> None:
        def fake_http(url: str, *, timeout: float = 20) -> object:
            del timeout
            if "ipafilezone.com" in url:
                return {
                    "results": [
                        {
                            "trackId": 111222333,
                            "trackName": "GoneApp",
                            "artistName": "Dev",
                        }
                    ]
                }
            if "itunes.apple.com/search" in url:
                return {"results": []}
            if "itunes.apple.com/lookup" in url:
                return {"results": []}
            return None

        with mock.patch(
            "apprestore_core.catalog._http_json",
            side_effect=fake_http,
        ):
            apps = search_app_catalogs("GoneApp", limit=5)
        self.assertEqual(len(apps), 1)
        self.assertEqual(apps[0]["storeId"], "111222333")
        self.assertIn("ipafilezone", apps[0]["source"])

    def test_domclick_alias_returns_dklik_hint(self) -> None:
        with mock.patch(
            "apprestore_core.catalog._http_json",
            return_value={"results": []},
        ):
            apps = search_app_catalogs("домклик", limit=5)
        store_ids = {app["storeId"] for app in apps}
        self.assertIn("1660762523", store_ids)
        self.assertIn("1143031400", store_ids)
        names = {app["name"] for app in apps}
        self.assertIn("ДКлик", names)

    def test_sber_alias_returns_online_hint(self) -> None:
        with mock.patch(
            "apprestore_core.catalog._http_json",
            return_value={"results": []},
        ):
            apps = search_app_catalogs("сбер", limit=5)
        self.assertTrue(apps)
        self.assertEqual(apps[0]["storeId"], "492224193")
        self.assertEqual(apps[0]["name"], "Сбербанк Онлайн")

    def test_web_html_scores_nearby_app_store_id(self) -> None:
        html = """
        <a href="https://apps.apple.com/ru/app/spasibo-ot-sberbanka/id899525659">
          СберСпасибо
        </a>
        <a href="https://apps.apple.com/us/app/garbage/id111111111">other</a>
        """
        ranked = _candidate_ids_from_html(html, "СберСпасибо")
        self.assertEqual(ranked[0][1], "899525659")
        self.assertGreaterEqual(ranked[0][0], 8)


if __name__ == "__main__":
    unittest.main()
