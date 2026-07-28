from __future__ import annotations

import os
import unittest
from pathlib import Path
from unittest.mock import patch

from apprestore_core.paths import imazing_apps_dirs, imazing_catalog_candidates


class WindowsPathTests(unittest.TestCase):
    def test_imazing_paths_follow_redirected_appdata(self) -> None:
        environment = {
            "APPDATA": r"D:\Profiles\Alice\Roaming",
            "LOCALAPPDATA": r"E:\Profiles\Alice\Local",
        }
        with (
            patch.dict(os.environ, environment, clear=False),
            patch("apprestore_core.paths.platform.system", return_value="Windows"),
        ):
            app_dirs = imazing_apps_dirs()
            catalogs = imazing_catalog_candidates()

        self.assertEqual(
            app_dirs,
            [
                Path(environment["APPDATA"]) / "iMazing" / "Library" / "Apps",
                Path(environment["APPDATA"])
                / "DigiDNA"
                / "iMazing"
                / "Library"
                / "Apps",
                Path(environment["LOCALAPPDATA"])
                / "DigiDNA"
                / "iMazing"
                / "Library"
                / "Apps",
            ],
        )
        self.assertEqual(
            catalogs,
            [
                Path(environment["APPDATA"])
                / "iMazing"
                / "Library"
                / "Apps.plist",
                Path(environment["APPDATA"])
                / "DigiDNA"
                / "iMazing"
                / "Library"
                / "Apps.plist",
                Path(environment["LOCALAPPDATA"])
                / "DigiDNA"
                / "iMazing"
                / "Library"
                / "Apps.plist",
            ],
        )


if __name__ == "__main__":
    unittest.main()
