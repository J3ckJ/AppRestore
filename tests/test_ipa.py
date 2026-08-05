from __future__ import annotations

import tempfile
import unittest
import warnings
import zipfile
from pathlib import Path
from unittest.mock import patch

from apprestore_core.ipa import (
    IpaError,
    read_ipa_metadata,
    scan_ipas,
    validate_bundle_id,
)
from apprestore_core.ipa_index import IpaIndex

from tests.helpers import make_ipa


class BundleIdTests(unittest.TestCase):
    def test_accepts_safe_ascii_identifier(self) -> None:
        self.assertEqual(validate_bundle_id("com.example.Alpha-1"), "com.example.Alpha-1")

    def test_rejects_unsafe_or_normalized_identifiers(self) -> None:
        invalid = [
            "",
            " com.example.alpha",
            "com.example.alpha ",
            ".com.example",
            "com.example.",
            "com..example",
            "../com.example",
            "com/example",
            "com\\example",
            "com.example:alpha",
            "сom.example.alpha",
            "a" * 256,
        ]
        for value in invalid:
            with self.subTest(value=value), self.assertRaises(IpaError):
                validate_bundle_id(value)

    def test_rejects_non_string(self) -> None:
        with self.assertRaises(IpaError):
            validate_bundle_id(123)  # type: ignore[arg-type]


class IpaMetadataTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_reads_xml_and_binary_plists(self) -> None:
        for binary in (False, True):
            with self.subTest(binary=binary):
                path = make_ipa(
                    self.root / f"alpha-{binary}.ipa",
                    binary=binary,
                    app_directory="My Alpha.app",
                )
                metadata = read_ipa_metadata(path)
                self.assertEqual(metadata.bundle_id, "com.example.alpha")
                self.assertEqual(metadata.name, "Alpha")
                self.assertEqual(metadata.version, "1.0")

    def test_ignores_nested_extension_plist(self) -> None:
        path = make_ipa(self.root / "nested.ipa", nested_extension=True)
        self.assertEqual(read_ipa_metadata(path).bundle_id, "com.example.alpha")

    def test_rejects_ambiguous_root_apps_and_duplicate_info(self) -> None:
        ambiguous = make_ipa(self.root / "ambiguous.ipa", second_root_app=True)
        with self.assertRaisesRegex(IpaError, "multiple"):
            read_ipa_metadata(ambiguous)

        with warnings.catch_warnings():
            warnings.simplefilter("ignore", UserWarning)
            duplicate = make_ipa(self.root / "duplicate.ipa", duplicate_plist=True)
        with self.assertRaisesRegex(IpaError, "multiple"):
            read_ipa_metadata(duplicate)

    def test_rejects_corrupt_missing_and_malformed_archives(self) -> None:
        corrupt = self.root / "corrupt.ipa"
        corrupt.write_bytes(b"not a zip")
        missing = make_ipa(self.root / "missing.ipa", missing_plist=True)
        malformed = make_ipa(self.root / "malformed.ipa", malformed_plist=True)
        for path in (corrupt, missing, malformed):
            with self.subTest(path=path), self.assertRaises(IpaError):
                read_ipa_metadata(path)

    def test_rejects_non_string_or_whitespace_bundle_id(self) -> None:
        numeric = make_ipa(self.root / "numeric.ipa", bundle_id=123)
        whitespace = make_ipa(
            self.root / "whitespace.ipa",
            bundle_id="com.example.alpha ",
        )
        for path in (numeric, whitespace):
            with self.subTest(path=path), self.assertRaises(IpaError):
                read_ipa_metadata(path)

    def test_rejects_multiple_root_info_even_when_one_sorts_first(self) -> None:
        path = self.root / "multi.ipa"
        raw = make_ipa(self.root / "source.ipa").read_bytes()
        path.write_bytes(raw)
        with zipfile.ZipFile(path, "a") as archive:
            archive.writestr(
                "Payload/A.app/Info.plist",
                (
                    "<?xml version='1.0'?><plist version='1.0'><dict>"
                    "<key>CFBundleIdentifier</key><string>com.example.evil</string>"
                    "</dict></plist>"
                ),
            )
        with self.assertRaisesRegex(IpaError, "multiple"):
            read_ipa_metadata(path)

    def test_scan_skips_invalid_ipas_but_keeps_valid_ones(self) -> None:
        good = make_ipa(self.root / "good.ipa")
        bad = self.root / "bad.ipa"
        bad.write_bytes(b"bad")
        entries, errors = scan_ipas([self.root])
        self.assertEqual([entry.path for entry in entries], [good.resolve()])
        self.assertEqual(len(errors), 1)
        self.assertEqual(errors[0][0], bad)

    def test_second_scan_reuses_persistent_metadata_index(self) -> None:
        good = make_ipa(self.root / "good.ipa")
        index = IpaIndex(self.root / "cache" / "ipas.sqlite3")

        first, first_errors = scan_ipas([self.root], index=index)
        with patch(
            "apprestore_core.ipa.read_ipa_metadata",
            side_effect=AssertionError("cached IPA was reopened"),
        ):
            second, second_errors = scan_ipas([self.root], index=index)

        self.assertEqual(first_errors, [])
        self.assertEqual(second_errors, [])
        self.assertEqual(first, second)
        self.assertEqual(second[0].path, good.resolve())

    def test_index_bounds_never_hide_otherwise_valid_ipa(self) -> None:
        good = make_ipa(
            self.root / "long-name.ipa",
            name="x" * 4_097,
        )
        index = IpaIndex(self.root / "cache" / "ipas.sqlite3")

        entries, errors = scan_ipas([self.root], index=index)

        self.assertEqual(errors, [])
        self.assertEqual([entry.path for entry in entries], [good.resolve()])
        self.assertEqual(entries[0].name, "x" * 4_097)
        self.assertEqual(index.count(), 0)


if __name__ == "__main__":
    unittest.main()
