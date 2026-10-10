"""«Найти» (02-picker §6b), «Настройки» and Apple's refusal (error-apple-rejected)."""

from __future__ import annotations

import socket
from types import SimpleNamespace

import pytest

pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication  # noqa: E402

from apprestore_core.region_probe import RegionStatus  # noqa: E402
from apprestore_gui.ui4b import find as F  # noqa: E402
from apprestore_gui.ui4b.fake_data import FakeIconBook, FakeSource  # noqa: E402
from apprestore_gui.ui4b.flow import APPLE_REJECTED_NOTE, is_apple_rejected  # noqa: E402
from apprestore_gui.ui4b.home import APPLE_REJECTED_LEAD, STATE_APPLE_REJECTED, STATE_STORE_MISMATCH  # noqa: E402
from apprestore_gui.ui4b.qt_bridge import Restore4b  # noqa: E402
from apprestore_gui.ui4b.settings import ARCHIVE_NOTE  # noqa: E402

SIRIUS, ARCH, VK = "6749962031", "111222333", "564177498"


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


def hit(tid, name, dev, src="builtin", conf=0.95, snap=None, dib=None, brand="ВТБ Онлайн"):
    return SimpleNamespace(track_id=int(tid), name=name, developer=dev, icon_url=None,
                           source=SimpleNamespace(value=src), confidence=conf, snapshot=snap,
                           brand=brand, developer_is_bank=dib)


class Delisted:
    """Stands in for Макс's search_delisted; records allow_network."""

    def __init__(self, builtin=(), archive=(), down=False):
        self.builtin, self.archive, self.down = list(builtin), list(archive), down
        self.calls: list[tuple[str, bool]] = []

    def __call__(self, query, allow_network):
        self.calls.append((query, allow_network))
        if allow_network:
            return self.builtin + self.archive, self.down
        return list(self.builtin), False


def controller(source=None, delisted=None, **kw):
    source = source or FakeSource("missing")
    c = Restore4b(source, find_options={"delisted": delisted or Delisted(), "spawn": lambda f: f(),
                                        "timeout_s": kw.pop("timeout_s", 12.0)}, **kw)
    return c, source


def rows(c, kind="app"):
    return [r for r in c.finder.view["rows"] if r["kind"] == kind]


# -- pure rules ---------------------------------------------------------------------


def test_snapshot_date_genitive_and_alias_detection() -> None:
    assert F.snapshot_date("20240312") == "12 марта 2024"
    assert F.snapshot_date("2024031") == "" and F.snapshot_date(None) == ""
    assert F.found_by_alias("сириус", "Cириус")  # Latin C in the store name
    assert not F.found_by_alias("втб", "ВТБ Онлайн")


def test_weak_and_snapshotless_archive_hits_are_hidden_not_grey() -> None:
    hits = [F.hit_from(h) for h in (
        hit(1, "Слабый", "Dev", conf=0.4),
        hit(2, "Без снимка", "Dev", "wayback", 0.8, None),
        hit(3, "Без разработчика", None, "wayback", 0.8, "20240101"),
        hit(4, "Хороший", "Dev", "wayback", 0.8, "20240101"),
    )]
    assert [h.name for h in F.visible_hits(hits)] == ["Хороший"]


def test_builtin_row_lines_bank_then_alias_and_never_brand(qapp) -> None:
    c, src = controller(delisted=Delisted([hit(SIRIUS, "Cириус", "Sergei Smirnov", dib=False)]))
    src.find_statuses = {SIRIUS: RegionStatus.DELISTED}
    c.finder.openWith("сириус")
    (row,) = rows(c)
    assert row["name"] == "Cириус" and row["developer"] == "Sergei Smirnov"  # real developer as is
    assert row["tag"] == "Удалено из App Store"
    assert row["line3"] == "Ссылку на это приложение публиковал банк · Найдено по запросу „сириус“"
    assert "ВТБ" not in repr(c.finder.view)  # brand nowhere
    assert "снимок" not in row["line3"]  # builtin: no date


def test_archive_row_has_date_and_alias_but_never_the_bank_line(qapp) -> None:
    arch = hit(ARCH, "Демо Банк Онлайн", "Demo LLC", "wayback", 0.8, "20240312", dib=False)
    c, src = controller(delisted=Delisted(archive=[arch]))
    c.finder.openWith("демо")
    (row,) = rows(c)
    assert row["line3"] == "Из архива · снимок 12 марта 2024"
    c.finder.search("demo bank")
    (row,) = rows(c)
    assert row["line3"] == "Из архива · снимок 12 марта 2024 · Найдено по запросу „demo bank“"
    assert "банк публиковал" not in row["line3"].casefold() and F.BANK_LINK_NOTE not in row["line3"]


def test_deleted_tag_only_after_region_probe_available_is_a_normal_store_row(qapp) -> None:
    d = Delisted([hit(SIRIUS, "Cириус", "Sergei Smirnov")])
    c, src = controller(delisted=d)
    c.finder.openWith("сириус")
    assert rows(c)[0]["tag"] == ""  # not checked: no «Удалено»
    src.find_statuses = {SIRIUS: RegionStatus.AVAILABLE}
    c.finder.search("сириус")
    head = rows(c, "header")
    assert [h["title"] for h in head] == ["В App Store"] and rows(c)[0]["tag"] == ""


def test_order_groups_dedupe_and_archive_only_as_fallback(qapp) -> None:
    d = Delisted([hit(SIRIUS, "Cириус", "Sergei Smirnov")], [hit(ARCH, "Сириус Архив", "X", "wayback", 0.8, "20240101")])
    c, src = controller(delisted=d)
    src.find_store = [{"storeId": VK, "name": "Сириус ВК", "source": "appstore"},
                      {"storeId": SIRIUS, "name": "Сириус", "source": "purchases"}]
    c.finder.openWith("сириус")
    head = [h["title"] for h in rows(c, "header")]
    assert head == ["В App Store", "Ваши покупки"]  # the builtin hit is already in purchases
    assert [q for q, net in d.calls if net] == []  # something found: no archive
    assert c.finder.view["footnote"] == ""


def test_archive_runs_only_when_all_empty_and_switch_on(qapp) -> None:
    d = Delisted(archive=[hit(ARCH, "Редкое", "Dev", "wayback", 0.8, "20240101")])
    c, src = controller(delisted=d)
    c.finder.openWith("редкое")
    assert ("редкое", True) in d.calls
    assert c.finder.view["footnote"] == ARCHIVE_NOTE
    assert [h["title"] for h in rows(c, "header")] == ["Удалено из App Store"]
    assert rows(c, "header")[0]["note"] == F.DELISTED_NOTE
    # switch off: no request with network at all, no footnote, the settings link in empty
    d.calls.clear()
    c.setArchiveSearch(False)
    c.finder.search("редкое")
    assert all(not net for _, net in d.calls)
    v = c.finder.view
    assert v["footnote"] == "" and v["empty"] == F.EMPTY_TEXT and v["emptyLink"] == F.ARCHIVE_LINK


def test_no_request_to_web_archive_when_switch_off_with_maks_module(qapp, monkeypatch) -> None:
    """Handoff test: with allow_network=False nothing goes to web.archive.org."""

    def no_network(*a, **k):
        raise AssertionError("network used")

    monkeypatch.setattr(socket, "create_connection", no_network)
    mod = F.load_module()
    if mod is None:
        pytest.skip("delisted_search not vendored yet")
    seen = []
    engine = mod.DelistedSearch(fetcher=lambda url, h, t: seen.append(url) or b"[]")
    monkeypatch.setattr(mod, "_default", engine, raising=False)
    c, src = controller(delisted=None, archive_search=False)
    c.finder.openWith("демо банк")
    assert seen == []


def test_spinner_while_archive_busy_and_late_answers_are_dropped(qapp) -> None:
    pending = []
    d = Delisted(archive=[hit(ARCH, "Редкое", "Dev", "wayback", 0.8, "20240101")])
    src = FakeSource("missing")
    c = Restore4b(src, find_options={"delisted": d, "spawn": lambda f: pending.append(f)})
    c.finder.openWith("редкое")
    pending.pop(0)()  # store search
    assert c.finder.view["spinner"] == F.SEARCHING_ARCHIVE
    old = pending.pop(0)
    c.finder.search("другое")  # new input cancels
    old()
    assert c.finder.view["spinner"] == "" and rows(c) == []


def test_archive_timeout_and_down_banner(qapp) -> None:
    d = Delisted(down=True)
    c, _ = controller(delisted=d)
    c.finder.openWith("редкое")
    v = c.finder.view
    assert v["banner"] == F.ARCHIVE_DOWN and v["spinner"] == ""
    pending = []
    c2 = Restore4b(FakeSource("missing"), find_options={"delisted": Delisted(), "spawn": pending.append})
    c2.finder.openWith("редкое")
    pending.pop(0)()
    c2.finder._archive_timeout(c2.finder._gen)
    assert c2.finder.view["banner"] == F.ARCHIVE_DOWN and c2.finder.view["spinner"] == ""


def test_actions_owned_free_paid_unknown_component_and_offline(qapp) -> None:
    src = FakeSource("missing")
    src.owned = {"1"}
    src.find_store = [{"storeId": s, "name": f"App {s}", "source": "appstore"} for s in ("1", "2", "3", "4")]
    src.find_offers = {"2": {"price": 0.0, "developer": "Dev2"}, "3": {"price": 2.99, "developer": "Dev3"}}
    c, _ = controller(source=src)
    c.finder.openWith("app")
    by = {r["storeId"]: r for r in rows(c)}
    assert by["1"]["action"] == "Поставить" and by["2"]["action"] == "Поставить"
    assert by["2"]["developer"] == "Dev2"
    assert by["3"]["actionNote"] == F.PAID_NOTE and by["3"]["action"] == ""
    assert by["4"]["actionNote"] == F.UNKNOWN_PRICE
    src.patches = ("0001",)
    c._on_source()
    c.finder.search("app")
    by = {r["storeId"]: r for r in rows(c)}
    assert by["1"]["action"] == "Поставить"  # owned needs no new license
    assert by["2"]["actionNote"] == F.COMPONENT_NOTE and by["2"]["actionLink"] == "Как установить"
    src.patches = ()
    src.online = False
    c._on_source()
    c.finder.search("app")
    v = c.finder.view
    assert v["banner"] == F.OFFLINE_BANNER and all(not r["enabled"] for r in rows(c))


def test_empty_field_is_placeholder_only_no_suggestions(qapp) -> None:
    d = Delisted([hit(SIRIUS, "Cириус", "Sergei Smirnov")])
    c, _ = controller(delisted=d)
    c.finder.openWith("")
    v = c.finder.view
    assert v["rows"] == [] and v["empty"] == "" and d.calls == []


def test_install_goes_the_usual_path_and_closes_the_sheet(qapp) -> None:
    src = FakeSource("missing")
    src.find_store = [{"storeId": "2", "name": "App 2", "source": "appstore"}]
    src.find_offers = {"2": {"price": 0.0, "developer": "D"}}
    c, _ = controller(source=src)
    started = []
    c._start = lambda items: started.append(items)  # consent → gate is _start's job
    c.finder.openWith("app")
    c.finder.install("2", "App 2")
    assert not c.finder.open and started and started[0][0].store_id == "2"


def test_find_sheet_and_settings_load_without_warnings(qapp) -> None:
    from PySide6.QtQml import QQmlApplicationEngine

    from apprestore_gui.ui4b.window import load

    d = Delisted([hit(SIRIUS, "Cириус", "Sergei Smirnov", dib=False)])
    c, src = controller(delisted=d)
    engine = QQmlApplicationEngine()
    warnings: list[str] = []
    engine.warnings.connect(lambda ws: warnings.extend(w.toString() for w in ws))
    icons = FakeIconBook()
    assert load(engine, c, icons)
    c.link("Найти другое приложение")
    assert c.finder.open
    c.finder.search("сириус")
    for _ in range(3):
        qapp.processEvents()
    c.finder.close()
    c.openSettings()
    for _ in range(3):
        qapp.processEvents()
    window = engine.rootObjects()[0]
    window.close()
    del window, engine
    qapp.processEvents()
    assert warnings == []


# -- settings -------------------------------------------------------------------------


def test_settings_archive_switch_persists_and_updates_check(qapp) -> None:
    saved = []
    info = SimpleNamespace(newer=False, current="1.2.0", latest="1.2.0")
    c, _ = controller(update_check=lambda: info)
    c.archiveSearchChanged.connect(saved.append)
    c.link("Настройки")
    s = c.settings
    assert s["open"] and s["archive"] and s["archiveNote"] == ARCHIVE_NOTE
    c.setArchiveSearch(False)
    assert saved == [False] and not c.settings["archive"]
    c.checkUpdates()
    import time

    for _ in range(50):
        qapp.processEvents()
        if not c.settings["updateBusy"]:
            break
        time.sleep(0.01)
    assert c.settings["updateStatus"] == "У вас последняя версия (1.2.0). На GitHub сейчас 1.2.0."
    failed = Restore4b(FakeSource("missing"), update_check=lambda: (_ for _ in ()).throw(OSError("x")))
    failed.checkUpdates()
    for _ in range(50):
        qapp.processEvents()
        if not failed.settings["updateBusy"]:
            break
        time.sleep(0.01)
    assert failed.settings["updateStatus"].startswith("Не удалось проверить обновления")


def test_windows_settings_link_and_mac_menu_in_qml() -> None:
    from apprestore_gui.ui4b.window import QML_DIR

    hero = (QML_DIR / "components" / "HomeHero.qml").read_text(encoding="utf-8")
    main = (QML_DIR / "Main.qml").read_text(encoding="utf-8")
    menu = (QML_DIR / "components" / "MacMenu.qml").read_text(encoding="utf-8")
    assert 'Theme.isWin && (root.view.links || []).indexOf("Apple ID") >= 0 ? ["Настройки"]' in hero
    assert '"Ctrl+,"' in main and "PreferencesRole" in menu and "Настройки…" in menu


# -- Apple's refusal ---------------------------------------------------------------


def test_apple_rejected_vs_store_mismatch_mapping() -> None:
    from apprestore_core.ipatool_api import MESSAGES_RU, ErrorCode
    from apprestore_core.license_gate import LIMIT_REFUSAL_TEXT, NEEDS_PATCHED_IPATOOL_TEXT
    from apprestore_gui.errors import STORE_REFUSED_TEXT

    for text in (STORE_REFUSED_TEXT, MESSAGES_RU[ErrorCode.APPLE_REJECTED],
                 "failed to purchase item with param 'STDQ': Purchase of this item is not currently available",
                 '{"failureType": 2040}'):
        assert is_apple_rejected(text), text
    for text in (MESSAGES_RU[ErrorCode.STORE_MISMATCH], "Account Not In This Store",
                 LIMIT_REFUSAL_TEXT, NEEDS_PATCHED_IPATOOL_TEXT, "Нет связи с Apple", ""):
        assert not is_apple_rejected(text), text


def test_apple_rejected_screen_mark_in_memory_no_region_group_no_retry(qapp, tmp_path, monkeypatch) -> None:
    from apprestore_gui.errors import STORE_REFUSED_TEXT

    monkeypatch.setenv("HOME", str(tmp_path))
    src = FakeSource("missing")
    c, _ = controller(source=src)
    alfa = [i for i in c.selection.items if i.store_id][3]
    c.primaryAction()
    for _ in range(5):
        qapp.processEvents()
    assert c.flow.running
    src.installSettled.emit(alfa.store_id, False, STORE_REFUSED_TEXT)
    home = c.home
    assert home["state"] == STATE_APPLE_REJECTED
    assert home["lead"] == APPLE_REJECTED_LEAD and home["cta"] == "На главный" and not home.get("cta2")
    assert not c.flow.running  # dropped, nothing retried
    assert src.calls.count(("install_store", alfa.store_id)) <= 1
    c.primaryAction()  # «На главный»
    assert c.home["state"] != STATE_APPLE_REJECTED
    item = next(i for i in c.selection.items if i.store_id == alfa.store_id)
    assert item.note == APPLE_REJECTED_NOTE and item.group != "region" and not item.selectable
    # «Найти» shows the same mark without a button
    src.find_store = [{"storeId": alfa.store_id, "name": alfa.name, "source": "appstore"}]
    src.find_offers = {alfa.store_id: {"price": 0.0, "developer": ""}}
    c.finder.openWith(alfa.name)
    (row,) = rows(c)
    assert row["actionNote"] == APPLE_REJECTED_NOTE and row["action"] == ""
    # memory only: nothing about it on disk
    assert not any(APPLE_REJECTED_NOTE in p.read_text(errors="ignore") for p in tmp_path.rglob("*") if p.is_file())


def test_store_mismatch_keeps_its_own_screen(qapp) -> None:
    from apprestore_core.ipatool_api import MESSAGES_RU, ErrorCode

    src = FakeSource("missing")
    c, _ = controller(source=src)
    sid = [i for i in c.selection.items if i.store_id][0].store_id
    c.primaryAction()
    for _ in range(5):
        qapp.processEvents()
    assert c.flow.running
    src.installSettled.emit(sid, False, MESSAGES_RU[ErrorCode.STORE_MISMATCH])
    assert c.home["state"] == STATE_STORE_MISMATCH and c.home.get("cta2") == "Войти заново"
    assert c.flow.apple_rejected == set()


def test_real_module_builtin_level_a_rows_are_data_driven(qapp, monkeypatch) -> None:
    """Макс's delisted_search offline (BUILTIN_STRICT default untouched): rows come
    from the module's data; the UI has no app names of its own."""

    def no_network(*a, **k):
        raise AssertionError("network used")

    monkeypatch.setattr(socket, "create_connection", no_network)
    mod = F.load_module()
    if mod is None:
        pytest.skip("delisted_search not vendored")
    c, src = controller(delisted=None, archive_search=False)
    hits, down = F.run_delisted("альфа", False)
    assert not down and hits
    by_bank = {h.name: h.developer_is_bank for h in hits}
    c.finder.openWith("альфа")
    for row in rows(c):
        bank_line = F.BANK_LINK_NOTE in row["line3"]
        assert bank_line == (by_bank.get(row["name"]) is False)
        assert row["developer"]  # always the real developer
    from pathlib import Path

    for name in ("find.py", "find_qt.py"):
        text = (Path(F.__file__).parent / name).read_text(encoding="utf-8")
        for word in ("ВТБ", "Альфа", "Сириус", "Cириус", "Делим", "Drive Transit", "BUILTIN_STRICT"):
            assert word not in text, (name, word)


def test_same_name_archive_hits_show_each_developer_order_by_score_no_badges(qapp) -> None:
    """Лена: three «Cириус» in the archive — developer on each, order only by the
    match score, no «настоящее/официальное» marks; region label unchanged."""

    arch = [hit(1001, "Cириус", "B Dev", "wayback", 0.7, "20250101"),
            hit(1002, "Cириус", "A Dev", "wayback", 0.8, "20250605"),
            hit(1003, "Cириус", "C Dev", "wayback", 0.65, "20240101")]
    c, src = controller(delisted=Delisted(archive=arch))
    src.find_statuses = {"1001": RegionStatus.DELISTED, "1002": RegionStatus.DELISTED, "1003": RegionStatus.DELISTED}
    c.finder.openWith("сириус")
    got = rows(c)
    assert [r["developer"] for r in got] == ["A Dev", "B Dev", "C Dev"]
    assert {r["tag"] for r in got} == {"Удалено из App Store"}
    text = repr(c.finder.view).casefold()
    for word in ("официальн", "настоящ", "оригинал", "проверен"):
        assert word not in text
