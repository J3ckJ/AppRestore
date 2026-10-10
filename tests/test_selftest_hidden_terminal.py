"""``--self-test`` runs the real ``ipatool auth info`` on the hidden terminal."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

pytest.importorskip("PySide6")

from apprestore_core.models import CommandResult
from apprestore_gui import auth_pty, selftest

_SIGNED_OUT = json.dumps(
    {
        "level": "error",
        "error": "failed to get account: The specified item could not be found in the keyring",
        "time": "2026-10-10T16:00:00+03:00",
    }
)


def _fake_run(code: int, text: str):
    calls: list[dict] = []

    def run(args, *, passphrase, timeout, env, **_kw):
        calls.append({"args": list(args), "passphrase": passphrase, "env": dict(env)})
        return CommandResult(tuple(args), code, text if code == 0 else "", text if code else "")

    return run, calls


@pytest.fixture
def fake_ipatool(monkeypatch):
    monkeypatch.setattr("apprestore_core.paths.resolve_tool", lambda name: "/opt/ipatool")


def test_signed_out_answer_passes_without_a_passphrase(fake_ipatool, monkeypatch) -> None:
    run, calls = _fake_run(1, _SIGNED_OUT)
    monkeypatch.setattr(auth_pty, "run_pty_command", run)
    detail = selftest._ipatool_auth_info_hidden_terminal()
    assert detail["session"] == "not signed in (expected)"
    assert detail["exit"] == 1
    assert "failed to get account" in detail["ipatool"]
    (call,) = calls
    assert call["args"][1:] == ["--non-interactive", "--format", "json", "auth", "info"]
    assert call["passphrase"] == ""
    # A throwaway home: a real saved session on the machine is never read.
    assert call["env"]["HOME"] == call["env"]["USERPROFILE"]
    assert not Path(call["env"]["HOME"]).exists()


def test_console_host_crash_fails_the_self_test(fake_ipatool, monkeypatch) -> None:
    for code in (0xC0000142, -1073741502):
        run, _ = _fake_run(code, "")
        monkeypatch.setattr(auth_pty, "run_pty_command", run)
        with pytest.raises(RuntimeError, match="0xc0000142"):
            selftest._ipatool_auth_info_hidden_terminal()


@pytest.mark.parametrize("code", [1, 2])
def test_silent_exit_fails_the_self_test(fake_ipatool, monkeypatch, code) -> None:
    run, _ = _fake_run(code, "")
    monkeypatch.setattr(auth_pty, "run_pty_command", run)
    with pytest.raises(RuntimeError, match="nothing readable"):
        selftest._ipatool_auth_info_hidden_terminal()


def test_spawn_failure_and_timeout_fail(fake_ipatool, monkeypatch) -> None:
    run, _ = _fake_run(127, "could not start ipatool: boom")
    monkeypatch.setattr(auth_pty, "run_pty_command", run)
    with pytest.raises(RuntimeError, match="could not start"):
        selftest._ipatool_auth_info_hidden_terminal()
    run, _ = _fake_run(124, "command timed out")
    monkeypatch.setattr(auth_pty, "run_pty_command", run)
    with pytest.raises(TimeoutError):
        selftest._ipatool_auth_info_hidden_terminal()


def test_signed_in_session_is_not_copied_into_the_report(fake_ipatool, monkeypatch) -> None:
    run, _ = _fake_run(0, json.dumps({"name": "Someone", "email": "someone@example.com", "success": True}))
    monkeypatch.setattr(auth_pty, "run_pty_command", run)
    detail = selftest._ipatool_auth_info_hidden_terminal()
    text = json.dumps(detail)
    assert "example.com" not in text and "Someone" not in text
    assert detail["session"].startswith("signed in")


def test_error_line_is_redacted() -> None:
    raw = (
        "failed to get account: open C:\\Users\\someone\\.ipatool\\account: "
        "not found for someone@example.com /Users/someone/.ipatool"
    )
    clean = selftest._redact(raw)
    assert "someone" not in clean
    assert "<path>" in clean and "<email>" in clean


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX pty path")
def test_real_posix_pty_reads_the_answer(tmp_path, monkeypatch) -> None:
    script = tmp_path / "ipatool"
    script.write_text(
        "#!/bin/sh\n"
        "test -t 0 || { echo 'stdin is not a tty' >&2; exit 9; }\n"
        f"echo '{_SIGNED_OUT}' >&2\n"
        "exit 1\n",
        encoding="utf-8",
    )
    os.chmod(script, 0o755)
    monkeypatch.setattr("apprestore_core.paths.resolve_tool", lambda name: str(script))
    detail = selftest._ipatool_auth_info_hidden_terminal()
    assert detail["terminal"] == "pty"
    assert detail["session"] == "not signed in (expected)"


def test_self_test_runs_the_check() -> None:
    source = Path(selftest.__file__).read_text(encoding="utf-8")
    assert '"ipatool auth info on the hidden terminal"' in source
