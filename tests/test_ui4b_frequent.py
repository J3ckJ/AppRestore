"""«Часто ищут» (Eugene 10.10, Ника 02-picker §6b line 232, Лена LEGAL §1.13)."""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication  # noqa: E402

from apprestore_core.region_probe import RegionStatus  # noqa: E402
from apprestore_gui.ui4b import frequent  # noqa: E402
from apprestore_gui.ui4b.fake_data import FakeSource  # noqa: E402
from apprestore_gui.ui4b.qt_bridge import Restore4b  # noqa: E402

CLONES = {"6749962031", "6755181069", "6473656113"}  # «Сириус», Spotluma, «Апгрейд»
INDIVIDUALS = {"6739035108", "6760469916"}  # level A, individual developers: search only
ORIGINALS = {"492224193", "472951966", "353127685", "455652438", "564177498", "511310430", "6739530834"}


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


def test_shipped_list_is_only_the_originals_with_their_developers() -> None:
    rows = frequent.load()
    assert {r["storeId"] for r in rows} == ORIGINALS
    assert all(r["developer"] for r in rows)
    by = {r["storeId"]: r["developer"] for r in rows}
    assert by["492224193"] == "Сбербанк России" and by["472951966"] == "VTB Bank (PJSC)"
    assert by["6739530834"] == "MAX LLC"


def test_clones_never_appear_even_if_the_file_lists_them(tmp_path) -> None:
    data = {"apps": [{"track_id": int(i), "name": "X", "developer": "Y"} for i in CLONES]
            + [{"track_id": 1, "name": "No dev", "developer": ""}, {"track_id": 2, "name": "Ok", "developer": "Dev"}]}
    p = tmp_path / "f.json"
    p.write_text(json.dumps(data), encoding="utf-8")
    assert [r["storeId"] for r in frequent.load(p)] == ["2"]


def test_data_file_has_no_icons_colours_or_letters() -> None:
    raw = json.loads(frequent.DATA.read_text(encoding="utf-8"))
    for row in raw["apps"]:
        assert set(row) <= {"track_id", "name", "developer", "source"}


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
    assert rows[0] == {"kind": "header", "group": "frequent", "title": "Часто ищут", "count": 7, "note": ""}
    apps = [r for r in rows if r["kind"] == "app"]
    ids = {r["storeId"] for r in apps}
    assert ids == ORIGINALS and not ids & (CLONES | INDIVIDUALS)
    assert all(r["developer"] for r in apps)
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
