"""Live phone data for the Qt Quick window.

The home list is the offloaded apps on the connected device. Restoring one
asks iOS to download it, and falls back to an IPA install when iOS refuses.
"""

from __future__ import annotations

import os
import re
import tempfile
import threading
import time
from pathlib import Path

from PySide6.QtCore import Property, QObject, Signal, Slot

from apprestore_core.models import Device, InstalledApp, IpaMetadata, OffloadedApp
from apprestore_gui.account_bindings import account_email, load_bindings, remember_binding
from apprestore_gui.account_vault import (
    active_email,
    forget_session,
    has_session,
    next_account_step,
    restore_session,
    save_session,
)
from apprestore_gui.auth_pty import AppleLogin, AuthResult, keychain_has_saved_account, probe_keychain
from apprestore_gui.device_form import device_form, device_noun
from apprestore_gui.errors import NOT_OWNED_TEXT, explain_user_error, is_license_missing
from apprestore_core.license_gate import LicenseDenied, run_with_free_license
from apprestore_gui.popular_apps import POPULAR_APPS
from apprestore_gui.service_adapter import GuiService
from apprestore_gui.shelf_probe import probe_store
from apprestore_core.ipatool_api import ErrorCode, IpatoolClient, IpatoolError
from apprestore_core.paths import resolve_tool
from apprestore_core.purchases_cache import PurchasesCache
from apprestore_gui.purchases import (
    SESSION_UNKNOWN,
    PurchasesLoader,
    PurchasesView,
    SessionChecker,
    SessionView,
    gui_runner,
)

_PROGRESS = re.compile(r"downloading\s+(\d+)\s*%", re.IGNORECASE)

_TILES: tuple[tuple[str, str], ...] = (
    ("#21A038", "#FFFFFF"),
    ("#FFDD2D", "#1C1C1E"),
    ("#0A2896", "#FFFFFF"),
    ("#EF3124", "#FFFFFF"),
    ("#0077FF", "#FFFFFF"),
    ("#5E5CE6", "#FFFFFF"),
    ("#FF9F0A", "#1C1C1E"),
    ("#1C1C1E", "#FFFFFF"),
    ("#34C759", "#1C1C1E"),
    ("#FF2D55", "#FFFFFF"),
)


def offloaded_card(app: OffloadedApp) -> dict[str, str]:
    """One home-screen tile. ``storeId`` is the bundle id the window uses as a key."""

    name = app.name.strip() or app.bundle_id
    version = app.version.strip()
    color, ink = _TILES[sum(ord(char) for char in app.bundle_id) % len(_TILES)]
    return {
        "storeId": app.bundle_id,
        "name": name,
        "detail": version if version and version != "?" else "сгружено",
        "mark": name[:1].upper(),
        "color": color,
        "ink": ink,
    }


def _tile(key: str) -> tuple[str, str]:
    return _TILES[sum(ord(char) for char in key) % len(_TILES)]


def phone_card(app: InstalledApp) -> dict[str, object]:
    """One installed app the Files sheet can copy into the IPA library."""

    name = app.name.strip() or app.bundle_id
    version = app.version.strip()
    color, ink = _tile(app.bundle_id)
    return {
        "bundleId": app.bundle_id,
        "storeId": app.store_id or "",
        "name": name,
        "detail": version if version and version != "?" else "на устройстве",
        "mark": name[:1].upper(),
        "color": color,
        "ink": ink,
    }


def library_card(entry: IpaMetadata) -> dict[str, object]:
    """One IPA already stored on the computer."""

    name = entry.name.strip() or entry.bundle_id or entry.path.stem
    version = entry.version.strip()
    key = entry.bundle_id or entry.path.name
    color, ink = _tile(key)
    return {
        "path": str(entry.path),
        "bundleId": entry.bundle_id,
        "storeId": "",
        "name": name,
        "detail": version if version and version != "?" else entry.path.name,
        "mark": name[:1].upper(),
        "color": color,
        "ink": ink,
        "placed": False,
    }


class QuickSession(QObject):
    """USB poll and restore calls. The window reads the properties after ``changed``."""

    changed = Signal()
    appRestored = Signal(str)
    restoreSettled = Signal(str)
    installSettled = Signal(str, bool, str)
    installProgress = Signal(int, str)
    deviceSwitched = Signal()
    filesChanged = Signal()
    filesFlagChanged = Signal()
    filesNoteChanged = Signal(str, bool)
    phoneLoadingChanged = Signal(bool)
    copySettled = Signal(str, bool, str)
    keychainPrompt = Signal(str)
    loginPrompt = Signal(str)
    followAccount = Signal(str)
    sessionChanged = Signal()
    purchasesChanged = Signal()

    def __init__(self) -> None:
        super().__init__()
        self.service = GuiService(demo_mode=False)
        self._lock = threading.Lock()
        self._tool_lock = threading.Lock()
        self._probe_started = False
        self._output_tail = ""
        self._thread_started = False
        self._busy = False
        self._force = False
        self._ready = False
        self._udid = ""
        self._rows: list[dict[str, str]] = []
        self._by_key: dict[str, OffloadedApp] = {}
        self._loading = True
        self._connected = False
        self._form = "iphone-island"
        self._noun = "iPhone"
        self._signed = False
        self._note = ""
        self._udid_order: list[str] = []
        self._device_cache: dict[str, Device] = {}
        self._loaded_udid = ""
        self._seen_udids: tuple[str, ...] = ()
        self._bindings = load_bindings()
        self._phase = "checking"
        self._account_email = ""
        self._auth_status = "Смотрим сессию…"
        self._pending_email = ""
        self._auth_job: AppleLogin | None = None
        self._passphrases: dict[str, str] = {}
        self._kept: dict[str, str] = {}
        self._switch_asked: tuple[str, str] | None = None
        self._keychain_prompted = False
        self._unlock_attempt = False
        self._after_unlock = ""
        self._return_account = ""
        self.followAccount.connect(self._begin_switch)
        self._phone_rows: list[dict[str, object]] = []
        self._library_rows: list[dict[str, object]] = []
        self._phone_by_bundle: dict[str, InstalledApp] = {}
        self._phone_udid = ""
        self._files_note = ""
        self._files_busy = False
        self._phone_loading = False
        self._files_mode = False
        self._session_view: SessionView = SESSION_UNKNOWN
        self._purchases_view = PurchasesView()
        self._session_checker = SessionChecker(
            self._ipatool_client, self._on_session_view, fallback_factory=self._ipatool_client_pty
        )
        self._purchases = PurchasesLoader(
            PurchasesCache(),
            self._ipatool_client,
            self._on_purchases_view,
            fallback_factory=self._ipatool_client_pty,
        )

    # -- session check and purchase list (logic in apprestore_gui.purchases) --

    def _ipatool_client_pty(self) -> IpatoolClient:
        return self._ipatool_client(force_pty=True)

    def _ipatool_client(self, force_pty: bool = False) -> IpatoolClient:
        binary = resolve_tool("ipatool")
        if not binary:
            raise IpatoolError(ErrorCode.BINARY_MISSING, "ipatool binary not found")
        tools = self.service.core.tools
        # keychain_passphrase="" keeps the passphrase out of argv and env;
        # gui_runner sends it via --keychain-passphrase-stdin (patched ipatool)
        # or answers the prompt on the hidden terminal (old ipatool).
        return IpatoolClient(
            binary,
            keychain_passphrase="",
            runner=gui_runner(self.service.keychain_passphrase, tools._ipatool_env, force_pty=force_pty),
        )

    def _on_session_view(self, view: SessionView) -> None:
        with self._lock:
            before = self._session_view
            self._session_view = view
            email = self._account_email
        self.sessionChanged.emit()
        if view.relogin and not before.relogin:
            self.loginPrompt.emit(email)

    def _on_purchases_view(self, view: PurchasesView) -> None:
        with self._lock:
            self._purchases_view = view
        self.purchasesChanged.emit()
        if view.session_problem:
            self._on_session_view(SessionView("expired", view.note, relogin=True))

    def _follow_purchases_account(self, email: str) -> None:
        self._purchases.set_account(email)

    @Property(str, notify=sessionChanged)
    def sessionState(self) -> str:
        with self._lock:
            return self._session_view.state

    @Property(str, notify=sessionChanged)
    def sessionNote(self) -> str:
        with self._lock:
            return self._session_view.note

    @Property(bool, notify=sessionChanged)
    def sessionRelogin(self) -> bool:
        with self._lock:
            return self._session_view.relogin

    @Property("QVariantList", notify=purchasesChanged)
    def purchases(self) -> list[dict[str, object]]:
        with self._lock:
            return [dict(row) for row in self._purchases_view.rows]

    @Property(bool, notify=purchasesChanged)
    def purchasesBusy(self) -> bool:
        with self._lock:
            return self._purchases_view.busy

    @Property(str, notify=purchasesChanged)
    def purchasesProgress(self) -> str:
        with self._lock:
            return self._purchases_view.progress

    @Property(str, notify=purchasesChanged)
    def purchasesNote(self) -> str:
        with self._lock:
            return self._purchases_view.note

    @Property(bool, notify=purchasesChanged)
    def purchasesFromCache(self) -> bool:
        with self._lock:
            return self._purchases_view.from_cache

    @Slot()
    def checkSession(self) -> None:
        with self._lock:
            signed = self._signed
        if signed:
            self._session_checker.start()

    @Slot()
    def loadPurchases(self) -> None:
        """Cached list at once, then a refresh page by page."""

        self._purchases.start()

    @Slot()
    def cancelPurchases(self) -> None:
        self._purchases.cancel()

    def _noun_now(self) -> str:
        with self._lock:
            return self._noun or "iPhone"

    def _snapshot(self) -> dict[str, object]:
        with self._lock:
            return {
                "apps": list(self._rows),
                "loading": self._loading,
                "connected": self._connected,
                "deviceForm": self._form,
                "deviceNoun": self._noun,
                "signedIn": self._signed,
                "note": self._note,
                "devices": self._device_rows_unlocked(),
                "deviceName": self._selected_name_unlocked(),
                "boundEmail": self._bindings.get(self._udid, ""),
                "accountEmail": self._account_email,
                "authPhase": self._phase,
                "authStatus": self._auth_status,
                "accounts": self._account_rows_unlocked(),
            }

    def _device_rows_unlocked(self) -> list[dict[str, object]]:
        rows: list[dict[str, object]] = []
        for udid in self._udid_order:
            info = self._device_cache.get(udid)
            product = info.product_type if info is not None else ""
            kind = info.device_class if info is not None else ""
            form = device_form(product, kind)
            name = (info.name if info is not None else "").strip() or device_noun(form)
            rows.append(
                {
                    "udid": udid,
                    "name": name,
                    "ios": info.ios_version if info is not None else "",
                    "form": form,
                    "noun": device_noun(form),
                    "account": self._bindings.get(udid, ""),
                    "active": udid == self._udid,
                }
            )
        return rows

    def _selected_name_unlocked(self) -> str:
        info = self._device_cache.get(self._udid)
        if info is None or not info.name.strip():
            return ""
        return info.name.strip()

    def _label_unlocked(self, udid: str) -> str:
        info = self._device_cache.get(udid)
        if info is None or not info.name.strip():
            return ""
        return info.name.strip()

    def _account_rows_unlocked(self) -> list[dict[str, object]]:
        grouped: dict[str, list[str]] = {}
        for udid, email in self._bindings.items():
            label = self._label_unlocked(udid)
            names = grouped.setdefault(email, [])
            if label and label not in names:
                names.append(label)
        current = self._account_email.strip()
        if current and not any(email.lower() == current.lower() for email in grouped):
            grouped[current] = []
        rows: list[dict[str, object]] = []
        for email, labels in grouped.items():
            rows.append(
                {
                    "email": email,
                    "devices": " · ".join(labels),
                    "active": bool(current) and email.lower() == current.lower(),
                    "saved": has_session(email),
                }
            )
        rows.sort(key=lambda row: (not bool(row["active"]), str(row["email"]).lower()))
        return rows

    def offloaded_snapshot(self) -> list[OffloadedApp]:
        """Offloaded apps of the selected device, in list order (4b window)."""

        with self._lock:
            rows = list(self._rows)
            by_key = dict(self._by_key)
        return [by_key[str(row["storeId"])] for row in rows if str(row.get("storeId")) in by_key]

    def current_udid(self) -> str:
        with self._lock:
            return self._udid

    @Property("QVariantList", notify=changed)
    def apps(self) -> list[dict[str, str]]:
        return list(self._snapshot()["apps"])  # type: ignore[arg-type]

    @Property(bool, notify=changed)
    def loading(self) -> bool:
        return bool(self._snapshot()["loading"])

    @Property(bool, notify=changed)
    def connected(self) -> bool:
        return bool(self._snapshot()["connected"])

    @Property(str, notify=changed)
    def deviceForm(self) -> str:
        return str(self._snapshot()["deviceForm"])

    @Property(str, notify=changed)
    def deviceNoun(self) -> str:
        return str(self._snapshot()["deviceNoun"])

    @Property(bool, notify=changed)
    def signedIn(self) -> bool:
        return bool(self._snapshot()["signedIn"])

    @Property(str, notify=changed)
    def note(self) -> str:
        return str(self._snapshot()["note"])

    @Property("QVariantList", notify=changed)
    def devices(self) -> list[dict[str, object]]:
        return list(self._snapshot()["devices"])  # type: ignore[arg-type]

    @Property(str, notify=changed)
    def deviceName(self) -> str:
        return str(self._snapshot()["deviceName"])

    @Property(str, notify=changed)
    def boundEmail(self) -> str:
        return str(self._snapshot()["boundEmail"])

    @Property(str, notify=changed)
    def accountEmail(self) -> str:
        return str(self._snapshot()["accountEmail"])

    @Property(str, notify=changed)
    def authPhase(self) -> str:
        return str(self._snapshot()["authPhase"])

    @Property(str, notify=changed)
    def authStatus(self) -> str:
        return str(self._snapshot()["authStatus"])

    @Property("QVariantList", notify=changed)
    def accounts(self) -> list[dict[str, object]]:
        return list(self._snapshot()["accounts"])  # type: ignore[arg-type]

    @Property("QVariantList", notify=filesChanged)
    def phoneApps(self) -> list[dict[str, object]]:
        with self._lock:
            return list(self._phone_rows)

    @Property("QVariantList", notify=filesChanged)
    def libraryFiles(self) -> list[dict[str, object]]:
        with self._lock:
            return list(self._library_rows)

    @Property(str, notify=filesFlagChanged)
    def filesNote(self) -> str:
        with self._lock:
            return self._files_note

    @Property(bool, notify=filesFlagChanged)
    def filesBusy(self) -> bool:
        with self._lock:
            return self._files_busy

    @Slot()
    def refresh(self) -> None:
        if self._thread_started:
            with self._lock:
                self._force = True
            return
        self._thread_started = True
        threading.Thread(target=self._loop, name="apprestore-quick", daemon=True).start()
        self._maybe_probe_shelf()

    @Slot("QVariantList")
    def restore(self, keys: list[object]) -> None:
        chosen_keys = [str(key) for key in list(keys or []) if str(key)]
        with self._lock:
            if self._busy or not chosen_keys:
                return
            self._busy = True
            udid = self._udid
            chosen = [(key, self._by_key.get(key)) for key in chosen_keys]
        threading.Thread(
            target=self._restore,
            args=(udid, chosen),
            name="apprestore-restore",
            daemon=True,
        ).start()

    @Slot(str)
    def installStore(self, store_id: str) -> None:
        # «Поставить» may add a free app to the Apple ID on purpose, but only
        # through license_gate: price==0 by lookup, 5/day + 15 total, journal.
        self._start_install(store_id, acquire=True)

    @Slot(str)
    def acquireStore(self, store_id: str) -> None:
        self._start_install(store_id, acquire=True)

    @Slot()
    def loadLibrary(self) -> None:
        threading.Thread(target=self._load_library, name="apprestore-library", daemon=True).start()

    @Slot()
    def loadPhone(self) -> None:
        with self._lock:
            if self._phone_loading or self._busy or self._files_busy:
                return
            self._phone_loading = True
            udid = self._udid
            noun = self._noun or "iPhone"
            looking = f"Смотрим приложения на {noun}…"
            self._files_note = looking
            busy = self._files_busy
        self.phoneLoadingChanged.emit(True)
        self.filesNoteChanged.emit(looking, busy)
        threading.Thread(
            target=self._load_phone,
            args=(udid,),
            name="apprestore-phone-apps",
            daemon=True,
        ).start()

    @Slot("QVariantList")
    def saveCopies(self, bundle_ids: list[object]) -> None:
        ids = [str(item) for item in list(bundle_ids or []) if str(item)]
        with self._lock:
            if not ids:
                return
            if self._busy or self._files_busy:
                self._files_note = "Сначала дождитесь текущей операции."
                busy = True
                chosen: list[InstalledApp] = []
            else:
                busy = False
                self._busy = True
                self._files_busy = True
                self._files_note = "Сохраняем копию…"
                chosen = [self._phone_by_bundle[item] for item in ids if item in self._phone_by_bundle]
        self._emit_files_note()
        if busy:
            return
        if not chosen:
            self._finish_files("Эти приложения уже не в списке.")
            return
        threading.Thread(
            target=self._save_copies,
            args=(chosen,),
            name="apprestore-save",
            daemon=True,
        ).start()

    @Slot(str)
    def installSaved(self, path: str) -> None:
        path = path.strip()
        with self._lock:
            if not path:
                return
            if self._busy or self._files_busy:
                self._files_note = "Сначала дождитесь текущей операции."
                rejected = True
                udid = ""
            else:
                rejected = False
                self._busy = True
                self._files_busy = True
                noun = self._noun or "iPhone"
                self._files_note = f"Ставим копию на {noun}…"
                udid = self._udid
        self._emit_files_note()
        if rejected:
            return
        threading.Thread(
            target=self._install_saved,
            args=(udid, path),
            name="apprestore-install-ipa",
            daemon=True,
        ).start()

    def _start_install(self, store_id: str, *, acquire: bool) -> None:
        store_id = store_id.strip()
        with self._lock:
            if self._busy or not store_id:
                return
            self._busy = True
            udid = self._udid
        threading.Thread(
            target=self._install_store,
            args=(udid, store_id, acquire),
            name="apprestore-install",
            daemon=True,
        ).start()

    @Slot(str)
    def selectDevice(self, udid: str) -> None:
        udid = udid.strip()
        with self._lock:
            if not udid or udid not in self._udid_order or udid == self._udid or self._busy:
                return
            self._udid = udid
            self._ready = False
            self._force = True
            self._rows = []
            self._loading = True
            self._by_key = {}
            self._phone_rows = []
            self._phone_by_bundle = {}
            self._phone_udid = ""
            for row in self._library_rows:
                row["placed"] = False
            info = self._device_cache.get(udid)
            if info is not None:
                self._form = device_form(info.product_type, info.device_class)
                self._noun = device_noun(self._form)
        self.deviceSwitched.emit()
        self.changed.emit()
        self.filesChanged.emit()
        self._consider_account()

    @Slot(str, str)
    def login(self, email: str, password: str) -> None:
        email = email.strip()
        if "@" not in email:
            self._set_auth("out", "Укажите почту Apple ID.")
            return
        if not password:
            self._set_auth("out", "Введите пароль. Программа его не сохраняет.")
            return
        if self._auth_job is not None:
            return
        with self._lock:
            current = self._account_email.strip() if self._signed else ""
        self._return_account = ""
        if current and current.lower() != email.lower() and save_session(current):
            self._return_account = current
        self._pending_email = email
        self._set_auth("running", "Входим…")
        self._start_job(AppleLogin(email, password, "", self.service.keychain_passphrase()))

    @Slot(str)
    def submitCode(self, code: str) -> None:
        code = code.strip()
        if not code:
            self._set_auth("need_code", "Введите код из сообщения Apple.")
            return
        job = self._auth_job
        if job is None:
            self._set_auth("out", "Вход уже завершился. Нажмите «Войти» ещё раз.")
            return
        job.submit("code", code)
        self._set_auth("running", "Код отправлен. Ждём ответ Apple…")

    @Slot()
    def cancelLogin(self) -> None:
        """«Отмена» while signing in: stop ipatool (AppleLogin.cancel); nothing is retried."""

        job = self._auth_job
        if job is not None:
            job.cancel()
            self._set_auth("running", "Отменяем вход…")

    @Slot(str)
    def unlock(self, passphrase: str) -> None:
        passphrase = passphrase.strip()
        if not passphrase:
            self._set_auth(self._phase or "locked", "Введите пароль связки ключей. Это не пароль Apple ID.")
            return
        self._unlock_attempt = True
        self.service.remember_keychain_passphrase(passphrase, session_open=False)
        job = self._auth_job
        if job is not None:
            job.submit("passphrase", passphrase)
            self._set_auth("running", "Пароль связки отправлен.")
            return
        self._set_auth("running", "Открываем сохранённую сессию…")
        self._start_job(AppleLogin("", "", "", passphrase))

    @Slot()
    def keepAccount(self) -> None:
        with self._lock:
            udid = self._udid
            wanted = self._bindings.get(udid, "").strip()
            if udid and wanted:
                self._kept[udid] = wanted.lower()
                self._switch_asked = (udid, wanted.lower())

    @Slot(str)
    def useAccount(self, email: str) -> None:
        email = email.strip()
        if "@" not in email:
            return
        with self._lock:
            self._kept.pop(self._udid, None)
            self._switch_asked = None
            current = self._account_email.strip()
            signed = self._signed
        if signed and current.lower() == email.lower():
            return
        if not signed and keychain_has_saved_account():
            self._after_unlock = email
            self.keychainPrompt.emit("")
            return
        if has_session(email):
            self.followAccount.emit(email)
            return
        self.loginPrompt.emit(email)

    @Slot()
    def signOut(self) -> None:
        if self._auth_job is not None:
            self._auth_job.cancel()
            self._auth_job = None
        try:
            self.service.revoke()
        except Exception:
            pass
        self.service.clear_keychain_passphrase()
        with self._lock:
            email = self._account_email
            self._signed = False
            self._phase = "out"
            self._account_email = ""
            self._auth_status = "Сессия Apple ID завершена."
            self._after_unlock = ""
            if email:
                self._passphrases.pop(email.lower(), None)
        if email:
            forget_session(email)
        self._purchases.forget()
        self._on_session_view(SESSION_UNKNOWN)
        self.changed.emit()

    def _set_auth(self, phase: str, status: str) -> None:
        with self._lock:
            self._phase = phase
            self._auth_status = status
        self.changed.emit()

    def _start_job(self, job: AppleLogin) -> None:
        job.need_input.connect(self._on_auth_need)
        job.status.connect(self._on_auth_status)
        job.done.connect(self._on_auth_done)
        self._auth_job = job
        job.start()

    def _on_auth_need(self, kind: str) -> None:
        if kind == "code":
            self._set_auth(
                "need_code",
                "Apple просит код из сообщения. Введите его и нажмите «Отправить код».",
            )
            return
        if kind != "passphrase":
            return
        secret = self.service.keychain_passphrase()
        job = self._auth_job
        if secret and job is not None:
            job.submit("passphrase", secret)
            self._set_auth("running", "Пароль связки отправлен.")
            return
        with self._lock:
            email = self._pending_email or self._account_email
        self._set_auth("need_passphrase", "Нужен пароль связки ключей. Это не пароль Apple ID.")
        self.keychainPrompt.emit(email)

    def _on_auth_status(self, text: str) -> None:
        with self._lock:
            if self._phase in ("need_code", "need_passphrase"):
                return
            self._auth_status = text
        self.changed.emit()

    def _on_auth_done(self, result: object) -> None:
        auth = result if isinstance(result, AuthResult) else AuthResult(False, "Вход завершился без ответа.")
        self._auth_job = None
        unlock_attempt = self._unlock_attempt
        self._unlock_attempt = False
        passphrase = self.service.keychain_passphrase()
        if auth.ok and passphrase:
            self.service.remember_keychain_passphrase(passphrase, session_open=auth.session_open)
        elif unlock_attempt:
            self.service.clear_keychain_passphrase()
            passphrase = ""
        email = ""
        udid = ""
        with self._lock:
            if auth.ok and auth.session_open:
                self._signed = True
                self._phase = "in"
                if self._pending_email:
                    self._account_email = self._pending_email
                self._auth_status = auth.message or "Сессия открыта."
                email = self._account_email
                udid = self._udid
                if email and passphrase:
                    self._passphrases[email.lower()] = passphrase
            elif auth.ok:
                self._signed = False
                self._phase = "out"
                self._auth_status = auth.message
            else:
                self._auth_status = auth.message or "Не удалось войти."
                if self._phase == "running":
                    self._phase = "locked" if keychain_has_saved_account() else "out"
                if unlock_attempt and self._pending_email:
                    self._passphrases.pop(self._pending_email.lower(), None)
        if email and udid:
            remember_binding(udid, email)
            with self._lock:
                self._bindings[udid] = email
        self.changed.emit()
        if not auth.ok and unlock_attempt:
            with self._lock:
                pending = self._pending_email
            self.keychainPrompt.emit(pending)
            return
        if auth.ok:
            self._return_account = ""
        elif self._return_account:
            previous = self._return_account
            self._return_account = ""
            restore_session(previous)
            secret = self._passphrases.get(previous.lower(), "")
            if secret:
                self.service.remember_keychain_passphrase(secret, session_open=True)
            with self._lock:
                self._account_email = previous
                self._pending_email = previous
                if secret:
                    self._signed = True
            self.changed.emit()
        if auth.ok and auth.session_open:
            threading.Thread(target=self._refresh_auth, name="apprestore-auth", daemon=True).start()

    def _refresh_auth(self) -> None:
        state = probe_keychain()
        email = ""
        if state == "in":
            try:
                email = account_email(self.service.core.tools.ipatool_auth_info())
            except Exception:
                email = ""
        prompt = False
        follow = ""
        with self._lock:
            if self._phase in ("running", "need_code", "need_passphrase"):
                return
            if self._signed and state != "in":
                return
            self._signed = state == "in"
            if email:
                self._account_email = email
            self._phase = "in" if state == "in" else ("locked" if state == "locked" else "out")
            if self._auth_status.startswith("Смотрим"):
                if state == "locked":
                    self._auth_status = (
                        "Сессия сохранена. Откройте её паролем связки — это не пароль Apple ID."
                    )
                elif state == "in":
                    self._auth_status = "Сессия открыта."
                else:
                    self._auth_status = ""
            if state == "locked" and not self.service.keychain_ready() and not self._keychain_prompted:
                self._keychain_prompted = True
                prompt = True
            udid = self._udid
            saved = self._account_email
            secret = self.service.keychain_passphrase()
            if state == "in" and saved and secret:
                self._passphrases[saved.lower()] = secret
            if state == "in":
                follow = self._after_unlock
                self._after_unlock = ""
        if state == "in" and saved:
            save_session(saved)
            self._follow_purchases_account(saved)
            self._session_checker.start()
        elif state == "out":
            # No saved session at all: nothing may stay cached.
            self._follow_purchases_account("")
        if state == "in" and saved and udid:
            remember_binding(udid, saved)
            with self._lock:
                self._bindings[udid] = saved
        self.changed.emit()
        if prompt:
            self.keychainPrompt.emit("")
        if state == "in" and follow and follow.lower() != saved.lower():
            self.followAccount.emit(follow)
        elif state in ("in", "locked", "out"):
            self._consider_account()

    def _consider_account(self) -> None:
        with self._lock:
            if self._busy or self._auth_job is not None:
                return
            udid = self._udid
            wanted = self._bindings.get(udid, "").strip()
            current = self._account_email.strip()
            kept = bool(wanted) and self._kept.get(udid, "") == wanted.lower()
            signed = self._signed
            phase = self._phase
        if not udid or not wanted:
            return
        if signed:
            step = next_account_step(
                current,
                wanted,
                saved=has_session(wanted),
                kept=kept,
            )
        elif kept or phase not in ("locked", "out"):
            return
        elif phase == "locked" and active_email().lower() in ("", wanted.lower()):
            return
        elif has_session(wanted):
            step = "switch"
        elif phase == "out":
            step = "login"
        else:
            return
        if step == "stay":
            return
        key = (udid, wanted.lower())
        with self._lock:
            if self._switch_asked == key:
                return
            self._switch_asked = key
        if step == "switch":
            self.followAccount.emit(wanted)
        else:
            self.loginPrompt.emit(wanted)

    @Slot(str)
    def _begin_switch(self, email: str) -> None:
        email = email.strip()
        if "@" not in email:
            return
        with self._lock:
            if self._auth_job is not None:
                return
            current = self._account_email.strip()
            signed = self._signed
        if signed and current.lower() == email.lower():
            return
        known = current or active_email()
        if known.lower() == email.lower() and keychain_has_saved_account():
            self._set_auth(
                "locked",
                f"Нужен пароль связки для {email}. Это не пароль Apple ID.",
            )
            self.keychainPrompt.emit(email)
            return
        if keychain_has_saved_account() and not known:
            self._after_unlock = email
            self.keychainPrompt.emit("")
            return
        if known and known.lower() != email.lower():
            save_session(known)
        if not restore_session(email):
            self.loginPrompt.emit(email)
            return
        self._follow_purchases_account(email)
        self._on_session_view(SESSION_UNKNOWN)
        secret = self._passphrases.get(email.lower(), "")
        with self._lock:
            self._pending_email = email
            self._unlock_attempt = bool(secret)
            self._signed = False
            self._account_email = email
            self._phase = "running" if secret else "locked"
            self._auth_status = (
                f"Открываем {email}…"
                if secret
                else f"Нужен пароль связки для {email}. Это не пароль Apple ID."
            )
        self.changed.emit()
        if secret:
            self.service.remember_keychain_passphrase(secret, session_open=False)
            self._start_job(AppleLogin("", "", "", secret))
            return
        self.service.clear_keychain_passphrase()
        self.keychainPrompt.emit(email)

    def _ensure_keychain(self) -> bool:
        if self.service.keychain_ready() or probe_keychain() == "in":
            return True
        with self._lock:
            email = self._account_email
        self.keychainPrompt.emit(email)
        return False

    def _loop(self) -> None:
        threading.Thread(target=self._refresh_auth, name="apprestore-auth", daemon=True).start()
        while True:
            try:
                self._poll_once()
            except Exception as exc:  # noqa: BLE001
                self._publish(connected=False, loading=False, note=explain_user_error(str(exc)), rows=[])
            time.sleep(2)

    def _poll_once(self) -> None:
        with self._lock:
            if self._busy:
                return
            forced = self._force
            self._force = False
        try:
            udids = self.service.connected_udids()
        except Exception as exc:  # noqa: BLE001
            self._publish(connected=False, loading=False, note=explain_user_error(str(exc)), rows=[], udid="")
            return
        self._bindings = load_bindings()
        for udid in udids:
            if udid in self._device_cache:
                continue
            try:
                self._device_cache[udid] = self.service.core.tools.device_info(udid)
            except Exception:
                self._device_cache[udid] = Device(udid=udid)
        with self._lock:
            self._udid_order = list(udids)
            if self._udid not in udids:
                self._udid = udids[0] if udids else ""
            selected = self._udid
            need_apps = bool(selected) and (forced or not self._ready or selected != self._loaded_udid)
            seen = tuple(udids)
            list_changed = seen != self._seen_udids
            self._seen_udids = seen
        if not selected:
            self._loaded_udid = ""
            self._publish(connected=False, loading=False, note="", rows=[], udid="", ready=True)
            return
        info = self._device_cache.get(selected)
        form = device_form(
            info.product_type if info is not None else "",
            info.device_class if info is not None else "",
        )
        noun = device_noun(form)
        if not need_apps:
            if list_changed:
                with self._lock:
                    self._form = form
                    self._noun = noun
                    self._connected = True
                self.changed.emit()
            self._consider_account()
            return
        self._publish(
            connected=True,
            loading=True,
            note="",
            rows=[],
            udid=selected,
            form=form,
            noun=noun,
            ready=False,
        )
        self._consider_account()
        try:
            apps = self.service.offloaded(selected)
        except Exception as exc:  # noqa: BLE001
            with self._lock:
                if self._udid != selected:
                    return
            self._publish(
                connected=True,
                loading=False,
                note=explain_user_error(str(exc)),
                rows=[],
                udid=selected,
                form=form,
                noun=noun,
            )
            return
        with self._lock:
            if self._udid != selected:
                return
            self._loaded_udid = selected
        self._publish(
            connected=True,
            loading=False,
            note="",
            rows=[offloaded_card(app) for app in apps],
            by_key={app.bundle_id: app for app in apps},
            udid=selected,
            form=form,
            noun=noun,
        )

    def _publish(
        self,
        *,
        connected: bool,
        loading: bool,
        note: str,
        rows: list[dict[str, str]],
        udid: str | None = None,
        by_key: dict[str, OffloadedApp] | None = None,
        form: str | None = None,
        noun: str | None = None,
        signed: bool | None = None,
        ready: bool = True,
    ) -> None:
        with self._lock:
            self._connected = connected
            self._loading = loading
            self._note = note
            self._rows = rows
            self._ready = ready
            if udid is not None:
                self._udid = udid
            if by_key is not None:
                self._by_key = by_key
            elif not rows and not loading:
                self._by_key = {}
            if form is not None:
                self._form = form
            if noun is not None:
                self._noun = noun
            if signed is not None:
                self._signed = signed
        self.changed.emit()

    def _restore(self, udid: str, chosen: list[tuple[str, OffloadedApp | None]]) -> None:
        errors: list[str] = []
        try:
            if not udid:
                self.restoreSettled.emit(f"Подключите {self._noun_now()} кабелем.")
                return
            for key, app in chosen:
                if app is None:
                    errors.append("Приложение уже не в списке.")
                    continue
                try:
                    self._restore_one(udid, app)
                except Exception as exc:  # noqa: BLE001
                    errors.append(f"{app.name}: {explain_user_error(str(exc))}")
                else:
                    self.appRestored.emit(key)
            self.restoreSettled.emit("\n".join(errors))
        finally:
            with self._lock:
                self._busy = False
                self._force = True

    def _restore_one(self, udid: str, app: OffloadedApp) -> None:
        try:
            self.service.restore_offloaded(udid, app, try_device_redownload=True)
        except Exception as exc:
            if "refusing a competing ipa install" not in str(exc).lower():
                raise
            self.service.restore_offloaded(udid, app, try_device_redownload=False)

    def _install_store(self, udid: str, store_id: str, acquire: bool = False) -> None:
        try:
            if not udid:
                self.installSettled.emit(store_id, False, f"Подключите {self._noun_now()} кабелем.")
                return
            try:
                if not self._ensure_keychain():
                    self.installSettled.emit(
                        store_id,
                        False,
                        explain_user_error("keychain passphrase is required"),
                    )
                    return
                self.installProgress.emit(-1, "Проверяем лицензию")
                self._output_tail = ""
                runner = self.service.core.tools.runner
                previous = getattr(runner, "on_output", None)
                with self._tool_lock:
                    if hasattr(runner, "on_output"):
                        runner.on_output = self._on_tool_output
                    try:
                        self._restore_store_gated(udid, store_id, acquire)
                    finally:
                        if hasattr(runner, "on_output"):
                            runner.on_output = previous
            except LicenseDenied as denied:
                self.installSettled.emit(store_id, False, str(denied))
                return
            except Exception as exc:  # noqa: BLE001
                text = NOT_OWNED_TEXT if is_license_missing(str(exc)) else explain_user_error(str(exc))
                self.installSettled.emit(store_id, False, text)
                return
            self.installSettled.emit(store_id, True, "")
        finally:
            with self._lock:
                self._busy = False
                self._force = True

    def _restore_store_gated(self, udid: str, store_id: str, acquire: bool) -> None:
        core = self.service.core
        run_with_free_license(
            store_id,
            lambda: core.restore_by_store_id(udid, store_id),
            tools=core.tools,
            acquire=acquire,
            notify=lambda text: self.installProgress.emit(-1, text),
            mode="gui",
        )

    def _download_copy_gated(self, app: InstalledApp) -> object:
        service = self.service
        return run_with_free_license(
            app.store_id or "",
            lambda: service.download_to_library(app),
            tools=service.core.tools,
            acquire=True,
            notify=lambda text: self._set_files_note(f"{app.name}: {text}", busy=True),
            mode="gui",
        )

    def _emit_files_note(self) -> None:
        with self._lock:
            note = self._files_note
            busy = self._files_busy
        self.filesNoteChanged.emit(note, busy)

    def _set_files_note(self, note: str, *, busy: bool | None = None) -> None:
        with self._lock:
            self._files_note = note
            if busy is not None:
                self._files_busy = busy
        self._emit_files_note()

    def _set_phone_loading(self, loading: bool) -> None:
        with self._lock:
            self._phone_loading = loading
        self.phoneLoadingChanged.emit(loading)

    def _finish_files(self, note: str, *, ok_key: str = "", ok: bool = False) -> None:
        with self._lock:
            self._busy = False
            self._files_busy = False
            self._files_mode = False
            self._files_note = note
            self._force = True
        self._emit_files_note()
        self.copySettled.emit(ok_key, ok, note)

    def _load_library(self) -> None:
        try:
            entries = self.service.scan_local()
        except Exception as exc:  # noqa: BLE001
            self._set_files_note(explain_user_error(str(exc)))
            return
        rows = [library_card(entry) for entry in entries if isinstance(entry, IpaMetadata)]
        rows.sort(key=lambda row: str(row["name"]).casefold())
        with self._lock:
            self._library_rows = rows
        self.filesChanged.emit()

    def _load_phone(self, udid: str) -> None:
        try:
            if not udid:
                with self._lock:
                    self._phone_rows = []
                    self._phone_by_bundle = {}
                    self._phone_udid = ""
                    self._phone_loading = False
                    noun = self._noun or "iPhone"
                    missing = f"Подключите {noun} кабелем."
                    self._files_note = missing
                    busy = self._files_busy
                self.phoneLoadingChanged.emit(False)
                self.filesChanged.emit()
                self.filesNoteChanged.emit(missing, busy)
                return
            apps = self.service.installed_apps(udid)
        except Exception as exc:  # noqa: BLE001
            self._set_phone_loading(False)
            self._set_files_note(explain_user_error(str(exc)))
            return
        rows = [phone_card(app) for app in apps]
        rows.sort(key=lambda row: str(row["name"]).casefold())
        with self._lock:
            self._phone_loading = False
            if self._udid != udid:
                note = self._files_note
            else:
                self._phone_by_bundle = {app.bundle_id: app for app in apps}
                self._phone_rows = rows
                self._phone_udid = udid
                noun = self._noun or "iPhone"
                note = (
                    f"{len(rows)} на {noun}"
                    if rows
                    else f"На {noun} нет установленных приложений"
                )
                self._files_note = note
            busy = self._files_busy
        self.phoneLoadingChanged.emit(False)
        self.filesChanged.emit()
        self.filesNoteChanged.emit(note, busy)

    def _save_copies(self, apps: list[InstalledApp]) -> None:
        errors: list[str] = []
        saved = 0
        message = ""
        ok = False
        runner = self.service.core.tools.runner
        previous = getattr(runner, "on_output", None)
        try:
            if not self._ensure_keychain():
                message = explain_user_error("keychain passphrase is required")
                return
            self._files_mode = True
            self._output_tail = ""
            if hasattr(runner, "on_output"):
                runner.on_output = self._on_tool_output
            for index, app in enumerate(apps, 1):
                self._set_files_note(f"{index} из {len(apps)} · {app.name}", busy=True)
                if not app.store_id:
                    errors.append(f"{app.name}: нет номера в App Store")
                    continue
                try:
                    with self._tool_lock:
                        self._download_copy_gated(app)
                except LicenseDenied as denied:
                    errors.append(f"{app.name}: {denied}")
                except Exception as exc:  # noqa: BLE001
                    errors.append(f"{app.name}: {explain_user_error(str(exc))}")
                else:
                    saved += 1
            self._load_library()
            if errors:
                message = "\n".join(errors)
                if saved:
                    message = f"Сохранено: {saved}.\n{message}"
                return
            message = f"Сохранено: {saved}"
            ok = True
        except Exception as exc:  # noqa: BLE001
            message = explain_user_error(str(exc))
        finally:
            self._files_mode = False
            if hasattr(runner, "on_output"):
                runner.on_output = previous
            self._finish_files(message, ok=ok)

    def _install_saved(self, udid: str, path: str) -> None:
        message = ""
        ok = False
        try:
            if not udid:
                message = f"Подключите {self._noun_now()} кабелем."
                return
            ipa = Path(path)
            if not ipa.is_file():
                message = "Файл копии не найден."
                return
            self.service.install_ipa(udid, ipa)
            ok = True
        except Exception as exc:  # noqa: BLE001
            message = explain_user_error(str(exc))
        finally:
            with self._lock:
                if ok:
                    for row in self._library_rows:
                        if row.get("path") == path:
                            row["placed"] = True
                self._busy = False
                self._files_busy = False
                self._files_note = "" if ok else message
                self._force = True
            self.filesChanged.emit()
            self._emit_files_note()
            self.copySettled.emit(path if ok else "", ok, message)

    def _on_tool_output(self, text: str) -> None:
        self._output_tail = (self._output_tail + text.casefold())[-180:]
        if "err error" in self._output_tail or "success=false" in self._output_tail:
            return
        match = None
        for match in _PROGRESS.finditer(self._output_tail):
            pass
        if match is None:
            return
        percent = int(match.group(1))
        if percent <= 0:
            return
        if self._files_mode:
            label = "Сохраняем файл" if percent >= 100 else f"Скачиваем {percent}%"
            self._set_files_note(label, busy=True)
            return
        label = f"Ставим на {self._noun_now()}" if percent >= 100 else f"Скачиваем {percent}%"
        self.installProgress.emit(percent, label)

    def _maybe_probe_shelf(self) -> None:
        if os.environ.get("APPRESTORE_PROBE_SHELF") != "1" or self._probe_started:
            return
        self._probe_started = True
        threading.Thread(target=self._probe_shelf, name="apprestore-shelf", daemon=True).start()

    def _probe_shelf(self) -> None:
        # Debug only (APPRESTORE_PROBE_SHELF=1) and read-only: probe_store never
        # sends --purchase; "license is required" is reported as not-owned.
        print("shelf probe waiting for session", flush=True)
        deadline = time.monotonic() + 900
        while time.monotonic() < deadline:
            if self.service.keychain_ready() or probe_keychain() == "in":
                break
            time.sleep(2)
        else:
            print("shelf probe: session closed", flush=True)
            return
        path = Path(tempfile.gettempdir()) / "apprestore-shelf-check.txt"
        lines: list[str] = []
        for app in POPULAR_APPS:
            with self._tool_lock:
                kind = probe_store(self.service.core.tools, app["storeId"])
            line = f"{kind}\t{app['name']}\t{app['storeId']}"
            lines.append(line)
            path.write_text("\n".join(lines) + "\n", encoding="utf-8")
            print("shelf", line, flush=True)
        print("shelf probe done", path, flush=True)
