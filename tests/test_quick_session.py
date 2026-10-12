from pathlib import Path

import pytest

from apprestore_core.models import Device, InstalledApp, IpaMetadata, OffloadedApp
from apprestore_gui.quick_session import library_card, offloaded_card, phone_card


@pytest.mark.parametrize(
    "acquire,purchase_started,attempts,phase",
    [(False, False, 1, "download"), (True, True, 1, "license_request"), (True, True, 2, "licensed_download")],
)
def test_store_failure_tracks_the_operation_not_just_the_error_text(
    monkeypatch: pytest.MonkeyPatch, acquire: bool, purchase_started: bool, attempts: int, phase: str
) -> None:
    from types import SimpleNamespace
    from apprestore_gui import quick_session as qs
    from apprestore_gui.errors import StoreOperationError

    calls = []
    core = SimpleNamespace(
        tools=SimpleNamespace(account_country=lambda: "ru"),
        restore_by_store_id=lambda udid, sid: calls.append((udid, sid)),
    )
    session = SimpleNamespace(service=SimpleNamespace(core=core), installProgress=SimpleNamespace(emit=lambda *_: None))

    def gate(sid, attempt, **kwargs):
        attempt()
        if purchase_started:
            kwargs["notify"]("Лицензия")
        if attempts > 1:
            attempt()
        raise RuntimeError("received error: 2059")

    monkeypatch.setattr(qs, "run_with_free_license", gate)
    with pytest.raises(StoreOperationError) as error:
        qs.QuickSession._restore_store_gated(session, "UDID", "1117817509", acquire)
    assert f"этап: {phase}" in str(error.value)
    assert "temporarily_unavailable" in str(error.value)
    assert len(calls) == attempts
    assert "для страны" not in str(error.value)
    assert error.value.__cause__ is not None


def test_reread_of_the_same_phone_keeps_the_offloaded_list(monkeypatch: pytest.MonkeyPatch) -> None:
    pytest.importorskip("PySide6.QtWidgets")
    from PySide6.QtWidgets import QApplication

    from apprestore_gui import quick_session as qs

    QApplication.instance() or QApplication([])
    monkeypatch.setattr(qs, "load_bindings", lambda: {})
    session = qs.QuickSession()
    app = OffloadedApp(bundle_id="app.one", name="One", version="1", store_id="1")
    session._rows = [qs.offloaded_card(app)]
    session._by_key = {app.bundle_id: app}
    session._loaded_udid = "UDID"
    session._udid = "UDID"
    session._ready = True
    session._force = True
    session._device_cache["UDID"] = Device(udid="UDID")
    seen: list[int] = []

    def offloaded(_udid: str) -> list[OffloadedApp]:
        seen.append(len(session.offloaded_snapshot()))
        return [app]

    session.service.connected_udids = lambda: ["UDID"]  # type: ignore[method-assign]
    session.service.offloaded = offloaded  # type: ignore[method-assign]
    session._poll_once()
    assert seen == [1]


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
