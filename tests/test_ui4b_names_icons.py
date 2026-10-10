"""Евгений's live run (10.10): 1) titles were bundle ids, 2) the phone showed
empty squares instead of icons. No network, no device: stubs only."""

from __future__ import annotations

import os
import re
import time
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QObject, QUrl, Signal  # noqa: E402
from PySide6.QtGui import QColor, QImage  # noqa: E402
from PySide6.QtQml import QQmlApplicationEngine  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from apprestore_core.models import InstalledApp, MissingApp, OffloadedApp  # noqa: E402
from apprestore_gui.icons_cache import IconBook  # noqa: E402
from apprestore_gui.ui4b import names  # noqa: E402
from apprestore_gui.ui4b.qt_bridge import Restore4b, SessionSource  # noqa: E402

BUNDLE = re.compile(r"\b[a-z0-9_-]+(\.[a-z0-9_-]+){2,}\b", re.I)


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


class StubSession(QObject):
    changed = Signal()
    purchasesChanged = Signal()
    installProgress = Signal(int, str)
    appRestored = Signal(str)
    restoreSettled = Signal(str)
    installSettled = Signal(str, bool, str)
    copySettled = Signal(str, bool, str)
    filesChanged = Signal()

    def __init__(self, offloaded, missing, phone, purchases):
        super().__init__()
        self._off, self.purchases, self.phoneApps = offloaded, purchases, phone
        self._missing = missing
        self.connected, self.loading, self.signedIn = True, False, True
        self.deviceName, self.deviceNoun = "iPhone", "iPhone"
        self.purchasesBusy, self.purchasesProgress = False, ""

    def offloaded_snapshot(self):
        return list(self._off)

    def current_udid(self):
        return ""

    def restore(self, keys):  # no device: nothing happens
        self.restored = list(keys)

    def installStore(self, store_id):
        self.installed = store_id

    acquireStore = installStore


def _source(qapp):
    off = [OffloadedApp(bundle_id="com.example.weather", name="com.example.weather", version="1"),  # no device name
           OffloadedApp(bundle_id="ru.example.maps", name="Карты", version="1"),                    # device name
           OffloadedApp(bundle_id="org.example.notes.pro", name="", version="1")]                   # nothing at all
    missing = [MissingApp(bundle_id="com.example.bank", name="com.example.bank", store_id="111")]
    phone = [{"name": "com.example.phone.app", "bundleId": "com.example.phone.app", "storeId": "222"},
             {"name": "Почта", "bundleId": "com.example.mail", "storeId": ""}]
    purchases = [{"trackId": "111", "bundleId": "com.example.bank", "name": "Банк Пример"},
                 {"trackId": "", "bundleId": "com.example.weather", "name": "Погода"},
                 {"trackId": "222", "bundleId": "com.example.phone.app", "name": "Телефон+"}]
    src = SessionSource(StubSession(off, missing, phone, purchases))
    src._missing = missing
    src.connected = src.signed_in = True
    src.auth_phase = "in"
    return src


def test_name_chain_device_then_purchases_then_builtin_then_fallback() -> None:
    assert names.resolve("Карты", bundle_id="ru.example.maps") == "Карты"
    assert names.resolve("ru.example.maps", bundle_id="ru.example.maps",
                         purchases={"ru.example.maps": "Карты П"}) == "Карты П"
    assert names.resolve("", bundle_id="x.y.z", builtin={"x.y.z": "Встроенное"}) == "Встроенное"
    assert names.resolve("", bundle_id="x.y.z") == "Приложение"
    assert names.resolve("123", store_id="123") == "Приложение"
    # real names with dots stay
    assert names.resolve("Booking.com", bundle_id="com.booking.BookingApp") == "Booking.com"


def test_builtin_list_names_by_bundle_from_delisted_search() -> None:
    from apprestore_core import delisted_search

    book = names.builtin_names()
    for e in delisted_search.builtin_entries():
        if e.bundle_id:
            assert book[e.bundle_id] == e.name


def test_no_bundle_id_as_a_title_anywhere_in_4b(qapp) -> None:
    src = _source(qapp)
    by_bundle = {i.bundle_id: i.name for i in src.items()}
    assert by_bundle == {"com.example.weather": "Погода", "ru.example.maps": "Карты",
                         "org.example.notes.pro": "Приложение", "com.example.bank": "Банк Пример"}
    assert [a.name for a in src.phone_apps()] == ["Телефон+", "Почта"]
    c = Restore4b(src)
    c._on_source()
    titles: list[str] = []
    titles += [str(t.get("name", "")) + " " + str(t.get("app", "")) for t in c.home.get("tiles", [])]
    titles += [str(r.get("name", "")) + " " + str(r.get("label", "")) for r in c.picker.rows()]
    titles += [str(c.home.get(k, "")) for k in ("lead", "title", "cta", "fine")]
    # install queue
    c.flow.begin([i for i in c.selection.items if i.selectable][:3], c.source.space())
    c._refresh()
    queue = c.home.get("queue", [])
    assert queue and c.home.get("tiles") and c.picker.rows()
    text_keys = {"name", "title", "label", "app", "detail", "right", "stages", "a11y"}
    titles += [" ".join(str(v) for k, v in r.items() if k in text_keys) for r in queue]
    assert any("Погода" in t for t in titles[-len(queue):])
    text = " | ".join(titles)
    assert "Погода" in text
    for bid in by_bundle:
        assert bid not in text
    assert not BUNDLE.search(text), text


# -- icons ------------------------------------------------------------------------------

class FakeArt:
    """ArtworkCache stand-in: first lookup fails (network not up yet), later works."""

    def __init__(self, png: Path, fail_first: bool = True):
        self.png, self.calls, self.fail_first = png, 0, fail_first

    def rounded_icon_file(self, *, store_id="", bundle_id="", site=""):
        self.calls += 1
        if self.fail_first and self.calls == 1:
            return None
        return self.png

    def artwork_info(self, *, store_id="", bundle_id=""):
        return {"bundleId": bundle_id}


def _png(tmp_path: Path) -> Path:
    img = QImage(64, 64, QImage.Format.Format_ARGB32)
    img.fill(QColor(10, 120, 200))
    p = tmp_path / "icon.png"
    img.save(str(p))
    return p


def _wait(qapp, cond, s=3.0):
    end = time.monotonic() + s
    while not cond() and time.monotonic() < end:
        qapp.processEvents()
        time.sleep(0.01)
    return cond()


def test_icon_book_retries_after_a_miss_and_updates_on_gui_thread(qapp, tmp_path, monkeypatch) -> None:
    book = IconBook(cache=FakeArt(_png(tmp_path)))
    monkeypatch.setattr(IconBook, "RETRY_S", 0.0)
    seen_threads = []
    book.changed.connect(lambda: seen_threads.append(__import__("threading").current_thread().name))
    book.consider_async([("555", "com.example.a", "")])
    assert _wait(qapp, lambda: book.stats["missed"] == 1)
    assert book.pathFor("555") == ""
    book.consider_async([("555", "com.example.a", "")])  # retry (was «tried» forever before)
    assert _wait(qapp, lambda: book.pathFor("555") != "")
    assert book.pathFor("com.example.a") == book.pathFor("555")
    assert seen_threads and all(name == "MainThread" for name in seen_threads)


def test_icon_book_gives_up_after_max_tries(qapp, tmp_path, monkeypatch) -> None:
    class Never(FakeArt):
        def rounded_icon_file(self, **k):
            self.calls += 1
            return None

    art = Never(_png(tmp_path))
    book = IconBook(cache=art)
    monkeypatch.setattr(IconBook, "RETRY_S", 0.0)
    for _ in range(6):
        book.consider_async([("1", "", "")])
        _wait(qapp, lambda: not book._pending, 1.0)
    assert art.calls == IconBook.MAX_TRIES


def test_one_failing_icon_does_not_stop_the_rest(qapp, tmp_path) -> None:
    png = _png(tmp_path)

    class Boom(FakeArt):
        def rounded_icon_file(self, *, store_id="", bundle_id="", site=""):
            if store_id == "1":
                raise RuntimeError("bad image")
            return png

    book = IconBook(cache=Boom(png, fail_first=False))
    book.consider_async([("1", "", ""), ("2", "", ""), ("3", "", "")])
    assert _wait(qapp, lambda: book.pathFor("2") and book.pathFor("3"))
    assert book.stats["errors"] == 1


def test_phone_tile_swaps_placeholder_for_artwork_without_letters(qapp, tmp_path) -> None:
    comp = Path(__file__).resolve().parents[1] / "apprestore_gui" / "qml4b" / "components"
    book = IconBook(cache=FakeArt(_png(tmp_path), fail_first=False))
    qml = tmp_path / "m.qml"
    qml.write_text(
        "import QtQuick\nimport QtQuick.Window\nimport \"" + QUrl.fromLocalFile(str(comp)).toString() + "\"\n"
        "Window { width: 200; height: 200; visible: true\n"
        "  PhoneTile { objectName: 'tile'; tile: ({kind: 'app', name: 'Погода', storeId: '9', bundleId: 'b.c.d'}) } }\n",
        encoding="utf-8",
    )
    engine = QQmlApplicationEngine()
    engine.rootContext().setContextProperty("iconBook", book)
    warnings = []
    engine.warnings.connect(lambda ws: warnings.extend(w.toString() for w in ws))
    engine.load(QUrl.fromLocalFile(str(qml)))
    win = engine.rootObjects()[0]
    tile = win.findChild(QObject, "tile")
    icon = [o for o in tile.findChildren(QObject) if o.property("hasArt") is not None][0]
    assert icon.property("hasArt") is False  # placeholder (Theme.iconPlaceholder), no letter
    book.consider_async([("9", "b.c.d", "")])
    assert _wait(qapp, lambda: icon.property("hasArt") is True)
    texts = [o.property("text") for o in tile.findChildren(QObject) if o.property("text") is not None]
    assert texts == ["Погода"] or set(texts) <= {"Погода", ""}
    assert not warnings, warnings
    del engine


def test_placeholder_is_theme_token_no_dash_no_letter() -> None:
    root = Path(__file__).resolve().parents[1] / "apprestore_gui" / "qml4b"
    icon = (root / "components" / "AppIcon.qml").read_text(encoding="utf-8")
    assert "Theme.iconPlaceholder" in icon and "DashLine" not in icon and "#" not in icon.split("Item {", 1)[1]
    theme = (root / "theme" / "Theme.qml").read_text(encoding="utf-8")
    assert re.search(r"iconPlaceholder:\s*\"#E2E0DA\"", theme, re.I)
    for f in (root / "components").glob("*.qml"):
        t = f.read_text(encoding="utf-8")
        assert "modelData.mark" not in t and ".mark\b" not in t, f


# -- BUG (Eugene, f7b0f28): 4 phone tiles «Приложение» with a dashed outline ---------------


def _tile_window(tmp_path, tile_js: str):
    comp = Path(__file__).resolve().parents[1] / "apprestore_gui" / "qml4b" / "components"
    book = IconBook(cache=FakeArt(_png(tmp_path), fail_first=False))
    qml = tmp_path / "t.qml"
    qml.write_text(
        "import QtQuick\nimport QtQuick.Window\nimport \"" + QUrl.fromLocalFile(str(comp)).toString() + "\"\n"
        "Window { width: 200; height: 200; visible: true\n"
        "  PhoneTile { objectName: 'tile'; tile: (" + tile_js + ") } }\n",
        encoding="utf-8",
    )
    engine = QQmlApplicationEngine()
    engine.rootContext().setContextProperty("iconBook", book)
    warnings: list[str] = []
    engine.warnings.connect(lambda ws: warnings.extend(w.toString() for w in ws))
    engine.load(QUrl.fromLocalFile(str(qml)))
    win = engine.rootObjects()[0]
    tile = win.findChild(QObject, "tile")
    return (engine, win), book, tile, warnings


@pytest.mark.parametrize("ids", ["storeId: '9'", "bundleId: 'ru.bank.app'", "storeId: '9', bundleId: 'ru.bank.app'"])
def test_missing_app_slot_is_a_placeholder_icon_not_a_dashed_empty_place(qapp, tmp_path, ids) -> None:
    engine, _book, tile, warnings = _tile_window(tmp_path, "{kind: 'slot', name: 'Приложение', " + ids + "}")
    dash = tile.findChild(QObject, "slotDash")
    icon = [o for o in tile.findChildren(QObject) if o.property("hasArt") is not None][0]
    assert tile.property("emptySlot") is False
    assert dash.property("visible") is False  # no dashes for a real app
    assert icon.property("visible") is True and icon.property("hasArt") is False  # Theme.iconPlaceholder
    assert icon.property("width") == tile.property("icon") and icon.property("radius") == tile.property("r")
    assert not warnings, warnings
    del engine


def test_only_a_truly_empty_place_is_dashed(qapp, tmp_path) -> None:
    engine, _book, tile, warnings = _tile_window(tmp_path, "{kind: 'slot', name: ''}")
    assert tile.property("emptySlot") is True
    assert tile.findChild(QObject, "slotDash").property("visible") is True
    icon = [o for o in tile.findChildren(QObject) if o.property("hasArt") is not None][0]
    assert icon.property("visible") is False
    assert not warnings, warnings
    del engine


def test_dash_rule_lives_in_one_place() -> None:
    qml = (Path(__file__).resolve().parents[1] / "apprestore_gui" / "qml4b" / "components" / "PhoneTile.qml").read_text(encoding="utf-8")
    assert qml.count("DashLine") == 1
    assert "visible: root.emptySlot && !root.tile.pending" in qml and "visible: !root.emptySlot" in qml
    assert 'root.kind === "slot" && !root.tile.pending' not in qml


def test_home_slots_for_missing_apps_carry_their_ids(qapp) -> None:
    from apprestore_gui.ui4b.catalog import GROUP_REMOVED, RestoreItem
    from apprestore_gui.ui4b.home import HomeInput, phone_tiles

    items = [RestoreItem(key=f"store:{i}", name="Приложение", group=GROUP_REMOVED, action="store",
                         store_id=str(i), bundle_id=f"ru.example.app{i}") for i in range(4)]
    inp = HomeInput(connected=True, items=items)
    slots = [t for t in phone_tiles(inp, "missing") if t["kind"] == "slot"]
    assert len(slots) == 4 and all(t["storeId"] and t["bundleId"] for t in slots)


def test_installation_proxy_asks_for_names_and_keeps_offloaded() -> None:
    import inspect

    from apprestore_core.tools import AppRestoreTools

    src = inspect.getsource(AppRestoreTools._list_apps_with_metadata)
    for attr in ("CFBundleDisplayName", "CFBundleName", "CFBundleIdentifier", "ApplicationType"):
        assert f'"{attr}"' in src
    assert '"ReturnAttributes": return_attributes' in src
    assert '"ApplicationType": "User"' in src and '"ShowPlaceholders": True' in src


def test_offloaded_placeholder_name_from_itunes_metadata() -> None:
    import plistlib

    from apprestore_core.catalog import parse_offloaded_apps

    meta = plistlib.dumps({"itemName": "Погода Плюс", "itemId": 123})
    payload = {"ru.example.weather": {"CFBundleIdentifier": "ru.example.weather", "IsPlaceholder": True,
                                      "ApplicationType": "User", "iTunesMetadata": meta}}
    apps = parse_offloaded_apps(payload, [], {})
    assert [a.name for a in apps] == ["Погода Плюс"]


def test_service_stand_in_name_is_not_a_title() -> None:
    from apprestore_gui.ui4b.names import resolve, usable

    assert usable("App Store 492224193", store_id="492224193") == ""
    assert resolve("App Store 492224193", store_id="492224193",
                   builtin={"492224193": "Сбербанк Онлайн"}) == "Сбербанк Онлайн"
    assert resolve("App Store 1", store_id="1") == "Приложение"
