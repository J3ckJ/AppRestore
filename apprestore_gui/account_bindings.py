"""Which Apple ID belongs to which iPhone.

The password is never stored. The binding remembers the email for that
device. The encrypted session itself lives in the account vault.
"""

from __future__ import annotations

import json
from pathlib import Path


def bindings_path() -> Path:
    return Path.home() / ".apprestore" / "device-accounts.json"


def load_bindings(path: Path | None = None) -> dict[str, str]:
    file = path or bindings_path()
    try:
        payload = json.loads(file.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    raw = payload.get("bindings") if isinstance(payload, dict) else None
    if not isinstance(raw, dict):
        return {}
    return {
        str(udid): str(email).strip()
        for udid, email in raw.items()
        if str(udid).strip() and isinstance(email, str) and "@" in email
    }


def remember_binding(udid: str, email: str, path: Path | None = None) -> None:
    udid = udid.strip()
    email = email.strip()
    if not udid or "@" not in email:
        return
    file = path or bindings_path()
    current = load_bindings(file)
    current[udid] = email
    file.parent.mkdir(parents=True, exist_ok=True)
    file.write_text(
        json.dumps({"bindings": current}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def account_email(payload: object) -> str:
    if not isinstance(payload, dict):
        return ""
    for key in ("email", "appleId", "appleID", "account"):
        value = payload.get(key)
        if isinstance(value, str) and "@" in value:
            return value.strip()
    return ""
