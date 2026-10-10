"""Keychain passphrase: never in a child's argv, never in its environment.

Every way the product starts ipatool is checked with the secret variables
set in os.environ: command.Runner (CLI/tools.py), the hidden terminal
(run_pty_command / KeychainRunner, GUI), and gui_runner for ipatool_api
(--keychain-passphrase-stdin on a patched ipatool, hidden terminal on an old
one, plain run without a secret).
"""

from __future__ import annotations

import json
import os
import re
import stat
import sys
import threading
from pathlib import Path

import pytest

import apprestore_core.ipatool_api as api
from apprestore_core.command import SECRET_ENV_NAMES, Runner, child_env
from apprestore_gui import purchases as pm

SECRET = "pw-Secret-123"
ROOT = Path(__file__).resolve().parents[1]

FAKE = r'''#!{python}
import json, os, sys
argv = sys.argv[1:]
PATCHED = {patched}
if "--help" in argv:
    print("Flags:\n  --keychain-passphrase string")
    if PATCHED:
        print("  --keychain-passphrase-stdin   read the keychain passphrase from the first line of stdin")
    sys.exit(0)
if "--keychain-passphrase-stdin" in argv:
    if not PATCHED:
        print(json.dumps({{"level": "error", "error": "unknown flag: --keychain-passphrase-stdin", "success": False}}))
        sys.exit(1)
    got = sys.stdin.readline().rstrip("\n")
    how = "stdin"
elif "--non-interactive" not in argv:
    sys.stdout.write("enter passphrase to unlock keychain: ")
    sys.stdout.flush()
    got = sys.stdin.readline().strip()
    how = "pty"
else:
    got = ""
    how = "none"
secret_env = sorted(k for k in os.environ if k.upper() in {names!r})
print(json.dumps({{"level": "info", "success": True, "argv": argv, "how": how,
                  "got_secret": got == {secret!r}, "secret_env": secret_env}}))
'''


def _fake(tmp_path: Path, *, patched: bool) -> str:
    folder = tmp_path / ("patched" if patched else "old")
    folder.mkdir()
    script = folder / "ipatool"
    script.write_text(
        FAKE.format(python=sys.executable, patched=patched, names=sorted(SECRET_ENV_NAMES), secret=SECRET),
        encoding="utf-8",
    )
    script.chmod(script.stat().st_mode | stat.S_IXUSR)
    return str(script)


@pytest.fixture(autouse=True)
def secret_env(monkeypatch: pytest.MonkeyPatch):
    for name in SECRET_ENV_NAMES:
        monkeypatch.setenv(name, SECRET)
    pm._STDIN_SUPPORT.clear()
    yield
    pm._STDIN_SUPPORT.clear()


def _payload(text: str) -> dict:
    rows = [json.loads(line) for line in text.splitlines() if line.strip().startswith("{")]
    return rows[-1]


def _assert_clean(payload: dict, argv_seen: list[str] | None = None) -> None:
    assert payload["secret_env"] == []
    assert SECRET not in json.dumps(payload["argv"])
    assert "--keychain-passphrase" not in payload["argv"]
    if argv_seen is not None:
        assert SECRET not in " ".join(argv_seen)


def test_child_env_drops_secrets_even_from_extra() -> None:
    env = child_env({"IPATOOL_KEYCHAIN_PASSPHRASE": "x", "HTTPS_PROXY": "http://p:1"})
    assert not SECRET_ENV_NAMES & set(env)
    assert env["HTTPS_PROXY"] == "http://p:1"
    assert SECRET not in json.dumps(env)


posix_only = pytest.mark.skipif(sys.platform == "win32", reason="fake ipatool is a POSIX script")


@posix_only
def test_runner_cli_and_tools_path(tmp_path: Path) -> None:
    script = _fake(tmp_path, patched=True)
    result = Runner().run([script, "auth", "info", "--format", "json", "--non-interactive"], capture=True, timeout=20)
    _assert_clean(_payload(result.stdout))


@posix_only
def test_hidden_terminal_path_keychain_runner(tmp_path: Path) -> None:
    pytest.importorskip("PySide6")
    from apprestore_gui.auth_pty import KeychainRunner

    script = _fake(tmp_path, patched=False)
    result = KeychainRunner(lambda: SECRET).run([script, "--format", "json", "auth", "info"], timeout=20)
    payload = _payload(result.stdout or result.stderr)
    assert payload["how"] == "pty" and payload["got_secret"]
    _assert_clean(payload)


@posix_only
def test_gui_runner_patched_ipatool_uses_stdin(tmp_path: Path) -> None:
    script = _fake(tmp_path, patched=True)
    runner = pm.gui_runner(lambda: SECRET, lambda: {"HTTPS_PROXY": "http://p:1"})
    client = api.IpatoolClient(script, keychain_passphrase="", runner=runner)
    data = client._call(["auth", "info"], 20)
    assert data["how"] == "stdin" and data["got_secret"]
    assert "--keychain-passphrase-stdin" in data["argv"] and "--non-interactive" in data["argv"]
    _assert_clean(data)


@posix_only
def test_gui_runner_old_ipatool_goes_to_hidden_terminal(tmp_path: Path) -> None:
    pytest.importorskip("PySide6")
    script = _fake(tmp_path, patched=False)
    runner = pm.gui_runner(lambda: SECRET, lambda: {})
    client = api.IpatoolClient(script, keychain_passphrase="", runner=runner)
    data = client._call(["auth", "info"], 20)
    assert data["how"] == "pty" and data["got_secret"]
    assert "--keychain-passphrase-stdin" not in data["argv"]
    _assert_clean(data)


@posix_only
def test_gui_runner_strips_a_passphrase_flag_and_env_from_the_client(tmp_path: Path) -> None:
    script = _fake(tmp_path, patched=True)
    runner = pm.gui_runner(lambda: "", lambda: {})
    # A client that (wrongly) puts the secret in argv and in env.
    result = runner(
        [script, "auth", "info", "--format", "json", "--non-interactive", "--keychain-passphrase", SECRET],
        20,
        {**os.environ, "IPATOOL_KEYCHAIN_PASSPHRASE": SECRET},
    )
    data = _payload(result.stdout)
    assert data["how"] == "stdin" and data["got_secret"]  # moved to stdin
    _assert_clean(data)


@posix_only
def test_gui_runner_without_secret_plain_run(tmp_path: Path) -> None:
    script = _fake(tmp_path, patched=True)
    result = pm.gui_runner(lambda: "", lambda: {})([script, "auth", "info", "--format", "json", "--non-interactive"], 20)
    data = _payload(result.stdout)
    assert data["how"] == "none"
    _assert_clean(data)


@posix_only
def test_stdin_support_is_detected_from_help(tmp_path: Path) -> None:
    assert pm.supports_passphrase_stdin(_fake(tmp_path, patched=True))
    assert not pm.supports_passphrase_stdin(_fake(tmp_path, patched=False))


def test_no_direct_environ_copies_in_launch_code() -> None:
    """All child envs go through command.child_env (one place)."""

    for rel in ("apprestore_core/command.py", "apprestore_gui/auth_pty.py", "apprestore_gui/purchases.py",
                "apprestore_core/tools.py", "apprestore_gui/selftest.py"):
        text = (ROOT / rel).read_text(encoding="utf-8")
        copies = re.findall(r"os\.environ\.copy\(\)|dict\(os\.environ\)", text)
        allowed = 1 if rel == "apprestore_core/command.py" else 0  # inside child_env
        assert len(copies) == allowed, rel


def test_no_secure_method_falls_back_to_hidden_terminal_client(tmp_path: Path) -> None:
    class Code:
        value = pm.NO_SECURE_METHOD

    class NoSecure(Exception):
        code = Code()

    class Primary:
        def session_alive(self, timeout):
            raise NoSecure()

        def iter_purchases(self, cancel_event=None, **_):
            raise NoSecure()
            yield  # pragma: no cover

    class Fallback:
        used = 0

        def session_alive(self, timeout):
            Fallback.used += 1
            return api.SessionCheck(api.SessionState.ALIVE, 0.1)

        def iter_purchases(self, cancel_event=None, **_):
            Fallback.used += 1
            yield api.PurchasesPage(1, 0, (), 0, 1, True)

    views: list = []
    checker = pm.SessionChecker(lambda: Primary(), views.append, fallback_factory=lambda: Fallback())
    checker.start()
    checker.join(5)
    assert views[-1].state == "alive"

    from apprestore_core.purchases_cache import PurchasesCache

    pviews: list = []
    loader = pm.PurchasesLoader(PurchasesCache(tmp_path), lambda: Primary(), pviews.append, fallback_factory=lambda: Fallback())
    loader.set_account("person@example.com")
    loader.start()
    loader.join(5)
    assert pviews[-1].note == "" and not pviews[-1].busy
    assert Fallback.used == 2
