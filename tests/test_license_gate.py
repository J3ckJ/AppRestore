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
    first, amend = _entries(journal)  # append-only: the purchase line stays as written
    assert first["status"] == "acquired" and first["id"]
    assert amend["amends"] == first["id"] and amend["status"] == "acquired_download_failed"
    assert amend["track_id"] == STORE and "voids" not in amend


def test_download_failure_after_purchase_still_counts_once(tmp_path: Path) -> None:
    journal = tmp_path / "j.jsonl"
    tools = Tools()
    with pytest.raises(RuntimeError, match="TLS"):
        run_with_free_license(
            STORE, Download(tools, fail_after_license=True), tools=tools, lookup=Lookup(0), journal=journal
        )
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


# ------------------------------------------- Apple's explicit "no" (Макс's live attempt)

# ipatool 2.6.0 on a live account, rc=1: HTTP 200 with failureType 2040 and
# customerMessage "Purchase of this item is not currently available".
APPLE_2040_ERROR = "failed to purchase item with param 'STDQ': Purchase of this item is not currently available"
APPLE_2040_LINE = json.dumps(
    {"level": "error", "error": APPLE_2040_ERROR, "success": False, "time": "2026-10-10T18:05:00+03:00"}
)


def _real_tools(stdout: str, stderr: str, returncode: int = 1):
    from unittest.mock import patch

    from apprestore_core.models import CommandResult
    from apprestore_core.tools import AppRestoreTools

    class Runner:
        def __init__(self) -> None:
            self.calls: list[tuple] = []

        def run(self, args, **_kwargs):
            self.calls.append(tuple(args))
            return CommandResult(tuple(args), returncode, stdout, stderr)

    class Tools(AppRestoreTools):
        licensed = False

        def account_country(self) -> str:
            return "us"

    runner = Runner()
    tools = Tools(runner)  # type: ignore[arg-type]
    patches = (
        patch("apprestore_core.tools.resolve_tool", return_value="ipatool"),
        patch.object(AppRestoreTools, "_ipatool_env", return_value={}),
    )
    return tools, runner, patches


@pytest.mark.parametrize(
    "stdout, stderr",
    [
        (APPLE_2040_LINE + "\n", ""),  # hidden terminal: everything on stdout
        ("", APPLE_2040_LINE + "\n"),  # plain run: ipatool errors go to stderr
        ("", f'6:05PM ERR error="{APPLE_2040_ERROR}" success=false\n'),  # text format
    ],
    ids=["pty-json", "stderr-json", "stderr-text"],
)
def test_apple_2040_refusal_exact_output_is_refused_and_not_journaled(tmp_path: Path, stdout, stderr) -> None:
    journal = tmp_path / "j.jsonl"
    tools, runner, patches = _real_tools(stdout, stderr)
    with patches[0], patches[1]:
        with pytest.raises(ToolUnavailable) as caught:
            run_with_free_license(STORE, Download(tools), tools=tools, lookup=Lookup(0), journal=journal)  # type: ignore[arg-type]
    assert [call[-3:] for call in runner.calls] == [("purchase", "--app-id", STORE)]
    assert not journal.exists(), "Apple's explicit refusal must not be journaled"
    from apprestore_core.license_gate import purchase_outcome

    assert purchase_outcome(caught.value) == "refused"
    from apprestore_gui.errors import STORE_REFUSED_TEXT, explain_user_error

    assert explain_user_error(str(caught.value)) == STORE_REFUSED_TEXT
    assert STORE_REFUSED_TEXT == "Apple сейчас не выдаёт это приложение для вашего аккаунта."


@pytest.mark.parametrize(
    "message",
    [
        APPLE_2040_ERROR,
        APPLE_2040_ERROR.upper(),
        "PURCHASE OF THIS ITEM IS NOT CURRENTLY AVAILABLE",
        '{"FailureType":"2040","customerMessage":"…"}',
        '{"failureType": 2040}',
        "failureType=2040",
        "failed to purchase item: 2040",
    ],
)
def test_store_refusal_is_case_insensitive(message: str) -> None:
    from apprestore_core.license_gate import is_store_refusal, purchase_outcome

    assert is_store_refusal(message)
    assert purchase_outcome(ToolUnavailable(message)) == "refused"


APPLE_128_ERROR = "failed to purchase item with param 'STDQ': Account Not In This Store"
APPLE_128_LINE = json.dumps(
    {"level": "error", "error": APPLE_128_ERROR, "success": False, "time": "2026-10-10T18:50:00+03:00"}
)


@pytest.mark.parametrize(
    ("stdout", "stderr"),
    [
        (APPLE_128_LINE + "\n", ""),
        ("", APPLE_128_LINE + "\n"),
        ("", f'6:50PM ERR error="{APPLE_128_ERROR}" success=false\n'),
    ],
    ids=["pty-json", "stderr-json", "stderr-text"],
)
def test_account_not_in_this_store_exact_output_is_refused_and_not_journaled(tmp_path: Path, stdout, stderr) -> None:
    journal = tmp_path / "j.jsonl"
    tools, runner, patches = _real_tools(stdout, stderr)
    with patches[0], patches[1]:
        with pytest.raises(ToolUnavailable) as caught:
            run_with_free_license(STORE, Download(tools), tools=tools, lookup=Lookup(0), journal=journal)  # type: ignore[arg-type]
    assert [call[-3:] for call in runner.calls] == [("purchase", "--app-id", STORE)]
    assert not journal.exists(), "Apple's explicit refusal must not be journaled"
    from apprestore_core.license_gate import purchase_outcome
    from apprestore_gui.errors import STORE_MISMATCH_TEXT, explain_user_error

    assert purchase_outcome(caught.value) == "refused"
    assert explain_user_error(str(caught.value)) == STORE_MISMATCH_TEXT
    assert STORE_MISMATCH_TEXT == (
        "Магазин в текущем входе не совпадает со страной вашего Apple ID. Выйдите из аккаунта и войдите заново."
    )
    assert "vpn" not in STORE_MISMATCH_TEXT.casefold() and "регион" not in STORE_MISMATCH_TEXT


@pytest.mark.parametrize(
    "message",
    [
        APPLE_128_ERROR,
        APPLE_128_ERROR.upper(),
        "account not in this store",
        '{"failureType":"-128","customerMessage":"Account Not In This Store"}',
        '{"FailureType": -128}',
        "failureType=-128",
    ],
)
def test_account_not_in_this_store_is_refused_case_insensitive(message: str) -> None:
    from apprestore_core.license_gate import is_store_refusal, purchase_outcome

    assert is_store_refusal(message)
    assert purchase_outcome(ToolUnavailable(message)) == "refused"


@pytest.mark.parametrize(
    "message",
    [
        "failed to purchase item: failed to send http request: dial tcp: i/o timeout",
        "failed to purchase item: net/http: TLS handshake timeout",
        "something nobody has seen before",
        "listening on port 2040",  # a bare number alone is not Apple's answer
    ],
)
def test_network_wrapped_or_unknown_purchase_errors_stay_uncertain(message: str) -> None:
    from apprestore_core.license_gate import is_store_refusal, purchase_outcome

    assert not is_store_refusal(message)
    assert purchase_outcome(ToolUnavailable(message)) == "uncertain"


# ------------------------------------------- -128 «Account Not In This Store» (Макс: STORE_MISMATCH)

APPLE_128_ERROR = "failed to purchase item with param 'STDQ': Account Not In This Store"
APPLE_128_LINE = json.dumps(
    {"level": "error", "error": APPLE_128_ERROR, "success": False, "time": "2026-10-10T19:05:00+03:00"}
)


@pytest.mark.parametrize(
    "stdout, stderr",
    [
        (APPLE_128_LINE + "\n", ""),
        ("", APPLE_128_LINE + "\n"),
        ("", f'7:05PM ERR error="{APPLE_128_ERROR}" success=false\n'),
    ],
    ids=["pty-json", "stderr-json", "stderr-text"],
)
def test_store_mismatch_is_refused_and_not_journaled(tmp_path: Path, stdout, stderr) -> None:
    journal = tmp_path / "j.jsonl"
    tools, runner, patches = _real_tools(stdout, stderr)
    with patches[0], patches[1]:
        with pytest.raises(ToolUnavailable) as caught:
            run_with_free_license(STORE, Download(tools), tools=tools, lookup=Lookup(0), journal=journal)  # type: ignore[arg-type]
    assert [call[-3:] for call in runner.calls] == [("purchase", "--app-id", STORE)]
    assert not journal.exists(), "-128 is a refusal: no journal line"
    from apprestore_core.license_gate import is_store_mismatch, purchase_outcome

    assert purchase_outcome(caught.value) == "refused"
    assert is_store_mismatch(str(caught.value))


@pytest.mark.parametrize(
    "message, expected",
    [
        ("Account Not In This Store", True),
        ("ACCOUNT NOT IN THIS STORE", True),
        ('{"failureType":"-128"}', True),
        ("error -128", True),
        ("error -1280", False),
        ("2-128", False),
        ("password token is expired", False),
    ],
)
def test_store_mismatch_detection(message: str, expected: bool) -> None:
    from apprestore_core.ipatool_api import SESSION_CODES, TRANSPORT_CODES, ErrorCode
    from apprestore_core.license_gate import is_store_mismatch

    assert is_store_mismatch(message) is expected
    assert ErrorCode.STORE_MISMATCH not in SESSION_CODES
    assert ErrorCode.STORE_MISMATCH not in TRANSPORT_CODES


def test_gui_text_for_store_mismatch_is_recognised_again() -> None:
    from apprestore_core.license_gate import STORE_MISMATCH_TEXT, is_limit_refusal, is_store_mismatch, refusal_text
    from apprestore_core.license_journal import Verdict
    from apprestore_gui.errors import explain_user_error

    text = explain_user_error(APPLE_128_ERROR)
    assert text == STORE_MISMATCH_TEXT and text.startswith("Магазин в текущем входе не совпадает со страной вашего Apple ID.")
    assert is_store_mismatch(text)
    limit = refusal_text(Verdict(False, "лимит за сутки", 5, 7))
    assert is_limit_refusal(limit) and not is_limit_refusal(text)


class _MismatchService:
    def __init__(self, **_kwargs: object) -> None:
        self.tools = type("T", (), {"ipatool_authenticated": lambda self: True})()

    def download(self, *_args, **_kwargs):
        raise ToolUnavailable(APPLE_128_ERROR)

    download_by_store_id = download


@pytest.mark.parametrize("argv", [["download", "com.example.alpha"], ["download", "--acquire-license", "389801252"]])
def test_cli_store_mismatch_says_sign_out_and_in(argv, capsys) -> None:
    from unittest.mock import patch

    from apprestore_core import cli

    with patch("apprestore_core.cli.AppRestoreService", _MismatchService):
        assert cli.main(argv) == 1
    err = capsys.readouterr().err
    assert "Магазин в текущем входе не совпадает со страной вашего Apple ID." in err
    assert "apprestore auth --revoke" in err and "apprestore auth --email" in err
    assert "vpn" not in err.casefold()


def test_cli_json_store_mismatch_has_message_and_hint(capsys) -> None:
    from unittest.mock import patch

    from apprestore_core import cli
    from apprestore_core.license_gate import STORE_MISMATCH_TEXT

    with patch("apprestore_core.cli.AppRestoreService", _MismatchService):
        assert cli.main(["--json", "download", "com.example.alpha"]) == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["error"] == APPLE_128_ERROR
    assert payload["message"] == STORE_MISMATCH_TEXT
    assert "auth --revoke" in payload["hint"]


def test_cli_other_errors_unchanged() -> None:
    from apprestore_core import cli

    assert cli._error_text(ToolUnavailable("boom")) == "boom"
