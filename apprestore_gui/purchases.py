"""Purchase list and session check for the Qt Quick window, without Qt.

All decisions live here so they can be tested on fakes; ``quick_session``
only forwards the views to QML. Uses Макс's ``apprestore_core.ipatool_api``:

* :class:`SessionChecker` runs ``session_alive`` on a worker thread and maps
  the three outcomes to a :class:`SessionView` (ALIVE → ``alive``, EXPIRED →
  ``expired`` + offer to sign in again, NO_NETWORK → ``offline``; nobody is
  signed out because of a bad line).
* :class:`PurchasesLoader` shows the cached list first, then reads
  ``iter_purchases`` page by page, merging as pages arrive. ``cancel()`` sets
  the cancel event (ipatool checks it before the next page). Only a complete
  refresh replaces the cache. ``FETCH_ALL`` asks for ``list-purchases --all``
  (one page with everything); the loader never relies on the number of pages.
"""

from __future__ import annotations

import inspect
import os
import subprocess
import threading
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from apprestore_core.ipatool_api import (
    ErrorCode,
    IpatoolError,
    RunResult,
    SessionCheck,
    SessionState,
)
from apprestore_core.purchases_cache import (
    CachedPurchase,
    PurchasesCache,
    account_key,
    dedupe,
)
from apprestore_gui.errors import explain_ipatool_error

#: Ask ipatool for the whole history in one call (``list-purchases --all``,
#: ipatool patch 0002). ``iter_purchases`` falls back to pages of 100 on an
#: ipatool without the patch; the loader works the same either way.
FETCH_ALL = True
#: ``iter_purchases`` keyword for that mode.
FETCH_ALL_KEYWORD = "prefer_all"

SESSION_TIMEOUT_S = 8.0

ClientFactory = Callable[[], Any]


# --------------------------------------------------------------------------
# Session
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class SessionView:
    #: ``unknown`` | ``checking`` | ``alive`` | ``expired`` | ``offline`` | ``error``
    state: str
    note: str = ""
    #: Offer "sign in again" (EXPIRED only).
    relogin: bool = False
    #: Never True: no outcome of the probe signs the user out.
    sign_out: bool = False


SESSION_UNKNOWN = SessionView("unknown")
SESSION_CHECKING = SessionView("checking", "Проверяем связь с Apple…")

OFFLINE_NOTE = "Нет связи с Apple. Вы остаётесь в аккаунте, проверим ещё раз позже."


def session_view(check: SessionCheck) -> SessionView:
    if check.state is SessionState.ALIVE:
        return SessionView("alive", "")
    if check.state is SessionState.EXPIRED:
        reason = explain_ipatool_error(check.error) if check.error is not None else ""
        return SessionView("expired", reason or "Сессия Apple ID истекла. Войдите заново.", relogin=True)
    return SessionView("offline", OFFLINE_NOTE)


def session_error_view(error: BaseException) -> SessionView:
    """``session_alive`` raised (only BINARY_MISSING by contract) or the client failed."""

    if isinstance(error, IpatoolError) and error.code is ErrorCode.BINARY_MISSING:
        return SessionView("error", explain_ipatool_error(error))
    return SessionView("offline", OFFLINE_NOTE)


class SessionChecker:
    """One background probe at a time; result goes to ``on_result``."""

    def __init__(
        self,
        client_factory: ClientFactory,
        on_result: Callable[[SessionView], None],
        *,
        timeout: float = SESSION_TIMEOUT_S,
    ) -> None:
        self._factory = client_factory
        self._on_result = on_result
        self._timeout = timeout
        self._lock = threading.Lock()
        self._running = False
        self._thread: threading.Thread | None = None

    @property
    def running(self) -> bool:
        with self._lock:
            return self._running

    def start(self) -> bool:
        with self._lock:
            if self._running:
                return False
            self._running = True
        self._on_result(SESSION_CHECKING)
        self._thread = threading.Thread(target=self._run, name="apprestore-session-alive", daemon=True)
        self._thread.start()
        return True

    def join(self, timeout: float | None = None) -> None:
        if self._thread is not None:
            self._thread.join(timeout)

    def _run(self) -> None:
        try:
            view = session_view(self._factory().session_alive(timeout=self._timeout))
        except Exception as exc:  # noqa: BLE001 - shown as a state, never raised into Qt
            view = session_error_view(exc)
        with self._lock:
            self._running = False
        self._on_result(view)


# --------------------------------------------------------------------------
# Purchases
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class PurchasesView:
    rows: tuple[dict[str, object], ...] = ()
    total: int | None = None
    busy: bool = False
    progress: str = ""
    note: str = ""
    from_cache: bool = False
    #: The refresh failed because the session needs the user (offer sign-in).
    session_problem: bool = False

    @property
    def count(self) -> int:
        return len(self.rows)


def progress_text(count: int, total: int | None) -> str:
    if total is not None and total > 0:
        return f"{min(count, total)} из {total}"
    return str(count)


def _row(item: CachedPurchase) -> dict[str, object]:
    name = item.name.strip() or item.bundle_id or str(item.track_id)
    return {
        "trackId": str(item.track_id),
        "bundleId": item.bundle_id,
        "name": name,
        "date": item.purchase_date[:10],
    }


@dataclass
class _Run:
    generation: int
    account: str
    cancel: threading.Event = field(default_factory=threading.Event)


class PurchasesLoader:
    def __init__(
        self,
        cache: PurchasesCache,
        client_factory: ClientFactory,
        on_change: Callable[[PurchasesView], None],
        *,
        fetch_all: bool | None = None,
    ) -> None:
        self._cache = cache
        self._factory = client_factory
        self._on_change = on_change
        self.fetch_all = FETCH_ALL if fetch_all is None else fetch_all
        self._lock = threading.Lock()
        self._account = ""
        self._generation = 0
        self._run: _Run | None = None
        self._thread: threading.Thread | None = None
        self._view = PurchasesView()

    # -- state ------------------------------------------------------------

    @property
    def view(self) -> PurchasesView:
        with self._lock:
            return self._view

    @property
    def account(self) -> str:
        with self._lock:
            return self._account

    def _publish(self, view: PurchasesView, generation: int | None = None) -> bool:
        with self._lock:
            if generation is not None and generation != self._generation:
                return False
            self._view = view
        self._on_change(view)
        return True

    def _stop_unlocked(self) -> None:
        self._generation += 1
        if self._run is not None:
            self._run.cancel.set()
            self._run = None

    # -- account lifecycle ------------------------------------------------

    def set_account(self, email: str) -> bool:
        """Follow the open Apple ID. Another account → drop the old cache.

        Returns True when the account changed.
        """

        key = self._cache.account_key(email)
        with self._lock:
            if key == self._account:
                return False
            previous = self._account
            self._stop_unlocked()
            self._account = key
        if previous or not key:
            self._cache.delete()
        self._publish(PurchasesView())
        if key:
            self.show_cached()
        return True

    def forget(self) -> None:
        """Sign-out: stop loading, delete the cache, empty the list."""

        with self._lock:
            self._stop_unlocked()
            self._account = ""
        self._cache.delete()
        self._publish(PurchasesView())

    # -- loading ----------------------------------------------------------

    def show_cached(self) -> PurchasesView:
        with self._lock:
            account = self._account
            generation = self._generation
        snapshot = self._cache.load(account) if account else None
        if snapshot is None:
            view = PurchasesView()
        else:
            view = PurchasesView(
                rows=tuple(_row(item) for item in snapshot.items),
                total=snapshot.total,
                progress=progress_text(len(snapshot.items), snapshot.total),
                from_cache=True,
            )
        self._publish(view, generation)
        return view

    def start(self) -> bool:
        with self._lock:
            if not self._account:
                note = "Войдите в Apple ID, чтобы увидеть покупки."
                view = PurchasesView(note=note)
                self._view = view
                run = None
            elif self._run is not None:
                return False
            else:
                self._generation += 1
                run = _Run(self._generation, self._account)
                self._run = run
        if run is None:
            self._on_change(view)
            return False
        self._thread = threading.Thread(target=self._load, args=(run,), name="apprestore-purchases", daemon=True)
        self._thread.start()
        return True

    def cancel(self) -> None:
        with self._lock:
            run = self._run
            if run is None:
                return
            run.cancel.set()
            view = PurchasesView(
                rows=self._view.rows,
                total=self._view.total,
                busy=True,
                progress=self._view.progress,
                note="Останавливаем после текущей страницы…",
                from_cache=self._view.from_cache,
            )
        self._publish(view, run.generation)

    def join(self, timeout: float | None = None) -> None:
        if self._thread is not None:
            self._thread.join(timeout)

    def _pages(self, client: Any, cancel: threading.Event):
        try:
            accepted = inspect.signature(client.iter_purchases).parameters
        except (TypeError, ValueError):
            accepted = {}
        kwargs: dict[str, object] = {}
        if FETCH_ALL_KEYWORD in accepted:
            kwargs[FETCH_ALL_KEYWORD] = bool(self.fetch_all)
        return client.iter_purchases(cancel, **kwargs)

    def _load(self, run: _Run) -> None:
        snapshot = self._cache.load(run.account)
        cached = list(snapshot.items) if snapshot is not None else []
        cached_total = snapshot.total if snapshot is not None else None
        fresh: list[CachedPurchase] = []
        seen: set[int] = set()
        total: int | None = cached_total

        def merged() -> tuple[dict[str, object], ...]:
            rest = [item for item in cached if item.track_id not in seen]
            return tuple(_row(item) for item in fresh + rest)

        self._publish(
            PurchasesView(
                rows=merged(),
                total=total,
                busy=True,
                progress="Загружаем…" if not cached else progress_text(0, total),
                from_cache=bool(cached),
            ),
            run.generation,
        )
        finished = False
        try:
            client = self._factory()
            for page in self._pages(client, run.cancel):
                for purchase in page.items:
                    item = CachedPurchase.from_purchase(purchase)
                    if item.track_id in seen:
                        continue
                    seen.add(item.track_id)
                    fresh.append(item)
                if page.total is not None:
                    total = page.total
                if not self._publish(
                    PurchasesView(
                        rows=merged(),
                        total=total,
                        busy=True,
                        progress=progress_text(len(fresh), total),
                        from_cache=bool(cached),
                    ),
                    run.generation,
                ):
                    return  # account changed or signed out: drop silently
            finished = not run.cancel.is_set()
        except IpatoolError as error:
            self._finish(
                run,
                PurchasesView(
                    rows=merged(),
                    total=total,
                    progress=progress_text(len(fresh), total) if fresh else "",
                    note=explain_ipatool_error(error),
                    from_cache=bool(cached),
                    session_problem=error.is_session_problem,
                ),
            )
            return
        except Exception as error:  # noqa: BLE001 - never leak into Qt
            self._finish(
                run,
                PurchasesView(
                    rows=merged(),
                    total=total,
                    note=explain_ipatool_error(error),
                    from_cache=bool(cached),
                ),
            )
            return

        if not finished:
            self._finish(
                run,
                PurchasesView(
                    rows=merged(),
                    total=total,
                    progress=progress_text(len(fresh), total),
                    note=f"Остановлено: обновлено {progress_text(len(fresh), total)}.",
                    from_cache=bool(cached),
                ),
            )
            return

        final = dedupe(fresh)
        with self._lock:
            still_current = run.generation == self._generation and run.account == self._account
        if still_current:
            try:
                self._cache.save(run.account, final, total=total, complete=True)
                note = ""
            except OSError:
                note = "Список обновлён, но сохранить его на диск не получилось."
        else:
            return
        self._finish(
            run,
            PurchasesView(
                rows=tuple(_row(item) for item in final),
                total=total,
                progress=progress_text(len(final), total),
                note=note,
            ),
        )

    def _finish(self, run: _Run, view: PurchasesView) -> None:
        with self._lock:
            if run.generation != self._generation:
                return
            self._run = None
            self._view = view
        self._on_change(view)


# --------------------------------------------------------------------------
# Runner for IpatoolClient inside the GUI
# --------------------------------------------------------------------------


def gui_runner(
    passphrase_of: Callable[[], str],
    env_of: Callable[[], Mapping[str, str]],
) -> Callable[[Sequence[str], float], RunResult]:
    """``runner`` for ``IpatoolClient`` that keeps the GUI's rules.

    * The keychain passphrase never goes into argv: the client is built with
      ``keychain_passphrase=""`` and, when the GUI holds a passphrase, the
      call runs on the hidden ConPTY/pty that answers ipatool's prompt
      (``--non-interactive`` is dropped for that call only, otherwise ipatool
      would refuse to ask).
    * The system proxy environment of ``AppRestoreTools`` is passed on.
    """

    def run(argv: Sequence[str], timeout: float) -> RunResult:
        extra = dict(env_of() or {})
        secret = passphrase_of()
        if secret:
            from apprestore_gui.auth_pty import run_pty_command

            args = [arg for arg in argv if arg != "--non-interactive"]
            result = run_pty_command(args, passphrase=secret, timeout=timeout, env=extra)
            if result.returncode == 124 and "timed out" in (result.stderr or ""):
                raise subprocess.TimeoutExpired(args, timeout)
            if result.returncode == 127 and not result.stdout:
                raise FileNotFoundError(args[0])
            return RunResult(result.returncode, result.stdout or "", result.stderr or "")
        from apprestore_core.command import windows_creationflags

        env = os.environ.copy()
        env.update(extra)
        completed = subprocess.run(  # noqa: S603 - fixed argv from IpatoolClient
            list(argv),
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            env=env,
            creationflags=windows_creationflags(),
        )
        return RunResult(completed.returncode, completed.stdout, completed.stderr)

    return run
