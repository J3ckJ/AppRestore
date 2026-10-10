"""Local copy of the signed-in Apple ID's purchase list (``purchases-cache.json``).

The purchase history is personal data, so (Лена's rules):

* only ``track_id``, ``bundle_id``, ``name`` and ``purchase_date`` are kept:
  no email, guid, tokens, prices or versions;
* the account is identified by a SHA-256 of the normalised email with a
  random per-install salt (``salt``, 32 bytes from ``secrets``), never by
  the email itself. A missing or damaged salt file means a new salt, so any
  existing cache no longer matches and is deleted as foreign;
* entries are de-duplicated by ``track_id`` (first occurrence wins, the list
  stays newest first);
* ``~/.apprestore`` and its ``purchases`` folder are 0700 (tightened if an
  older install left them wider), the cache and the salt 0600. On Windows
  both live in the user profile next to the ipatool vault and rely on the
  profile ACL (see SECURITY.md);
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
import secrets
import stat
import tempfile
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

FORMAT_VERSION = 1
FILE_NAME = "purchases-cache.json"
SALT_NAME = "salt"
SALT_BYTES = 32
_KEY_PREFIX = b"apprestore-purchases-cache-v2:"


def app_dir() -> Path:
    return Path.home() / ".apprestore"


def cache_dir() -> Path:
    return app_dir() / "purchases"


def cache_path(root: Path | None = None) -> Path:
    return (root or cache_dir()) / FILE_NAME


def _private_dir(folder: Path) -> None:
    """mkdir 0700; tighten an existing folder that is wider (POSIX)."""

    folder.mkdir(mode=0o700, parents=True, exist_ok=True)
    if os.name == "nt":
        return
    mode = stat.S_IMODE(folder.stat().st_mode)
    if mode & 0o077:
        os.chmod(folder, mode & 0o700)


def _secure_dir(folder: Path) -> None:
    if folder == cache_dir():
        _private_dir(app_dir())
    _private_dir(folder)


def _write_private(path: Path, data: bytes, *, exclusive: bool = False) -> bool:
    """Atomic 0600 write: temp file in the same folder, fsync, then rename.

    ``exclusive``: never replace an existing file (hard link instead of
    replace); returns False when another process won the race.
    """

    folder = path.parent
    _secure_dir(folder)
    fd, tmp_name = tempfile.mkstemp(prefix=".purchases-", suffix=".tmp", dir=folder)  # 0600
    tmp = Path(tmp_name)
    try:
        with os.fdopen(fd, "wb") as handle:
            if os.name != "nt":
                os.fchmod(handle.fileno(), stat.S_IRUSR | stat.S_IWUSR)
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        if exclusive:
            try:
                os.link(tmp, path)
            except FileExistsError:
                return False
            except OSError:
                # No hard links (some Windows/network file systems).
                if path.exists():
                    return False
                os.replace(tmp, path)
                tmp = None  # type: ignore[assignment]
        else:
            os.replace(tmp, path)
            tmp = None  # type: ignore[assignment]
    finally:
        if tmp is not None:
            try:
                tmp.unlink()
            except OSError:
                pass
    if os.name != "nt":
        os.chmod(path, stat.S_IRUSR | stat.S_IWUSR)
    return True


def _read_salt(path: Path) -> bytes | None:
    try:
        data = path.read_bytes()
    except OSError:
        return None
    return data if len(data) == SALT_BYTES else None


def install_salt(root: Path | None = None) -> bytes:
    """The per-install random salt; created (atomically) when missing/damaged."""

    path = (root or cache_dir()) / SALT_NAME
    salt = _read_salt(path)
    if salt is not None:
        return salt
    damaged = path.exists()
    fresh = secrets.token_bytes(SALT_BYTES)
    if damaged:
        _write_private(path, fresh)  # replace the broken file
        # A cache keyed with the lost salt can no longer be matched.
        try:
            cache_path(root).unlink()
        except OSError:
            pass
        return fresh
    if _write_private(path, fresh, exclusive=True):
        try:
            cache_path(root).unlink()  # written under an older/unknown salt
        except OSError:
            pass
        return fresh
    return _read_salt(path) or install_salt(root)


def account_key(email: str, root: Path | None = None) -> str:
    """Key for one Apple ID: sha256(prefix + per-install salt + email)."""

    cleaned = (email or "").strip().lower()
    if not cleaned:
        return ""
    digest = hashlib.sha256(_KEY_PREFIX + install_salt(root) + cleaned.encode("utf-8"))
    return digest.hexdigest()


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


class PurchasesCache:
    """One file, one account at a time."""

    def __init__(self, root: Path | None = None) -> None:
        self._root = root

    @property
    def path(self) -> Path:
        return cache_path(self._root)

    def account_key(self, email: str) -> str:
        return account_key(email, self._root)

    def claim(self, email: str) -> None:
        """``email`` is now the open account: drop a cache of any other one."""

        key = self.account_key(email)
        if not key:
            self.delete()
            return
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            self.delete()
            return
        if not isinstance(payload, dict) or payload.get("account") != key:
            self.delete()

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
        _write_private(self.path, data)

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


def forget_purchases_cache(root: Path | None = None) -> None:
    """Sign-out from any front end: the cached purchase list goes away."""

    PurchasesCache(root).delete()
