"""Drive interactive ipatool auth from GUI fields via PTY / ConPTY."""

from __future__ import annotations

import os
import select
import shutil
import signal
import subprocess
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass


@dataclass
class AuthResult:
    ok: bool
    message: str


def _which_ipatool() -> str | None:
    # resolve_tool prefers the ipatool bundled next to the app, then PATH.
    try:
        from apprestore_core.paths import resolve_tool

        path = resolve_tool("ipatool")
        if path:
            return path
    except Exception:
        pass
    return shutil.which("ipatool")


def login_with_prompts(
    email: str,
    password: str,
    code: str = "",
    passphrase: str = "",
    *,
    timeout: float = 300.0,
    on_output: Callable[[str], None] | None = None,
) -> AuthResult:
    """
    Spawn `ipatool auth login --email …` and answer common prompts.

    On POSIX uses pty. On Windows tries pywinpty, else returns a clear error
    (Windows GUI build should vendor pywinpty).
    """
    email = email.strip()
    if not email or "@" not in email:
        return AuthResult(False, "Укажите корректный email Apple ID")
    ipatool = _which_ipatool()
    if not ipatool:
        return AuthResult(False, "ipatool не найден. Установите AppRestore или добавьте ipatool в PATH.")

    cmd = [ipatool, "auth", "login", "--email", email]
    if sys.platform == "win32":
        return _login_windows(cmd, password, code, passphrase, timeout, on_output)
    return _login_posix(cmd, password, code, passphrase, timeout, on_output)


def _feed_answers(buffer: str, answers: list[tuple[str, str, bool]]) -> tuple[str, list[str]]:
    """Match prompt fragments case-insensitively; return remaining buffer and writes."""
    writes: list[str] = []
    lower = buffer.lower()
    remaining = buffer
    for needle, value, used_flag_name in list(answers):
        # answers items: (needle, value, already_sent_box) - we mutate via sentinel in list
        pass
    return remaining, writes


def _login_posix(
    cmd: list[str],
    password: str,
    code: str,
    passphrase: str,
    timeout: float,
    on_output: Callable[[str], None] | None,
) -> AuthResult:
    import pty

    master, slave = pty.openpty()
    env = os.environ.copy()
    env.setdefault("TERM", "xterm-256color")
    proc = subprocess.Popen(
        cmd,
        stdin=slave,
        stdout=slave,
        stderr=slave,
        env=env,
        close_fds=True,
    )
    os.close(slave)

    sent_password = False
    sent_code = False
    sent_passphrase = False
    buf = ""
    deadline = time.monotonic() + timeout
    try:
        while proc.poll() is None and time.monotonic() < deadline:
            ready, _, _ = select.select([master], [], [], 0.4)
            if not ready:
                continue
            try:
                chunk = os.read(master, 4096)
            except OSError:
                break
            if not chunk:
                break
            text = chunk.decode("utf-8", errors="replace")
            buf += text
            if on_output:
                on_output(text)
            low = buf.lower()
            if (not sent_password) and password and any(
                k in low for k in ("password", "пароль", "enter password")
            ):
                os.write(master, (password + "\n").encode())
                sent_password = True
                buf = ""
                continue
            if (not sent_code) and code and any(
                k in low for k in ("auth code", "verification", "2fa", "код", "one-time")
            ):
                os.write(master, (code + "\n").encode())
                sent_code = True
                buf = ""
                continue
            if (not sent_passphrase) and passphrase and "passphrase" in low:
                os.write(master, (passphrase + "\n").encode())
                sent_passphrase = True
                buf = ""
                continue
        # wait a bit more
        try:
            proc.wait(timeout=max(1.0, deadline - time.monotonic()))
        except subprocess.TimeoutExpired:
            proc.send_signal(signal.SIGTERM)
            try:
                proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                proc.kill()
            return AuthResult(False, "Вход прерван по таймауту")
    finally:
        try:
            os.close(master)
        except OSError:
            pass

    if proc.returncode == 0:
        return AuthResult(True, "Вход выполнен")
    return AuthResult(False, f"Вход не удался (код {proc.returncode})")


def _load_pty_process():
    """pywinpty is installed as the ``winpty`` import package."""

    try:
        from winpty import PtyProcess  # type: ignore[import-not-found]
    except ImportError:
        return None
    return PtyProcess


def _login_windows(
    cmd: list[str],
    password: str,
    code: str,
    passphrase: str,
    timeout: float,
    on_output: Callable[[str], None] | None,
) -> AuthResult:
    import queue
    import threading

    PtyProcess = _load_pty_process()
    if PtyProcess is None:
        return AuthResult(
            False,
            "На Windows для входа из окна нужен пакет pywinpty. "
            "Пока выполните: apprestore auth --email … в терминале.",
        )
    proc = PtyProcess.spawn(cmd)
    chunks: "queue.Queue[str]" = queue.Queue()

    def reader() -> None:
        # PtyProcess.read() blocks and has no timeout argument; read in a
        # thread and poll the queue so the deadline below is honoured.
        while True:
            try:
                data = proc.read(1024)
            except Exception:  # noqa: BLE001 - EOF / closed pty
                break
            if not data:
                if not proc.isalive():
                    break
                continue
            chunks.put(data)

    threading.Thread(target=reader, name="ipatool-conpty-reader", daemon=True).start()

    sent_password = False
    sent_code = False
    sent_passphrase = False
    buf = ""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            text = chunks.get(timeout=0.4)
        except queue.Empty:
            if not proc.isalive():
                break
            continue
        if on_output:
            on_output(text)
        buf += text
        low = buf.lower()
        if (not sent_password) and password and "password" in low:
            proc.write(password + "\r\n")
            sent_password = True
            buf = ""
        elif (not sent_code) and code and any(
            k in low for k in ("auth code", "verification", "2fa")
        ):
            proc.write(code + "\r\n")
            sent_code = True
            buf = ""
        elif (not sent_passphrase) and passphrase and "passphrase" in low:
            proc.write(passphrase + "\r\n")
            sent_passphrase = True
            buf = ""
    timed_out = proc.isalive()
    exit_status = getattr(proc, "exitstatus", None)
    try:
        proc.terminate(force=True)
    except Exception:  # noqa: BLE001
        pass
    if timed_out:
        return AuthResult(False, "Вход не завершился вовремя. Попробуйте ещё раз.")
    if exit_status == 0:
        return AuthResult(True, "Вход выполнен")
    if exit_status is None and sent_password:
        return AuthResult(True, "Сеанс входа завершён. Проверьте статус в проверках.")
    return AuthResult(False, f"Вход не удался (код {exit_status})")
