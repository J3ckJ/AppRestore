"""«Поставить» / «Сохранить копии» take a free license only through the gate:
price==0 in the account's country, 5/day + 15 total, separate purchase, journal."""

from __future__ import annotations

import datetime as dt
import json
from pathlib import Path

import pytest

from apprestore_core.license_guard import read_counts
from apprestore_core.tools import ToolUnavailable
from apprestore_core.license_gate import LICENSE_NOTICE, LicenseDenied, run_with_free_license

STORE = "1234567890"
MISSING = (
    f"could not download App Store ID {STORE} (store={STORE} without "
    "--purchase: license is required). Typical causes: ... no purchase/license."
)


class Tools:
    def __init__(self, *, country: str = "us", purchase_ok: bool = True, error: BaseException | None = None) -> None:
        self.country = country
        self.purchase_ok = purchase_ok
        self.error = error
        self.purchases: list[str] = []
        self.licensed = False

    def account_country(self) -> str:
        return self.country

    def purchase_license(self, store_id: str, *, grant: object) -> dict:
        from apprestore_core.purchase_grant import require_grant

        require_grant(grant, store_id)
        self.purchases.append(str(store_id))
        if self.error is not None:
            raise self.error
        if not self.purchase_ok:
            raise ToolUnavailable("failed to purchase app")
        self.licensed = True
        return {"success": True}


class Download:
    """Read-only download: fails with "license is required" until licensed."""

    def __init__(self, tools: Tools, *, owned: bool = False, fail_after_license: bool = False) -> None:
        self.tools = tools
        self.owned = owned
        self.fail_after_license = fail_after_license
        self.calls = 0

    def __call__(self) -> str:
        self.calls += 1
        if self.owned:
            return "installed"
        if self.tools.licensed:
            if self.fail_after_license:
                raise RuntimeError("store=1 without --purchase: net/http: TLS handshake timeout")
            return "installed"
        raise RuntimeError(MISSING)


class Lookup:
    def __init__(self, price: object = 0.0, *, found: bool = True) -> None:
        self.price = price
        self.found = found
        self.calls: list[tuple[str, object]] = []

    def __call__(self, store_id: str, countries: object) -> dict | None:
        self.calls.append((store_id, countries))
        if not self.found:
            return None
        assert countries, "price lookup must name the account country"
        country = countries[0]
        return {"storeId": store_id, "bundleId": "com.example.free", "price": self.price, "country": country}


def _fill(journal: Path, *, today: int = 0, old: int = 0) -> None:
    now = dt.datetime.now(dt.timezone.utc)
    lines = [{"time": now.isoformat(timespec="seconds"), "track_id": "1", "status": "acquired"}] * today
    lines += [{"time": (now - dt.timedelta(days=3)).isoformat(timespec="seconds"), "app_id": "1"}] * old
    journal.write_text("".join(json.dumps(line) + "\n" for line in lines), encoding="utf-8")


def _entries(journal: Path) -> list[dict]:
    return [json.loads(line) for line in journal.read_text(encoding="utf-8").splitlines()]


def test_owned_app_never_looks_up_or_purchases(tmp_path: Path) -> None:
    tools, lookup = Tools(), Lookup()
    download = Download(tools, owned=True)
    assert run_with_free_license(STORE, download, tools=tools, lookup=lookup, journal=tmp_path / "j") == "installed"
    assert download.calls == 1 and lookup.calls == [] and tools.purchases == []
    assert not (tmp_path / "j").exists()


def test_free_app_purchase_then_plain_download_and_journal(tmp_path: Path) -> None:
    journal = tmp_path / "j.jsonl"
    tools, lookup = Tools(country="kz"), Lookup(0)
    download = Download(tools)
    notes: list[str] = []
    result = run_with_free_license(
        STORE, download, tools=tools, lookup=lookup, journal=journal, notify=notes.append
    )
    assert result == "installed"
    assert tools.purchases == [STORE]
    assert download.calls == 2
    assert notes == [LICENSE_NOTICE]
    assert lookup.calls == [(STORE, ("kz",))]
    [entry] = _entries(journal)
    assert entry["track_id"] == STORE
    assert entry["status"] == "acquired"
    assert entry["storefront"] == "kz"
    assert entry["price"] == 0.0
    assert entry["mode"] == "gui"
    assert "@" not in journal.read_text(encoding="utf-8")


def test_unknown_account_country_refuses_purchase_without_guessing(tmp_path: Path) -> None:
    tools, lookup = Tools(country=""), Lookup(0)
    journal = tmp_path / "j"
    with pytest.raises(LicenseDenied) as caught:
        run_with_free_license(STORE, Download(tools), tools=tools, lookup=lookup, journal=journal)
    assert "страну аккаунта" in str(caught.value)
    assert "обновите ipatool" in str(caught.value).casefold()
    assert lookup.calls == [] and tools.purchases == []
    assert not journal.exists()


def test_account_country_error_also_refuses(tmp_path: Path) -> None:
    class Broken(Tools):
        def account_country(self) -> str:
            raise ToolUnavailable("failed to get account: not logged in")

    tools, lookup = Broken(), Lookup(0)
    with pytest.raises(LicenseDenied):
        run_with_free_license(STORE, Download(tools), tools=tools, lookup=lookup, journal=tmp_path / "j")
    assert lookup.calls == [] and tools.purchases == []


def test_price_is_looked_up_only_in_the_account_country(tmp_path: Path) -> None:
    tools, lookup = Tools(country="us"), Lookup(0)
    run_with_free_license(STORE, Download(tools), tools=tools, lookup=lookup, journal=tmp_path / "j")
    assert lookup.calls == [(STORE, ("us",))]


def test_default_lookup_has_no_fallback_list(monkeypatch: pytest.MonkeyPatch) -> None:
    from apprestore_core import catalog, license_gate

    seen: list[object] = []
    monkeypatch.setattr(catalog, "lookup_itunes_offer", lambda sid, countries: seen.append(countries) or None)
    assert license_gate.lookup_offer(STORE, ()) is None
    license_gate.lookup_offer(STORE, ("us",))
    assert seen == [("us",)]


def test_app_missing_in_account_country_is_refused(tmp_path: Path) -> None:
    tools, lookup = Tools(country="ru"), Lookup(found=False)
    with pytest.raises(LicenseDenied, match="цену"):
        run_with_free_license(STORE, Download(tools), tools=tools, lookup=lookup, journal=tmp_path / "j")
    assert tools.purchases == []


def test_download_failure_after_purchase_updates_status_and_still_counts(tmp_path: Path) -> None:
    journal = tmp_path / "j.jsonl"
    tools = Tools()
    with pytest.raises(RuntimeError, match="TLS"):
        run_with_free_license(
            STORE, Download(tools, fail_after_license=True), tools=tools, lookup=Lookup(0), journal=journal
        )
    [entry] = _entries(journal)
    assert entry["status"] == "acquired_download_failed"
    assert read_counts(journal) == (1, 1)


def test_failed_purchase_is_not_journaled(tmp_path: Path) -> None:
    journal = tmp_path / "j.jsonl"
    tools = Tools(purchase_ok=False)
    with pytest.raises(ToolUnavailable):
        run_with_free_license(STORE, Download(tools), tools=tools, lookup=Lookup(0), journal=journal)
    assert tools.purchases == [STORE]
    assert not journal.exists()


@pytest.mark.parametrize("price", [0.99, 1, "149.0"])
def test_paid_app_is_refused_before_purchase(tmp_path: Path, price: object) -> None:
    tools = Tools()
    with pytest.raises(LicenseDenied, match="платное"):
        run_with_free_license(STORE, Download(tools), tools=tools, lookup=Lookup(price), journal=tmp_path / "j")
    assert tools.purchases == []
    assert not (tmp_path / "j").exists()


@pytest.mark.parametrize("price", [None, "free"])
def test_unknown_price_is_refused(tmp_path: Path, price: object) -> None:
    tools = Tools()
    with pytest.raises(LicenseDenied, match="цену"):
        run_with_free_license(STORE, Download(tools), tools=tools, lookup=Lookup(price), journal=tmp_path / "j")
    assert tools.purchases == []


def test_lookup_failure_is_refused(tmp_path: Path) -> None:
    def boom(_s: str, _c: object) -> dict:
        raise OSError("offline")

    tools = Tools()
    with pytest.raises(LicenseDenied):
        run_with_free_license(STORE, Download(tools), tools=tools, lookup=boom, journal=tmp_path / "j")
    assert tools.purchases == []


def test_daily_limit_of_five_counts_both_statuses(tmp_path: Path) -> None:
    journal = tmp_path / "j.jsonl"
    _fill(journal, today=4)
    with journal.open("a", encoding="utf-8") as handle:
        now = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
        handle.write(json.dumps({"time": now, "track_id": "2", "status": "acquired_download_failed"}) + "\n")
    tools = Tools()
    with pytest.raises(LicenseDenied, match="5/5"):
        run_with_free_license(STORE, Download(tools), tools=tools, lookup=Lookup(0), journal=journal)
    assert tools.purchases == []


def test_four_today_still_allows_the_fifth(tmp_path: Path) -> None:
    journal = tmp_path / "j.jsonl"
    _fill(journal, today=4)
    tools = Tools()
    run_with_free_license(STORE, Download(tools), tools=tools, lookup=Lookup(0), journal=journal)
    assert tools.purchases == [STORE]
    assert read_counts(journal) == (5, 5)


def test_total_limit_of_fifteen_counts_bench_lines_too(tmp_path: Path) -> None:
    journal = tmp_path / "j.jsonl"
    _fill(journal, old=15)
    tools = Tools()
    with pytest.raises(LicenseDenied, match="15/15"):
        run_with_free_license(STORE, Download(tools), tools=tools, lookup=Lookup(0), journal=journal)
    assert tools.purchases == []


def test_acquire_false_never_purchases(tmp_path: Path) -> None:
    tools = Tools()
    with pytest.raises(RuntimeError, match="license is required"):
        run_with_free_license(
            STORE, Download(tools), tools=tools, acquire=False, lookup=Lookup(0), journal=tmp_path / "j"
        )
    assert tools.purchases == []


def test_other_errors_are_not_a_reason_to_purchase(tmp_path: Path) -> None:
    def attempt() -> str:
        raise RuntimeError("store=1 without --purchase: net/http: TLS handshake timeout")

    tools, lookup = Tools(), Lookup(0)
    with pytest.raises(RuntimeError, match="TLS"):
        run_with_free_license(STORE, attempt, tools=tools, lookup=lookup, journal=tmp_path / "j")
    assert tools.purchases == [] and lookup.calls == []


# ---------------------------------------------------------------- purchase_uncertain


def _uncertain_errors():
    from apprestore_core.command import CommandError
    from apprestore_core.models import CommandResult

    timeout = CommandError(CommandResult(("ipatool",), 124, "", ""), "command timed out: ipatool")
    return [
        ToolUnavailable("net/http: TLS handshake timeout"),
        ToolUnavailable("dial tcp: i/o timeout"),
        ToolUnavailable("context deadline exceeded"),
        timeout,
        ToolUnavailable("ipatool purchase failed"),  # no answer at all: unknown
    ]


@pytest.mark.parametrize("error", _uncertain_errors(), ids=lambda e: str(e)[:24])
def test_network_or_timeout_on_purchase_is_journaled_as_uncertain(tmp_path: Path, error) -> None:
    from apprestore_core.license_guard import check_can_acquire

    journal = tmp_path / "j.jsonl"
    tools = Tools(error=error)
    with pytest.raises(type(error)):
        run_with_free_license(STORE, Download(tools), tools=tools, lookup=Lookup(0), journal=journal)
    [entry] = _entries(journal)
    assert entry["status"] == "purchase_uncertain"
    assert entry["track_id"] == STORE and entry["app_id"] == STORE
    # Макс's own counters count it (ACQUIRED_STATUSES includes purchase_uncertain).
    assert read_counts(journal) == (1, 1)
    verdict = check_can_acquire("999", 0, journal_path=journal)
    assert verdict.used_today == 1 and verdict.used_total == 1


@pytest.mark.parametrize(
    "message",
    [
        "purchasing paid apps is not supported",
        "failed to purchase item with param 'GAME': item is temporarily unavailable",
        "ipatool is not authenticated",
        "keychain passphrase is required",
        "failed to purchase app",
        "app not found",
    ],
)
def test_explicit_refusal_on_purchase_is_not_journaled(tmp_path: Path, message: str) -> None:
    journal = tmp_path / "j.jsonl"
    tools = Tools(error=ToolUnavailable(message))
    with pytest.raises(ToolUnavailable):
        run_with_free_license(STORE, Download(tools), tools=tools, lookup=Lookup(0), journal=journal)
    assert not journal.exists()


def test_uncertain_lines_count_toward_the_daily_limit(tmp_path: Path) -> None:
    journal = tmp_path / "j.jsonl"
    _fill(journal, today=4)
    with journal.open("a", encoding="utf-8") as handle:
        now = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
        handle.write(json.dumps({"time": now, "track_id": "3", "status": "purchase_uncertain"}) + "\n")
    tools = Tools()
    with pytest.raises(LicenseDenied, match="5/5"):
        run_with_free_license(STORE, Download(tools), tools=tools, lookup=Lookup(0), journal=journal)
    assert tools.purchases == []
