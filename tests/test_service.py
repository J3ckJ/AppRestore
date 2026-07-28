from __future__ import annotations

import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from apprestore_core.ipa import IpaError
from apprestore_core.service import AppRestoreError, AppRestoreService

from tests.helpers import make_ipa


class FakeTools:
    def __init__(self, effects: list[Path | None] | None = None) -> None:
        self.effects = list(effects or [])
        self.download_calls: list[dict[str, object]] = []
        self.install_calls: list[tuple[str, Path]] = []

    def ipatool_authenticated(self) -> bool:
        return True

    def download_ipa(
        self,
        output: Path,
        *,
        bundle_id: str | None = None,
        store_id: str | None = None,
        purchase: bool = False,
    ) -> bool:
        self.download_calls.append(
            {
                "output": output,
                "bundle_id": bundle_id,
                "store_id": store_id,
                "purchase": purchase,
            }
        )
        effect = self.effects.pop(0) if self.effects else None
        if effect is None:
            return False
        output.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(effect, output)
        return True

    def install_ipa(self, udid: str, ipa: Path) -> None:
        self.install_calls.append((udid, ipa))


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

    def tearDown(self) -> None:
        self._lookup_patch.stop()
        self.temporary.cleanup()

    def service(self, tools: FakeTools) -> AppRestoreService:
        return AppRestoreService(
            tools=tools,  # type: ignore[arg-type]
            library=self.library,
            cache=self.cache,
        )

    def test_wrong_store_result_falls_back_to_exact_bundle(self) -> None:
        tools = FakeTools([self.wrong, self.wrong, self.good])
        target = self.service(tools).download("com.example.alpha", "123")
        self.assertTrue(target.is_file())
        self.assertEqual(len(tools.download_calls), 3)
        self.assertEqual(tools.download_calls[0]["store_id"], "123")
        self.assertFalse(tools.download_calls[0]["purchase"])
        self.assertEqual(tools.download_calls[1]["store_id"], "123")
        self.assertTrue(tools.download_calls[1]["purchase"])
        self.assertEqual(
            tools.download_calls[2]["bundle_id"],
            "com.example.alpha",
        )
        self.assertFalse(tools.download_calls[2]["purchase"])
        self.assertNotEqual(
            Path(tools.download_calls[0]["output"]).parent,
            Path(tools.download_calls[2]["output"]).parent,
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
        with self.assertRaisesRegex(AppRestoreError, "got bundle ID"):
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
        self.assertEqual(tools.install_calls, [("TEST-UDID", self.good.resolve())])

    def test_changed_file_is_blocked_before_install(self) -> None:
        tools = FakeTools()
        service = MutatingService(
            tools=tools,  # type: ignore[arg-type]
            library=self.library,
            cache=self.cache,
        )
        with self.assertRaisesRegex(AppRestoreError, "changed"):
            service.install(
                "TEST-UDID",
                self.good,
                expected_bundle_id="com.example.alpha",
            )
        self.assertEqual(tools.install_calls, [])

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
