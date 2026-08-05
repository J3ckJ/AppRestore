from __future__ import annotations

import copy
import json
import plistlib
import re
import threading
import time
from collections import OrderedDict
from collections.abc import Iterable, Mapping
from concurrent.futures import Future, ThreadPoolExecutor, wait
from pathlib import Path
from typing import Any, Callable, TypeVar

from . import __version__
from .ipa import IpaError, validate_bundle_id
from .models import IpaMetadata, MissingApp, OffloadedApp

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
MIN_STORE_ID_DIGITS = 8
MAX_STORE_ID_DIGITS = 12
UDID_RE = re.compile(r"^[A-Za-z0-9-]{8,128}$")

_T = TypeVar("_T")
_R = TypeVar("_R")
_NETWORK_WORKERS = 6
_ORCHESTRATION_WORKERS = 4
_NETWORK_LIMIT = threading.BoundedSemaphore(_NETWORK_WORKERS)
_NETWORK_EXECUTOR = ThreadPoolExecutor(
    max_workers=_NETWORK_WORKERS,
    thread_name_prefix="apprestore-net",
)
_ORCHESTRATION_EXECUTOR = ThreadPoolExecutor(
    max_workers=_ORCHESTRATION_WORKERS,
    thread_name_prefix="apprestore-orchestrate",
)
_NETWORK_JSON_LIMIT = 4 * 1024 * 1024
_HTTP_CACHE_TTL_SECONDS = 300.0
_HTTP_NEGATIVE_CACHE_TTL_SECONDS = 5.0
_HTTP_CACHE_MAX_ENTRIES = 256
_PARALLEL_STOREFRONTS = 4
_PARALLEL_SOURCES = 4
_LOOKUP_DEADLINE_SECONDS = 10.0
_ITUNES_SEARCH_DEADLINE_SECONDS = 12.0
_CATALOG_SEARCH_DEADLINE_SECONDS = 24.0
_CATALOG_SOURCE_STAGE_SECONDS = 12.0


class CatalogError(ValueError):
    pass


class _BoundedTtlCache:
    """Small thread-safe LRU/TTL cache using a monotonic clock."""

    def __init__(
        self,
        *,
        max_entries: int,
        clock: Callable[[], float] | None = None,
    ) -> None:
        if max_entries < 1:
            raise ValueError("max_entries must be positive")
        self._max_entries = max_entries
        self._clock = clock
        self._entries: OrderedDict[str, tuple[float, Any]] = OrderedDict()
        self._lock = threading.Lock()

    def _now(self) -> float:
        return self._clock() if self._clock is not None else time.monotonic()

    def get(self, key: str) -> tuple[bool, Any]:
        now = self._now()
        with self._lock:
            entry = self._entries.get(key)
            if entry is None:
                return False, None
            expires_at, value = entry
            if expires_at <= now:
                self._entries.pop(key, None)
                return False, None
            self._entries.move_to_end(key)
            return True, value

    def put(self, key: str, value: Any, *, ttl: float) -> None:
        if ttl <= 0:
            return
        with self._lock:
            self._entries[key] = (self._now() + ttl, value)
            self._entries.move_to_end(key)
            while len(self._entries) > self._max_entries:
                self._entries.popitem(last=False)

    def clear(self) -> None:
        with self._lock:
            self._entries.clear()

    def __len__(self) -> int:
        with self._lock:
            return len(self._entries)


def _ordered_parallel_map(
    function: Callable[[_T], _R],
    values: Iterable[_T],
    *,
    max_workers: int,
    timeout: float,
    executor: ThreadPoolExecutor | None = None,
) -> list[_R | None]:
    """Return completed results in input order, bounded by workers and time."""

    items = list(values)
    if not items:
        return []
    if timeout <= 0:
        return [None] * len(items)

    call_limit = threading.BoundedSemaphore(
        max(1, min(max_workers, len(items)))
    )

    def invoke(item: _T) -> _R:
        with call_limit:
            return function(item)

    futures: list[Future[_R]] = []
    try:
        selected_executor = executor or _NETWORK_EXECUTOR
        futures = [selected_executor.submit(invoke, item) for item in items]
        completed, _pending = wait(futures, timeout=timeout)
        results: list[_R | None] = []
        for future in futures:
            if future not in completed:
                future.cancel()
                results.append(None)
                continue
            try:
                results.append(future.result())
            except Exception:
                # One storefront/archive adapter must not take down every
                # independent provider. Cancellation and KeyboardInterrupt are
                # deliberately not swallowed (they derive from BaseException).
                results.append(None)
        return results
    finally:
        for future in futures:
            if not future.done():
                future.cancel()
        # The module-level executor caps all timed-out calls together. Running
        # operations retain their own socket timeout; queued work is canceled.


def _remaining(deadline: float, *, cap: float | None = None) -> float:
    value = max(0.0, deadline - time.monotonic())
    return min(value, cap) if cap is not None else value


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
    if isinstance(value, int):
        value = str(value)
    text = str(value).strip()
    return (
        text
        if text.isascii()
        and text.isdigit()
        and len(text) >= MIN_STORE_ID_DIGITS
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


def load_imazing_app_records(candidates: list[Path]) -> dict[str, dict[str, Any]]:
    """
    Load richer iMazing Apps.plist records: name/version/store id by bundle ID.

    Later files in the candidate list do not overwrite an earlier richer record
    unless they add a missing store id / name.
    """
    result: dict[str, dict[str, Any]] = {}
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
        for raw_bundle_id, info in payload.items():
            if not isinstance(info, Mapping):
                continue
            bundle_id = str(
                info.get("CFBundleIdentifier") or raw_bundle_id
            ).strip()
            try:
                validate_bundle_id(bundle_id)
            except IpaError:
                continue
            enriched = enrich_app_record(info)
            store_id = find_store_id(enriched)
            name = _clean_text(
                enriched.get("CFBundleDisplayName")
                or enriched.get("CFBundleName")
                or enriched.get("name")
                or enriched.get("Name"),
                bundle_id,
            )
            version = _clean_text(
                enriched.get("CFBundleShortVersionString")
                or enriched.get("CFBundleVersion")
                or enriched.get("bundleVersion")
                or enriched.get("version"),
                "?",
            )
            existing = result.get(bundle_id)
            if existing is None:
                result[bundle_id] = {
                    "bundle_id": bundle_id,
                    "name": name,
                    "version": version,
                    "store_id": store_id,
                }
                continue
            if not existing.get("store_id") and store_id:
                existing["store_id"] = store_id
            if existing.get("name") in {None, "", bundle_id} and name != bundle_id:
                existing["name"] = name
            if existing.get("version") in {None, "", "?"} and version != "?":
                existing["version"] = version
    return result


def installed_bundle_ids(payload: object) -> set[str]:
    """All apps currently registered on the device, including placeholders."""
    return {bundle_id for bundle_id, _info in _app_records(payload)}


def parse_missing_apps(
    *,
    installed: set[str],
    imazing_records: Mapping[str, Mapping[str, Any]],
    local_ipas: list[IpaMetadata],
) -> list[MissingApp]:
    local_by_bundle: dict[str, IpaMetadata] = {}
    for ipa in local_ipas:
        local_by_bundle.setdefault(ipa.bundle_id, ipa)

    candidates: set[str] = set(imazing_records) | set(local_by_bundle)
    apps: list[MissingApp] = []
    for bundle_id in candidates:
        if bundle_id in installed:
            continue
        record = imazing_records.get(bundle_id) or {}
        local = local_by_bundle.get(bundle_id)
        store_id = _store_id_from_value(record.get("store_id"))
        if store_id:
            store_match = "exact-imazing"
            source = "imazing"
        elif local is not None:
            store_id = None
            store_match = "none"
            source = "local-ipa"
        else:
            store_match = "none"
            source = "imazing"
        if local is not None and source == "imazing":
            source = "imazing+local-ipa"
        name = str(record.get("name") or (local.name if local else bundle_id))
        version = str(
            record.get("version") or (local.version if local else "?")
        )
        apps.append(
            MissingApp(
                bundle_id=bundle_id,
                name=_clean_text(name, bundle_id),
                version=_clean_text(version, "?"),
                store_id=str(store_id) if store_id else None,
                store_match=store_match,
                local_ipa=local.path if local else None,
                source=source,
            )
        )
    apps.sort(key=lambda item: item.name.casefold())
    return apps


def lookup_itunes_store_id(
    bundle_id: str,
    *,
    countries: tuple[str, ...] = ("", "ru", "us", "gb", "de"),
) -> str | None:
    """Resolve a numeric App Store ID via the public iTunes Lookup API."""
    import urllib.parse

    try:
        expected = validate_bundle_id(bundle_id)
    except (IpaError, TypeError):
        return None

    urls: list[str] = []
    for country in countries:
        query: dict[str, str] = {"bundleId": expected}
        if country:
            query["country"] = country
        urls.append(
            "https://itunes.apple.com/lookup?" + urllib.parse.urlencode(query)
        )

    deadline = time.monotonic() + _LOOKUP_DEADLINE_SECONDS
    for offset in range(0, len(urls), _PARALLEL_STOREFRONTS):
        remaining = _remaining(deadline)
        if remaining <= 0:
            break
        wave = urls[offset : offset + _PARALLEL_STOREFRONTS]
        payloads = _ordered_parallel_map(
            lambda url: _http_json(
                url,
                timeout=max(0.1, _remaining(deadline, cap=8.0)),
            ),
            wave,
            max_workers=_PARALLEL_STOREFRONTS,
            timeout=remaining,
        )
        # Completion timing never changes storefront precedence.
        for payload in payloads:
            if not isinstance(payload, Mapping):
                continue
            results = payload.get("results")
            if not isinstance(results, list) or not results:
                continue
            for row in results:
                if not isinstance(row, Mapping):
                    continue
                returned_bundle = row.get("bundleId")
                if not isinstance(returned_bundle, str):
                    continue
                if returned_bundle.strip() != expected:
                    continue
                store_id = _store_id_from_value(row.get("trackId"))
                if store_id:
                    return store_id
    return None


_ITUNES_SEARCH_COUNTRIES: tuple[str, ...] = (
    "ru",
    "us",
    "kz",
    "ae",
    "tr",
    "de",
    "gb",
    "",
)
_HTTP_UA = f"AppRestore/{__version__} (+local; app discovery)"
_HTTP_JSON_CACHE = _BoundedTtlCache(max_entries=_HTTP_CACHE_MAX_ENTRIES)


def _json_payload_is_empty(payload: object) -> bool:
    if payload is None or payload == [] or payload == {}:
        return True
    if isinstance(payload, Mapping):
        results = payload.get("results")
        if isinstance(results, list) and not results:
            return True
    return False


def _response_header(response: object, name: str) -> str:
    headers = getattr(response, "headers", None)
    if headers is not None:
        try:
            value = headers.get(name)
        except (AttributeError, KeyError, TypeError):
            value = None
        if value is not None:
            return str(value).strip()
    getter = getattr(response, "getheader", None)
    if callable(getter):
        try:
            value = getter(name)
        except (KeyError, TypeError, ValueError):
            value = None
        if value is not None:
            return str(value).strip()
    return ""


def _response_media_type(response: object) -> str:
    return _response_header(response, "Content-Type").split(";", 1)[0].strip().lower()


def _read_bounded_response(response: object, *, max_bytes: int) -> bytes:
    content_length = _response_header(response, "Content-Length")
    if content_length:
        try:
            declared = int(content_length)
        except ValueError:
            declared = -1
        if declared > max_bytes:
            raise ValueError("HTTP response exceeds size limit")

    reader = getattr(response, "read", None)
    if not callable(reader):
        raise ValueError("HTTP response is not readable")
    try:
        payload = reader(max_bytes + 1)
    except TypeError:
        # Compatibility with tiny response doubles and file-like wrappers that
        # only expose read() without a size parameter.
        payload = reader()
    if not isinstance(payload, (bytes, bytearray, memoryview)):
        raise ValueError("HTTP response is not bytes")
    result = bytes(payload)
    if len(result) > max_bytes:
        raise ValueError("HTTP response exceeds size limit")
    return result


def _http_json(url: str, *, timeout: float = 20) -> Any | None:
    import urllib.error
    import urllib.request

    cached, cached_value = _HTTP_JSON_CACHE.get(url)
    if cached:
        return copy.deepcopy(cached_value)

    request = urllib.request.Request(url, headers={"User-Agent": _HTTP_UA})
    acquired = _NETWORK_LIMIT.acquire(timeout=max(0.05, float(timeout)))
    if not acquired:
        _HTTP_JSON_CACHE.put(
            url,
            None,
            ttl=_HTTP_NEGATIVE_CACHE_TTL_SECONDS,
        )
        return None
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            media_type = _response_media_type(response)
            if media_type and not (
                media_type.endswith("+json")
                or media_type
                in {
                    "application/json",
                    "application/javascript",
                    "text/json",
                    "text/javascript",
                }
            ):
                raise ValueError(f"unexpected JSON content type: {media_type}")
            raw = _read_bounded_response(
                response,
                max_bytes=_NETWORK_JSON_LIMIT,
            )
            payload = parse_json_output(raw.decode("utf-8-sig"))
            if not isinstance(payload, Mapping):
                raise ValueError("JSON response root is not an object")
    except (
        TimeoutError,
        urllib.error.URLError,
        CatalogError,
        UnicodeDecodeError,
        OSError,
        ValueError,
    ):
        _HTTP_JSON_CACHE.put(
            url,
            None,
            ttl=_HTTP_NEGATIVE_CACHE_TTL_SECONDS,
        )
        return None
    finally:
        _NETWORK_LIMIT.release()

    _HTTP_JSON_CACHE.put(
        url,
        copy.deepcopy(payload),
        ttl=(
            _HTTP_NEGATIVE_CACHE_TTL_SECONDS
            if _json_payload_is_empty(payload)
            else _HTTP_CACHE_TTL_SECONDS
        ),
    )
    return payload


def search_itunes_apps(
    term: str,
    *,
    limit: int = 10,
    countries: tuple[str, ...] = _ITUNES_SEARCH_COUNTRIES,
) -> list[dict[str, str]]:
    """
    Search currently listed App Store apps by name via the public iTunes API.

    Does not use ipatool / keychain. Delisted apps usually will not appear.
    """
    import urllib.parse

    query = str(term or "").strip()
    if not query:
        return []
    if limit < 1 or limit > 50:
        raise ValueError("limit must be between 1 and 50")

    urls: list[str] = []
    for country in countries:
        params: dict[str, str] = {
            "term": query,
            "entity": "software",
            "limit": str(limit),
        }
        if country:
            params["country"] = country
        urls.append(
            "https://itunes.apple.com/search?" + urllib.parse.urlencode(params)
        )

    deadline = time.monotonic() + _ITUNES_SEARCH_DEADLINE_SECONDS
    seen: set[str] = set()
    apps: list[dict[str, str]] = []
    for offset in range(0, len(urls), _PARALLEL_STOREFRONTS):
        remaining = _remaining(deadline)
        if remaining <= 0:
            break
        wave = urls[offset : offset + _PARALLEL_STOREFRONTS]
        payloads = _ordered_parallel_map(
            lambda url: _http_json(
                url,
                timeout=max(0.1, _remaining(deadline, cap=8.0)),
            ),
            wave,
            max_workers=_PARALLEL_STOREFRONTS,
            timeout=remaining,
        )
        for payload in payloads:
            if not isinstance(payload, Mapping):
                continue
            results = payload.get("results")
            if not isinstance(results, list):
                continue
            for row in results:
                if not isinstance(row, Mapping):
                    continue
                store_id = _store_id_from_value(row.get("trackId"))
                if not store_id or store_id in seen:
                    continue
                bundle_id = row.get("bundleId")
                if not isinstance(bundle_id, str):
                    continue
                try:
                    validated_bundle_id = validate_bundle_id(bundle_id)
                except IpaError:
                    continue
                name = row.get("trackName") or row.get("name")
                seen.add(store_id)
                apps.append(
                    {
                        "storeId": store_id,
                        "bundleId": validated_bundle_id,
                        "name": _clean_text(name, store_id),
                        "source": "itunes",
                    }
                )
                if len(apps) >= limit:
                    return apps
    return apps


def search_ipafilezone_apps(term: str, *, limit: int = 10) -> list[dict[str, str]]:
    """Search the IPA Filezone archive (may include apps missing from live storefront)."""
    import urllib.parse

    query = str(term or "").strip()
    if not query:
        return []
    if limit < 1 or limit > 50:
        raise ValueError("limit must be between 1 and 50")

    params = urllib.parse.urlencode({"q": query, "per_page": str(limit)})
    payload = _http_json(
        "https://ipafilezone.com/api/search?" + params,
        timeout=10,
    )
    if not isinstance(payload, Mapping):
        return []
    results = payload.get("results")
    if not isinstance(results, list):
        return []

    apps: list[dict[str, str]] = []
    seen: set[str] = set()
    for row in results:
        if not isinstance(row, Mapping):
            continue
        store_id = _store_id_from_value(row.get("trackId"))
        if not store_id or store_id in seen:
            continue
        name = row.get("trackName") or row.get("name") or store_id
        artist = row.get("artistName")
        label = _clean_text(name, store_id)
        if artist:
            artist_text = _clean_text(artist, "")
            if artist_text:
                label = f"{label} — {artist_text}"
        seen.add(store_id)
        apps.append(
            {
                "storeId": store_id,
                "bundleId": "",
                "name": label,
                "source": "ipafilezone",
            }
        )
        if len(apps) >= limit:
            break
    return apps


def lookup_itunes_app_by_store_id(
    store_id: str,
    *,
    countries: tuple[str, ...] = _ITUNES_SEARCH_COUNTRIES,
) -> dict[str, str] | None:
    """Resolve bundle/name for a numeric App Store ID via iTunes lookup."""
    import urllib.parse

    resolved = _store_id_from_value(store_id)
    if not resolved:
        return None
    urls: list[str] = []
    for country in countries:
        params: dict[str, str] = {"id": resolved}
        if country:
            params["country"] = country
        urls.append(
            "https://itunes.apple.com/lookup?" + urllib.parse.urlencode(params)
        )

    deadline = time.monotonic() + _LOOKUP_DEADLINE_SECONDS
    for offset in range(0, len(urls), _PARALLEL_STOREFRONTS):
        remaining = _remaining(deadline)
        if remaining <= 0:
            break
        wave = urls[offset : offset + _PARALLEL_STOREFRONTS]
        payloads = _ordered_parallel_map(
            lambda url: _http_json(
                url,
                timeout=max(0.1, _remaining(deadline, cap=8.0)),
            ),
            wave,
            max_workers=_PARALLEL_STOREFRONTS,
            timeout=remaining,
        )
        for payload in payloads:
            if not isinstance(payload, Mapping):
                continue
            results = payload.get("results")
            if not isinstance(results, list) or not results:
                continue
            for row in results:
                if not isinstance(row, Mapping):
                    continue
                track_id = _store_id_from_value(row.get("trackId"))
                if track_id != resolved:
                    continue
                bundle_id = row.get("bundleId")
                if not isinstance(bundle_id, str):
                    continue
                try:
                    validated_bundle_id = validate_bundle_id(bundle_id)
                except IpaError:
                    continue
                name = row.get("trackName") or row.get("name")
                return {
                    "storeId": track_id,
                    "bundleId": validated_bundle_id,
                    "name": _clean_text(name, track_id),
                    "source": "itunes-lookup",
                }
    return None


# Renames / sanctions aliases: query → extra search terms.
_SEARCH_ALIASES: dict[str, tuple[str, ...]] = {
    "домклик": ("дклик", "domclick"),
    "domclick": ("домклик", "дклик"),
    "дклик": ("домклик", "domclick"),
    "сбер": ("сбербанк", "сбербанк онлайн", "сбол"),
    "сбербанк": ("сбербанк онлайн", "сбол", "sbol"),
    "сбербанк онлайн": ("сбол", "sbol", "сбербанк"),
    "сбер онлайн": ("сбербанк онлайн", "сбол", "sbol"),
    "сбол": ("sbol", "сбербанк онлайн"),
    "sbol": ("сбол", "сбербанк онлайн"),
    "sberbank": ("сбербанк", "сбербанк онлайн", "сбол"),
    "sberbank online": ("сбербанк онлайн", "сбол", "sbol"),
}

# Hard hints when catalogs forget delisted / renamed apps.
_KNOWN_APP_HINTS: dict[str, tuple[dict[str, str], ...]] = {
    "домклик": (
        {
            "storeId": "1660762523",
            "bundleId": "",
            "name": "ДКлик",
            "source": "hint",
        },
        {
            "storeId": "1143031400",
            "bundleId": "",
            "name": "Домклик",
            "source": "hint",
        },
    ),
    "domclick": (
        {
            "storeId": "1660762523",
            "bundleId": "",
            "name": "ДКлик",
            "source": "hint",
        },
        {
            "storeId": "1143031400",
            "bundleId": "",
            "name": "Домклик",
            "source": "hint",
        },
    ),
    "дклик": (
        {
            "storeId": "1660762523",
            "bundleId": "",
            "name": "ДКлик",
            "source": "hint",
        },
    ),
    "сбер": (
        {
            "storeId": "492224193",
            "bundleId": "",
            "name": "Сбербанк Онлайн",
            "source": "hint",
        },
    ),
    "сбербанк": (
        {
            "storeId": "492224193",
            "bundleId": "",
            "name": "Сбербанк Онлайн",
            "source": "hint",
        },
    ),
    "сбербанк онлайн": (
        {
            "storeId": "492224193",
            "bundleId": "",
            "name": "Сбербанк Онлайн",
            "source": "hint",
        },
    ),
    "сбер онлайн": (
        {
            "storeId": "492224193",
            "bundleId": "",
            "name": "Сбербанк Онлайн",
            "source": "hint",
        },
    ),
    "сбол": (
        {
            "storeId": "492224193",
            "bundleId": "",
            "name": "Сбербанк Онлайн",
            "source": "hint",
        },
    ),
    "sbol": (
        {
            "storeId": "492224193",
            "bundleId": "",
            "name": "Сбербанк Онлайн",
            "source": "hint",
        },
    ),
    "sberbank": (
        {
            "storeId": "492224193",
            "bundleId": "",
            "name": "Сбербанк Онлайн",
            "source": "hint",
        },
    ),
    "sberbank online": (
        {
            "storeId": "492224193",
            "bundleId": "",
            "name": "Сбербанк Онлайн",
            "source": "hint",
        },
    ),
}


def _search_query_variants(term: str) -> list[str]:
    primary = str(term or "").strip()
    if not primary:
        return []
    variants = [primary]
    seen = {primary.casefold()}
    for alias in _SEARCH_ALIASES.get(primary.casefold(), ()):
        text = alias.strip()
        key = text.casefold()
        if not text or key in seen:
            continue
        seen.add(key)
        variants.append(text)
    return variants


def search_app_catalogs(term: str, *, limit: int = 10) -> list[dict[str, str]]:
    """
    Merge live iTunes search with IPA Filezone archive.

    Also expands known aliases (e.g. Домклик → ДКлик) and injects hard
    store-id hints for frequently delisted apps.
    """
    query = str(term or "").strip()
    if not query:
        return []
    if limit < 1 or limit > 50:
        raise ValueError("limit must be between 1 and 50")

    deadline = time.monotonic() + _CATALOG_SEARCH_DEADLINE_SECONDS
    variants = _search_query_variants(query)
    per_source = max(limit, min(20, limit * 2))
    rows: list[dict[str, str]] = []
    source_tasks: list[tuple[str, str]] = []
    for variant in variants:
        source_tasks.append(("itunes", variant))
        source_tasks.append(("ipafilezone", variant))

    def _search_source(task: tuple[str, str]) -> list[dict[str, str]]:
        source, variant = task
        if source == "itunes":
            return search_itunes_apps(variant, limit=per_source)
        return search_ipafilezone_apps(variant, limit=per_source)

    source_results = _ordered_parallel_map(
        _search_source,
        source_tasks,
        max_workers=_PARALLEL_SOURCES,
        timeout=min(
            _CATALOG_SOURCE_STAGE_SECONDS,
            _remaining(deadline),
        ),
        executor=_ORCHESTRATION_EXECUTOR,
    )
    for result in source_results:
        if result:
            rows.extend(result)
    for hint in _KNOWN_APP_HINTS.get(query.casefold(), ()):
        rows.append(dict(hint))

    # If catalogs/hints are thin, discover IDs the same way a human does:
    # web search for apps.apple.com links.
    query_fold = query.casefold()
    strong_before_web = False
    for row in rows:
        name = (row.get("name") or "").casefold()
        if name == query_fold or (
            len(query_fold) >= 4 and query_fold in name
        ):
            strong_before_web = True
            break
        if "hint" in (row.get("source") or "").split("+"):
            strong_before_web = True
            break
    if not strong_before_web and _remaining(deadline) > 1.0:
        import sys

        from .web_discovery import search_web_app_store_ids_safe

        print("  веб-поиск App Store ID…", file=sys.stderr)
        rows.extend(search_web_app_store_ids_safe(query, limit=max(5, limit)))

    merged: dict[str, dict[str, str]] = {}
    order: list[str] = []
    for row in rows:
        store_id = row.get("storeId") or ""
        if not store_id:
            continue
        if store_id not in merged:
            merged[store_id] = dict(row)
            order.append(store_id)
            continue
        existing = merged[store_id]
        sources = {
            part
            for part in (existing.get("source") or "").split("+")
            if part
        }
        sources.update(
            part for part in (row.get("source") or "").split("+") if part
        )
        existing["source"] = "+".join(sorted(sources))
        if not existing.get("bundleId") and row.get("bundleId"):
            existing["bundleId"] = row["bundleId"]
        # Prefer shorter/clean itunes name over archive "name — artist".
        if "itunes" in sources and row.get("source") == "itunes" and row.get("name"):
            existing["name"] = row["name"]
        elif existing.get("source") == "hint" and row.get("name") and row.get("source") != "hint":
            existing["name"] = row["name"]

    apps = [merged[store_id] for store_id in order]
    # Fill bundle IDs for archive-only hits while the listing still resolves.
    lookup_targets = [
        (index, app["storeId"])
        for index, app in enumerate(apps[: max(limit * 2, limit)])
        if not app.get("bundleId")
    ]
    lookup_results = _ordered_parallel_map(
        lambda target: lookup_itunes_app_by_store_id(target[1]),
        lookup_targets,
        max_workers=_PARALLEL_STOREFRONTS,
        timeout=_remaining(deadline),
        executor=_ORCHESTRATION_EXECUTOR,
    )
    for target, looked in zip(lookup_targets, lookup_results):
        if not looked:
            continue
        app = apps[target[0]]
        if looked.get("bundleId"):
            app["bundleId"] = looked["bundleId"]
        if looked.get("name") and "itunes" not in (app.get("source") or ""):
            app["name"] = looked["name"]
        sources = {
            part for part in (app.get("source") or "").split("+") if part
        }
        sources.add("itunes-lookup")
        app["source"] = "+".join(sorted(sources))

    needles = {variant.casefold() for variant in variants}
    # Cyrillic queries (сбол/домклик) should not rank-match Latin lookalikes.
    rank_needles = set(needles)
    if any(ord(character) > 127 for character in query):
        rank_needles -= {
            "sbol",
            "sberbank",
            "sberbank online",
            "domclick",
        }

    def _name_tier(name: str, pool: set[str] | None = None) -> int:
        best = 9
        for needle in pool if pool is not None else rank_needles:
            if name == needle:
                best = min(best, 0)
            elif name.startswith(needle):
                best = min(best, 1)
            elif needle in name.split() or f" {needle} " in f" {name} ":
                best = min(best, 2)
            elif needle in name:
                best = min(best, 3)
        return best

    def _rank(app: dict[str, str]) -> tuple[int, int, int, str]:
        name = (app.get("name") or "").casefold()
        tier = _name_tier(name)
        source = app.get("source") or ""
        # Keep explicit hints / web discoveries even when title is a rename.
        if "hint" in source.split("+") or "web" in source.split("+"):
            tier = min(tier, 2)
        archive_bonus = 0 if "ipafilezone" in source else 1
        hint_bonus = 0 if ("hint" in source or "web" in source) else 1
        return (tier, hint_bonus, archive_bonus, name)

    apps.sort(key=_rank)
    if apps and _rank(apps[0])[0] <= 3:
        apps = [app for app in apps if _rank(app)[0] <= 3]
    else:
        # Avoid dumping unrelated App Store suggestions when nothing matches.
        apps = []
    return apps[:limit]


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
        number = int(str(value or 0))
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
            raw_bundle_id = value.get("CFBundleIdentifier")
            if raw_bundle_id is None:
                continue
            if not isinstance(raw_bundle_id, str):
                raise CatalogError("application bundle identifier is not a string")
            bundle_id = raw_bundle_id
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
        store_id: str | None
        if direct_store_id:
            store_id = direct_store_id
            store_match = "device"
        else:
            store_id = _store_id_from_value(imazing_catalog.get(bundle_id))
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
