from __future__ import annotations

import base64
import ctypes
import os
import platform
import re
import shutil
import stat
import subprocess
import sys
import xml.etree.ElementTree as ET
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


def data_dir() -> Path:
    """Persistent non-cache user data (known app IDs, etc.)."""
    configured = os.environ.get("APPRESTORE_DATA_DIR")
    if configured:
        return Path(configured).expanduser()
    if platform.system() == "Windows":
        root = os.environ.get("LOCALAPPDATA")
        return (
            Path(root) / "AppRestore"
            if root
            else Path.home() / "AppData" / "Local" / "AppRestore"
        )
    return (
        Path.home()
        / "Library"
        / "Application Support"
        / "AppRestore"
    )


def known_apps_path() -> Path:
    configured = os.environ.get("APPRESTORE_KNOWN_APPS")
    if configured:
        return Path(configured).expanduser()
    return data_dir() / "known-apps.json"


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


_DESKTOP_APP_INSTALLER_PACKAGE_RE = re.compile(
    r"^Microsoft\.DesktopAppInstaller_"
    r"[A-Za-z0-9._~-]+__8wekyb3d8bbwe$"
)
_MICROSOFT_PUBLISHER = (
    "CN=Microsoft Corporation, O=Microsoft Corporation, "
    "L=Redmond, S=Washington, C=US"
)
_WINDOWS_REPARSE_POINT = 0x400


def _plain_windows_path(path: Path, *, directory: bool) -> bool:
    try:
        observed = path.lstat()
    except OSError:
        return False
    expected = stat.S_ISDIR if directory else stat.S_ISREG
    return expected(observed.st_mode) and not bool(
        int(getattr(observed, "st_file_attributes", 0))
        & _WINDOWS_REPARSE_POINT
    )


def _windows_program_files_root() -> Path | None:
    try:
        import winreg

        view = winreg.KEY_READ
        if sys.maxsize > 2**32:
            view |= winreg.KEY_WOW64_64KEY
        with winreg.OpenKey(
            winreg.HKEY_LOCAL_MACHINE,
            r"SOFTWARE\Microsoft\Windows\CurrentVersion",
            0,
            view,
        ) as key:
            value, _kind = winreg.QueryValueEx(key, "ProgramFilesDir")
    except (ImportError, OSError, TypeError):
        return None
    text = str(value or "").strip()
    return Path(text) if text else None


def _windows_directory() -> Path:
    """Return the OS directory from Kernel32, not a caller-controlled env var."""

    try:
        buffer = ctypes.create_unicode_buffer(32_768)
        length = ctypes.windll.kernel32.GetWindowsDirectoryW(  # type: ignore[attr-defined]
            buffer,
            len(buffer),
        )
        if 0 < length < len(buffer):
            return Path(buffer.value)
    except (AttributeError, OSError):
        pass
    # Non-Windows tests exercise path policy with a synthetic SystemRoot.
    return Path(os.environ.get("SystemRoot") or r"C:\Windows")


def _registered_desktop_app_installer_packages() -> list[str]:
    try:
        import winreg

        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            (
                "Software\\Classes\\Local Settings\\Software\\Microsoft\\Windows\\"
                "CurrentVersion\\AppModel\\Repository\\Packages"
            ),
        ) as key:
            names: list[str] = []
            index = 0
            while True:
                try:
                    candidate = winreg.EnumKey(key, index)
                except OSError:
                    break
                index += 1
                if _DESKTOP_APP_INSTALLER_PACKAGE_RE.fullmatch(candidate):
                    names.append(candidate)
            return names
    except (ImportError, OSError):
        return []


def _manifest_identity(path: Path) -> tuple[tuple[int, ...], str, str] | None:
    try:
        root = ET.parse(path).getroot()
        identity = next(
            element for element in root.iter() if element.tag.rsplit("}", 1)[-1] == "Identity"
        )
        name = str(identity.attrib.get("Name") or "")
        publisher = str(identity.attrib.get("Publisher") or "")
        version_text = str(identity.attrib.get("Version") or "")
        pieces = tuple(int(piece) for piece in version_text.split("."))
    except (ET.ParseError, OSError, StopIteration, TypeError, ValueError):
        return None
    if len(pieces) != 4 or any(piece < 0 for piece in pieces):
        return None
    return pieces, name, publisher


def _microsoft_authenticode_valid(candidate: Path, powershell: Path) -> bool:
    script = r"""
$candidate = [Environment]::GetEnvironmentVariable(
    'APPRESTORE_WINGET_CANDIDATE',
    'Process'
)
if ([string]::IsNullOrWhiteSpace($candidate)) { exit 2 }
Import-Module (
    Join-Path $PSHOME 'Modules\Microsoft.PowerShell.Security\Microsoft.PowerShell.Security.psd1'
) -ErrorAction Stop
$signature = Microsoft.PowerShell.Security\Get-AuthenticodeSignature `
    -LiteralPath $candidate
$expected = 'CN=Microsoft Corporation, O=Microsoft Corporation, L=Redmond, S=Washington, C=US'
if (
    $signature.Status -eq [System.Management.Automation.SignatureStatus]::Valid -and
    $null -ne $signature.SignerCertificate -and
    [string]::Equals(
        $signature.SignerCertificate.Subject,
        $expected,
        [System.StringComparison]::Ordinal
    )
) { exit 0 }
exit 1
""".strip()
    encoded = base64.b64encode(script.encode("utf-16-le")).decode("ascii")
    environment = os.environ.copy()
    environment["APPRESTORE_WINGET_CANDIDATE"] = str(candidate)
    # PowerShell 7 exports its own PSModulePath.  Passing it into Windows
    # PowerShell 5.1 can hide the built-in Security module entirely.
    environment.pop("PSModulePath", None)
    try:
        result = subprocess.run(
            [
                str(powershell),
                "-NoLogo",
                "-NoProfile",
                "-NonInteractive",
                "-EncodedCommand",
                encoded,
            ],
            check=False,
            capture_output=True,
            timeout=20,
            env=environment,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return result.returncode == 0


def _resolve_trusted_winget(windows: Path) -> str | None:
    program_files = _windows_program_files_root()
    if program_files is None:
        return None
    windows_apps = program_files / "WindowsApps"
    if not _plain_windows_path(windows_apps, directory=True):
        return None
    powershell = (
        windows
        / "System32"
        / "WindowsPowerShell"
        / "v1.0"
        / "powershell.exe"
    )
    if not _plain_windows_path(powershell, directory=False):
        return None

    trusted: list[tuple[tuple[int, ...], Path]] = []
    for package_name in _registered_desktop_app_installer_packages():
        package = Path(os.path.abspath(windows_apps / package_name))
        if os.path.normcase(str(package.parent)) != os.path.normcase(
            str(Path(os.path.abspath(windows_apps)))
        ):
            continue
        manifest = package / "AppxManifest.xml"
        candidate = package / "winget.exe"
        if not (
            _plain_windows_path(package, directory=True)
            and _plain_windows_path(manifest, directory=False)
            and _plain_windows_path(candidate, directory=False)
        ):
            continue
        identity = _manifest_identity(manifest)
        if identity is None:
            continue
        version, name, publisher = identity
        if name != "Microsoft.DesktopAppInstaller" or publisher != _MICROSOFT_PUBLISHER:
            continue
        resolved = Path(os.path.abspath(candidate))
        if _microsoft_authenticode_valid(resolved, powershell):
            trusted.append((version, resolved))
    if not trusted:
        return None
    return str(max(trusted, key=lambda item: item[0])[1])


def resolve_windows_system_tool(name: str) -> str | None:
    """Resolve a Windows-owned executable without consulting user-controlled PATH.

    This resolver is intentionally narrow.  It is for commands that may install
    system components or otherwise cross a trust boundary; normal AppRestore
    dependencies continue to use :func:`resolve_tool`.
    """

    if platform.system() != "Windows":
        return None

    normalized = name.casefold().removesuffix(".exe")
    windows = _windows_directory()
    if normalized == "winget":
        return _resolve_trusted_winget(windows)
    candidates: dict[str, list[Path]] = {
        "powershell": [
            windows
            / "System32"
            / "WindowsPowerShell"
            / "v1.0"
            / "powershell.exe"
        ],
        "sc": [windows / "System32" / "sc.exe"],
    }
    for candidate in candidates.get(normalized, []):
        try:
            resolved = candidate.resolve(strict=True)
        except OSError:
            continue
        if resolved.is_file():
            return str(resolved)
    return None
