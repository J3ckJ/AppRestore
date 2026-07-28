from __future__ import annotations

import plistlib
import zipfile
from pathlib import Path


def make_ipa(
    path: Path,
    *,
    bundle_id: object = "com.example.alpha",
    name: str = "Alpha",
    version: str = "1.0",
    binary: bool = False,
    app_directory: str = "Alpha.app",
    missing_plist: bool = False,
    malformed_plist: bool = False,
    second_root_app: bool = False,
    duplicate_plist: bool = False,
    nested_extension: bool = False,
) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    info = {
        "CFBundleIdentifier": bundle_id,
        "CFBundleDisplayName": name,
        "CFBundleShortVersionString": version,
    }
    plist_format = plistlib.FMT_BINARY if binary else plistlib.FMT_XML
    raw = b"not a plist" if malformed_plist else plistlib.dumps(
        info,
        fmt=plist_format,
        sort_keys=True,
    )
    main_name = f"Payload/{app_directory}/Info.plist"

    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("Payload/.keep", b"")
        if not missing_plist:
            archive.writestr(main_name, raw)
        if duplicate_plist:
            archive.writestr(main_name, raw)
        if second_root_app:
            archive.writestr("Payload/Beta.app/Info.plist", raw)
        if nested_extension:
            archive.writestr(
                f"Payload/{app_directory}/PlugIns/Widget.appex/Info.plist",
                plistlib.dumps(
                    {"CFBundleIdentifier": "com.example.alpha.widget"},
                    fmt=plist_format,
                ),
            )
    return path
