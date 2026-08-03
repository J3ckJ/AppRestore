from __future__ import annotations

import json
import plistlib
import re
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any

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
        store_id = record.get("store_id")
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
_HTTP_UA = "AppRestore/0.1 (+local; app discovery)"


def _http_json(url: str, *, timeout: float = 20) -> Any | None:
    import urllib.error
    import urllib.request

    request = urllib.request.Request(url, headers={"User-Agent": _HTTP_UA})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))
    except (
        TimeoutError,
        urllib.error.URLError,
        json.JSONDecodeError,
        OSError,
        ValueError,
    ):
        return None


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

    seen: set[str] = set()
    apps: list[dict[str, str]] = []
    for country in countries:
        params: dict[str, str] = {
            "term": query,
            "entity": "software",
            "limit": str(limit),
        }
        if country:
            params["country"] = country
        url = "https://itunes.apple.com/search?" + urllib.parse.urlencode(params)
        payload = _http_json(url)
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
            name = row.get("trackName") or row.get("name")
            seen.add(store_id)
            apps.append(
                {
                    "storeId": store_id,
                    "bundleId": str(bundle_id).strip() if bundle_id else "",
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
        timeout=25,
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
    for country in countries:
        params: dict[str, str] = {"id": resolved}
        if country:
            params["country"] = country
        url = "https://itunes.apple.com/lookup?" + urllib.parse.urlencode(params)
        payload = _http_json(url, timeout=15)
        if not isinstance(payload, Mapping):
            continue
        results = payload.get("results")
        if not isinstance(results, list) or not results:
            continue
        first = results[0]
        if not isinstance(first, Mapping):
            continue
        track_id = _store_id_from_value(first.get("trackId")) or resolved
        bundle_id = first.get("bundleId")
        name = first.get("trackName") or first.get("name")
        return {
            "storeId": track_id,
            "bundleId": str(bundle_id).strip() if bundle_id else "",
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

    variants = _search_query_variants(query)
    per_source = max(limit, min(20, limit * 2))
    rows: list[dict[str, str]] = []
    for variant in variants:
        rows.extend(search_itunes_apps(variant, limit=per_source))
        rows.extend(search_ipafilezone_apps(variant, limit=per_source))
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
    if not strong_before_web:
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
    for app in apps[: max(limit * 2, limit)]:
        if app.get("bundleId"):
            continue
        looked = lookup_itunes_app_by_store_id(app["storeId"])
        if not looked:
            continue
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
