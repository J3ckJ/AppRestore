"""«удалённое не на аккаунте» (LEGAL §1.14): the GUI side, the delisted_attempt
adapter and Макс's license_guard path (region_unavailable=True + RegionAttemptSession).
No real purchase anywhere: purchase_license is a fake (tests.test_license_gate.Tools).
"""

from __future__ import annotations

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from apprestore_core import delisted_attempt, license_guard, license_journal  # noqa: E402
from apprestore_core.license_gate import LicenseDenied, run_with_free_license  # noqa: E402
from apprestore_core.region_probe import RegionStatus  # noqa: E402
from apprestore_core.tools import ToolUnavailable  # noqa: E402
from apprestore_gui.ui4b import home, licenses, region, selection  # noqa: E402
from apprestore_gui.ui4b.catalog import ACTION_STORE, GROUP_REGION, GROUP_REMOVED, RestoreItem  # noqa: E402
from tests.test_license_gate import STORE, Download, Lookup, Tools, _entries  # noqa: E402

class PTools(Tools):
    """Patched ipatool: preflight says «ok» (None). §1.14 requires a preflight."""

    def license_preflight(self):
        return None


CONSENT_LINE = "Если приложение окажется платным, Apple откажет в выдаче. Отказ лимит не тратит"


@pytest.fixture(autouse=True)
def _clean():
    delisted_attempt.forget_all()
    yield
    delisted_attempt.forget_all()


def item(sid: str, group: str = GROUP_REMOVED) -> RestoreItem:
    return RestoreItem(key=f"store:{sid}", name=f"App {sid}", group=group, action=ACTION_STORE, store_id=sid)


def flagged(status=RegionStatus.DELISTED, sid: str = STORE) -> None:
    delisted_attempt.record_region_probe({sid: status})


# -- switch ---------------------------------------------------------------------------

def test_feature_is_on_with_vendored_guard() -> None:
    assert delisted_attempt.guard_supports() and delisted_attempt.enabled()


# -- flag only from region_probe ------------------------------------------------------

def test_only_region_probe_sets_the_flag() -> None:
    out = region.apply_statuses([item("1"), item("2"), item("3"), item("4")],
                                {"1": RegionStatus.DELISTED, "2": RegionStatus.NOT_IN_REGION,
                                 "3": RegionStatus.UNKNOWN, "4": RegionStatus.AVAILABLE})
    assert [(i.group, i.store_status, i.attemptable) for i in out] == [
        (GROUP_REMOVED, "delisted", True), (GROUP_REGION, "not_in_region", True),
        (GROUP_REMOVED, "", False), (GROUP_REMOVED, "", False)]
    assert not item("9").attemptable  # builtin list / a GUI guess never sets it


def test_plan_counts_flagged_unknown_price_in_k_known_paid_stays_paid() -> None:
    items = region.apply_statuses([item("1"), item("2"), item("3"), item("4"), item("5")],
                                  {"1": RegionStatus.DELISTED, "2": RegionStatus.NOT_IN_REGION,
                                   "4": RegionStatus.DELISTED})
    plan = licenses.plan_licenses(items, owned=set(), prices={"4": 2.99, "5": 0.0})
    assert [i.store_id for i in plan.need] == ["1", "2", "5"]  # K = 3
    assert [i.store_id for i in plan.paid] == ["4"]
    assert [i.store_id for i in plan.rest] == ["3"]  # no flag: the gate refuses as before
    assert licenses.plan_licenses(items[:1], owned={"1"}, prices={}).k == 0  # on the account


def test_picker_region_row_selectable_home_count_unchanged() -> None:
    items = region.apply_statuses([item("1"), item("2"), item("3")], {"3": RegionStatus.NOT_IN_REGION})
    sel = selection.Selection(items)
    if "store:3" in {i.key for i in sel.selected_items()}:
        sel.toggle("store:3")
    assert sel.toggle("store:3") and "3" in {i.store_id for i in sel.selected_items()}
    assert {r["key"]: r for r in sel.rail_rows()}[GROUP_REGION]["sub"] == "выбрано 1"
    v = home.home_view(home.HomeInput(connected=True, signed_in=True, items=items))
    assert v["state"] == "region" and v["cta"] == "Вернуть 2"  # home keeps region apps out


def test_gate_without_flag_still_refuses_unknown_price(tmp_path) -> None:
    tools = Tools()
    with pytest.raises(LicenseDenied, match="не показывает его цену"):
        run_with_free_license(STORE, Download(tools), tools=tools, lookup=Lookup(found=False), journal=tmp_path / "j")
    assert tools.purchases == [] and not (tmp_path / "j").exists()
    flagged(RegionStatus.UNKNOWN)
    with pytest.raises(LicenseDenied):
        run_with_free_license(STORE, Download(tools), tools=tools, lookup=Lookup(found=False), journal=tmp_path / "j")
    assert tools.purchases == []


def test_gate_passes_exactly_true_and_the_session(tmp_path, monkeypatch) -> None:
    seen: list[dict] = []
    real = license_guard.acquire_and_record
    import functools

    @functools.wraps(real)  # keeps the signature guard_supports() looks at
    def spy(*a, **k):
        seen.append({"price": a[1], "flag": k.get("region_unavailable"), "session": k.get("region_session")})
        return real(*a, **k)

    monkeypatch.setattr(license_journal.license_guard, "acquire_and_record", spy)
    flagged()
    tools = PTools()
    run_with_free_license(STORE, Download(tools), tools=tools, lookup=Lookup(found=False), journal=tmp_path / "j")
    assert seen[0]["price"] is None and seen[0]["flag"] is True
    assert seen[0]["session"] is delisted_attempt.session()
    assert isinstance(seen[0]["session"], license_guard.RegionAttemptSession)


# -- price > 0 blocks even with the flag ----------------------------------------------

def test_price_above_zero_blocks_even_with_flag(tmp_path) -> None:
    flagged()
    tools = PTools()
    with pytest.raises(LicenseDenied, match="платное"):
        run_with_free_license(STORE, Download(tools), tools=tools, lookup=Lookup(1.99), journal=tmp_path / "j")
    assert tools.purchases == []
    # a reference-storefront lookup said paid earlier: no flag passed, no button
    delisted_attempt.record_prices({STORE: 0.99})
    assert delisted_attempt.guard_kwargs(STORE, None) == {}
    assert not delisted_attempt.may_offer(STORE, RegionStatus.DELISTED)
    with pytest.raises(LicenseDenied, match="платное"):
        run_with_free_license(STORE, Download(tools), tools=tools, lookup=Lookup(found=False), journal=tmp_path / "j")
    assert tools.purchases == []


# -- outcomes on Макс's path ----------------------------------------------------------

def test_granted_records_price_source_apple_fixed_0_then_plain_download(tmp_path) -> None:
    journal = tmp_path / "j.jsonl"
    flagged()
    tools = PTools()
    download = Download(tools)
    assert run_with_free_license(STORE, download, tools=tools, lookup=Lookup(found=False), journal=journal) == "installed"
    assert tools.purchases == [STORE] and download.calls == 2
    [entry] = _entries(journal)
    assert entry["track_id"] == STORE and entry["price"] is None
    assert entry["price_source"] == license_guard.PRICE_SOURCE_APPLE_FIXED_0
    assert entry["status"] == "acquired"


def test_region_notice_never_says_free(tmp_path) -> None:
    from apprestore_core.license_gate import LICENSE_NOTICE, REGION_NOTICE

    flagged()
    tools, notes = PTools(), []
    run_with_free_license(STORE, Download(tools), tools=tools, lookup=Lookup(found=False),
                          journal=tmp_path / "j", notify=notes.append)
    assert notes == [REGION_NOTICE] and "бесплатн" not in REGION_NOTICE.casefold()
    tools2, notes2 = PTools(), []
    run_with_free_license("42", Download(tools2), tools=tools2, lookup=Lookup(0), journal=tmp_path / "j2",
                          notify=notes2.append)
    assert notes2 == [LICENSE_NOTICE]


def test_apple_refusal_no_journal_no_limit_and_no_second_attempt(tmp_path) -> None:
    journal = tmp_path / "j.jsonl"
    flagged(RegionStatus.NOT_IN_REGION)
    tools = PTools(purchase_ok=False)
    with pytest.raises(ToolUnavailable):  # the original error → error-apple-rejected in 4b
        run_with_free_license(STORE, Download(tools), tools=tools, lookup=Lookup(found=False), journal=journal)
    assert tools.purchases == [STORE]
    assert not journal.exists() and license_guard.read_counts(journal) == (0, 0)
    with pytest.raises(LicenseDenied):  # REGION_ALREADY_ATTEMPTED: purchase not called again
        run_with_free_license(STORE, Download(tools), tools=tools, lookup=Lookup(found=False), journal=journal)
    assert tools.purchases == [STORE]


def test_unclear_answer_is_uncertain_and_counted(tmp_path) -> None:
    journal = tmp_path / "j.jsonl"
    flagged()
    tools = PTools(error=ToolUnavailable("net/http: TLS handshake timeout"))
    with pytest.raises(ToolUnavailable):
        run_with_free_license(STORE, Download(tools), tools=tools, lookup=Lookup(found=False), journal=journal)
    [entry] = _entries(journal)
    assert entry["status"] == "purchase_uncertain" and entry["price_source"] == "apple_fixed_0"
    assert license_guard.read_counts(journal) == (1, 1)


def test_no_preflight_on_region_path_refused_no_purchase_no_journal(tmp_path) -> None:
    journal = tmp_path / "j.jsonl"
    flagged()
    tools = Tools()  # no license_preflight at all
    with pytest.raises(LicenseDenied, match="preflight"):
        run_with_free_license(STORE, Download(tools), tools=tools, lookup=Lookup(found=False), journal=journal)
    assert tools.purchases == [] and not journal.exists()
    called = []
    res = license_guard.acquire_and_record(STORE, None, purchase=lambda: called.append(1) or "acquired",
                                           journal_path=journal, region_unavailable=True,
                                           region_session=delisted_attempt.session())
    assert res.code == "region_preflight_required" and not res.allowed and not res.recorded
    assert called == [] and not journal.exists()


def test_limit_still_applies(tmp_path) -> None:
    from tests.test_license_gate import _fill

    journal = tmp_path / "j.jsonl"
    _fill(journal, today=5)
    flagged()
    tools = PTools()
    with pytest.raises(LicenseDenied):
        run_with_free_license(STORE, Download(tools), tools=tools, lookup=Lookup(found=False), journal=journal)
    assert tools.purchases == []


# -- one attempt per session, reset on «Выйти» / account switch ------------------------

def test_one_attempt_per_session_and_reset_on_signout_and_account_switch(tmp_path) -> None:
    it = region.apply_statuses([item("1")], {"1": RegionStatus.DELISTED})[0]
    assert it.attemptable
    delisted_attempt.mark_attempted(["1"])
    assert not it.attemptable  # GUI: no second button
    delisted_attempt.reset_session()  # «Выйти»
    assert it.attemptable
    # guard session too
    delisted_attempt.on_account("a@example.com")  # signed in as a
    journal = tmp_path / "j.jsonl"
    flagged()
    tools = PTools(purchase_ok=False)
    with pytest.raises(ToolUnavailable):
        run_with_free_license(STORE, Download(tools), tools=tools, lookup=Lookup(found=False), journal=journal)
    assert delisted_attempt.session().attempted(STORE)
    delisted_attempt.on_account("a@example.com")
    delisted_attempt.on_account("a@example.com")  # same account: nothing changes
    flagged()
    with pytest.raises(LicenseDenied):
        run_with_free_license(STORE, Download(tools), tools=tools, lookup=Lookup(found=False), journal=journal)
    delisted_attempt.on_account("b@example.com")  # another Apple ID: new session
    assert not delisted_attempt.session().attempted(STORE) and delisted_attempt.flag_for(STORE) == ""
    flagged()
    with pytest.raises(ToolUnavailable):
        run_with_free_license(STORE, Download(tools), tools=tools, lookup=Lookup(found=False), journal=journal)
    assert tools.purchases == [STORE, STORE]


def test_any_paid_lookup_blocks_offer() -> None:
    it = region.apply_statuses([item("1"), item("2")], {"1": RegionStatus.DELISTED, "2": RegionStatus.DELISTED})
    delisted_attempt.record_prices({"1": 0.99, "2": None})
    assert not it[0].attemptable and it[1].attemptable
    assert licenses.plan_licenses(it, owned=set(), prices={}).k == 1


def test_consent_line_only_when_k_has_flagged_apps() -> None:
    fl = region.apply_statuses([item("1")], {"1": RegionStatus.DELISTED})
    v = licenses.consent_view(licenses.plan_licenses(fl + [item("2")], set(), {"2": 0.0}), (0, 0))
    assert v["attempt"] == CONSENT_LINE
    assert "бесплатн" not in (v["lead"] + v["attempt"]).casefold()
    v = licenses.consent_view(licenses.plan_licenses([item("2")], set(), {"2": 0.0}), (0, 0))
    assert v["attempt"] == ""


# -- region_probe known reference price (Макс §1.14 п.2) ------------------------------

def _detailed(status, known):
    from apprestore_core.region_probe import RegionResult

    return RegionResult(status=status, known_price=known,
                        known_prices={} if known is None else {"US": known})


def test_reference_price_above_zero_means_no_attempt(tmp_path) -> None:
    fake = lambda ids, country: {int(STORE): _detailed(RegionStatus.NOT_IN_REGION, 0.99)}  # noqa: E731
    statuses = region.classify(fake, [STORE], "RU", online=True)
    assert statuses == {STORE: RegionStatus.NOT_IN_REGION}
    it = region.apply_statuses([item(STORE)], statuses)[0]
    assert it.store_status == "not_in_region" and not it.attemptable  # no «Поставить»
    assert delisted_attempt.max_known_price(STORE, None) == 0.99
    assert delisted_attempt.max_known_price(STORE, 0.0) == 0.99  # MAX of all known
    delisted_attempt.record_region_probe(statuses)
    tools = PTools()
    with pytest.raises(LicenseDenied, match="платное"):  # guard gets max price, refuses
        run_with_free_license(STORE, Download(tools), tools=tools, lookup=Lookup(found=False),
                              journal=tmp_path / "j")
    assert tools.purchases == [] and not (tmp_path / "j").exists()


def test_unknown_everywhere_keeps_the_attempt_and_garbage_prices_ignored() -> None:
    fake = lambda ids, country: {int(STORE): _detailed(RegionStatus.DELISTED, None)}  # noqa: E731
    statuses = region.classify(fake, [STORE], "RU", online=True)
    assert region.apply_statuses([item(STORE)], statuses)[0].attemptable
    delisted_attempt.record_prices({STORE: float("nan"), "x": True, "y": -1, "z": "abc"})
    assert delisted_attempt.max_known_price(STORE) is None
    assert region.apply_statuses([item(STORE)], statuses)[0].attemptable


def test_classifier_uses_detailed_api() -> None:
    from apprestore_core import region_probe

    assert region.load_classifier() is region_probe.classify_region_detailed
