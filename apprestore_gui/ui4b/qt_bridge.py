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
from typing import Any

from PySide6.QtCore import (
    Property,
    QAbstractListModel,
    QByteArray,
    QModelIndex,
    QObject,
    Qt,
    Signal,
    Slot,
)

from apprestore_gui.ui4b.catalog import GROUP_REGION, RestoreItem, build_items
from apprestore_gui.ui4b.flow import RestoreFlow
from apprestore_gui.ui4b.formatting import format_size
from apprestore_gui.ui4b.home import STATE_DONE, HomeInput, PhoneApp, home_view
from apprestore_gui.ui4b.onboarding import Onboarding
from apprestore_gui.ui4b.scan import ScanCounter
from apprestore_gui.ui4b.selection import CHECK_ON, Selection
from apprestore_gui.ui4b.space import UNKNOWN_SPACE, DeviceSpace, query_device_space

ROLES: tuple[str, ...] = (
    "kind",
    "key",
    "group",
    "title",
    "countText",
    "check",
    "action",
    "name",
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

    def items(self) -> list[RestoreItem]:
        return []

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


class SessionSource(SourceBase):
    """Live data from :class:`QuickSession`; installs go through its gated calls."""

    _missingReady = Signal(str, object)
    _spaceReady = Signal(str, object)

    def __init__(self, session: Any) -> None:
        super().__init__()
        self.session = session
        self._missing: list[Any] = []
        self._missing_udid = ""
        self._space = UNKNOWN_SPACE
        self._space_udid = ""
        session.changed.connect(self._on_session)
        session.purchasesChanged.connect(self.changed)
        session.installProgress.connect(self.progress)
        session.appRestored.connect(self.appRestored)
        session.restoreSettled.connect(self.restoreSettled)
        session.installSettled.connect(self.installSettled)
        session.copySettled.connect(self.copySettled)
        self._missingReady.connect(self._on_missing)
        self._spaceReady.connect(self._on_space)

    # -- reading ---------------------------------------------------------------

    def _on_session(self) -> None:
        s = self.session
        self.connected = bool(s.connected)
        self.loading = bool(s.loading)
        self.device_name = str(s.deviceName or "")
        self.noun = str(s.deviceNoun or "iPhone")
        self.signed_in = bool(s.signedIn)
        udid = s.current_udid()
        if self.connected and udid and not self.loading and udid != self._missing_udid:
            self._missing_udid = udid
            threading.Thread(target=self._load_missing, args=(udid,), daemon=True, name="ui4b-missing").start()
        if self.connected and udid and udid != self._space_udid:
            self._space_udid = udid
            threading.Thread(target=self._load_space, args=(udid,), daemon=True, name="ui4b-space").start()
        if not self.connected:
            self._missing_udid = ""
            self._space_udid = ""
            self._space = UNKNOWN_SPACE
        self.changed.emit()

    def _load_missing(self, udid: str) -> None:
        try:
            apps = self.session.service.missing(udid)
        except Exception:  # noqa: BLE001 - the list just stays without them
            apps = []
        self._missingReady.emit(udid, apps)

    def _on_missing(self, udid: str, apps: object) -> None:
        if udid == self.session.current_udid():
            self._missing = list(apps or [])  # type: ignore[call-overload]
            self.changed.emit()

    def _load_space(self, udid: str) -> None:
        self._spaceReady.emit(udid, query_device_space(self.session.service.core.tools, udid))

    def _on_space(self, udid: str, space: object) -> None:
        if udid == self.session.current_udid() and isinstance(space, DeviceSpace):
            self._space = space
            self.changed.emit()

    def items(self) -> list[RestoreItem]:
        return build_items(self.session.offloaded_snapshot(), self._missing)

    def space(self) -> DeviceSpace:
        return self._space

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


class Restore4b(QObject):
    """Everything the 4b QML reads. One ``changed`` signal; QML re-reads properties."""

    changed = Signal()
    storeSearchRequested = Signal(str)
    _spaceChecked = Signal(object)

    def __init__(self, source: SourceBase, *, onboarded: bool = True, mark_color: str = "#f6ebe4") -> None:
        super().__init__()
        self.source = source
        self.selection = Selection(mark_color=mark_color)
        self.picker = PickerModel(self)
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
        source.changed.connect(self._on_source)
        source.progress.connect(self.flow.on_progress)
        source.appRestored.connect(self.flow.on_app_restored)
        source.restoreSettled.connect(self.flow.on_restore_settled)
        source.installSettled.connect(self.flow.on_install_settled)
        source.copySettled.connect(self._on_copy_settled)
        self._spaceChecked.connect(self._start_after_space)
        self._on_source()

    # -- updates ---------------------------------------------------------------

    def _on_source(self) -> None:
        self.selection.set_items(self.source.items())
        self.selection.set_space(self.source.space())
        checked, total, done = self.source.scan()
        self.scan.update(checked, total)
        if done:
            self.scan.finish()
        self._refresh()

    def _refresh(self) -> None:
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
    def home(self) -> dict[str, object]:
        return dict(self._home)

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
        return [
            {"count": offloaded, "label": "сгруженных"},
            {"count": removed, "label": "удалённых из App Store"},
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

    @Slot(str)
    def searchStore(self, query: str) -> None:
        """«Искать в App Store и архиве»: handed to the window (not wired yet)."""

        self.storeSearchRequested.emit(query)

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
        return {
            "total": plan.total_text(),
            "free": plan.free_text(),
            "ratio": plan.ratio,
            "over": plan.verdict == "over",
            "known": plan.free_bytes is not None,
            "warnBold": bold,
            "warnText": rest,
            "go": f"Вернуть {plan.count}" if plan.count else "Вернуть",
            "goEnabled": not plan.blocked and not self._checking_space and not self.flow.running,
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
        if group == GROUP_REGION:
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

    # -- restore -----------------------------------------------------------------

    @Slot()
    def primaryAction(self) -> None:
        """The big button of the home screen."""

        state = str(self._home.get("state", ""))
        if state == STATE_DONE:
            self.dismissDone()
        elif state in ("many",):
            self.openPicker()
        elif state in ("missing", "region"):
            self.selection.set_rail("all")
            self.selection.set_query("")
            for item in self.selection.items:
                self.selection.set_selected(item.key, item.selectable)
            self.restoreSelected()

    @Slot()
    def restoreSelected(self) -> None:
        """Fresh free-space check on the device, then the queue (or a warning)."""

        if self._checking_space or self.flow.running:
            return
        self._checking_space = True
        self._refresh()
        threading.Thread(target=self._check_space, daemon=True, name="ui4b-space-check").start()

    def _check_space(self) -> None:
        try:
            space = self.source.fresh_space()
        except Exception:  # noqa: BLE001
            space = UNKNOWN_SPACE
        self._spaceChecked.emit(space)

    def _start_after_space(self, space: object) -> None:
        self._checking_space = False
        fresh = space if isinstance(space, DeviceSpace) else UNKNOWN_SPACE
        self.selection.set_space(fresh)
        plan = self.flow.begin(self.selection.selected_items(), fresh)
        if not plan.blocked:
            self._picker_open = False
            self._done_dismissed = False
        self._refresh()

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

    @Slot(str, str)
    def login(self, email: str, password: str) -> None:
        self.source.login(email, password)

    @Slot(str)
    def submitCode(self, code: str) -> None:
        self.source.submit_code(code)


def is_on(check: str) -> bool:
    return check == CHECK_ON
