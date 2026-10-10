from pathlib import Path

from apprestore_core.models import InstalledApp, IpaMetadata, OffloadedApp
from apprestore_gui.quick_session import library_card, offloaded_card, phone_card


def test_offloaded_card_uses_bundle_id_as_the_window_key() -> None:
    card = offloaded_card(
        OffloadedApp(
            bundle_id="ru.sberbankmobile",
            name="СберБанк",
            version="17.6.1",
            store_id="492224193",
        )
    )
    assert card["storeId"] == "ru.sberbankmobile"
    assert card["name"] == "СберБанк"
    assert card["detail"] == "17.6.1"
    assert card["mark"] == ""  # never a letter
    assert card["color"].startswith("#")
    assert card["ink"].startswith("#")


def test_phone_card_keeps_the_store_id_and_bundle() -> None:
    card = phone_card(
        InstalledApp(
            bundle_id="app.cleanorder.hudorban",
            name="Spotluma",
            version="2.8.2",
            store_id="6755181069",
        )
    )
    assert card["bundleId"] == "app.cleanorder.hudorban"
    assert card["storeId"] == "6755181069"
    assert card["name"] == "Spotluma"
    assert card["detail"] == "2.8.2"


def test_phone_card_without_a_store_id_cannot_pretend_to_be_a_download() -> None:
    card = phone_card(InstalledApp(bundle_id="local.sideload", name="Своё", version="1"))
    assert card["storeId"] == ""
    assert card["bundleId"] == "local.sideload"


def test_library_card_points_at_the_ipa_file() -> None:
    card = library_card(
        IpaMetadata(
            path=Path("Spotluma-2.8.2.ipa"),
            bundle_id="app.cleanorder.hudorban",
            name="Spotluma",
            version="2.8.2",
            size=10,
        )
    )
    assert card["path"] == "Spotluma-2.8.2.ipa"
    assert card["bundleId"] == "app.cleanorder.hudorban"
    assert card["placed"] is False


def test_offloaded_card_falls_back_when_the_version_is_unknown() -> None:
    card = offloaded_card(OffloadedApp(bundle_id="ru.example", name="  ", version="?"))
    assert card["name"] == "ru.example"
    assert card["detail"] == "сгружено"
    assert card["mark"] == ""
