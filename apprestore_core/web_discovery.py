"""Discover App Store IDs from public web search results."""

from __future__ import annotations

import html as html_lib
import re
import urllib.parse

_APP_STORE_ID_RE = re.compile(
    r"apps\.apple\.com/[^\"'\s<>\\]*?/id(\d{8,12})",
    re.IGNORECASE,
)
_LOOSE_ID_RE = re.compile(
    r"(?:apps\.apple\.com[^\"'\s<>\\]*|/app/[^\"'\s<>\\]*)/id(\d{8,12})",
    re.I,
)
_HTTP_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)

# Enough for slug matching (spasibo / sberbank / domclick).
_CYR_TO_LAT = str.maketrans(
    {
        "а": "a",
        "б": "b",
        "в": "v",
        "г": "g",
        "д": "d",
        "е": "e",
        "ё": "e",
        "ж": "zh",
        "з": "z",
        "и": "i",
        "й": "y",
        "к": "k",
        "л": "l",
        "м": "m",
        "н": "n",
        "о": "o",
        "п": "p",
        "р": "r",
        "с": "s",
        "т": "t",
        "у": "u",
        "ф": "f",
        "х": "h",
        "ц": "ts",
        "ч": "ch",
        "ш": "sh",
        "щ": "sch",
        "ъ": "",
        "ы": "y",
        "ь": "",
        "э": "e",
        "ю": "yu",
        "я": "ya",
    }
)


def _latinize(text: str) -> str:
    return text.casefold().translate(_CYR_TO_LAT)


def _http_text(url: str, *, timeout: float = 20) -> str | None:
    import urllib.error
    import urllib.request

    request = urllib.request.Request(url, headers={"User-Agent": _HTTP_UA})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.read().decode("utf-8", "replace")
    except (
        TimeoutError,
        urllib.error.URLError,
        OSError,
        ValueError,
    ):
        return None


def _decode_duckduckgo_redirect(url: str) -> str:
    try:
        parsed = urllib.parse.urlparse(url)
        if "duckduckgo.com" not in (parsed.netloc or ""):
            return url
        query = urllib.parse.parse_qs(parsed.query)
        for key in ("uddg", "u"):
            values = query.get(key)
            if values:
                return urllib.parse.unquote(values[0])
    except (TypeError, ValueError):
        return url
    return url


def _candidate_ids_from_html(body: str, term: str) -> list[tuple[int, str]]:
    from .catalog import _store_id_from_value

    needle = term.casefold().strip()
    latin_needle = _latinize(needle).replace(" ", "")
    decoded = html_lib.unescape(body)
    decoded = re.sub(
        r"https?://[^\"'\s]*duckduckgo\.com/l/\?[^\"'\s]+",
        lambda match: _decode_duckduckgo_redirect(match.group(0)),
        decoded,
    )
    scored: dict[str, int] = {}
    for regex in (_APP_STORE_ID_RE, _LOOSE_ID_RE):
        for match in regex.finditer(decoded):
            store_id = match.group(1)
            if not _store_id_from_value(store_id):
                continue
            start = max(0, match.start() - 40)
            end = min(len(decoded), match.end() + 48)
            context = decoded[start:end].casefold()
            slug = match.group(0).casefold()
            compact_slug = re.sub(r"[^a-z0-9а-яё]+", "", slug)
            compact_slug_latin = _latinize(compact_slug)
            score = 0
            if needle and needle in context:
                score += 12
            if latin_needle and latin_needle in compact_slug_latin:
                score += 10
            elif latin_needle and len(latin_needle) >= 6:
                # Compound names: СберСпасибо → slug spasibo-ot-sberbanka.
                fragments = {
                    latin_needle,
                    latin_needle[:6],
                    latin_needle[-6:],
                    latin_needle[4:],
                }
                if any(
                    len(fragment) >= 5 and fragment in compact_slug_latin
                    for fragment in fragments
                ):
                    score += 8
            for token in re.findall(r"[a-zа-яё]{4,}", needle):
                latin_token = _latinize(token)
                if token in context or token in compact_slug:
                    score += 4
                    break
                if latin_token and (
                    latin_token in compact_slug_latin
                    or latin_token in _latinize(context)
                ):
                    score += 4
                    break
            if score <= 0:
                continue
            if "apps.apple.com" in slug:
                score += 2
            scored[store_id] = max(scored.get(store_id, 0), score)
    return sorted(
        ((score, store_id) for store_id, score in scored.items()),
        key=lambda item: (-item[0], item[1]),
    )


def _search_pages(term: str) -> list[str]:
    query = term.strip()
    if not query:
        return []
    variants = (
        f"{query} site:apps.apple.com",
        f"{query} apps.apple.com",
        f"{query} itunes.apple.com id",
        f"{_latinize(query)} apps.apple.com",
    )
    urls: list[str] = []
    for variant in variants:
        encoded = urllib.parse.quote_plus(variant)
        urls.extend(
            (
                f"https://search.brave.com/search?q={encoded}",
                f"https://html.duckduckgo.com/html/?q={encoded}",
                f"https://lite.duckduckgo.com/lite/?q={encoded}",
            )
        )

    pages: list[str] = []
    strong_hit = False
    for url in urls:
        if strong_hit:
            break
        body = _http_text(url, timeout=18)
        if not body:
            continue
        lowered = body.casefold()
        if "apps.apple.com" not in lowered and "/id" not in lowered:
            continue
        pages.append(body)
        top = _candidate_ids_from_html(body, query)
        if top and top[0][0] >= 10:
            strong_hit = True
    return pages


def _names_related(query: str, name: str) -> bool:
    q = query.casefold().strip()
    n = name.casefold().strip()
    if not q or not n:
        return False
    if q == n or q in n or n in q:
        return True
    q_lat = _latinize(q).replace(" ", "")
    n_lat = _latinize(n).replace(" ", "")
    if q_lat and (q_lat in n_lat or n_lat in q_lat):
        return True
    tokens = re.findall(r"[a-zа-яё]{4,}", q)
    for token in tokens:
        if token in n or _latinize(token) in n_lat:
            return True
    return False


def search_web_app_store_ids(term: str, *, limit: int = 5) -> list[dict[str, str]]:
    """
    Find App Store IDs via public web search pages.

    This mirrors the manual "google the app + apps.apple.com" workflow.
    """
    query = str(term or "").strip()
    if not query:
        return []
    if limit < 1 or limit > 20:
        raise ValueError("limit must be between 1 and 20")

    ranked: dict[str, int] = {}
    for page in _search_pages(query):
        for score, store_id in _candidate_ids_from_html(page, query):
            ranked[store_id] = max(ranked.get(store_id, 0), score)

    if not ranked:
        return []

    from .catalog import _clean_text, lookup_itunes_app_by_store_id

    ordered = sorted(ranked.items(), key=lambda item: (-item[1], item[0]))
    apps: list[dict[str, str]] = []
    for store_id, score in ordered:
        if score < 6:
            continue
        looked = lookup_itunes_app_by_store_id(store_id)
        if looked:
            # Live listings must still look like the queried app.
            if not _names_related(query, looked.get("name") or ""):
                continue
            apps.append(
                {
                    "storeId": looked["storeId"],
                    "bundleId": looked.get("bundleId") or "",
                    "name": looked.get("name") or query,
                    "source": "web+itunes-lookup",
                }
            )
        else:
            # Delisted apps often have no iTunes lookup; keep web hit.
            apps.append(
                {
                    "storeId": store_id,
                    "bundleId": "",
                    "name": _clean_text(query, store_id),
                    "source": "web",
                }
            )
        if len(apps) >= limit:
            break
    return apps


_WEB_CACHE: dict[str, list[dict[str, str]]] = {}


def search_web_app_store_ids_safe(term: str, *, limit: int = 5) -> list[dict[str, str]]:
    """Same as search_web_app_store_ids, but never raises for network/parse issues."""
    key = f"{str(term or '').strip().casefold()}|{limit}"
    cached = _WEB_CACHE.get(key)
    if cached is not None:
        return [dict(item) for item in cached]
    try:
        result = search_web_app_store_ids(term, limit=limit)
        if not result:
            # One retry helps when the first backend page is a captcha interstitial.
            result = search_web_app_store_ids(term, limit=limit)
    except Exception:
        result = []
    _WEB_CACHE[key] = [dict(item) for item in result]
    return [dict(item) for item in result]
