"""Apple ID login from the window on macOS (POSIX pty instead of pywinpty)."""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")

pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="POSIX pty only")

FAKE_IPATOOL = """#!{python}
import sys, termios
args = sys.argv[1:]
if "info" in args:
    print('{{"success":false}}')
    sys.exit(1)
fd = 0
sys.stdout.write("enter password: ")
sys.stdout.flush()
old = termios.tcgetattr(fd)
new = termios.tcgetattr(fd)
new[3] &= ~termios.ECHO
termios.tcsetattr(fd, termios.TCSANOW, new)
try:
    password = sys.stdin.readline().strip()
finally:
    termios.tcsetattr(fd, termios.TCSANOW, old)
sys.stdout.write("\\nenter 2FA code: ")
sys.stdout.flush()
code = sys.stdin.readline().strip()
ok = password == "pw" and code == "123456"
print("ok" if ok else "bad")
sys.exit(0 if ok else 1)
"""


@pytest.fixture
def fake_ipatool(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    tool = tmp_path / "ipatool"
    tool.write_text(FAKE_IPATOOL.format(python=sys.executable), encoding="utf-8")
    tool.chmod(0o755)
    monkeypatch.setenv("PATH", f"{tmp_path}{os.pathsep}{os.environ.get('PATH', '')}")
    return tool


def test_posix_uses_builtin_pty() -> None:
    from apprestore_gui.auth_pty import PosixPtyProcess, _load_pty_process

    assert _load_pty_process() is PosixPtyProcess


@pytest.mark.parametrize(("password", "code", "ok"), [("pw", "123456", True), ("wrong-pass", "000000", False)])
def test_window_login_asks_for_code_on_posix(
    fake_ipatool: Path,
    monkeypatch: pytest.MonkeyPatch,
    password: str,
    code: str,
    ok: bool,
) -> None:
    from apprestore_gui import auth_pty

    monkeypatch.setattr(auth_pty, "_apple_login_host_reachable", lambda env, timeout=8: True)

    asked: list[str] = []
    output: list[str] = []
    inbox: "auth_pty.queue.Queue[tuple[str, str]]" = auth_pty.queue.Queue()

    def need(kind: str) -> None:
        asked.append(kind)
        inbox.put((kind, code))

    result = auth_pty._drive_windows_login(
        email="a@b.c",
        password=password,
        code="",
        passphrase="",
        on_output=output.append,
        on_status=None,
        on_need=need,
        inbox=inbox,
        cancel=None,
        timeout=30,
    )
    assert asked == ["code"]
    assert result.ok is ok
    transcript = "".join(output)
    assert password not in transcript
    assert "enter password" in transcript
