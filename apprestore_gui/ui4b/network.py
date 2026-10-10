"""Is the computer offline? Only when no host answers (BUG: «Нет интернета» while the
internet works).

QuickSession's session probe (``ipatool list-versions`` → ``session_alive``) reports
NO_NETWORK for transport errors AND for any unknown ipatool failure — e.g. no saved
account or an unfinished sign-in. That is not «no internet». 4b says «Нет интернета»
only when the session probe says so AND an anonymous TCP probe to Apple's public
hosts also fails (only Apple's public hosts:
no request goes to the archive when its switch is off). A single failed lookup or Wayback request is never
«offline» either (the archive gets its own quiet note).
"""

from __future__ import annotations

import os
import socket
from collections.abc import Callable, Iterable, Mapping

#: Public hosts only: no session, no cookie, nothing about the account is sent.
PROBE_HOSTS: tuple[tuple[str, int], ...] = (
    ("itunes.apple.com", 443),
    ("init.itunes.apple.com", 443),
    ("apps.apple.com", 443),
)
PROBE_TIMEOUT_S = 4.0
#: Do not probe again sooner than this while the session probe keeps saying «offline».
REPROBE_S = 20.0

_PROXY_NAMES = ("HTTPS_PROXY", "https_proxy", "HTTP_PROXY", "http_proxy")


def any_host_answers(
    hosts: Iterable[tuple[str, int]] = PROBE_HOSTS,
    *,
    timeout: float = PROBE_TIMEOUT_S,
    env: Mapping[str, str] | None = None,
    connect: Callable[..., object] = socket.create_connection,
) -> bool:
    """True when at least one host accepts a TCP connection. Behind an explicit
    proxy a raw socket would go around it, so the answer is «assume online»."""

    environ = os.environ if env is None else env
    if any(environ.get(name) for name in _PROXY_NAMES):
        return True
    for host, port in hosts:
        try:
            sock = connect((host, port), timeout=timeout)
        except OSError:
            continue
        try:
            sock.close()  # type: ignore[attr-defined]
        except Exception:  # noqa: BLE001
            pass
        return True
    return False


def is_offline(session_state: str, hosts_answer: bool | None) -> bool:
    """«Нет интернета» only on the session probe's «offline» with no host answering.
    ``hosts_answer`` None = not probed yet → not offline (never guess «offline»)."""

    return session_state == "offline" and hosts_answer is False
