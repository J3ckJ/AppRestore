"""«Часто ищут»: the list shown in «Найти» before anything is typed.

Eugene's decision 10.10 (his accepted risk, recorded by Лена in LEGAL.md): the 33
entries of ``popular_apps.POPULAR_APPS`` at b3f869a, same ids, labels and order.
Data only (``data/frequent.json``: track_id, name, detail, developer), so Макс's
table can fill ``developer`` without code changes. Rows look like normal results:
name, the developer only when known from trusted data (or the App Store lookup at
runtime) — otherwise no developer line, never invented — and the usual status
button (same gate). Icons come from Apple (icons_cache) or Theme.iconPlaceholder;
no brand letters, colours or website icons.
"""

from __future__ import annotations

import json
from pathlib import Path

DATA = Path(__file__).with_name("data") / "frequent.json"
TITLE = "Часто ищут"


def load(path: Path = DATA) -> list[dict[str, str]]:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    out: list[dict[str, str]] = []
    seen: set[str] = set()
    for row in raw.get("apps", []) if isinstance(raw, dict) else []:
        if not isinstance(row, dict):
            continue
        sid = str(row.get("track_id") or "").strip()
        name = str(row.get("name") or "").strip()
        if not sid.isdigit() or sid in seen or not name:
            continue
        seen.add(sid)
        out.append({
            "storeId": sid,
            "name": name,
            "detail": str(row.get("detail") or "").strip(),
            "developer": str(row.get("developer") or "").strip(),
        })
    return out
