"""Thin adapter over apprestore_core for the GUI."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from apprestore_core.models import Device, DoctorCheck, MissingApp, OffloadedApp
from apprestore_core.service import AppRestoreError, AppRestoreService
from apprestore_core.tools import AppRestoreTools, ToolUnavailable

from apprestore_gui import demo


class GuiService:
    def __init__(self, *, demo_mode: bool = False) -> None:
        self.demo_mode = demo_mode
        self._service: AppRestoreService | None = None
        if not demo_mode:
            self._service = AppRestoreService()

    @property
    def core(self) -> AppRestoreService:
        if self._service is None:
            self._service = AppRestoreService()
        return self._service

    def devices(self) -> list[Device]:
        if self.demo_mode:
            return [demo.demo_device()]
        try:
            return self.core.devices()
        except Exception:
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
        if hasattr(tools, "ensure_windows_bridge"):
            try:
                tools.ensure_windows_bridge()  # type: ignore[attr-defined]
                notes.append("Проверка USB-моста выполнена")
            except Exception as exc:
                notes.append(str(exc))
        return notes or ["Перепроверка зависимостей завершена"]

    def offloaded(self, udid: str) -> list[OffloadedApp]:
        if self.demo_mode:
            return demo.demo_offloaded()
        return self.core.offloaded(udid)

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
        if self.demo_mode:
            return
        self.core.tools.ipatool_revoke()

    def restore_offloaded(
        self,
        udid: str,
        app: OffloadedApp,
        *,
        acquire_license: bool = False,
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
            udid, app, acquire_license=acquire_license
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
