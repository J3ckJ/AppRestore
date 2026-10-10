"""4b paths: the Apple ID password, the 2FA code and the keychain passphrase never
reach a child's argv or environment, also on an old (unpatched) ipatool.

* sign-in from the 4b sheet: QuickSession.login → AppleLogin → auth_pty
  (``ipatool auth login --email`` on a terminal; answers are typed);
* every ipatool_api call from the GUI (purchases, account_info): QuickSession's
  client has ``keychain_passphrase=""`` and ``gui_runner`` (stdin on a patched
  binary, hidden terminal on an old one — PASSPHRASE_NO_SECURE_METHOD).
"""

from __future__ import annotations

import inspect
import json
import sys

import pytest

from apprestore_core.command import SECRET_ENV_NAMES

PASSWORD = "Apple-Pass-31415"
PASSPHRASE = "keychain-secret-27182"


class _Spawned(Exception):
    pass


def _check(argv: list[str], env: dict[str, str]) -> None:
    blob = json.dumps([argv, env])
    assert PASSWORD not in blob and PASSPHRASE not in blob
    assert not SECRET_ENV_NAMES & {k.upper() for k in env}
    assert not any(a.startswith("--keychain-passphrase") and a != "--keychain-passphrase-stdin" for a in argv)
    assert "--password" not in argv


@pytest.mark.parametrize("which", ["login", "unlock"])
def test_4b_signin_never_puts_secrets_into_argv_or_env(monkeypatch, which) -> None:
    from apprestore_gui import auth_pty

    for name in SECRET_ENV_NAMES:
        monkeypatch.setenv(name, PASSPHRASE)  # even a stray one in the parent
    seen: list[tuple[list[str], dict[str, str]]] = []

    class Pty:
        @staticmethod
        def spawn(argv, env=None, **_kw):
            seen.append((list(argv), dict(env or {})))
            raise _Spawned()

    monkeypatch.setattr(auth_pty, "_which_ipatool", lambda: "/opt/ipatool")
    monkeypatch.setattr(auth_pty, "_load_pty_process", lambda: Pty)
    monkeypatch.setattr(auth_pty, "_apple_login_host_reachable", lambda env: True)
    kw = dict(on_output=None, on_status=None, on_need=None, inbox=None, cancel=None)
    if which == "login":
        result = auth_pty._drive_windows_login(email="m@example.com", password=PASSWORD, code="482913",
                                               passphrase=PASSPHRASE, **kw)
    else:
        result = auth_pty._drive_windows_unlock(passphrase=PASSPHRASE, **kw)
    assert not result.ok
    assert seen, "ipatool was not started"
    for argv, env in seen:
        _check(argv, env)
        assert "482913" not in json.dumps(argv)


def test_4b_ipatool_api_client_has_no_passphrase_and_uses_gui_runner(monkeypatch) -> None:
    from apprestore_gui import quick_session

    src = inspect.getsource(quick_session.QuickSession._ipatool_client)
    assert 'keychain_passphrase=""' in src and "gui_runner(" in src
    # 4b reads purchases/account through QuickSession only (no own IpatoolClient)
    from pathlib import Path

    root = Path(quick_session.__file__).parent
    for path in [*(root / "ui4b").rglob("*.py"), *(root / "qml4b").rglob("*.qml")]:
        text = path.read_text(encoding="utf-8")
        assert "IpatoolClient(" not in text and "keychain_passphrase" not in text, path.name


@pytest.mark.skipif(sys.platform == "win32", reason="fake ipatool is a POSIX script")
def test_old_ipatool_passphrase_goes_to_hidden_terminal_not_argv(tmp_path, monkeypatch) -> None:
    """PASSPHRASE_NO_SECURE_METHOD on an old binary → gui_runner falls back to the
    hidden terminal; the secret never shows up in argv/env of the child."""

    pytest.importorskip("PySide6")
    import stat

    from apprestore_core import ipatool_api as api
    from apprestore_core import ipatool_caps
    from apprestore_gui import purchases as pm

    ipatool_caps.reset_cache()
    record = tmp_path / "seen.jsonl"
    script = tmp_path / "ipatool"
    script.write_text(
        f"""#!{sys.executable}
import json, os, sys
open({str(record)!r}, "a").write(json.dumps({{"argv": sys.argv, "env": dict(os.environ)}}) + "\\n")
if "--help" in sys.argv:
    print("Usage: ipatool auth info [flags]"); sys.exit(0)
if "--keychain-passphrase-stdin" in sys.argv:
    print("unknown flag: --keychain-passphrase-stdin", file=sys.stderr); sys.exit(1)
if sys.stdin.isatty():
    sys.stdout.write("enter keychain passphrase: "); sys.stdout.flush()
    sys.stdin.readline()
print(json.dumps({{"level": "info", "success": True, "email": "m@example.com"}}))
""",
        encoding="utf-8",
    )
    script.chmod(script.stat().st_mode | stat.S_IXUSR)
    runner = pm.gui_runner(lambda: PASSPHRASE, lambda: {})
    client = api.IpatoolClient(str(script), keychain_passphrase="", runner=runner)
    try:
        client._call(["auth", "info"], 20)
    except api.IpatoolError:
        pass  # an honest error is fine; a leak is not
    rows = [json.loads(line) for line in record.read_text(encoding="utf-8").splitlines()]
    assert rows
    for row in rows:
        _check(row["argv"], row["env"])
    ipatool_caps.reset_cache()
