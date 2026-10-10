"""«Часто ищут» (Ника 02-picker §6b line 232; Eugene's decision 10.10: all 33 of b3f869a)."""

from __future__ import annotations

import json

import pytest

pytest.importorskip("PySide6")


import subprocess
from pathlib import Path

from apprestore_core.region_probe import RegionStatus
from apprestore_gui.ui4b import frequent
from apprestore_gui.ui4b.fake_data import FakeSource
from apprestore_gui.ui4b.qt_bridge import Restore4b

REPO = Path(__file__).resolve().parents[1]


def _b3f869a() -> list[dict]:
    """POPULAR_APPS exactly as in commit b3f869a (Eugene's decision 10.10)."""

    try:
        src = subprocess.run(["git", "show", "b3f869a:apprestore_gui/popular_apps.py"], cwd=REPO,
                             capture_output=True, text=True, check=True).stdout
    except (OSError, subprocess.CalledProcessError):
        pytest.skip("b3f869a is not in this checkout")
    ns: dict = {}
    exec(compile(src, "popular_apps@b3f869a", "exec"), ns)  # noqa: S102 — our own file at a fixed commit
    return list(ns["POPULAR_APPS"])


def test_frequent_json_is_exactly_the_33_entries_of_b3f869a() -> None:
    want = _b3f869a()
    assert len(want) == 33
    raw = json.loads(frequent.DATA.read_text(encoding="utf-8"))["apps"]
    assert [(str(r["track_id"]), r["name"], r["detail"]) for r in raw] == [
        (a["storeId"], a["name"], a["detail"]) for a in want]
    got = frequent.load()
    assert [r["storeId"] for r in got] == [a["storeId"] for a in want]
    assert [r["name"] for r in got] == [a["name"] for a in want]
    ids = {r["storeId"]: r["name"] for r in got}
    assert ids["6749962031"] == "ВТБ" and ids["6755181069"] == "Т-Банк" and ids["6473656113"] == "Альфа"


def test_data_file_has_no_icons_colours_letters_or_sites() -> None:
    raw = json.loads(frequent.DATA.read_text(encoding="utf-8"))
    for row in raw["apps"]:
        assert set(row) <= {"track_id", "name", "detail", "developer", "developer_source"}
    assert "ICON_SITES" not in (REPO / "apprestore_gui" / "ui4b" / "frequent.py").read_text(encoding="utf-8")


def test_developer_only_from_trusted_data() -> None:
    from apprestore_core import delisted_search as ds

    builtin = {str(e.track_id): e.developer for e in ds.BUILTIN}
    raw = json.loads(frequent.DATA.read_text(encoding="utf-8"))["apps"]
    for row in raw:
        sid, dev = str(row["track_id"]), row["developer"]
        if sid in builtin:
            assert dev == builtin[sid]
        if dev:
            assert row.get("developer_source"), sid  # every developer says where it came from
    by = {str(r["track_id"]): r["developer"] for r in raw}
    assert by["6749962031"] == "Sergei Smirnov"  # the real developer of «Сириус», as is
    assert by["1406492297"] == ""  # unknown → no line


def test_loader_keeps_order_and_allows_rows_without_developer(tmp_path) -> None:
    data = {"apps": [{"track_id": 2, "name": "B", "developer": ""}, {"track_id": 1, "name": "A", "developer": "Dev"},
                     {"track_id": 2, "name": "dup"}, {"track_id": "x", "name": "bad"}]}
    p = tmp_path / "f.json"
    p.write_text(json.dumps(data), encoding="utf-8")
    assert [(r["storeId"], r["developer"]) for r in frequent.load(p)] == [("2", ""), ("1", "Dev")]


def _controller(src=None):
    src = src or FakeSource("missing")
    return Restore4b(src, find_options={"delisted": lambda q, net: ([], False), "spawn": lambda f: f()}), src


def test_empty_field_shows_frequent_rows_like_results_typing_replaces_clearing_returns(qapp) -> None:
    src = FakeSource("missing")
    src.find_statuses = {"472951966": RegionStatus.DELISTED}
    src.find_store = [{"storeId": "7", "name": "Погода", "source": "appstore"}]
    src.find_offers = {"7": {"price": 0.0, "developer": "Dev"}}
    c, _ = _controller(src)
    c.finder.openWith("")
    rows = c.finder.view["rows"]
    assert rows[0] == {"kind": "header", "group": "frequent", "title": "Часто ищут", "count": 33, "note": ""}
    apps = [r for r in rows if r["kind"] == "app"]
    assert [r["storeId"] for r in apps] == [r["storeId"] for r in frequent.load()]
    assert not {"6739035108", "6760469916"} & {r["storeId"] for r in apps}  # level A individuals: search only
    assert all(set(r) >= {"name", "developer", "action", "actionNote", "iconUrl"} for r in apps)
    assert all(r["iconUrl"] == "" for r in apps)  # icons only via icons_cache (Apple) / placeholder
    c.finder.search("погода")
    titles = [r.get("title") for r in c.finder.view["rows"] if r["kind"] == "header"]
    assert "Часто ищут" not in titles
    c.finder.search("")
    assert c.finder.view["rows"][0]["title"] == "Часто ищут"


def test_frequent_install_goes_the_usual_path(qapp) -> None:
    src = FakeSource("missing")
    src.owned = {"492224193"}
    c, _ = _controller(src)
    started = []
    c._start = lambda items: started.append(items)
    c.finder.openWith("")
    sber = [r for r in c.finder.view["rows"] if r.get("storeId") == "492224193"][0]
    assert sber["action"] == "Поставить"
    c.finder.install("492224193", sber["name"])
    assert started and started[0][0].store_id == "492224193"


def test_frequent_row_goes_only_through_the_gate(qapp, tmp_path) -> None:
    """Лена: a «Часто ищут» row is installed exactly like «Поставить» elsewhere —
    consent first, then license_gate (preflight, limits, journal). No purchase
    happens before the choice, and none outside run_with_free_license."""

    from apprestore_core.license_guard import read_counts
    from tests.test_ui4b_qml import _gated_source, wait

    source, journal, bought, notes = _gated_source(tmp_path, scenario="missing")
    sid = "6749962031"  # «ВТБ» in the list: not on the account, price 0 in the fake lookup
    source.prices[sid] = 0.0
    source.find_offers = {sid: {"price": 0.0, "developer": ""}}
    source.owned = set(source.owned or ()) - {sid}
    c = Restore4b(source, find_options={"delisted": lambda q, net: ([], False), "spawn": lambda f: f()})
    c.finder.openWith("")
    row = [r for r in c.finder.view["rows"] if r.get("storeId") == sid][0]
    assert row["action"] == "Поставить"
    before = read_counts(journal)
    c.finder.install(sid, row["name"])
    qapp.processEvents()
    assert c.consent["open"] and bought == [] and not [x for x in source.calls if x[0] == "install_store"]
    c.consentContinue()
    wait(qapp, lambda: not c.flow.running)
    assert bought == [sid]  # only via Tools.purchase_license inside run_with_free_license
    assert [x for x in source.calls if x[1:] == (sid,)] == [("install_store", sid)]
    assert read_counts(journal) != before  # the journal saw it (the gate's limit accounting)
    assert any("Apple ID" in n for n in notes)  # the gate's notification


def test_no_official_word_for_these_rows() -> None:
    from apprestore_gui.ui4b import find

    text = frequent.DATA.read_text(encoding="utf-8") + (REPO / "apprestore_gui" / "ui4b" / "frequent.py").read_text(
        encoding="utf-8") + str(find.FREQUENT_TITLE)
    assert "официальн" not in text.casefold()


def test_icons_only_from_apple_cache_or_placeholder() -> None:
    sheet = (REPO / "apprestore_gui" / "qml4b" / "components" / "FindSheet.qml").read_text(encoding="utf-8")
    assert "storeId: r.storeId" in sheet  # AppIcon → iconBook (icons_cache, Apple artwork) or iconPlaceholder
    icon = (REPO / "apprestore_gui" / "qml4b" / "components" / "AppIcon.qml").read_text(encoding="utf-8")
    assert "iconPlaceholder" in icon
    for path in (REPO / "apprestore_gui" / "ui4b").glob("*.py"):
        assert "ICON_SITES" not in path.read_text(encoding="utf-8"), path


def test_window_queues_find_icons_without_a_site() -> None:
    src = (REPO / "apprestore_gui" / "ui4b" / "window.py").read_text(encoding="utf-8")
    assert "controller.finder.changed.connect(queue_icons)" in src
    assert 'items.append((str(row["storeId"]), "", ""))' in src  # site = "" → no favicon path
