from __future__ import annotations

import ctypes
import math
import os
import signal
import subprocess
import sys
import threading
import time
from collections.abc import Mapping, Sequence
from typing import IO, Any

from .models import CommandResult


_CAPTURE_LIMIT_BYTES = 4 * 1024 * 1024
_READ_CHUNK_BYTES = 64 * 1024
_TRUNCATION_NOTICE = b"\n...[output truncated by AppRestore]...\n"
_PROCESS_TREE_GRACE_SECONDS = 1.0
_READER_SHUTDOWN_SECONDS = 5.0


def _output_text(value: str | bytes | None) -> str:
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return value or ""


class _BoundedCapture:
    def __init__(self, limit: int = _CAPTURE_LIMIT_BYTES) -> None:
        self._limit = limit
        self._buffer = bytearray()
        self._truncated = False

    def append(self, chunk: bytes) -> None:
        remaining = self._limit - len(self._buffer)
        if remaining > 0:
            self._buffer.extend(chunk[:remaining])
        if len(chunk) > remaining:
            self._truncated = True

    def value(self) -> bytes:
        if not self._truncated or self._limit < len(_TRUNCATION_NOTICE):
            return bytes(self._buffer)
        kept = self._limit - len(_TRUNCATION_NOTICE)
        return bytes(self._buffer[:kept]) + _TRUNCATION_NOTICE


def _drain_pipe(pipe: IO[bytes], capture: _BoundedCapture) -> None:
    try:
        while chunk := pipe.read(_READ_CHUNK_BYTES):
            capture.append(chunk)
    except (OSError, ValueError):
        # Forced process-tree cleanup can close a pipe while its reader is
        # blocked. Partial output collected before that point is still useful.
        pass
    finally:
        try:
            pipe.close()
        except OSError:
            pass


def _remaining_seconds(deadline: float | None) -> float | None:
    if deadline is None:
        return None
    return max(0.0, deadline - time.monotonic())


def _join_readers(
    readers: Sequence[threading.Thread],
    deadline: float | None,
) -> bool:
    for reader in readers:
        reader.join(_remaining_seconds(deadline))
    return all(not reader.is_alive() for reader in readers)


def _trusted_windows_taskkill() -> str | None:
    """Resolve taskkill from the real Windows directory, never caller PATH."""

    loader = getattr(ctypes, "WinDLL", None)
    if loader is None:
        return None
    try:
        kernel32 = loader("kernel32", use_last_error=True)
        get_windows_directory = kernel32.GetSystemWindowsDirectoryW
        buffer = ctypes.create_unicode_buffer(32768)
        length = int(get_windows_directory(buffer, len(buffer)))
    except (AttributeError, OSError, TypeError, ValueError):
        return None
    if length <= 0 or length >= len(buffer):
        return None
    candidate = os.path.join(buffer.value, "System32", "taskkill.exe")
    return candidate if os.path.isfile(candidate) else None


def _terminate_windows_tree(process: subprocess.Popen[bytes]) -> None:
    taskkill = _trusted_windows_taskkill()
    if taskkill is not None:
        try:
            killer = subprocess.Popen(
                (taskkill, "/PID", str(process.pid), "/T", "/F"),
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                shell=False,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            killer.wait(timeout=10)
        except (OSError, subprocess.TimeoutExpired):
            pass
    if process.poll() is None:
        try:
            process.kill()
        except OSError:
            pass
    try:
        process.wait(timeout=_PROCESS_TREE_GRACE_SECONDS)
    except subprocess.TimeoutExpired:
        pass


def _terminate_posix_tree(process: subprocess.Popen[bytes]) -> None:
    kill_process_group = getattr(os, "killpg", None)
    if kill_process_group is None:
        process.kill()
        return
    try:
        kill_process_group(process.pid, signal.SIGTERM)
    except (ProcessLookupError, PermissionError):
        pass
    try:
        process.wait(timeout=_PROCESS_TREE_GRACE_SECONDS)
    except subprocess.TimeoutExpired:
        pass

    # The group can outlive its original leader, so always follow with KILL.
    try:
        kill_process_group(process.pid, getattr(signal, "SIGKILL", 9))
    except (ProcessLookupError, PermissionError):
        pass
    try:
        process.wait(timeout=_PROCESS_TREE_GRACE_SECONDS)
    except subprocess.TimeoutExpired:
        try:
            process.kill()
        except OSError:
            pass


def _terminate_process_tree(process: subprocess.Popen[bytes]) -> None:
    if os.name == "nt":
        _terminate_windows_tree(process)
    else:
        _terminate_posix_tree(process)


class CommandError(RuntimeError):
    def __init__(self, result: CommandResult, message: str | None = None) -> None:
        self.result = result
        detail = message or result.stderr.strip() or result.stdout.strip()
        if not detail:
            detail = f"command exited with code {result.returncode}"
        super().__init__(detail)


class Runner:
    """Run commands without invoking a shell."""

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
        command = tuple(str(arg) for arg in args)
        process_env = os.environ.copy()
        if env:
            process_env.update(env)
        if capture:
            process_env.setdefault("NO_COLOR", "1")

        if capture and output_to_stderr:
            raise ValueError("capture and output_to_stderr are mutually exclusive")
        if timeout is not None and (
            not math.isfinite(timeout) or timeout <= 0
        ):
            raise ValueError("timeout must be a positive finite number")

        stdout_target: Any
        stderr_target: Any
        if output_to_stderr:
            # Machine-readable CLI commands reserve stdout for their final JSON
            # document.  The child still inherits stdin, while prompts and
            # progress remain visible on stderr.
            stdout_target = sys.stderr
            stderr_target = sys.stderr
        elif capture:
            stdout_target = subprocess.PIPE
            stderr_target = subprocess.PIPE
        else:
            stdout_target = None
            stderr_target = None

        try:
            process = subprocess.Popen(
                command,
                stdout=stdout_target,
                stderr=stderr_target,
                env=process_env,
                shell=False,
                start_new_session=os.name != "nt",
                creationflags=(
                    getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0x00000200)
                    if os.name == "nt"
                    else 0
                ),
            )
        except FileNotFoundError as exc:
            result = CommandResult(command, 127, "", str(exc))
            raise CommandError(result, f"command not found: {command[0]}") from exc

        stdout_capture = _BoundedCapture()
        stderr_capture = _BoundedCapture()
        readers: list[threading.Thread] = []
        pipes: list[IO[bytes]] = []
        if capture:
            assert process.stdout is not None
            assert process.stderr is not None
            pipes.extend((process.stdout, process.stderr))
            readers.extend(
                (
                    threading.Thread(
                        target=_drain_pipe,
                        args=(process.stdout, stdout_capture),
                        name=f"apprestore-stdout-{process.pid}",
                        daemon=True,
                    ),
                    threading.Thread(
                        target=_drain_pipe,
                        args=(process.stderr, stderr_capture),
                        name=f"apprestore-stderr-{process.pid}",
                        daemon=True,
                    ),
                )
            )
            for reader in readers:
                reader.start()

        deadline = None if timeout is None else time.monotonic() + timeout
        timeout_error: subprocess.TimeoutExpired | None = None
        try:
            process.wait(timeout=_remaining_seconds(deadline))
            if capture and not _join_readers(readers, deadline):
                assert timeout is not None
                timeout_error = subprocess.TimeoutExpired(command, timeout)
        except subprocess.TimeoutExpired as exc:
            timeout_error = exc

        if timeout_error is not None:
            _terminate_process_tree(process)
            reader_deadline = time.monotonic() + _READER_SHUTDOWN_SECONDS
            if capture and not _join_readers(readers, reader_deadline):
                for pipe in pipes:
                    try:
                        pipe.close()
                    except OSError:
                        pass
                _join_readers(readers, time.monotonic() + 1.0)
            result = CommandResult(
                command,
                124,
                _output_text(stdout_capture.value()),
                _output_text(stderr_capture.value()),
            )
            raise CommandError(
                result,
                f"command timed out: {command[0]}",
            ) from timeout_error

        result = CommandResult(
            command,
            process.returncode,
            _output_text(stdout_capture.value()) if capture else "",
            _output_text(stderr_capture.value()) if capture else "",
        )
        if check and result.returncode != 0:
            raise CommandError(result)
        return result
