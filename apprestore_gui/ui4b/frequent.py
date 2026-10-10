"""«Часто ищут»: the vetted list shown in «Найти» before anything is typed.

Data only (``data/frequent.json``: track_id, name, developer, source), so Макс's
§1.13 table can replace it without code changes. Rows look like normal results:
name, the real developer from the data, the usual status button (same gate).
Icons come from Apple (icons_cache) or Theme.iconPlaceholder; nothing else.
"""

from __future__ import annotations

import json
from pathlib import Path

DATA = Path(__file__).with_name("data") / "frequent.json"
TITLE = "Часто ищут"
#: bank-named clones / unconfirmed links (Лена): never in this list, whatever the file says
EXCLUDED = frozenset({"6749962031", "6755181069", "6473656113"})


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
        developer = str(row.get("developer") or "").strip()
        # a row without a developer is not shown (the developer is always visible)
        if not sid.isdigit() or sid in EXCLUDED or sid in seen or not name or not developer:
            continue
        seen.add(sid)
        out.append({"storeId": sid, "name": name, "developer": developer})
    return out
