"""Durable local memory of App Store IDs discovered by AppRestore.

The file is deliberately a small, human-readable JSON document.  Writes are
serialized across AppRestore processes and committed atomically so a search,
restore, and interactive session cannot lose one another's updates.
"""

from __future__ import annotations

import json
import os
import re
import stat
import tempfile
import time
from collections.abc import Iterable, Iterator, Mapping
from contextlib import contextmanager
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from .ipa import validate_bundle_id
from .paths import known_apps_path

SCHEMA_VERSION = 3
KNOWN_APP_STATUSES = ("candidate", "confirmed", "restored")

_STATUS_RANK = {status: rank for rank, status in enumerate(KNOWN_APP_STATUSES)}
_LOCK_TIMEOUT_SECONDS = 30.0
_LOCK_POLL_SECONDS = 0.025
_MAX_DOCUMENT_BYTES = 8 * 1024 * 1024
_WINDOWS_REPARSE_POINT = 0x400
_STORE_ID_RE = re.compile(r"(?:id)?(\d{8,12})\b", re.IGNORECASE)
_URL_ID_RE = re.compile(
    r"/id(\d{8,12})(?:/|$)",
    re.IGNORECASE,
)


class KnownAppsLockError(RuntimeError):
    """Raised when another process holds the known-apps writer lock too long."""


class KnownAppsDataError(RuntimeError):
    """Raised when an existing history document cannot be updated safely."""


def parse_app_store_id(value: str) -> str | None:
    """Extract numeric App Store ID from bare id, idNNNN, or App Store URL."""
    text = (value or "").strip()
    if not text:
        return None
    url_text = (
        f"https://{text}"
        if text.casefold().startswith("apps.apple.com/")
        else text
    )
    if "://" in url_text:
        try:
            parsed = urlsplit(url_text)
        except ValueError:
            return None
        if parsed.scheme.casefold() != "https" or parsed.hostname != "apps.apple.com":
            return None
        url_match = _URL_ID_RE.search(parsed.path)
        if url_match:
            candidate = url_match.group(1)
            return candidate if candidate.strip("0") else None
        return None
    if text.isdigit() and 8 <= len(text) <= 12:
        return text if text.strip("0") else None
    bare = _STORE_ID_RE.fullmatch(text)
    if bare:
        candidate = bare.group(1)
        return candidate if candidate.strip("0") else None
    return None


def _normalise_provenance(value: object, *, fallback: str) -> list[str]:
    if isinstance(value, str):
        values: Iterable[object] = (value,)
    elif isinstance(value, (list, tuple, set, frozenset)):
        values = value
    else:
        values = ()

    result: set[str] = set()
    for item in values:
        text = str(item or "").strip()
        if text:
            # This is metadata, not an unbounded log supplied by remote catalogs.
            result.add(text[:128])
    if not result:
        result.add(fallback)
    return sorted(result, key=str.casefold)


def _normalise_store_id(value: object) -> str | None:
    store_id = str(value or "").strip()
    if (
        not store_id.isdigit()
        or not 8 <= len(store_id) <= 12
        or not store_id.strip("0")
    ):
        return None
    return store_id


def _normalise_bundle_id(value: object) -> str | None:
    bundle_id = str(value or "").strip()
    if not bundle_id:
        return None
    try:
        return validate_bundle_id(bundle_id)
    except (TypeError, ValueError):
        return None


def _normalise_app(
    item: Mapping[str, object],
    *,
    legacy: bool,
    default_provenance: str,
) -> dict[str, Any] | None:
    store_id = _normalise_store_id(item.get("storeId"))
    bundle_id = _normalise_bundle_id(item.get("bundleId"))
    if store_id is None and bundle_id is None:
        return None

    name = str(item.get("name") or bundle_id or store_id).strip()
    version = str(item.get("version") or "?").strip()
    status = str(item.get("status") or "confirmed").strip().casefold()
    if status not in _STATUS_RANK:
        status = "confirmed"

    provenance_value = item.get("provenance")
    if provenance_value is None and item.get("source") is not None:
        provenance_value = item.get("source")
    provenance = _normalise_provenance(
        provenance_value,
        fallback="legacy" if legacy else default_provenance,
    )
    return {
        "storeId": store_id,
        "bundleId": bundle_id,
        "name": name or bundle_id or store_id,
        "version": version or "?",
        "status": status,
        "provenance": provenance,
    }


def _load_known_apps_unlocked(
    target: Path,
    *,
    strict: bool = False,
) -> list[dict[str, Any]]:
    try:
        observed = target.lstat()
    except FileNotFoundError:
        return []
    except OSError as exc:
        if strict:
            raise KnownAppsDataError(
                f"cannot inspect existing known-apps document: {target}"
            ) from exc
        return []
    if (
        not stat.S_ISREG(observed.st_mode)
        or bool(
            int(getattr(observed, "st_file_attributes", 0))
            & _WINDOWS_REPARSE_POINT
        )
        or observed.st_size > _MAX_DOCUMENT_BYTES
    ):
        if strict:
            raise KnownAppsDataError(
                f"refusing to replace an unsafe known-apps document: {target}"
            )
        return []
    try:
        with target.open("rb") as stream:
            raw = stream.read(_MAX_DOCUMENT_BYTES + 1)
        if len(raw) > _MAX_DOCUMENT_BYTES:
            raise ValueError("known-apps document exceeds the size limit")
        payload = json.loads(raw.decode("utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        if strict:
            raise KnownAppsDataError(
                f"refusing to replace an unreadable known-apps document: {target}"
            ) from exc
        return []
    if not isinstance(payload, dict):
        if strict:
            raise KnownAppsDataError(
                f"refusing to replace a malformed known-apps document: {target}"
            )
        return []
    apps = payload.get("apps")
    if not isinstance(apps, list):
        if strict:
            raise KnownAppsDataError(
                f"refusing to replace a malformed known-apps document: {target}"
            )
        return []

    schema_version = payload.get("schemaVersion")
    if "schemaVersion" in payload and (
        isinstance(schema_version, bool)
        or not isinstance(schema_version, int)
        or schema_version < 1
    ):
        if strict:
            raise KnownAppsDataError(
                "known-apps schemaVersion must be a positive integer"
            )
        return []
    if isinstance(schema_version, int) and schema_version > SCHEMA_VERSION:
        if strict:
            raise KnownAppsDataError(
                f"known-apps schema {schema_version} is newer than supported "
                f"schema {SCHEMA_VERSION}"
            )
        return []
    legacy = not isinstance(schema_version, int) or schema_version < 2
    result: list[dict[str, Any]] = []
    for item in apps:
        if not isinstance(item, dict):
            if strict:
                raise KnownAppsDataError(
                    "refusing to replace known-apps with an invalid app record"
                )
            continue
        raw_status = item.get("status")
        if schema_version == SCHEMA_VERSION and raw_status is not None:
            normalized_status = str(raw_status).strip().casefold()
            if not isinstance(raw_status, str) or normalized_status not in _STATUS_RANK:
                if strict:
                    raise KnownAppsDataError(
                        "refusing to replace known-apps with an invalid status"
                    )
                continue
        normalised = _normalise_app(
            item,
            legacy=legacy,
            default_provenance="unknown",
        )
        if normalised is None:
            if strict:
                raise KnownAppsDataError(
                    "refusing to replace known-apps with an invalid app identity"
                )
            continue
        _merge_into_records(result, normalised)
    result.sort(key=_app_sort_key)
    return result


def load_known_apps(path: Path | None = None) -> list[dict[str, Any]]:
    """Load schema v1-v3 records in the current normalised representation.

    Reading is side-effect free.  A v1/v2 document is upgraded on the next call
    to :func:`remember_known_app` or :func:`remember_known_apps`.
    """

    return _load_known_apps_unlocked(path or known_apps_path())


def _app_sort_key(item: Mapping[str, object]) -> tuple[str, str, str]:
    return (
        str(item.get("name") or item.get("storeId") or "").casefold(),
        str(item.get("storeId") or ""),
        str(item.get("bundleId") or ""),
    )


def _lock_path(target: Path) -> Path:
    return target.with_name(f".{target.name}.lock")


@contextmanager
def _exclusive_writer_lock(target: Path) -> Iterator[None]:
    """Hold a sibling advisory lock on Windows and POSIX platforms."""

    lock_path = _lock_path(target)
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    handle = lock_path.open("a+b")
    try:
        try:
            lock_path.chmod(0o600)
        except OSError:
            pass

        deadline = time.monotonic() + _LOCK_TIMEOUT_SECONDS
        if os.name == "nt":
            import msvcrt

            handle.seek(0, os.SEEK_END)
            if handle.tell() < 1:
                handle.write(b"\0")
                handle.flush()
                os.fsync(handle.fileno())

            while True:
                try:
                    handle.seek(0)
                    msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                    break
                except OSError as exc:
                    if time.monotonic() >= deadline:
                        raise KnownAppsLockError(
                            f"timed out waiting for known-apps lock: {lock_path}"
                        ) from exc
                    time.sleep(_LOCK_POLL_SECONDS)
            try:
                yield
            finally:
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl

            flock = getattr(fcntl, "flock")
            lock_ex = int(getattr(fcntl, "LOCK_EX"))
            lock_nb = int(getattr(fcntl, "LOCK_NB"))
            lock_un = int(getattr(fcntl, "LOCK_UN"))
            while True:
                try:
                    flock(handle.fileno(), lock_ex | lock_nb)
                    break
                except BlockingIOError as exc:
                    if time.monotonic() >= deadline:
                        raise KnownAppsLockError(
                            f"timed out waiting for known-apps lock: {lock_path}"
                        ) from exc
                    time.sleep(_LOCK_POLL_SECONDS)
            try:
                yield
            finally:
                flock(handle.fileno(), lock_un)
    finally:
        handle.close()


def _merge_app(
    current: dict[str, Any],
    update: Mapping[str, object],
) -> None:
    current_status = str(current.get("status") or "confirmed").strip().casefold()
    current_rank = _STATUS_RANK.get(current_status, _STATUS_RANK["confirmed"])
    incoming_status_raw = update.get("status")
    incoming_status = (
        str(incoming_status_raw).strip().casefold()
        if incoming_status_raw is not None
        else "confirmed"
    )
    if incoming_status not in _STATUS_RANK:
        raise ValueError(
            "status must be one of: " + ", ".join(KNOWN_APP_STATUSES)
        )
    incoming_rank = _STATUS_RANK[incoming_status]
    current_store_id = _normalise_store_id(current.get("storeId"))
    incoming_store_id = _normalise_store_id(update.get("storeId"))
    current_bundle_id = _normalise_bundle_id(current.get("bundleId"))
    incoming_bundle_id = _normalise_bundle_id(update.get("bundleId"))
    store_conflict = bool(
        current_store_id
        and incoming_store_id
        and current_store_id != incoming_store_id
    )
    bundle_conflict = bool(
        current_bundle_id
        and incoming_bundle_id
        and current_bundle_id != incoming_bundle_id
    )
    non_stronger_identity_conflict = (
        incoming_rank <= current_rank and (store_conflict or bundle_conflict)
    )

    for key, current_identity, incoming_identity in (
        ("storeId", current_store_id, incoming_store_id),
        ("bundleId", current_bundle_id, incoming_bundle_id),
    ):
        if incoming_identity is None:
            continue
        if (
            current_identity is None
            and incoming_rank < _STATUS_RANK["confirmed"]
            and incoming_rank < current_rank
        ):
            continue
        if (
            current_identity is not None
            and current_identity != incoming_identity
            and incoming_rank <= current_rank
        ):
            continue
        current[key] = incoming_identity

    for key in ("name", "version"):
        value = update.get(key)
        incoming_value = str(value).strip() if value is not None else ""
        if not incoming_value:
            continue

        # Metadata attached to a non-stronger conflicting identity is ignored.
        # A matching identity may still refresh the display name and version.
        if non_stronger_identity_conflict:
            continue
        current[key] = incoming_value

    if incoming_status_raw is not None:
        if incoming_rank > current_rank:
            current["status"] = incoming_status

    provenance_value = update.get("provenance")
    if provenance_value is None and update.get("source") is not None:
        provenance_value = update.get("source")
    incoming_provenance = _normalise_provenance(
        provenance_value,
        fallback="remembered",
    )
    current_provenance = _normalise_provenance(
        current.get("provenance"),
        fallback="unknown",
    )
    current["provenance"] = sorted(
        set(current_provenance).union(incoming_provenance),
        key=str.casefold,
    )


def _normalise_update(item: Mapping[str, object]) -> dict[str, object] | None:
    store_id = _normalise_store_id(item.get("storeId"))
    bundle_id = _normalise_bundle_id(item.get("bundleId"))
    if store_id is None and bundle_id is None:
        return None
    update: dict[str, object] = {
        "storeId": store_id,
        "bundleId": bundle_id,
    }
    for key in ("name", "version", "status", "provenance", "source"):
        if key in item:
            update[key] = item[key]
    status = update.get("status")
    if status is not None and str(status).strip().casefold() not in _STATUS_RANK:
        raise ValueError("status must be one of: " + ", ".join(KNOWN_APP_STATUSES))
    return update


def _identity_component(
    records: list[dict[str, Any]],
    update: Mapping[str, object],
) -> list[dict[str, Any]]:
    store_ids = {_normalise_store_id(update.get("storeId"))} - {None}
    bundle_ids = {_normalise_bundle_id(update.get("bundleId"))} - {None}
    matches: list[dict[str, Any]] = []
    remaining = list(records)
    changed = True
    while changed:
        changed = False
        for record in tuple(remaining):
            store_id = _normalise_store_id(record.get("storeId"))
            bundle_id = _normalise_bundle_id(record.get("bundleId"))
            if not (
                (store_id is not None and store_id in store_ids)
                or (bundle_id is not None and bundle_id in bundle_ids)
            ):
                continue
            matches.append(record)
            remaining.remove(record)
            if store_id is not None:
                store_ids.add(store_id)
            if bundle_id is not None:
                bundle_ids.add(bundle_id)
            changed = True
    return matches


def _record_strength(item: Mapping[str, object]) -> tuple[int, int, int]:
    status = str(item.get("status") or "confirmed").strip().casefold()
    return (
        _STATUS_RANK.get(status, _STATUS_RANK["confirmed"]),
        int(_normalise_bundle_id(item.get("bundleId")) is not None),
        int(_normalise_store_id(item.get("storeId")) is not None),
    )


def _identities_conflict(
    first: Mapping[str, object],
    second: Mapping[str, object],
) -> bool:
    first_store = _normalise_store_id(first.get("storeId"))
    second_store = _normalise_store_id(second.get("storeId"))
    first_bundle = _normalise_bundle_id(first.get("bundleId"))
    second_bundle = _normalise_bundle_id(second.get("bundleId"))
    return bool(
        (first_store and second_store and first_store != second_store)
        or (first_bundle and second_bundle and first_bundle != second_bundle)
    )


def _merge_into_records(
    records: list[dict[str, Any]],
    update: Mapping[str, object],
) -> dict[str, Any] | None:
    matches = _identity_component(records, update)
    if not matches:
        candidate = _normalise_app(
            update,
            legacy=False,
            default_provenance="remembered",
        )
        if candidate is not None:
            records.append(candidate)
        return candidate

    canonical = max(matches, key=_record_strength)
    canonical_rank = _record_strength(canonical)[0]
    incoming_status = str(update.get("status") or "confirmed").strip().casefold()
    incoming_rank = _STATUS_RANK.get(
        incoming_status,
        _STATUS_RANK["confirmed"],
    )
    merged_ids: set[int] = set()
    for duplicate in matches:
        if duplicate is canonical:
            continue
        duplicate_rank = _record_strength(duplicate)[0]
        if _identities_conflict(canonical, duplicate) and not (
            canonical_rank > duplicate_rank or incoming_rank > canonical_rank
        ):
            # A weak bridge must not collapse two equally trusted but
            # contradictory identities.  Preserve both until stronger evidence
            # resolves the ambiguity.
            continue
        _merge_app(canonical, duplicate)
        merged_ids.add(id(duplicate))
    if merged_ids:
        records[:] = [item for item in records if id(item) not in merged_ids]
    _merge_app(canonical, update)
    return canonical


def _fsync_parent_directory(path: Path) -> None:
    """Persist the directory entry where the platform supports directory fsync."""

    if os.name == "nt":
        return
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError:
        return
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _atomic_write(target: Path, apps: list[dict[str, Any]]) -> None:
    payload = {"schemaVersion": SCHEMA_VERSION, "apps": apps}
    encoded = (
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
    ).encode("utf-8")
    if len(encoded) > _MAX_DOCUMENT_BYTES:
        raise KnownAppsDataError(
            "refusing to write known-apps document beyond the size limit"
        )
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{target.name}.",
        suffix=".tmp",
        dir=target.parent,
    )
    temporary = Path(temporary_name)
    try:
        try:
            temporary.chmod(0o600)
        except OSError:
            pass
        with os.fdopen(descriptor, "wb") as handle:
            descriptor = -1
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, target)
        _fsync_parent_directory(target.parent)
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


def remember_known_apps(
    apps: Iterable[Mapping[str, object]],
    *,
    path: Path | None = None,
) -> None:
    """Merge several known apps in one locked, durable transaction."""

    updates = [update for item in apps if (update := _normalise_update(item))]
    if not updates:
        return

    target = path or known_apps_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    with _exclusive_writer_lock(target):
        current = _load_known_apps_unlocked(target, strict=True)
        for update in updates:
            _merge_into_records(current, update)

        current.sort(key=_app_sort_key)
        _atomic_write(target, current)


def remember_known_app(
    *,
    store_id: str | None = None,
    bundle_id: str | None = None,
    name: str | None = None,
    version: str | None = None,
    provenance: str | Iterable[str] | None = None,
    status: str | None = None,
    path: Path | None = None,
) -> None:
    """Remember one app without losing concurrent updates from other processes.

    Existing callers remain valid.  New callers can describe how trustworthy a
    record is with ``status`` and where it came from with ``provenance``.
    Either a numeric ``store_id`` or valid ``bundle_id`` is required.  Status is
    monotonic: candidate < confirmed < restored.
    """

    update: dict[str, object] = {
        "storeId": store_id,
        "bundleId": bundle_id,
        "name": name,
        "version": version,
    }
    if provenance is not None:
        update["provenance"] = provenance
    if status is not None:
        update["status"] = status
    remember_known_apps((update,), path=path)
