"""Every sign-out and account-switch path deletes purchases-cache.json.

The single core point is AppRestoreService.sign_out / note_account; the CLI,
the Widgets window, GuiService and QuickSession all go through it.
"""

from __future__ import annotations

import io
from contextlib import redirect_stdout
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from apprestore_core import cli
from apprestore_core.purchases_cache import CachedPurchase, PurchasesCache
from apprestore_core.service import AppRestoreService

EMAIL = "person@example.com"
OTHER = "other@example.com"


@pytest.fixture()
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    return tmp_path


def _seed(email: str = EMAIL) -> PurchasesCache:
    cache = PurchasesCache()
    cache.save(cache.account_key(email), [CachedPurchase(1, "com.example.a", "A", "")])
    assert cache.path.exists()
    return cache


class Tools:
    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
        self.revoked = 0
        self.logins: list[str] = []

    def ipatool_revoke(self) -> None:
        self.revoked += 1
        if self.fail:
            raise RuntimeError("ipatool revoke failed")

    def ipatool_login(self, email: str) -> None:
        self.logins.append(email)


def _service(home: Path, tools: Tools) -> AppRestoreService:
    return AppRestoreService(tools=tools, library=home / "lib", cache=home / "cache")  # type: ignore[arg-type]


def test_core_sign_out_deletes_cache(home: Path) -> None:
    cache = _seed()
    tools = Tools()
    _service(home, tools).sign_out()
    assert tools.revoked == 1 and not cache.path.exists()


def test_core_sign_out_deletes_cache_even_if_revoke_fails(home: Path) -> None:
    cache = _seed()
    with pytest.raises(RuntimeError):
        _service(home, Tools(fail=True)).sign_out()
    assert not cache.path.exists()


def test_cli_auth_revoke_deletes_cache(home: Path) -> None:
    cache = _seed()
    tools = Tools()
    with patch("apprestore_core.cli.AppRestoreService", side_effect=lambda **kw: _service(home, tools)), redirect_stdout(
        io.StringIO()
    ):
        assert cli.main(["--json", "auth", "--revoke"]) == 0
    assert tools.revoked == 1 and not cache.path.exists()


def test_login_with_another_account_deletes_cache(home: Path) -> None:
    cache = _seed(EMAIL)
    service = _service(home, Tools())
    service.authenticate(EMAIL)
    assert cache.path.exists()  # same account keeps its list
    service.authenticate(OTHER)
    assert not cache.path.exists()


pyside = pytest.importorskip  # alias for readability below


def test_gui_service_revoke_and_switch(home: Path) -> None:
    pyside("PySide6")
    from apprestore_gui.service_adapter import GuiService

    tools = Tools()
    gui = GuiService(demo_mode=True)
    gui.demo_mode = False
    gui._service = _service(home, tools)
    cache = _seed()
    gui.note_account(EMAIL)
    assert cache.path.exists()
    gui.note_account(OTHER)
    assert not cache.path.exists()
    cache = _seed()
    gui.revoke()
    assert tools.revoked == 1 and not cache.path.exists()


def test_widgets_window_revoke_deletes_cache(home: Path) -> None:
    pyside("PySide6")
    from apprestore_gui.main_window import MainWindow
    from apprestore_gui.service_adapter import GuiService

    tools = Tools(fail=True)  # even when ipatool fails, the cache goes
    gui = GuiService(demo_mode=True)
    gui.demo_mode = False
    gui._service = _service(home, tools)
    cache = _seed()
    statuses: list[str] = []
    fake = SimpleNamespace(
        _auth_job=None,
        service=gui,
        _set_auth_status=lambda text, color: statuses.append(text),
        log=lambda text: None,
    )
    MainWindow._revoke(fake)  # type: ignore[arg-type]
    assert tools.revoked == 1 and not cache.path.exists()


def test_widgets_window_login_as_other_account_deletes_cache(home: Path) -> None:
    pyside("PySide6")
    from apprestore_gui.main_window import MainWindow

    cache = _seed(EMAIL)
    seen: list[str] = []
    edit = SimpleNamespace(text=lambda: f" {OTHER} ")
    page = SimpleNamespace(findChild=lambda _cls, name: edit if name == "auth_email" else None)

    def note(email: str) -> None:
        seen.append(email)
        PurchasesCache().claim(email)

    fake = SimpleNamespace(pages={"account": page}, service=SimpleNamespace(note_account=note))
    fake._note_account_quietly = lambda value: MainWindow._note_account_quietly(fake, value)
    MainWindow._note_signed_in_account(fake)  # type: ignore[arg-type]
    assert seen == [OTHER] and not cache.path.exists()


def test_widgets_saved_keychain_without_email_uses_auth_info(home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    pyside("PySide6")
    from apprestore_gui import workers
    from apprestore_gui.main_window import MainWindow

    cache = _seed(EMAIL)
    seen: list[str] = []
    edit = SimpleNamespace(text=lambda: "")  # keychain opened, email field empty
    page = SimpleNamespace(findChild=lambda _cls, name: edit if name == "auth_email" else None)

    def note(email: str) -> None:
        seen.append(email)
        PurchasesCache().claim(email)

    def run_now(parent, fn, *args, on_finished=None, on_failed=None, **kwargs):
        try:
            value = fn(*args, **kwargs)
        except Exception as exc:  # noqa: BLE001
            on_failed(str(exc))
            return
        on_finished(value)

    monkeypatch.setattr(workers, "run_in_thread", run_now)
    tools = SimpleNamespace(ipatool_auth_info=lambda: {"email": OTHER, "success": True})
    fake = SimpleNamespace(
        pages={"account": page},
        service=SimpleNamespace(note_account=note, core=SimpleNamespace(tools=tools)),
    )
    fake._note_account_quietly = lambda value: MainWindow._note_account_quietly(fake, value)
    MainWindow._note_signed_in_account(fake)  # type: ignore[arg-type]
    assert seen == [OTHER] and not cache.path.exists()

    # auth info without an email (or failing) changes nothing.
    cache = _seed(EMAIL)
    seen.clear()
    fake.service.core.tools.ipatool_auth_info = lambda: None
    MainWindow._note_signed_in_account(fake)  # type: ignore[arg-type]
    assert seen == [] and cache.path.exists()
