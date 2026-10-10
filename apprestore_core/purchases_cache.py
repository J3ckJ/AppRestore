"""Local copy of the signed-in Apple ID's purchase list (``purchases-cache.json``).

The purchase history is personal data, so (Лена's rules):

* only ``track_id``, ``bundle_id``, ``name`` and ``purchase_date`` are kept:
  no email, guid, tokens, prices or versions;
* the account is identified by a salted SHA-256 of the normalised email,
  never by the email itself;
* entries are de-duplicated by ``track_id`` (first occurrence wins, the list
  stays newest first);
* the folder is 0700 and the file 0600 (on Windows it lives in the user
  profile next to the ipatool vault, where the ACL already limits access);
* writes are atomic: a 0600 temporary file in the same folder, fsync, then
  ``os.replace``;
* the cache is deleted on sign-out and when another account is opened;
* a damaged or foreign file is ignored (treated as no cache) and replaced on
  the next successful refresh.
"""

from __future__ import annotations

import hashlib
import json
import os
import stat
import tempfile
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

FORMAT_VERSION = 1
FILE_NAME = "purchases-cache.json"
_SALT = "apprestore-purchases-cache-v1:"
_FIELDS = ("track_id", "bundle_id", "name", "purchase_date")


def cache_dir() -> Path:
    return Path.home() / ".apprestore" / "purchases"


def cache_path(root: Path | None = None) -> Path:
    return (root or cache_dir()) / FILE_NAME


def account_key(email: str) -> str:
    """Stable, non-reversible-at-a-glance key for one Apple ID."""

    cleaned = (email or "").strip().lower()
    if not cleaned:
        return ""
    return hashlib.sha256((_SALT + cleaned).encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class CachedPurchase:
    track_id: int
    bundle_id: str
    name: str
    purchase_date: str = ""  # ISO 8601 or ""

    def as_row(self) -> dict[str, object]:
        return {
            "track_id": self.track_id,
            "bundle_id": self.bundle_id,
            "name": self.name,
            "purchase_date": self.purchase_date,
        }

    @classmethod
    def from_purchase(cls, purchase: object) -> "CachedPurchase":
        """From ``ipatool_api.Purchase`` (or anything with the same attributes)."""

        date = getattr(purchase, "purchase_date", None)
        if isinstance(date, datetime):
            text = date.astimezone(timezone.utc).isoformat() if date.tzinfo else date.isoformat()
        else:
            text = str(date or "")
        return cls(
            track_id=int(getattr(purchase, "track_id")),
            bundle_id=str(getattr(purchase, "bundle_id", "") or ""),
            name=str(getattr(purchase, "name", "") or ""),
            purchase_date=text,
        )

    @classmethod
    def from_row(cls, row: Mapping[str, object]) -> "CachedPurchase":
        raw_id = row.get("track_id")
        if isinstance(raw_id, bool) or not isinstance(raw_id, int) or raw_id <= 0:
            raise ValueError("bad track_id")
        values = {}
        for key in ("bundle_id", "name", "purchase_date"):
            value = row.get(key, "")
            if not isinstance(value, str):
                raise ValueError(f"bad {key}")
            values[key] = value
        return cls(track_id=raw_id, **values)


def dedupe(items: Iterable[CachedPurchase]) -> list[CachedPurchase]:
    seen: set[int] = set()
    out: list[CachedPurchase] = []
    for item in items:
        if item.track_id in seen:
            continue
        seen.add(item.track_id)
        out.append(item)
    return out


@dataclass(frozen=True)
class CacheSnapshot:
    items: tuple[CachedPurchase, ...]
    total: int | None
    complete: bool
    updated: str


def _secure_dir(folder: Path) -> None:
    folder.mkdir(parents=True, exist_ok=True)
    if os.name != "nt":
        os.chmod(folder, stat.S_IRWXU)  # 0700


class PurchasesCache:
    """One file, one account at a time."""

    def __init__(self, root: Path | None = None) -> None:
        self._root = root

    @property
    def path(self) -> Path:
        return cache_path(self._root)

    def load(self, account: str) -> CacheSnapshot | None:
        """The cached list for ``account`` (see :func:`account_key`), else None.

        A file written for another account is deleted right away. A damaged
        file is left in place (it is overwritten by the next save) and ignored.
        """

        if not account:
            return None
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return None
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            return None
        if not isinstance(payload, dict) or payload.get("version") != FORMAT_VERSION:
            return None
        owner = payload.get("account")
        if not isinstance(owner, str):
            return None
        if owner != account:
            self.delete()
            return None
        rows = payload.get("items")
        if not isinstance(rows, list):
            return None
        try:
            items = dedupe(CachedPurchase.from_row(row) for row in rows if isinstance(row, dict))
        except (TypeError, ValueError):
            return None
        total = payload.get("total")
        if isinstance(total, bool) or not isinstance(total, int) or total < 0:
            total = None
        return CacheSnapshot(
            items=tuple(items),
            total=total,
            complete=payload.get("complete") is True,
            updated=str(payload.get("updated") or ""),
        )

    def save(
        self,
        account: str,
        items: Iterable[CachedPurchase],
        *,
        total: int | None = None,
        complete: bool = True,
    ) -> None:
        if not account:
            raise ValueError("account key is required")
        rows = [item.as_row() for item in dedupe(items)]
        payload = {
            "version": FORMAT_VERSION,
            "account": account,
            "updated": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
            "complete": bool(complete),
            "total": total if isinstance(total, int) and not isinstance(total, bool) else None,
            "items": rows,
        }
        data = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        folder = self.path.parent
        _secure_dir(folder)
        # mkstemp creates the file with 0600 on POSIX.
        fd, tmp_name = tempfile.mkstemp(prefix=".purchases-", suffix=".tmp", dir=folder)
        tmp = Path(tmp_name)
        try:
            with os.fdopen(fd, "wb") as handle:
                if os.name != "nt":
                    os.fchmod(handle.fileno(), stat.S_IRUSR | stat.S_IWUSR)
                handle.write(data)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(tmp, self.path)
        except BaseException:
            try:
                tmp.unlink()
            except OSError:
                pass
            raise
        if os.name != "nt":
            os.chmod(self.path, stat.S_IRUSR | stat.S_IWUSR)

    def delete(self) -> None:
        try:
            self.path.unlink()
        except FileNotFoundError:
            pass
        except OSError:
            # Could not remove (e.g. locked on Windows): at least empty it.
            try:
                self.path.write_bytes(b"")
            except OSError:
                pass
        folder = self.path.parent
        if folder.is_dir():
            for leftover in folder.glob(".purchases-*.tmp"):
                try:
                    leftover.unlink()
                except OSError:
                    pass
