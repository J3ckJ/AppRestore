"""Tile names via Макс's app_names.display_names (names_lookup) — Евгений 10.10."""

from __future__ import annotations

import threading
from pathlib import Path

import pytest

from apprestore_gui.ui4b import names_lookup as NL
from apprestore_gui.ui4b.names import FALLBACK, resolve

REPO = Path(__file__).resolve().parents[1]


class Clock:
    def __init__(self) -> None:
        self.t = 100.0

    def __call__(self) -> float:
        return self.t


def test_chain_device_then_purchases_then_display_names_then_fallback() -> None:
    looked = {"111": "Looked"}
    assert resolve("Device", store_id="111", looked_up=looked) == "Device"
    assert resolve("", store_id="111", purchases={"111": "Bought"}, looked_up=looked) == "Bought"
    assert resolve("", store_id="111", looked_up=looked, builtin={"111": "Builtin"}) == "Looked"
    assert resolve("com.x.y", bundle_id="com.x.y", looked_up={}) == FALLBACK


def test_pending_label_then_names_then_fallback_after_deadline() -> None:
    calls: list[tuple] = []
    jobs: list = []
    clock = Clock()

    def display_names(ids, *, bundle_ids=(), account_country=None, device_locale=None):
        calls.append((list(ids), list(bundle_ids), account_country, device_locale))
        return {"1": "Сбербанк Онлайн"}

    lk = NL.NameLookup(display_names, spawn=jobs.append, clock=clock)
    assert lk.request(["1", "2"], ["com.a.b"], account_country=lambda: None, locale=lambda: "en_RU")
    assert lk.pending("1") and lk.pending("com.a.b")  # label empty meanwhile
    assert not lk.request(["1"], [])  # asked once per key
    jobs.pop()()
    assert calls == [(["1", "2"], ["com.a.b"], None, "en_RU")]  # before sign-in: no account country
    assert lk.name("1") == "Сбербанк Онлайн" and not lk.pending("2")  # finished, not found → «Приложение»
    lk2 = NL.NameLookup(display_names, spawn=lambda f: None, clock=clock)
    lk2.request(["9"], [])
    clock.t += NL.DEADLINE_S + 0.1
    assert not lk2.pending("9")  # ~3 s → «Приложение»


def test_visible_tiles_first_and_batches_capped() -> None:
    seen: list[int] = []
    jobs: list = []
    lk = NL.NameLookup(lambda ids, **kw: seen.append(len(list(ids)) + len(list(kw["bundle_ids"]))) or {},
                       spawn=jobs.append)
    lk.request([str(i) for i in range(1, 2501)], [], account_country=lambda: "RU")
    jobs.pop()()
    assert seen[0] == NL.VISIBLE_FIRST and max(seen) <= NL.MAX_KEYS and sum(seen) == 2500


def test_runs_in_a_worker_thread_by_default() -> None:
    where: list[str] = []
    done = threading.Event()

    def display_names(ids, **kw):
        where.append(threading.current_thread().name)
        done.set()
        return {}

    NL.NameLookup(display_names).request(["5"], [])
    assert done.wait(2) and where[0] != threading.main_thread().name


def test_clear_drops_late_answers() -> None:
    jobs: list = []
    lk = NL.NameLookup(lambda ids, **kw: {"1": "X"}, spawn=jobs.append)
    lk.request(["1"], [])
    lk.clear()
    jobs.pop()()
    assert lk.name("1") == ""


def test_vendored_module_is_the_announced_one() -> None:
    import hashlib

    data = (REPO / "apprestore_core" / "app_names.py").read_bytes()
    assert hashlib.sha256(data).hexdigest() == "15f5581c535278b08dff660d911df3f0c56249c4a0ee167856aa1d759f3846d5"
    assert NL.vendored_display_names() is not None


def test_no_request_to_web_archive_from_the_names_path(monkeypatch) -> None:
    """The names path (names_lookup → app_names) never contacts web.archive.org."""

    import urllib.request

    from apprestore_core import app_names, region_probe

    urls: list[str] = []

    def fake_urlopen(req, *a, **kw):
        url = req.full_url if hasattr(req, "full_url") else str(req)
        urls.append(url)
        raise OSError("offline in tests")

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    for mod in (app_names, region_probe):
        if hasattr(mod, "urlopen"):
            monkeypatch.setattr(mod, "urlopen", fake_urlopen)
    resolver = app_names.AppNames(region_probe.RegionProbe(min_interval_s=0) if "min_interval_s" in
                                  region_probe.RegionProbe.__init__.__code__.co_varnames else None)
    lk = NL.NameLookup(resolver.display_names, spawn=lambda f: f())
    lk.request(["492224193", "6749962031"], ["ru.sberbankmobile"], locale=lambda: "ru_RU")
    assert not any("archive.org" in u for u in urls)
    for path in (REPO / "apprestore_gui" / "ui4b" / "names_lookup.py", REPO / "apprestore_core" / "app_names.py"):
        src = path.read_text(encoding="utf-8")
        assert "search_delisted(" not in src and "CDX_URL" not in src and "SNAPSHOT_URL" not in src


@pytest.mark.parametrize("key", ["", "abc"])
def test_bad_keys_are_ignored(key) -> None:
    lk = NL.NameLookup(lambda ids, **kw: {}, spawn=lambda f: f())
    assert not lk.request([key] if key.isdigit() else [], [""])


def test_tile_label_empty_while_pending() -> None:
    from apprestore_gui.ui4b.catalog import GROUP_REMOVED, RestoreItem
    from apprestore_gui.ui4b.home import HomeInput, home_view

    items = [RestoreItem(key="1", name=FALLBACK, group=GROUP_REMOVED, action="store", store_id="1", name_pending=True),
             RestoreItem(key="2", name="ВТБ Онлайн", group=GROUP_REMOVED, action="store", store_id="2")]
    tiles = {t["storeId"]: t for t in home_view(HomeInput(connected=True, items=items))["tiles"] if t.get("storeId")}
    assert tiles["1"]["namePending"] is True and tiles["2"]["namePending"] is False
    qml = (REPO / "apprestore_gui" / "qml4b" / "components" / "PhoneTile.qml").read_text(encoding="utf-8")
    assert 'root.tile.namePending ? ""' in qml
