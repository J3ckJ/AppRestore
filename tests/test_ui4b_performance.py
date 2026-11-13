"""Fresh UI state after reuse of expensive derived data."""
from dataclasses import replace
from types import SimpleNamespace

import pytest

pytest.importorskip("PySide6")

from apprestore_gui.ui4b.catalog import ACTION_STORE, GROUP_REMOVED, RestoreItem
from apprestore_gui.ui4b.qt_bridge import PickerModel, SessionSource
from apprestore_gui.ui4b.search import search_store
from apprestore_gui.ui4b.selection import Selection


def test_selection_cached_rows_follow_data_filters_marks_and_network():
    item = RestoreItem(key="one", name="One", group=GROUP_REMOVED, action=ACTION_STORE, store_id="12345678")
    selection = Selection([item], selected=[])
    first = selection.rows()
    first[-1]["name"] = "caller mutation"
    assert selection.rows()[-1]["name"] == "One"
    selection.toggle("one")
    assert selection.rows()[-1]["selected"]
    selection.set_query("absent")
    assert selection.rows() == []
    selection.set_query("")
    assert selection.rows()[-1]["selected"]
    selection.set_offline(True)
    assert not selection.rows()[-1]["selectable"]
    selection.set_offline(False)
    assert selection.rows()[-1]["selected"]
    selection.set_items([replace(item, name="Updated")])
    assert selection.rows()[-1]["name"] == "Updated"
    selection.set_items([replace(item, name="Updated")], selected=[])
    assert not selection.rows()[-1]["selected"]


def test_sticky_headers_follow_rows_updates_and_removal():
    model = PickerModel()
    model.set_rows([{"kind": "header", "key": "g", "title": "First"}, {"kind": "app", "key": "one"}])
    assert model.header_at(1)["title"] == "First"
    model.set_rows([{"kind": "header", "key": "g", "title": "Updated"}, {"kind": "app", "key": "one"}])
    assert model.header_at(1)["title"] == "Updated"
    model.set_rows([])
    assert model.header_at(1) == model.header_at(-1) == {}


def test_price_checks_are_batched_and_unknown_prices_remain_unknown(monkeypatch):
    requests = []

    def lookup(ids, country):
        requests.append((ids, country))
        return {"1": {"price": 0}, "2": {"price": float("nan")}, "3": {"price": -1},
                "4": {"price": True}, "5": {"price": 3.99}}

    monkeypatch.setattr("apprestore_gui.ui4b.qt_bridge.lookup_itunes_offers", lookup)
    source = SimpleNamespace(_account_country=lambda: "us")
    assert SessionSource.free_prices(source, ["1", "2", "3", "4", "5", "6", "1"]) == {
        "1": 0.0, "2": None, "3": None, "4": None, "5": 3.99, "6": None}
    assert requests == [(["1", "2", "3", "4", "5", "6"], "us")]


@pytest.mark.parametrize("query,limit", [("12345678", 10), ("Example", 1)])
def test_purchase_match_does_not_trigger_redundant_store_query(query, limit):
    def unexpected(*args, **kwargs):
        pytest.fail("The local purchase already satisfies the query")

    rows = search_store(query, [{"trackId": "12345678", "name": "Example"}], limit=limit,
                        itunes_search=unexpected, itunes_lookup=unexpected)
    assert rows == [{"storeId": "12345678", "name": "Example", "source": "purchases"}]
