"""Tests for ipatool_api.py on recorded ipatool outputs (no network).

Fixtures marked REAL were recorded on 2026-10-10 from ipatool 2.6.0
(cde7d00 build) with email, guid and app data removed.  SYNTH fixtures follow
the same JSON shape for answers we must not provoke on the live account
(an expired token would make ipatool re-login).
"""

from __future__ import annotations

import json
import subprocess
import threading

import pytest

import apprestore_core.ipatool_api as api

PASS = "secret-passphrase-for-tests"


def line(**fields) -> str:
    return json.dumps({"level": "info", **fields, "time": "2026-10-10T17:55:00+03:00"}) + "\n"


def err(message: str) -> str:
    return json.dumps({"level": "error", "error": message, "success": False, "time": "2026-10-10T17:55:00+03:00"}) + "\n"


# REAL: list-versions on the live session (version id replaced)
LIST_VERSIONS_OK = line(bundleID="com.apple.store.Jolly", externalVersionIdentifiers=["800000001"], success=True)
# REAL: list-versions with HTTPS_PROXY pointing at a closed port
PROXY_REFUSED = err(
    'failed to send http request: request failed: Post "https://p29-buy.itunes.apple.com/WebObjects/'
    'MZFinance.woa/wa/volumeStoreDownloadProduct?guid=822B383DBCD0": failed to make round trip: '
    "proxyconnect tcp: dial tcp 127.0.0.1:9: connect: connection refused"
)
# REAL: wrong / missing passphrase, empty state directory
WRONG_PASSPHRASE = err("failed to get account: failed to get item: aes.KeyUnwrap(): integrity check failed.")
NO_PASSPHRASE = err(
    "failed to get account: failed to get item: keychain passphrase is required when not running in "
    'interactive mode; use the "--keychain-passphrase" flag'
)
NO_ACCOUNT = err("failed to get account: failed to get item: The specified item could not be found in the keyring")
# SYNTH: what ipatool prints when 2034 and the silent re-login both fail
TOKEN_EXPIRED = err("password token is expired")
AUTH_CODE_REQUIRED = err("auth code is required")
LICENSE_REQUIRED = err("license is required")
DNS_FAIL = err('failed to get bag: request failed: Get "https://init.itunes.apple.com/bag.xml": dial tcp: lookup init.itunes.apple.com: no such host')


class FakeRunner:
    """Replays queued (stdout, stderr, rc) answers or raises queued exceptions."""

    def __init__(self, *answers):
        self.answers = list(answers)
        self.calls: list[list[str]] = []
        self.timeouts: list[float] = []

    def __call__(self, argv, timeout):
        self.calls.append(list(argv))
        self.timeouts.append(timeout)
        answer = self.answers.pop(0)
        if isinstance(answer, BaseException):
            raise answer
        if isinstance(answer, str):  # error line -> stderr, rc 1
            return api.RunResult(1, "", answer)
        return answer


def ok(stdout: str) -> api.RunResult:
    return api.RunResult(0, stdout, "")


def client(runner) -> api.IpatoolClient:
    return api.IpatoolClient("/x/ipatool", keychain_passphrase=PASS, runner=runner)


# ---------------------------------------------------------------- session_alive


def test_session_alive_alive_on_versions():
    runner = FakeRunner(ok(LIST_VERSIONS_OK))
    check = client(runner).session_alive(timeout=8)
    assert check.state is api.SessionState.ALIVE
    assert check.error is None
    argv = runner.calls[0]
    assert argv[1:4] == ["list-versions", "-i", str(api.PROBE_APP_ID)]
    assert "--format" in argv and "json" in argv and "--non-interactive" in argv
    assert not any("purchase" in a for a in argv), "probe must never purchase"
    assert runner.timeouts == [8]


def test_session_alive_license_required_still_alive():
    check = client(FakeRunner(LICENSE_REQUIRED)).session_alive()
    assert check.state is api.SessionState.ALIVE


@pytest.mark.parametrize(
    "answer, code",
    [
        (TOKEN_EXPIRED, api.ErrorCode.TOKEN_EXPIRED),
        (AUTH_CODE_REQUIRED, api.ErrorCode.AUTH_CODE_REQUIRED),
        (NO_ACCOUNT, api.ErrorCode.NOT_SIGNED_IN),
        (WRONG_PASSPHRASE, api.ErrorCode.KEYCHAIN_PASSPHRASE_WRONG),
        (NO_PASSPHRASE, api.ErrorCode.KEYCHAIN_PASSPHRASE_REQUIRED),
    ],
)
def test_session_alive_expired(answer, code):
    check = client(FakeRunner(answer)).session_alive()
    assert check.state is api.SessionState.EXPIRED
    assert check.error is not None and check.error.code is code
    assert check.message_ru == api.MESSAGES_RU[code]


@pytest.mark.parametrize(
    "answer, code",
    [
        (PROXY_REFUSED, api.ErrorCode.NETWORK),
        (DNS_FAIL, api.ErrorCode.NETWORK),
        (subprocess.TimeoutExpired(["ipatool"], 8), api.ErrorCode.TIMEOUT),
        (api.RunResult(1, "", "panic: something odd\n"), api.ErrorCode.UNKNOWN),
    ],
)
def test_session_alive_no_network(answer, code):
    check = client(FakeRunner(answer)).session_alive()
    assert check.state is api.SessionState.NO_NETWORK
    assert check.error is not None and check.error.code is code


def test_session_alive_binary_missing_raises():
    with pytest.raises(api.IpatoolError) as caught:
        client(FakeRunner(FileNotFoundError())).session_alive()
    assert caught.value.code is api.ErrorCode.BINARY_MISSING


def test_error_detail_is_redacted():
    leak = err(f"failed for someone@example.com with {PASS} guid=ABCDEF012345")
    with pytest.raises(api.IpatoolError) as caught:
        client(FakeRunner(leak))._call(["auth", "info"], 5)
    detail = caught.value.detail
    assert "someone@example.com" not in detail and PASS not in detail and "ABCDEF012345" not in detail
    assert PASS not in repr(caught.value) and PASS not in str(caught.value)


# --------------------------------------------------------------- iter_purchases


def purchases_page(page: int, count: int, total, start_id: int = 1000) -> str:
    apps = [
        {
            "id": start_id + i,
            "bundleID": f"com.example.app{start_id + i}",
            "name": f"App {start_id + i}",
            "version": "",
            "price": 0,
            "purchaseDate": "2024-05-0%dT10:00:00Z" % (1 + i % 9),
            "platforms": ["iphone", "ipad"],
        }
        for i in range(count)
    ]
    fields = {"count": count, "page": page, "apps": apps}
    if total is not None:
        fields["totalCount"] = total
    return line(**fields)


def test_iter_purchases_total_and_pages():
    runner = FakeRunner(
        ok(purchases_page(1, 100, 250, 0)),
        ok(purchases_page(2, 100, 250, 100)),
        ok(purchases_page(3, 50, 250, 200)),
    )
    pages = list(client(runner).iter_purchases(prefer_all=False))
    assert [p.page for p in pages] == [1, 2, 3]
    assert all(p.total == 250 and p.pages == 3 for p in pages)
    assert [p.is_last for p in pages] == [False, False, True]
    assert sum(len(p.items) for p in pages) == 250
    first = pages[0].items[0]
    assert first.track_id == 0 and first.bundle_id == "com.example.app0" and first.name == "App 0"
    assert first.purchase_date is not None and first.purchase_date.tzinfo is not None
    assert first.cache_key == (0, "com.example.app0", "App 0", "2024-05-01T10:00:00+00:00")
    assert first.platforms == ("iphone", "ipad")
    assert runner.calls[1][1:6] == ["list-purchases", "--page", "2", "--max-results", "100"]


def test_iter_purchases_without_total():
    runner = FakeRunner(ok(purchases_page(1, 100, None, 0)), ok(purchases_page(2, 7, None, 100)))
    pages = list(client(runner).iter_purchases(prefer_all=False))
    assert [p.total for p in pages] == [None, None]
    assert [p.pages for p in pages] == [None, None]
    assert [p.is_last for p in pages] == [False, True]


def test_iter_purchases_cancel_on_second_page():
    cancel = threading.Event()
    runner = FakeRunner(ok(purchases_page(1, 100, 300, 0)), ok(purchases_page(2, 100, 300, 100)))
    seen = []
    for page in client(runner).iter_purchases(cancel_event=cancel, prefer_all=False):
        seen.append(page.page)
        if page.page == 2:
            cancel.set()  # user pressed "Stop" while page 2 was on screen
    assert seen == [1, 2]
    assert len(runner.calls) == 2, "page 3 must not be requested after cancel"


def test_iter_purchases_cancel_before_second_request():
    cancel = threading.Event()
    runner = FakeRunner(ok(purchases_page(1, 100, 300, 0)))
    gen = client(runner).iter_purchases(cancel_event=cancel, prefer_all=False)
    assert next(gen).page == 1
    cancel.set()
    assert list(gen) == []
    assert len(runner.calls) == 1


def test_iter_purchases_error_is_typed():
    runner = FakeRunner(ok(purchases_page(1, 100, 300, 0)), PROXY_REFUSED)
    gen = client(runner).iter_purchases(prefer_all=False)
    next(gen)
    with pytest.raises(api.IpatoolError) as caught:
        next(gen)
    assert caught.value.code is api.ErrorCode.NETWORK
    assert caught.value.is_transport_problem


def test_iter_purchases_page_size_limit():
    with pytest.raises(api.IpatoolError) as caught:
        list(client(FakeRunner()).iter_purchases(page_size=101, prefer_all=False))
    assert caught.value.code is api.ErrorCode.INVALID_ARGUMENT


# REAL: unpatched ipatool rejects the flag before --format applies (text + ANSI)
UNKNOWN_FLAG_ALL = api.RunResult(
    1, "", '\x1b[90m6:05PM\x1b[0m \x1b[1m\x1b[31mERR\x1b[0m\x1b[0m \x1b[36merror=\x1b[0m\x1b[31m"unknown flag: --all"\x1b[0m \x1b[36msuccess=\x1b[0mfalse\n'
)


def test_iter_purchases_uses_all_in_one_call():
    runner = FakeRunner(ok(purchases_page(1, 2838, 2838, 0)))
    pages = list(client(runner).iter_purchases())
    assert len(pages) == 1 and len(runner.calls) == 1
    assert runner.calls[0][1:3] == ["list-purchases", "--all"]
    assert "--page" not in runner.calls[0] and "--max-results" not in runner.calls[0]
    page = pages[0]
    assert (page.total, page.pages, page.is_last, len(page.items)) == (2838, 1, True, 2838)


def test_iter_purchases_falls_back_to_pages_on_old_ipatool():
    runner = FakeRunner(UNKNOWN_FLAG_ALL, ok(purchases_page(1, 100, 150, 0)), ok(purchases_page(2, 50, 150, 100)))
    pages = list(client(runner).iter_purchases())
    assert [p.page for p in pages] == [1, 2]
    assert runner.calls[0][1:3] == ["list-purchases", "--all"]
    assert runner.calls[1][1:6] == ["list-purchases", "--page", "1", "--max-results", "100"]


def test_all_purchases_on_old_ipatool_is_typed():
    with pytest.raises(api.IpatoolError) as caught:
        client(FakeRunner(UNKNOWN_FLAG_ALL)).all_purchases()
    assert caught.value.code is api.ErrorCode.FLAG_UNSUPPORTED
    assert "\x1b" not in caught.value.detail


def test_iter_purchases_all_respects_cancel_and_errors():
    cancel = threading.Event()
    cancel.set()
    runner = FakeRunner()
    assert list(client(runner).iter_purchases(cancel_event=cancel)) == []
    assert runner.calls == []
    with pytest.raises(api.IpatoolError) as caught:
        list(client(FakeRunner(TOKEN_EXPIRED)).iter_purchases())
    assert caught.value.code is api.ErrorCode.TOKEN_EXPIRED


# --------------------------------------------------------------- misc


def test_account_info_reads_patched_fields_and_hides_email():
    runner = FakeRunner(ok(line(name="Eugene", email="e@example.com", storeFront="143441-1,34", countryCode="US", success=True)))
    info = client(runner).account_info()
    assert (info.store_front, info.country_code) == ("143441-1,34", "US")
    assert "e@example.com" not in repr(info)


def test_account_info_old_binary_has_no_country():
    info = client(FakeRunner(ok(line(name="Eugene", email="e@example.com", success=True)))).account_info()
    assert info.store_front is None and info.country_code is None


def test_every_code_has_russian_text():
    assert set(api.MESSAGES_RU) == set(api.ErrorCode)
