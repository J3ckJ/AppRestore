from __future__ import annotations

import os
import subprocess
from collections.abc import Mapping, Sequence

from .models import CommandResult


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
        timeout: float | None = None,
        env: Mapping[str, str] | None = None,
    ) -> CommandResult:
        command = tuple(str(arg) for arg in args)
        process_env = os.environ.copy()
        if env:
            process_env.update(env)
        if capture:
            process_env.setdefault("NO_COLOR", "1")

        try:
            completed = subprocess.run(
                command,
                check=False,
                capture_output=capture,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=timeout,
                env=process_env,
            )
        except FileNotFoundError as exc:
            result = CommandResult(command, 127, "", str(exc))
            raise CommandError(result, f"command not found: {command[0]}") from exc
        except subprocess.TimeoutExpired as exc:
            result = CommandResult(
                command,
                124,
                exc.stdout or "",
                exc.stderr or "",
            )
            raise CommandError(result, f"command timed out: {command[0]}") from exc

        result = CommandResult(
            command,
            completed.returncode,
            completed.stdout or "",
            completed.stderr or "",
        )
        if check and result.returncode != 0:
            raise CommandError(result)
        return result
