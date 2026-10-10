"""BUG (Eugene, f7b0f28): «Найти» said «Нет интернета» while the internet worked and he
was not signed in (sign-in stopped at 2FA).

Root cause: SessionSource took QuickSession.sessionState == "offline" as «no internet»;
that state comes from ``session_alive`` NO_NETWORK, which also covers unknown ipatool
failures (no saved account, unfinished sign-in). Now «Нет интернета» needs the session
probe's «offline» AND no public host answering (ui4b.network). Not signed in / expired
is its own banner «Войдите в Apple ID, чтобы поставить найденное» with «Войти»; search
works fully; «Поставить» opens sign-in; after signing in nothing starts by itself
(Лена R3) and the results, the query and the scroll stay (02-picker §6b, line 253).
"""

from __future__ import annotations

import time
from types import SimpleNamespace

import pytest

pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication  # noqa: E402

from apprestore_gui.ui4b import find as F  # noqa: E402
from apprestore_gui.ui4b import network  # noqa: E402
from apprestore_gui.ui4b.fake_data import FakeIconBook, FakeSource  # noqa: E402
from apprestore_gui.ui4b.qt_bridge import Restore4b  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


def hit(tid, name, dev, src="builtin", conf=0.95, snap=None):
    return SimpleNamespace(track_id=int(tid), name=name, developer=dev, icon_url=None,
                           source=SimpleNamespace(value=src), confidence=conf, snapshot=snap,
                           brand="", developer_is_bank=None)


class Delisted:
    def __init__(self, builtin=(), archive=(), down=False):
        self.builtin, self.archive, self.down = list(builtin), list(archive), down
        self.calls: list[tuple[str, bool]] = []

    def __call__(self, query, allow_network):
        self.calls.append((query, allow_network))
        return (self.builtin + self.archive, self.down) if allow_network else (list(self.builtin), False)


def _controller(src, delisted=None):
    return Restore4b(src, find_options={"delisted": delisted or Delisted(), "spawn": lambda f: f()})


def _signed_out_source(n: int = 1) -> FakeSource:
    src = FakeSource("missing")
    src.signed_in = False
    src.owned = None
    src.find_store = [{"storeId": str(100 + i), "name": f"Погода {i}", "source": "appstore"} for i in range(n)]
    src.find_offers = {str(100 + i): {"price": 0.0, "developer": "Dev"} for i in range(n)}
    return src


# -- network rule ---------------------------------------------------------------------


def test_offline_only_when_session_probe_and_hosts_both_fail() -> None:
    assert network.is_offline("offline", False) is True
    assert network.is_offline("offline", True) is False
    assert network.is_offline("offline", None) is False  # not probed yet: never guess
    assert network.is_offline("expired", False) is False
    assert network.is_offline("unknown", False) is False


def test_any_host_answers_tries_each_host_and_respects_proxy() -> None:
    tried: list[tuple[str, int]] = []

    def refuse(addr, timeout):
        tried.append(addr)
        raise OSError("refused")

    assert network.any_host_answers(env={}, connect=refuse) is False
    assert tried == list(network.PROBE_HOSTS)

    class Sock:
        def close(self):
            pass

    calls = []

    def second(addr, timeout):
        calls.append(addr)
        if len(calls) == 1:
            raise OSError("one failed lookup is not offline")
        return Sock()

    assert network.any_host_answers(env={}, connect=second) is True
    assert network.any_host_answers(env={"HTTPS_PROXY": "http://p:1"}, connect=refuse) is True


def _source(qapp, state: str, hosts: bool, signed: bool = False):
    from apprestore_gui.ui4b.qt_bridge import SessionSource

    from tests.test_ui4b_qml import FakeSession

    sess = FakeSession()
    sess.signedIn, sess.authPhase, sess.sessionState = signed, ("in" if signed else "out"), state
    src = SessionSource(sess)
    src._probe_hosts = lambda: hosts
    sess.changed.emit()
    end = time.monotonic() + 5
    while src._hosts_busy and time.monotonic() < end:
        qapp.processEvents()
        time.sleep(0.01)
    qapp.processEvents()
    return src


@pytest.mark.parametrize(
    ("state", "hosts", "online"),
    [
        ("offline", True, True),     # Eugene: not signed in, ipatool's unknown failure → NO_NETWORK
        ("offline", False, False),   # really no internet
        ("expired", False, True),    # expired is not «no internet»
        ("unknown", True, True),
    ],
)
def test_session_source_online_needs_no_host_answering(qapp, state, hosts, online) -> None:
    src = _source(qapp, state, hosts)
    assert src.online is online


def test_not_signed_in_find_works_fully_with_signin_banner_not_offline(qapp) -> None:
    d = Delisted([hit("6749962031", "Cириус", "Sergei Smirnov")])
    src = _signed_out_source()
    c = _controller(src, d)
    c.finder.openWith("погода")
    b = c.finder.banner
    assert b == {"kind": "signin", "text": "Войдите в Apple ID, чтобы поставить найденное", "link": "Войти"}
    assert b["text"] != F.OFFLINE_BANNER
    rows = [r for r in c.finder.view["rows"] if r["kind"] == "app"]
    assert {r["storeId"] for r in rows} >= {"100", "6749962031"}  # App Store and the built-in list
    assert all(r["enabled"] for r in rows)
    assert [r for r in rows if r["storeId"] == "100"][0]["action"] == "Поставить"  # stays active


def test_archive_down_is_a_note_under_deleted_block_not_offline(qapp) -> None:
    src = FakeSource("missing")
    c = _controller(src, Delisted(down=True))
    c.finder.openWith("редкое")
    assert c.finder.view["archiveNote"].startswith("Архив сейчас не отвечает")
    assert c.finder.banner["text"] == ""
    assert all(r["enabled"] for r in c.finder.view["rows"] if r["kind"] == "app")


def test_install_without_session_opens_signin_then_nothing_starts_by_itself(qapp) -> None:
    src = _signed_out_source()
    c = _controller(src)
    started: list[object] = []
    c._start = lambda items: started.append(items)
    c.finder.openWith("погода")
    before = c.finder.view["rows"]
    c.finder.install("100", "Погода 0")
    assert c.signIn["open"] and c.finder.open and not started  # sign-in over the results
    # sign-in succeeds: the sheet closes, the results stay, nothing is installed
    views: list[int] = []
    c.finder.changed.connect(lambda: views.append(1))
    src.signed_in, src.auth_phase = True, "in"
    src.changed.emit()
    assert not c.signIn["open"]
    assert c.finder.open and c.finder.query == "погода"
    assert c.finder.view["rows"] == before and views == []  # rows not re-read: scroll stays
    assert c.finder.banner["text"] == ""  # the sign-in banner is gone
    assert not started  # Лена R3: no acquire / install until a new press
    c.finder.install("100", "Погода 0")
    assert len(started) == 1 and started[0][0].store_id == "100"


def test_cancel_signin_leaves_the_row_and_starts_nothing(qapp) -> None:
    src = _signed_out_source()
    c = _controller(src)
    started: list[object] = []
    c._start = lambda items: started.append(items)
    c.finder.openWith("погода")
    before = c.finder.view["rows"]
    c.finder.signIn()  # the banner link
    assert c.signIn["open"]
    c.cancelLogin()
    assert not c.signIn["open"] and c.finder.open and c.finder.view["rows"] == before and not started


def test_qml_scroll_and_query_survive_signin(qapp) -> None:
    from PySide6.QtCore import QObject
    from PySide6.QtQml import QQmlApplicationEngine

    from apprestore_gui.ui4b.window import load

    src = _signed_out_source(40)
    c = _controller(src)
    engine = QQmlApplicationEngine()
    warnings: list[str] = []
    engine.warnings.connect(lambda ws: warnings.extend(w.toString() for w in ws))
    icons = FakeIconBook()  # kept alive for the whole test
    assert load(engine, c, icons)
    window = engine.rootObjects()[0]
    c.finder.openWith("погода")
    for _ in range(5):
        qapp.processEvents()
    lst = window.findChild(QObject, "findList")
    banner = window.findChild(QObject, "findBanner")
    assert lst is not None and banner.property("visible")
    lst.setProperty("contentY", 300.0)
    qapp.processEvents()
    y = lst.property("contentY")
    assert y > 0
    c.finder.install("100", "Погода 0")
    for _ in range(5):
        qapp.processEvents()
    assert window.findChild(QObject, "signInSheet") is not None
    src.signed_in, src.auth_phase = True, "in"
    src.changed.emit()
    from PySide6.QtCore import QCoreApplication, QEvent

    for _ in range(5):
        qapp.processEvents()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    assert not c.signIn["open"]
    assert window.findChild(QObject, "signInSheet") is None
    lst = window.findChild(QObject, "findList")
    assert lst.property("contentY") == y
    assert window.findChild(QObject, "findInput").property("text") == "погода"
    assert not window.findChild(QObject, "findBanner").property("visible")
    window.close()
    del window, engine
    qapp.processEvents()
    assert warnings == []
