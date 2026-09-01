from __future__ import annotations

import base64
import ctypes
import os
import platform
import re
import shutil
import socket
import stat
import subprocess
import sys
import xml.etree.ElementTree as ET
from pathlib import Path
from urllib.parse import urlsplit


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


def user_cache_root() -> Path:
    """Платформенный кэш-корень так, как его резолвит Go `os.UserCacheDir()`.

    ipatool >= 2.4 держит там SAP-рантайм авторизации, поэтому AppRestore
    обязан вычислять ровно тот же каталог, что и апстрим.
    """
    if platform.system() == "Windows":
        root = os.environ.get("LOCALAPPDATA")
        return Path(root) if root else Path.home() / "AppData" / "Local"
    if platform.system() == "Darwin":
        return Path.home() / "Library" / "Caches"
    xdg = os.environ.get("XDG_CACHE_HOME")
    return Path(xdg) if xdg else Path.home() / ".cache"


def ipatool_sap_runtime_dir(unicorn_version: str) -> Path:
    """Кэш Unicorn-библиотеки, которую ipatool качает при первом входе."""
    return user_cache_root() / "ipatool" / "unicorn" / unicorn_version


def ipatool_sap_assets_dir() -> Path:
    """Кэш ассетов Apple, которые SAP-подписчик тянет со swcdn.apple.com."""
    return user_cache_root() / "ipatool" / "sap" / "apple-assets-v2"


def windows_system_proxy() -> tuple[str, str] | None:
    """Прокси и bypass-список из настроек WinINET, если прокси включён.

    Go читает только HTTP_PROXY/HTTPS_PROXY и полностью игнорирует настройки
    Windows, поэтому ipatool уходит напрямую там, где браузер идёт через
    прокси. Возвращает (url, bypass) в форме, готовой для переменных окружения.
    """
    if platform.system() != "Windows":
        return None
    try:
        import winreg
    except ImportError:
        return None
    try:
        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"Software\Microsoft\Windows\CurrentVersion\Internet Settings",
        ) as key:
            enabled, _type = winreg.QueryValueEx(key, "ProxyEnable")
            if not enabled:
                return None
            server, _type = winreg.QueryValueEx(key, "ProxyServer")
            try:
                override, _type = winreg.QueryValueEx(key, "ProxyOverride")
            except OSError:
                override = ""
    except OSError:
        return None

    url = _normalize_windows_proxy(str(server))
    if not url:
        return None
    return url, _normalize_windows_proxy_bypass(str(override))


def _normalize_windows_proxy(server: str) -> str:
    """Свести значение ProxyServer к одному URL для HTTPS.

    WinINET хранит либо `host:port`, либо список вида
    `http=host:port;https=host:port`.
    """
    server = server.strip()
    if not server:
        return ""
    if "=" in server:
        entries = {}
        for part in server.split(";"):
            scheme, separator, value = part.partition("=")
            if separator and value.strip():
                entries[scheme.strip().lower()] = value.strip()
        server = entries.get("https") or entries.get("http") or ""
        if not server:
            return ""
    if "://" not in server:
        server = f"http://{server}"
    return server


def _normalize_windows_proxy_bypass(override: str) -> str:
    """Перевести ProxyOverride в формат NO_PROXY."""
    hosts = ["localhost", "127.0.0.1", "::1"]
    for entry in override.split(";"):
        entry = entry.strip()
        # `<local>` означает «все имена без точки» — в NO_PROXY аналога нет.
        if not entry or entry == "<local>":
            continue
        if entry not in hosts:
            hosts.append(entry)
    return ",".join(hosts)


def macos_system_proxy() -> tuple[str, str] | None:
    """HTTPS proxy from macOS Network preferences, if enabled and configured.

    ipatool is a Go binary and only reads HTTP_PROXY/HTTPS_PROXY -- it never
    consults the System Configuration framework macOS uses for the Network
    pane's proxy settings. Where DPI interferes with the direct path to
    Apple, a proxy configured there would otherwise never reach ipatool.
    """
    if platform.system() != "Darwin":
        return None
    scutil = shutil.which("scutil")
    if not scutil:
        return None
    try:
        result = subprocess.run(
            [scutil, "--proxy"],
            check=False,
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0:
        return None

    settings = _parse_scutil_proxy(result.stdout)
    if settings.get("HTTPSEnable") != "1":
        return None
    host = settings.get("HTTPSProxy", "").strip()
    if not host:
        return None
    port = settings.get("HTTPSPort", "").strip()
    url = f"http://{host}:{port}" if port else f"http://{host}"
    return url, _normalize_macos_proxy_bypass(settings.get("ExceptionsList"))


def _parse_scutil_proxy(output: str) -> dict[str, str]:
    """Parse the flat `key : value` dictionary `scutil --proxy` prints.

    `ExceptionsList` is the one nested `<array> { N : value ... }` scutil
    emits for this command; its entries are folded into one comma-separated
    value under the same key so callers can treat every entry uniformly.
    """
    settings: dict[str, str] = {}
    exceptions: list[str] = []
    in_exceptions = False
    for raw_line in output.splitlines():
        line = raw_line.strip()
        if in_exceptions:
            if line.startswith("}"):
                in_exceptions = False
                continue
            match = re.match(r"^\d+\s*:\s*(.+)$", line)
            if match:
                exceptions.append(match.group(1).strip())
            continue
        if line.startswith("ExceptionsList"):
            in_exceptions = True
            continue
        match = re.match(r"^(\w+)\s*:\s*(.+)$", line)
        if match:
            settings[match.group(1)] = match.group(2).strip()
    if exceptions:
        settings["ExceptionsList"] = ",".join(exceptions)
    return settings


def _normalize_macos_proxy_bypass(exceptions: str | None) -> str:
    """Translate scutil's ExceptionsList into ipatool's NO_PROXY format."""
    hosts = ["localhost", "127.0.0.1", "::1"]
    for entry in (exceptions or "").split(","):
        entry = entry.strip()
        if entry and entry not in hosts:
            hosts.append(entry)
    return ",".join(hosts)


def proxy_is_reachable(url: str, timeout: float = 1.0) -> bool:
    """Проверить, что прокси реально слушает.

    Системный прокси часто указывает на локальный VPN-клиент, который может
    быть выключен. Подставлять такой адрес в окружение ipatool вслепую нельзя:
    это сломает то, что до сих пор работало напрямую.
    """
    parsed = urlsplit(url)
    host, port = parsed.hostname, parsed.port
    if not host:
        return False
    try:
        with socket.create_connection((host, port or 80), timeout=timeout):
            return True
    except OSError:
        return False


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
