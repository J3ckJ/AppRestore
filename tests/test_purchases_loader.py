"""Session check → UI state, and the purchase list loader (fakes, no network)."""

from __future__ import annotations

import json
import subprocess
import threading
from pathlib import Path

import pytest

import apprestore_core.ipatool_api as api
from apprestore_core.purchases_cache import CachedPurchase, PurchasesCache
from apprestore_gui import purchases as pm

EMAIL = "person@example.com"
OTHER = "other@example.com"


def _line(**fields) -> str:
    return json.dumps({"level": "info", **fields, "time": "2026-10-10T18:00:00+03:00"}) + "\n"


def _err(message: str) -> api.RunResult:
    return api.RunResult(1, "", json.dumps({"level": "error", "error": message, "success": False}) + "\n")


def _apps(start: int, count: int) -> list[dict]:
    return [
        {"id": i, "bundleID": f"com.example.app{i}", "name": f"App {i}", "purchaseDate": "2024-02-01T10:00:00Z", "version": "1"}
        for i in range(start, start + count)
    ]


class Runner:
    """Answers by subcommand; ``gate`` lets a test hold a page request."""

    def __init__(self, answers: dict, *, total: int | None = None) -> None:
        self.answers = answers
        self.calls: list[list[str]] = []
        self.gates: dict[int, threading.Event] = {}
        self.entered: dict[int, threading.Event] = {}
        self.total = total

    def __call__(self, argv, timeout, env=None, stdin=None):
        argv = list(argv)
        sub = argv[1]
        if sub == "--help":  # newer ipatool_api probes --keychain-passphrase-stdin support
            return api.RunResult(0, "Usage: ipatool [command]\n", "")
        self.calls.append(argv)
        if sub == "list-purchases":
            if "--all" in argv:
                answer = self.answers.get("all")
                if answer is None:
                    return api.RunResult(1, "", "ERR error=\"unknown flag: --all\" success=false\n")
                return answer
            page = int(argv[argv.index("--page") + 1])
            self.entered.setdefault(page, threading.Event()).set()
            gate = self.gates.get(page)
            if gate is not None:
                assert gate.wait(5)
            return self.answers["pages"][page]
        answer = self.answers[sub]
        if isinstance(answer, BaseException):
            raise answer
        return answer


def _client(runner):
    return lambda: api.IpatoolClient("/x/ipatool", keychain_passphrase="", runner=runner)


def _page(page: int, count: int, total: int | None) -> api.RunResult:
    fields = {"count": count, "page": page, "apps": _apps((page - 1) * 100 + 1, count)}
    if total is not None:
        fields["totalCount"] = total
    return api.RunResult(0, _line(**fields), "")


# ------------------------------------------------------------------ session


@pytest.mark.parametrize(
    "answer, state, relogin",
    [
        (api.RunResult(0, _line(bundleID="com.apple.store.Jolly", externalVersionIdentifiers=["1"], success=True), ""), "alive", False),
        (_err("license is required"), "alive", False),
        (_err("password token is expired"), "expired", True),
        (_err("auth code is required"), "expired", True),
        (_err("failed to get account: failed to get item: The specified item could not be found in the keyring"), "expired", True),
        (_err('dial tcp: lookup init.itunes.apple.com: no such host'), "offline", False),
        (subprocess.TimeoutExpired(["ipatool"], 8), "offline", False),
        (_err("something nobody has seen before"), "offline", False),
    ],
)
def test_three_session_outcomes_map_to_ui_states(answer, state, relogin) -> None:
    views: list[pm.SessionView] = []
    checker = pm.SessionChecker(_client(Runner({"list-versions": answer})), views.append)
    assert checker.start()
    checker.join(5)
    assert views[0] == pm.SESSION_CHECKING
    final = views[-1]
    assert final.state == state and final.relogin is relogin
    assert final.sign_out is False  # no outcome signs the user out
    if state == "offline":
        assert final.note == pm.OFFLINE_NOTE
    assert EMAIL not in final.note


def test_missing_binary_is_an_error_state_not_expired() -> None:
    views: list[pm.SessionView] = []
    checker = pm.SessionChecker(_client(Runner({"list-versions": FileNotFoundError()})), views.append)
    checker.start()
    checker.join(5)
    assert views[-1].state == "error" and not views[-1].relogin
    assert "ipatool" in views[-1].note


def test_session_check_runs_off_the_calling_thread() -> None:
    release = threading.Event()
    seen: list[str] = []

    class Slow:
        def session_alive(self, timeout):
            seen.append(threading.current_thread().name)
            release.wait(5)
            return api.SessionCheck(api.SessionState.ALIVE, 0.1)

    views: list[pm.SessionView] = []
    checker = pm.SessionChecker(lambda: Slow(), views.append)
    assert checker.start()
    assert not checker.start()  # one probe at a time
    assert views == [pm.SESSION_CHECKING]  # start() returned while the probe hangs
    release.set()
    checker.join(5)
    assert seen == ["apprestore-session-alive"]
    assert views[-1].state == "alive"


# ------------------------------------------------------------------ loader


def _loader(tmp_path: Path, runner, **kwargs):
    views: list[pm.PurchasesView] = []
    cache = PurchasesCache(tmp_path)
    loader = pm.PurchasesLoader(cache, _client(runner), views.append, **kwargs)
    return loader, cache, views


def test_all_mode_one_page_saves_cache(tmp_path: Path) -> None:
    runner = Runner({"all": api.RunResult(0, _line(count=150, totalCount=150, page=1, apps=_apps(1, 150)), "")})
    loader, cache, views = _loader(tmp_path, runner)
    loader.set_account(EMAIL)
    assert loader.start()
    loader.join(5)
    assert [c[1:3] for c in runner.calls] == [["list-purchases", "--all"]]
    final = views[-1]
    assert not final.busy and final.count == 150 and final.progress == "150 из 150"
    snapshot = cache.load(cache.account_key(EMAIL))
    assert snapshot is not None and len(snapshot.items) == 150 and snapshot.complete


def test_pages_fill_in_with_progress_and_dedupe(tmp_path: Path) -> None:
    # Old ipatool (no --all) → pages; page 2 repeats one id of page 1.
    page2 = _page(2, 50, 150)
    payload = json.loads(page2.stdout)
    payload["apps"][0]["id"] = 1
    page2 = api.RunResult(0, json.dumps(payload) + "\n", "")
    runner = Runner({"pages": {1: _page(1, 100, 150), 2: page2}})
    loader, cache, views = _loader(tmp_path, runner)
    loader.set_account(EMAIL)
    loader.start()
    loader.join(5)
    progress = [v.progress for v in views if v.busy]
    assert "100 из 150" in progress and "149 из 150" in progress
    final = views[-1]
    assert final.count == 149 and not final.busy
    ids = [row["trackId"] for row in final.rows]
    assert len(ids) == len(set(ids))


def test_progress_without_total_is_just_a_count(tmp_path: Path) -> None:
    runner = Runner({"pages": {1: _page(1, 100, None), 2: _page(2, 7, None)}})
    loader, _cache, views = _loader(tmp_path, runner, fetch_all=False)
    loader.set_account(EMAIL)
    loader.start()
    loader.join(5)
    assert all("--all" not in call for call in runner.calls)
    assert [v.progress for v in views if v.busy][-2:] == ["100", "107"]
    assert views[-1].progress == "107"


def test_cached_list_shows_first_then_refresh_replaces_it(tmp_path: Path) -> None:
    cache = PurchasesCache(tmp_path)
    cache.save(cache.account_key(EMAIL), [CachedPurchase(999, "com.example.old", "Old", "")], total=1)
    runner = Runner({"all": api.RunResult(0, _line(totalCount=2, apps=_apps(1, 2)), "")})
    loader, _cache, views = _loader(tmp_path, runner)
    loader.set_account(EMAIL)
    shown = views[-1]
    assert shown.from_cache and [r["trackId"] for r in shown.rows] == ["999"]
    loader.start()
    loader.join(5)
    assert views[-1].rows and [r["trackId"] for r in views[-1].rows] == ["1", "2"]
    assert not views[-1].from_cache


def test_cancel_sets_event_and_stops_before_next_page(tmp_path: Path) -> None:
    cache = PurchasesCache(tmp_path)
    cache.save(cache.account_key(EMAIL), [CachedPurchase(5, "com.example.five", "Five", "")])
    runner = Runner({"pages": {1: _page(1, 100, 300), 2: _page(2, 100, 300), 3: _page(3, 100, 300)}})
    runner.gates[2] = threading.Event()
    runner.entered[2] = threading.Event()
    loader, _cache, views = _loader(tmp_path, runner, fetch_all=False)
    loader.set_account(EMAIL)
    loader.start()
    assert runner.entered[2].wait(5)  # page 2 is running
    loader.cancel()
    assert views[-1].busy and "Останавливаем" in views[-1].note
    runner.gates[2].set()
    loader.join(5)
    pages = [c[c.index("--page") + 1] for c in runner.calls if "--page" in c]
    assert pages == ["1", "2"]  # page 3 never requested
    final = views[-1]
    assert not final.busy and final.note.startswith("Остановлено")
    # A cancelled refresh does not overwrite the cache.
    snapshot = cache.load(cache.account_key(EMAIL))
    assert snapshot is not None and [i.track_id for i in snapshot.items] == [5]


def test_ipatool_error_is_shown_without_personal_data(tmp_path: Path) -> None:
    runner = Runner({"all": _err(f"password token is expired for {EMAIL} guid=ABCDEF0123")})
    loader, _cache, views = _loader(tmp_path, runner)
    loader.set_account(EMAIL)
    loader.start()
    loader.join(5)
    final = views[-1]
    assert final.note == api.MESSAGES_RU[api.ErrorCode.TOKEN_EXPIRED]
    assert final.session_problem
    assert EMAIL not in final.note and "ABCDEF" not in final.note


def test_sign_out_deletes_cache(tmp_path: Path) -> None:
    runner = Runner({"all": api.RunResult(0, _line(totalCount=1, apps=_apps(1, 1)), "")})
    loader, cache, views = _loader(tmp_path, runner)
    loader.set_account(EMAIL)
    loader.start()
    loader.join(5)
    assert cache.path.exists()
    loader.forget()
    assert not cache.path.exists()
    assert views[-1] == pm.PurchasesView()


def test_switching_account_deletes_old_cache(tmp_path: Path) -> None:
    runner = Runner({"all": api.RunResult(0, _line(totalCount=1, apps=_apps(1, 1)), "")})
    loader, cache, views = _loader(tmp_path, runner)
    loader.set_account(EMAIL)
    loader.start()
    loader.join(5)
    assert cache.path.exists()
    assert not loader.set_account(EMAIL.upper())  # same account, case-insensitive
    assert cache.path.exists()
    assert loader.set_account(OTHER)
    assert not cache.path.exists()
    assert views[-1].rows == ()


def test_switch_during_refresh_drops_the_stale_result(tmp_path: Path) -> None:
    runner = Runner({"pages": {1: _page(1, 100, 200), 2: _page(2, 100, 200)}})
    runner.gates[1] = threading.Event()
    runner.entered[1] = threading.Event()
    loader, cache, views = _loader(tmp_path, runner, fetch_all=False)
    loader.set_account(EMAIL)
    loader.start()
    assert runner.entered[1].wait(5)
    loader.set_account(OTHER)
    runner.gates[1].set()
    loader.join(5)
    assert not cache.path.exists()  # nothing written for the old account
    assert views[-1].rows == () and not views[-1].busy


def test_start_without_account_asks_to_sign_in(tmp_path: Path) -> None:
    loader, _cache, views = _loader(tmp_path, Runner({}))
    assert not loader.start()
    assert "Войдите" in views[-1].note


def test_gui_runner_keeps_passphrase_out_of_argv(monkeypatch) -> None:
    seen: dict = {}

    def fake_pty(args, *, passphrase, timeout, env):
        seen.update(args=list(args), passphrase=passphrase, env=env)
        from apprestore_core.models import CommandResult

        return CommandResult(tuple(args), 0, _line(success=True), "")

    import types
    import sys

    fake_module = types.SimpleNamespace(run_pty_command=fake_pty)
    monkeypatch.setitem(sys.modules, "apprestore_gui.auth_pty", fake_module)
    runner = pm.gui_runner(lambda: "s3cret", lambda: {"HTTPS_PROXY": "http://proxy:3128"})
    client = api.IpatoolClient("/x/ipatool", keychain_passphrase="", runner=runner)
    assert client.session_alive().state is api.SessionState.ALIVE
    assert "s3cret" not in seen["args"] and "--keychain-passphrase" not in seen["args"]
    assert "--non-interactive" not in seen["args"]
    assert seen["passphrase"] == "s3cret"
    assert seen["env"] == {"HTTPS_PROXY": "http://proxy:3128"}


def test_gui_runner_maps_pty_timeout(monkeypatch) -> None:
    import sys
    import types

    from apprestore_core.models import CommandResult

    monkeypatch.setitem(
        sys.modules,
        "apprestore_gui.auth_pty",
        types.SimpleNamespace(run_pty_command=lambda args, **_: CommandResult(tuple(args), 124, "", "command timed out")),
    )
    client = api.IpatoolClient("/x/ipatool", keychain_passphrase="", runner=pm.gui_runner(lambda: "p", lambda: {}))
    check = client.session_alive()
    assert check.state is api.SessionState.NO_NETWORK
    assert check.error is not None and check.error.code is api.ErrorCode.TIMEOUT


def test_ipatool_error_without_russian_text_is_not_quoted() -> None:
    from apprestore_gui.errors import IPATOOL_FALLBACK_TEXT, explain_ipatool_error

    error = api.IpatoolError(api.ErrorCode.UNKNOWN, f"raw {EMAIL} guid=ABC")
    error.message_ru = ""
    text = explain_ipatool_error(error)
    assert text == IPATOOL_FALLBACK_TEXT
    assert EMAIL not in text and "raw" not in text
