from __future__ import annotations

import os
import platform
import shutil
import sys
from pathlib import Path


def project_root() -> Path:
    return Path(__file__).resolve().parents[1]


def cache_dir() -> Path:
    configured = os.environ.get("APPRESTORE_CACHE_DIR")
    if configured:
        return Path(configured).expanduser()
    if platform.system() == "Windows":
        root = os.environ.get("LOCALAPPDATA")
        return Path(root) / "AppRestore" if root else Path.home() / "AppData" / "Local" / "AppRestore"
    xdg = os.environ.get("XDG_CACHE_HOME")
    return Path(xdg) / "AppRestore" if xdg else Path.home() / "Library" / "Caches" / "AppRestore"


def ipa_library_dir() -> Path:
    configured = os.environ.get("APPRESTORE_IPA_DIR")
    if configured:
        return Path(configured).expanduser()
    if platform.system() == "Darwin":
        return (
            Path.home()
            / "Library"
            / "Application Support"
            / "AppRestore"
            / "ipas"
        )
    return Path.home() / "AppRestore" / "ipas"


def imazing_apps_dirs() -> list[Path]:
    """Folders where iMazing keeps downloaded .ipa files."""
    home = Path.home()
    if platform.system() == "Darwin":
        return [
            home / "Library" / "Application Support" / "iMazing" / "Library" / "Apps",
        ]
    roaming = Path(os.environ.get("APPDATA") or home / "AppData" / "Roaming")
    local = Path(os.environ.get("LOCALAPPDATA") or home / "AppData" / "Local")
    return [
        roaming / "iMazing" / "Library" / "Apps",
        roaming / "DigiDNA" / "iMazing" / "Library" / "Apps",
        local / "DigiDNA" / "iMazing" / "Library" / "Apps",
    ]


def ipa_search_roots(library: Path | None = None) -> list[Path]:
    library = library or ipa_library_dir()
    home = Path.home()
    roots = [
        library,
        *imazing_apps_dirs(),
        home / "Downloads",
        home / "Music" / "iTunes" / "iTunes Media" / "Mobile Applications",
    ]
    extra = os.environ.get("APPRESTORE_EXTRA_IPA_DIRS")
    if extra:
        for part in extra.split(os.pathsep):
            part = part.strip()
            if part:
                roots.append(Path(part).expanduser())

    unique: list[Path] = []
    seen: set[str] = set()
    for root in roots:
        key = os.path.normcase(os.path.abspath(root))
        if key not in seen:
            unique.append(root)
            seen.add(key)
    return unique


def imazing_catalog_candidates() -> list[Path]:
    configured = os.environ.get("APPRESTORE_IMAZING_PLIST")
    if configured:
        return [Path(configured).expanduser()]

    home = Path.home()
    if platform.system() == "Darwin":
        return [home / "Library" / "Application Support" / "iMazing" / "Library" / "Apps.plist"]

    roaming = Path(os.environ.get("APPDATA") or home / "AppData" / "Roaming")
    local = Path(os.environ.get("LOCALAPPDATA") or home / "AppData" / "Local")
    return [
        roaming / "iMazing" / "Library" / "Apps.plist",
        roaming / "DigiDNA" / "iMazing" / "Library" / "Apps.plist",
        local / "DigiDNA" / "iMazing" / "Library" / "Apps.plist",
    ]


def resolve_tool(name: str) -> str | None:
    executable = f"{name}.exe" if platform.system() == "Windows" else name
    candidates = [
        project_root() / "bin" / executable,
        Path(sys.executable).resolve().parent / executable,
    ]
    local_app_data = os.environ.get("LOCALAPPDATA")
    if local_app_data:
        candidates.append(
            Path(local_app_data)
            / "Programs"
            / "AppRestore"
            / "bin"
            / executable
        )
    for candidate in candidates:
        if candidate.is_file():
            return str(candidate)
    return shutil.which(name)
