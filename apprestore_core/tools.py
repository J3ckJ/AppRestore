from __future__ import annotations

import asyncio
from importlib import metadata as package_metadata
import importlib.util
import json
import os
import platform
import re
import socket
import sys
import time
import uuid
from enum import Enum
from pathlib import Path
from typing import Any, Mapping

from .catalog import CatalogError, parse_json_output, parse_udids
from .command import CommandError, Runner
from . import __version__
from .models import Device, DeviceAppState, DoctorCheck, RedownloadRequestState
from .paths import (
    ipatool_sap_assets_dir,
    ipatool_sap_runtime_dir,
    macos_system_proxy,
    resolve_tool,
    proxy_is_reachable,
    resolve_windows_system_tool,
    windows_system_proxy,
)


class ToolUnavailable(RuntimeError):
    pass


class InstallRequestState(str, Enum):
    """How certain AppRestore is that iOS received an IPA install request."""

    COMPLETED = "completed"
    FAILED_BEFORE_REQUEST = "failed-before-request"
    INDETERMINATE = "indeterminate"


IPATOOL_VERSION = "2.5.0"
# Хеш официального windows-amd64.tar.gz (проверяется установщиком до распаковки).
# Не использовать для сверки извлечённого ipatool.exe — это разные файлы.
IPATOOL_WINDOWS_AMD64_ARCHIVE_SHA256 = (
    "d7494be51097e4ab132c5f2453a1ccafa56fffe5379a1ac0366e0997bbda6df8"
)
# ipatool >= 2.4 подписывает запросы авторизации App Store через SAP, а сам
# подписчик исполняется в эмуляторе Unicorn. Его shared library не входит в
# релиз ipatool: она скачивается и кэшируется при первом входе в Apple ID.
# Держать в синхроне с internal/sap/unicorn/artifact.go апстрима.
IPATOOL_SAP_UNICORN_VERSION = "2.1.4"
# Приватные фреймворки Apple, которые SAP-подписчик достаёт из пакета
# обновления macOS. Держать в синхроне с internal/sap/assets апстрима.
IPATOOL_SAP_ASSET_FILES = (
    ("CommerceKit", 3271840),
    ("CommerceCore", 207744),
    ("CoreFP", 29014912),
    ("CoreFP.icxs", 5288352),
)
# Имена самой Unicorn-библиотеки по платформам — по ним видно, что загрузка
# дошла до конца, а не оставила пустой каталог под хеш.
IPATOOL_SAP_RUNTIME_LIBRARIES = (
    "libunicorn.dll",
    "libunicorn.2.dylib",
    "libunicorn.so.2",
)
# Первый вход = загрузка SAP-рантайма + ручной ввод пароля и 2FA.
IPATOOL_LOGIN_TIMEOUT_SECONDS = 1800
# Обратная совместимость со старым именем константы.
IPATOOL_WINDOWS_AMD64_SHA256 = IPATOOL_WINDOWS_AMD64_ARCHIVE_SHA256

_ANSI_ESCAPE_RE = re.compile(
    r"\x1b(?:\[[0-?]*[ -/]*[@-~]|\][^\x07]*(?:\x07|\x1b\\))"
)
_ACTIVE_INSTALL_STATES = frozenset(
    {
        "download",
        "downloadpending",
        "downloadqueued",
        "downloading",
        "downloadinprogress",
        "install",
        "installpending",
        "installqueued",
        "installing",
        "installationinprogress",
        "installinprogress",
        "pendingdownload",
        "pendinginstall",
        "progress",
        "queued",
        "waiting",
        "waitingfordownload",
        "waitingforinstall",
    }
)


class AppRestoreTools:
    def __init__(
        self,
        runner: Runner | None = None,
        *,
        json_output: bool = False,
    ) -> None:
        self.runner = runner or Runner()
        self.json_output = json_output
        # После успешного login не запускаем лишний процесс `auth info`:
        # официальный ipatool хранит passphrase только в памяти одного
        # процесса и на Windows спросил бы его снова.
        self._ipatool_session_authenticated = False
        # Прокси для ipatool разрешаем один раз за процесс: проба сокета
        # не должна повторяться перед каждым вызовом.
        self._ipatool_proxy_env: dict[str, str] | None = None

    def _ipatool_env(self) -> dict[str, str]:
        """Прокси-окружение для дочернего ipatool.

        ipatool написан на Go, а Go читает только HTTP_PROXY/HTTPS_PROXY и не
        видит настроек прокси Windows. Там, где DPI растягивает TLS-хендшейк к
        серверам Apple дольше десятисекундного таймаута Go, прямое соединение
        падает с `TLS handshake timeout`, а через системный прокси проходит.
        """
        if self._ipatool_proxy_env is None:
            self._ipatool_proxy_env = self._resolve_ipatool_proxy_env()
        return self._ipatool_proxy_env

    @staticmethod
    def _resolve_ipatool_proxy_env() -> dict[str, str]:
        # Явный выбор пользователя всегда важнее наших догадок.
        if any(
            os.environ.get(name)
            for name in ("HTTPS_PROXY", "https_proxy", "HTTP_PROXY", "http_proxy")
        ):
            return {}
        system = windows_system_proxy() or macos_system_proxy()
        if not system:
            return {}
        url, bypass = system
        # Системный прокси часто указывает на локальный VPN-клиент, который
        # может быть выключен. Подставлять его вслепую нельзя: это сломает то,
        # что до сих пор работало напрямую.
        if not proxy_is_reachable(url):
            return {}
        return {"HTTP_PROXY": url, "HTTPS_PROXY": url, "NO_PROXY": bypass}

    def _tool(self, name: str) -> str:
        executable = resolve_tool(name)
        if not executable:
            raise ToolUnavailable(f"{name} is not installed")
        return executable

    def _ipatool_cmd(self, *parts: str) -> list[str]:
        # Не проксируем password, 2FA или passphrase через argv/env. В
        # интерактивных вызовах ipatool сам читает их из унаследованной
        # консоли без отображения password/passphrase.
        return [self._tool("ipatool"), *parts]

    def _pymobiledevice3_cmd(self, *parts: str) -> list[str]:
        if platform.system() in ("Windows", "Darwin"):
            try:
                module_available = (
                    importlib.util.find_spec("pymobiledevice3") is not None
                )
            except (ImportError, ValueError):
                module_available = False
            if not module_available:
                raise ToolUnavailable("pymobiledevice3 is not installed")
            # Console launchers generated by pip embed the absolute venv path.
            # The Windows and macOS installers prepare their venv in a sibling
            # staging directory and then move it into place, so invoke the
            # relocatable interpreter and module instead -- the moved launcher
            # script still points at the now-gone staging path.
            return [sys.executable, "-I", "-m", "pymobiledevice3", *parts]
        return [self._tool("pymobiledevice3"), *parts]

    def doctor(self) -> list[DoctorCheck]:
        try:
            pymobiledevice3_command = self._pymobiledevice3_cmd()
        except ToolUnavailable:
            pymobiledevice3_ok = False
            pymobiledevice3_detail = "not found"
        else:
            pymobiledevice3_ok = True
            pymobiledevice3_detail = " ".join(pymobiledevice3_command)
        ipatool = resolve_tool("ipatool")
        ipatool_ok, ipatool_detail = self._ipatool_check(ipatool)
        checks = [
            DoctorCheck(
                "Python runtime",
                (3, 10) <= sys.version_info[:2] < (3, 14),
                (
                    f"{platform.python_version()} at "
                    f"{Path(sys.executable).resolve()} (supported: 3.10-3.13)"
                ),
            ),
            DoctorCheck(
                "pymobiledevice3",
                pymobiledevice3_ok,
                pymobiledevice3_detail,
            ),
            DoctorCheck(
                "ipatool",
                ipatool_ok,
                ipatool_detail,
            ),
        ]
        checks.append(self._ipatool_sap_runtime_check())
        checks.append(self._ipatool_sap_assets_check())
        checks.append(self._ipatool_proxy_check())
        checks.append(self._runtime_provenance_check())
        if platform.system() == "Windows":
            service_ok, service_detail = self._apple_mobile_device_service()
            checks.append(
                DoctorCheck(
                    "Apple Mobile Device Service",
                    service_ok,
                    service_detail,
                )
            )
            port_ok, port_detail = self._apple_usbmux_port()
            checks.append(DoctorCheck("Apple usbmux (127.0.0.1:27015)", port_ok, port_detail))
        return checks

    @staticmethod
    def _ipatool_sap_runtime_check() -> DoctorCheck:
        """Сообщить, готов ли SAP-рантайм ipatool к входу в Apple ID.

        Начиная с ipatool 2.4 вход подписывается через SAP, и подписчику нужна
        Unicorn-библиотека, которой нет в релизе ipatool. Первый вход тянет её
        из сети и кладёт в кэш; без интернета или при закрытом прокси вход
        падает ещё до запроса пароля, поэтому состояние кэша стоит видеть
        заранее.
        """
        root = ipatool_sap_runtime_dir(IPATOOL_SAP_UNICORN_VERSION)
        # Прерванная загрузка оставляет пустой каталог под хеш библиотеки и
        # временный `.unicorn-artifact-*`, поэтому наличие каталога ничего не
        # значит — искать надо саму библиотеку.
        try:
            cached = any(
                (candidate / name).is_file()
                for candidate in root.glob("*")
                if candidate.is_dir()
                for name in IPATOOL_SAP_RUNTIME_LIBRARIES
            )
        except OSError:
            cached = False
        if cached:
            return DoctorCheck(
                "ipatool SAP runtime",
                True,
                f"cached at {root}",
                required=False,
            )
        return DoctorCheck(
            "ipatool SAP runtime",
            False,
            (
                f"not cached at {root}; the first Apple ID login downloads the "
                f"Unicorn {IPATOOL_SAP_UNICORN_VERSION} runtime and can take "
                "several minutes (needs internet access)"
            ),
            required=False,
        )

    @staticmethod
    def _ipatool_sap_assets_check() -> DoctorCheck:
        """Сообщить, лежат ли в кэше ассеты Apple для SAP-подписчика.

        Подписчику мало Unicorn: он ещё вытягивает несколько приватных
        фреймворков из пакета обновления macOS на swcdn.apple.com. Это второй
        сетевой поход первого входа, и падает он отдельно от первого.
        """
        root = ipatool_sap_assets_dir()
        # ipatool сверяет размер и SHA-256 и перекачивает всё заново, если файл
        # не сошёлся. Размер здесь ловит обрыв загрузки, не читая 37 МБ.
        missing: list[str] = []
        for name, expected_size in IPATOOL_SAP_ASSET_FILES:
            asset = root / name
            try:
                if asset.stat().st_size != expected_size:
                    missing.append(f"{name} (truncated)")
            except OSError:
                missing.append(name)
        if not missing:
            return DoctorCheck(
                "ipatool SAP assets",
                True,
                f"cached at {root}",
                required=False,
            )
        return DoctorCheck(
            "ipatool SAP assets",
            False,
            (
                f"not cached at {root} (missing: {', '.join(missing)}); the "
                "first Apple ID login fetches them from swcdn.apple.com"
            ),
            required=False,
        )

    def _ipatool_proxy_check(self) -> DoctorCheck:
        """Показать, через что ipatool пойдёт в сеть.

        ipatool на Go, а Go смотрит только на HTTP_PROXY/HTTPS_PROXY и не
        читает системные настройки прокси Windows или macOS. Там, где DPI
        мешает прямому пути к Apple, а системный прокси спасает —
        поэтому важно видеть, какой именно путь будет выбран.
        """
        inherited = next(
            (
                f"{name}={os.environ[name]}"
                for name in ("HTTPS_PROXY", "https_proxy", "HTTP_PROXY", "http_proxy")
                if os.environ.get(name)
            ),
            None,
        )
        if inherited:
            return DoctorCheck(
                "ipatool proxy",
                True,
                f"inherited from the environment ({inherited})",
                required=False,
            )
        injected = self._ipatool_env().get("HTTPS_PROXY")
        if injected:
            return DoctorCheck(
                "ipatool proxy",
                True,
                f"system proxy {injected} is up and passed to ipatool",
                required=False,
            )
        system = windows_system_proxy() or macos_system_proxy()
        if system:
            return DoctorCheck(
                "ipatool proxy",
                False,
                (
                    f"system is configured for proxy {system[0]}, but nothing "
                    "is listening there, so ipatool connects directly; start "
                    "the proxy if Apple hosts time out during TLS handshake"
                ),
                required=False,
            )
        return DoctorCheck(
            "ipatool proxy",
            True,
            "no proxy configured; ipatool connects directly",
            required=False,
        )

    @staticmethod
    def _runtime_provenance_check() -> DoctorCheck:
        runtime_path = Path(__file__).resolve().parent
        try:
            distribution = package_metadata.distribution("apprestore")
            installed_version = distribution.version
            direct_url_text = distribution.read_text("direct_url.json")
        except package_metadata.PackageNotFoundError:
            return DoctorCheck(
                "AppRestore runtime",
                False,
                f"package metadata missing; runtime={__version__} at {runtime_path}",
                required=False,
            )

        editable = False
        source = ""
        if direct_url_text:
            try:
                direct_url = json.loads(direct_url_text)
                editable = bool(
                    direct_url.get("dir_info", {}).get("editable")
                )
                source = str(direct_url.get("url") or "")
            except (AttributeError, json.JSONDecodeError):
                source = "invalid direct_url.json"

        matches = installed_version == __version__
        detail = (
            f"runtime={__version__}; metadata={installed_version}; "
            f"python={Path(sys.executable).resolve()}; package={runtime_path}"
        )
        if editable:
            detail += f"; editable source={source or 'unknown'}"
        return DoctorCheck(
            "AppRestore runtime",
            matches and not editable,
            detail,
            # A source checkout is useful for development, but a managed user
            # installation must never silently run an editable checkout.
            required=not editable,
        )

    def _ipatool_check(self, executable: str | None) -> tuple[bool, str]:
        if not executable:
            return False, "not found"
        path = Path(executable)
        try:
            result = self.runner.run([executable, "--version"], timeout=30)
        except CommandError:
            return False, f"{path} (version check timed out or failed)"
        version_text = f"{result.stdout} {result.stderr}".strip()
        if result.returncode != 0:
            return False, f"{path} (version check failed)"
        version_tokens = {
            match.group("version")
            for match in re.finditer(
                r"(?<![\w.])v?(?P<version>\d+\.\d+\.\d+)"
                r"(?![\w.+-])",
                version_text,
                flags=re.IGNORECASE,
            )
        }
        if IPATOOL_VERSION not in version_tokens:
            return False, (
                f"{path} ({version_text or 'version unknown'}; "
                f"expected {IPATOOL_VERSION})"
            )
        return True, f"{path} ({version_text})"

    def _apple_mobile_device_service(self) -> tuple[bool, str]:
        sc = resolve_windows_system_tool("sc")
        if not sc:
            return False, "trusted System32 sc.exe is unavailable"
        for service_name in ("Apple Mobile Device Service", "Apple Mobile Device"):
            try:
                result = self.runner.run(
                    [sc, "query", service_name],
                    capture=True,
                    timeout=15,
                )
            except CommandError:
                continue
            combined = f"{result.stdout}\n{result.stderr}"
            if result.returncode == 0:
                if re.search(r"(?m):\s*4\s+\S+", combined):
                    return True, f"{service_name}: running"
                return False, f"{service_name}: installed but not running"
        return (
            False,
            "not found; will try winget Apple.AppleMobileDeviceSupport via setup",
        )

    @staticmethod
    def _apple_usbmux_port() -> tuple[bool, str]:
        try:
            with socket.create_connection(("127.0.0.1", 27015), timeout=1):
                return True, "listening"
        except OSError as exc:
            return False, f"not reachable: {exc}"

    def windows_bridge_ready(self) -> bool:
        if platform.system() != "Windows":
            return True
        port_ok, _ = self._apple_usbmux_port()
        return port_ok

    def ensure_windows_bridge(self) -> list[str]:
        """Install/start Apple Mobile Device Support when missing (Windows only)."""
        notes: list[str] = []
        if platform.system() != "Windows":
            return notes
        if self.windows_bridge_ready():
            notes.append("Apple USB bridge already available")
            return notes

        sc = resolve_windows_system_tool("sc")
        if sc:
            for service_name in ("Apple Mobile Device Service", "Apple Mobile Device"):
                try:
                    queried = self.runner.run(
                        [sc, "query", service_name],
                        capture=True,
                        timeout=15,
                    )
                except CommandError:
                    continue
                if queried.returncode != 0:
                    continue
                notes.append(f"Starting {service_name}…")
                try:
                    self.runner.run(
                        [sc, "start", service_name],
                        capture=True,
                        timeout=30,
                    )
                except CommandError:
                    pass
                if self._wait_usbmux(timeout_sec=20):
                    notes.append("Apple usbmux is listening on 127.0.0.1:27015")
                    return notes

        notes.append(
            "Installing Apple Mobile Device Support via verified Microsoft winget…"
        )
        install_ok = self._winget_install_apple_bridge()
        if not install_ok:
            notes.append(
                "winget could not install Apple.AppleMobileDeviceSupport; "
                "install Apple Devices or iTunes from Microsoft Store, then reconnect the iPhone"
            )
            return notes

        if sc:
            for service_name in ("Apple Mobile Device Service", "Apple Mobile Device"):
                try:
                    self.runner.run(
                        [sc, "start", service_name],
                        capture=True,
                        timeout=30,
                    )
                except CommandError:
                    continue

        if self._wait_usbmux(timeout_sec=45):
            notes.append("Apple USB bridge is ready")
        else:
            notes.append(
                "Apple Mobile Device Support installed, but usbmux is not listening yet; "
                "reconnect the iPhone and unlock it"
            )
        return notes

    @staticmethod
    def _wait_usbmux(timeout_sec: int) -> bool:
        end = time.monotonic() + timeout_sec
        while time.monotonic() < end:
            try:
                with socket.create_connection(("127.0.0.1", 27015), timeout=1):
                    return True
            except OSError:
                time.sleep(1)
        return False

    def _winget_install_apple_bridge(self) -> bool:
        winget = resolve_windows_system_tool("winget")
        if not winget:
            return False

        success_codes = {
            0,
            (-1978335189) & 0xFFFFFFFF,
            (-1978334964) & 0xFFFFFFFF,
        }

        def succeeded(returncode: int) -> bool:
            return (returncode & 0xFFFFFFFF) in success_codes

        args = [
            winget,
            "install",
            "-e",
            "--id",
            "Apple.AppleMobileDeviceSupport",
            "--accept-package-agreements",
            "--accept-source-agreements",
            "--disable-interactivity",
        ]
        try:
            result = self.runner.run(args, capture=True, timeout=600)
        except CommandError:
            return False
        return succeeded(result.returncode) or "already installed" in (
            result.stdout + result.stderr
        ).lower()

    def list_udids(self) -> list[str]:
        command = self._pymobiledevice3_cmd(
            "usbmux",
            "list",
            "--simple",
            "--usb",
        )
        result = self.runner.run(command, check=True, timeout=60)
        return parse_udids(result.stdout)

    @staticmethod
    def _terminal_text(
        value: object,
        *,
        fallback: str,
        max_length: int,
    ) -> str:
        """Collapse untrusted device text into one terminal-safe line."""

        text = _ANSI_ESCAPE_RE.sub("", str(value or ""))
        printable = "".join(
            " " if character.isspace() else character
            for character in text
            if character.isspace() or character.isprintable()
        )
        collapsed = " ".join(printable.split())
        return collapsed[:max_length] or fallback

    def device_info(self, udid: str) -> Device:
        result = self.runner.run(
            self._pymobiledevice3_cmd(
                "lockdown",
                "info",
                "--udid",
                udid,
            ),
            check=True,
            timeout=60,
        )
        payload = parse_json_output(result.stdout)
        if not isinstance(payload, dict):
            raise CatalogError("unexpected lockdown info format")
        return Device(
            udid=udid,
            name=self._terminal_text(
                payload.get("DeviceName"),
                fallback="iPhone",
                max_length=128,
            ),
            ios_version=self._terminal_text(
                payload.get("ProductVersion"),
                fallback="?",
                max_length=64,
            ),
        )

    def list_apps(self, udid: str) -> Any:
        # Prefer a rich lookup with iTunesMetadata so we can recover adam/store IDs
        # for delisted apps (bundle lookup in ipatool would fail).
        try:
            return self._list_apps_with_metadata(udid)
        except Exception:
            result = self.runner.run(
                self._pymobiledevice3_cmd(
                    "apps",
                    "list",
                    "--type",
                    "User",
                    "--show-placeholders",
                    "--udid",
                    udid,
                ),
                check=True,
                timeout=120,
            )
            return parse_json_output(result.stdout)

    def _list_apps_with_metadata(
        self,
        udid: str,
        *,
        timeout: float = 120,
        bundle_id: str | None = None,
        include_store_metadata: bool = True,
    ) -> Any:
        async def _run() -> dict[str, Any]:
            from pymobiledevice3.lockdown import create_using_usbmux
            from pymobiledevice3.services.installation_proxy import (
                InstallationProxyService,
            )

            async with (
                await create_using_usbmux(
                    serial=udid,
                    connection_type="USB",
                ) as lockdown,
                InstallationProxyService(lockdown) as proxy,
            ):
                return_attributes = [
                    "CFBundleIdentifier",
                    "CFBundleDisplayName",
                    "CFBundleName",
                    "CFBundleShortVersionString",
                    "CFBundleVersion",
                    "StaticDiskUsage",
                    "DynamicDiskUsage",
                    "ApplicationType",
                    "IsPlaceholder",
                    "IsDemotedApp",
                    "DownloadState",
                    "InstallState",
                    "ApplicationState",
                    "PlaceholderState",
                    "IsInstalling",
                    "IsDownloading",
                ]
                if include_store_metadata:
                    return_attributes.extend(["iTunesMetadata", "ApplicationSINF"])
                options: dict[str, Any] = {
                    "ApplicationType": "User",
                    "ShowPlaceholders": True,
                    "ReturnAttributes": return_attributes,
                }
                if bundle_id is not None:
                    # InstallationProxy accepts BundleIDs as a ClientOptions
                    # filter, avoiding a full application inventory on every
                    # post-install poll.
                    options["BundleIDs"] = [bundle_id]
                result = await proxy.lookup(options)
                if not isinstance(result, dict):
                    raise CatalogError("unexpected apps list format")
                return result

        return asyncio.run(asyncio.wait_for(_run(), timeout=max(timeout, 0.1)))

    def lookup_store_id_on_device(self, udid: str, bundle_id: str) -> str | None:
        from .catalog import enrich_app_record, find_store_id

        try:
            payload = self._list_apps_with_metadata(udid, timeout=15)
        except Exception:
            return None
        if not isinstance(payload, dict):
            return None
        try:
            info = self._app_record(payload, bundle_id)
        except CatalogError:
            return None
        if not isinstance(info, Mapping):
            return None
        return find_store_id(enrich_app_record(info))

    def device_request_redownload(
        self,
        udid: str,
        bundle_id: str,
    ) -> RedownloadRequestState:
        """Ask iOS to restore an offloaded app without guessing after a timeout."""

        request_started = False

        async def _run() -> None:
            nonlocal request_started
            from pymobiledevice3.lockdown import create_using_usbmux
            from pymobiledevice3.services.installation_proxy import (
                InstallationProxyService,
            )

            async with (
                await create_using_usbmux(
                    serial=udid,
                    connection_type="USB",
                ) as lockdown,
                InstallationProxyService(lockdown) as proxy,
            ):
                # In pymobiledevice3 10.1.0 restore() waits for the terminal
                # protocol status.  From this point on, any timeout/transport
                # error is ambiguous: iOS may already be downloading.
                request_started = True
                await proxy.restore(bundle_id)

        try:
            asyncio.run(asyncio.wait_for(_run(), timeout=60))
            return RedownloadRequestState.COMPLETED
        except Exception:
            return (
                RedownloadRequestState.INDETERMINATE
                if request_started
                else RedownloadRequestState.FAILED_BEFORE_REQUEST
            )

    @staticmethod
    def _app_record(payload: object, bundle_id: str) -> Mapping[str, Any] | None:
        if not isinstance(payload, dict):
            return None
        info = payload.get(bundle_id)
        if isinstance(info, Mapping):
            embedded_bundle_id = info.get("CFBundleIdentifier")
            if embedded_bundle_id is not None and embedded_bundle_id != bundle_id:
                raise CatalogError(
                    "device app lookup returned conflicting bundle identifiers"
                )
            return info
        for value in payload.values():
            if (
                isinstance(value, Mapping)
                and value.get("CFBundleIdentifier") == bundle_id
            ):
                return value
        return None

    @staticmethod
    def _app_state_from_record(info: Mapping[str, Any]) -> DeviceAppState:
        semantic_states = {
            re.sub(r"[^a-z0-9]+", "", str(info.get(key) or "").casefold())
            for key in (
                "DownloadState",
                "InstallState",
                "ApplicationState",
                "PlaceholderState",
            )
        }
        if (
            info.get("IsInstalling") is True
            or info.get("IsDownloading") is True
            or bool(semantic_states & _ACTIVE_INSTALL_STATES)
        ):
            return DeviceAppState.DOWNLOADING

        placeholder = bool(
            info.get("IsPlaceholder") is True
            or info.get("IsDemotedApp") is True
            or "placeholder" in str(info.get("ApplicationType") or "").casefold()
        )
        if placeholder:
            return DeviceAppState.OFFLOADED
        return DeviceAppState.INSTALLED

    def device_app_snapshot(
        self,
        udid: str,
        bundle_id: str,
    ) -> tuple[DeviceAppState, str | None]:
        """Read one bundle's state and visible version with a small lookup."""

        try:
            payload = self._list_apps_with_metadata(
                udid,
                timeout=15,
                bundle_id=bundle_id,
                include_store_metadata=False,
            )
            if not isinstance(payload, dict):
                return DeviceAppState.UNKNOWN, None
            info = self._app_record(payload, bundle_id)
        except Exception:
            return DeviceAppState.UNKNOWN, None
        if info is None:
            return DeviceAppState.ABSENT, None

        version: str | None = None
        for key in ("CFBundleShortVersionString", "CFBundleVersion"):
            candidate = self._terminal_text(
                info.get(key),
                fallback="",
                max_length=128,
            )
            if candidate:
                version = candidate
                break
        return self._app_state_from_record(info), version

    def device_app_state(self, udid: str, bundle_id: str) -> DeviceAppState:
        """Return an explicit state; transport failures are never success."""

        state, _version = self.device_app_snapshot(udid, bundle_id)
        return state

    def app_still_offloaded(self, udid: str, bundle_id: str) -> bool:
        """Compatibility wrapper; absence/unknown can no longer mean success."""

        return self.device_app_state(udid, bundle_id) is not DeviceAppState.INSTALLED

    def install_ipa(self, udid: str, ipa: Path) -> InstallRequestState:
        """Install an IPA without turning an ambiguous transport loss into a retry."""

        try:
            from pymobiledevice3.lockdown import create_using_usbmux
            from pymobiledevice3.services.afc import AfcService
            from pymobiledevice3.services.installation_proxy import (
                InstallationProxyService,
            )
        except ImportError:
            try:
                pymobile_command = self._pymobiledevice3_cmd(
                    "apps",
                    "install",
                    str(ipa),
                    "--udid",
                    udid,
                )
            except ToolUnavailable:
                pass
            else:
                try:
                    result = self.runner.run(
                        pymobile_command,
                        capture=False,
                        output_to_stderr=self.json_output,
                        timeout=1800,
                    )
                except CommandError as exc:
                    # FileNotFoundError is known to happen before a child can
                    # submit the request. A timeout is ambiguous.
                    if exc.result.returncode != 127:
                        return InstallRequestState.INDETERMINATE
                else:
                    if result.returncode == 0:
                        return InstallRequestState.COMPLETED
                    return InstallRequestState.INDETERMINATE
        else:
            request_started = False

            async def install_streaming() -> None:
                nonlocal request_started
                remote_dir = "/PublicStaging/AppRestore"
                remote_path = f"{remote_dir}/{uuid.uuid4().hex}.ipa"
                async with (
                    await create_using_usbmux(
                        serial=udid,
                        connection_type="USB",
                    ) as lockdown,
                    AfcService(lockdown) as afc,
                    InstallationProxyService(lockdown) as installation_proxy,
                ):
                    await afc.makedirs(remote_dir)
                    try:
                        await afc.push(
                            str(ipa),
                            remote_path,
                            # Keep stdout reserved for the CLI's single JSON
                            # document in machine-readable mode.
                            progress_bar=not self.json_output,
                        )
                        # send_package waits for the terminal protocol status.
                        # From this line onward, a timeout or USB loss cannot
                        # prove whether iOS accepted the request.
                        request_started = True
                        await installation_proxy.send_package(
                            "Install",
                            {},
                            None,
                            remote_path,
                        )
                    finally:
                        try:
                            await afc.rm_single(remote_path, force=True)
                        except Exception:
                            pass

            try:
                asyncio.run(asyncio.wait_for(install_streaming(), timeout=1800))
                return InstallRequestState.COMPLETED
            except Exception:
                return (
                    InstallRequestState.INDETERMINATE
                    if request_started
                    else InstallRequestState.FAILED_BEFORE_REQUEST
                )

        ideviceinstaller = resolve_tool("ideviceinstaller")
        if ideviceinstaller:
            try:
                result = self.runner.run(
                    [ideviceinstaller, "-u", udid, "install", str(ipa)],
                    capture=False,
                    output_to_stderr=self.json_output,
                    timeout=1800,
                )
            except CommandError as exc:
                return (
                    InstallRequestState.FAILED_BEFORE_REQUEST
                    if exc.result.returncode == 127
                    else InstallRequestState.INDETERMINATE
                )
            return (
                InstallRequestState.COMPLETED
                if result.returncode == 0
                else InstallRequestState.INDETERMINATE
            )
        return InstallRequestState.FAILED_BEFORE_REQUEST

    def ipatool_authenticated(self) -> bool:
        if self._ipatool_session_authenticated:
            return True
        ipatool = resolve_tool("ipatool")
        if not ipatool:
            return False
        result = self.runner.run(
            self._ipatool_cmd("auth", "info"),
            capture=False,
            output_to_stderr=self.json_output,
            timeout=60,
            env=self._ipatool_env(),
        )
        if result.returncode == 0:
            self._ipatool_session_authenticated = True
            return True
        return False

    def ipatool_auth_info(self) -> dict[str, Any] | None:
        ipatool = resolve_tool("ipatool")
        if not ipatool:
            return None
        result = self.runner.run(
            self._ipatool_cmd("--format", "json", "auth", "info"),
            # stdout нужен вызывающему коду как JSON. Runner перенаправляет
            # только stdout/stderr; stdin остаётся унаследованным.
            capture=True,
            timeout=60,
            env=self._ipatool_env(),
        )
        if result.returncode != 0:
            return None
        try:
            payload = parse_json_output(result.stdout)
        except CatalogError:
            return None
        if not isinstance(payload, dict) or not payload:
            return None
        explicitly_authenticated = payload.get("authenticated") is True
        successful = payload.get("success") is True
        has_identity = any(
            isinstance(payload.get(key), str) and bool(payload[key].strip())
            for key in ("account", "appleId", "appleID", "email")
        )
        if not (explicitly_authenticated or successful or has_identity):
            return None
        if payload.get("authenticated") is False or payload.get("success") is False:
            return None
        self._ipatool_session_authenticated = True
        return payload

    def ipatool_login(self, email: str) -> None:
        result = self.runner.run(
            self._ipatool_cmd("auth", "login", "--email", email),
            capture=False,
            output_to_stderr=self.json_output,
            # ipatool >= 2.4 сначала поднимает SAP-подписчик: на первом входе он
            # качает и распаковывает Unicorn-рантайм, и только потом спрашивает
            # пароль и 2FA. Прежних 10 минут на всё это уже не хватает.
            timeout=IPATOOL_LOGIN_TIMEOUT_SECONDS,
            env=self._ipatool_env(),
        )
        if result.returncode != 0:
            self._ipatool_session_authenticated = False
            raise ToolUnavailable("ipatool authentication failed")
        self._ipatool_session_authenticated = True

    def ipatool_revoke(self) -> None:
        result = self.runner.run(
            self._ipatool_cmd("auth", "revoke"),
            capture=False,
            output_to_stderr=self.json_output,
            timeout=60,
            env=self._ipatool_env(),
        )
        self._ipatool_session_authenticated = False
        if result.returncode != 0:
            raise ToolUnavailable("ipatool revoke failed")

    def download_ipa(
        self,
        output: Path,
        *,
        bundle_id: str | None = None,
        store_id: str | None = None,
        purchase: bool = False,
    ) -> bool:
        if bool(bundle_id) == bool(store_id):
            raise ValueError("provide exactly one of bundle_id or store_id")
        args = self._ipatool_cmd("download")
        if store_id:
            if not store_id.isdigit() or int(store_id) <= 0:
                raise ValueError("store_id must be a positive integer")
            args.extend(["--app-id", store_id])
        else:
            args.extend(["--bundle-identifier", str(bundle_id)])
        if purchase:
            args.append("--purchase")
        args.extend(["--output", str(output)])
        result = self.runner.run(
            args,
            capture=False,
            output_to_stderr=self.json_output,
            timeout=1800,
            env=self._ipatool_env(),
        )
        return result.returncode == 0

    def search_apps(self, term: str, *, limit: int = 10) -> list[dict[str, str]]:
        """Search App Store via ipatool; may prompt for keychain passphrase."""
        query = term.strip()
        if not query:
            raise ValueError("search term is empty")
        if limit < 1 or limit > 50:
            raise ValueError("limit must be between 1 and 50")
        result = self.runner.run(
            self._ipatool_cmd(
                "search",
                query,
                "--limit",
                str(limit),
                "--format",
                "json",
            ),
            capture=True,
            timeout=120,
            env=self._ipatool_env(),
        )
        if result.returncode != 0:
            detail = (result.stderr or result.stdout or "").strip()
            raise ToolUnavailable(
                detail or "ipatool search failed"
            )
        try:
            payload = json.loads(result.stdout or "{}")
        except json.JSONDecodeError as exc:
            raise ToolUnavailable("ipatool search returned invalid JSON") from exc

        rows: list[Any]
        if isinstance(payload, list):
            rows = payload
        elif isinstance(payload, dict):
            for key in ("apps", "results", "data"):
                value = payload.get(key)
                if isinstance(value, list):
                    rows = value
                    break
            else:
                rows = [payload]
        else:
            rows = []

        apps: list[dict[str, str]] = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            store_id = (
                row.get("id")
                or row.get("appId")
                or row.get("trackId")
                or row.get("adamId")
            )
            bundle_id = (
                row.get("bundleId")
                or row.get("bundleIdentifier")
                or row.get("bundleID")
            )
            name = row.get("name") or row.get("trackName") or row.get("title")
            if store_id is None:
                continue
            store_text = str(store_id).strip()
            if not store_text.isdigit():
                continue
            apps.append(
                {
                    "storeId": store_text,
                    "bundleId": str(bundle_id).strip() if bundle_id else "",
                    "name": str(name).strip() if name else store_text,
                }
            )
        return apps
