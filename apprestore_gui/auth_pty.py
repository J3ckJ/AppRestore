"""Drive interactive ipatool auth from GUI fields via PTY / ConPTY."""

from __future__ import annotations

import json
import os
import queue
import re
import select
import shutil
import signal
import socket
import subprocess
import sys
import threading
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from PySide6.QtCore import QThread, Signal

from apprestore_core.command import CommandError, Runner, child_env, windows_creationflags
from apprestore_core.models import CommandResult
from apprestore_core.tools import AppRestoreTools


@dataclass
class AuthResult:
    ok: bool
    message: str
    session_open: bool = True


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
    timeout: float = 1800.0,
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

    # One driver on every platform (pywinpty on Windows, a POSIX pty elsewhere):
    # Apple ID and password through the terminal prompts, output scrubbed.
    return _drive_windows_login(
        email=email,
        password=password,
        code=code,
        passphrase=passphrase,
        on_output=on_output,
        on_status=None,
        on_need=None,
        inbox=None,
        cancel=None,
        timeout=timeout,
    )


def _feed_answers(buffer: str, answers: list[tuple[str, str, bool]]) -> tuple[str, list[str]]:
    """Match prompt fragments case-insensitively; return remaining buffer and writes."""
    writes: list[str] = []
    lower = buffer.lower()
    remaining = buffer
    for needle, value, used_flag_name in list(answers):
        # answers items: (needle, value, already_sent_box) - we mutate via sentinel in list
        pass
    return remaining, writes


class PosixPtyProcess:
    """The part of pywinpty's ``PtyProcess`` the login code uses, on a POSIX pty.

    macOS has no pywinpty. ipatool reads the password and the code from its
    stdin, so a pseudo-terminal on stdin/stdout/stderr is all it needs.
    """

    def __init__(self, proc: subprocess.Popen[bytes], master: int) -> None:
        import codecs

        self._proc = proc
        self._master = master
        self._decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")

    @classmethod
    def spawn(
        cls,
        argv: Sequence[str],
        env: Mapping[str, str] | None = None,
        dimensions: tuple[int, int] = (24, 80),
    ) -> PosixPtyProcess:
        import fcntl
        import pty
        import struct
        import termios

        master, slave = pty.openpty()
        try:
            rows, cols = dimensions
            fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack("HHHH", rows, cols, 0, 0))
        except OSError:
            pass
        try:
            proc = subprocess.Popen(
                list(argv),
                stdin=slave,
                stdout=slave,
                stderr=slave,
                env=dict(env) if env is not None else None,
                close_fds=True,
                start_new_session=True,
            )
        except BaseException:
            os.close(master)
            os.close(slave)
            raise
        os.close(slave)
        return cls(proc, master)

    def read(self, size: int = 1024) -> str:
        """Block until output arrives. Raise EOFError once the child is gone."""

        try:
            data = os.read(self._master, size)
        except OSError as exc:  # EIO on Linux once the child closed the pty
            raise EOFError("pty closed") from exc
        if not data:  # macOS reports EOF as an empty read
            raise EOFError("pty closed")
        return self._decoder.decode(data)

    def write(self, text: str) -> int:
        return os.write(self._master, text.encode("utf-8"))

    def isalive(self) -> bool:
        return self._proc.poll() is None

    @property
    def exitstatus(self) -> int | None:
        return self._proc.poll()

    def terminate(self, force: bool = False) -> None:
        if self._proc.poll() is None:
            try:
                self._proc.kill() if force else self._proc.terminate()
            except OSError:
                pass
            try:
                self._proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                self._proc.kill()

    def __del__(self) -> None:
        try:
            os.close(self._master)
        except (AttributeError, OSError):
            pass


def _load_pty_process():
    """Windows: pywinpty (the ``winpty`` import package). Elsewhere: a POSIX pty."""

    if sys.platform != "win32":
        return PosixPtyProcess
    try:
        from winpty import PtyProcess  # type: ignore[import-not-found]
    except ImportError:
        return None
    return PtyProcess


_ANSI = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]|\x1b\][^\x07]*\x07")
_PASSWORD_HINTS = ("password", "passwd", "пароль")
_CODE_HINTS = (
    "2fa",
    "two-factor",
    "verification code",
    "auth code",
    "security code",
    "one-time",
    "one time",
    "код подтвержд",
    "код из",
)
_PASSPHRASE_HINTS = ("passphrase", "keychain", "связк")


def _visible_output(text: str, secrets: tuple[str, ...]) -> str:
    clean = _ANSI.sub("", text).replace("\r", "")
    for secret in secrets:
        if secret:
            clean = clean.replace(secret, "•" * min(len(secret), 8))
    return clean


def _apple_login_host_reachable(env: dict[str, str], timeout: float = 8) -> bool:
    """Прямой TCP до узла, с которого ipatool начинает вход.

    Если в окружении уже есть HTTP(S)_PROXY, проверку пропускаем: сырой сокет
    пошёл бы мимо прокси и дал ложный отказ.
    """
    if any(env.get(name) for name in ("HTTPS_PROXY", "https_proxy", "HTTP_PROXY", "http_proxy")):
        return True
    try:
        with socket.create_connection(("init.itunes.apple.com", 443), timeout=timeout):
            return True
    except OSError:
        return False


#: Sign-in failed after a 2FA code went in: ipatool does not let you retry the
#: code, the login is over (Ника, часть 4, auth-code-wrong).
WRONG_CODE_TEXT = (
    "Код не подошёл или устарел, и Apple завершила вход. "
    "Введите пароль ещё раз и возьмите самый свежий код."
)
#: Ника, 04-auth §3: there is no «send again»; a fresh code comes from the iPhone settings.
CODE_HINT = "Код не пришёл? На iPhone: Настройки → ваше имя → Вход и безопасность → Получить код проверки"
_WRONG_CODE_HINTS = (
    "invalid verification code",
    "incorrect verification code",
    "verification code is incorrect",
    "invalid code",
    "incorrect code",
    "wrong code",
    "code is incorrect",
    "code was incorrect",
)


def is_two_factor_rejected(transcript: str) -> bool:
    """Макс: «apple did not complete verification» / 5005 = ErrorCode.TWO_FACTOR_REJECTED
    (the code was not accepted — e.g. a code from an earlier attempt), not an expired session."""

    try:
        from apprestore_core.ipatool_api import ErrorCode, classify_error
    except Exception:  # noqa: BLE001
        return "did not complete verification" in (transcript or "").casefold()
    return classify_error(transcript or "") is ErrorCode.TWO_FACTOR_REJECTED


def is_wrong_code(transcript: str, *, code_sent: bool = False) -> bool:
    """True when the failure is the 2FA code (TWO_FACTOR_REJECTED, explicit text, or
    any non-network, non-password failure right after a code was sent)."""

    text = (transcript or "").casefold()
    if is_two_factor_rejected(transcript) or any(hint in text for hint in _WRONG_CODE_HINTS):
        return True
    if not code_sent:
        return False
    if any(h in text for h in ("invalid password", "incorrect password", "wrong password", "bad credentials")):
        return False
    network = any(h in text for h in ("i/o timeout", "tls handshake timeout", "no such host", "connection refused"))
    return not network


def _explain_login_failure(exit_status: object, transcript: str, *, code_sent: bool = False) -> str:
    text = transcript.casefold()
    if is_two_factor_rejected(transcript):
        return WRONG_CODE_TEXT  # before the network check: 5005 is Apple's answer, not a lost one
    network = (
        "failed to get bag" in text
        or "init.itunes.apple.com" in text
        or "tls handshake timeout" in text
        or ("dial tcp" in text and any(hint in text for hint in ("443", "timeout", "connectex")))
    )
    if network:
        return (
            "Сервер Apple не ответил. "
            "Проверьте подключение к интернету и попробуйте ещё раз."
        )
    if is_wrong_code(transcript, code_sent=code_sent):
        return WRONG_CODE_TEXT
    if any(hint in text for hint in ("invalid password", "incorrect password", "wrong password", "bad credentials")):
        return "Apple не приняла пароль. Проверьте его и нажмите «Войти» ещё раз."
    if "http 204" in text or "empty or non-plist" in text:
        return (
            "Пароль дошёл до ipatool, но адрес входа App Store "
            "(buy.itunes.apple.com) ответил пустым HTTP 204. "
            "Сайт icloud.com — другой вход, он может открываться, "
            "когда магазин приложений вход не принимает."
        )
    if "http 301" in text:
        return "Apple не приняла вход. Попробуйте ещё раз позже."
    return f"Вход не удался (код {exit_status}). Текст ipatool показан выше."


def extract_json_document(text: str) -> str | None:
    """Return the first JSON value in a ConPTY transcript, if one parses."""

    decoder = json.JSONDecoder()
    for index, char in enumerate(text):
        if char not in "{[":
            continue
        try:
            _value, end = decoder.raw_decode(text[index:])
        except json.JSONDecodeError:
            continue
        return text[index : index + end]
    return None


def _command_wants_json(args: Sequence[str]) -> bool:
    folded = [str(arg).lower() for arg in args]
    return "--format" in folded and "json" in folded


def _is_ipatool_command(args: Sequence[str]) -> bool:
    if not args:
        return False
    name = str(args[0]).replace("\\", "/").rsplit("/", 1)[-1].lower()
    return name in {"ipatool", "ipatool.exe"}


def keychain_has_saved_account() -> bool:
    """True when ipatool still has an encrypted account entry on disk.

    ``auth revoke`` deletes that file. Until a new login writes it, the file
    keyring returns "item not found" and never asks for the passphrase.
    """

    return (Path.home() / ".ipatool" / "account").is_file()


def classify_keychain_unlock(exit_status: object, transcript: str) -> AuthResult:
    """Turn an ``auth info`` result into a passphrase outcome."""

    text = transcript.casefold()
    if exit_status == 0:
        return AuthResult(True, "Сессия открыта. Тот же вход, что и в терминале.")
    if "could not be found" in text:
        return AuthResult(
            True,
            "Пароль связки запомнен. Сохранённого входа нет: он удалён при выходе. "
            "Введите почту и пароль Apple ID и нажмите «Войти».",
            session_open=False,
        )
    if "handle is invalid" in text:
        return AuthResult(
            False,
            "Не удалось передать пароль связки без консоли. Нажмите «Открыть» ещё раз.",
            session_open=False,
        )
    if any(hint in text for hint in ("invalid", "incorrect", "decrypt")):
        return AuthResult(False, "ipatool не принял пароль связки ключей.", session_open=False)
    return AuthResult(
        False,
        "Не удалось открыть связку. Проверьте пароль и попробуйте ещё раз.",
        session_open=False,
    )


def unlock_keychain(passphrase: str) -> AuthResult:
    """Check the keychain passphrase the way the terminal does, without a console."""

    if not keychain_has_saved_account():
        if not passphrase:
            return AuthResult(False, "Введите пароль связки ключей.", session_open=False)
        return AuthResult(
            True,
            "Пароль связки запомнен. Сохранённого входа нет: он удалён при выходе. "
            "Введите почту и пароль Apple ID и нажмите «Войти».",
            session_open=False,
        )
    return _drive_windows_unlock(
        passphrase=passphrase,
        on_output=None,
        on_status=None,
        on_need=None,
        inbox=None,
        cancel=None,
    )


def run_stdin_command(
    args: Sequence[str],
    *,
    passphrase: str,
    timeout: float | None,
    env: Mapping[str, str] | None,
    on_output: Callable[[str], None] | None = None,
    stop_when: Callable[[str], bool] | None = None,
) -> CommandResult:
    """Run a patched ipatool with ``--keychain-passphrase-stdin``.

    The passphrase is written as the first stdin line and stdin is closed, so
    a later prompt reads EOF instead of hanging. Output streams to
    ``on_output`` (passphrase masked) like the hidden terminal does, and
    ``stop_when`` / ``timeout`` end the process tree.
    """

    from apprestore_core.command import _terminate_process_tree

    command = tuple(str(arg) for arg in args)
    popen_kwargs: dict[str, object] = {}
    if sys.platform == "win32":
        popen_kwargs["creationflags"] = windows_creationflags()
    else:
        popen_kwargs["start_new_session"] = True
    try:
        proc = subprocess.Popen(  # noqa: S603 - fixed argv
            list(command),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=child_env(env),
            **popen_kwargs,  # type: ignore[arg-type]
        )
    except OSError as exc:
        return CommandResult(command, 127, "", f"could not start ipatool: {exc}")
    try:
        assert proc.stdin is not None
        proc.stdin.write((passphrase + "\n").encode("utf-8"))
        proc.stdin.close()
    except OSError:
        pass

    chunks: queue.Queue[tuple[str, str]] = queue.Queue()

    def reader(stream, name: str) -> None:
        while True:
            data = stream.read1(4096) if hasattr(stream, "read1") else stream.read(4096)
            if not data:
                break
            chunks.put((name, data.decode("utf-8", errors="replace")))
        chunks.put((name, ""))

    threads = [
        threading.Thread(target=reader, args=(proc.stdout, "out"), daemon=True),
        threading.Thread(target=reader, args=(proc.stderr, "err"), daemon=True),
    ]
    for thread in threads:
        thread.start()

    parts = {"out": [], "err": []}
    open_streams = 2
    deadline = time.monotonic() + (1800.0 if timeout is None else max(timeout, 0.1))
    stopped = False
    while open_streams:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            _terminate_process_tree(proc)  # type: ignore[arg-type]
            return CommandResult(command, 124, "", "command timed out")
        try:
            name, text = chunks.get(timeout=min(0.4, remaining))
        except queue.Empty:
            continue
        if not text:
            open_streams -= 1
            continue
        text = _visible_output(text, (passphrase,))
        parts[name].append(text)
        if on_output is not None and text:
            on_output(text)
        if stop_when is not None and stop_when("".join(parts["out"] + parts["err"])):
            _terminate_process_tree(proc)  # type: ignore[arg-type]
            stopped = True
            break
    try:
        code = proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        _terminate_process_tree(proc)  # type: ignore[arg-type]
        code = 124
    if stopped and code is None:
        code = 1
    return CommandResult(command, int(code), "".join(parts["out"]), "".join(parts["err"]))


def run_pty_command(
    args: Sequence[str],
    *,
    passphrase: str,
    timeout: float | None,
    env: Mapping[str, str] | None,
    on_output: Callable[[str], None] | None = None,
    stop_when: Callable[[str], bool] | None = None,
) -> CommandResult:
    """Run ipatool on a hidden ConPTY and answer the keychain prompt once."""

    command = tuple(str(arg) for arg in args)
    pty_process = _load_pty_process()
    if pty_process is None:
        return CommandResult(command, 127, "", "pywinpty is not installed")
    process_env = child_env(env)
    process_env.setdefault("TERM", "xterm-256color")
    try:
        proc = pty_process.spawn(
            list(command),
            env=process_env,
            dimensions=(32, 120),
        )
    except Exception as exc:
        return CommandResult(command, 127, "", f"could not start ipatool: {exc}")

    chunks: queue.Queue[str] = queue.Queue()

    def reader() -> None:
        while True:
            try:
                data = proc.read(1024)
            except Exception:
                break
            if not data:
                if not proc.isalive():
                    break
                continue
            chunks.put(str(data))

    threading.Thread(target=reader, name="ipatool-pty-reader", daemon=True).start()

    sent = False
    buf = ""
    parts: list[str] = []
    kept = 0
    limit = 4_000_000
    deadline = time.monotonic() + (1800.0 if timeout is None else max(timeout, 0.1))
    while time.monotonic() < deadline:
        try:
            raw = chunks.get(timeout=0.4)
        except queue.Empty:
            if not proc.isalive():
                break
            continue
        text = _visible_output(raw, (passphrase,))
        if kept < limit and text:
            parts.append(text[: limit - kept])
            kept += len(parts[-1])
        buf = (buf + text)[-2000:]
        if on_output is not None and text:
            on_output(text)
        if (not sent) and passphrase and _prompt_hit(buf, _PASSPHRASE_HINTS):
            proc.write(passphrase + "\r")
            sent = True
            buf = ""
        if stop_when is not None and stop_when("".join(parts)):
            try:
                proc.terminate(force=True)
            except Exception:
                pass
            break
        if not proc.isalive() and chunks.empty():
            break
    else:
        try:
            proc.terminate(force=True)
        except Exception:
            pass
        return CommandResult(command, 124, "", "command timed out")

    while True:
        try:
            raw = chunks.get_nowait()
        except queue.Empty:
            break
        text = _visible_output(raw, (passphrase,))
        if kept < limit and text:
            parts.append(text[: limit - kept])
            kept += len(parts[-1])

    code = getattr(proc, "exitstatus", None)
    for _ in range(20):
        if code is not None:
            break
        if not proc.isalive():
            time.sleep(0.05)
            code = getattr(proc, "exitstatus", None)
            continue
        break
    if code is None and proc.isalive():
        try:
            proc.terminate(force=True)
        except Exception:
            pass
        code = 124
    if code is None:
        code = 1
    transcript = "".join(parts)
    stdout = transcript
    if _command_wants_json(command):
        stdout = extract_json_document(transcript) or ""
    stderr = "" if int(code) == 0 else transcript
    return CommandResult(command, int(code), stdout, stderr)


class KeychainRunner(Runner):
    """Feed a remembered keychain passphrase to each new ipatool process.

    ipatool keeps the passphrase only in that process. The GUI asks once and
    replays it, never through argv or the environment: as the first line of
    stdin with ``--keychain-passphrase-stdin`` on AppRestore's patched ipatool
    (``apprestore_core.ipatool_caps``), else through the hidden ConPTY/pty.
    """

    def __init__(self, passphrase_of: Callable[[], str]) -> None:
        super().__init__()
        self._passphrase_of = passphrase_of
        self.on_output: Callable[[str], None] | None = None
        self.stop_when: Callable[[str], bool] | None = None

    def run(
        self,
        args: Sequence[str],
        *,
        check: bool = False,
        capture: bool = True,
        output_to_stderr: bool = False,
        timeout: float | None = None,
        env: Mapping[str, str] | None = None,
    ) -> CommandResult:
        passphrase = self._passphrase_of()
        if passphrase and _is_ipatool_command(args):
            from apprestore_core.ipatool_caps import STDIN_FLAG, supports_passphrase_stdin

            if supports_passphrase_stdin(str(args[0])):
                # Patched ipatool: the passphrase goes to stdin, never argv/env.
                command = [str(arg) for arg in args]
                if STDIN_FLAG not in command:
                    command.append(STDIN_FLAG)
                result = run_stdin_command(
                    command,
                    passphrase=passphrase,
                    timeout=timeout,
                    env=env,
                    on_output=self.on_output,
                    stop_when=self.stop_when,
                )
                if check and result.returncode != 0:
                    raise CommandError(result)
                return result
            # Old ipatool: the hidden terminal answers its prompt (fallback).
            result = run_pty_command(
                args,
                passphrase=passphrase,
                timeout=timeout,
                env=env,
                on_output=self.on_output,
                stop_when=self.stop_when,
            )
            if check and result.returncode != 0:
                raise CommandError(result)
            return result
        return super().run(
            args,
            check=check,
            capture=capture,
            output_to_stderr=output_to_stderr,
            timeout=timeout,
            env=env,
        )


def login_argv(ipatool: str) -> list[str]:
    """``ipatool auth login`` for the window (Макс, путь A): one interactive process on
    a terminal. No ``--non-interactive`` (ipatool would skip «enter 2FA code: »), no
    ``-e``/``-p``: the Apple ID and the password are typed at ipatool's own prompts,
    so neither shows up in ``ps`` / argv / the environment (Лена, п.2)."""

    return [ipatool, "auth", "login"]


#: ipatool cde7d00 prompts, verbatim, printed without a newline (cmd/auth.go, prompt.go).
PROMPT_APPLE_ID = "enter Apple ID (email address or phone number): "
PROMPT_PASSWORD = "enter password: "
PROMPT_CODE = "enter 2FA code: "


class PromptScrubber:
    """Drop whatever the terminal echoes after a prompt up to the end of that line
    (Лена, п.1): the password and the 2FA code never reach the transcript, the
    window or a log. Works across chunk boundaries."""

    _PROMPTS = (PROMPT_APPLE_ID, PROMPT_PASSWORD, PROMPT_CODE)

    def __init__(self) -> None:
        self._muted = False
        self._tail = ""

    def feed(self, text: str) -> str:
        out: list[str] = []
        for ch in text:
            if self._muted:
                if ch == "\n":
                    self._muted = False
                    out.append(ch)
                continue
            out.append(ch)
            self._tail = (self._tail + ch)[-64:]
            if any(self._tail.casefold().endswith(p.casefold()) for p in self._PROMPTS):
                self._muted = True
                self._tail = ""
        return "".join(out)


def _prompt_hit(buffer: str, hints: tuple[str, ...]) -> bool:
    folded = buffer.casefold()
    return any(hint in folded for hint in hints)


def probe_keychain() -> str:
    """Как терминал видит сессию, но без вопроса в консоль.

    ``in`` — связка уже открыта. ``locked`` — учётка есть, нужен пароль связки.
    ``out`` — сохранённой сессии нет, нужен вход в Apple ID.
    """
    ipatool = _which_ipatool()
    if not ipatool:
        return "out"
    env = child_env(AppRestoreTools()._ipatool_env())
    flags = windows_creationflags() if sys.platform == "win32" else 0
    try:
        proc = subprocess.run(
            [ipatool, "--non-interactive", "--format", "json", "auth", "info"],
            check=False,
            capture_output=True,
            text=True,
            timeout=20,
            env=env,
            encoding="utf-8",
            errors="replace",
            creationflags=flags,
        )
    except (OSError, subprocess.TimeoutExpired):
        return "out"
    if proc.returncode == 0:
        return "in"
    folded = f"{proc.stdout}\n{proc.stderr}".casefold()
    if "passphrase" in folded or "handle is invalid" in folded:
        return "locked"
    return "out"


class AppleLogin(QThread):
    """Interactive ``ipatool auth login`` with live status for the window."""

    output = Signal(str)
    status = Signal(str)
    need_input = Signal(str)
    done = Signal(object)

    def __init__(
        self,
        email: str,
        password: str,
        code: str = "",
        passphrase: str = "",
    ) -> None:
        super().__init__()
        self._email = email.strip()
        self._password = password
        self._code = code.strip()
        self._passphrase = passphrase
        self._purpose = "auto"
        self._inbox: queue.Queue[tuple[str, str]] = queue.Queue()
        self._cancel = threading.Event()
        self._proc: object | None = None

    def submit(self, kind: str, value: str) -> None:
        self._inbox.put((kind, value))

    def cancel(self) -> None:
        self._cancel.set()
        proc = self._proc
        if proc is not None:
            try:
                proc.terminate(force=True)  # type: ignore[attr-defined]
            except Exception:
                pass

    def run(self) -> None:
        try:
            state = probe_keychain()
            if state == "in":
                result = AuthResult(True, "Сессия уже открыта. Повторный вход не нужен.")
            elif state == "locked":
                if not self._passphrase:
                    self.need_input.emit("passphrase")
                    result = AuthResult(
                        False,
                        "Сессия Apple ID уже сохранена. В терминале ipatool спрашивает пароль связки ключей — это не пароль Apple ID. Введите его в нижнее поле и нажмите «Открыть сессию».",
                    )
                else:
                    result = _drive_windows_unlock(
                        passphrase=self._passphrase,
                        on_output=self.output.emit,
                        on_status=self.status.emit,
                        on_need=self.need_input.emit,
                        inbox=self._inbox,
                        cancel=self._cancel,
                        capture_proc=self._remember_proc,
                    )
            else:
                result = _drive_windows_login(
                    email=self._email,
                    password=self._password,
                    code=self._code,
                    passphrase=self._passphrase,
                    on_output=self.output.emit,
                    on_status=self.status.emit,
                    on_need=self.need_input.emit,
                    inbox=self._inbox,
                    cancel=self._cancel,
                    capture_proc=self._remember_proc,
                )
        except Exception as exc:  # noqa: BLE001 - the window must show the failure
            result = AuthResult(False, f"Вход не запустился: {exc}")
        # nothing of the answers stays in this object once ipatool has answered
        self._password = self._code = ""
        while True:
            try:
                self._inbox.get_nowait()
            except queue.Empty:
                break
        self.done.emit(result)

    def _remember_proc(self, proc: object) -> None:
        self._proc = proc


def _drive_windows_unlock(
    *,
    passphrase: str,
    on_output: Callable[[str], None] | None,
    on_status: Callable[[str], None] | None,
    on_need: Callable[[str], None] | None,
    inbox: queue.Queue[tuple[str, str]] | None,
    cancel: threading.Event | None,
    capture_proc: Callable[[object], None] | None = None,
) -> AuthResult:
    """Открыть уже сохранённую сессию, как это делает терминал."""
    if not passphrase:
        return AuthResult(False, "Введите пароль связки ключей.")
    ipatool = _which_ipatool()
    if not ipatool:
        return AuthResult(False, "ipatool не найден. Установите AppRestore или добавьте ipatool в PATH.")
    pty_process = _load_pty_process()
    if pty_process is None:
        return AuthResult(
            False,
            "Для входа из окна не хватает пакета pywinpty. Перезапустите AppRestore после установки.",
        )

    def say(text: str) -> None:
        if on_status:
            on_status(text)

    say("Открываем сохранённую сессию. Пароль Apple ID для этого не нужен.")
    env = child_env(AppRestoreTools()._ipatool_env())
    env.setdefault("TERM", "xterm-256color")
    try:
        proc = pty_process.spawn(
            [ipatool, "auth", "info"],
            env=env,
            dimensions=(32, 100),
        )
    except Exception as exc:
        return AuthResult(False, f"Не удалось запустить ipatool: {exc}")
    if capture_proc:
        capture_proc(proc)

    secrets = [passphrase]
    chunks: queue.Queue[str] = queue.Queue()
    transcript = ""

    def reader() -> None:
        while True:
            try:
                data = proc.read(1024)
            except Exception:
                break
            if not data:
                if not proc.isalive():
                    break
                continue
            chunks.put(str(data))

    threading.Thread(target=reader, name="ipatool-keychain-reader", daemon=True).start()

    sent = False
    asked = False
    buf = ""
    deadline = time.monotonic() + 120
    user_inbox = inbox or queue.Queue()
    stop = cancel or threading.Event()
    while time.monotonic() < deadline and not stop.is_set():
        while True:
            try:
                _kind, value = user_inbox.get_nowait()
            except queue.Empty:
                break
            cleaned = value.strip()
            if not cleaned:
                continue
            secrets.append(cleaned)
            proc.write(cleaned + "\r")
            sent = True
            say("Пароль связки отправлен.")
            buf = ""
        try:
            raw = chunks.get(timeout=0.4)
        except queue.Empty:
            if not proc.isalive():
                break
            continue
        text = _visible_output(raw, tuple(secrets))
        if text:
            transcript = (transcript + text)[-4000:]
            if on_output:
                on_output(text)
        buf = (buf + text)[-2000:]
        if (not sent) and _prompt_hit(buf, _PASSPHRASE_HINTS):
            proc.write(passphrase + "\r")
            sent = True
            say("Пароль связки отправлен.")
            buf = ""
            continue
        if (not sent) and (not asked) and on_need and _prompt_hit(buf, ("password", "пароль")):
            asked = True
            on_need("passphrase")
            say("Нужен пароль связки ключей, не пароль Apple ID.")

    if stop.is_set():
        return AuthResult(False, "Вход отменён.", session_open=False)
    if proc.isalive():
        try:
            proc.terminate(force=True)
        except Exception:
            pass
        return AuthResult(
            False,
            "Не удалось открыть связку. Проверьте пароль и попробуйте ещё раз.",
            session_open=False,
        )
    return classify_keychain_unlock(getattr(proc, "exitstatus", None), transcript)


def _drive_windows_login(
    *,
    email: str,
    password: str,
    code: str,
    passphrase: str,
    on_output: Callable[[str], None] | None,
    on_status: Callable[[str], None] | None,
    on_need: Callable[[str], None] | None,
    inbox: queue.Queue[tuple[str, str]] | None,
    cancel: threading.Event | None,
    capture_proc: Callable[[object], None] | None = None,
    timeout: float = 1800,
) -> AuthResult:
    if not email or "@" not in email:
        return AuthResult(False, "Укажите корректный email Apple ID")
    if not password:
        return AuthResult(False, "Введите пароль Apple ID")
    ipatool = _which_ipatool()
    if not ipatool:
        return AuthResult(False, "ipatool не найден. Установите AppRestore или добавьте ipatool в PATH.")
    pty_process = _load_pty_process()
    if pty_process is None:
        return AuthResult(
            False,
            "Для входа из окна не хватает пакета pywinpty. Перезапустите AppRestore после установки.",
        )

    def say(text: str) -> None:
        if on_status:
            on_status(text)

    say("Проверяем, открывается ли сервер Apple…")
    env = child_env(AppRestoreTools()._ipatool_env())
    env.setdefault("TERM", "xterm-256color")
    if not _apple_login_host_reachable(env):
        return AuthResult(
            False,
            "Сервер Apple не ответил. "
            "Проверьте подключение к интернету и попробуйте ещё раз.",
        )
    say("Запускаем ipatool. Первый вход может занять несколько минут: скачивается служебный компонент.")
    try:
        proc = pty_process.spawn(
            login_argv(ipatool),
            env=env,
            dimensions=(32, 100),
        )
    except Exception as exc:
        return AuthResult(False, f"Не удалось запустить ipatool: {exc}")
    if capture_proc:
        capture_proc(proc)

    secrets: list[str] = [item for item in (email, password, code, passphrase) if item]
    scrub = PromptScrubber()
    chunks: queue.Queue[str] = queue.Queue()
    transcript = ""

    def reader() -> None:
        while True:
            try:
                data = proc.read(1024)
            except Exception:
                break
            if not data:
                if not proc.isalive():
                    break
                continue
            chunks.put(str(data))

    threading.Thread(target=reader, name="ipatool-conpty-reader", daemon=True).start()

    sent_email = False
    sent_password = False
    sent_code = False
    sent_passphrase = False
    asked_code = False
    asked_passphrase = False
    said_wait = False
    buf = ""
    started = time.monotonic()
    last_status = started
    deadline = started + max(timeout, 30)
    user_inbox = inbox or queue.Queue()
    stop = cancel or threading.Event()

    while time.monotonic() < deadline and not stop.is_set():
        while True:
            try:
                kind, value = user_inbox.get_nowait()
            except queue.Empty:
                break
            cleaned = value.strip()
            if not cleaned:
                continue
            secrets.append(cleaned)
            proc.write(cleaned + "\r")
            cleaned = value = ""
            if kind == "code":
                sent_code = True
                say("Код отправлен. Ждём ответ Apple…")
            elif kind == "passphrase":
                sent_passphrase = True
                say("Passphrase отправлен.")
            buf = ""
        try:
            raw = chunks.get(timeout=0.4)
        except queue.Empty:
            if not proc.isalive():
                break
            waiting_user = (asked_code and not sent_code) or (
                asked_passphrase and not sent_passphrase
            )
            if waiting_user:
                continue
            if not said_wait and time.monotonic() - started > 8:
                said_wait = True
                say("ipatool ещё работает. На первом входе он скачивает служебный компонент, это нормально.")
            elif time.monotonic() - last_status > 20:
                last_status = time.monotonic()
                elapsed = int(time.monotonic() - started)
                say(f"Всё ещё ждём ipatool, прошло {elapsed} с.")
            continue
        text = scrub.feed(_visible_output(raw, tuple(secrets)))
        if text:
            transcript = (transcript + text)[-6000:]  # in memory only, never written out
            if on_output:
                on_output(text)
        buf = (buf + text)[-2000:]
        if (not sent_email) and _prompt_hit(buf, ("enter apple id",)):
            proc.write(email + "\r")
            sent_email = True
            buf = ""
            continue
        if (not sent_passphrase) and _prompt_hit(buf, _PASSPHRASE_HINTS):
            if passphrase:
                proc.write(passphrase + "\r")
                sent_passphrase = True
                say("Пароль связки отправлен.")
                buf = ""
            elif not asked_passphrase and on_need:
                asked_passphrase = True
                on_need("passphrase")
                say("ipatool спрашивает пароль связки ключей. Введите его и нажмите «Отправить».")
            continue
        if (not sent_code) and _prompt_hit(buf, _CODE_HINTS):
            if code:
                proc.write(code + "\r")
                sent_code = True
                say("Код отправлен. Ждём ответ Apple…")
                buf = ""
            elif not asked_code and on_need:
                asked_code = True
                on_need("code")
                say("Нужен код из сообщения Apple. Введите его выше и нажмите «Отправить код».")
            continue
        if (not sent_password) and _prompt_hit(buf, _PASSWORD_HINTS):
            proc.write(password + "\r")
            sent_password = True
            said_wait = True
            last_status = time.monotonic()
            say("Пароль отправлен. Ждём ответ Apple…")
            buf = ""
            continue

    secrets.clear()  # the code and the password are not kept after ipatool answered
    if stop.is_set():
        return AuthResult(False, "Вход отменён.")
    if proc.isalive():
        try:
            proc.terminate(force=True)
        except Exception:
            pass
        return AuthResult(False, "Вход не завершился вовремя. Проверьте сеть и попробуйте ещё раз.")
    exit_status = getattr(proc, "exitstatus", None)
    if exit_status == 0:
        return AuthResult(True, "Вход выполнен. Сессия сохранена в ipatool.")
    if asked_code and not sent_code:
        return AuthResult(False, "Вход остановлен: код из сообщения не отправлен.")
    return AuthResult(False, _explain_login_failure(exit_status, transcript, code_sent=sent_code))
