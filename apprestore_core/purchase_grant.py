"""A one-shot permission to send one ``ipatool purchase``.

Only ``apprestore_core.license_gate`` can mint a ``PurchaseGrant`` (it holds
the private key object), and ``AppRestoreTools.purchase_license`` refuses to
run without a fresh grant for exactly that App Store ID. A grant is spent on
first use, so it cannot be replayed for a second purchase.

This is not a security boundary against hostile code in the same process;
it makes the gate impossible to skip by accident, and a static test checks
that ``purchase_license`` is called from the gate only.
"""

from __future__ import annotations

import threading

_MINT_KEY = object()


class PurchaseNotAllowed(RuntimeError):
    """``purchase_license`` was called without a valid grant from the gate."""


class PurchaseGrant:
    __slots__ = ("track_id", "_key", "_spent", "_lock")

    def __init__(self, track_id: str, *, _key: object) -> None:
        if _key is not _MINT_KEY:
            raise PurchaseNotAllowed("a PurchaseGrant is issued by the license gate only")
        self.track_id = str(track_id)
        self._key = _key
        self._spent = False
        self._lock = threading.Lock()

    def spend(self, track_id: str) -> None:
        with self._lock:
            if self._key is not _MINT_KEY:
                raise PurchaseNotAllowed("forged purchase grant")
            if self._spent:
                raise PurchaseNotAllowed("purchase grant already used")
            if str(track_id) != self.track_id:
                raise PurchaseNotAllowed("purchase grant is for another app")
            self._spent = True


def _mint(track_id: str) -> PurchaseGrant:
    """For ``license_gate`` only."""

    return PurchaseGrant(track_id, _key=_MINT_KEY)


def require_grant(grant: object, track_id: str) -> None:
    if not isinstance(grant, PurchaseGrant):
        raise PurchaseNotAllowed("ipatool purchase needs a grant from the license gate")
    grant.spend(track_id)
