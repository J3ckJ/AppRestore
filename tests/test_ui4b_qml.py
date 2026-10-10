"""4b QML loads without errors or warnings; the Qt bridge forwards to the logic."""

from __future__ import annotations

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6.QtQuick")

from PySide6.QtCore import QModelIndex
from PySide6.QtQml import QQmlApplicationEngine
from PySide6.QtQuick import QQuickItem
from PySide6.QtWidgets import QApplication

from apprestore_gui.ui4b.fake_data import FakeIconBook, FakeSource
from apprestore_gui.ui4b.qt_bridge import PickerModel, Restore4b, SessionSource
from apprestore_gui.ui4b.space import DeviceSpace
from apprestore_gui.ui4b.window import load, qml_path

SCENARIOS = (
    "missing", "many", "picker", "picker-search", "installing", "done", "disconnected",
    "signin", "region", "empty", "onboarding-1", "onboarding-2", "onboarding-3", "onboarding-4",
)


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


def open_window(qapp, controller):
    engine = QQmlApplicationEngine()
    warnings: list[str] = []
    engine.warnings.connect(lambda ws: warnings.extend(w.toString() for w in ws))
    icons = FakeIconBook()
    assert load(engine, controller, icons), warnings
    for _ in range(3):
        qapp.processEvents()
    return engine, icons, warnings


@pytest.mark.parametrize("scenario", SCENARIOS)
def test_main_qml_loads_without_warnings(qapp, scenario: str) -> None:
    source = FakeSource(scenario)
    controller = Restore4b(source, onboarded=not scenario.startswith("onboarding"))
    if scenario.startswith("picker"):
        controller.openPicker()
        if scenario == "picker-search":
            controller.setQuery("google")
    engine, icons, warnings = open_window(qapp, controller)
    window = engine.rootObjects()[0]
    assert window.objectName() == "ui4bWindow"
    if scenario.startswith("picker"):
        view = window.findChild(QQuickItem, "pickerList")
        assert view is not None and view.property("count") > 0
        view.setProperty("contentY", 600.0)
        for _ in range(3):
            qapp.processEvents()
    window.close()
    del window
    del engine
    qapp.processEvents()
    assert warnings == []
    assert qml_path().is_file()


def test_picker_model_keeps_rows_on_toggle(qapp) -> None:
    model = PickerModel()
    resets: list[int] = []
    changed: list[int] = []
    model.modelReset.connect(lambda: resets.append(1))
    model.dataChanged.connect(lambda a, b, *r: changed.append(a.row()))
    model.set_rows([{"key": "a", "selected": False}, {"key": "b", "selected": False}])
    model.set_rows([{"key": "a", "selected": False}, {"key": "b", "selected": True}])
    assert resets == [1] and changed == [1]
    roles = {bytes(v).decode(): k for k, v in model.roleNames().items()}
    assert model.data(model.index(1), roles["selected"]) is True
    assert model.rowCount(QModelIndex()) == 2


def wait(qapp, predicate, rounds: int = 200) -> None:
    import time

    for _ in range(rounds):
        qapp.processEvents()
        if predicate():
            return
        time.sleep(0.01)
    raise AssertionError("timeout")


def test_restore_checks_space_first_and_blocks_when_over(qapp) -> None:
    source = FakeSource("picker")
    controller = Restore4b(source)
    controller.openPicker()
    controller.selectAll()  # 516 apps, far over 6,8 GB
    assert controller.footer["over"] and not controller.footer["goEnabled"]
    controller.restoreSelected()
    wait(qapp, lambda: not controller._checking_space)
    assert source.calls == []
    assert controller.pickerOpen


def test_restore_runs_store_apps_through_session_gate(qapp) -> None:
    source = FakeSource("missing")
    controller = Restore4b(source)
    assert controller.home["cta"] == "Вернуть все 4"
    controller.primaryAction()
    wait(qapp, lambda: bool(source.calls))
    assert source.calls[0][0] == "install_store"
    assert controller.home["state"] == "installing"
    first = controller.flow.queue.entries[0].item.store_id
    source.installSettled.emit(first, True, "")
    assert source.calls[1][0] == "install_store"


def test_unknown_free_space_does_not_block(qapp) -> None:
    source = FakeSource("missing")
    source._space = DeviceSpace()
    controller = Restore4b(source)
    controller.openPicker()
    assert controller.footer["free"] == "свободное место не удалось проверить"
    assert controller.footer["goEnabled"]
    assert controller.pickerSubtitle.endswith("свободное место не удалось проверить")


class FakeSignal:
    def __init__(self) -> None:
        self.slots = []

    def connect(self, slot) -> None:
        self.slots.append(slot)

    def emit(self, *args) -> None:
        for slot in self.slots:
            slot(*args)


class FakeSession:
    """Only what SessionSource touches; records the calls."""

    def __init__(self) -> None:
        for name in ("changed", "purchasesChanged", "installProgress", "appRestored",
                     "restoreSettled", "installSettled", "copySettled"):
            setattr(self, name, FakeSignal())
        self.calls: list[tuple[str, object]] = []
        self.connected = False
        self.loading = False
        self.deviceName = ""
        self.deviceNoun = "iPhone"
        self.signedIn = True
        self.purchases: list[dict] = []
        self.purchasesBusy = False
        self.purchasesProgress = ""

    def current_udid(self) -> str:
        return ""

    def offloaded_snapshot(self):
        return []

    def restore(self, keys):
        self.calls.append(("restore", list(keys)))

    def installStore(self, store_id):
        self.calls.append(("installStore", store_id))

    def installSaved(self, path):
        self.calls.append(("installSaved", path))

    def loadPurchases(self):
        self.calls.append(("loadPurchases", None))


def test_session_source_uses_gated_quick_session_calls(qapp) -> None:
    session = FakeSession()
    source = SessionSource(session)
    source.install_store("492224193")
    source.restore_offloaded(["com.a"])
    source.install_ipa("/tmp/a.ipa")
    source.start_scan()
    assert session.calls == [
        ("installStore", "492224193"),
        ("restore", ["com.a"]),
        ("installSaved", "/tmp/a.ipa"),
        ("loadPurchases", None),
    ]


def test_old_quick_window_still_available() -> None:
    from apprestore_gui import app as app_module
    from apprestore_gui.quick_window import qml_path as legacy_qml

    assert legacy_qml().is_file()
    import inspect

    text = inspect.getsource(app_module.main)
    assert '"quick-legacy"' in text and "ui4b.window" in text
