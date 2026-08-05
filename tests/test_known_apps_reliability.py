"""Durability and schema migration tests for the known-apps store."""

from __future__ import annotations

import json
import multiprocessing
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from apprestore_core.known_apps import (
    KnownAppsDataError,
    load_known_apps,
    remember_known_app,
    remember_known_apps,
)


def _concurrent_writer(path_text: str, worker: int, records: int) -> None:
    path = Path(path_text)
    for offset in range(records):
        store_id = str(100_000_000 + worker * records + offset)
        remember_known_app(
            store_id=store_id,
            bundle_id=f"com.example.worker{worker}.app{offset}",
            name=f"Worker {worker} App {offset}",
            provenance=f"worker-{worker}",
            path=path,
        )


def _concurrent_identity_writer(path_text: str, worker: int) -> None:
    remember_known_app(
        store_id="6472732558" if worker % 2 else None,
        bundle_id="com.example.shared",
        name="Shared App",
        status="restored" if worker == 0 else "confirmed",
        provenance=f"identity-worker-{worker}",
        path=Path(path_text),
    )


class KnownAppsReliabilityTests(unittest.TestCase):
    def test_v1_is_loaded_and_upgraded_on_next_write(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "known-apps.json"
            path.write_text(
                json.dumps(
                    {
                        "schemaVersion": 1,
                        "apps": [
                            {
                                "storeId": "6472732558",
                                "bundleId": "com.example.old",
                                "name": "Old App",
                                "version": "1.0",
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )

            migrated = load_known_apps(path)
            self.assertEqual(migrated[0]["status"], "confirmed")
            self.assertEqual(migrated[0]["provenance"], ["legacy"])

            remember_known_app(
                store_id="6472732558",
                version="1.1",
                provenance="download",
                status="restored",
                path=path,
            )
            payload = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(payload["schemaVersion"], 3)
            self.assertEqual(payload["apps"][0]["version"], "1.1")
            self.assertEqual(payload["apps"][0]["status"], "restored")
            self.assertEqual(
                payload["apps"][0]["provenance"],
                ["download", "legacy"],
            )

    def test_v2_is_loaded_and_upgraded_without_losing_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "known-apps.json"
            path.write_text(
                json.dumps(
                    {
                        "schemaVersion": 2,
                        "apps": [
                            {
                                "storeId": "6472732558",
                                "bundleId": "com.example.v2",
                                "name": "Version Two",
                                "version": "2.0",
                                "status": "restored",
                                "provenance": ["device-install"],
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )

            loaded = load_known_apps(path)
            self.assertEqual(loaded[0]["status"], "restored")
            self.assertEqual(loaded[0]["provenance"], ["device-install"])

            remember_known_app(
                bundle_id="com.example.bundle-only",
                status="restored",
                provenance="native-redownload",
                path=path,
            )
            payload = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(payload["schemaVersion"], 3)
            self.assertEqual(len(payload["apps"]), 2)

    def test_status_cannot_be_downgraded_and_provenance_is_merged(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "known-apps.json"
            remember_known_app(
                store_id="6472732558",
                status="restored",
                provenance="device-install",
                path=path,
            )
            remember_known_app(
                store_id="6472732558",
                name="Homuz",
                status="candidate",
                provenance=("catalog", "device-install"),
                path=path,
            )

            app = load_known_apps(path)[0]
            self.assertEqual(app["status"], "restored")
            self.assertEqual(app["name"], "Homuz")
            self.assertEqual(
                app["provenance"],
                ["catalog", "device-install"],
            )

    def test_weaker_update_cannot_replace_verified_identity(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "known-apps.json"
            remember_known_app(
                store_id="6472732558",
                bundle_id="com.example.verified",
                name="Verified App",
                version="2.0",
                status="restored",
                provenance="device-install",
                path=path,
            )

            remember_known_app(
                store_id="6472732558",
                bundle_id="com.example.untrusted",
                name="Catalog Collision",
                version="99.0",
                status="candidate",
                provenance="catalog",
                path=path,
            )

            app = load_known_apps(path)[0]
            self.assertEqual(app["bundleId"], "com.example.verified")
            self.assertEqual(app["name"], "Verified App")
            self.assertEqual(app["version"], "2.0")
            self.assertEqual(app["status"], "restored")
            self.assertEqual(
                app["provenance"],
                ["catalog", "device-install"],
            )

            remember_known_app(
                store_id="6472732558",
                bundle_id="com.example.verified",
                name="Renamed Verified App",
                version="2.1",
                status="candidate",
                provenance="catalog-refresh",
                path=path,
            )
            refreshed = load_known_apps(path)[0]
            self.assertEqual(refreshed["bundleId"], "com.example.verified")
            self.assertEqual(refreshed["name"], "Renamed Verified App")
            self.assertEqual(refreshed["version"], "2.1")

    def test_bundle_only_restore_is_enriched_without_duplicate(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "known-apps.json"
            remember_known_app(
                bundle_id="com.example.history",
                name="History",
                status="restored",
                provenance="native-redownload",
                path=path,
            )
            remember_known_app(
                store_id="11111111",
                bundle_id="com.example.history",
                status="candidate",
                provenance="untrusted-catalog",
                path=path,
            )
            candidate_ignored = load_known_apps(path)
            self.assertEqual(len(candidate_ignored), 1)
            self.assertIsNone(candidate_ignored[0]["storeId"])

            remember_known_app(
                store_id="6472732558",
                bundle_id="com.example.history",
                status="confirmed",
                provenance="store-lookup",
                path=path,
            )

            apps = load_known_apps(path)
            self.assertEqual(len(apps), 1)
            self.assertEqual(apps[0]["storeId"], "6472732558")
            self.assertEqual(apps[0]["bundleId"], "com.example.history")
            self.assertEqual(apps[0]["status"], "restored")
            self.assertEqual(
                apps[0]["provenance"],
                ["native-redownload", "store-lookup", "untrusted-catalog"],
            )

    def test_bridge_merges_store_only_and_bundle_only_records(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "known-apps.json"
            remember_known_app(
                store_id="6472732558",
                name="Store Record",
                status="candidate",
                path=path,
            )
            remember_known_app(
                bundle_id="com.example.bridge",
                name="Bundle Record",
                status="restored",
                path=path,
            )
            self.assertEqual(len(load_known_apps(path)), 2)

            remember_known_app(
                store_id="6472732558",
                bundle_id="com.example.bridge",
                status="confirmed",
                path=path,
            )

            apps = load_known_apps(path)
            self.assertEqual(len(apps), 1)
            self.assertEqual(apps[0]["storeId"], "6472732558")
            self.assertEqual(apps[0]["bundleId"], "com.example.bridge")
            self.assertEqual(apps[0]["status"], "restored")

    def test_weak_bridge_does_not_collapse_conflicting_restored_records(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "known-apps.json"
            remember_known_app(
                store_id="11111111",
                bundle_id="com.example.first",
                status="restored",
                path=path,
            )
            remember_known_app(
                store_id="22222222",
                bundle_id="com.example.second",
                status="restored",
                path=path,
            )

            remember_known_app(
                store_id="11111111",
                bundle_id="com.example.second",
                status="candidate",
                path=path,
            )

            identities = {
                (app["storeId"], app["bundleId"])
                for app in load_known_apps(path)
            }
            self.assertEqual(
                identities,
                {
                    ("11111111", "com.example.first"),
                    ("22222222", "com.example.second"),
                },
            )

    def test_batch_is_deduplicated_and_committed_without_temp_files(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            path = root / "known-apps.json"
            remember_known_apps(
                (
                    {
                        "storeId": "6472732558",
                        "name": "First",
                        "provenance": "catalog",
                        "status": "candidate",
                    },
                    {
                        "storeId": "6472732558",
                        "bundleId": "com.example.homuz",
                        "name": "Homuz",
                        "provenance": "user-selection",
                        "status": "confirmed",
                    },
                    {
                        "storeId": "899525659",
                        "name": "Thanks",
                    },
                ),
                path=path,
            )

            apps = {item["storeId"]: item for item in load_known_apps(path)}
            self.assertEqual(set(apps), {"6472732558", "899525659"})
            self.assertEqual(apps["6472732558"]["name"], "Homuz")
            self.assertEqual(apps["6472732558"]["status"], "confirmed")
            self.assertEqual(
                apps["6472732558"]["provenance"],
                ["catalog", "user-selection"],
            )
            self.assertEqual(list(root.glob(".known-apps.json.*.tmp")), [])

    def test_invalid_status_is_rejected_without_touching_file(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "known-apps.json"
            with self.assertRaisesRegex(ValueError, "status must be one of"):
                remember_known_app(
                    store_id="6472732558",
                    status="maybe",
                    path=path,
                )
            self.assertFalse(path.exists())

    def test_record_requires_numeric_store_id_or_valid_bundle_id(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "known-apps.json"
            remember_known_app(store_id="not-numeric", path=path)
            remember_known_app(store_id="123", path=path)
            remember_known_app(bundle_id="../not-a-bundle", path=path)
            self.assertFalse(path.exists())

            remember_known_app(
                bundle_id="com.example.valid",
                status="restored",
                path=path,
            )
            apps = load_known_apps(path)
            self.assertEqual(len(apps), 1)
            self.assertIsNone(apps[0]["storeId"])
            self.assertEqual(apps[0]["bundleId"], "com.example.valid")

    def test_malformed_document_is_never_overwritten(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "known-apps.json"
            original = b'{"schemaVersion":3,"apps":['
            path.write_bytes(original)

            with self.assertRaisesRegex(KnownAppsDataError, "unreadable"):
                remember_known_app(
                    store_id="6472732558",
                    name="Must not replace history",
                    path=path,
                )

            self.assertEqual(path.read_bytes(), original)
            self.assertEqual(list(path.parent.glob(".known-apps.json.*.tmp")), [])

    def test_future_schema_is_never_downgraded_or_overwritten(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "known-apps.json"
            original = b'{"schemaVersion":99,"apps":[]}\n'
            path.write_bytes(original)

            with self.assertRaisesRegex(KnownAppsDataError, "newer"):
                remember_known_app(
                    store_id="6472732558",
                    name="Must not downgrade history",
                    path=path,
                )

            self.assertEqual(path.read_bytes(), original)

    def test_invalid_schema_type_is_never_treated_as_legacy(self) -> None:
        for schema_version in (True, "2", 0, -1):
            with self.subTest(schema_version=schema_version):
                with tempfile.TemporaryDirectory() as temporary:
                    path = Path(temporary) / "known-apps.json"
                    original = json.dumps(
                        {"schemaVersion": schema_version, "apps": []}
                    ).encode("utf-8")
                    path.write_bytes(original)

                    with self.assertRaisesRegex(
                        KnownAppsDataError,
                        "positive integer",
                    ):
                        remember_known_app(
                            store_id="6472732558",
                            path=path,
                        )

                    self.assertEqual(path.read_bytes(), original)

    def test_schema_three_unknown_status_is_never_silently_promoted(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "known-apps.json"
            original = json.dumps(
                {
                    "schemaVersion": 3,
                    "apps": [
                        {
                            "storeId": "6472732558",
                            "bundleId": "com.example.original",
                            "name": "Original",
                            "status": "maybe",
                            "provenance": ["manual"],
                        }
                    ],
                }
            ).encode("utf-8")
            path.write_bytes(original)

            self.assertEqual(load_known_apps(path), [])
            with self.assertRaisesRegex(KnownAppsDataError, "invalid status"):
                remember_known_app(
                    store_id="899525659",
                    name="Must not replace history",
                    path=path,
                )

            self.assertEqual(path.read_bytes(), original)

    def test_writer_never_creates_document_larger_than_read_limit(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            path = root / "known-apps.json"

            with self.assertRaisesRegex(KnownAppsDataError, "size limit"):
                remember_known_app(
                    store_id="6472732558",
                    name="x" * (8 * 1024 * 1024),
                    path=path,
                )

            self.assertFalse(path.exists())
            self.assertEqual(list(root.glob(".known-apps.json.*.tmp")), [])

    def test_failed_replace_preserves_previous_document_and_cleans_temp(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            path = root / "known-apps.json"
            remember_known_app(
                store_id="6472732558",
                name="Original",
                path=path,
            )
            original = path.read_bytes()

            with mock.patch(
                "apprestore_core.known_apps.os.replace",
                side_effect=OSError("simulated replace failure"),
            ):
                with self.assertRaisesRegex(OSError, "simulated replace failure"):
                    remember_known_app(
                        store_id="899525659",
                        name="Never committed",
                        path=path,
                    )

            self.assertEqual(path.read_bytes(), original)
            self.assertEqual(list(root.glob(".known-apps.json.*.tmp")), [])

    def test_concurrent_processes_do_not_lose_updates(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "known-apps.json"
            context = multiprocessing.get_context("spawn")
            workers = 4
            records = 5
            processes = [
                context.Process(
                    target=_concurrent_writer,
                    args=(str(path), worker, records),
                )
                for worker in range(workers)
            ]
            for process in processes:
                process.start()
            for process in processes:
                process.join(timeout=30)
                self.assertFalse(process.is_alive())
                self.assertEqual(process.exitcode, 0)

            apps = load_known_apps(path)
            self.assertEqual(len(apps), workers * records)
            self.assertEqual(
                len({item["storeId"] for item in apps}),
                workers * records,
            )
            json.loads(path.read_text(encoding="utf-8"))

    def test_concurrent_bundle_and_store_writers_converge(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "known-apps.json"
            context = multiprocessing.get_context("spawn")
            workers = 6
            processes = [
                context.Process(
                    target=_concurrent_identity_writer,
                    args=(str(path), worker),
                )
                for worker in range(workers)
            ]
            for process in processes:
                process.start()
            for process in processes:
                process.join(timeout=30)
                self.assertFalse(process.is_alive())
                self.assertEqual(process.exitcode, 0)

            apps = load_known_apps(path)
            self.assertEqual(len(apps), 1)
            self.assertEqual(apps[0]["storeId"], "6472732558")
            self.assertEqual(apps[0]["bundleId"], "com.example.shared")
            self.assertEqual(apps[0]["status"], "restored")
            self.assertEqual(
                apps[0]["provenance"],
                [f"identity-worker-{worker}" for worker in range(workers)],
            )


if __name__ == "__main__":
    unittest.main()
