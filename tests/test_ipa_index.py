from __future__ import annotations

import multiprocessing
import os
import sqlite3
from collections.abc import Iterator
from pathlib import Path

import pytest

from apprestore_core.ipa_index import (
    SCHEMA_VERSION,
    IpaIndex,
    IpaIndexClosedError,
    IpaIndexError,
    IpaIndexRecord,
    IpaIndexSchemaError,
    normalize_ipa_path,
)
from apprestore_core.ipa import scan_ipas
from apprestore_core.models import IpaMetadata


def _record(
    path: Path,
    *,
    bundle_id: str = "com.example.app",
    name: str = "Example",
    version: str = "1.0",
    sha256: str | None = None,
) -> IpaIndexRecord:
    metadata = path.lstat()
    return IpaIndexRecord(
        path=path,
        size=metadata.st_size,
        mtime_ns=metadata.st_mtime_ns,
        bundle_id=bundle_id,
        name=name,
        version=version,
        sha256=sha256,
    )


def _concurrent_writer(
    database: str,
    paths: list[str],
    start: multiprocessing.synchronize.Event,
) -> None:
    if not start.wait(timeout=15):
        raise RuntimeError("concurrent index test did not start")
    with IpaIndex(database, busy_timeout_ms=10_000) as index:
        records = [
            _record(Path(path), bundle_id=f"com.example.worker{Path(path).stem}")
            for path in paths
        ]
        index.upsert_many(records)


def test_schema_wal_and_round_trip_without_reopening_ipa(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database = tmp_path / "cache" / "ipas.sqlite3"
    ipa = tmp_path / "Example.ipa"
    ipa.write_bytes(b"not parsed by the metadata index")
    observed = ipa.lstat()
    metadata = IpaMetadata(
        path=ipa,
        bundle_id="com.example.roundtrip",
        name="Round Trip",
        version="2.4",
        size=observed.st_size,
    )
    record = IpaIndexRecord.from_metadata(
        metadata,
        mtime_ns=observed.st_mtime_ns,
        sha256="a" * 64,
    )

    index = IpaIndex(database)
    assert index.upsert(record).upserted == 1

    def unexpected_lstat(_: Path) -> os.stat_result:
        raise AssertionError("get() ignored the scanner-provided stat result")

    monkeypatch.setattr(Path, "lstat", unexpected_lstat)
    noncanonical_path = os.path.join(
        str(ipa.parent),
        "directory-that-need-not-exist",
        "..",
        ipa.name,
    )
    cached = index.get(noncanonical_path, stat_result=observed)

    assert cached == record
    assert cached is not None
    assert cached.to_metadata() == metadata
    with sqlite3.connect(database) as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION
        assert connection.execute("PRAGMA journal_mode").fetchone()[0] == "wal"


def test_changed_signature_invalidates_and_removes_cached_record(
    tmp_path: Path,
) -> None:
    ipa = tmp_path / "Changed.ipa"
    ipa.write_bytes(b"old")
    before = ipa.lstat()
    index = IpaIndex(tmp_path / "ipas.sqlite3")
    index.upsert(_record(ipa))

    ipa.write_bytes(b"new")
    os.utime(
        ipa,
        ns=(before.st_atime_ns, before.st_mtime_ns + 10_000_000),
    )
    assert ipa.lstat().st_mtime_ns != before.st_mtime_ns

    assert index.get(ipa) is None
    assert index.count() == 0


def test_find_bundle_is_exact_newest_first_and_prunes_stale(
    tmp_path: Path,
) -> None:
    older = tmp_path / "Older.ipa"
    newer = tmp_path / "Newer.ipa"
    other = tmp_path / "Other.ipa"
    for path in (older, newer, other):
        path.write_bytes(path.name.encode("ascii"))
    base = older.lstat().st_mtime_ns
    os.utime(older, ns=(base, base + 1_000_000))
    os.utime(newer, ns=(base, base + 2_000_000))
    os.utime(other, ns=(base, base + 3_000_000))

    index = IpaIndex(tmp_path / "ipas.sqlite3")
    index.upsert_many(
        (
            _record(older, bundle_id="com.example.same"),
            _record(newer, bundle_id="com.example.same"),
            _record(other, bundle_id="com.example.other"),
        )
    )
    newer.unlink()

    matches = index.find_bundle("com.example.same")

    assert [record.path for record in matches] == [older]
    assert index.count() == 2
    assert index.find_bundle("com.example.other")[0].path == other


def test_prune_missing_can_be_limited_to_selected_paths(tmp_path: Path) -> None:
    first = tmp_path / "First.ipa"
    second = tmp_path / "Second.ipa"
    first.write_bytes(b"first")
    second.write_bytes(b"second")
    index = IpaIndex(tmp_path / "ipas.sqlite3")
    index.upsert_many((_record(first), _record(second)))
    first.unlink()
    second.unlink()

    assert index.prune_missing((first,)) == 1
    assert index.count() == 1
    assert index.records(existing_only=False)[0].path == second
    assert index.prune_missing() == 1
    assert index.count() == 0


def test_replace_snapshot_only_prunes_the_scanned_roots(tmp_path: Path) -> None:
    first_root = tmp_path / "first"
    second_root = tmp_path / "second"
    first_root.mkdir()
    second_root.mkdir()
    keep = first_root / "Keep.ipa"
    omitted = first_root / "Omitted.ipa"
    outside = second_root / "Outside.ipa"
    for path in (keep, omitted, outside):
        path.write_bytes(path.name.encode("ascii"))

    index = IpaIndex(tmp_path / "ipas.sqlite3")
    index.upsert_many((_record(keep), _record(omitted), _record(outside)))

    update = index.replace_snapshot((_record(keep),), roots=(first_root,))

    assert update.upserted == 1
    assert update.removed == 1
    assert {record.path for record in index.records(existing_only=False)} == {
        keep,
        outside,
    }
    assert omitted.is_file(), "snapshot pruning must not delete the actual IPA"


def test_replace_snapshot_preserves_rows_written_after_scan_watermark(
    tmp_path: Path,
) -> None:
    keep = tmp_path / "Keep.ipa"
    omitted = tmp_path / "Omitted.ipa"
    concurrent = tmp_path / "Concurrent.ipa"
    for path in (keep, omitted, concurrent):
        path.write_bytes(path.name.encode("ascii"))

    first = IpaIndex(tmp_path / "ipas.sqlite3")
    second = IpaIndex(tmp_path / "ipas.sqlite3")
    first.upsert_many((_record(keep), _record(omitted)))
    scan_watermark = first.snapshot_watermark()
    second.upsert(_record(keep, bundle_id="com.example.newer"))
    second.upsert(_record(concurrent))

    update = first.replace_snapshot(
        (_record(keep, bundle_id="com.example.stale"),),
        roots=(tmp_path,),
        scan_watermark=scan_watermark,
    )

    assert update.upserted == 0
    assert update.removed == 1
    records = first.records(existing_only=False)
    assert {record.path for record in records} == {
        keep,
        concurrent,
    }
    assert next(record for record in records if record.path == keep).bundle_id == (
        "com.example.newer"
    )


def test_scan_ipas_protects_a_concurrent_row_created_during_walk(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    trigger = tmp_path / "Trigger.ipa"
    late = tmp_path / "Late.ipa"
    trigger.write_bytes(b"trigger")
    first = IpaIndex(tmp_path / "ipas.sqlite3")
    second = IpaIndex(tmp_path / "ipas.sqlite3")

    def fake_metadata(path: str | Path) -> IpaMetadata:
        candidate = Path(path)
        if not late.exists():
            late.write_bytes(b"late")
            second.upsert(_record(late, bundle_id="com.example.late"))
        observed = candidate.lstat()
        return IpaMetadata(
            path=candidate.resolve(),
            bundle_id="com.example.trigger",
            name="Trigger",
            version="1.0",
            size=observed.st_size,
        )

    monkeypatch.setattr("apprestore_core.ipa.read_ipa_metadata", fake_metadata)

    metadata, errors = scan_ipas([tmp_path], index=first)

    assert errors == []
    assert [item.path for item in metadata] == [trigger.resolve()]
    assert {record.path for record in first.records(existing_only=False)} == {
        trigger.resolve(),
        late.resolve(),
    }


def test_replace_all_and_upserts_are_transactional(tmp_path: Path) -> None:
    ipa = tmp_path / "Atomic.ipa"
    ipa.write_bytes(b"atomic")
    index = IpaIndex(tmp_path / "ipas.sqlite3")

    def broken_records() -> Iterator[IpaIndexRecord]:
        yield _record(ipa)
        raise RuntimeError("scan failed before snapshot completion")

    with pytest.raises(RuntimeError, match="scan failed"):
        index.upsert_many(broken_records())
    assert index.count() == 0

    index.upsert(_record(ipa))
    update = index.replace_snapshot((), roots=None)
    assert update.upserted == 0
    assert update.removed == 1
    assert index.count() == 0


def test_concurrent_process_writers_do_not_lose_records(tmp_path: Path) -> None:
    context = multiprocessing.get_context("spawn")
    start = context.Event()
    database = tmp_path / "shared.sqlite3"
    groups: list[list[str]] = []
    for worker in range(4):
        paths: list[str] = []
        for item in range(8):
            path = tmp_path / f"{worker}-{item}.ipa"
            path.write_bytes(f"{worker}:{item}".encode("ascii"))
            paths.append(str(path))
        groups.append(paths)

    processes = [
        context.Process(
            target=_concurrent_writer,
            args=(str(database), paths, start),
        )
        for paths in groups
    ]
    for process in processes:
        process.start()
    start.set()
    for process in processes:
        process.join(timeout=30)
        if process.is_alive():
            process.terminate()
            process.join(timeout=5)
            pytest.fail("concurrent IPA index writer hung")
        assert process.exitcode == 0

    index = IpaIndex(database)
    assert index.count() == 32
    assert len(index.records()) == 32


def test_incompatible_schema_and_closed_index_fail_explicitly(
    tmp_path: Path,
) -> None:
    database = tmp_path / "ipas.sqlite3"
    index = IpaIndex(database)
    index.close()
    with pytest.raises(IpaIndexClosedError):
        index.count()
    with pytest.raises(IpaIndexClosedError):
        index.upsert_many(())

    with sqlite3.connect(database) as connection:
        connection.execute("PRAGMA user_version = 999")
    with pytest.raises(IpaIndexSchemaError, match="schema 999"):
        IpaIndex(database)


def test_non_finite_sqlite_integer_is_reported_as_index_corruption(
    tmp_path: Path,
) -> None:
    database = tmp_path / "ipas.sqlite3"
    index = IpaIndex(database)
    with sqlite3.connect(database) as connection:
        connection.execute(
            """
            INSERT INTO ipa_entries (
                path, size, mtime_ns, bundle_id, name, version, sha256,
                indexed_at_ns
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                str(tmp_path / "Corrupt.ipa"),
                float("inf"),
                1,
                "com.example.corrupt",
                "Corrupt",
                "1.0",
                None,
                1,
            ),
        )

    with pytest.raises(IpaIndexError, match="invalid record"):
        index.records(existing_only=False)


def test_fractional_sqlite_integer_is_not_silently_truncated(
    tmp_path: Path,
) -> None:
    database = tmp_path / "ipas.sqlite3"
    index = IpaIndex(database)
    with sqlite3.connect(database) as connection:
        connection.execute(
            """
            INSERT INTO ipa_entries (
                path, size, mtime_ns, bundle_id, name, version, sha256,
                indexed_at_ns
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                str(tmp_path / "Fractional.ipa"),
                1.5,
                1,
                "com.example.fractional",
                "Fractional",
                "1.0",
                None,
                1,
            ),
        )

    with pytest.raises(IpaIndexError, match="invalid record"):
        index.records(existing_only=False)


def test_path_normalization_collapses_directory_symlink_aliases(
    tmp_path: Path,
) -> None:
    real = tmp_path / "real"
    real.mkdir()
    ipa = real / "Example.ipa"
    ipa.write_bytes(b"example")
    alias = tmp_path / "alias"
    try:
        alias.symlink_to(real, target_is_directory=True)
    except OSError as exc:
        pytest.skip(f"directory symlinks are unavailable: {exc}")

    assert normalize_ipa_path(alias / ipa.name) == normalize_ipa_path(ipa)
