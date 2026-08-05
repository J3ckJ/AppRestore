"""Persistent metadata cache for locally discovered IPA files.

The index is deliberately a cache, not a security boundary.  Callers still
verify an IPA before installation.  A cached entry is reusable only while the
file's size and nanosecond modification time match the values observed during
the scan that produced the metadata.
"""

from __future__ import annotations

import os
import sqlite3
import stat as stat_module
import threading
import time
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import TypeVar

from .models import IpaMetadata


SCHEMA_VERSION = 1
DEFAULT_BUSY_TIMEOUT_MS = 5_000
DEFAULT_RETRY_ATTEMPTS = 3
_MAX_SQLITE_INTEGER = (1 << 63) - 1
_REPARSE_POINT_ATTRIBUTE = 0x400

_T = TypeVar("_T")
_Pathish = str | os.PathLike[str]


class IpaIndexError(RuntimeError):
    """Base exception raised by the IPA metadata index."""


class IpaIndexBusyError(IpaIndexError):
    """Raised when another process holds the database lock for too long."""


class IpaIndexSchemaError(IpaIndexError):
    """Raised when the database schema is newer or otherwise incompatible."""


class IpaIndexClosedError(IpaIndexError):
    """Raised when an operation is attempted after :meth:`IpaIndex.close`."""


def normalize_ipa_path(path: _Pathish) -> str:
    """Return the platform-normalized absolute key used by the database."""

    raw = os.fspath(path)
    if not isinstance(raw, str):
        raise TypeError("IPA paths must be text paths")
    if not raw or "\x00" in raw:
        raise ValueError("IPA path must be non-empty and cannot contain NUL")
    expanded = os.path.expanduser(raw)
    absolute = os.path.abspath(expanded)
    canonical = os.path.realpath(absolute)
    return os.path.normcase(os.path.normpath(canonical))


def _bounded_integer(value: object, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"{field} must be an integer")
    if value < 0 or value > _MAX_SQLITE_INTEGER:
        raise ValueError(f"{field} is outside SQLite's integer range")
    return value


def _database_integer(value: object, field: str) -> int:
    try:
        return _bounded_integer(value, field)
    except (TypeError, ValueError) as error:
        raise IpaIndexError(
            f"IPA index contains an invalid {field} value"
        ) from error


def _optional_text(
    value: object,
    field: str,
    *,
    maximum: int = 4_096,
) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise TypeError(f"{field} must be text or None")
    normalized = value.strip()
    if not normalized:
        return None
    if "\x00" in normalized:
        raise ValueError(f"{field} cannot contain NUL")
    if len(normalized) > maximum:
        raise ValueError(f"{field} is unexpectedly long")
    return normalized


@dataclass(frozen=True, slots=True)
class IpaIndexRecord:
    """Metadata tied to one exact on-disk file signature."""

    path: Path
    size: int
    mtime_ns: int
    bundle_id: str | None = None
    name: str | None = None
    version: str | None = None
    sha256: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "path", Path(normalize_ipa_path(self.path)))
        object.__setattr__(self, "size", _bounded_integer(self.size, "size"))
        object.__setattr__(
            self,
            "mtime_ns",
            _bounded_integer(self.mtime_ns, "mtime_ns"),
        )
        object.__setattr__(
            self,
            "bundle_id",
            _optional_text(self.bundle_id, "bundle_id", maximum=512),
        )
        object.__setattr__(self, "name", _optional_text(self.name, "name"))
        object.__setattr__(
            self,
            "version",
            _optional_text(self.version, "version", maximum=1_024),
        )
        digest = _optional_text(self.sha256, "sha256", maximum=64)
        if digest is not None:
            digest = digest.lower()
            if len(digest) != 64 or any(
                character not in "0123456789abcdef" for character in digest
            ):
                raise ValueError("sha256 must contain 64 hexadecimal characters")
        object.__setattr__(self, "sha256", digest)

    @classmethod
    def from_metadata(
        cls,
        metadata: IpaMetadata,
        *,
        mtime_ns: int,
        sha256: str | None = None,
    ) -> IpaIndexRecord:
        """Build a record from metadata already read by the IPA scanner."""

        return cls(
            path=metadata.path,
            size=metadata.size,
            mtime_ns=mtime_ns,
            bundle_id=metadata.bundle_id,
            name=metadata.name,
            version=metadata.version,
            sha256=sha256,
        )

    def to_metadata(self) -> IpaMetadata | None:
        """Return scanner metadata, or ``None`` for an incomplete record."""

        if self.bundle_id is None:
            return None
        return IpaMetadata(
            path=self.path,
            bundle_id=self.bundle_id,
            name=self.name or self.path.stem,
            version=self.version or "?",
            size=self.size,
        )


@dataclass(frozen=True, slots=True)
class IpaIndexUpdate:
    upserted: int
    removed: int = 0


_UPSERT_SQL = """
INSERT INTO ipa_entries (
    path,
    size,
    mtime_ns,
    bundle_id,
    name,
    version,
    sha256,
    indexed_at_ns
) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
ON CONFLICT(path) DO UPDATE SET
    size = excluded.size,
    mtime_ns = excluded.mtime_ns,
    bundle_id = excluded.bundle_id,
    name = excluded.name,
    version = excluded.version,
    sha256 = excluded.sha256,
    indexed_at_ns = excluded.indexed_at_ns
"""

_SELECT_COLUMNS = (
    "path, size, mtime_ns, bundle_id, name, version, sha256"
)


class IpaIndex:
    """Small process-safe SQLite index for IPA metadata.

    A fresh SQLite connection is used for every operation.  That keeps one
    :class:`IpaIndex` safe to call from multiple threads and lets independent
    AppRestore processes share the same WAL-backed database.
    """

    def __init__(
        self,
        database: _Pathish,
        *,
        busy_timeout_ms: int = DEFAULT_BUSY_TIMEOUT_MS,
        retry_attempts: int = DEFAULT_RETRY_ATTEMPTS,
    ) -> None:
        self.path = Path(normalize_ipa_path(database))
        self.busy_timeout_ms = _bounded_integer(
            busy_timeout_ms,
            "busy_timeout_ms",
        )
        self.retry_attempts = _bounded_integer(
            retry_attempts,
            "retry_attempts",
        )
        if self.busy_timeout_ms <= 0:
            raise ValueError("busy_timeout_ms must be positive")
        if self.retry_attempts > 20:
            raise ValueError("retry_attempts is unexpectedly large")
        self._state_lock = threading.Lock()
        self._closed = False

        self.path.parent.mkdir(parents=True, exist_ok=True)
        if self.path.is_symlink():
            raise IpaIndexError(f"refusing to open an index symlink: {self.path}")
        self._initialize()

    def __enter__(self) -> IpaIndex:
        self._ensure_open()
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def close(self) -> None:
        """Reject future operations (connections are already operation-scoped)."""

        with self._state_lock:
            self._closed = True

    def _ensure_open(self) -> None:
        with self._state_lock:
            if self._closed:
                raise IpaIndexClosedError("IPA index is closed")

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(
            self.path,
            timeout=self.busy_timeout_ms / 1_000,
            isolation_level=None,
        )
        connection.row_factory = sqlite3.Row
        connection.execute(f"PRAGMA busy_timeout = {self.busy_timeout_ms}")
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA synchronous = NORMAL")
        return connection

    @staticmethod
    def _is_busy(error: sqlite3.OperationalError) -> bool:
        busy_codes = {
            getattr(sqlite3, "SQLITE_BUSY", 5),
            getattr(sqlite3, "SQLITE_LOCKED", 6),
        }
        if getattr(error, "sqlite_errorcode", None) in busy_codes:
            return True
        message = str(error).casefold()
        return "locked" in message or "busy" in message

    def _retry_delay(self, attempt: int) -> None:
        time.sleep(min(0.025 * (2**attempt), 0.4))

    def _run_read(self, operation: Callable[[sqlite3.Connection], _T]) -> _T:
        self._ensure_open()
        for attempt in range(self.retry_attempts + 1):
            connection: sqlite3.Connection | None = None
            try:
                connection = self._connect()
                return operation(connection)
            except sqlite3.OperationalError as error:
                if not self._is_busy(error):
                    raise IpaIndexError("could not read the IPA index") from error
                if attempt == self.retry_attempts:
                    raise IpaIndexBusyError(
                        "timed out waiting to read the IPA index"
                    ) from error
                self._retry_delay(attempt)
            except sqlite3.DatabaseError as error:
                raise IpaIndexError("could not read the IPA index") from error
            finally:
                if connection is not None:
                    connection.close()
        raise AssertionError("unreachable")

    def _run_write(self, operation: Callable[[sqlite3.Connection], _T]) -> _T:
        self._ensure_open()
        for attempt in range(self.retry_attempts + 1):
            connection: sqlite3.Connection | None = None
            try:
                connection = self._connect()
                connection.execute("BEGIN IMMEDIATE")
                result = operation(connection)
                connection.commit()
                return result
            except sqlite3.OperationalError as error:
                if not self._is_busy(error):
                    raise IpaIndexError("could not update the IPA index") from error
                if attempt == self.retry_attempts:
                    raise IpaIndexBusyError(
                        "timed out waiting to update the IPA index"
                    ) from error
                self._retry_delay(attempt)
            except sqlite3.DatabaseError as error:
                raise IpaIndexError("could not update the IPA index") from error
            finally:
                if connection is not None:
                    if connection.in_transaction:
                        connection.rollback()
                    connection.close()
        raise AssertionError("unreachable")

    def _initialize(self) -> None:
        def initialize(connection: sqlite3.Connection) -> None:
            mode = connection.execute("PRAGMA journal_mode = WAL").fetchone()[0]
            if str(mode).casefold() != "wal":
                raise IpaIndexError("SQLite could not enable WAL mode")

            connection.execute("BEGIN IMMEDIATE")
            current = int(connection.execute("PRAGMA user_version").fetchone()[0])
            if current not in {0, SCHEMA_VERSION}:
                raise IpaIndexSchemaError(
                    f"unsupported IPA index schema {current}; "
                    f"expected {SCHEMA_VERSION}"
                )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS ipa_entries (
                    path TEXT NOT NULL PRIMARY KEY,
                    size INTEGER NOT NULL CHECK (size >= 0),
                    mtime_ns INTEGER NOT NULL CHECK (mtime_ns >= 0),
                    bundle_id TEXT,
                    name TEXT,
                    version TEXT,
                    sha256 TEXT CHECK (
                        sha256 IS NULL OR (
                            length(sha256) = 64
                            AND sha256 NOT GLOB '*[^0-9a-f]*'
                        )
                    ),
                    indexed_at_ns INTEGER NOT NULL CHECK (indexed_at_ns >= 0)
                ) WITHOUT ROWID
                """
            )
            connection.execute(
                """
                CREATE INDEX IF NOT EXISTS ipa_entries_bundle_mtime
                ON ipa_entries (bundle_id, mtime_ns DESC, path)
                """
            )
            columns = {
                str(row[1])
                for row in connection.execute("PRAGMA table_info(ipa_entries)")
            }
            expected = {
                "path",
                "size",
                "mtime_ns",
                "bundle_id",
                "name",
                "version",
                "sha256",
                "indexed_at_ns",
            }
            if columns != expected:
                raise IpaIndexSchemaError(
                    f"incompatible IPA index columns: {sorted(columns)}"
                )
            if current == 0:
                connection.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
            connection.commit()

        self._ensure_open()
        for attempt in range(self.retry_attempts + 1):
            connection: sqlite3.Connection | None = None
            try:
                connection = self._connect()
                initialize(connection)
                break
            except sqlite3.OperationalError as error:
                if not self._is_busy(error):
                    raise IpaIndexError("could not initialize the IPA index") from error
                if attempt == self.retry_attempts:
                    raise IpaIndexBusyError(
                        "timed out initializing the IPA index"
                    ) from error
                self._retry_delay(attempt)
            except sqlite3.DatabaseError as error:
                raise IpaIndexError("could not initialize the IPA index") from error
            finally:
                if connection is not None:
                    if connection.in_transaction:
                        connection.rollback()
                    connection.close()
        if os.name != "nt":
            try:
                os.chmod(self.path, stat_module.S_IRUSR | stat_module.S_IWUSR)
            except OSError:
                pass

    @staticmethod
    def _prepare_records(
        records: Iterable[IpaIndexRecord],
    ) -> list[IpaIndexRecord]:
        unique: dict[str, IpaIndexRecord] = {}
        for record in records:
            if not isinstance(record, IpaIndexRecord):
                raise TypeError("IPA index accepts only IpaIndexRecord values")
            key = str(record.path)
            previous = unique.get(key)
            if previous is not None and previous != record:
                raise ValueError(f"conflicting metadata for indexed path: {key}")
            unique[key] = record
        return list(unique.values())

    @staticmethod
    def _values(record: IpaIndexRecord, indexed_at_ns: int) -> tuple[object, ...]:
        return (
            str(record.path),
            record.size,
            record.mtime_ns,
            record.bundle_id,
            record.name,
            record.version,
            record.sha256,
            indexed_at_ns,
        )

    @staticmethod
    def _next_indexed_at_ns(connection: sqlite3.Connection) -> int:
        latest = _database_integer(
            connection.execute(
                "SELECT COALESCE(MAX(indexed_at_ns), 0) FROM ipa_entries"
            ).fetchone()[0],
            "indexed_at_ns",
        )
        if latest >= _MAX_SQLITE_INTEGER:
            raise IpaIndexError("IPA index write watermark is exhausted")
        return max(_bounded_integer(time.time_ns(), "time_ns"), latest + 1)

    @staticmethod
    def _row_to_record(row: sqlite3.Row) -> IpaIndexRecord:
        try:
            return IpaIndexRecord(
                path=Path(str(row["path"])),
                size=_database_integer(row["size"], "size"),
                mtime_ns=_database_integer(row["mtime_ns"], "mtime_ns"),
                bundle_id=row["bundle_id"],
                name=row["name"],
                version=row["version"],
                sha256=row["sha256"],
            )
        except (IpaIndexError, OverflowError, TypeError, ValueError) as error:
            raise IpaIndexError("IPA index contains an invalid record") from error

    def upsert(self, record: IpaIndexRecord) -> IpaIndexUpdate:
        return self.upsert_many((record,))

    def upsert_many(
        self,
        records: Iterable[IpaIndexRecord],
    ) -> IpaIndexUpdate:
        """Insert or replace records in one transaction."""

        self._ensure_open()
        prepared = self._prepare_records(records)
        if not prepared:
            return IpaIndexUpdate(upserted=0)

        def write(connection: sqlite3.Connection) -> IpaIndexUpdate:
            indexed_at_ns = self._next_indexed_at_ns(connection)
            connection.executemany(
                _UPSERT_SQL,
                (self._values(record, indexed_at_ns) for record in prepared),
            )
            return IpaIndexUpdate(upserted=len(prepared))

        return self._run_write(write)

    def snapshot_watermark(self) -> int:
        """Return the newest committed row generation before a filesystem scan."""

        return self._run_read(
            lambda connection: _database_integer(
                connection.execute(
                    "SELECT COALESCE(MAX(indexed_at_ns), 0) FROM ipa_entries"
                ).fetchone()[0],
                "indexed_at_ns",
            )
        )

    def _select_all(self) -> list[IpaIndexRecord]:
        def read(connection: sqlite3.Connection) -> list[IpaIndexRecord]:
            rows = connection.execute(
                f"SELECT {_SELECT_COLUMNS} "
                "FROM ipa_entries ORDER BY mtime_ns DESC, path"
            ).fetchall()
            return [self._row_to_record(row) for row in rows]

        return self._run_read(read)

    @staticmethod
    def _signature(
        path: Path,
        stat_result: os.stat_result | None = None,
    ) -> tuple[int, int] | None:
        try:
            metadata = stat_result if stat_result is not None else path.lstat()
            mode = int(metadata.st_mode)
            attributes = int(getattr(metadata, "st_file_attributes", 0))
            if not stat_module.S_ISREG(mode):
                return None
            if attributes & _REPARSE_POINT_ATTRIBUTE:
                return None
            return int(metadata.st_size), int(metadata.st_mtime_ns)
        except (AttributeError, OSError, TypeError, ValueError):
            return None

    def _delete_exact(self, records: Sequence[IpaIndexRecord]) -> int:
        if not records:
            return 0

        def remove(connection: sqlite3.Connection) -> int:
            removed = 0
            for record in records:
                cursor = connection.execute(
                    """
                    DELETE FROM ipa_entries
                    WHERE path = ? AND size = ? AND mtime_ns = ?
                    """,
                    (str(record.path), record.size, record.mtime_ns),
                )
                removed += max(cursor.rowcount, 0)
            return removed

        return self._run_write(remove)

    def get(
        self,
        path: _Pathish,
        *,
        stat_result: os.stat_result | None = None,
    ) -> IpaIndexRecord | None:
        """Return an entry only if its current file signature still matches.

        A scanner that already called ``lstat`` can pass that result to avoid a
        second filesystem lookup.  The supplied result must belong to ``path``.
        """

        key = normalize_ipa_path(path)

        def read(connection: sqlite3.Connection) -> IpaIndexRecord | None:
            row = connection.execute(
                f"SELECT {_SELECT_COLUMNS} FROM ipa_entries WHERE path = ?",
                (key,),
            ).fetchone()
            return self._row_to_record(row) if row is not None else None

        record = self._run_read(read)
        if record is None:
            return None
        signature = self._signature(record.path, stat_result)
        if signature == (record.size, record.mtime_ns):
            return record
        self._delete_exact((record,))
        return None

    def find_bundle(
        self,
        bundle_id: str,
        *,
        existing_only: bool = True,
    ) -> list[IpaIndexRecord]:
        """Return newest-first exact bundle matches."""

        normalized = _optional_text(bundle_id, "bundle_id", maximum=512)
        if normalized is None:
            raise ValueError("bundle_id cannot be empty")

        def read(connection: sqlite3.Connection) -> list[IpaIndexRecord]:
            rows = connection.execute(
                f"SELECT {_SELECT_COLUMNS} FROM ipa_entries "
                "WHERE bundle_id = ? ORDER BY mtime_ns DESC, path",
                (normalized,),
            ).fetchall()
            return [self._row_to_record(row) for row in rows]

        records = self._run_read(read)
        if not existing_only:
            return records
        valid: list[IpaIndexRecord] = []
        stale: list[IpaIndexRecord] = []
        for record in records:
            if self._signature(record.path) == (record.size, record.mtime_ns):
                valid.append(record)
            else:
                stale.append(record)
        self._delete_exact(stale)
        return valid

    def records(self, *, existing_only: bool = True) -> list[IpaIndexRecord]:
        """Return all records, optionally pruning invalidated paths."""

        records = self._select_all()
        if not existing_only:
            return records
        valid: list[IpaIndexRecord] = []
        stale: list[IpaIndexRecord] = []
        for record in records:
            if self._signature(record.path) == (record.size, record.mtime_ns):
                valid.append(record)
            else:
                stale.append(record)
        self._delete_exact(stale)
        return valid

    def prune_missing(self, paths: Iterable[_Pathish] | None = None) -> int:
        """Remove indexed files that no longer exist as plain files.

        ``paths=None`` checks the whole index.  Passing paths limits pruning to
        those exact keys; it never removes unmentioned records.
        """

        selected = None
        if paths is not None:
            selected = {normalize_ipa_path(path) for path in paths}
        missing = [
            record
            for record in self._select_all()
            if (selected is None or str(record.path) in selected)
            and self._signature(record.path) is None
        ]
        return self._delete_exact(missing)

    @staticmethod
    def _within_roots(path: str, roots: Sequence[str]) -> bool:
        for root in roots:
            try:
                if os.path.commonpath((path, root)) == root:
                    return True
            except ValueError:
                continue
        return False

    def replace_snapshot(
        self,
        records: Iterable[IpaIndexRecord],
        *,
        roots: Iterable[_Pathish] | None = (),
        scan_watermark: int | None = None,
    ) -> IpaIndexUpdate:
        """Atomically merge a completed scan and remove stale snapshot rows.

        With explicit ``roots``, only old records below those scan roots are
        removed.  An empty iterable performs a safe upsert-only merge.
        ``roots=None`` explicitly replaces the entire index.
        Rows written after ``scan_watermark`` are preserved because the scan
        could not have observed them.  Call :meth:`snapshot_watermark` before
        walking the filesystem and pass the returned value here.
        """

        if scan_watermark is None:
            scan_watermark = self.snapshot_watermark()
        else:
            scan_watermark = _bounded_integer(scan_watermark, "scan_watermark")
        prepared = self._prepare_records(records)
        incoming = {str(record.path): record for record in prepared}
        normalized_roots = (
            None
            if roots is None
            else tuple(dict.fromkeys(normalize_ipa_path(root) for root in roots))
        )
        def replace(connection: sqlite3.Connection) -> IpaIndexUpdate:
            protected_paths = {
                str(row[0])
                for row in connection.execute(
                    "SELECT path FROM ipa_entries WHERE indexed_at_ns > ?",
                    (scan_watermark,),
                )
            }
            old_paths = [
                str(row[0])
                for row in connection.execute(
                    "SELECT path FROM ipa_entries WHERE indexed_at_ns <= ?",
                    (scan_watermark,),
                )
            ]
            if normalized_roots is None:
                stale = [path for path in old_paths if path not in incoming]
            elif not normalized_roots:
                stale = []
            else:
                stale = [
                    path
                    for path in old_paths
                    if path not in incoming
                    and self._within_roots(path, normalized_roots)
                ]
            removed = 0
            for path in stale:
                cursor = connection.execute(
                    "DELETE FROM ipa_entries WHERE path = ?",
                    (path,),
                )
                removed += max(cursor.rowcount, 0)
            eligible = [
                record
                for record in prepared
                if str(record.path) not in protected_paths
            ]
            if eligible:
                indexed_at_ns = self._next_indexed_at_ns(connection)
                connection.executemany(
                    _UPSERT_SQL,
                    (self._values(record, indexed_at_ns) for record in eligible),
                )
            return IpaIndexUpdate(upserted=len(eligible), removed=removed)

        return self._run_write(replace)

    def count(self) -> int:
        return self._run_read(
            lambda connection: int(
                connection.execute("SELECT COUNT(*) FROM ipa_entries").fetchone()[0]
            )
        )
