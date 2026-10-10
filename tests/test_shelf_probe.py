from __future__ import annotations

import unittest

from apprestore_gui.shelf_probe import classify_offer


class ClassifyOfferTests(unittest.TestCase):
    def test_license_and_purchase_and_progress_stay_distinct(self) -> None:
        self.assertEqual(classify_offer(""), "pending")
        self.assertEqual(
            classify_offer('ERR error="license is required" success=false'),
            "needs-license",
        )
        self.assertEqual(
            classify_offer(
                "ERR error=\"failed to purchase item with param 'STDQ': "
                'failed to purchase app" success=false'
            ),
            "refused",
        )
        self.assertEqual(classify_offer("downloading 0%"), "pending")
        self.assertEqual(classify_offer("downloading 12% |######"), "offers")
        self.assertEqual(
            classify_offer("keychain passphrase is required"),
            "closed",
        )


if __name__ == "__main__":
    unittest.main()
