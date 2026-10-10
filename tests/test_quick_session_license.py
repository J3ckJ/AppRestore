"""QuickSession paths: probe never purchases; «Поставить» and «Сохранить копии»
take a license (separate ``ipatool purchase``) only for price==0 within the
limit, journal it, and every download stays without --purchase."""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

pytest.importorskip("PySide6")

from apprestore_core.models import DeviceAppState, InstalledApp  # noqa: E402
from apprestore_core.service import AppRestoreService  # noqa: E402
from apprestore_core.tools import InstallRequestState, ToolUnavailable  # noqa: E402
from apprestore_core import license_gate  # noqa: E402
from apprestore_core.license_gate import LICENSE_NOTICE, LicenseDenied  # noqa: E402
from apprestore_gui.quick_session import QuickSession  # noqa: E402
from tests.helpers import make_ipa  # noqa: E402

STORE = "1234567890"


class StoreTools:
    """Fake ipatool: no license until purchase_license."""

    def __init__(self, ipa: Path, *, owned: bool = False, country: str = "ru") -> None:
        self.ipa = ipa
        self.owned = owned
        self.country = country
        self.downloads = 0
        self.purchases: list[str] = []

    def ipatool_authenticated(self) -> bool:
        return True

    def account_country(self) -> str:
        return self.country

    def purchase_license(self, store_id, *, grant) -> dict:
        from apprestore_core.purchase_grant import require_grant

        require_grant(grant, store_id)
        self.purchases.append(str(store_id))
        self.owned = True
        return {"success": True}

    def download_ipa(self, output: Path, *, bundle_id=None, store_id=None) -> bool:
        self.downloads += 1
        if not self.owned:
            raise ToolUnavailable("license is required")
        output.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(self.ipa, output)
        return True

    def install_ipa(self, _udid: str, _ipa: Path) -> InstallRequestState:
        return InstallRequestState.COMPLETED

    def device_app_state(self, _udid: str, _bundle_id: str) -> DeviceAppState:
        return DeviceAppState.INSTALLED

    def device_app_snapshot(self, _udid: str, _bundle_id: str):
        return DeviceAppState.INSTALLED, "1.0"


class Emitter:
    def __init__(self) -> None:
        self.values: list[tuple] = []

    def emit(self, *args: object) -> None:
        self.values.append(args)


@pytest.fixture()
def env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    journal = tmp_path / "licenses_acquired.jsonl"
    monkeypatch.setenv("APPRESTORE_LICENSE_JOURNAL", str(journal))
    price = {"value": 0.0}
    monkeypatch.setattr(
        license_gate,
        "lookup_offer",
        lambda s, countries=None: {
            "storeId": s, "bundleId": "com.example.alpha", "price": price["value"],
            "country": (countries or ("ru",))[0],
        },
    )
    with patch("apprestore_core.service.lookup_itunes_store_id", return_value=None), patch(
        "apprestore_core.service.remember_known_app"
    ):
        yield SimpleNamespace(tmp=tmp_path, journal=journal, price=price)


def _session(env, *, owned: bool = False):
    tools = StoreTools(make_ipa(env.tmp / "good.ipa"), owned=owned)
    core = AppRestoreService(tools=tools, library=env.tmp / "lib", cache=env.tmp / "cache")  # type: ignore[arg-type]

    def download_to_library(app, *, acquire_license: bool = False):
        assert acquire_license is False, "GUI must never ask the core to acquire"
        return core.download(app.bundle_id, store_id=app.store_id, lookup_store_id=False).name

    fake = SimpleNamespace(
        service=SimpleNamespace(core=core, download_to_library=download_to_library),
        installProgress=Emitter(),
        notes=[],
    )
    fake._set_files_note = lambda text, busy=None: fake.notes.append(text)
    return fake, tools


def test_install_store_keeps_auto_acquire_on() -> None:
    calls: list[tuple] = []
    fake = SimpleNamespace(_start_install=lambda store_id, acquire: calls.append((store_id, acquire)))
    QuickSession.installStore(fake, STORE)
    QuickSession.acquireStore(fake, STORE)
    assert calls == [(STORE, True), (STORE, True)]


def test_owned_app_installs_without_purchase(env) -> None:
    fake, tools = _session(env, owned=True)
    QuickSession._restore_store_gated(fake, "udid", STORE, True)
    assert tools.purchases == []
    assert not env.journal.exists()


def test_free_app_install_purchases_and_journals(env) -> None:
    fake, tools = _session(env)
    QuickSession._restore_store_gated(fake, "udid", STORE, True)
    assert tools.purchases == [STORE]
    assert (-1, LICENSE_NOTICE) in fake.installProgress.values
    lines = env.journal.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    entry = json.loads(lines[0])
    assert entry["track_id"] == STORE and entry["status"] == "acquired" and entry["storefront"] == "ru"


def test_paid_app_install_never_purchases(env) -> None:
    env.price["value"] = 2.99
    fake, tools = _session(env)
    with pytest.raises(LicenseDenied, match="платное"):
        QuickSession._restore_store_gated(fake, "udid", STORE, True)
    assert tools.purchases == []
    assert not env.journal.exists()


def test_install_over_limit_never_purchases(env) -> None:
    env.journal.write_text('{"time": "garbage"}\n' * 5, encoding="utf-8")
    fake, tools = _session(env)
    with pytest.raises(LicenseDenied, match="Лимит"):
        QuickSession._restore_store_gated(fake, "udid", STORE, True)
    assert tools.purchases == []


def test_save_copy_goes_through_the_same_gate(env) -> None:
    app = InstalledApp(bundle_id="com.example.alpha", name="Alpha", version="1.0", store_id=STORE)
    fake, tools = _session(env)
    QuickSession._download_copy_gated(fake, app)
    assert tools.purchases == [STORE]
    assert any(LICENSE_NOTICE in note for note in fake.notes)
    assert env.journal.is_file()

    env.price["value"] = 0.99
    fake, tools = _session(env)
    with pytest.raises(LicenseDenied):
        QuickSession._download_copy_gated(fake, app)
    assert tools.purchases == []


def test_shelf_probe_thread_uses_read_only_probe(env, monkeypatch: pytest.MonkeyPatch) -> None:
    import apprestore_gui.quick_session as qs

    seen: list[str] = []
    monkeypatch.setattr(qs, "probe_store", lambda _tools, store_id: seen.append(store_id) or "not-owned")
    monkeypatch.setattr(qs, "POPULAR_APPS", [{"storeId": STORE, "name": "X"}])
    monkeypatch.setattr(qs.tempfile, "gettempdir", lambda: str(env.tmp))
    import threading

    fake = SimpleNamespace(
        service=SimpleNamespace(keychain_ready=lambda: True, core=SimpleNamespace(tools=None)),
        _tool_lock=threading.Lock(),
    )
    QuickSession._probe_shelf(fake)
    assert seen == [STORE]
    assert "not-owned" in (env.tmp / "apprestore-shelf-check.txt").read_text(encoding="utf-8")
