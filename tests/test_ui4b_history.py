"""The purchase-history sheet: whole list, newest first, one restore action."""

from __future__ import annotations

from apprestore_gui.ui4b.history import ACTION, ID_ACTION, history_view, name_for_store_id, parse_store_id


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


def test_short_vk_query_does_not_match_letters_inside_another_word() -> None:
    view = history_view(
        [
            {"trackId": "1", "name": "Доставка продуктов", "date": "2024-01-01"},
            {"trackId": "2", "name": "Азбука вкуса", "date": "2024-01-02"},
            {"trackId": "3", "name": "VK", "bundleId": "com.vk.vkclient", "date": "2024-01-03"},
            {"trackId": "4", "name": "ВКонтакте", "date": "2024-01-04"},
        ],
        signed_in=True,
        query="ВК",
    )
    assert [row["name"] for row in view["rows"]] == ["ВКонтакте", "VK"]


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


def test_store_id_comes_from_digits_or_one_app_store_link() -> None:
    assert parse_store_id("492224193") == "492224193"
    assert parse_store_id("492 224 193") == "492224193"
    assert parse_store_id("id492224193") == "492224193"
    assert parse_store_id("https://apps.apple.com/ru/app/id492224193?mt=8") == "492224193"
    assert parse_store_id("привет") == ""
    assert parse_store_id("123") == ""
    assert parse_store_id("") == ""


def test_known_history_id_keeps_its_name() -> None:
    items = [{"trackId": "492224193", "name": "СберБанк"}, {"trackId": "10", "name": "10"}]
    assert name_for_store_id(items, "492224193") == "СберБанк"
    assert name_for_store_id(items, "10") == ""
    assert name_for_store_id(items, "11111111") == ""


def test_signed_in_history_offers_an_id_download() -> None:
    view = history_view([], signed_in=True, id_note="Нужен ID")
    assert view["signedIn"] is True
    assert view["idAction"] == ID_ACTION
    assert view["idPlaceholder"]
    assert view["idNote"] == "Нужен ID"
    unsigned = history_view([], signed_in=False, id_note="Нужен ID")
    assert unsigned["signedIn"] is False
    assert unsigned["idNote"] == ""


def test_history_says_when_the_account_has_none() -> None:
    view = history_view([], signed_in=True)
    assert view["empty"].startswith("В этом Apple ID")
    assert view["title"] == "Восстановить из истории"
