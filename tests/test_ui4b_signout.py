"""Смоук п.18: «Выйти» from the 4b window (QuickSession.signOut → AppRestoreService
sign_out). Every path — ok, «session already revoked», an error — deletes the
session copy and purchases-cache.json; the license journal stays byte for byte,
the icon cache is not touched."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

pytest.importorskip("PySide6")

from apprestore_gui import quick_session as qs  # noqa: E402

EMAIL = "person@example.com"


@pytest.fixture(scope="module")
def qapp():
    from PySide6.QtWidgets import QApplication

    return QApplication.instance() or QApplication([])


class Service:
    def __init__(self, fail: str) -> None:
        self.fail = fail
        self.core = SimpleNamespace(tools=SimpleNamespace(_ipatool_env=lambda: {}))

    def keychain_passphrase(self) -> str:
        return ""

    def revoke(self) -> None:
        if self.fail == "revoked":
            raise RuntimeError("no session: already revoked")
        if self.fail == "error":
            raise OSError("ipatool crashed")
        from apprestore_core.purchases_cache import forget_purchases_cache

        forget_purchases_cache()  # what core.sign_out does

    def clear_keychain_passphrase(self) -> None:
        pass


@pytest.mark.parametrize("fail", ["", "revoked", "error"])
def test_sign_out_deletes_session_and_purchases_cache_keeps_journal_and_icons(
    qapp, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fail: str
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "xdg-cache"))
    monkeypatch.delenv("APPRESTORE_LICENSE_JOURNAL", raising=False)
    from apprestore_core.license_guard import default_journal_path
    from apprestore_core.purchases_cache import CachedPurchase, PurchasesCache
    from apprestore_gui.account_vault import vault_path, vault_root
    from apprestore_gui.icons_cache import default_cache_dir

    cache = PurchasesCache()
    cache.save(cache.account_key(EMAIL), [CachedPurchase(1, "com.example.a", "A", "")])
    session_copy = vault_path(EMAIL, vault_root())
    session_copy.mkdir(parents=True)
    (session_copy / "keychain").write_bytes(b"session")
    journal = default_journal_path()
    journal.parent.mkdir(parents=True, exist_ok=True)
    journal.write_bytes(b'{"id":"1","time":"2026-10-10T17:00:00Z","track_id":"1","status":"acquired"}\n')
    before = journal.read_bytes()
    from apprestore_core.license_guard import read_counts

    counts = read_counts(journal_path=journal)
    icons = default_cache_dir()
    icons.mkdir(parents=True, exist_ok=True)
    (icons / "1.png").write_bytes(b"png")
    assert cache.path.exists()

    monkeypatch.setattr(qs, "GuiService", lambda demo_mode=False: Service(fail))
    monkeypatch.setattr(qs, "load_bindings", lambda: {})
    s = qs.QuickSession()
    s._signed, s._phase, s._account_email = True, "in", EMAIL
    s.signOut()

    assert not cache.path.exists()
    assert not session_copy.exists()
    assert journal.read_bytes() == before
    assert read_counts(journal_path=journal) == counts
    assert (icons / "1.png").read_bytes() == b"png"
    assert not s.signedIn and s.authPhase == "out"
