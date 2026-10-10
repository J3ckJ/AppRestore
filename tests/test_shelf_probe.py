from __future__ import annotations

import unittest
from pathlib import Path
from unittest.mock import patch

from apprestore_core.models import CommandResult
from apprestore_core.tools import AppRestoreTools
from apprestore_gui import shelf_probe
from apprestore_gui.shelf_probe import classify_offer, probe_store


class ClassifyOfferTests(unittest.TestCase):
    def test_missing_license_means_not_owned_and_progress_stays_distinct(self) -> None:
        self.assertEqual(classify_offer(""), "pending")
        self.assertEqual(
            classify_offer('ERR error="license is required" success=false'),
            "not-owned",
        )
        self.assertEqual(classify_offer("downloading 0%"), "pending")
        self.assertEqual(classify_offer("downloading 12% |######"), "offers")
        self.assertEqual(classify_offer("keychain passphrase is required"), "closed")

    def test_there_is_no_granted_or_refused_outcome(self) -> None:
        self.assertEqual(
            classify_offer('ERR error="failed to purchase app" success=false'),
            "error",
        )
        self.assertNotIn("granted", shelf_probe.PROBE_OUTCOMES)
        self.assertNotIn("refused", shelf_probe.PROBE_OUTCOMES)


class _Runner:
    def __init__(self, stderr: str) -> None:
        self.stderr = stderr
        self.calls: list[tuple[str, ...]] = []
        self.on_output = None
        self.stop_when = None

    def run(self, args: list[str], **_kwargs: object) -> CommandResult:
        self.calls.append(tuple(args))
        return CommandResult(tuple(args), 1, "", self.stderr)


class ProbeNeverPurchasesTests(unittest.TestCase):
    def _probe(self, stderr: str) -> tuple[str, _Runner]:
        runner = _Runner(stderr)
        tools = AppRestoreTools(runner)  # type: ignore[arg-type]
        with patch.object(AppRestoreTools, "_tool", return_value="ipatool"), patch.object(
            AppRestoreTools, "_ipatool_env", return_value={}
        ):
            kind = probe_store(tools, "1234567890")
        return kind, runner

    def test_license_required_is_not_owned_after_one_read_only_call(self) -> None:
        kind, runner = self._probe('ERR error="license is required" success=false')
        self.assertEqual(kind, "not-owned")
        self.assertEqual(len(runner.calls), 1)
        self.assertEqual(runner.calls[0][1], "download")
        for call in runner.calls:
            self.assertNotIn("--purchase", call)
            self.assertNotIn("purchase", call)

    def test_no_outcome_ever_sends_purchase(self) -> None:
        for stderr in (
            "downloading 5%",
            'ERR error="license not found"',
            'ERR error="item is temporarily unavailable"',
            "keychain passphrase is required",
            "boom",
        ):
            kind, runner = self._probe(stderr)
            self.assertIn(kind, shelf_probe.PROBE_OUTCOMES)
            self.assertTrue(all("--purchase" not in call for call in runner.calls), stderr)

    def test_probe_args_reject_purchase(self) -> None:
        tools = AppRestoreTools(_Runner(""))  # type: ignore[arg-type]
        with patch.object(AppRestoreTools, "_tool", return_value="ipatool"):
            args = shelf_probe.probe_args(tools, "1", Path("x.ipa"))
        self.assertNotIn("--purchase", args)


if __name__ == "__main__":
    unittest.main()
