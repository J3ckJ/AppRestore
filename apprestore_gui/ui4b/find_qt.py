"""The «Найти» sheet controller (QObject over :class:`ui4b.find.FindState`).

Order (02-picker §6b): the built-in list answers at once (no network); App
Store + purchases run in a worker; the Internet Archive runs only after them,
only if all of that is empty, only when «Искать в архиве» is on, in its own
thread with a spinner. A new query drops late answers of the old one.
"""

from __future__ import annotations

import threading
from collections.abc import Callable
from typing import Any

from PySide6.QtCore import Property, QObject, QTimer, Signal, Slot

from .find import (
    ARCHIVE_TIMEOUT_S, PLACEHOLDER, TITLE, FindState, Hit, hit_from, load_module, min_confidence, run_delisted,
)
from .search import SEARCH_HINT, search_store


def _thread(fn: Callable[[], None]) -> None:
    threading.Thread(target=fn, daemon=True, name="ui4b-find").start()


class Find4b(QObject):
    changed = Signal()
    _storeDone = Signal(int, object, object, object)  # gen, rows, offers, statuses
    _archiveDone = Signal(int, object, bool, object, object)  # gen, hits, down, statuses, offers

    def __init__(
        self,
        source: Any,
        *,
        archive_enabled: Callable[[], bool],
        install: Callable[[str, str], None],
        open_settings: Callable[[], None],
        component_missing: Callable[[], bool] = lambda: False,
        rejected: Callable[[], set[str]] = set,
        delisted: Callable[[str, bool], tuple[list[Any], bool]] | None = None,
        spawn: Callable[[Callable[[], None]], None] = _thread,
        module: Any | None = None,
        timeout_s: float = ARCHIVE_TIMEOUT_S,
    ) -> None:
        super().__init__()
        self.source = source
        self._archive_enabled = archive_enabled
        self._install = install
        self._open_settings = open_settings
        self._component_missing = component_missing
        self._rejected = rejected
        self._module = module if module is not None else (None if delisted else load_module())
        self.set_delisted(delisted or (lambda q, net: run_delisted(q, net, self._module)))
        #: no delisted_search vendored → no built-in list and no archive
        self._has_delisted = delisted is not None or self._module is not None
        self._spawn = spawn
        self._timeout_ms = int(timeout_s * 1000)
        self.state = FindState(min_confidence=min_confidence(self._module))
        self._open = False
        self._gen = 0
        self._storeDone.connect(self._on_store)
        self._archiveDone.connect(self._on_archive)

    def set_delisted(self, raw: Callable[[str, bool], tuple[list[Any], bool]]) -> None:
        """The search_delisted adapter (tests and screenshots pass a fake)."""

        def normalised(query: str, net: bool) -> tuple[list[Hit], bool]:
            hits, down = raw(query, net)
            out = [h if isinstance(h, Hit) else hit_from(h) for h in hits or []]
            return [h for h in out if h is not None], bool(down)

        self._delisted = normalised
        self._has_delisted = True

    # -- QML -----------------------------------------------------------------------

    @Property(bool, notify=changed)
    def open(self) -> bool:
        return self._open

    @Property(str, notify=changed)
    def query(self) -> str:
        return self.state.query

    @Property("QVariantMap", notify=changed)
    def view(self) -> dict[str, object]:
        v = self.state.view()
        v.update(title=TITLE, hint=SEARCH_HINT, placeholder=PLACEHOLDER, close="Закрыть")
        return v

    @Slot(str)
    def openWith(self, query: str) -> None:
        self._open = True
        self.search(query)

    @Slot()
    def close(self) -> None:
        self._open = False
        self._gen += 1  # late answers are dropped
        self.changed.emit()

    @Slot(str)
    def search(self, query: str) -> None:
        """QML calls this after a 250 ms pause in typing."""

        self._gen += 1
        gen = self._gen
        st = self.state
        st.reset(query)
        st.offline = not self.source.online
        st.archive_enabled = bool(self._archive_enabled())
        st.archive_available = self._has_delisted
        st.component_missing = bool(self._component_missing())
        st.rejected = set(self._rejected())
        st.owned = self.source.owned_store_ids()
        if not query.strip():
            st.store_done = True
            self.changed.emit()
            return
        st.builtin = [h for h in self._delisted(query, False)[0] if h.origin != "wayback"]  # instant
        self.changed.emit()
        source, offline, purchases = self.source, st.offline, self.source.purchase_rows()
        owned = set(st.owned or ())
        builtin_ids = [h.store_id for h in st.builtin]

        def work() -> None:
            if offline:  # purchases from the cache only, no App Store
                rows = search_store(query, purchases, itunes_search=lambda *a, **k: [],
                                    itunes_lookup=lambda *a, **k: None)
                self._storeDone.emit(gen, rows, {}, {})
                return
            statuses = source.store_statuses(builtin_ids) if builtin_ids else {}
            rows = source.search_store(query, purchases)
            ids = [str(r.get("storeId")) for r in rows] + builtin_ids
            offers = source.store_offers([i for i in dict.fromkeys(ids) if i not in owned])
            self._storeDone.emit(gen, rows, offers, statuses)

        self._spawn(work)

    @Slot(str, str)
    def install(self, store_id: str, name: str) -> None:
        if self.state.offline or not store_id:
            return
        self._open = False
        self._gen += 1
        self.changed.emit()
        self._install(store_id, name)

    @Slot()
    def openSettings(self) -> None:
        self._open_settings()

    # -- answers ---------------------------------------------------------------------

    def _on_store(self, gen: int, rows: object, offers: object, statuses: object) -> None:
        if gen != self._gen:
            return
        st = self.state
        st.store = list(rows or [])  # type: ignore[call-overload]
        st.offers.update(dict(offers or {}))  # type: ignore[call-overload]
        st.statuses.update(dict(statuses or {}))  # type: ignore[call-overload]
        st.store_done = st.offers_done = True
        if st.wants_archive():
            self._start_archive(gen)
        self.changed.emit()

    def _start_archive(self, gen: int) -> None:
        st = self.state
        st.archive_state, st.archive_asked = "busy", True
        query, source = st.query, self.source
        owned = set(st.owned or ())

        def work() -> None:
            hits, down = self._delisted(query, True)
            ids = [h.store_id for h in hits]
            statuses = source.store_statuses(ids) if ids else {}
            offers = source.store_offers([i for i in ids if i not in owned]) if ids else {}
            self._archiveDone.emit(gen, hits, bool(down), statuses, offers)

        QTimer.singleShot(self._timeout_ms, lambda: self._archive_timeout(gen))
        self._spawn(work)

    def _archive_timeout(self, gen: int) -> None:
        if gen == self._gen and self.state.archive_state == "busy":
            self.state.archive_state = "down"
            self.changed.emit()

    def _on_archive(self, gen: int, hits: object, down: bool, statuses: object, offers: object) -> None:
        st = self.state
        if gen != self._gen or st.archive_state != "busy":
            return  # a newer query, or the spinner already gave up
        st.archive = [h for h in (hits or []) if h.origin == "wayback"]  # type: ignore[union-attr]
        st.statuses.update(dict(statuses or {}))  # type: ignore[call-overload]
        st.offers.update(dict(offers or {}))  # type: ignore[call-overload]
        st.archive_state = "down" if down else "done"
        self.changed.emit()
