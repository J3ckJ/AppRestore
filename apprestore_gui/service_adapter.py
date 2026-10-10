"""Thin adapter over apprestore_core for the GUI."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from apprestore_core.models import (
    Device,
    DoctorCheck,
    InstalledApp,
    MissingApp,
    OffloadedApp,
)
from apprestore_core.service import AppRestoreError, AppRestoreService
from apprestore_core.tools import AppRestoreTools, ToolUnavailable

from apprestore_gui import demo
from apprestore_gui.auth_pty import KeychainRunner


class GuiService:
    def __init__(self, *, demo_mode: bool = False) -> None:
        self.demo_mode = demo_mode
        self.device_error = ""
        self._keychain_passphrase = ""
        self._service: AppRestoreService | None = None
        if not demo_mode:
            self._attach_service(AppRestoreService())

    def keychain_passphrase(self) -> str:
        return self._keychain_passphrase

    def keychain_ready(self) -> bool:
        return bool(self._keychain_passphrase)

    def remember_keychain_passphrase(self, passphrase: str, *, session_open: bool = True) -> None:
        cleaned = passphrase.replace("\r", "").replace("\n", "")
        if not cleaned:
            return
        self._keychain_passphrase = cleaned
        if self._service is not None:
            self._service.tools._ipatool_session_authenticated = session_open

    def clear_keychain_passphrase(self) -> None:
        self._keychain_passphrase = ""
        if self._service is not None:
            self._service.tools._ipatool_session_authenticated = False

    def _attach_service(self, service: AppRestoreService) -> AppRestoreService:
        service.tools.runner = KeychainRunner(self.keychain_passphrase)
        service.license_mode = "gui"  # journal mode for the Widgets window
        if self._keychain_passphrase:
            service.tools._ipatool_session_authenticated = True
        self._service = service
        return service

    @property
    def core(self) -> AppRestoreService:
        if self._service is None:
            self._attach_service(AppRestoreService())
        assert self._service is not None
        return self._service

    def connected_udids(self) -> list[str]:
        """USB serials only. Cheap enough to poll while the window is open."""

        self.device_error = ""
        if self.demo_mode:
            return [demo.demo_device().udid]
        try:
            return self.core.tools.list_udids()
        except Exception as exc:
            self.device_error = str(exc).strip() or type(exc).__name__
            raise

    def devices(self) -> list[Device]:
        self.device_error = ""
        if self.demo_mode:
            return [demo.demo_device()]
        try:
            return self.core.devices()
        except Exception as exc:
            self.device_error = str(exc).strip() or type(exc).__name__
            return []

    def doctor(self) -> list[DoctorCheck]:
        if self.demo_mode:
            return demo.demo_doctor()
        try:
            return self.core.tools.doctor()
        except Exception as exc:
            return [DoctorCheck("doctor", False, str(exc))]

    def setup(self) -> list[str]:
        if self.demo_mode:
            return ["Демо: setup пропущен"]
        notes: list[str] = []
        tools = self.core.tools
        try:
            notes.extend(tools.ensure_windows_bridge())
        except Exception as exc:  # noqa: BLE001
            notes.append(f"USB-мост: {exc}")
        # Same follow-up as `apprestore setup`: show what still blocks the phone.
        try:
            udids = tools.list_udids()
            notes.append(
                f"Найдено iPhone по USB: {len(udids)}"
                if udids
                else "iPhone по USB не найден: разблокируйте его, нажмите «Доверять» и переподключите кабель"
            )
        except Exception as exc:  # noqa: BLE001
            notes.append(f"Поиск iPhone не удался: {exc}")
        return notes or ["Перепроверка зависимостей завершена"]

    def offloaded(self, udid: str) -> list[OffloadedApp]:
        if self.demo_mode:
            return demo.demo_offloaded()
        return self.core.offloaded(udid)

    def installed_apps(self, udid: str) -> list[InstalledApp]:
        if self.demo_mode:
            return demo.demo_installed()
        return self.core.installed(udid)

    def download_to_library(
        self,
        app: InstalledApp,
        *,
        acquire_license: bool = False,
    ) -> str:
        """Save the App Store IPA for an installed app. Does not install it."""

        if self.demo_mode:
            return f"{app.name}.ipa"
        if not app.store_id:
            raise AppRestoreError("у приложения нет номера в App Store")
        path = self.core.download(
            app.bundle_id,
            store_id=app.store_id,
            lookup_store_id=False,
            acquire_license=acquire_license,
        )
        return path.name

    def missing(self, udid: str) -> list[MissingApp]:
        if self.demo_mode:
            return demo.demo_missing()
        return self.core.missing(udid)

    def search(self, term: str, limit: int = 10) -> list[dict[str, str]]:
        if self.demo_mode:
            q = term.casefold()
            rows = []
            for app in demo.DEMO_MISSING + demo.DEMO_OFFLOADED:
                if q in app.name.casefold() or q in app.bundle_id.casefold():
                    rows.append(
                        {
                            "name": app.name,
                            "bundleId": app.bundle_id,
                            "storeId": app.store_id or "",
                            "source": "demo",
                        }
                    )
            return rows[:limit]
        return self.core.search_apps(term, limit=limit)

    def authenticated(self) -> bool:
        if self.demo_mode:
            return False
        try:
            return bool(self.core.tools.ipatool_authenticated())
        except Exception:
            return False

    def revoke(self) -> None:
        """Sign out: revoke and delete the cached purchase list (core.sign_out)."""

        if self.demo_mode:
            return
        self.core.sign_out()

    def note_account(self, email: str) -> None:
        """A login/switch opened ``email``: drop another account's cached data."""

        if self.demo_mode or "@" not in (email or ""):
            return
        self.core.note_account(email.strip())

    def restore_offloaded(
        self,
        udid: str,
        app: OffloadedApp,
        *,
        acquire_license: bool = False,
        try_device_redownload: bool = True,
        progress: Any = None,
    ) -> str:
        if self.demo_mode:
            if progress:
                progress("Найден источник")
                progress("Файл проверен")
                progress("Установка на iPhone")
                progress("Проверка иконки")
            return f"demo restored {app.name}"
        return self.core.restore_offloaded(
            udid,
            app,
            acquire_license=acquire_license,
            try_device_redownload=try_device_redownload,
        )

    def restore_missing(
        self,
        udid: str,
        app: MissingApp,
        *,
        acquire_license: bool = False,
        progress: Any = None,
    ) -> str:
        if self.demo_mode:
            if progress:
                progress("Найден источник")
                progress("Файл проверен")
                progress("Установка на iPhone")
            return f"demo installed {app.name}"
        return self.core.restore_missing(
            udid, app, acquire_license=acquire_license
        )

    def scan_local(self) -> list[Any]:
        if self.demo_mode:
            return demo.DEMO_LIBRARY
        entries, _errors = self.core.scan_local(refresh=True)
        return entries

    def install_ipa(self, udid: str, path: Path) -> str:
        if self.demo_mode:
            return f"demo install {path.name}"
        meta = self.core.install(udid, path)
        return f"{meta.name} {meta.version}"
