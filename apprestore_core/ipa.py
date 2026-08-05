from __future__ import annotations

import os
import plistlib
import re
import stat
import zipfile
from pathlib import Path, PurePosixPath

from .models import IpaMetadata
from .ipa_index import IpaIndex, IpaIndexError, IpaIndexRecord, normalize_ipa_path

BUNDLE_ID_RE = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9.-]{0,253}[A-Za-z0-9])?$")
MAX_PLIST_SIZE = 8 * 1024 * 1024
MAX_ZIP_ENTRIES = 100_000
MAX_PLIST_COMPRESSION_RATIO = 1_000


class IpaError(ValueError):
    pass


def validate_bundle_id(bundle_id: str) -> str:
    if not isinstance(bundle_id, str):
        raise IpaError("bundle identifier must be a string")
    value = bundle_id
    if not value or not BUNDLE_ID_RE.fullmatch(value):
        raise IpaError(f"invalid bundle identifier: {bundle_id!r}")
    if ".." in value or "/" in value or "\\" in value:
        raise IpaError(f"invalid bundle identifier: {bundle_id!r}")
    return value


def _clean_text(value: object, fallback: str) -> str:
    if value is None:
        return fallback
    text = "".join(
        character if character.isprintable() else " " for character in str(value)
    )
    text = " ".join(text.splitlines()).strip()
    return text or fallback


def _main_info_plist(archive: zipfile.ZipFile) -> zipfile.ZipInfo:
    entries = archive.infolist()
    if len(entries) > MAX_ZIP_ENTRIES:
        raise IpaError("IPA contains too many ZIP entries")
    if any(entry.flag_bits & 0x1 for entry in entries):
        raise IpaError("IPA contains encrypted ZIP entries")

    matches: list[zipfile.ZipInfo] = []
    for entry in entries:
        parts = PurePosixPath(entry.filename).parts
        if (
            len(parts) == 3
            and parts[0] == "Payload"
            and parts[1].endswith(".app")
            and parts[2] == "Info.plist"
        ):
            matches.append(entry)
    if not matches:
        raise IpaError("IPA does not contain Payload/*.app/Info.plist")
    if len(matches) != 1:
        raise IpaError("IPA contains multiple top-level application Info.plist files")
    entry = matches[0]
    if entry.file_size <= 0 or entry.file_size > MAX_PLIST_SIZE:
        raise IpaError("Info.plist has an invalid size")
    if entry.compress_size <= 0 and entry.file_size > 0:
        raise IpaError("Info.plist has an invalid compression size")
    if entry.file_size / entry.compress_size > MAX_PLIST_COMPRESSION_RATIO:
        raise IpaError("Info.plist compression ratio is suspicious")
    return entry


def read_ipa_metadata(path: str | Path) -> IpaMetadata:
    ipa_path = Path(path).expanduser()
    if ipa_path.is_symlink():
        raise IpaError(f"refusing to follow an IPA symlink: {ipa_path}")
    if not ipa_path.is_file():
        raise IpaError(f"IPA not found: {ipa_path}")
    if ipa_path.suffix.lower() != ".ipa":
        raise IpaError(f"not an IPA file: {ipa_path}")

    try:
        with zipfile.ZipFile(ipa_path, "r") as archive:
            entry = _main_info_plist(archive)
            raw = archive.read(entry)
    except (OSError, zipfile.BadZipFile, RuntimeError) as exc:
        raise IpaError(f"invalid IPA archive: {ipa_path}") from exc

    try:
        info = plistlib.loads(raw)
    except (plistlib.InvalidFileException, ValueError, TypeError) as exc:
        raise IpaError("IPA contains an unreadable Info.plist") from exc
    if not isinstance(info, dict):
        raise IpaError("Info.plist root is not a dictionary")

    raw_bundle_id = info.get("CFBundleIdentifier")
    if not isinstance(raw_bundle_id, str):
        raise IpaError("CFBundleIdentifier is missing or is not a string")
    bundle_id = validate_bundle_id(raw_bundle_id)
    fallback_name = ipa_path.stem
    name = _clean_text(
        info.get("CFBundleDisplayName") or info.get("CFBundleName"),
        fallback_name,
    )
    version = _clean_text(
        info.get("CFBundleShortVersionString") or info.get("CFBundleVersion"),
        "?",
    )
    return IpaMetadata(
        path=ipa_path.resolve(),
        bundle_id=bundle_id,
        name=name,
        version=version,
        size=ipa_path.stat().st_size,
    )


def scan_ipas(
    roots: list[Path],
    *,
    index: IpaIndex | None = None,
) -> tuple[list[IpaMetadata], list[tuple[Path, str]]]:
    metadata: list[IpaMetadata] = []
    errors: list[tuple[Path, str]] = []
    seen: set[str] = set()
    observed_records: list[IpaIndexRecord] = []
    completed_roots: list[Path] = []
    modified_by_path: dict[str, int] = {}

    cached_by_path: dict[str, IpaIndexRecord] = {}
    active_index = index
    scan_watermark: int | None = None
    if active_index is not None:
        try:
            scan_watermark = active_index.snapshot_watermark()
            cached_by_path = {
                str(record.path): record
                for record in active_index.records(existing_only=False)
            }
        except IpaIndexError:
            # The index is only a performance cache.  Corruption/contention
            # must never make local IPA discovery unavailable.
            active_index = None

    for root in roots:
        root = root.expanduser()
        if not root.is_dir():
            continue
        walk_failed = False

        def record_walk_error(error: OSError) -> None:
            nonlocal walk_failed
            walk_failed = True
            errors.append((Path(error.filename or root), str(error)))

        for current, directories, files in os.walk(
            root,
            followlinks=False,
            onerror=record_walk_error,
        ):
            current_path = Path(current)
            directories[:] = [
                name for name in directories if not (current_path / name).is_symlink()
            ]
            for filename in files:
                if not filename.lower().endswith(".ipa"):
                    continue
                candidate = current_path / filename
                if candidate.is_symlink():
                    continue
                key = normalize_ipa_path(candidate)
                if key in seen:
                    continue
                seen.add(key)
                try:
                    observed = candidate.lstat()
                    if (
                        not stat.S_ISREG(observed.st_mode)
                        or bool(
                            getattr(observed, "st_file_attributes", 0) & 0x400
                        )
                    ):
                        continue
                    # Zip metadata stores the canonical resolved path.  Resolve
                    # here as well so Windows 8.3 aliases and long paths share
                    # one cache key.
                    normalized = normalize_ipa_path(candidate.resolve())
                    cached = cached_by_path.get(normalized)
                    entry = None
                    if (
                        cached is not None
                        and cached.size == observed.st_size
                        and cached.mtime_ns == observed.st_mtime_ns
                    ):
                        entry = cached.to_metadata()
                    if entry is None:
                        entry = read_ipa_metadata(candidate)
                        if active_index is not None:
                            try:
                                cached = IpaIndexRecord.from_metadata(
                                    entry,
                                    mtime_ns=observed.st_mtime_ns,
                                )
                            except (TypeError, ValueError):
                                # The index is an optimization. Metadata that
                                # exceeds its conservative bounds must remain
                                # visible to the user and installable.
                                cached = None
                    metadata.append(entry)
                    if cached is not None:
                        observed_records.append(cached)
                    modified_by_path[normalize_ipa_path(entry.path)] = (
                        observed.st_mtime_ns
                    )
                except IpaError as exc:
                    errors.append((candidate, str(exc)))
                except OSError as exc:
                    errors.append((candidate, str(exc)))
        if not walk_failed:
            completed_roots.append(root)

    if active_index is not None:
        try:
            active_index.replace_snapshot(
                observed_records,
                roots=completed_roots,
                scan_watermark=scan_watermark,
            )
        except IpaIndexError:
            pass

    metadata.sort(
        key=lambda item: modified_by_path.get(normalize_ipa_path(item.path), 0),
        reverse=True,
    )
    return metadata, errors


def find_exact_ipa(bundle_id: str, entries: list[IpaMetadata]) -> Path | None:
    expected = validate_bundle_id(bundle_id)
    for entry in entries:
        if entry.bundle_id == expected and entry.path.is_file():
            return entry.path
    return None
