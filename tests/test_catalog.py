from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from apprestore_core.catalog import (
    CatalogError,
    lookup_itunes_store_id,
    parse_json_output,
    parse_offloaded_apps,
    parse_udids,
)
from apprestore_core.ipa import read_ipa_metadata

from tests.helpers import make_ipa


class JsonParsingTests(unittest.TestCase):
    def test_parses_bom_and_device_shapes(self) -> None:
        self.assertEqual(parse_json_output("\ufeff{\"ok\": true}"), {"ok": True})
        self.assertEqual(
            parse_udids(
                '["DEVICE-A1", {"UniqueDeviceID": "DEVICE-B2"}, '
                '{"udid": "DEVICE-A1"}]'
            ),
            ["DEVICE-A1", "DEVICE-B2"],
        )

    def test_rejects_non_finite_or_malformed_json(self) -> None:
        for raw in (
            '{"value": NaN}',
            '{"value": 1, "value": 2}',
            "warning\n{}",
            "42",
            "",
        ):
            with self.subTest(raw=raw):
                if raw == "42":
                    with self.assertRaises(CatalogError):
                        parse_udids(raw)
                else:
                    with self.assertRaises(CatalogError):
                        parse_json_output(raw)

    def test_rejects_unsafe_device_identifier(self) -> None:
        with self.assertRaises(CatalogError):
            parse_udids('["SAFE-DEVICE-123", "bad\\nvalue"]')


class PlaceholderParsingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.local = read_ipa_metadata(make_ipa(self.root / "alpha.ipa"))

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_keeps_only_placeholders_and_exact_matches(self) -> None:
        payload = {
            "com.example.alpha": {
                "CFBundleIdentifier": "com.example.alpha",
                "CFBundleDisplayName": "Alpha",
                "IsPlaceholder": True,
                "StaticDiskUsage": "100",
                "DynamicDiskUsage": 20,
            },
            "com.example.normal": {
                "CFBundleIdentifier": "com.example.normal",
                "CFBundleDisplayName": "Alpha",
                "ApplicationType": "User",
            },
        }
        apps = parse_offloaded_apps(
            payload,
            [self.local],
            {"com.example.alpha": "123456", "com.example.normal": "999"},
        )
        self.assertEqual(len(apps), 1)
        self.assertEqual(apps[0].bundle_id, "com.example.alpha")
        self.assertEqual(apps[0].local_ipa, self.local.path)
        self.assertEqual(apps[0].store_id, "123456")
        self.assertEqual(apps[0].store_match, "exact-imazing")
        self.assertEqual(apps[0].static_size + apps[0].dynamic_size, 120)

    def test_prefers_exact_device_store_id(self) -> None:
        payload = [
            {
                "CFBundleIdentifier": "com.example.alpha",
                "IsDemotedApp": True,
                "iTunesMetadata": {"itemId": 321},
            }
        ]
        apps = parse_offloaded_apps(
            payload,
            [],
            {"com.example.alpha": "123"},
        )
        self.assertEqual(apps[0].store_id, "321")
        self.assertEqual(apps[0].store_match, "device")

    def test_never_matches_by_display_name(self) -> None:
        payload = [
            {
                "CFBundleIdentifier": "com.example.other",
                "CFBundleDisplayName": "Alpha",
                "IsPlaceholder": True,
            }
        ]
        apps = parse_offloaded_apps(payload, [self.local], {})
        self.assertIsNone(apps[0].local_ipa)

    def test_rejects_conflicting_or_duplicate_bundle_ids(self) -> None:
        conflict = {
            "com.example.alpha": {
                "CFBundleIdentifier": "com.example.beta",
                "IsPlaceholder": True,
            }
        }
        duplicate = [
            {"CFBundleIdentifier": "com.example.alpha", "IsPlaceholder": True},
            {"CFBundleIdentifier": "com.example.alpha", "IsPlaceholder": True},
        ]
        for payload in (conflict, duplicate):
            with self.subTest(payload=payload), self.assertRaises(CatalogError):
                parse_offloaded_apps(payload, [], {})

    def test_removes_terminal_control_characters(self) -> None:
        apps = parse_offloaded_apps(
            [
                {
                    "CFBundleIdentifier": "com.example.alpha",
                    "CFBundleDisplayName": "Alpha\x1b[31m\nInjected",
                    "IsPlaceholder": True,
                }
            ],
            [],
            {},
        )
        self.assertNotIn("\x1b", apps[0].name)
        self.assertNotIn("\n", apps[0].name)

    def test_lookup_itunes_store_id_reads_track_id(self) -> None:
        class FakeResponse:
            def __enter__(self):
                return self

            def __exit__(self, *_args: object) -> None:
                return None

            def read(self) -> bytes:
                return (
                    b'{"resultCount":1,"results":[{"bundleId":'
                    b'"com.example.alpha","trackId":424242}]}'
                )

        with mock.patch(
            "urllib.request.urlopen",
            return_value=FakeResponse(),
        ):
            self.assertEqual(
                lookup_itunes_store_id("com.example.alpha", countries=("",)),
                "424242",
            )

    def test_find_store_id_decodes_binary_itunes_metadata(self) -> None:
        import plistlib

        from apprestore_core.catalog import find_store_id

        blob = plistlib.dumps({"itemId": 998877, "playlistName": "x"})
        self.assertEqual(find_store_id({"iTunesMetadata": blob}), "998877")


if __name__ == "__main__":
    unittest.main()
