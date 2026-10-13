"""The purchase-history sheet: whole list, newest first, one restore action."""

from __future__ import annotations

from apprestore_gui.ui4b.history import ACTION, history_view


def test_history_lists_every_purchase_newest_first() -> None:
    view = history_view(
        [
            {"trackId": "2", "name": "Бета", "date": "2020-01-02"},
            {"trackId": "1", "name": "Альфа", "date": "2024-03-01"},
            {"trackId": "1", "name": "Альфа", "date": "2024-03-01"},
            {"trackId": "3", "name": "Гамма", "date": ""},
        ],
        signed_in=True,
    )
    assert [row["storeId"] for row in view["rows"]] == ["1", "2", "3"]
    assert view["rows"][0]["developer"] == "куплено 01.03.2024"
    assert view["rows"][0]["action"] == ACTION
    assert view["lead"] == "3 покупки в истории"


def test_history_filter_matches_store_id() -> None:
    view = history_view(
        [{"trackId": "564177498", "name": "VK", "bundleId": "com.vk.vkclient", "date": "2024-06-01"}],
        signed_in=True,
        query="564177498",
    )
    assert [row["storeId"] for row in view["rows"]] == ["564177498"]
    assert view["rows"][0]["bundleId"] == "com.vk.vkclient"


def test_history_filter_matches_name() -> None:
    view = history_view(
        [
            {"trackId": "10", "name": "Почта", "date": "2024-01-01"},
            {"trackId": "11", "name": "Карты", "date": "2024-01-02"},
        ],
        signed_in=True,
        query="поч",
    )
    assert [row["name"] for row in view["rows"]] == ["Почта"]
    assert view["lead"] == "Найдено 1"
    assert view["empty"] == ""


def test_history_hides_rows_until_sign_in() -> None:
    view = history_view(
        [{"trackId": "10", "name": "Почта", "date": "2024-01-01"}],
        signed_in=False,
    )
    assert view["rows"] == []
    assert view["emptyLink"] == "Войти"


def test_history_keeps_the_saved_rows_while_a_refresh_is_still_running() -> None:
    view = history_view(
        [{"trackId": "10", "name": "Почта", "date": "2024-01-01"}],
        signed_in=True,
        busy=True,
    )
    assert view["rows"][0]["name"] == "Почта"
    assert "обновляется" in str(view["lead"])
    assert view["spinner"]


def test_history_says_when_the_account_has_none() -> None:
    view = history_view([], signed_in=True)
    assert view["empty"].startswith("В этом Apple ID")
    assert view["title"] == "Восстановить из истории"
