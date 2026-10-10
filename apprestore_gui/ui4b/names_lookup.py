"""Titles for nameless tiles via Макс's ``app_names.display_names`` (public iTunes
lookup, LEGAL §1.10; built-in list as its own offline fallback) — the step between
the purchases cache and «Приложение».

Chain (Евгений 10.10): device CFBundleDisplayName / CFBundleName / iTunesMetadata →
purchases cache → display_names (here: background thread, batched, the tiles on
screen first) → after ~3 s or nothing found → «Приложение». While a key is being
looked up its tile label is empty (space kept, no skeleton); a name that arrives
later is swapped in without animation.

Network: only Apple's public lookup through app_names — never the Internet Archive (the
Wayback path of delisted_search is not on this path). Never called offline. The
account country only after sign-in; the iPhone Locale as is (lockdown.get_locale()).
"""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable, Iterable, Mapping

log = logging.getLogger(__name__)

#: «after ~3 s … → «Приложение»» (a later answer is still swapped in)
DEADLINE_S = 3.0
#: the phone mock shows up to 16 tiles: those go in the first batch
VISIBLE_FIRST = 16
#: app_names refuses more than 2000 keys per call
MAX_KEYS = 2000

DisplayNames = Callable[..., Mapping[str, str]]


def vendored_display_names() -> DisplayNames | None:
    try:
        from apprestore_core import app_names
    except Exception:  # noqa: BLE001 - cannot import: the chain skips this step
        return None
    return getattr(app_names, "display_names", None)


def device_locale(udid: str, *, timeout: float = 10) -> str | None:
    """iPhone ``Locale`` (com.apple.international) as is, e.g. "en_RU"; None on any problem."""

    if not udid:
        return None
    try:
        import asyncio

        from pymobiledevice3.lockdown import create_using_usbmux

        async def _run() -> str:
            async with await create_using_usbmux(serial=udid, connection_type="USB") as lockdown:
                return await lockdown.get_locale()

        value = asyncio.run(asyncio.wait_for(_run(), timeout=timeout))
    except Exception:  # noqa: BLE001 - no locale: app_names goes straight to US
        return None
    return value or None


class NameLookup:
    """Keys (store id or bundle id strings) → titles, looked up once per key."""

    def __init__(self, display_names: DisplayNames | None = None, *,
                 spawn: Callable[[Callable[[], None]], object] | None = None,
                 notify: Callable[[], None] = lambda: None,
                 clock: Callable[[], float] = time.monotonic,
                 deadline_s: float = DEADLINE_S) -> None:
        self._display_names = display_names
        self._spawn = spawn or (lambda fn: threading.Thread(target=fn, name="ui4b-names", daemon=True).start())
        self._notify = notify
        self._clock = clock
        self._deadline = deadline_s
        self._lock = threading.Lock()
        self.found: dict[str, str] = {}
        self._asked: dict[str, float] = {}
        self._finished: set[str] = set()
        self._generation = 0

    def name(self, *keys: str) -> str:
        with self._lock:
            for key in keys:
                if key and self.found.get(key):
                    return self.found[key]
        return ""

    def pending(self, *keys: str) -> bool:
        """Still looking for any of these keys, within the ~3 s window."""

        now = self._clock()
        with self._lock:
            return any(key and key in self._asked and key not in self._finished
                       and now - self._asked[key] < self._deadline for key in keys)

    def request(self, ids: Iterable[str], bundle_ids: Iterable[str], *,
                account_country: Callable[[], str | None] = lambda: None,
                locale: Callable[[], str | None] = lambda: None) -> bool:
        """Ask for keys never asked before, in the given order (visible tiles first).
        True when a lookup was started (the caller re-reads after the deadline)."""

        if self._display_names is None:
            return False
        now = self._clock()
        with self._lock:
            new_ids = [k for k in dict.fromkeys(str(i) for i in ids) if k.isdigit() and k not in self._asked]
            new_bids = [k for k in dict.fromkeys(str(b) for b in bundle_ids)
                        if k and not k.isdigit() and k not in self._asked and k not in new_ids]
            for key in (*new_ids, *new_bids):
                self._asked[key] = now
            generation = self._generation
        if not new_ids and not new_bids:
            return False
        lookup = self._display_names

        def batches() -> list[tuple[list[str], list[str]]]:
            first_ids = new_ids[:VISIBLE_FIRST]
            first_bids = new_bids[:max(0, VISIBLE_FIRST - len(first_ids))]
            out = [(first_ids, first_bids)]
            rest_ids, rest_bids = new_ids[len(first_ids):], new_bids[len(first_bids):]
            while rest_ids or rest_bids:
                take_ids = rest_ids[:MAX_KEYS]
                take_bids = rest_bids[:MAX_KEYS - len(take_ids)]
                rest_ids, rest_bids = rest_ids[len(take_ids):], rest_bids[len(take_bids):]
                out.append((take_ids, take_bids))
            return out

        def work() -> None:
            try:
                cc = account_country()
            except Exception:  # noqa: BLE001
                cc = None
            try:
                loc = locale()
            except Exception:  # noqa: BLE001
                loc = None
            for batch_ids, batch_bids in batches():
                if not batch_ids and not batch_bids:
                    continue
                try:
                    out = dict(lookup(batch_ids, bundle_ids=batch_bids, account_country=cc or None,
                                      device_locale=loc or None) or {})
                except Exception:  # noqa: BLE001 - app_names does not raise; be safe anyway
                    log.debug("display_names failed", exc_info=True)
                    out = {}
                with self._lock:
                    if generation != self._generation:
                        return  # account changed meanwhile: drop
                    for key, value in out.items():
                        text = " ".join(str(value or "").split())
                        if text:
                            self.found[str(key)] = text
                    self._finished.update(batch_ids)
                    self._finished.update(batch_bids)
                self._notify()

        self._spawn(work)
        return True

    def clear(self) -> None:
        """«Выйти» / another Apple ID: forget everything (and app_names' own cache)."""

        with self._lock:
            self._generation += 1
            self.found.clear()
            self._asked.clear()
            self._finished.clear()
        try:
            from apprestore_core import app_names

            app_names.default_resolver().clear_cache()
        except Exception:  # noqa: BLE001
            pass
