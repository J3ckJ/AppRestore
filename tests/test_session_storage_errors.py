"""Storage failures never make the GUI claim that a different account is open."""

from types import SimpleNamespace

import pytest

pytest.importorskip("PySide6")

from apprestore_gui import quick_session as qs
from apprestore_gui.purchases import PurchasesCache

EMAIL = "a@example.com"


@pytest.fixture
def session(qapp, tmp_path, monkeypatch):
    service = SimpleNamespace(
        core=SimpleNamespace(tools=SimpleNamespace()),
        secret="keychain password",
    )
    service.keychain_passphrase = lambda: service.secret
    service.keychain_ready = lambda: bool(service.secret)
    service.clear_keychain_passphrase = lambda: setattr(service, "secret", "")
    service.remember_keychain_passphrase = lambda value, **kw: setattr(service, "secret", value)
    monkeypatch.setattr(qs, "GuiService", lambda **kw: service)
    monkeypatch.setattr(qs, "load_bindings", lambda: {})
    monkeypatch.setattr(qs, "PurchasesCache", lambda: PurchasesCache(tmp_path))
    monkeypatch.setattr(qs, "keychain_has_saved_account", lambda: True)
    s = qs.QuickSession()
    s._signed, s._phase, s._account_email = True, "in", EMAIL
    s._follow_purchases_account = lambda email: None
    return s


def fail(*args, **kwargs):
    raise OSError("disk write failed")


@pytest.mark.parametrize("operation", ["login", "switch"])
def test_failed_change_keeps_current_account_and_releases_tool_lock(session, monkeypatch, operation):
    s = session
    started = []
    s._start_job = started.append
    monkeypatch.setattr(qs, "save_session", fail)
    if operation == "login":
        s.login("b@example.com", "password")
    else:
        s._begin_switch("b@example.com")
    assert started == []
    assert s.signedIn and s.accountEmail == EMAIL and s.authPhase == "in"
    assert "Не удалось сохранить" in s.authStatus
    assert s._tool_lock.acquire(blocking=False)
    s._tool_lock.release()


def test_failed_restore_keeps_current_account(session, monkeypatch):
    monkeypatch.setattr(qs, "save_session", lambda email: True)
    monkeypatch.setattr(qs, "restore_session", fail)
    session._begin_switch("b@example.com")
    assert session.signedIn and session.accountEmail == EMAIL
    assert "Не удалось сохранить" in session.authStatus


@pytest.mark.parametrize("missing", [False, True])
def test_failed_return_to_previous_account_requires_unlock(session, monkeypatch, missing):
    s = session
    s._return_account = EMAIL
    s._pending_email = "b@example.com"
    s._phase = "running"
    s._passphrases[EMAIL] = "old secret"
    monkeypatch.setattr(qs, "restore_session", (lambda email: False) if missing else fail)
    s._on_auth_done(qs.AuthResult(False, "login failed"))
    assert not s.signedIn and s.accountEmail == ""
    assert s.authPhase == "locked" and not s.service.keychain_ready()
    assert "Откройте сессию заново" in s.authStatus


def test_optional_save_failure_still_updates_purchase_list(session, monkeypatch):
    s = session
    s.service.core.tools.ipatool_auth_info = lambda: {"email": EMAIL}
    started = []
    s._purchases = SimpleNamespace(start=lambda: started.append("purchases"))
    s._session_checker = SimpleNamespace(start=lambda: started.append("session"))
    monkeypatch.setattr(qs, "save_session", fail)
    s._refresh_auth()
    assert s.signedIn and s.accountEmail == EMAIL
    assert started == ["purchases", "session"]
    assert "Не удалось сохранить" in s.authStatus


@pytest.mark.parametrize("secret", [" secret ", "\tsecret\t", "   "])
@pytest.mark.parametrize("existing_job", [False, True])
def test_unlock_passes_exact_password_to_service_and_process(session, monkeypatch, secret, existing_job):
    s = session
    submitted = []
    if existing_job:
        s._auth_job = SimpleNamespace(submit=lambda kind, value: submitted.append((kind, value)))
    else:
        monkeypatch.setattr(qs, "AppleLogin", lambda *args: args)
        s._start_job = lambda args: submitted.append(("passphrase", args[-1]))
    s.unlock(secret)
    assert s.service.secret == secret
    assert submitted == [("passphrase", secret)]
