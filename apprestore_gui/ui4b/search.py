"""«Нет того, что искали?» in 4b: App Store (iTunes search/lookup) and the
account's purchases only. No third-party IPA catalogues here (the CLI and the
old window keep theirs; Евгений decides)."""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping

from .selection import parse_query

SEARCH_HINT = "Поищем в App Store, по названию или номеру, и в покупках вашего Apple ID."


def search_store(
    term: str,
    purchases: Iterable[Mapping[str, object]] = (),
    *,
    itunes_search: Callable[..., list[dict[str, str]]] | None = None,
    itunes_lookup: Callable[..., dict[str, str] | None] | None = None,
    limit: int = 10,
) -> list[dict[str, str]]:
    """Rows {storeId, name, source} — purchases first, then App Store; no duplicates."""

    if itunes_search is None or itunes_lookup is None:
        from apprestore_core.catalog import lookup_itunes_app_by_store_id, search_itunes_apps

        itunes_search = itunes_search or search_itunes_apps
        itunes_lookup = itunes_lookup or lookup_itunes_app_by_store_id
    words, store_id = parse_query(term)
    out: list[dict[str, str]] = []
    seen: set[str] = set()

    def add(sid: object, name: object, source: str) -> None:
        sid = str(sid or "")
        if sid and sid.isdigit() and sid not in seen and len(out) < limit:
            seen.add(sid)
            out.append({"storeId": sid, "name": str(name or sid), "source": source})

    for row in purchases:
        sid = str(row.get("trackId") or row.get("storeId") or "")
        name = str(row.get("name") or "")
        if (store_id and sid == store_id) or (words and words in name.casefold()):
            add(sid, name, "purchases")
    try:
        if store_id:
            found = itunes_lookup(store_id)
            if found:
                add(found.get("storeId") or store_id, found.get("name"), "appstore")
        elif words:
            for row in itunes_search(term, limit=limit):
                add(row.get("storeId"), row.get("name"), "appstore")
    except Exception:  # noqa: BLE001 - no network: purchases still answer
        pass
    return out
