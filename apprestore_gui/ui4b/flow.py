"""Runs the restore queue through the existing services, one app at a time.

Order of a run: free space is checked on the device first (blocked when the
known sizes alone do not fit), then every chosen app goes through the path the
old window uses:

* offloaded → ``QuickSession.restore`` (iOS redownload, IPA fallback);
* removed from the App Store → ``QuickSession.installStore`` → license gate
  (``apprestore_core.license_gate.run_with_free_license``: read-only attempt,
  price 0 in the account's country, 5/day + 15 total, journal);
* local IPA → ``QuickSession.installSaved``.

When Apple wants the user again (expired session, any ipatool session code)
the run stops and is dropped: nothing waits to resume.

-128 «Account Not In This Store» (``ErrorCode.STORE_MISMATCH``) is not a
session problem: the run is dropped too and the home screen shows «Магазин
не совпал» with «На главный» / «Войти заново». Whether the user already signed
in again with this account after a -128 is kept only in memory; a second -128
then says the app is not available in that country (no sign-in button).
After «Войти заново» / a fresh sign-in the user lands on the home screen and
presses «Вернуть» again (Ника/Лена: no auto-continue, no «продолжим
автоматически»).

No Qt here: :class:`Backend` is whatever performs those calls.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from typing import Protocol

from apprestore_core.ipatool_api import MESSAGES_RU, SESSION_CODES, classify_error
from apprestore_core.license_gate import is_store_mismatch

from apprestore_gui.ui4b.catalog import ACTION_IPA, ACTION_OFFLOADED, ACTION_STORE, RestoreItem
from apprestore_gui.ui4b.queue import CURRENT, WAIT, RestoreQueue
from apprestore_gui.ui4b.space import DeviceSpace, SpacePlan, plan_space


_SESSION_TEXTS = tuple(MESSAGES_RU[code].casefold() for code in SESSION_CODES if code in MESSAGES_RU)


def needs_signin(text: str) -> bool:
    """The failure means «sign in again», not «this app did not work»."""

    if not text or is_store_mismatch(text):
        return False  # -128 has its own screen
    if classify_error(text) in SESSION_CODES:
        return True
    folded = text.casefold()
    return any(message in folded for message in _SESSION_TEXTS) or "войдите заново" in folded


class Backend(Protocol):
    def restore_offloaded(self, keys: list[str]) -> None: ...

    def install_store(self, store_id: str) -> None: ...

    def install_ipa(self, path: str) -> None: ...


class RestoreFlow:
    def __init__(self, backend: Backend, on_change: Callable[[], None] = lambda: None) -> None:
        self.backend = backend
        self.queue = RestoreQueue()
        self.on_change = on_change
        self.last_plan: SpacePlan | None = None
        self._batch: set[str] = set()
        self._started: set[str] = set()
        #: The run was dropped because Apple wants the user to sign in again.
        self.needs_signin = False
        self._account: tuple[bool, str, bool] | None = None
        self.account_email = ""
        #: "" | "mismatch" (first -128) | "unavailable" (-128 again after signing in again)
        self.store_problem = ""
        self.store_problem_app = ""
        self._relogin_for_store: str | None = None
        self._relogged_for_store: set[str] = set()

    @property
    def running(self) -> bool:
        return self.queue.active

    def begin(self, items: Iterable[RestoreItem], space: DeviceSpace) -> SpacePlan:
        """Check space with fresh numbers; start only when it is not over."""

        chosen = [item for item in items if item.selectable]
        plan = plan_space(chosen, space)
        self.last_plan = plan
        if plan.blocked or self.queue.active:
            self.on_change()
            return plan
        self._batch = set()
        self._started = set()
        self.needs_signin = False
        self.queue.start(chosen)
        self._kick()
        self.on_change()
        return plan

    def _kick(self) -> None:
        entry = self.queue.current()
        if entry is None:
            return
        item = entry.item
        if item.key in self._started:
            return
        if item.action == ACTION_OFFLOADED:
            # One call for every offloaded app still waiting: QuickSession
            # restores them in order and reports each through appRestored.
            keys = [
                e.item.key
                for e in self.queue.entries
                if e.item.action == ACTION_OFFLOADED and e.state in (WAIT, CURRENT)
            ]
            self._batch = set(keys)
            self._started.update(keys)
            self.backend.restore_offloaded(keys)
        elif item.action == ACTION_STORE:
            self._started.add(item.key)
            self.backend.install_store(item.store_id)
        elif item.action == ACTION_IPA:
            self._started.add(item.key)
            self.backend.install_ipa(item.ipa_path)
        else:
            self.queue.settle(item.key, False, "Нечем вернуть")
            self._kick()

    # -- QuickSession signals ------------------------------------------------

    def on_progress(self, percent: int, text: str) -> None:
        self.queue.progress(percent, text)
        self.on_change()

    def on_app_restored(self, key: str) -> None:
        self._batch.discard(key)
        self.queue.settle(key, True)
        self._kick()
        self.on_change()

    def on_restore_settled(self, errors: str) -> None:
        """End of the offloaded batch: whatever did not report back failed."""

        if self.queue.entries and needs_signin(errors):
            self.interrupt_for_signin()
            return
        lines = [line for line in (errors or "").splitlines() if line.strip()]
        for key in list(self._batch):
            entry = self.queue._find(key)
            if entry is None or entry.state not in (WAIT, CURRENT):
                continue
            text = next((line.split(": ", 1)[-1] for line in lines if line.startswith(entry.item.name)), "")
            self.queue.settle(key, False, text or (lines[0] if lines else "Не получилось"))
        self._batch = set()
        self._kick()
        self.on_change()

    def on_install_settled(self, store_id: str, ok: bool, text: str) -> None:
        if not ok and self.queue.entries and is_store_mismatch(text):
            entry = self.queue._find(store_id) or self.queue.current()
            self.interrupt_for_store_mismatch(entry.item.label if entry else "")
            return
        if not ok and self.queue.entries and needs_signin(text):
            self.interrupt_for_signin()
            return
        self.queue.settle(store_id, ok, text)
        self._kick()
        self.on_change()

    def stop(self) -> None:
        self.queue.stop()
        self.on_change()

    def interrupt_for_signin(self) -> None:
        """Drop the run: no queue, no done screen, nothing kept to resume."""

        self.queue = RestoreQueue()
        self._batch = set()
        self._started = set()
        self.needs_signin = True
        self.on_change()

    def on_signed_in(self) -> None:
        """Back on the home screen; the user presses «Вернуть» again."""

        changed = self.needs_signin or bool(self.store_problem)
        if self._relogin_for_store is not None:
            if not self._relogin_for_store or self._relogin_for_store == self.account_email.casefold():
                self._relogged_for_store.add(self.account_email.casefold())
            self._relogin_for_store = None
        self.needs_signin = False
        self.store_problem = ""
        self.store_problem_app = ""
        if changed:
            self.on_change()

    def interrupt_for_store_mismatch(self, app: str = "") -> None:
        """-128: drop the run; nothing is retried by itself."""

        self.queue = RestoreQueue()
        self._batch = set()
        self._started = set()
        relogged = self.account_email.casefold() in self._relogged_for_store
        self.store_problem = "unavailable" if relogged else "mismatch"
        self.store_problem_app = app
        self.on_change()

    def store_relogin_requested(self) -> None:
        """«Войти заново» on the -128 screen: remember for whom (memory only)."""

        self._relogin_for_store = self.account_email.casefold()

    def dismiss_store_problem(self) -> None:
        """«На главный»."""

        self.store_problem = ""
        self.store_problem_app = ""
        self._relogin_for_store = None
        self.on_change()

    def observe_account(self, signed_in: bool, auth_phase: str = "", relogin: bool = False, email: str = "") -> None:
        """Follow QuickSession's account state.

        ``relogin`` (expired session) during a run drops it; a fresh sign-in
        (signed out → in, auth phase → ``in``, or relogin cleared) only clears
        the flag. Nothing is started from here.
        """

        before = self._account
        self._account = (bool(signed_in), auth_phase, bool(relogin))
        if email:
            self.account_email = email
        if relogin and self.queue.active:
            self.interrupt_for_signin()
        if before is None:
            return
        was_signed, was_phase, was_relogin = before
        fresh = (
            (signed_in and not was_signed)
            or (auth_phase == "in" and was_phase not in ("", "in"))
            or (was_relogin and not relogin and signed_in)
        )
        if fresh and not relogin:
            self.on_signed_in()
