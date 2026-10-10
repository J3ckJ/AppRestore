"""Runs the restore queue through the existing services, one app at a time.

Order of a run: free space is checked on the device first (blocked when the
known sizes alone do not fit), then every chosen app goes through the path the
old window uses:

* offloaded → ``QuickSession.restore`` (iOS redownload, IPA fallback);
* removed from the App Store → ``QuickSession.installStore`` → license gate
  (``apprestore_core.license_gate.run_with_free_license``: read-only attempt,
  price 0 in the account's country, 5/day + 15 total, journal);
* local IPA → ``QuickSession.installSaved``.

No Qt here: :class:`Backend` is whatever performs those calls.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from typing import Protocol

from apprestore_gui.ui4b.catalog import ACTION_IPA, ACTION_OFFLOADED, ACTION_STORE, RestoreItem
from apprestore_gui.ui4b.queue import CURRENT, WAIT, RestoreQueue
from apprestore_gui.ui4b.space import DeviceSpace, SpacePlan, plan_space


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
        self.queue.settle(store_id, ok, text)
        self._kick()
        self.on_change()

    def stop(self) -> None:
        self.queue.stop()
        self.on_change()
