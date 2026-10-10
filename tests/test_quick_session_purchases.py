"""QuickSession wiring: session check states, sign-out deletes the cache."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

pytest.importorskip("PySide6")

import apprestore_core.ipatool_api as api  # noqa: E402
from apprestore_core.purchases_cache import CachedPurchase, PurchasesCache  # noqa: E402
from apprestore_gui import quick_session as qs  # noqa: E402
from apprestore_gui.purchases import PurchasesLoader, SessionChecker  # noqa: E402

EMAIL = "person@example.com"


class FakeService:
    def __init__(self) -> None:
        self.revoked = 0
        self.core = SimpleNamespace(tools=SimpleNamespace(_ipatool_env=lambda: {}))

    def keychain_passphrase(self) -> str:
        return ""

    def revoke(self) -> None:
        self.revoked += 1

    def clear_keychain_passphrase(self) -> None:
        pass


@pytest.fixture(scope="module")
def qapp():
    from PySide6.QtCore import QCoreApplication

    return QCoreApplication.instance() or QCoreApplication([])


def _drain(app) -> None:
    # loginPrompt is emitted from the worker thread; Qt delivers it queued.
    for _ in range(5):
        app.processEvents()


@pytest.fixture()
def session(qapp, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(qs, "GuiService", lambda demo_mode=False: FakeService())
    monkeypatch.setattr(qs, "load_bindings", lambda: {})
    monkeypatch.setattr(qs, "forget_session", lambda email: None)
    monkeypatch.setattr(qs, "PurchasesCache", lambda: PurchasesCache(tmp_path))
    s = qs.QuickSession()
    s._signed = True
    s._phase = "in"
    s._account_email = EMAIL
    prompts: list[str] = []
    s.loginPrompt.connect(prompts.append)
    s._drain = lambda: _drain(qapp)
    return s, prompts, PurchasesCache(tmp_path)


def _probe(s, answer) -> None:
    class Client:
        def session_alive(self, timeout):
            if isinstance(answer, BaseException):
                raise answer
            return answer

    s._session_checker = SessionChecker(lambda: Client(), s._on_session_view)
    s.checkSession()
    s._session_checker.join(5)
    s._drain()


def _check(state, code=None):
    error = api.IpatoolError(code) if code else None
    return api.SessionCheck(state, 0.1, error)


def test_alive(session) -> None:
    s, prompts, _ = session
    _probe(s, _check(api.SessionState.ALIVE))
    assert s.sessionState == "alive" and not s.sessionRelogin
    assert prompts == [] and s.signedIn


def test_expired_offers_sign_in_again(session) -> None:
    s, prompts, _ = session
    _probe(s, _check(api.SessionState.EXPIRED, api.ErrorCode.TOKEN_EXPIRED))
    assert s.sessionState == "expired" and s.sessionRelogin
    assert prompts == [EMAIL]
    assert s.sessionNote == api.MESSAGES_RU[api.ErrorCode.TOKEN_EXPIRED]


def test_no_network_keeps_the_account(session) -> None:
    s, prompts, _ = session
    _probe(s, _check(api.SessionState.NO_NETWORK, api.ErrorCode.TIMEOUT))
    assert s.sessionState == "offline" and "Нет связи" in s.sessionNote
    assert prompts == []
    assert s.signedIn and s.accountEmail == EMAIL


def test_sign_out_deletes_purchases_cache(session) -> None:
    s, _prompts, cache = session
    s._purchases.set_account(EMAIL)
    cache.save(cache.account_key(EMAIL), [CachedPurchase(1, "com.example.a", "A", "")])
    s._purchases.show_cached()
    assert len(s.purchases) == 1
    s.signOut()
    assert s.service.revoked == 1  # core sign_out (revoke + cache delete) via GuiService
    assert not cache.path.exists()
    assert s.purchases == [] and s.sessionState == "unknown"


def test_purchases_session_problem_routes_to_relogin(session, tmp_path: Path) -> None:
    s, prompts, _ = session

    class Client:
        def iter_purchases(self, cancel_event=None, *, prefer_all=True):
            raise api.IpatoolError(api.ErrorCode.AUTH_CODE_REQUIRED, "x")
            yield  # pragma: no cover

    s._purchases = PurchasesLoader(PurchasesCache(tmp_path), lambda: Client(), s._on_purchases_view)
    s._purchases.set_account(EMAIL)
    s.loadPurchases()
    s._purchases.join(5)
    s._drain()
    assert s.purchasesNote == api.MESSAGES_RU[api.ErrorCode.AUTH_CODE_REQUIRED]
    assert s.sessionState == "expired" and prompts == [EMAIL]
