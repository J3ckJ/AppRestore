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
    "signin", "relogin", "region", "empty", "onboarding-1", "onboarding-2", "onboarding-3", "onboarding-4",
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
        for name in ("changed", "sessionChanged", "purchasesChanged", "installProgress", "appRestored",
                     "restoreSettled", "installSettled", "copySettled"):
            setattr(self, name, FakeSignal())
        self.calls: list[tuple[str, object]] = []
        self.connected = False
        self.loading = False
        self.deviceName = ""
        self.deviceNoun = "iPhone"
        self.signedIn = True
        self.authPhase = "in"
        self.sessionRelogin = False
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


def test_relogin_returns_home_without_resuming(qapp) -> None:
    source = FakeSource("missing")
    controller = Restore4b(source)
    asked: list[int] = []
    controller.signInRequested.connect(lambda: asked.append(1))
    controller.primaryAction()
    wait(qapp, lambda: bool(source.calls))
    first = controller.flow.queue.entries[0].item.store_id
    source.installSettled.emit(first, False, "Сессия Apple ID истекла. Войдите заново.")
    assert controller.home["state"] == "signin"
    assert controller.home["cta"] == "Войти заново"
    controller.primaryAction()
    assert asked == [1]
    # Signed in again (QuickSession: authPhase running → in).
    source.auth_phase = "running"
    source.changed.emit()
    source.auth_phase = "in"
    source.changed.emit()
    assert controller.home["state"] == "missing"
    assert controller.home["cta"] == "Вернуть все 4"
    assert [c[0] for c in source.calls] == ["install_store"]


def test_session_source_reads_relogin_and_auth_phase(qapp) -> None:
    session = FakeSession()
    source = SessionSource(session)
    session.sessionRelogin = True
    session.authPhase = "out"
    session.sessionChanged.emit()
    assert source.relogin and source.auth_phase == "out"


def test_signin_sheet_with_2fa_loads_and_closes_without_resuming(qapp) -> None:
    source = FakeSource("relogin")
    controller = Restore4b(source)
    assert controller.home["state"] == "signin"
    controller.primaryAction()
    assert controller.signIn["open"] and not controller.signIn["code"]
    engine, icons, warnings = open_window(qapp, controller)
    window = engine.rootObjects()[0]
    assert window.findChild(QQuickItem, "signInSheet") is not None
    controller.login("marina@example.com", "secret")
    qapp.processEvents()
    assert controller.signIn["code"]
    controller.submitCode("123456")
    for _ in range(3):
        qapp.processEvents()
    assert not controller.signIn["open"]
    assert controller.home["state"] == "missing"
    assert [c[0] for c in source.calls] == ["login", "submit_code"]
    assert "secret" not in repr(source.calls) and "123456" not in repr(source.calls)
    window.close()
    del window
    del engine
    qapp.processEvents()
    assert warnings == []



def test_store_mismatch_screen_buttons(qapp) -> None:
    source = FakeSource("missing")
    source.account_email = "marina@example.com"
    controller = Restore4b(source)
    controller.primaryAction()
    wait(qapp, lambda: bool(source.calls))
    first = controller.flow.queue.entries[0].item.store_id
    source.installSettled.emit(first, False, "Account Not In This Store")
    assert controller.home["state"] == "store_mismatch"
    controller.secondaryAction()  # «Войти заново» opens the sign-in sheet
    assert controller.signIn["open"]
    controller.login("marina@example.com", "x")
    controller.submitCode("111111")
    assert controller.home["state"] == "missing"
    assert [c[0] for c in source.calls].count("install_store") == 1
    controller.primaryAction()
    wait(qapp, lambda: [c[0] for c in source.calls].count("install_store") == 2)
    first = controller.flow.queue.entries[0].item.store_id
    source.installSettled.emit(first, False, "Account Not In This Store")
    assert controller.home["state"] == "store_mismatch" and controller.home["cta2"] == ""
    controller.primaryAction()  # «На главный»
    assert controller.home["state"] == "missing"


# -- согласие на бесплатные лицензии ---------------------------------------------------


def _record(journal, n: int, prefix: str = "9") -> None:
    from apprestore_core.license_guard import record_acquire

    for i in range(n):
        record_acquire(f"{prefix}{i:05d}", f"com.example.app{prefix}{i}", "us", journal_path=journal, price=0)


def _open_consent(qapp, controller, source):
    controller.primaryAction()
    wait(qapp, lambda: bool(controller.consent.get("open")))
    return controller.consent


def test_consent_numbers_are_read_counts_every_time(qapp, tmp_path) -> None:
    from apprestore_core.license_guard import read_counts

    journal = tmp_path / "licenses_acquired.jsonl"
    source = FakeSource("consent")
    source.journal = journal
    _record(journal, 2)
    controller = Restore4b(source)
    consent = _open_consent(qapp, controller, source)
    today, total = read_counts(journal)
    assert (consent["usedToday"], consent["usedTotal"]) == (today, total) == (2, 2)
    assert consent["limit"] == f"Осталось на сегодня: {5 - today} из 5, всего: {15 - total} из 15."
    assert consent["k"] == 2
    assert not [c for c in source.calls if c[0] == "install_store"], "nothing starts before a choice"
    controller.consentCancel()
    assert not controller.consent["open"] and not controller.flow.running
    # another process writes to the journal between two shows
    _record(journal, 2, prefix="8")
    consent = _open_consent(qapp, controller, source)
    today, total = read_counts(journal)
    assert (consent["usedToday"], consent["usedTotal"]) == (today, total) == (4, 4)
    assert consent["limit"] == "Осталось на сегодня: 1 из 5, всего: 11 из 15."
    engine, icons, warnings = open_window(qapp, controller)
    window = engine.rootObjects()[0]
    sheet = window.findChild(QQuickItem, "consentLimit")
    assert sheet is not None and sheet.property("text") == consent["limit"]
    assert warnings == []
    del window, engine, icons


def test_consent_continue_and_owned_only(qapp, tmp_path) -> None:
    source = FakeSource("consent")
    source.journal = tmp_path / "j.jsonl"
    controller = Restore4b(source)
    _open_consent(qapp, controller, source)
    controller.consentOwnedOnly()
    started = [e.item.label for e in controller.flow.queue.entries]
    assert "Сбер" not in started and "Т-Банк" not in started and started
    assert controller.flow.queue.skipped["no_license"] == ["Сбер", "Т-Банк"]
    controller.stop()
    controller.dismissDone()
    source2 = FakeSource("consent")
    source2.journal = tmp_path / "j.jsonl"
    c2 = Restore4b(source2)
    _open_consent(qapp, c2, source2)
    c2.consentContinue()
    assert [e.item.label for e in c2.flow.queue.entries][-2:] == ["Сбер", "Т-Банк"]


def test_no_consent_when_everything_is_on_the_account(qapp) -> None:
    source = FakeSource("missing")
    controller = Restore4b(source)
    controller.primaryAction()
    wait(qapp, lambda: bool(controller.flow.queue.entries))
    assert not controller.consent["open"] and controller.flow.running


# -- «Что вернуть» без сети -----------------------------------------------------------------


def test_picker_offline_marks_unverified_and_disables_go(qapp) -> None:
    source = FakeSource("picker-offline")
    controller = Restore4b(source)
    controller.openPicker()
    rows = [r for r in controller.selection.rows() if r["kind"] == "app"]
    assert rows and all(r["note"] == "не проверено" and r["unverified"] for r in rows)
    controller.toggle(rows[0]["key"])  # marking still works
    assert controller.footer["goEnabled"] is False
    assert controller.footer["warnText"] == "Нет интернета, вернуть можно, когда он появится."
    controller.restoreSelected()
    assert not controller.flow.running and not source.calls
    source.go_online()
    assert [c[0] for c in source.calls] == ["recheck"]  # read only, nothing bought or installed
    assert controller.footer["goEnabled"] is True


def test_session_source_rechecks_read_only_when_network_returns(qapp) -> None:
    from apprestore_gui.ui4b.qt_bridge import SessionSource

    session = FakeSession()
    session.sessionState = "offline"
    src = SessionSource(session)
    session.changed.emit()
    assert src.online is False
    session.sessionState = "ok"
    session.changed.emit()
    assert src.online is True
    names = [c[0] for c in session.calls]
    assert names.count("loadPurchases") == 1
    assert not any(n.startswith(("install", "purchase", "restore")) for n in names)
