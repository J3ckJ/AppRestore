from __future__ import annotations

import ctypes
import os
import sys
import time
from pathlib import Path

import pytest

from apprestore_core.command import (
    _CAPTURE_LIMIT_BYTES,
    CommandError,
    Runner,
)


def _pid_is_alive(pid: int) -> bool:
    if os.name == "nt":
        loader = getattr(ctypes, "WinDLL", None)
        if loader is None:
            return False
        kernel32 = loader("kernel32", use_last_error=True)
        handle = kernel32.OpenProcess(0x1000, False, pid)
        if not handle:
            return False
        try:
            exit_code = ctypes.c_ulong()
            if not kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code)):
                return False
            return exit_code.value == 259
        finally:
            kernel32.CloseHandle(handle)

    proc_stat = Path(f"/proc/{pid}/stat")
    if proc_stat.is_file():
        try:
            fields = proc_stat.read_text(encoding="utf-8").split()
        except OSError:
            return False
        return len(fields) > 2 and fields[2] != "Z"
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def test_capture_is_bounded_while_both_streams_are_drained() -> None:
    output_size = _CAPTURE_LIMIT_BYTES + 256 * 1024
    script = (
        "import os; "
        f"size={output_size}; "
        "os.write(1, b'A' * size); "
        "os.write(2, b'B' * size)"
    )

    result = Runner().run(
        [sys.executable, "-c", script],
        check=True,
        timeout=30,
    )

    assert len(result.stdout.encode("utf-8")) <= _CAPTURE_LIMIT_BYTES
    assert len(result.stderr.encode("utf-8")) <= _CAPTURE_LIMIT_BYTES
    assert result.stdout.startswith("A")
    assert result.stderr.startswith("B")
    assert "output truncated by AppRestore" in result.stdout
    assert "output truncated by AppRestore" in result.stderr


def test_timeout_kills_the_descendant_process_tree(tmp_path: Path) -> None:
    child_pid_path = tmp_path / "child.pid"
    child_script = "import time; time.sleep(60)"
    parent_script = (
        "import os, pathlib, subprocess, sys, time; "
        f"child=subprocess.Popen([sys.executable, '-c', {child_script!r}]); "
        "pathlib.Path(os.environ['APPRESTORE_TEST_CHILD_PID']).write_text("
        "str(child.pid), encoding='utf-8'); "
        "print('parent-ready', flush=True); "
        "print('parent-error', file=sys.stderr, flush=True); "
        "time.sleep(60)"
    )

    with pytest.raises(CommandError) as caught:
        Runner().run(
            [sys.executable, "-c", parent_script],
            timeout=1.5,
            env={"APPRESTORE_TEST_CHILD_PID": str(child_pid_path)},
        )

    assert caught.value.result.returncode == 124
    assert "parent-ready" in caught.value.result.stdout
    assert "parent-error" in caught.value.result.stderr
    assert child_pid_path.is_file()
    child_pid = int(child_pid_path.read_text(encoding="utf-8"))

    deadline = time.monotonic() + 5
    while _pid_is_alive(child_pid) and time.monotonic() < deadline:
        time.sleep(0.05)
    assert not _pid_is_alive(child_pid)


def test_runner_never_interprets_an_executable_as_shell_source(
    tmp_path: Path,
) -> None:
    marker = tmp_path / "shell-ran.txt"
    malicious_executable = (
        f"{tmp_path / 'missing.exe'} & "
        f'"{sys.executable}" -c "from pathlib import Path; '
        f"Path(r'{marker}').write_text('unsafe')\""
    )

    with pytest.raises(CommandError) as caught:
        Runner().run([malicious_executable], timeout=5)

    assert caught.value.result.returncode == 127
    assert not marker.exists()


@pytest.mark.parametrize("timeout", [0, -1, float("inf"), float("nan")])
def test_runner_rejects_non_positive_or_non_finite_timeouts(
    timeout: float,
) -> None:
    with pytest.raises(ValueError, match="positive finite"):
        Runner().run(["must-not-start"], timeout=timeout)


@pytest.mark.skipif(os.name == "nt", reason="POSIX process-group contract")
def test_posix_runner_starts_a_new_session() -> None:
    script = "import os; print(os.getpid() == os.getsid(0))"

    result = Runner().run([sys.executable, "-c", script], check=True)

    assert result.stdout.strip() == "True"


@pytest.mark.skipif(os.name != "nt", reason="Windows process-group contract")
def test_windows_timeout_cleanup_uses_trusted_system_taskkill() -> None:
    source = Path(__file__).parents[1] / "apprestore_core" / "command.py"
    command_runner = source.read_text(encoding="utf-8")

    assert "GetSystemWindowsDirectoryW" in command_runner
    assert '"System32", "taskkill.exe"' in command_runner
    assert 'getattr(subprocess, "CREATE_NEW_PROCESS_GROUP"' in command_runner
    assert "shell=False" in command_runner
    assert "shutil.which" not in command_runner
    assert "command -v" not in command_runner
