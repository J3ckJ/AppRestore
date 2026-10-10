"""Eugene's live test of 1f34c49 (10.10): «Всё на месте» after one install, no
«Выбрать и вернуть», «Файлы IPA», phone tile labels."""

from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("PySide6")

from PySide6.QtCore import QCoreApplication, QEvent, QObject  # noqa: E402
from PySide6.QtQml import QQmlApplicationEngine  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from apprestore_gui.ui4b import files as Fv  # noqa: E402
from apprestore_gui.ui4b.catalog import GROUP_OFFLOADED, GROUP_REMOVED, RestoreItem  # noqa: E402
from apprestore_gui.ui4b.fake_data import FakeIconBook, FakeSource  # noqa: E402
from apprestore_gui.ui4b.home import STATE_DONE, HomeInput, home_view, link_action  # noqa: E402
from apprestore_gui.ui4b.qt_bridge import Restore4b  # noqa: E402
from apprestore_gui.ui4b.queue import RestoreQueue  # noqa: E402
from apprestore_gui.ui4b.window import load  # noqa: E402

QML = Path(__file__).resolve().parents[1] / "apprestore_gui" / "qml4b"


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


def _items(n_off: int, n_removed: int = 0) -> list[RestoreItem]:
    out = [RestoreItem(key=f"o{i}", name=f"App {i}", group=GROUP_OFFLOADED, action="offloaded",
                       bundle_id=f"b.o{i}", size_bytes=10) for i in range(n_off)]
    out += [RestoreItem(key=f"r{i}", name=f"Bank {i}", group=GROUP_REMOVED, action="store",
                        store_id=str(100 + i), size_bytes=10) for i in range(n_removed)]
    return out


# -- bug 1: done only at 0 missing --------------------------------------------------

def test_one_install_with_547_left_goes_back_to_missing_with_new_number() -> None:
    items = _items(547, 4)
    q = RestoreQueue()
    q.start([items[-1]])
    q.settle(items[-1].key, True)
    assert q.finished
    v = home_view(HomeInput(connected=True, items=items, queue=q))
    assert v["state"] != STATE_DONE and v["number"] == 550
    assert v["cta"] == "Выбрать и вернуть"


def test_done_only_when_nothing_is_left() -> None:
    items = _items(0, 2)
    q = RestoreQueue()
    q.start(items)
    for it in items:
        q.settle(it.key, True)
    v = home_view(HomeInput(connected=True, items=items, queue=q))
    assert v["state"] == STATE_DONE and v["title"].startswith("Всё")


def test_finished_run_rereads_the_device_once_and_starts_nothing(qapp) -> None:
    src = FakeSource("many")
    reloads: list[int] = []
    src.reload_device = lambda: reloads.append(1)
    c = Restore4b(src)
    item = src.items()[0]
    c.flow.queue.start([item])
    c.flow.queue.settle(item.key, True)
    c._refresh()
    c._refresh()
    assert reloads == [1]
    assert c.home["state"] != STATE_DONE


# -- bug 4: «Выбрать и вернуть» when «Вернуть все N» does not fit -----------------------

@pytest.mark.parametrize("n", [9, 547])
def test_many_shows_choose_and_restore_with_hint(n: int) -> None:
    v = home_view(HomeInput(connected=True, items=_items(n)))
    assert v["cta"] == "Выбрать и вернуть"
    assert "Сначала покажем список, отметите нужные." in str(v.get("hint") or v.get("note") or v)


def test_hero_cta_row_does_not_depend_on_its_child_visibility() -> None:
    src = (QML / "components" / "HomeHero.qml").read_text(encoding="utf-8")
    assert "visible: root.hasCta" in src


def test_cta_row_visible_after_loading_then_many(qapp) -> None:
    src = FakeSource("many")
    src.loading = True
    c = Restore4b(src)
    engine = QQmlApplicationEngine()
    warnings: list[str] = []
    engine.warnings.connect(lambda ws: warnings.extend(w.toString() for w in ws))
    icons = FakeIconBook()
    assert load(engine, c, icons)
    window = engine.rootObjects()[0]
    qapp.processEvents()
    src.loading = False
    src.changed.emit()
    for _ in range(5):
        qapp.processEvents()
    row = window.findChild(QObject, "heroCtaRow")
    assert row is not None and row.property("visible")
    window.close()
    del window, engine
    qapp.processEvents()
    assert warnings == []


# -- bug 3: «Файлы IPA» ----------------------------------------------------------

def test_files_link_opens_the_sheet_ipa_link_still_picks_a_file() -> None:
    assert link_action("Файлы IPA") == "files"
    from apprestore_gui.ui4b.home import IPA_LINK

    assert link_action(IPA_LINK) == "ipa"


def test_files_view_rows_and_buttons(tmp_path) -> None:
    f = tmp_path / "Telegram_11.2.ipa"
    f.write_bytes(b"\0" * 2_000_000)
    rows = Fv.library_rows([{"path": str(f), "name": "Telegram", "detail": "11.2", "bundleId": "ph.telegra"},
                            {"path": str(tmp_path / "x.ipa"), "name": "", "detail": "x.ipa"}])
    assert rows[0]["meta"].startswith("11.2 · ") and rows[1]["name"] == "x" and rows[1]["meta"] == ""
    v = Fv.files_view(open_=True, mode="list", library=rows, export=[], note="", busy=False, connected=False)
    assert (v["title"], v["secondary"], v["primary"]) == ("Файлы IPA", "Выгрузить с устройства", "Выбрать на ПК")
    assert v["secondaryEnabled"] is False and v["primaryEnabled"] is True
    empty = Fv.files_view(open_=True, mode="list", library=[], export=[], note="", busy=False, connected=True)
    assert empty["empty"] == Fv.EMPTY
    ex = Fv.export_rows([{"bundleId": "a", "name": "A"}, {"bundleId": "b", "name": "B"}], {"a", "b"})
    v = Fv.files_view(open_=True, mode="export", library=[], export=ex, note="", busy=False, connected=True)
    assert v["primary"] == "Сохранить 2" and v["primaryEnabled"] and v["secondary"] == "Назад"


class _FilesSource(FakeSource):
    def __init__(self, tmp: Path) -> None:
        super().__init__("many")
        self.loaded = 0
        self.saved: list[list[str]] = []
        f = tmp / "Сбербанк Онлайн_17.6.ipa"
        f.write_bytes(b"\0" * 1000)
        self._lib = [{"path": str(f), "name": "Сбербанк Онлайн", "detail": "17.6", "bundleId": "ru.sber"}]

    def library_files(self):
        return list(self._lib)

    def load_library(self) -> None:
        self.loaded += 1

    def export_apps(self):
        return [{"bundleId": "ru.vtb", "name": "ВТБ Онлайн", "detail": "21.1"}]

    def save_copies(self, ids) -> None:
        self.saved.append(list(ids))


def test_files_sheet_flow_and_qml(qapp, tmp_path) -> None:
    src = _FilesSource(tmp_path)
    c = Restore4b(src)
    picks: list[int] = []
    c.pickIpaRequested.connect(lambda: picks.append(1))
    installed: list[str] = []
    c.installIpaFile = lambda p: installed.append(p)
    engine = QQmlApplicationEngine()
    warnings: list[str] = []
    engine.warnings.connect(lambda ws: warnings.extend(w.toString() for w in ws))
    icons = FakeIconBook()
    assert load(engine, c, icons)
    window = engine.rootObjects()[0]
    c.link("Файлы IPA")
    for _ in range(5):
        qapp.processEvents()
    assert c.files["open"] and src.loaded == 1
    sheet = window.findChild(QObject, "filesSheet")
    assert sheet is not None
    assert window.findChild(QObject, "filesPrimary").property("text") == "Выбрать на ПК"
    assert window.findChild(QObject, "filesSecondary").property("text") == "Выгрузить с устройства"
    c.filesExport()
    bid = c.files["rows"][0]["key"]
    c.filesToggle(bid)
    c.filesSave()
    assert src.saved == [[bid]] and c.files["mode"] == "list"
    c.filesInstall(src._lib[0]["path"])
    assert installed == [src._lib[0]["path"]] and not c.files["open"]
    c.openFiles()
    c.filesPick()
    assert picks == [1] and not c.files["open"]
    for _ in range(5):
        qapp.processEvents()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    assert window.findChild(QObject, "filesSheet") is None
    window.close()
    del window, engine
    qapp.processEvents()
    assert warnings == []


# -- bug 5: tile labels one line, elided at the grid step --------------------------------

def test_phone_tile_label_is_one_line_elided_at_grid_step() -> None:
    tile = (QML / "components" / "PhoneTile.qml").read_text(encoding="utf-8")
    assert "Text.ElideRight" in tile and "maximumLineCount: 1" in tile and "Text.NoWrap" in tile
    mock = (QML / "components" / "PhoneMock.qml").read_text(encoding="utf-8")
    assert "labelWidth" in mock


def test_ipa_sheet_where_line_comes_from_the_real_scanned_folders(tmp_path, monkeypatch) -> None:
    """Ника: «Нашли на этом компьютере: …» — only folders the scan really uses."""

    from apprestore_core.paths import ipa_search_roots

    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    monkeypatch.delenv("APPRESTORE_EXTRA_IPA_DIRS", raising=False)
    lib = tmp_path / "lib"
    roots = Fv.labelled_roots(ipa_search_roots(lib))
    labels = [label for label, _ in roots]
    assert labels[0] == "AppRestore" and "iMazing" in labels and "«Загрузках»" in labels and "iTunes" in labels
    a = str(lib / "A.ipa")
    b = str(tmp_path / "Downloads" / "B.ipa")
    assert Fv.where_line([a, b], roots) == "Нашли на этом компьютере: в папке AppRestore и «Загрузках»"
    assert Fv.where_line([], roots).startswith("Ищем на этом компьютере: в папке AppRestore, iMazing")
    v = Fv.files_view(open_=True, mode="list", library=[], export=[], note="", busy=False, connected=True)
    assert v["close"] == "Готово"


def test_ipa_sheet_one_filled_button_only() -> None:
    qml = (QML / "components" / "FilesSheet.qml").read_text(encoding="utf-8")
    row = qml[qml.index('objectName: "filesInstall"'):qml.index("onClicked: ui.filesInstall")]
    assert "Theme.surfaceSoft" in row and "color: Theme.accent;" in row and "Theme.accentHover" not in row
