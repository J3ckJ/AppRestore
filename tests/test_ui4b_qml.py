"""4b QML loads without errors or warnings; the Qt bridge forwards to the logic."""

from __future__ import annotations

import os
from types import SimpleNamespace

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
    "unpatched",
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


def test_long_home_content_remains_reachable_at_minimum_height(qapp) -> None:
    source = FakeSource("region")
    controller = Restore4b(source, onboarded=True)
    engine, icons, warnings = open_window(qapp, controller)
    window = engine.rootObjects()[0]
    window.setHeight(window.minimumHeight())
    for _ in range(3):
        qapp.processEvents()
    scroll = window.findChild(QQuickItem, "homeScroll")
    hero = window.findChild(QQuickItem, "homeHero")
    assert scroll is not None and hero is not None
    assert scroll.property("clip")
    assert scroll.property("contentHeight") == hero.height()
    assert scroll.property("contentHeight") > scroll.height()
    bottom = scroll.property("contentHeight") - scroll.height()
    scroll.setProperty("contentY", bottom)
    qapp.processEvents()
    assert scroll.property("contentY") > 0
    source.changed.emit()  # same screen refresh does not reset user's position
    qapp.processEvents()
    assert scroll.property("contentY") == bottom
    window.close()
    assert warnings == []


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


def begin_picker_restore(controller) -> None:
    """Home CTA opens the picker; the second explicit action starts the chosen rows."""

    controller.primaryAction()
    assert controller.pickerOpen
    controller.restoreSelected()


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
    assert controller.home["cta"] == "Выбрать и вернуть"
    begin_picker_restore(controller)
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
                     "restoreSettled", "installSettled", "copySettled", "keychainPrompt"):
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

    def unlock(self, passphrase):
        self.calls.append(("unlock", len(passphrase)))


def test_session_source_uses_gated_quick_session_calls(qapp) -> None:
    session = FakeSession()
    source = SessionSource(session)
    source.install_store("492224193")
    source.restore_offloaded(["com.a"])
    source.install_ipa("/tmp/a.ipa")
    source.start_scan()
    source.unlock("local-pass")
    assert session.calls == [
        ("installStore", "492224193"),
        ("restore", ["com.a"]),
        ("installSaved", "/tmp/a.ipa"),
        ("loadPurchases", None),
        ("unlock", 10),
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
    begin_picker_restore(controller)
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
    assert controller.home["cta"] == "Выбрать и вернуть"
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
    begin_picker_restore(controller)
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
    begin_picker_restore(controller)
    wait(qapp, lambda: [c[0] for c in source.calls].count("install_store") == 2)
    first = controller.flow.queue.entries[0].item.store_id
    source.installSettled.emit(first, False, "Account Not In This Store")
    assert controller.home["state"] == "store_mismatch" and controller.home["cta2"] == ""
    controller.primaryAction()  # «На главный»
    assert controller.home["state"] == "missing"


# -- согласие на лицензии ---------------------------------------------------


def test_license_run_starts_without_the_sheet(qapp, tmp_path) -> None:
    source = FakeSource("consent")
    source.journal = tmp_path / "licenses_acquired.jsonl"
    controller = Restore4b(source)
    begin_picker_restore(controller)
    wait(qapp, lambda: bool(controller.flow.queue.entries))
    assert not controller.consent.get("open")
    labels = [entry.item.label for entry in controller.flow.queue.entries]
    assert labels[-2:] == ["Сбер", "Т-Банк"]
    engine, icons, warnings = open_window(qapp, controller)
    window = engine.rootObjects()[0]
    assert window.findChild(QQuickItem, "consentSheet") is None
    assert warnings == []
    del window, engine, icons


def test_no_consent_when_everything_is_on_the_account(qapp) -> None:
    source = FakeSource("missing")
    controller = Restore4b(source)
    begin_picker_restore(controller)
    wait(qapp, lambda: bool(controller.flow.queue.entries))
    assert not controller.consent["open"] and controller.flow.running


# -- «Что вернуть» без сети -----------------------------------------------------------------


def test_picker_offline_folds_groups_and_allows_only_offloaded(qapp) -> None:
    source = FakeSource("picker-offline")
    controller = Restore4b(source)
    controller.openPicker()
    rows = controller.selection.rows()
    groups = [r["group"] for r in rows if r["kind"] == "header"]
    assert groups == ["offloaded", "nophone"]  # offloaded first; removed + region folded
    nophone = [r for r in rows if r["kind"] == "app" and r["group"] == "nophone"]
    assert nophone and all(r["note"] == "нужен интернет" and r["unverified"] and not r["selectable"] for r in nophone)
    rail = [r["key"] for r in controller.selection.rail_rows()]
    assert "region" not in rail and "removed" not in rail and "nophone" in rail
    assert controller.footer["goEnabled"] is False  # store apps were marked, none can go offline
    assert controller.footer["warnText"] == "Нет интернета: сейчас можно вернуть только сгруженные."
    controller.toggle(nophone[0]["key"])  # not selectable: nothing happens
    off = next(r for r in rows if r["kind"] == "app" and r["group"] == "offloaded")
    controller.toggle(off["key"])
    assert controller.footer["goEnabled"] is True
    controller.restoreSelected()
    wait(qapp, lambda: controller.flow.running)
    assert [e.item.key for e in controller.flow.queue.entries] == [off["key"]]
    assert [c[0] for c in source.calls] == ["restore_offloaded"] and source.lookups == []
    controller.stop()
    controller.dismissDone()
    source.go_online()
    assert [c[0] for c in source.calls][-1] == "recheck"  # read only
    assert not any(c[0] == "install_store" for c in source.calls)
    groups = [r["group"] for r in controller.selection.rows() if r["kind"] == "header"]
    assert "removed" in groups and "nophone" not in groups
    # marks of store apps come back with the network
    assert any(i.group == "removed" for i in controller.selection.selected_items())


def test_session_source_rechecks_read_only_when_network_returns(qapp) -> None:
    from apprestore_gui.ui4b.qt_bridge import SessionSource

    import time

    session = FakeSession()
    session.sessionState = "offline"
    src = SessionSource(session)
    src._probe_hosts = lambda: False  # no host answers either: really offline
    session.changed.emit()
    end = time.monotonic() + 5
    while src._hosts_busy and time.monotonic() < end:
        qapp.processEvents()
        time.sleep(0.01)
    qapp.processEvents()
    assert src.online is False
    session.sessionState = "ok"
    session.changed.emit()
    assert src.online is True
    names = [c[0] for c in session.calls]
    assert names.count("loadPurchases") == 1
    assert not any(n.startswith(("install", "purchase", "restore")) for n in names)


# -- вход (часть 4) ---------------------------------------------------------------------


def test_signin_view_states() -> None:
    from apprestore_gui.auth_pty import WRONG_CODE_TEXT
    from apprestore_gui.ui4b.qt_bridge import code_digits, signin_view

    v = signin_view(open_=True, phase="out", status="", email="", relogin=False)
    assert v["title"] == "Вход в Apple ID" and not v["emailReadOnly"]
    r = signin_view(open_=True, phase="out", status="", email="m@example.com", relogin=True)
    assert r["title"] == "Войдите заново" and r["emailReadOnly"] is True
    w = signin_view(open_=True, phase="out", status=WRONG_CODE_TEXT, email="m@example.com", relogin=False)
    assert w["error"] == WRONG_CODE_TEXT and not w["code"] and w["go"] == "Войти"
    c = signin_view(open_=True, phase="need_code", status="", email="m", relogin=False)
    assert c["code"] and c["codeHint"].startswith("Код не пришёл? На iPhone") and "снова" not in c["go"]
    k = signin_view(open_=True, phase="locked", status="", email="m", relogin=False, keychain=True)
    assert k["keychain"] and k["title"] == "Открыть связку ключей" and k["go"] == "Открыть"
    assert "не пароль Apple ID" in k["sub"] and "stdin" in k["fine"]
    assert code_digits("482 913") == code_digits("482-913") == "482913"
    assert code_digits("1234567") == "123456"


def test_keychain_prompt_opens_distinct_password_form_and_forwards_only_to_unlock(qapp) -> None:
    source = FakeSource("signin")
    source.auth_phase = "locked"
    received: list[int] = []
    source.unlock = lambda value: received.append(len(value))  # type: ignore[method-assign]
    controller = Restore4b(source)
    source.keychainPrompt.emit("person@example.com")
    assert controller.signIn["open"] and controller.signIn["keychain"]
    assert controller.signIn["title"] == "Открыть связку ключей"

    engine, icons, warnings = open_window(qapp, controller)
    window = engine.rootObjects()[0]
    password = window.findChild(QQuickItem, "signInPassword")
    email = window.findChild(QQuickItem, "signInEmail")
    assert password is not None and password.property("visible") is True
    assert email is not None and email.property("visible") is False
    controller.unlock("separate-local-password")
    assert received == [23]
    assert "separate-local-password" not in repr(controller.signIn)
    assert warnings == []
    window.close()
    del window, engine, icons


def test_cancel_login_stops_running_signin(qapp) -> None:
    source = FakeSource("signin")
    stopped = []
    source.cancel_login = lambda: stopped.append(True)  # type: ignore[method-assign]
    controller = Restore4b(source)
    controller.openSignIn()
    source.auth_phase = "running"
    controller.cancelLogin()
    assert stopped == [True] and not controller.signIn["open"]


def test_signin_sheet_relogin_email_is_read_only(qapp) -> None:
    source = FakeSource("relogin")
    source.account_email = "m@example.com"
    controller = Restore4b(source)
    controller.openSignIn()
    engine, icons, warnings = open_window(qapp, controller)
    window = engine.rootObjects()[0]
    field = window.findChild(QQuickItem, "signInEmail")
    assert field is not None and field.property("readOnly") is True
    assert warnings == []
    del window, engine, icons


def test_session_source_phone_apps_from_pymobiledevice3_rows(qapp) -> None:
    session = FakeSession()
    session.phoneApps = [{"name": "Telegram", "storeId": "686449807", "bundleId": "ph.telegra.Telegraph"}]
    session.connected = True
    session.current_udid = lambda: "UDID-1"  # type: ignore[method-assign]
    session.loadPhone = lambda: session.calls.append(("loadPhone", None))  # type: ignore[attr-defined]
    session.service = type("S", (), {"missing": lambda self, udid: [], "core": None})()
    src = SessionSource(session)
    session.changed.emit()
    assert ("loadPhone", None) in session.calls
    apps = src.phone_apps()
    assert apps[0].name == "Telegram" and apps[0].store_id == "686449807"


def test_ipa_link_installs_only_a_local_file(qapp) -> None:
    source = FakeSource("missing")
    controller = Restore4b(source)
    asked = []
    controller.pickIpaRequested.connect(lambda: asked.append(True))
    controller.link("Файлы IPA")  # the sheet (Eugene 10.10); «Выбрать на ПК» = the file dialog
    assert controller.files["open"] and asked == []
    controller.filesPick()
    assert asked == [True]
    controller.installIpaFile("file:///tmp/Example.ipa")
    assert ("install_ipa", "/tmp/Example.ipa") in source.calls
    controller.link("Журнал")
    assert controller.journal["open"]
    controller.closeJournal()
    assert not controller.journal["open"]


def test_second_phone_and_saved_account_are_on_the_screen(qapp) -> None:
    source = FakeSource("missing")
    source.devices = lambda: [  # type: ignore[method-assign]
        {"udid": "home", "name": "Домашний", "account": "one@example.com", "active": True, "noun": "iPhone"},
        {"udid": "work", "name": "Рабочий", "account": "two@example.com", "active": False, "noun": "iPhone"},
    ]
    source.accounts = lambda: [  # type: ignore[method-assign]
        {"email": "one@example.com", "devices": "Домашний", "active": True, "saved": True},
        {"email": "two@example.com", "devices": "Рабочий", "active": False, "saved": True},
    ]
    seen: list[tuple[str, str]] = []
    source.select_device = lambda udid: seen.append(("device", udid))  # type: ignore[method-assign]
    source.use_account = lambda email: seen.append(("account", email))  # type: ignore[method-assign]
    controller = Restore4b(source)
    assert controller.home["pill"] == "Домашний"
    assert len(controller.devices) == 2
    controller.selectDevice("work")
    controller.openAccount()
    assert controller.account["others"][0]["email"] == "two@example.com"
    assert controller.account["devices"] == "Домашний"
    controller.useAccount("two@example.com")
    source.loginPrompt.emit("two@example.com")
    assert ("device", "work") in seen and ("account", "two@example.com") in seen
    assert controller.signIn["open"] and controller.signIn["email"] == "two@example.com"
    assert controller.signIn["keychain"] is False and controller.account["open"] is False
    controller.closeSignIn()
    engine, _icons, warnings = open_window(qapp, controller)
    controller.openAccount()
    controller.link("Журнал")
    for _ in range(4):
        qapp.processEvents()
    window = engine.rootObjects()[0]
    assert window.findChild(QQuickItem, "accountSheet") is not None
    assert window.findChild(QQuickItem, "journalSheet") is not None
    assert warnings == []
    window.close()


# -- смоук Лены, блокирующие пункты, на моках гейта ---------------------------------------------


def _gated_source(tmp_path, scenario="consent", paid=()):
    """FakeSource whose install_store goes through the real license_gate (fake ipatool)."""

    from apprestore_core.license_gate import run_with_free_license

    journal = tmp_path / "licenses_acquired.jsonl"
    source = FakeSource(scenario)
    source.journal = journal
    for sid in paid:
        source.prices[sid] = 2.99
    owned = set(source.owned or ())
    bought: list[str] = []
    notes: list[str] = []

    class Tools:
        def license_preflight(self):
            from apprestore_core.license_gate import NEEDS_PATCHED_IPATOOL_TEXT

            return NEEDS_PATCHED_IPATOOL_TEXT if source.missing_patches() else None

        def account_country(self):
            return "us"

        def purchase_license(self, store_id, grant=None):
            bought.append(store_id)
            owned.add(store_id)

    def attempt(sid):
        def run():
            if sid not in owned:
                raise RuntimeError("license is required")
            return "ok"
        return run

    def install_store(sid):
        source.calls.append(("install_store", sid))
        try:
            run_with_free_license(
                sid, attempt(sid), tools=Tools(), journal=journal, notify=lambda t: (notes.append(t), source.progress.emit(-1, t)),
                lookup=lambda s, c: {"price": source.prices.get(s, 9.99), "bundleId": "b", "country": c[0]},
            )
        except Exception as exc:  # noqa: BLE001
            source.installSettled.emit(sid, False, str(exc))
            return
        source.installSettled.emit(sid, True, "")

    source.install_store = install_store  # type: ignore[method-assign]
    return source, journal, bought, notes


def _run_restore(qapp, tmp_path):
    from apprestore_core.license_guard import read_counts

    source, journal, bought, notes = _gated_source(tmp_path)
    controller = Restore4b(source)
    before = read_counts(journal)
    begin_picker_restore(controller)
    wait(qapp, lambda: controller.flow.queue.finished)
    return controller, source, journal, bought, notes, before, read_counts(journal)


def test_smoke13_sheet_does_not_block_the_free_license(qapp, tmp_path) -> None:
    controller, source, journal, bought, notes, before, after = _run_restore(qapp, tmp_path)
    assert not controller.consent.get("open")
    assert len(bought) == 2 and after[0] - before[0] == 2


def test_smoke12_17_continue_notice_then_plus_k_in_read_counts(qapp, tmp_path) -> None:
    import json

    controller, source, journal, bought, notes, before, after = _run_restore(qapp, tmp_path)
    k = 2
    assert len(bought) == k and notes.count("Приложение будет добавлено на ваш Apple ID, если Apple его выдаст.") == k
    assert (after[0] - before[0], after[1] - before[1]) == (k, k)
    statuses = [json.loads(line)["status"] for line in journal.read_text(encoding="utf-8").splitlines()]
    assert statuses == ["acquired"] * k  # append-only, nothing extra


def test_smoke14_paid_never_enters_k_or_purchase(qapp, tmp_path) -> None:
    from apprestore_gui.ui4b.fake_data import removed_items

    paid_id = removed_items()[0].store_id  # Сбер: not on the account, price 2.99
    source, journal, bought, notes = _gated_source(tmp_path, paid=(paid_id,))
    controller = Restore4b(source)
    begin_picker_restore(controller)
    wait(qapp, lambda: controller.flow.queue.finished)
    assert not controller.consent.get("open")
    assert paid_id not in bought and ("install_store", paid_id) not in source.calls


def test_smoke12_notice_visible_in_queue(qapp, tmp_path) -> None:
    source = FakeSource("missing")
    controller = Restore4b(source)
    begin_picker_restore(controller)
    wait(qapp, lambda: controller.flow.running)
    source.progress.emit(-1, "Приложение будет добавлено на ваш Apple ID, если Apple его выдаст.")
    rows = controller.home["queue"]
    assert any("Приложение будет добавлено на ваш Apple ID, если Apple его выдаст" in str(r.get("detail")) for r in rows)


def test_smoke5_no_password_or_code_in_ui_state(qapp) -> None:
    source = FakeSource("signin")
    controller = Restore4b(source)
    controller.openSignIn()
    controller.login("m@example.com", "Secret-Pass-123")
    controller.submitCode("482913")
    dump = repr(controller.signIn) + repr(controller.home) + repr(source.calls)
    assert "Secret-Pass-123" not in dump and "482913" not in dump


def test_session_source_minus128_is_not_a_relogin_in_4b(qapp) -> None:
    from apprestore_core.license_gate import STORE_MISMATCH_TEXT

    session = FakeSession()
    session.sessionRelogin = True
    session.sessionNote = STORE_MISMATCH_TEXT
    src = SessionSource(session)
    session.changed.emit()
    assert src.relogin is False
    session.sessionNote = "Сессия Apple ID истекла."
    session.changed.emit()
    assert src.relogin is True


def test_smoke9_offloaded_only_never_touches_the_license_journal(qapp, tmp_path) -> None:
    from apprestore_gui.ui4b.catalog import GROUP_OFFLOADED

    source, journal, bought, notes = _gated_source(tmp_path, scenario="picker")
    source.owned = set()  # nothing on the account: still no license for offloaded ones
    controller = Restore4b(source)
    before = source.license_counts()
    controller.selection.select_visible(False)
    keys = [it.key for it in controller.selection.items if it.group == GROUP_OFFLOADED][:3]
    for key in keys:
        controller.selection.set_selected(key, True)
    controller.restoreSelected()
    wait(qapp, lambda: any(c[0] == "restore_offloaded" for c in source.calls))
    assert not controller.consent.get("open")
    assert [c for c in source.calls if c[0] == "install_store"] == [] and bought == [] and notes == []
    assert source.license_counts() == before and not journal.exists()


def test_smoke5_no_secrets_in_logs_or_terminal(qapp, caplog, capsys) -> None:
    """Window state is covered above; here: logging and stdout/stderr during sign-in,
    and no log/print call in the sign-in/session code takes a secret variable."""

    import ast
    import logging

    caplog.set_level(logging.DEBUG)
    class Session(FakeSession):
        def login(self, email, password):
            self.got = (email, len(password))

        def submitCode(self, code):
            self.code_len = len(code)

    session = Session()
    controller = Restore4b(SessionSource(session))
    controller.openSignIn()
    controller.login("m@example.com", "Secret-Pass-123")
    controller.submitCode("482913")
    out = capsys.readouterr()
    text = caplog.text + out.out + out.err
    assert "Secret-Pass-123" not in text and "482913" not in text

    from pathlib import Path

    root = Path(__file__).resolve().parents[1] / "apprestore_gui"
    files = [root / "auth_pty.py", root / "quick_session.py", root / "purchases.py", *(root / "ui4b").rglob("*.py")]
    secret = ("password", "passphrase", "secret", "cookie", "token", "code")
    bad = []
    for path in files:
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if not isinstance(node, ast.Call):
                continue
            fn = node.func
            name = fn.id if isinstance(fn, ast.Name) else fn.attr if isinstance(fn, ast.Attribute) else ""
            if name not in ("print", "debug", "info", "warning", "error", "exception", "critical", "log"):
                continue
            for arg in ast.walk(ast.Module(body=[ast.Expr(a) for a in node.args], type_ignores=[])):
                if isinstance(arg, ast.Name) and any(s in arg.id.casefold() for s in secret):
                    bad.append(f"{path.name}:{node.lineno} {arg.id}")
    assert bad == []



def test_unpatched_ipatool_no_consent_owned_go_marked_listed(qapp, tmp_path) -> None:
    """Смоук #12 on an old ipatool: no purchase, empty journal, read_counts same;
    owned/offloaded go as usual; the rest says «Нужен дополнительный компонент»."""

    from apprestore_gui.ui4b.component import COMPONENT_NOTE, DETAILS_LINK, HOWTO_LINK, QUIET_LINE
    from apprestore_gui.ui4b.fake_data import removed_items

    source, journal, bought, notes = _gated_source(tmp_path, scenario="unpatched")
    controller = Restore4b(source)
    marked = [i for i in controller.selection.items if i.note == COMPONENT_NOTE]
    not_owned = [i.store_id for i in removed_items()[:2]]
    assert sorted(i.store_id for i in marked) == sorted(not_owned) and not any(i.selectable for i in marked)
    rows = [r for r in controller.selection.rows() if r.get("kind") == "app" and r["storeId"] in not_owned]
    assert rows and all(r["ipaHint"] == HOWTO_LINK and r["hasIpaHint"] for r in rows)
    home = controller.home
    assert home["state"] == "needs_component" and home["number"] == 4 and home["cta"] == "Выбрать и вернуть"
    assert home["fine"] == QUIET_LINE and HOWTO_LINK in home["fine"]
    assert home["links"] == ["Восстановить из истории", DETAILS_LINK]
    assert "удалены из App Store" in home["lead"] and "уже на вашем Apple ID" in home["lead"]
    assert "ipatool" not in home["fine"] + home["lead"]  # technical words only behind «Подробнее»
    controller.link(DETAILS_LINK)
    assert "0001" in controller.home["fine"] and "ipatool" in controller.home["fine"]
    assert controller.ipatoolCaps == {"canAcquireLicense": False, "missing": ["0001", "0003"]}
    before = source.license_counts()
    controller.link(DETAILS_LINK)
    controller.primaryAction()
    assert controller.pickerOpen
    controller.selectAll()
    controller.restoreSelected()
    wait(qapp, lambda: any(c[0] == "install_store" for c in source.calls))
    wait(qapp, lambda: not controller.flow.running)
    assert not controller.consent.get("open")
    assert bought == [] and notes == []
    assert not any(c == ("install_store", sid) for sid in not_owned for c in source.calls)
    assert source.license_counts() == before and not journal.exists()


def test_unpatched_gate_refuses_even_if_called_directly(qapp, tmp_path) -> None:
    from apprestore_core.license_gate import NEEDS_PATCHED_IPATOOL_TEXT
    from apprestore_gui.ui4b.fake_data import removed_items

    source, journal, bought, notes = _gated_source(tmp_path, scenario="unpatched")
    settled = []
    source.installSettled.connect(lambda sid, ok, text: settled.append((sid, ok, text)))
    sid = removed_items()[0].store_id
    source.install_store(sid)
    assert settled and settled[0][:2] == (sid, False) and NEEDS_PATCHED_IPATOOL_TEXT in settled[0][2]
    assert bought == [] and not journal.exists()



def test_needs_component_nothing_returnable_offers_howto_button(qapp, tmp_path) -> None:
    from apprestore_gui.ui4b.component import HOWTO_LINK

    source = FakeSource("unpatched")
    source.owned = set()  # nothing on the account, nothing offloaded
    source.changed.emit()
    controller = Restore4b(source)
    opened = []
    controller.openDocRequested.connect(opened.append)
    home = controller.home
    assert home["state"] == "needs_component" and home["cta"] == "" and home["cta2"] == HOWTO_LINK
    controller.secondaryAction()
    # opens packaging/BUILD-ipatool.md or docs/RUN-FROM-SOURCE.md; without them, the details
    assert (opened and opened[0].endswith(("BUILD-ipatool.md", "RUN-FROM-SOURCE.md"))) or "ipatool" in controller.home["fine"]
    assert not any(c[0] == "install_store" for c in source.calls)


def test_needs_component_hides_region_and_skips_region_probe(qapp) -> None:
    from apprestore_gui.ui4b.catalog import GROUP_REGION
    from apprestore_gui.ui4b.component import COMPONENT_NOTE

    source = FakeSource("region")
    source.patches = ("0001",)
    source.owned = set()
    source.changed.emit()
    controller = Restore4b(source)
    assert not any(i.group == GROUP_REGION for i in controller.selection.items)
    assert "region" not in [r["key"] for r in controller.selection.rail_rows()]
    assert any(i.note == COMPONENT_NOTE for i in controller.selection.items)

    class Tools:
        def license_preflight(self):
            return "нужна сборка"

        def ipatool_capabilities(self):
            return SimpleNamespace(auth_info_country=None, list_purchases_all=False, passphrase_stdin=True)

        def account_country(self):
            raise AssertionError("region_probe must be off without 0001")

    session = FakeSession()
    session.service = SimpleNamespace(core=SimpleNamespace(tools=Tools()))
    src = SessionSource(session)
    src.online = True
    assert src._classify([SimpleNamespace(store_id="1")]) == {}



def test_needs_patched_mid_run_switches_home_to_needs_component(qapp) -> None:
    from apprestore_core.license_gate import NEEDS_PATCHED_IPATOOL_TEXT
    from apprestore_gui.ui4b.component import COMPONENT_NOTE

    source = FakeSource("missing")
    source.owned = set()  # unknown to the GUI that the binary is old
    controller = Restore4b(source)
    begin_picker_restore(controller)
    wait(qapp, lambda: controller.flow.running)
    for entry in list(controller.flow.queue.entries):
        if entry.item.store_id:
            source.installSettled.emit(entry.item.store_id, False, NEEDS_PATCHED_IPATOOL_TEXT)
    wait(qapp, lambda: not controller.flow.running)
    assert controller.flow.component_blocked
    assert "ipatool" not in controller.home.get("lead", "")
    controller.dismissDone()
    home = controller.home
    assert home["state"] == "needs_component" and home["state"] != "store_mismatch"
    assert any(i.note == COMPONENT_NOTE for i in controller.selection.items)


# -- «Выйти» (04-auth §2a, смоук п.18) -----------------------------------------------------


def _signed_in_source():
    source = FakeSource("missing")
    source.auth_phase = "in"
    source.account_email = "a-very-long-apple-id-address-for-elide-tests@example.com"
    source.changed.emit()
    return source


def test_apple_id_link_opens_account_sheet_and_signs_out_after_confirm(qapp) -> None:
    source = _signed_in_source()
    controller = Restore4b(source)
    controller.flow._relogged_for_store.add("x@example.com")  # -128 mark in memory
    controller.link("Apple ID")
    a = controller.account
    assert a["open"] and not controller.signIn["open"]
    assert a["email"] == source.account_email  # in full, no mask
    assert a["signOut"] == "Выйти" and a["signOutEnabled"] and not a["confirm"]
    controller.askSignOut()
    a = controller.account
    assert a["confirm"] and a["confirmTitle"] == "Выйти из Apple ID?"
    assert a["confirmText"] == "Приложения на iPhone останутся. Чтобы вернуть новые, нужно будет войти снова."
    controller.cancelSignOut()
    assert controller.account["open"] and not controller.account["confirm"]
    assert ("sign_out", None) not in source.calls
    controller.askSignOut()
    controller.confirmSignOut()
    assert ("sign_out", None) in source.calls
    assert not controller.account["open"] and not controller.signIn["open"]
    assert controller.flow._relogged_for_store == set() and controller.flow.store_problem == ""


def test_sign_out_inactive_during_installation(qapp) -> None:
    source = _signed_in_source()
    controller = Restore4b(source)
    begin_picker_restore(controller)
    wait(qapp, lambda: controller.flow.running)
    controller.openAccount()
    a = controller.account
    assert not a["signOutEnabled"] and a["signOutHint"] == "Дождитесь конца установки"
    controller.askSignOut()
    controller.confirmSignOut()
    assert not controller.account["confirm"] and ("sign_out", None) not in source.calls


def test_account_sheet_and_confirmation_load_without_warnings(qapp) -> None:
    source = _signed_in_source()
    controller = Restore4b(source)
    controller.openAccount()
    engine, icons, warnings = open_window(qapp, controller)
    window = engine.rootObjects()[0]
    email = window.findChild(QQuickItem, "accountEmail")
    assert email is not None and email.property("text") == source.account_email
    sheet = (qml_path().parent / "components" / "AccountSheet.qml").read_text(encoding="utf-8")
    assert "Text.ElideMiddle" in sheet and "Theme.danger" not in sheet  # no red
    controller.askSignOut()
    for _ in range(3):
        qapp.processEvents()
    assert window.findChild(QQuickItem, "signOutConfirm").property("visible")
    window.close()
    del window, engine
    qapp.processEvents()
    assert warnings == []


@pytest.mark.parametrize(
    "caps, missing",
    [
        (dict(list_purchases_all=True, passphrase_stdin=False, auth_info_country=None), ("0003",)),
        (dict(list_purchases_all=False, passphrase_stdin=True, auth_info_country=True), ("0002",)),
        (dict(list_purchases_all=False, passphrase_stdin=False, auth_info_country=False), ("0001", "0002", "0003")),
    ],
)
def test_needs_component_details_list_only_what_capabilities_lack(caps, missing) -> None:
    from types import SimpleNamespace

    from apprestore_core.ipatool_api import Capabilities
    from apprestore_gui.ui4b import component
    from apprestore_gui.ui4b.qt_bridge import SessionSource

    tools = SimpleNamespace(license_preflight=lambda: "needs", ipatool_capabilities=lambda: Capabilities(**caps))
    src = SessionSource.__new__(SessionSource)
    src.session = SimpleNamespace(service=SimpleNamespace(core=SimpleNamespace(tools=tools)))
    assert src.missing_patches() == missing
    text = component.details_text(missing)
    for name in ("0001", "0002", "0003"):
        assert (name in text) == (name in missing)


def test_history_store_id_starts_the_usual_restore(qapp) -> None:
    source = FakeSource("empty")
    source.signed_in = True
    source.purchase_rows = lambda: [{"trackId": "492224193", "name": "СберБанк"}]
    controller = Restore4b(source, onboarded=True)
    controller.openHistory()
    controller.restoreByStoreId("не id")
    assert controller.history["open"] is True
    assert controller.history["idNote"]
    controller.restoreByStoreId("https://apps.apple.com/app/id492224193")
    assert controller.history["open"] is False
    assert controller._checking_space is True


def test_history_sheet_lists_purchases(qapp) -> None:
    source = FakeSource("empty")
    source.purchase_rows = lambda: [
        {"trackId": "10", "name": "Почта", "date": "2024-05-01", "bundleId": "ru.mail"},
    ]
    controller = Restore4b(source, onboarded=True)
    controller.openHistory()
    engine, _icons, warnings = open_window(qapp, controller)
    window = engine.rootObjects()[0]
    sheet = window.findChild(QQuickItem, "historySheet")
    assert sheet is not None
    window.close()
    del window
    del engine
    qapp.processEvents()
    assert warnings == []

