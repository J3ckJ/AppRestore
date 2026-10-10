"""Qt side of 4b: list model for «Что вернуть» and the window controller.

Thin on purpose: every decision is in ``selection``, ``space``, ``home``,
``flow``, ``scan`` and ``onboarding``. Sources:

* :class:`SessionSource` — the live :class:`QuickSession` (USB poll,
  offloaded apps, purchases, sign-in) plus the missing-apps list and the
  device's free space;
* ``fake_data.FakeSource`` — fixed data for screenshots and tests.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from dataclasses import replace
from typing import Any

from PySide6.QtCore import (
    Property,
    QAbstractListModel,
    QByteArray,
    QModelIndex,
    QObject,
    QTimer,
    Qt,
    Signal,
    Slot,
)

from apprestore_gui.ui4b.catalog import ACTION_IPA, ACTION_NONE, ACTION_STORE, GROUP_REGION, GROUP_REMOVED, RestoreItem, build_items
from apprestore_gui.ui4b.flow import APPLE_REJECTED_NOTE, RestoreFlow
from apprestore_gui.ui4b.formatting import format_size
from apprestore_gui.ui4b.licenses import (
    CANCEL,
    CONTINUE,
    OWNED_ONLY,
    LicensePlan,
    consent_view,
    next_slot,
    plan_licenses,
    slot_text,
)
from apprestore_gui.ui4b.licenses import candidates as license_candidates
from apprestore_gui.ui4b.home import STATE_APPLE_REJECTED, STATE_DONE, STATE_STORE_MISMATCH, HomeInput, PhoneApp, home_view, link_action
from apprestore_gui.ui4b.region import apply_statuses, load_classifier
from apprestore_gui.ui4b.region import classify as classify_region_ids
from apprestore_gui.ui4b import component
from apprestore_gui.ui4b.onboarding import Onboarding
from apprestore_core.license_gate import is_store_mismatch
from apprestore_core.license_guard import DEFAULT_TOTAL_LIMIT
from apprestore_gui.ui4b.queue import LIMIT_ERROR
from apprestore_gui.ui4b.onboarding import illustration_tiles, scanning_count, scanning_tiles
from apprestore_gui.ui4b.scan import ScanCounter
from apprestore_gui.ui4b.search import SEARCH_HINT, search_store
from apprestore_gui.ui4b.selection import CHECK_ON, OFFLINE_FOOTER, Selection
from apprestore_core.delisted_attempt import on_account as attempt_account
from apprestore_core.delisted_attempt import reset_session as reset_attempt_session
from apprestore_core.delisted_attempt import mark_attempted, record_prices, record_region_probe
from apprestore_gui.ui4b.find_qt import Find4b
from apprestore_gui.ui4b.network import REPROBE_S, any_host_answers, is_offline
from apprestore_gui.ui4b.names import builtin_names, purchase_names
from apprestore_gui.ui4b.names import FALLBACK as NAME_FALLBACK
from apprestore_gui.ui4b.names import resolve as resolve_name
from apprestore_gui.ui4b.names_lookup import DEADLINE_S as NAMES_DEADLINE_S
from apprestore_gui.ui4b.names_lookup import NameLookup, device_locale, vendored_display_names
from apprestore_gui.ui4b.settings import ARCHIVE_DEFAULT, UPDATE_BUSY, settings_view, update_status
from apprestore_gui.ui4b.space import UNKNOWN_SPACE, DeviceSpace, plan_space, query_device_space

ROLES: tuple[str, ...] = (
    "kind",
    "key",
    "group",
    "title",
    "countText",
    "check",
    "action",
    "name",
    "shortName",
    "ipaHint",
    "unverified",
    "nameHtml",
    "developer",
    "storeId",
    "bundleId",
    "sizeText",
    "sizeKnown",
    "note",
    "hasIpaHint",
    "selected",
    "selectable",
)


class PickerModel(QAbstractListModel):
    """Rows of :meth:`Selection.rows`. Same keys → dataChanged, so the list keeps its scroll."""

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._rows: list[dict[str, object]] = []

    def rowCount(self, parent: QModelIndex = QModelIndex()) -> int:  # noqa: B008
        return 0 if parent.isValid() else len(self._rows)

    def roleNames(self) -> dict[int, QByteArray]:
        return {Qt.ItemDataRole.UserRole + 1 + i: QByteArray(name.encode()) for i, name in enumerate(ROLES)}

    def data(self, index: QModelIndex, role: int = Qt.ItemDataRole.DisplayRole) -> Any:
        if not index.isValid() or not 0 <= index.row() < len(self._rows):
            return None
        offset = role - Qt.ItemDataRole.UserRole - 1
        if not 0 <= offset < len(ROLES):
            return None
        return self._rows[index.row()].get(ROLES[offset], "")

    def rows(self) -> list[dict[str, object]]:
        return list(self._rows)

    def set_rows(self, rows: list[dict[str, object]]) -> None:
        same = len(rows) == len(self._rows) and all(
            a.get("key") == b.get("key") for a, b in zip(rows, self._rows)
        )
        if not same:
            self.beginResetModel()
            self._rows = list(rows)
            self.endResetModel()
            return
        old = self._rows
        self._rows = list(rows)
        for i, (a, b) in enumerate(zip(old, rows)):
            if a != b:
                self.dataChanged.emit(self.index(i), self.index(i))


class SourceBase(QObject):
    """What the controller reads. Subclasses fill the fields and emit ``changed``."""

    changed = Signal()
    progress = Signal(int, str)
    appRestored = Signal(str)
    restoreSettled = Signal(str)
    installSettled = Signal(str, bool, str)
    copySettled = Signal(str, bool, str)

    connected = False
    loading = False
    device_name = ""
    noun = "iPhone"
    signed_in = False
    region_name = ""
    #: QuickSession.authPhase ("out" | "running" | "need_code" | "in" …).
    auth_phase = ""
    #: Apple wants the user again (QuickSession.sessionRelogin).
    relogin = False
    auth_status = ""
    account_email = ""
    #: False while there is no connection to Apple (QuickSession.sessionState "offline").
    online = True

    def items(self) -> list[RestoreItem]:
        return []

    # -- licenses (worker thread) --------------------------------------------------

    def owned_store_ids(self) -> set[str] | None:
        """Store ids in the purchase history (list-purchases); None = not known."""

        return None

    def free_prices(self, store_ids: list[str]) -> dict[str, float | None]:
        """iTunes lookup price in the account's country; None = unknown."""

        return {}

    def store_offers(self, store_ids: list[str]) -> dict[str, dict[str, object]]:
        """«Найти» (worker thread): {id: {price, developer}} from the public lookup
        in the account's country; price None = unknown."""

        return {}

    def store_statuses(self, store_ids: list[str]) -> dict[str, object]:
        """«Найти» (worker thread): region_probe for found removed apps; {} = not checked."""

        return {}

    def license_counts(self) -> tuple[int, int]:
        """(today, total) from license_guard.read_counts — the gate's own journal."""

        from apprestore_core.license_gate import journal_path
        from apprestore_core.license_guard import read_counts

        return read_counts(journal_path())

    def recheck(self) -> None:
        """The network is back: read again (lookup, list-purchases), buy nothing."""

    def reload_device(self) -> None:
        """A restore run ended: read the device again (offloaded, missing, installed, space)."""

    # «Файлы IPA» (QuickSession library / saveCopies)
    def library_files(self) -> list[dict[str, object]]:
        return []

    def load_library(self) -> None:
        pass

    def export_apps(self) -> list[dict[str, object]]:
        return [{"bundleId": a.bundle_id, "storeId": a.store_id, "name": a.name, "detail": ""}
                for a in self.phone_apps()]

    def save_copies(self, bundle_ids: list[str]) -> None:
        pass

    def load_phone(self) -> None:
        pass

    def files_note(self) -> tuple[str, bool]:
        return ("", False)

    def purchase_rows(self) -> list[dict[str, object]]:
        return []

    def missing_patches(self) -> tuple[str, ...]:
        """() when Макс's ``can_acquire_license()``; else the patches that are
        missing (for «Подробнее»; may be ("?",) when it cannot say which)."""

        return ()

    def search_store(self, term: str, purchases: list[dict[str, object]]) -> list[dict[str, str]]:
        """Worker thread: App Store + purchases (ui4b.search), no IPA catalogues."""

        return search_store(term, purchases)

    def limit_slot_text(self) -> str:
        """For «не хватило лимита»: next_daily_slot + read_counts, local time."""

        from apprestore_core.license_gate import journal_path

        path = journal_path()
        return slot_text(next_slot(path), self.license_counts()[1])

    def phone_apps(self) -> list[PhoneApp]:
        return []

    def space(self) -> DeviceSpace:
        return UNKNOWN_SPACE

    def fresh_space(self) -> DeviceSpace:
        """Blocking device query (worker thread); default: the last known."""

        return self.space()

    def scan(self) -> tuple[int, int | None, bool]:
        """(checked, total, done) for the onboarding counter."""

        return (0, None, not self.loading)

    # Backend for RestoreFlow
    def restore_offloaded(self, keys: list[str]) -> None:
        raise NotImplementedError

    def install_store(self, store_id: str) -> None:
        raise NotImplementedError

    def install_ipa(self, path: str) -> None:
        raise NotImplementedError

    def start_scan(self) -> None:
        """Onboarding step 4 began: start reading the purchase history."""

    # Sign-in (QuickSession.login / submitCode)
    def login(self, email: str, password: str) -> None:
        pass

    def submit_code(self, code: str) -> None:
        pass

    def cancel_login(self) -> None:
        pass

    def sign_out(self) -> None:
        """QuickSession.signOut → AppRestoreService.sign_out (revoke, session copy
        and purchases-cache.json deleted; license journal and icon cache stay)."""


class SessionSource(SourceBase):
    """Live data from :class:`QuickSession`; installs go through its gated calls."""

    _missingReady = Signal(str, object)
    _spaceReady = Signal(str, object)
    _hostsReady = Signal(bool)
    _namesReady = Signal()

    def __init__(self, session: Any) -> None:
        super().__init__()
        self.session = session
        self._missing: list[Any] = []
        self._missing_udid = ""
        self._space = UNKNOWN_SPACE
        self._space_udid = ""
        session.changed.connect(self._on_session)
        session.purchasesChanged.connect(self.changed)
        if hasattr(session, "sessionChanged"):
            session.sessionChanged.connect(self._on_session)
        session.installProgress.connect(self.progress)
        session.appRestored.connect(self.appRestored)
        session.restoreSettled.connect(self.restoreSettled)
        session.installSettled.connect(self.installSettled)
        session.copySettled.connect(self.copySettled)
        if hasattr(session, "filesChanged"):
            session.filesChanged.connect(self.changed)  # phoneApps (installed apps, pymobiledevice3)
        if hasattr(session, "filesNoteChanged"):
            session.filesNoteChanged.connect(lambda *_a: self.changed.emit())  # «Файлы IPA» note/busy
        if hasattr(session, "copySettled"):
            session.copySettled.connect(lambda *_a: self.load_library())  # a saved copy → re-scan
        self._phone_udid = ""
        self._statuses: dict[str, object] = {}
        self._builtin_names: dict[str, str] | None = None
        #: app_names.display_names in the background (names_lookup); results → changed
        self._names = NameLookup(vendored_display_names(), notify=self._namesReady.emit)
        self._namesReady.connect(self.changed)
        self._missingReady.connect(self._on_missing)
        self._spaceReady.connect(self._on_space)
        self._hostsReady.connect(self._on_hosts)
        #: anonymous TCP probe (ui4b.network): None = not asked / no answer yet
        self._hosts_answer: bool | None = None
        self._hosts_busy = False
        self._hosts_at = 0.0
        self._probe_hosts: Callable[[], bool] = any_host_answers

    # -- reading ---------------------------------------------------------------

    def _on_session(self) -> None:
        s = self.session
        self.connected = bool(s.connected)
        self.loading = bool(s.loading)
        self.device_name = str(s.deviceName or "")
        self.noun = str(s.deviceNoun or "iPhone")
        self.signed_in = bool(s.signedIn)
        self.auth_phase = str(getattr(s, "authPhase", "") or "")
        # QuickSession puts -128 into the same «sign in again» state as an expired
        # session (old window). 4b has its own -128 screen: not a relogin here.
        note = str(getattr(s, "sessionNote", "") or "")
        self.relogin = bool(getattr(s, "sessionRelogin", False)) and not is_store_mismatch(note)
        self.auth_status = str(getattr(s, "authStatus", "") or "")
        self.account_email = str(getattr(s, "accountEmail", "") or getattr(s, "boundEmail", "") or "")
        # «offline» from the session probe is not «no internet» by itself (no account,
        # an unfinished sign-in, an unknown ipatool answer…): ask public hosts too
        state = str(getattr(s, "sessionState", "") or "")
        if state == "offline":
            self._ask_hosts()
        else:
            self._hosts_answer = None
        self._set_online(not is_offline(state, self._hosts_answer))
        udid = s.current_udid()
        if self.connected and udid and not self.loading and udid != self._missing_udid:
            self._missing_udid = udid
            threading.Thread(target=self._load_missing, args=(udid,), daemon=True, name="ui4b-missing").start()
        if self.connected and udid and udid != self._space_udid:
            self._space_udid = udid
            threading.Thread(target=self._load_space, args=(udid,), daemon=True, name="ui4b-space").start()
        if self.connected and udid and udid != self._phone_udid and hasattr(s, "loadPhone"):
            self._phone_udid = udid
            s.loadPhone()
        if not self.connected:
            self._phone_udid = ""
            self._missing_udid = ""
            self._space_udid = ""
            self._space = UNKNOWN_SPACE
        self.changed.emit()

    def _load_missing(self, udid: str) -> None:
        try:
            apps = self.session.service.missing(udid)
        except Exception:  # noqa: BLE001 - the list just stays without them
            apps = []
        self._statuses = self._classify(apps)
        self._missingReady.emit(udid, apps)

    def _classify(self, apps: object) -> dict[str, object]:
        """region_probe for the user's apps not on the phone (worker thread, only online)."""

        if not self.online:
            return {}
        if self.missing_patches():
            return {}  # needs-component: region_probe is off
        try:
            country = str(self.session.service.core.tools.account_country() or "").strip().upper()
        except Exception:  # noqa: BLE001
            country = ""
        ids = [str(getattr(app, "store_id", "") or "") for app in apps or []]  # type: ignore[union-attr]
        return classify_region_ids(load_classifier(), ids, country or None, online=self.online)

    def _on_missing(self, udid: str, apps: object) -> None:
        if udid == self.session.current_udid():
            self._missing = list(apps or [])  # type: ignore[call-overload]
            self.changed.emit()

    def library_files(self) -> list[dict[str, object]]:
        return list(getattr(self.session, "libraryFiles", []) or [])

    def load_library(self) -> None:
        if hasattr(self.session, "loadLibrary"):
            self.session.loadLibrary()

    def export_apps(self) -> list[dict[str, object]]:
        rows = list(getattr(self.session, "phoneApps", []) or [])
        names = {(a.bundle_id or a.store_id): a.name for a in self.phone_apps()}
        return [dict(r, name=names.get(str(r.get("bundleId") or r.get("storeId") or ""), r.get("name"))) for r in rows]

    def load_phone(self) -> None:
        if hasattr(self.session, "loadPhone"):
            self.session.loadPhone()

    def save_copies(self, bundle_ids: list[str]) -> None:
        if hasattr(self.session, "saveCopies"):
            self.session.saveCopies(list(bundle_ids))

    def files_note(self) -> tuple[str, bool]:
        return (str(getattr(self.session, "filesNote", "") or ""), bool(getattr(self.session, "filesBusy", False)))

    def reload_device(self) -> None:
        self._missing_udid = ""
        self._phone_udid = ""
        self._space_udid = ""
        refresh = getattr(self.session, "refresh", None)
        if callable(refresh):
            refresh()  # QuickSession re-reads the offloaded list (its loop, _force)
        self._on_session()

    def _set_online(self, online: bool) -> None:
        was_online = self.online
        self.online = online
        if online and not was_online:
            self.recheck()

    def _ask_hosts(self) -> None:
        if self._hosts_busy or time.monotonic() - self._hosts_at < REPROBE_S:
            return
        self._hosts_busy = True
        probe = self._probe_hosts

        def work() -> None:
            try:
                ok = bool(probe())
            except Exception:  # noqa: BLE001
                ok = True  # cannot tell: never claim «offline»
            self._hostsReady.emit(ok)

        threading.Thread(target=work, daemon=True, name="ui4b-hosts").start()

    def _on_hosts(self, ok: bool) -> None:
        self._hosts_busy = False
        self._hosts_at = time.monotonic()
        self._hosts_answer = ok
        state = str(getattr(self.session, "sessionState", "") or "")
        self._set_online(not is_offline(state, ok))
        self.changed.emit()

    def _load_space(self, udid: str) -> None:
        try:
            space = query_device_space(self.session.service.core.tools, udid)
        except Exception:  # noqa: BLE001 - no service yet: «не удалось проверить»
            space = UNKNOWN_SPACE
        self._spaceReady.emit(udid, space)

    def _on_space(self, udid: str, space: object) -> None:
        if udid == self.session.current_udid() and isinstance(space, DeviceSpace):
            self._space = space
            self.changed.emit()

    def _name_books(self) -> tuple[dict[str, str], dict[str, str]]:
        if self._builtin_names is None:
            self._builtin_names = builtin_names()
        return purchase_names(list(self.session.purchases or [])), self._builtin_names

    def _named(self, apps: list[Any], nameless: list[tuple[str, str]] | None = None) -> list[Any]:
        """device CFBundleDisplayName/CFBundleName/iTunesMetadata → purchases cache →
        app_names.display_names (background) → built-in list → «Приложение»."""

        bought, builtin = self._name_books()
        looked = self._names.found
        out = []
        for app in apps:
            bid = str(getattr(app, "bundle_id", "") or "")
            sid = str(getattr(app, "store_id", "") or "")
            name = resolve_name(getattr(app, "name", ""), bundle_id=bid, store_id=sid, purchases=bought,
                                builtin=builtin, looked_up=looked)
            if name == NAME_FALLBACK and nameless is not None:
                nameless.append((sid, bid))
            try:
                out.append(replace(app, name=name) if name != getattr(app, "name", None) else app)
            except TypeError:  # not a dataclass (tests): keep it
                out.append(app)
        return out

    def _look_up_names(self, nameless: list[tuple[str, str]]) -> None:
        """Nameless tiles → display_names in a worker (missing first: they are the
        tiles on screen). Online only; the account country only after sign-in."""

        if not nameless or not self.online:
            return
        ids = [sid for sid, _ in nameless if sid]
        bids = [bid for _, bid in nameless if bid]
        udid = str(self.session.current_udid() or "") if hasattr(self.session, "current_udid") else ""
        signed_in = bool(self.signed_in)

        def country() -> str | None:
            return self._account_country().upper() or None if signed_in else None

        if self._names.request(ids, bids, account_country=country, locale=lambda: device_locale(udid)):
            QTimer.singleShot(int(NAMES_DEADLINE_S * 1000) + 50, self.changed.emit)  # → «Приложение»

    def items(self) -> list[RestoreItem]:
        nameless: list[tuple[str, str]] = []
        missing = self._named(list(self._missing), nameless)
        offloaded = self._named(list(self.session.offloaded_snapshot()), nameless)
        self._look_up_names(nameless)
        items = apply_statuses(build_items(offloaded, missing), self._statuses)
        return [replace(item, name_pending=True)
                if item.name == NAME_FALLBACK and self._names.pending(item.store_id, item.bundle_id) else item
                for item in items]

    def phone_apps(self) -> list[PhoneApp]:
        rows = list(getattr(self.session, "phoneApps", []) or [])
        bought, builtin = self._name_books()
        out = []
        for r in rows:
            sid, bid = str(r.get("storeId") or ""), str(r.get("bundleId") or "")
            name = resolve_name(r.get("name"), bundle_id=bid, store_id=sid, purchases=bought, builtin=builtin)
            out.append(PhoneApp(name, store_id=sid, bundle_id=bid))
        return out

    def space(self) -> DeviceSpace:
        return self._space

    def purchase_rows(self) -> list[dict[str, object]]:
        return list(self.session.purchases or [])

    def missing_patches(self) -> tuple[str, ...]:
        # ipatoolCaps.canAcquireLicense = client.can_acquire_license() (cached probes)
        try:
            tools = self.session.service.core.tools
            if tools.license_preflight() is None:
                return ()
            caps = tools.ipatool_capabilities()
        except Exception:  # noqa: BLE001 - cannot tell: like the gate, not safe
            return ("?",)
        if caps is None:
            return ("0001", "0002", "0003")
        missing = tuple(
            name for name, ok in (
                ("0001", caps.auth_info_country is not False),
                ("0002", caps.list_purchases_all),
                ("0003", caps.passphrase_stdin),
            ) if not ok
        )
        return missing or ("?",)

    def owned_store_ids(self) -> set[str] | None:
        # QuickSession.purchases = purchases.py's list-purchases cache
        # (ipatool_api.all_purchases / iter_purchases).
        rows = list(self.session.purchases or [])
        if not rows:
            return None
        return {str(row.get("trackId") or "") for row in rows} - {""}

    def free_prices(self, store_ids: list[str]) -> dict[str, float | None]:
        from apprestore_core.license_gate import lookup_offer

        try:
            country = str(self.session.service.core.tools.account_country() or "").strip().lower()
        except Exception:  # noqa: BLE001
            country = ""
        out: dict[str, float | None] = {}
        for store_id in store_ids:
            price: float | None = None
            if country and str(store_id).isdigit():
                try:
                    offer = lookup_offer(str(store_id), (country,))
                    price = float(offer["price"]) if offer and offer.get("price") is not None else None
                except Exception:  # noqa: BLE001 - unknown price: the gate refuses it anyway
                    price = None
            out[str(store_id)] = price
        return out

    def _account_country(self) -> str:
        try:
            return str(self.session.service.core.tools.account_country() or "").strip().lower()
        except Exception:  # noqa: BLE001
            return ""

    def store_offers(self, store_ids: list[str]) -> dict[str, dict[str, object]]:
        from apprestore_core.license_gate import lookup_offer
        from apprestore_gui.ui4b.find import offers_from

        country = self._account_country()
        found: dict[str, dict[str, object] | None] = {}
        for store_id in store_ids:
            offer = None
            if country and str(store_id).isdigit():
                try:
                    offer = lookup_offer(str(store_id), (country,))
                except Exception:  # noqa: BLE001 - unknown price: «Не удалось проверить»
                    offer = None
            found[str(store_id)] = offer
        return offers_from(found)

    def store_statuses(self, store_ids: list[str]) -> dict[str, object]:
        if not self.online or self.missing_patches():
            return {}
        country = self._account_country().upper()
        return classify_region_ids(load_classifier(), list(store_ids), country or None, online=self.online)

    def recheck(self) -> None:
        # Read-only: purchase history and the missing list (lookup). No purchase.
        if self.session.signedIn:
            self.session.loadPurchases()
        udid = self.session.current_udid()
        if self.connected and udid:
            self._missing_udid = udid
            threading.Thread(target=self._load_missing, args=(udid,), daemon=True, name="ui4b-missing").start()

    def fresh_space(self) -> DeviceSpace:
        udid = self.session.current_udid()
        space = query_device_space(self.session.service.core.tools, udid)
        self._spaceReady.emit(udid, space)
        return space

    def scan(self) -> tuple[int, int | None, bool]:
        rows = self.session.purchases
        busy = bool(self.session.purchasesBusy)
        progress = str(self.session.purchasesProgress or "")
        total = None
        if " из " in progress:
            try:
                total = int(progress.split(" из ", 1)[1])
            except ValueError:
                total = None
        return (len(rows), total, not busy and not self.loading)

    # -- actions ---------------------------------------------------------------

    def restore_offloaded(self, keys: list[str]) -> None:
        self.session.restore(keys)

    def install_store(self, store_id: str) -> None:
        # QuickSession.installStore → license_gate.run_with_free_license.
        self.session.installStore(store_id)

    def install_ipa(self, path: str) -> None:
        self.session.installSaved(path)

    def start_scan(self) -> None:
        if self.session.signedIn:
            self.session.loadPurchases()

    def login(self, email: str, password: str) -> None:
        self.session.login(email, password)

    def submit_code(self, code: str) -> None:
        self.session.submitCode(code)

    def cancel_login(self) -> None:
        if hasattr(self.session, "cancelLogin"):
            self.session.cancelLogin()

    def sign_out(self) -> None:
        self._names.clear()  # names looked up for this Apple ID's storefront
        self.session.signOut()


class Restore4b(QObject):
    """Everything the 4b QML reads. One ``changed`` signal; QML re-reads properties."""

    changed = Signal()
    storeSearchRequested = Signal(str)
    #: «Войти» / «Войти заново»: QML opens the sign-in sheet (2FA inside).
    signInRequested = Signal()
    _spaceChecked = Signal(object)  # (space, chosen, owned, prices)

    #: the archive switch in «Настройки» (window.py stores it in QSettings).
    archiveSearchChanged = Signal(bool)
    #: «Проверить обновления» found a newer version: window.py shows the old UpdateDialog.
    updateAvailable = Signal(object)
    _updateChecked = Signal(object)

    def __init__(self, source: SourceBase, *, onboarded: bool = True, mark_color: str = "#f6ebe4",
                 archive_search: bool = ARCHIVE_DEFAULT, update_check: Any = None,
                 find_options: dict[str, Any] | None = None) -> None:
        super().__init__()
        self.source = source
        self._archive_search = bool(archive_search)
        self._update_check = update_check
        self._settings_open = False
        self._update_text = ""
        self._update_busy = False
        self.finder = Find4b(
            source,
            archive_enabled=lambda: self._archive_search,
            install=self.installFound,
            open_settings=self.openSettings,
            sign_in=self.openSignIn,
            component_missing=lambda: bool(self._patches_missing),
            rejected=lambda: set(self.flow.apple_rejected),
            **(find_options or {}),
        )
        self.finder.changed.connect(self.changed)
        self._updateChecked.connect(self._on_update_checked)
        self.selection = Selection(mark_color=mark_color)
        self.picker = PickerModel(self)
        self._run_reread = False
        self._files_open = False
        self._files_mode = "list"
        self._files_chosen: set[str] = set()
        self.flow = RestoreFlow(source, self._refresh)
        self.scan = ScanCounter()
        self.onboarding = Onboarding(started=onboarded, apple_id_skipped=onboarded, finished=onboarded)
        self._picker_open = False
        self._done_dismissed = False
        self._checking_space = False
        self._home: dict[str, object] = {}
        self._step = 0
        self._revision = 0
        self._scan_started = False
        self._signin_open = False
        self._consent: dict[str, object] = {}
        self._limit_note: tuple[object, object] = (None, ("", False))
        self._found: list[dict[str, str]] = []
        self._account_open = False
        self._signout_confirm = False
        self._patches_missing: tuple[str, ...] = ()
        self._component_details = False
        self._rejected_seen: set[str] = set()
        self._consent_plan: LicensePlan | None = None
        self._consent_space: DeviceSpace = UNKNOWN_SPACE
        source.changed.connect(self._on_source)
        source.progress.connect(self.flow.on_progress)
        source.appRestored.connect(self.flow.on_app_restored)
        source.restoreSettled.connect(self.flow.on_restore_settled)
        source.installSettled.connect(self.flow.on_install_settled)
        source.copySettled.connect(self._on_copy_settled)
        self._spaceChecked.connect(self._start_after_space)
        self._storeFound.connect(self._on_found)
        self._on_source()

    # -- updates ---------------------------------------------------------------

    def _on_source(self) -> None:
        src = self.source
        self.flow.observe_account(src.signed_in, src.auth_phase, src.relogin, src.account_email)
        if src.signed_in and src.account_email:
            attempt_account(src.account_email)  # another Apple ID → new §1.14 session
        if self._signin_open and src.signed_in and src.auth_phase == "in" and not src.relogin:
            self._signin_open = False
        self._patches_missing = tuple(self.source.missing_patches()) or (
            ("?",) if self.flow.component_blocked else ()
        )
        items = self.source.items()
        if self._patches_missing:
            items = component.mark(items, self.source.owned_store_ids(), self._patches_missing)
        if self.flow.apple_rejected:
            # session-only mark, the group stays (not a region problem)
            items = [
                replace(it, action=ACTION_NONE, note=APPLE_REJECTED_NOTE)
                if it.store_id and it.store_id in self.flow.apple_rejected and it.action == ACTION_STORE else it
                for it in items
            ]
        self.selection.set_items(items)
        self.selection.set_space(self.source.space())
        self.selection.set_offline(not src.online)
        checked, total, done = self.source.scan()
        self.scan.update(checked, total)
        if done:
            self.scan.finish()
        self._refresh()

    def _refresh(self) -> None:
        finished = self.flow.queue.finished
        if finished and not self._run_reread:
            # a run just ended: read the phone again (offloaded, missing, installed
            # apps, free space) so the home screen counts what is really left
            self._run_reread = True
            self.source.reload_device()
        elif not finished:
            self._run_reread = False
        if self.flow.apple_rejected != self._rejected_seen:
            self._rejected_seen = set(self.flow.apple_rejected)
            self._on_source()  # re-mark «Apple отказала в выдаче» (memory only)
            return
        if self.flow.component_blocked and not self._patches_missing:
            self._on_source()  # NEEDS_PATCHED_BUILD mid-run: re-mark the list (needs-component)
            return
        src = self.source
        self._step = self.onboarding.step(
            connected=src.connected, signed_in=src.signed_in, scan_done=self.scan.done
        )
        if self._step == 4 and not self._scan_started:
            self._scan_started = True
            self.source.start_scan()
        queue = self.flow.queue if self.flow.queue.entries else None
        self._home = home_view(
            HomeInput(
                connected=src.connected,
                loading=src.loading,
                device_name=src.device_name,
                noun=src.noun,
                signed_in=src.signed_in,
                items=self.selection.items,
                space=src.space(),
                queue=queue,
                done_dismissed=self._done_dismissed,
                phone_apps=src.phone_apps(),
                region_name=src.region_name,
                relogin=bool(src.relogin or self.flow.needs_signin),
                store_problem=self.flow.store_problem,
                store_problem_app=self.flow.store_problem_app,
                store_problem_name=self.flow.store_problem_name,
                limit_note=self._limit_note_for_queue(queue)[0],
                limit_total=self._limit_note_for_queue(queue)[1],
                component_details=component.details_text(self._patches_missing)
                if self._component_details and self._patches_missing else "",
                component=bool(self._patches_missing),
            )
        )
        self.picker.set_rows(self.selection.rows())
        self._revision += 1
        self.changed.emit()

    def _on_copy_settled(self, key: str, ok: bool, text: str) -> None:
        entry = self.flow.queue.current()
        if entry is not None and entry.item.ipa_path:
            self.flow.on_install_settled(entry.item.ipa_path if ok else "", ok, text)

    # -- properties --------------------------------------------------------------

    @Property("QVariantMap", notify=changed)
    def ipatoolCaps(self) -> dict[str, object]:
        return {"canAcquireLicense": not self._patches_missing, "missing": list(self._patches_missing)}

    @Property("QVariantMap", notify=changed)
    def home(self) -> dict[str, object]:
        view = dict(self._home)
        if self._step == 1:
            view["tiles"] = illustration_tiles()
        elif self._step == 4 and not self.scan.done:
            view["tiles"] = scanning_tiles(list(view.get("tiles") or []), self.scan.fraction)
        return view

    @Property(str, notify=changed)
    def screen(self) -> str:
        return "onboarding" if self._step else "home"

    @Property(int, notify=changed)
    def onboardingStep(self) -> int:
        return self._step

    @Property("QVariantList", notify=changed)
    def stepper(self) -> list[dict[str, object]]:
        return self.onboarding.stepper(self._step)

    @Property(str, notify=changed)
    def scanCaption(self) -> str:
        return self.scan.caption()

    @Property(str, notify=changed)
    def scanEta(self) -> str:
        return self.scan.eta()

    @Property(float, notify=changed)
    def scanFraction(self) -> float:
        return self.scan.fraction

    @Property("QVariantList", notify=changed)
    def scanFound(self) -> list[dict[str, object]]:
        items = self.selection.items
        offloaded = sum(1 for item in items if item.group == "offloaded")
        removed = sum(1 for item in items if item.group == "removed")
        frac, done = self.scan.fraction, self.scan.done
        return [
            {"count": scanning_count(offloaded, frac, done), "label": "сгруженных"},
            {"count": scanning_count(removed, frac, done), "label": "удалённых из App Store"},
        ]

    @Property(int, notify=changed)
    def revision(self) -> int:
        """Bumped on every refresh; lets QML re-run function bindings."""

        return self._revision

    @Property(str, notify=changed)
    def searchNote(self) -> str:
        return self.selection.search_note()

    @Slot(int, int, result="QVariantMap")
    def headerAt(self, index: int, _revision: int = 0) -> dict[str, object]:
        """Header row of the group that list row ``index`` belongs to (sticky header)."""

        rows = self.picker.rows()
        if not 0 <= index < len(rows):
            return {}
        for row in reversed(rows[: index + 1]):
            if row.get("kind") == "header":
                return dict(row)
        return {}

    _storeFound = Signal(object)

    @Property(str, constant=True)
    def searchHint(self) -> str:
        return SEARCH_HINT

    @Property("QVariantList", notify=changed)
    def storeFound(self) -> list[dict[str, str]]:
        return list(self._found)

    @Slot(str)
    def searchStore(self, query: str) -> None:
        """App Store (iTunes search/lookup) + the account's purchases. Nothing else."""

        self.storeSearchRequested.emit(query)
        if not query.strip() or not self.source.online:
            return
        purchases = self.source.purchase_rows()
        threading.Thread(
            target=lambda: self._storeFound.emit(self.source.search_store(query, purchases)),
            daemon=True, name="ui4b-store-search",
        ).start()

    def _on_found(self, rows: object) -> None:
        self._found = [
            dict(r, sourceText="в ваших покупках" if r.get("source") == "purchases" else "App Store")
            for r in (rows or [])  # type: ignore[union-attr]
        ]
        self._refresh()

    @Slot(str, str)
    def installFound(self, store_id: str, name: str, store_status: str = "") -> None:
        """A found app: the same path as «Вернуть» (fresh space, consent if needed, gate).
        ``store_status`` comes from region_probe only (Find4b passes its answer)."""

        item = RestoreItem(key=f"store:{store_id}", name=name or store_id, group=GROUP_REMOVED,
                           action=ACTION_STORE, store_id=store_id, store_status=store_status)
        self._start([item])

    @Property(bool, notify=changed)
    def pickerOpen(self) -> bool:
        return self._picker_open

    @Property(QObject, constant=True)
    def pickerModel(self) -> QObject:
        return self.picker

    @Property("QVariantList", notify=changed)
    def rail(self) -> list[dict[str, object]]:
        return self.selection.rail_rows()

    @Property(str, notify=changed)
    def sort(self) -> str:
        return self.selection.sort

    @Property(str, notify=changed)
    def query(self) -> str:
        return self.selection.query

    @Property(str, notify=changed)
    def pickerSubtitle(self) -> str:
        src = self.source
        name = src.device_name or src.noun
        space = src.space()
        if space.free_bytes is None:
            return f"{name} · свободное место не удалось проверить"
        total = f" из {format_size(space.total_bytes, floor=True)}" if space.total_bytes else ""
        return f"{name} · свободно {format_size(space.free_bytes, floor=True)}{total}"

    @Property("QVariantMap", notify=changed)
    def footer(self) -> dict[str, object]:
        plan = self.selection.plan()
        bold, rest = plan.warning(self.source.noun)
        if self.flow.last_plan is not None and self._checking_space:
            bold, rest = "", "Проверяем место на телефоне…"
        if not self.source.online:
            bold, rest = "", OFFLINE_FOOTER
        return {
            "total": plan.total_text(),
            "free": plan.free_text(),
            "ratio": plan.ratio,
            "over": plan.verdict == "over",
            "known": plan.free_bytes is not None,
            "warnBold": bold,
            "warnText": rest,
            "go": f"Вернуть {plan.count}" if plan.count else "Вернуть",
            "goEnabled": bool(plan.count) and not plan.blocked and not self._checking_space and not self.flow.running,
        }

    # -- picker slots ------------------------------------------------------------

    @Slot()
    def openPicker(self) -> None:
        self._picker_open = True
        self._refresh()

    @Slot()
    def closePicker(self) -> None:
        self._picker_open = False
        self.selection.set_query("")
        self._refresh()

    @Slot(str)
    def toggle(self, key: str) -> None:
        if self.selection.toggle(key):
            self._refresh()

    @Slot(str)
    def toggleGroup(self, group: str) -> None:
        if group == GROUP_REGION and not any(i.selectable for i in self.selection.items if i.group == group):
            return
        self.selection.toggle_group(group)
        self._refresh()

    @Slot()
    def selectAll(self) -> None:
        self.selection.select_visible(True)
        self._refresh()

    @Slot()
    def clearAll(self) -> None:
        self.selection.select_visible(False)
        self._refresh()

    @Slot(str)
    def setSort(self, sort: str) -> None:
        self.selection.set_sort(sort)
        self._refresh()

    @Slot(str)
    def setRail(self, rail: str) -> None:
        self.selection.set_rail(rail)
        self._refresh()

    @Slot(str)
    def setQuery(self, query: str) -> None:
        self.selection.set_query(query)
        self._refresh()

    def _limit_note_for_queue(self, queue: object) -> tuple[str, bool]:
        """(slot text, total used up): read once per finished queue with limit refusals."""

        if queue is None or not getattr(queue, "finished", False):
            return ("", False)
        if not any(e.error == LIMIT_ERROR for e in queue.entries):  # type: ignore[attr-defined]
            return ("", False)
        if self._limit_note[0] is not queue:
            try:
                total = self.source.license_counts()[1] >= DEFAULT_TOTAL_LIMIT
                text = "" if total else self.source.limit_slot_text()
            except Exception:  # noqa: BLE001
                total, text = False, ""
            self._limit_note = (queue, (text, total))
        return self._limit_note[1]  # type: ignore[return-value]

    # -- restore -----------------------------------------------------------------

    @Slot()
    def primaryAction(self) -> None:
        """The big button of the home screen."""

        state = str(self._home.get("state", ""))
        if state == STATE_DONE:
            self.dismissDone()
        elif state in (STATE_STORE_MISMATCH, STATE_APPLE_REJECTED):
            self.flow.dismiss_store_problem()  # «На главный»
        elif state == "signin":
            self.openSignIn()
        elif state in ("many",):
            self.openPicker()
        elif state in ("missing", "region", "needs_component"):
            self.selection.set_rail("all")
            self.selection.set_query("")
            for item in self.selection.items:
                # the home button keeps its count: region apps only from «Что вернуть»
                self.selection.set_selected(item.key, item.selectable and item.group != GROUP_REGION)
            self.restoreSelected()

    @Slot()
    def restoreSelected(self) -> None:
        """Fresh free-space check, the license count, then the queue (or a sheet)."""

        if self._checking_space or self.flow.running or self._consent:
            return
        if not self.source.online and not self.selection.selected_items():
            return  # offline: only offloaded ones can go (the rest are not selectable)
        self._start(self.selection.selected_items())

    def _start(self, chosen: list[RestoreItem]) -> None:
        if self._checking_space or self.flow.running or self._consent or not chosen:
            return
        self._checking_space = True
        self._refresh()
        threading.Thread(target=self._check_space, args=(chosen,), daemon=True, name="ui4b-space-check").start()

    def _check_space(self, chosen: list[RestoreItem]) -> None:
        try:
            space = self.source.fresh_space()
        except Exception:  # noqa: BLE001
            space = UNKNOWN_SPACE
        try:
            if not self.source.online:
                raise LookupError("offline: nothing to look up (only offloaded ones go)")
            owned = self.source.owned_store_ids()
            prices = self.source.free_prices(license_candidates(chosen, owned))
            record_prices(prices)
        except Exception:  # noqa: BLE001 - unknown: the gate decides per app
            owned, prices = None, {}
        self._spaceChecked.emit((space, chosen, owned, prices))

    def _start_after_space(self, payload: object) -> None:
        self._checking_space = False
        space, chosen, owned, prices = payload  # type: ignore[misc]
        fresh = space if isinstance(space, DeviceSpace) else UNKNOWN_SPACE
        self.selection.set_space(fresh)
        if plan_space([i for i in chosen if i.selectable], fresh).blocked:
            self.flow.begin(chosen, fresh)  # records the warning, starts nothing
            self._refresh()
            return
        if self._patches_missing:
            # No free licenses on this ipatool: no consent sheet; what needs one
            # is listed in the summary, the rest goes as usual (gate refuses anyway).
            owned_ids = None if owned is None else {str(s) for s in owned}
            blocked = [i for i in chosen if component.needs_component(i, owned_ids) or i.note == component.COMPONENT_NOTE]
            rest = [i for i in chosen if i not in blocked]
            skipped = {"component": [i.label for i in blocked]} if blocked else {}
            result = self.flow.begin(rest, fresh, skipped)
            if not result.blocked:
                self._picker_open = False
                self._done_dismissed = False
            self._refresh()
            return
        plan = plan_licenses(chosen, owned, prices)
        if plan.k == 0:
            self._begin(plan, CONTINUE, fresh)
            return
        self._consent_plan = plan
        self._consent_space = fresh
        self._show_consent()

    def _show_consent(self) -> None:
        # Read the journal every time the sheet is shown (another process may
        # have written to it): the same numbers the gate will use.
        assert self._consent_plan is not None
        self._consent = consent_view(self._consent_plan, self.source.license_counts())
        self._refresh()

    def _begin(self, plan: LicensePlan, choice: str, space: DeviceSpace) -> None:
        if choice == CONTINUE:
            # tell the gate (memory only) which unknown-price apps region_probe flagged
            flagged = {i.store_id: i.store_status for i in plan.need if i.store_status}
            record_region_probe(flagged)
            mark_attempted(flagged)  # one attempt per app per session (§1.14)
        skipped = {"paid": [i.label for i in plan.paid]}
        if choice == OWNED_ONLY:
            skipped["no_license"] = [i.label for i in plan.need]
        result = self.flow.begin(plan.items_for(choice), space, skipped)
        if not result.blocked:
            self._picker_open = False
            self._done_dismissed = False
        self._refresh()

    def _consent_choice(self, choice: str) -> None:
        plan, space = self._consent_plan, self._consent_space
        self._consent = {}
        self._consent_plan = None
        if plan is None or choice == CANCEL:
            self._refresh()
            return
        self._begin(plan, choice, space)

    @Property("QVariantMap", notify=changed)
    def consent(self) -> dict[str, object]:
        return dict(self._consent) if self._consent else {"open": False}

    @Slot()
    def consentContinue(self) -> None:
        self._consent_choice(CONTINUE)

    @Slot()
    def consentOwnedOnly(self) -> None:
        if self._consent_plan is not None and self._consent_plan.rest:
            self._consent_choice(OWNED_ONLY)

    @Slot()
    def consentCancel(self) -> None:
        self._consent_choice(CANCEL)

    @Slot()
    def stop(self) -> None:
        self.flow.stop()

    @Slot()
    def dismissDone(self) -> None:
        self._done_dismissed = True
        self.flow.queue.entries = []
        self._refresh()

    # -- onboarding & sign-in ----------------------------------------------------

    @Slot()
    def onboardingStart(self) -> None:
        self.onboarding.started = True
        self._refresh()

    @Slot()
    def onboardingLater(self) -> None:
        self.onboarding.apple_id_skipped = True
        self._refresh()

    @Slot()
    def secondaryAction(self) -> None:
        """Second button next to the main one («Войти заново» on -128,
        «Как установить» when nothing can be returned without the component)."""

        if str(self._home.get("cta2", "")) == component.HOWTO_LINK:
            self.link(component.HOWTO_LINK)
            return
        if str(self._home.get("state", "")) == STATE_STORE_MISMATCH:
            self.flow.store_relogin_requested()
            self.openSignIn()

    @Slot()
    def openSignIn(self) -> None:
        self._signin_open = True
        self.signInRequested.emit()
        self._refresh()

    pickIpaRequested = Signal()
    #: «Как установить»: a local file (BUILD-ipatool.md / RUN-FROM-SOURCE.md) to open.
    openDocRequested = Signal(str)

    @Slot(str)
    def link(self, name: str) -> None:
        action = link_action(name)
        if action == "stop":
            self.stop()
        elif action == "ipa":
            self.pickIpaRequested.emit()  # QML FileDialog: only a file already on this computer
        elif action == "files":
            self.openFiles()
        elif action == "signin":
            src = self.source
            if src.signed_in and src.auth_phase == "in" and not src.relogin:
                self.openAccount()  # «Apple ID» when signed in: who, and «Выйти»
            else:
                self.openSignIn()
        elif action == "picker":
            self.openPicker()
        elif action == "find":
            self.finder.openWith("")
        elif action == "settings":
            self.openSettings()
        elif action == "howto":
            path = component.howto_path()
            if path is not None:
                self.openDocRequested.emit(str(path))
            else:
                self._component_details = True
                self._refresh()
        elif action == "details":
            self._component_details = not self._component_details
            self._refresh()

    # -- «Файлы IPA» (0.3.2's library, 4b sheet) --------------------------------------

    @Property("QVariantMap", notify=changed)
    def files(self) -> dict[str, object]:
        from apprestore_gui.ui4b.files import export_rows, files_view, library_rows

        note, busy = self.source.files_note()
        return files_view(
            open_=self._files_open, mode=self._files_mode,
            library=library_rows(self.source.library_files()),
            export=export_rows(self.source.export_apps(), self._files_chosen) if self._files_mode == "export" else [],
            note=note if self._files_open else "", busy=busy, connected=bool(self.source.connected),
        )

    @Slot()
    def openFiles(self) -> None:
        self._files_open, self._files_mode = True, "list"
        self._files_chosen = set()
        self.source.load_library()
        self._refresh()

    @Slot()
    def closeFiles(self) -> None:
        self._files_open = False
        self._files_chosen = set()
        self._refresh()

    @Slot()
    def filesExport(self) -> None:
        """«Выгрузить с устройства»: the installed apps on the phone, pick, save copies."""

        self._files_mode = "export"
        self._files_chosen = set()
        self.source.load_phone()
        self._refresh()

    @Slot()
    def filesBack(self) -> None:
        self._files_mode = "list"
        self._files_chosen = set()
        self._refresh()

    @Slot(str)
    def filesToggle(self, bundle_id: str) -> None:
        if bundle_id in self._files_chosen:
            self._files_chosen.discard(bundle_id)
        elif bundle_id:
            self._files_chosen.add(bundle_id)
        self._refresh()

    @Slot()
    def filesSave(self) -> None:
        if self._files_chosen:
            self.source.save_copies(sorted(self._files_chosen))
            self._files_chosen = set()
            self._files_mode = "list"
            self._refresh()

    @Slot()
    def filesPick(self) -> None:
        """«Выбрать на ПК»: the file dialog; the chosen file goes the usual IPA path."""

        self._files_open = False
        self._refresh()
        self.pickIpaRequested.emit()

    @Slot(str)
    def filesInstall(self, path: str) -> None:
        self._files_open = False
        self.installIpaFile(path)
        self._refresh()

    @Slot(str)
    def installIpaFile(self, url_or_path: str) -> None:
        """Install a local .ipa chosen by the user (QuickSession.installSaved)."""

        from pathlib import Path

        from PySide6.QtCore import QUrl

        text = str(url_or_path or "")
        path = QUrl(text).toLocalFile() if text.startswith("file:") else text
        if not path or not path.lower().endswith(".ipa") or self.flow.running:
            return
        item = RestoreItem(
            key=f"ipa:{path}", name=Path(path).stem, group=GROUP_REMOVED, action=ACTION_IPA, ipa_path=path
        )
        plan = self.flow.begin([item], self.source.space())
        if not plan.blocked:
            self._picker_open = False
            self._done_dismissed = False
        self._refresh()

    @Slot()
    def cancelLogin(self) -> None:
        """«Отмена»/Esc in the sheet: stop a running sign-in, then close the sheet."""

        if self.source.auth_phase in ("running", "need_code", "checking_code"):
            self.source.cancel_login()
        self.closeSignIn()

    @Slot()
    def closeSignIn(self) -> None:
        self._signin_open = False
        self._refresh()

    # -- «Найти» and «Настройки» (02-picker §6b) ------------------------------------

    @Property(QObject, constant=True)
    def find(self) -> QObject:
        return self.finder

    @Slot()
    def openSettings(self) -> None:
        self._settings_open = True
        self._refresh()

    @Slot()
    def closeSettings(self) -> None:
        self._settings_open = False
        self._refresh()

    @Slot(bool)
    def setArchiveSearch(self, on: bool) -> None:
        """Applied at once; off = no request to the Internet Archive at all."""

        if bool(on) == self._archive_search:
            return
        self._archive_search = bool(on)
        self.archiveSearchChanged.emit(self._archive_search)
        if self.finder.open and self.finder.query:
            self.finder.search(self.finder.query)
        self._refresh()

    @Slot()
    def checkUpdates(self) -> None:
        """The old window's check (updater.check_for_update) in a worker."""

        if self._update_busy:
            return
        self._update_busy = True
        self._update_text = UPDATE_BUSY
        check = self._update_check
        if check is None:
            from apprestore_gui import updater

            check = updater.check_for_update

        def work() -> None:
            try:
                info = check()
            except Exception:  # noqa: BLE001 - shown as «Не удалось проверить…»
                info = None
            self._updateChecked.emit(info)

        threading.Thread(target=work, daemon=True, name="ui4b-update").start()
        self._refresh()

    def _on_update_checked(self, info: object) -> None:
        self._update_busy = False
        self._update_text = update_status(info)
        self._refresh()
        if info is not None and getattr(info, "newer", False):
            self.updateAvailable.emit(info)

    @Property("QVariantMap", notify=changed)
    def settings(self) -> dict[str, object]:
        return settings_view(open_=self._settings_open, archive=self._archive_search,
                             update_text=self._update_text, update_busy=self._update_busy)

    # -- «Apple ID» sheet (04-auth §2a): who is signed in, «Выйти» -------------------

    @Slot()
    def openAccount(self) -> None:
        self._account_open = True
        self._signout_confirm = False
        self._refresh()

    @Slot()
    def closeAccount(self) -> None:
        self._account_open = False
        self._signout_confirm = False
        self._refresh()

    @Slot()
    def askSignOut(self) -> None:
        if self.flow.running:
            return  # inactive during installation
        self._signout_confirm = True
        self._refresh()

    @Slot()
    def cancelSignOut(self) -> None:
        self._signout_confirm = False
        self._refresh()

    @Slot()
    def confirmSignOut(self) -> None:
        if self.flow.running:
            return
        self._signout_confirm = False
        self._account_open = False
        self._signin_open = False
        self.flow.on_signed_out()
        reset_attempt_session()  # §1.14: one attempt per Apple ID session
        self.source.sign_out()
        self._refresh()

    @Property("QVariantMap", notify=changed)
    def account(self) -> dict[str, object]:
        return account_view(
            open_=self._account_open,
            confirm=self._signout_confirm,
            email=self.source.account_email,
            running=self.flow.running,
        )

    @Property("QVariantMap", notify=changed)
    def signIn(self) -> dict[str, object]:
        """Sign-in sheet: phase "out" (email + password) or "need_code" (2FA).

        After a successful sign-in the sheet closes and the home screen shows
        «Вернуть» again; nothing restarts by itself.
        """

        return signin_view(
            open_=self._signin_open,
            phase=self.source.auth_phase or "out",
            status=self.source.auth_status,
            email=self.source.account_email,
            relogin=bool(self.source.relogin or self.flow.needs_signin or self.flow._relogin_for_store is not None),
        )

    @Slot(str, str)
    def login(self, email: str, password: str) -> None:
        self.source.login(email, password)

    @Slot(str, result=str)
    def codeDigits(self, text: str) -> str:
        return code_digits(text)

    @Slot(result=str)
    def clipboardText(self) -> str:
        from PySide6.QtGui import QGuiApplication

        board = QGuiApplication.clipboard()
        return board.text() if board is not None else ""

    @Slot(str)
    def submitCode(self, code: str) -> None:
        self.source.submit_code(code)


WRONG_PASSWORD_PREFIX = "Apple не приняла пароль"


def signin_view(*, open_: bool, phase: str, status: str, email: str, relogin: bool) -> dict[str, object]:
    """Texts of the sign-in sheet (Ника, часть 4 §2). Pure: tested without Qt."""

    from apprestore_gui.auth_pty import CODE_HINT, WRONG_CODE_TEXT

    # auth-code: the prompt is up (need_code) or the code was sent and Apple is
    # answering (checking_code: button busy «Проверяем…», field locked)
    checking = phase == "checking_code"
    code = phase == "need_code" or checking
    wrong_code = status == WRONG_CODE_TEXT  # auth-code-wrong → the password form
    error = status if (status.startswith(WRONG_PASSWORD_PREFIX) or wrong_code) else ""
    if code:
        title = "Код подтверждения"
        sub = "Apple отправила код на ваши устройства: iPhone, iPad или Mac. Введите 6 цифр."
    elif relogin:
        title = "Войдите заново"
        sub = "Apple закрыла сессию. Так бывает раз в несколько недель, с аккаунтом всё в порядке."
    else:
        title = "Вход в Apple ID"
        sub = "Нужен для удалённых из App Store приложений: они скачиваются на <b>ваш</b> Apple ID."
    return {
        "open": open_,
        "phase": phase,
        "busy": phase == "running" or checking,
        "code": code,
        "status": "" if (error or code) else status,
        "error": error,
        "email": email,
        # in the re-login sheet the address is fixed: same account (Ника #5)
        "emailReadOnly": bool(relogin and email),
        "title": title,
        "sub": sub,
        "codeHint": CODE_HINT,
        # the same quiet hint under the field and under the auth-code-wrong error
        "hint": CODE_HINT if (code or wrong_code) else "",
        "codePlaceholder": "Код из 6 цифр",  # Windows: one plain field instead of cells
        "fine": "Пароль уходит только в Apple. Программа его не хранит; вход остаётся на этом компьютере, в связке ключей.",
        "go": ("Проверяем…" if checking else "Подтвердить") if code else "Войти",
    }


def account_view(*, open_: bool, confirm: bool, email: str, running: bool) -> dict[str, object]:
    """«Apple ID» sheet and the «Выйти из Apple ID?» confirmation (04-auth §2a).
    The address is shown in full (ElideMiddle in QML when it does not fit)."""

    return {
        "open": bool(open_),
        "title": "Apple ID",
        "close": "Закрыть",
        "label": "Вошли как",
        "email": email,
        "fine": "Пароль уходит только в Apple. Программа его не хранит; вход остаётся на этом компьютере, в связке ключей.",
        "signOut": "Выйти",
        "signOutEnabled": not running,
        "signOutHint": "Дождитесь конца установки" if running else "",
        "confirm": bool(confirm and open_ and not running),
        "confirmTitle": "Выйти из Apple ID?",
        "confirmText": "Приложения на iPhone останутся. Чтобы вернуть новые, нужно будет войти снова.",
        "confirmGo": "Выйти",
        "confirmCancel": "Отмена",
    }


def code_digits(text: str) -> str:
    """Pasted 2FA code: digits only, at most 6 («482 913», «482-913» → 482913)."""

    return "".join(ch for ch in str(text or "") if ch.isdigit())[:6]


def is_on(check: str) -> bool:
    return check == CHECK_ON
