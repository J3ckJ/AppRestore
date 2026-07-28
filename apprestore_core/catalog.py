from __future__ import annotations

import json
import plistlib
import re
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any

from .ipa import IpaError, validate_bundle_id
from .models import IpaMetadata, OffloadedApp

ADAM_ID_KEYS = {
    "adamid",
    "appleitemid",
    "appstoreid",
    "itemid",
    "storeid",
    "storeitemidentifier",
    "salableadamid",
    "trackid",
}
# Back-compat alias used by older imports/tests.
STORE_ID_KEYS = ADAM_ID_KEYS
MAX_JSON_OUTPUT = 32 * 1024 * 1024
MAX_STORE_ID_DIGITS = 20
UDID_RE = re.compile(r"^[A-Za-z0-9-]{8,128}$")


class CatalogError(ValueError):
    pass


def parse_json_output(raw: str) -> Any:
    if len(raw.encode("utf-8", errors="replace")) > MAX_JSON_OUTPUT:
        raise CatalogError("command returned too much JSON")
    text = raw.lstrip("\ufeff").strip()
    if not text:
        raise CatalogError("command returned no JSON")

    def reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise ValueError(f"duplicate JSON key: {key}")
            result[key] = value
        return result

    try:
        return json.loads(
            text,
            object_pairs_hook=reject_duplicate_keys,
            parse_constant=lambda value: (_ for _ in ()).throw(
                ValueError(f"invalid JSON number: {value}")
            ),
        )
    except (json.JSONDecodeError, ValueError) as exc:
        raise CatalogError("command returned malformed JSON") from exc


def parse_udids(raw: str) -> list[str]:
    payload = parse_json_output(raw)
    values: Iterable[Any]
    if isinstance(payload, list):
        values = payload
    elif isinstance(payload, Mapping):
        values = payload.keys()
    else:
        raise CatalogError("unexpected device list format")

    result: list[str] = []
    for value in values:
        if isinstance(value, Mapping):
            value = (
                value.get("UniqueDeviceID")
                or value.get("UDID")
                or value.get("udid")
                or ""
            )
        text = str(value).strip()
        if text and not UDID_RE.fullmatch(text):
            raise CatalogError(f"invalid device identifier: {text!r}")
        if text and text not in result:
            result.append(text)
    return result


def _normal_key(value: object) -> str:
    return re.sub(r"[^a-z0-9]", "", str(value).lower())


def _store_id_from_value(value: object) -> str | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int) and value > 0:
        return str(value)
    text = str(value).strip()
    return (
        text
        if text.isascii()
        and text.isdigit()
        and len(text) <= MAX_STORE_ID_DIGITS
        and int(text) > 0
        else None
    )


def find_store_id(value: object, *, depth: int = 0) -> str | None:
    if depth > 8:
        return None
    if isinstance(value, (bytes, bytearray, memoryview)):
        try:
            decoded = plistlib.loads(bytes(value))
        except Exception:
            return None
        return find_store_id(decoded, depth=depth + 1)
    if isinstance(value, Mapping):
        for key, nested in value.items():
            if _normal_key(key) in ADAM_ID_KEYS:
                store_id = _store_id_from_value(nested)
                if store_id:
                    return store_id
        # Prefer decoding known metadata blobs before deep walk.
        for meta_key in ("iTunesMetadata", "ITunesMetadata", "itunesMetadata"):
            if meta_key in value:
                store_id = find_store_id(value.get(meta_key), depth=depth + 1)
                if store_id:
                    return store_id
        for nested in value.values():
            store_id = find_store_id(nested, depth=depth + 1)
            if store_id:
                return store_id
    elif isinstance(value, list):
        for nested in value:
            store_id = find_store_id(nested, depth=depth + 1)
            if store_id:
                return store_id
    return None


def enrich_app_record(info: Mapping[str, Any]) -> dict[str, Any]:
    """Return a shallow copy with binary iTunesMetadata decoded when possible."""
    enriched = dict(info)
    meta = enriched.get("iTunesMetadata")
    if isinstance(meta, (bytes, bytearray, memoryview)):
        try:
            decoded = plistlib.loads(bytes(meta))
        except Exception:
            return enriched
        if isinstance(decoded, Mapping):
            enriched["iTunesMetadata"] = dict(decoded)
    return enriched


def load_imazing_catalog(candidates: list[Path]) -> dict[str, str]:
    result: dict[str, str] = {}
    for candidate in candidates:
        if not candidate.is_file():
            continue
        try:
            with candidate.open("rb") as handle:
                payload = plistlib.load(handle)
        except (OSError, plistlib.InvalidFileException):
            continue
        if not isinstance(payload, Mapping):
            continue
        for bundle_id, info in payload.items():
            if not isinstance(info, Mapping):
                continue
            store_id = find_store_id(info)
            if store_id:
                result[str(bundle_id)] = store_id
    return result


def lookup_itunes_store_id(
    bundle_id: str,
    *,
    countries: tuple[str, ...] = ("", "ru", "us", "gb", "de"),
) -> str | None:
    """Resolve a numeric App Store ID via the public iTunes Lookup API."""
    import urllib.error
    import urllib.parse
    import urllib.request

    expected = str(bundle_id).strip()
    if not expected:
        return None

    for country in countries:
        query: dict[str, str] = {"bundleId": expected}
        if country:
            query["country"] = country
        url = "https://itunes.apple.com/lookup?" + urllib.parse.urlencode(query)
        try:
            with urllib.request.urlopen(url, timeout=20) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except (
            TimeoutError,
            urllib.error.URLError,
            json.JSONDecodeError,
            OSError,
            ValueError,
        ):
            continue
        if not isinstance(payload, Mapping):
            continue
        results = payload.get("results")
        if not isinstance(results, list) or not results:
            continue
        first = results[0]
        if not isinstance(first, Mapping):
            continue
        returned_bundle = first.get("bundleId")
        if isinstance(returned_bundle, str) and returned_bundle != expected:
            continue
        store_id = _store_id_from_value(first.get("trackId"))
        if store_id:
            return store_id
    return None


def _clean_text(value: object, fallback: str) -> str:
    if value is None:
        return fallback
    text = "".join(
        character if character.isprintable() else " " for character in str(value)
    )
    text = " ".join(text.splitlines()).strip()
    return text or fallback


def _safe_int(value: object) -> int:
    try:
        number = int(value or 0)
        return max(number, 0)
    except (TypeError, ValueError):
        return 0


def _is_placeholder(info: Mapping[str, Any]) -> bool:
    if info.get("IsPlaceholder") is True or info.get("IsDemotedApp") is True:
        return True
    application_type = str(info.get("ApplicationType") or "")
    return "placeholder" in application_type.lower()


def _app_records(payload: object) -> list[tuple[str, Mapping[str, Any]]]:
    records: list[tuple[str, Mapping[str, Any]]] = []
    seen: set[str] = set()
    if isinstance(payload, Mapping):
        for key, value in payload.items():
            if not isinstance(value, Mapping):
                continue
            outer_bundle_id = str(key)
            inner_bundle_id = value.get("CFBundleIdentifier")
            if inner_bundle_id is not None and not isinstance(inner_bundle_id, str):
                raise CatalogError("application bundle identifier is not a string")
            bundle_id = inner_bundle_id or outer_bundle_id
            if inner_bundle_id and inner_bundle_id != outer_bundle_id:
                raise CatalogError(
                    "application list contains conflicting bundle identifiers"
                )
            try:
                validate_bundle_id(bundle_id)
            except IpaError as exc:
                raise CatalogError(str(exc)) from exc
            if bundle_id in seen:
                raise CatalogError("application list contains duplicate bundle IDs")
            seen.add(bundle_id)
            records.append((bundle_id, value))
    elif isinstance(payload, list):
        for value in payload:
            if not isinstance(value, Mapping):
                continue
            bundle_id = value.get("CFBundleIdentifier")
            if bundle_id is None:
                continue
            if not isinstance(bundle_id, str):
                raise CatalogError("application bundle identifier is not a string")
            try:
                validate_bundle_id(bundle_id)
            except IpaError as exc:
                raise CatalogError(str(exc)) from exc
            if bundle_id in seen:
                raise CatalogError("application list contains duplicate bundle IDs")
            seen.add(bundle_id)
            records.append((bundle_id, value))
    else:
        raise CatalogError("unexpected apps list format")
    return records


def parse_offloaded_apps(
    payload: object,
    local_ipas: list[IpaMetadata],
    imazing_catalog: Mapping[str, str] | None = None,
) -> list[OffloadedApp]:
    local_by_bundle: dict[str, Path] = {}
    for ipa in local_ipas:
        local_by_bundle.setdefault(ipa.bundle_id, ipa.path)

    imazing_catalog = imazing_catalog or {}
    apps: list[OffloadedApp] = []
    for bundle_id, info in _app_records(payload):
        if not _is_placeholder(info):
            continue

        enriched = enrich_app_record(info)
        direct_store_id = find_store_id(enriched)
        if direct_store_id:
            store_id = direct_store_id
            store_match = "device"
        else:
            store_id = imazing_catalog.get(bundle_id)
            store_match = "exact-imazing" if store_id else "none"

        apps.append(
            OffloadedApp(
                bundle_id=bundle_id,
                name=_clean_text(
                    enriched.get("CFBundleDisplayName") or enriched.get("CFBundleName"),
                    bundle_id,
                ),
                version=_clean_text(
                    enriched.get("CFBundleShortVersionString")
                    or enriched.get("CFBundleVersion"),
                    "?",
                ),
                static_size=_safe_int(enriched.get("StaticDiskUsage")),
                dynamic_size=_safe_int(enriched.get("DynamicDiskUsage")),
                store_id=store_id,
                store_match=store_match,
                local_ipa=local_by_bundle.get(bundle_id),
            )
        )

    apps.sort(key=lambda item: item.name.casefold())
    return apps
