"""Local memory of App Store IDs discovered during restores/searches."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from .paths import known_apps_path

_STORE_ID_RE = re.compile(r"(?:id)?(\d{8,12})\b", re.IGNORECASE)
_URL_ID_RE = re.compile(
    r"apps\.apple\.com/[^?\s#]*/id(\d{8,12})",
    re.IGNORECASE,
)


def parse_app_store_id(value: str) -> str | None:
    """Extract numeric App Store ID from bare id, idNNNN, or App Store URL."""
    text = (value or "").strip()
    if not text:
        return None
    url_match = _URL_ID_RE.search(text)
    if url_match:
        return url_match.group(1)
    if text.isdigit() and 8 <= len(text) <= 12:
        return text
    bare = _STORE_ID_RE.fullmatch(text)
    if bare:
        return bare.group(1)
    return None


def load_known_apps(path: Path | None = None) -> list[dict[str, Any]]:
    target = path or known_apps_path()
    if not target.is_file():
        return []
    try:
        payload = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return []
    if not isinstance(payload, dict):
        return []
    apps = payload.get("apps")
    if not isinstance(apps, list):
        return []
    result: list[dict[str, Any]] = []
    for item in apps:
        if not isinstance(item, dict):
            continue
        store_id = str(item.get("storeId") or "").strip()
        bundle_id = str(item.get("bundleId") or "").strip()
        if not store_id.isdigit():
            continue
        result.append(
            {
                "storeId": store_id,
                "bundleId": bundle_id or None,
                "name": str(item.get("name") or bundle_id or store_id),
                "version": str(item.get("version") or "?"),
            }
        )
    return result


def remember_known_app(
    *,
    store_id: str,
    bundle_id: str | None = None,
    name: str | None = None,
    version: str | None = None,
    path: Path | None = None,
) -> None:
    if not store_id.isdigit() or int(store_id) <= 0:
        return
    store_text = str(store_id)
    bundle_text = str(bundle_id).strip() if bundle_id else None
    name_text = str(name).strip() if name else None
    version_text = str(version).strip() if version else None
    target = path or known_apps_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    apps = load_known_apps(target)
    updated = False
    for item in apps:
        if item.get("storeId") == store_text:
            if bundle_text:
                item["bundleId"] = bundle_text
            if name_text:
                item["name"] = name_text
            if version_text:
                item["version"] = version_text
            updated = True
            break
    if not updated:
        apps.append(
            {
                "storeId": store_text,
                "bundleId": bundle_text,
                "name": name_text or bundle_text or store_text,
                "version": version_text or "?",
            }
        )
    apps.sort(
        key=lambda item: str(item.get("name") or item.get("storeId") or "").casefold()
    )
    payload = {"schemaVersion": 1, "apps": apps}
    temporary = target.with_suffix(".tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    temporary.replace(target)
