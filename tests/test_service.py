from __future__ import annotations

import os
import shutil
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

from apprestore_core.ipa import IpaError
from apprestore_core.models import DeviceAppState
from apprestore_core.service import AppRestoreError, AppRestoreService
from apprestore_core.tools import InstallRequestState, ToolUnavailable

from tests.helpers import make_ipa


class FakeTools:
    def __init__(self, effects: list[Path | str | None] | None = None) -> None:
        self.effects = list(effects or [])
        self.download_calls: list[dict[str, object]] = []
        self.purchase_calls: list[dict[str, object]] = []
        self.install_calls: list[tuple[str, Path]] = []

    def ipatool_authenticated(self) -> bool:
        return True

    def download_ipa(
        self,
        output: Path,
        *,
        bundle_id: str | None = None,
        store_id: str | None = None,
    ) -> bool:
        # download_ipa has no purchase parameter any more (R2); the key stays
        # so the older assertions keep reading naturally.
        self.download_calls.append(
            {
                "output": output,
                "bundle_id": bundle_id,
                "store_id": store_id,
                "purchase": False,
            }
        )
        effect = self.effects.pop(0) if self.effects else None
        if effect is None:
            return False
        if effect == "LICENSE":
            raise ToolUnavailable("license is required")
        output.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(effect, output)
        return True

    def purchase_license(self, store_id: str, *, grant: object) -> dict[str, object]:
        from apprestore_core.purchase_grant import require_grant

        require_grant(grant, store_id)
        self.purchase_calls.append({"store_id": store_id})
        return {"success": True}

    def account_country(self) -> str:
        return "ru"

    def install_ipa(self, udid: str, ipa: Path) -> InstallRequestState:
        self.install_calls.append((udid, ipa))
        return InstallRequestState.COMPLETED

    def device_app_state(self, _udid: str, _bundle_id: str) -> DeviceAppState:
        return DeviceAppState.INSTALLED

    def device_app_snapshot(
        self,
        _udid: str,
        _bundle_id: str,
    ) -> tuple[DeviceAppState, str | None]:
        return DeviceAppState.INSTALLED, "1.0"


class MutatingService(AppRestoreService):
    def _sha256(self, path: Path) -> str:
        digest = super()._sha256(path)
        with path.open("ab") as handle:
            handle.write(b"changed")
        return digest


class ServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.library = self.root / "library"
        self.cache = self.root / "cache"
        self.good = make_ipa(self.root / "good.ipa")
        self.wrong = make_ipa(
            self.root / "wrong.ipa",
            bundle_id="com.example.wrong",
        )
        self._lookup_patch = patch(
            "apprestore_core.service.lookup_itunes_store_id",
            return_value=None,
        )
        self.lookup = self._lookup_patch.start()
        self._remember_patch = patch(
            "apprestore_core.service.remember_known_app",
        )
        self.remember = self._remember_patch.start()
        self.journal = self.root / "licenses_acquired.jsonl"
        self._env_patch = patch.dict(os.environ, {"APPRESTORE_LICENSE_JOURNAL": str(self.journal)})
        self._env_patch.start()
        self.price: object = 0.0
        self._offer_patch = patch(
            "apprestore_core.license_gate.lookup_offer",
            side_effect=lambda store_id, countries=None: {
                "storeId": store_id, "bundleId": "com.example.alpha",
                "price": self.price, "country": (countries or ("ru",))[0],
            },
        )
        self._offer_patch.start()

    def tearDown(self) -> None:
        self._offer_patch.stop()
        self._env_patch.stop()
        self._remember_patch.stop()
        self._lookup_patch.stop()
        self.temporary.cleanup()

    def service(self, tools: FakeTools) -> AppRestoreService:
        return AppRestoreService(
            tools=tools,  # type: ignore[arg-type]
            library=self.library,
            cache=self.cache,
        )

    def test_wrong_store_result_falls_back_to_exact_bundle(self) -> None:
        tools = FakeTools([self.wrong, self.good])
        target = self.service(tools).download(
            "com.example.alpha",
            "12345678",
            acquire_license=True,
        )
        self.assertTrue(target.is_file())
        self.assertEqual(len(tools.download_calls), 2)
        self.assertEqual(tools.download_calls[0]["store_id"], "12345678")
        self.assertFalse(tools.download_calls[0]["purchase"])
        self.assertEqual(
            tools.download_calls[1]["bundle_id"],
            "com.example.alpha",
        )
        self.assertFalse(tools.download_calls[1]["purchase"])
        self.assertNotEqual(
            Path(tools.download_calls[0]["output"]).parent,
            Path(tools.download_calls[1]["output"]).parent,
        )
        self.assertTrue(
            all(not bool(call["purchase"]) for call in tools.download_calls)
        )

    def test_purchase_is_skipped_after_definitive_identity_mismatch(self) -> None:
        tools = FakeTools([self.wrong, self.wrong])
        with self.assertRaisesRegex(AppRestoreError, "another app|different|mismatch|bundle"):
            self.service(tools).download(
                "com.example.alpha",
                "12345678",
                acquire_license=True,
            )
        self.assertEqual(tools.purchase_calls, [])

        self.assertEqual(len(tools.download_calls), 2)
        self.assertTrue(
            all(not bool(call["purchase"]) for call in tools.download_calls)
        )

    def test_failure_never_uses_newest_decoy(self) -> None:
        decoy = make_ipa(
            self.library / "newest-decoy.ipa",
            bundle_id="com.example.wrong",
        )
        tools = FakeTools([None])
        with self.assertRaises(AppRestoreError):
            self.service(tools).download("com.example.alpha")
        self.assertTrue(decoy.exists())
        self.assertEqual(tools.install_calls, [])

    def test_wrong_bundle_is_never_committed(self) -> None:
        tools = FakeTools([self.wrong])
        with self.assertRaisesRegex(AppRestoreError, "expected 'com.example.alpha'"):
            self.service(tools).download("com.example.alpha")
        self.assertFalse((self.library / "com.example.alpha.ipa").exists())
        self.assertEqual(tools.install_calls, [])

    def test_pre_resolved_store_lookup_is_not_repeated(self) -> None:
        tools = FakeTools([self.good])
        target = self.service(tools).download(
            "com.example.alpha",
            lookup_store_id=False,
        )
        self.assertTrue(target.is_file())
        self.lookup.assert_not_called()

    def test_install_revalidates_exact_bundle_before_calling_tool(self) -> None:
        tools = FakeTools()
        service = self.service(tools)
        with self.assertRaisesRegex(AppRestoreError, "refusing"):
            service.install(
                "TEST-UDID",
                self.wrong,
                expected_bundle_id="com.example.alpha",
            )
        self.assertEqual(tools.install_calls, [])

        metadata = service.install(
            "TEST-UDID",
            self.good,
            expected_bundle_id="com.example.alpha",
        )
        self.assertEqual(metadata.bundle_id, "com.example.alpha")
        self.assertEqual(len(tools.install_calls), 1)
        installed_path = tools.install_calls[0][1]
        self.assertEqual(tools.install_calls[0][0], "TEST-UDID")
        self.assertNotEqual(installed_path, self.good.resolve())
        self.assertEqual(installed_path.name, "verified.ipa")

    def test_acquire_license_goes_through_the_gate(self) -> None:
        tools = FakeTools(["LICENSE", self.good])
        target = self.service(tools).download_by_store_id("12345678", acquire_license=True)
        self.assertTrue(target.is_file())
        self.assertEqual(tools.purchase_calls, [{"store_id": "12345678"}])
        self.assertEqual(len(tools.download_calls), 2)
        self.assertIn('"status": "acquired"', self.journal.read_text(encoding="utf-8"))
        self.assertIn('"mode": null', self.journal.read_text(encoding="utf-8"))

    def test_acquire_license_by_bundle_purchases_by_track_id_once(self) -> None:
        tools = FakeTools(["LICENSE", "LICENSE", self.good])
        target = self.service(tools).download(
            "com.example.alpha", "12345678", acquire_license=True
        )
        self.assertTrue(target.is_file())
        self.assertEqual(tools.purchase_calls, [{"store_id": "12345678"}])

    def test_acquire_license_refuses_paid_app(self) -> None:
        self.price = 1.99
        tools = FakeTools(["LICENSE"])
        with self.assertRaisesRegex(AppRestoreError, "платное"):
            self.service(tools).download_by_store_id("12345678", acquire_license=True)
        self.assertEqual(tools.purchase_calls, [])
        self.assertFalse(self.journal.exists())

    def test_purchase_attempts_require_explicit_opt_in(self) -> None:
        tools = FakeTools([self.wrong, self.wrong])
        with self.assertRaises(AppRestoreError):
            self.service(tools).download("com.example.alpha", "12345678")
        self.assertTrue(tools.download_calls)
        self.assertEqual(tools.purchase_calls, [])
        self.assertTrue(
            all(not bool(call["purchase"]) for call in tools.download_calls)
        )

    def test_verified_bundle_download_records_confirmed_history(self) -> None:
        tools = FakeTools([self.good])
        target = self.service(tools).download(
            "com.example.alpha",
            "12345678",
        )

        self.assertTrue(target.is_file())
        self.remember.assert_called_once_with(
            store_id="12345678",
            bundle_id="com.example.alpha",
            name="Alpha",
            version="1.0",
            provenance="verified-download",
            status="confirmed",
        )

    def test_invalid_explicit_store_id_is_rejected_before_download(self) -> None:
        tools = FakeTools([self.good])
        with self.assertRaisesRegex(AppRestoreError, "8-12 digit"):
            self.service(tools).download("com.example.alpha", "123")

        self.assertEqual(tools.download_calls, [])
        self.remember.assert_not_called()

    def test_changed_file_is_blocked_by_direct_verifier(self) -> None:
        tools = FakeTools()
        service = MutatingService(
            tools=tools,  # type: ignore[arg-type]
            library=self.library,
            cache=self.cache,
        )
        with self.assertRaisesRegex(AppRestoreError, "changed"):
            service._verify_ipa(
                self.good,
                expected_bundle_id="com.example.alpha",
            )
        self.assertEqual(tools.install_calls, [])

    def test_commit_rehashes_target_after_atomic_move(self) -> None:
        downloaded = make_ipa(self.root / "downloaded.ipa")
        with zipfile.ZipFile(downloaded, "a") as archive:
            archive.comment = b"A"
        replacement = self.root / "replacement.ipa"
        shutil.copyfile(downloaded, replacement)
        with zipfile.ZipFile(replacement, "a") as archive:
            archive.comment = b"B"
        self.assertEqual(downloaded.stat().st_size, replacement.stat().st_size)
        self.assertNotEqual(downloaded.read_bytes(), replacement.read_bytes())

        tools = FakeTools([downloaded])
        service = self.service(tools)
        real_replace = os.replace

        def replace_then_mutate(source: Path, target: Path) -> None:
            real_replace(source, target)
            committed = target.stat()
            shutil.copyfile(replacement, target)
            os.utime(
                target,
                ns=(committed.st_atime_ns, committed.st_mtime_ns),
            )

        expected_target = self.library / (
            f"com.example.alpha-1.0-{service._sha256(downloaded)[:12]}.ipa"
        )

        with (
            patch(
                "apprestore_core.service.os.replace",
                side_effect=replace_then_mutate,
            ),
            patch.object(service, "_cache_verified_ipa") as cache_verified,
            self.assertRaisesRegex(AppRestoreError, "final verification"),
        ):
            service.download("com.example.alpha")

        cache_verified.assert_not_called()
        self.remember.assert_not_called()
        self.assertFalse(expected_target.exists())

    def test_local_symlink_is_not_accepted(self) -> None:
        link = self.root / "link.ipa"
        try:
            link.symlink_to(self.good)
        except OSError:
            self.skipTest("symlink creation is unavailable")
        tools = FakeTools()
        with self.assertRaises(IpaError):
            self.service(tools).install("TEST-UDID", link)
        self.assertEqual(tools.install_calls, [])


if __name__ == "__main__":
    unittest.main()
