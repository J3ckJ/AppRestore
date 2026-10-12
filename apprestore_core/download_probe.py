"""Allowlisted download diagnostics: never expose raw ipatool response data."""

from __future__ import annotations

import re
from collections.abc import Mapping

CHANNELS = frozenset({
    "volume_unpinned", "legacy_volume_unpinned", "redownload_unpinned", "minimal_redownload_unpinned", "version_lookup",
    "volume_pinned", "redownload_pinned", "update_pinned", "ent_pinned",
    "update_unpinned", "ent_unpinned",
})
STATUSES = frozenset({
    "network_or_protocol_error", "cancelled_or_timeout", "license_required", "unavailable",
    "authentication_required", "store_mismatch", "apple_refused", "http_error", "empty",
    "invalid_response", "identity_mismatch", "metadata_only", "package_and_license_offered",
    "country_unavailable", "version_found", "version_unavailable", "endpoint_unavailable",
    "invalid_endpoint", "invalid_redirect", "redirect_limit",
})


def positive_id(value: object) -> str:
    text = str(value) if not isinstance(value, bool) else ""
    if not re.fullmatch(r"[0-9]{1,18}", text) or int(text) <= 0:
        raise ValueError("expected a positive numeric identifier")
    return text


def sanitize_download_probe(payload: object, store_id: str) -> dict[str, object]:
    """Project only known scalars; a mismatched app is not a usable result."""

    expected = positive_id(store_id)
    if not isinstance(payload, Mapping) or positive_id(payload.get("appId")) != expected:
        raise ValueError("download probe returned another app")
    country = payload.get("country")
    country = country.upper() if isinstance(country, str) else ""
    if not re.fullmatch(r"[A-Z]{2}", country):
        country = ""
    rows = payload.get("channels")
    if not isinstance(rows, list) or not 1 <= len(rows) <= len(CHANNELS):
        raise ValueError("invalid download probe channels")
    channels: list[dict[str, object]] = []
    seen: set[str] = set()
    for row in rows:
        if not isinstance(row, Mapping):
            raise ValueError("invalid download probe channel")
        channel = row.get("channel")
        if not isinstance(channel, str) or channel not in CHANNELS or channel in seen:
            raise ValueError("invalid download probe channel")
        seen.add(channel)
        status = row.get("status")
        if not isinstance(status, str) or status not in STATUSES:
            status = "invalid_response"
        clean: dict[str, object] = {"channel": channel, "status": status}
        http_status = row.get("httpStatus")
        if type(http_status) is int and 100 <= http_status <= 599:
            clean["httpStatus"] = http_status
        failure = row.get("failureType")
        if isinstance(failure, str) and re.fullmatch(r"-?[0-9]{1,10}", failure):
            clean["failureType"] = failure
        version = row.get("versionId")
        try:
            clean["versionId"] = positive_id(version)
        except ValueError:
            pass
        matched = row.get("identityMatches") is True
        clean.update(
            identityMatches=matched,
            packageOffered=matched and row.get("packageOffered") is True,
            licenseMaterial=matched and row.get("licenseMaterial") is True,
        )
        if status == "package_and_license_offered" and not (clean["packageOffered"] and clean["licenseMaterial"]):
            clean["status"] = "invalid_response"
        channels.append(clean)
    return {"appId": expected, "country": country, "channels": channels}
