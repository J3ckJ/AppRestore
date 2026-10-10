"""«удалённое не на аккаунте» (decision 10.10, Облачко): the GUI side and the
isolated adapter to Макс's pending license_guard path.

The tests marked xfail(strict) need Макс's license_guard (price unknown +
region_probe flag → attempt allowed, limit counted, journal only if Apple
grants). They turn into failures (XPASS strict) the moment his API lands, so
the marks must be removed together with vendoring it.
"""

from __future__ import annotations

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from apprestore_core import delisted_attempt, license_journal  # noqa: E402
from apprestore_core.license_gate import LicenseDenied, run_with_free_license  # noqa: E402
from apprestore_core.region_probe import RegionStatus  # noqa: E402
from apprestore_gui.ui4b import home, licenses, region, selection  # noqa: E402
from apprestore_gui.ui4b.catalog import ACTION_STORE, GROUP_REGION, GROUP_REMOVED, RestoreItem  # noqa: E402

from tests.test_license_gate import STORE, Download, Lookup, Tools, _entries  # noqa: E402

PENDING = pytest.mark.xfail(
    strict=True,
    reason="Макс's license_guard path (price unknown + region_probe DELISTED/NOT_IN_REGION → "
    "attempt allowed, limit counted, journal only if Apple grants) is not vendored yet; "
    "call site: license_gate.run_with_free_license → license_journal.acquire_and_record "
    "→ license_guard.acquire_and_record(..., <flag>)",
)


@pytest.fixture(autouse=True)
def _clean_flags():
    delisted_attempt.forget_all()
    yield
    delisted_attempt.forget_all()


@pytest.fixture
def feature_on(monkeypatch):
    """The GUI side as it will run once Макс's guard takes the flag."""

    monkeypatch.setattr(delisted_attempt, "enabled", lambda: True)


def item(sid: str, group: str = GROUP_REMOVED, status: str = "") -> RestoreItem:
    return RestoreItem(key=f"store:{sid}", name=f"App {sid}", group=group, action=ACTION_STORE,
                       store_id=sid, store_status=status)


# -- GUI side (works today) ----------------------------------------------------------

def test_plan_counts_flagged_unknown_price_in_k_known_paid_stays_paid(feature_on) -> None:
    items = [item("1", status="delisted"), item("2", GROUP_REGION, "not_in_region"),
             item("3"), item("4", status="delisted"), item("5")]
    plan = licenses.plan_licenses(items, owned=set(), prices={"4": 2.99, "5": 0.0})
    assert [i.store_id for i in plan.need] == ["1", "2", "5"]  # K = 3
    assert [i.store_id for i in plan.paid] == ["4"]              # price>0 never attempted
    assert [i.store_id for i in plan.rest] == ["3"]              # no flag: as before (gate refuses)
    # already on the account: no license, no K
    assert licenses.plan_licenses([item("1", status="delisted")], owned={"1"}, prices={}).k == 0


def test_only_region_probe_sets_the_flag(feature_on) -> None:
    items = [item("1"), item("2"), item("3")]
    out = region.apply_statuses(items, {"1": RegionStatus.DELISTED, "2": RegionStatus.NOT_IN_REGION,
                                        "3": RegionStatus.UNKNOWN})
    assert [(i.group, i.store_status, i.attemptable) for i in out] == [
        (GROUP_REMOVED, "delisted", True), (GROUP_REGION, "not_in_region", True), (GROUP_REMOVED, "", False)]
    # a plain RestoreItem (builtin list, GUI guess) has no flag
    assert not item("9").attemptable


def test_picker_region_row_selectable_home_count_unchanged(feature_on) -> None:
    base = [item("1"), item("2"), item("3")]
    items = region.apply_statuses(base, {"3": RegionStatus.NOT_IN_REGION})
    sel = selection.Selection(items)
    if "store:3" in {i.key for i in sel.selected_items()}:
        sel.toggle("store:3")
    assert sel.toggle("store:3") and "3" in {i.store_id for i in sel.selected_items()}
    rail = {r["key"]: r for r in sel.rail_rows()}
    assert rail[GROUP_REGION]["sub"] == "выбрано 1"  # an ordinary group now
    v = home.home_view(home.HomeInput(connected=True, signed_in=True, items=items))
    assert v["state"] == "region" and v["cta"] == "Вернуть 2"  # home keeps region apps out


def test_adapter_registry_and_no_kwargs_without_maks_api() -> None:
    delisted_attempt.record_region_probe({"1": RegionStatus.DELISTED, "2": RegionStatus.AVAILABLE,
                                          "3": "not_in_region"})
    assert delisted_attempt.flag_for("1") == "delisted" and delisted_attempt.flag_for("3") == "not_in_region"
    assert delisted_attempt.flag_for("2") == ""
    delisted_attempt.record_region_probe({"1": RegionStatus.UNKNOWN})
    assert delisted_attempt.flag_for("1") == ""
    assert delisted_attempt.guard_kwargs("3", 0.0) == {}  # known price: never


def test_gate_passes_flag_only_for_unknown_price_when_guard_takes_it(tmp_path, monkeypatch) -> None:
    seen: list[dict] = []

    def fake_acquire(track_id, price, purchase, *, preflight=None, journal_path=None,
                     bundle_id="", storefront="", mode=None, store_status=None):
        seen.append({"price": price, "store_status": store_status})
        from types import SimpleNamespace
        return SimpleNamespace(allowed=False, recorded=False, reason="test", used_today=0, used_total=0, code=None)

    monkeypatch.setattr(license_journal.license_guard, "acquire_and_record", fake_acquire)
    assert delisted_attempt.guard_supports()
    delisted_attempt.record_region_probe({STORE: RegionStatus.DELISTED})
    tools = Tools()
    with pytest.raises(LicenseDenied):
        run_with_free_license(STORE, Download(tools), tools=tools, lookup=Lookup(found=False), journal=tmp_path / "j")
    with pytest.raises(LicenseDenied):
        run_with_free_license(STORE, Download(tools), tools=tools, lookup=Lookup(1.99), journal=tmp_path / "j")
    assert seen == [{"price": None, "store_status": "delisted"}, {"price": 1.99, "store_status": None}]
    assert tools.purchases == []


def test_today_unknown_price_still_refused_by_guard_nothing_journaled(tmp_path) -> None:
    """Without Макс's path the vendored guard refuses an unknown price: no purchase."""

    if delisted_attempt.guard_supports():
        pytest.skip("Макс's path is vendored")
    delisted_attempt.record_region_probe({STORE: RegionStatus.DELISTED})
    tools = Tools()
    with pytest.raises(LicenseDenied):
        run_with_free_license(STORE, Download(tools), tools=tools, lookup=Lookup(found=False), journal=tmp_path / "j")
    assert tools.purchases == [] and not (tmp_path / "j").exists()


def test_feature_off_by_default_until_maks_guard() -> None:
    if delisted_attempt.guard_supports():
        pytest.skip("Макс's guard vendored: remove the xfail marks below")
    assert not delisted_attempt.enabled()
    items = region.apply_statuses([item("1"), item("2")], {"1": RegionStatus.DELISTED, "2": RegionStatus.NOT_IN_REGION})
    assert not any(i.attemptable for i in items) and not items[1].selectable
    plan = licenses.plan_licenses(items, owned=set(), prices={})
    assert plan.k == 0  # nothing attempted, gate refuses unknown price as before


def test_any_paid_lookup_blocks_and_one_attempt_per_session(feature_on) -> None:
    it = region.apply_statuses([item("1"), item("2")], {"1": RegionStatus.DELISTED, "2": RegionStatus.DELISTED})
    assert all(i.attemptable for i in it)
    delisted_attempt.record_prices({"1": 0.99, "2": None})  # e.g. a reference storefront said paid
    assert not it[0].attemptable and it[1].attemptable
    assert licenses.plan_licenses(it, owned=set(), prices={}).k == 1
    delisted_attempt.mark_attempted(["2"])
    assert not it[1].attemptable  # one attempt per app per session
    delisted_attempt.forget_flags()  # sign out keeps «already attempted»
    assert not it[1].attemptable


def test_consent_line_only_when_k_has_flagged_apps_never_says_free_app(feature_on) -> None:
    flagged = region.apply_statuses([item("1")], {"1": RegionStatus.DELISTED})
    v = licenses.consent_view(licenses.plan_licenses(flagged + [item("2")], set(), {"2": 0.0}), (0, 0))
    assert v["attempt"] == "Если приложение окажется платным, Apple его не выдаст. Отказ Apple лимит не тратит"
    assert "бесплатное" not in v["attempt"] and "бесплатно " not in v["attempt"]
    v = licenses.consent_view(licenses.plan_licenses([item("2")], set(), {"2": 0.0}), (0, 0))
    assert v["attempt"] == ""


# -- needs Макс's license_guard -------------------------------------------------------

@PENDING
def test_feature_is_on_with_vendored_guard() -> None:
    assert delisted_attempt.enabled()


@PENDING
def test_flagged_unknown_price_attempt_counts_limit_and_journals_on_grant(tmp_path) -> None:
    journal = tmp_path / "j.jsonl"
    delisted_attempt.record_region_probe({STORE: RegionStatus.DELISTED})
    tools = Tools()
    assert run_with_free_license(STORE, Download(tools), tools=tools, lookup=Lookup(found=False),
                                 journal=journal) == "installed"
    assert tools.purchases == [STORE]
    [entry] = _entries(journal)
    assert entry["track_id"] == STORE


@PENDING
def test_flagged_unknown_price_apple_refusal_no_journal(tmp_path) -> None:
    journal = tmp_path / "j.jsonl"
    delisted_attempt.record_region_probe({STORE: RegionStatus.NOT_IN_REGION})
    tools = Tools(purchase_ok=False)
    with pytest.raises(Exception):
        run_with_free_license(STORE, Download(tools), tools=tools, lookup=Lookup(found=False), journal=journal)
    assert tools.purchases == [STORE]  # attempted once (no retry) …
    assert not journal.exists() or _entries(journal) == []  # … nothing journaled
