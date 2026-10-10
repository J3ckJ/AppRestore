"""Тесты delisted_search — без сети: HTTP подменён, сокеты заблокированы."""
import gzip
import json
import logging
import re
import socket
import urllib.parse
import urllib.request

import pytest

try:
    from apprestore_core import delisted_search as ds
except ImportError:
    import delisted_search as ds

SIRIUS, VTB, ALFA, DELIM, DRIVE = 6749962031, 472951966, 353127685, 6739035108, 6760469916
SBER, TINK = 492224193, 455652438          # оригиналы, добавлены по §1.13 (Лена, вопрос 2)
TIER_A = {VTB, ALFA, DELIM, DRIVE, SBER, TINK}


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def boom(*a, **k):
        raise AssertionError("реальная сеть в тестах запрещена")
    monkeypatch.setattr(socket.socket, "connect", boom)
    monkeypatch.setattr(socket, "create_connection", boom)


class FakeClock:
    def __init__(self):
        self.t = 1000.0
        self.slept = []

    def __call__(self):
        return self.t

    def sleep(self, s):
        self.slept.append(s)
        self.t += s


# --- фикстуры страниц ---------------------------------------------------------
def page_svelte(tid, name, dev, bundle, decoy_bundle="com.other.app", decoy_id=1111111111,
                image=None):
    ld = {"@context": "https://schema.org", "@type": "SoftwareApplication", "name": name,
          "image": image or ("https://is1-ssl.mzstatic.com/image/thumb/Purple211/v4/aa/bb/"
                             "AppIcon-0-0-1x_U007emarketing-0-8-0-85-220.png/1200x630wa.png"),
          "author": {"@type": "Organization", "name": dev,
                     "url": "https://apps.apple.com/us/developer/x/id1"}}
    server = {"data": [{"pageId": str(tid), "shelves": [
        {"items": [{"adamId": str(decoy_id), "bundleId": decoy_bundle}]}],
        "titleOfferDisplayProperties": {"adamId": str(tid), "bundleId": bundle}}]}
    return (f'<html><head><link rel="canonical" href="https://apps.apple.com/us/app/x/id{tid}">'
            f'<script id=software-application type="application/ld+json">{json.dumps(ld)}</script>'
            f'</head><body><a href="https://apps.apple.com/us/app/y/id{decoy_id}">y</a>'
            f'<script type="application/json" id="serialized-server-data">{json.dumps(server)}'
            f'</script></body></html>').encode()


def page_ember_shoebox(tid, name, dev, bundle):
    ld = {"@type": "SoftwareApplication", "name": name, "author": {"name": dev},
          "image": "https://is3-ssl.mzstatic.com/image/thumb/Purple126/v4/c4/AppIcon.png/1200x630wa.png"}
    inner = {"d": [{"id": str(tid), "type": "apps", "attributes": {
        "platformAttributes": {"ios": {"bundleId": bundle}}}}]}
    other = {"d": [{"id": "222", "type": "apps", "attributes": {
        "platformAttributes": {"ios": {"bundleId": "ru.decoy.bank"}}}}]}
    shoebox = {"k1": json.dumps(inner), "k2": json.dumps(other)}
    return (f'<link rel="canonical" href="https://apps.apple.com/ru/app/x/id{tid}">'
            f'<script type="application/ld+json">{json.dumps(ld, ensure_ascii=False)}</script>'
            f'<script type="fastboot/shoebox" id="shoebox-media-api-cache-apps">'
            f'{json.dumps(shoebox)}</script>').encode()


def page_ember_store(tid, name, dev, bundle):
    ld = {"@type": "SoftwareApplication", "name": name, "author": {"name": dev}}
    store = {str(tid): {"data": {"id": str(tid), "type": "media/app", "relationships": {
        "platforms": {"data": [{"id": "ios", "attributes": {"bundleId": bundle}}]}}},
        "included": [{"id": "999", "attributes": {"bundleId": "decoy.included"}}]}}
    return (f'<link href="https://apps.apple.com/ru/app/x/id{tid}">'
            f'<script type="application/ld+json">{json.dumps(ld)}</script>'
            f'<script type="fastboot/shoebox" id="shoebox-ember-data-store">{json.dumps(store)}'
            f'</script>').encode()


JUNK = ('https://apps.apple.com/ru/app/%D0%B2%D1%82%D0%B1/%7B%22appVersion%22%3A1%2C%22MEDIA_API'
        '%22%3A%7B%22token%22%3A%22eyJhbGciOi%22%7D%7D')


class FakeArchive:
    """CDX: (cc, slug) -> rows; снимки: original -> bytes. Записывает все вызовы."""

    def __init__(self, cdx=None, pages=None, fail=None):
        self.cdx = cdx or {}
        self.pages = pages or {}
        self.fail = list(fail or [])   # статусы, которые вернуть по очереди перед успехом
        self.calls = []

    def __call__(self, url, headers, timeout):
        self.calls.append((url, dict(headers)))
        if self.fail:
            raise ds.WaybackError("HTTP", self.fail.pop(0))
        p = urllib.parse.urlparse(url)
        assert p.scheme == "https" and p.netloc == "web.archive.org"
        if p.path == "/cdx/search/cdx":
            q = urllib.parse.parse_qs(p.query)
            assert set(q) == {"url", "matchType", "collapse", "fl", "filter", "output", "limit"}
            m = re.match(r"^apps\.apple\.com/([a-z]{2})/app/(.+)$", q["url"][0])
            assert m, q["url"][0]
            rows = self.cdx.get((m.group(1), urllib.parse.unquote(m.group(2))), [])
            body = [["original", "timestamp", "statuscode"]] + rows if rows else []
            return json.dumps(body).encode()
        m = re.match(r"^/web/(\d{14})id_/(.+)$", p.path + (("?" + p.query) if p.query else ""))
        assert m, url
        return gzip.compress(self.pages[m.group(2)])


def mk(fetch, clock=None, **kw):
    clock = clock or FakeClock()
    return ds.DelistedSearch(fetcher=fetch, clock=clock, sleep=clock.sleep, **kw), clock


# --- встроенный список -------------------------------------------------------
@pytest.mark.parametrize("q", ["Сириус", "сириус", "СИРИУС", "Cириус", "sirius", "cbhbec", "сирус"])
def test_builtin_sirius_variants(q):
    # Сириус — уровень B (нет прямой ссылки банка), поэтому только при strict=False.
    hits = ds.search_builtin(q, strict=False)
    assert hits and hits[0].track_id == SIRIUS
    assert hits[0].brand == "ВТБ Онлайн" and hits[0].source is ds.HitSource.BUILTIN


def test_builtin_layout_vtb():
    ids = [h.track_id for h in ds.search_builtin("dn,")]       # «втб» в EN-раскладке
    assert ids == [VTB]                                        # strict: Сириуса нет
    ids = [h.track_id for h in ds.search_builtin("dn,", strict=False)]
    assert ids[:2] == [VTB, SIRIUS]
    assert {h.brand for h in ds.search_builtin("ыиук", strict=False)} >= {"СберБанк Онлайн"}
    # strict: из Сбера только оригинал (уровень A); «Активы Онлайн» — уровень B, выключен
    assert [h.track_id for h in ds.search_builtin("ыиук")] == [SBER]


def test_builtin_bank_synonym_and_order():
    hits = ds.search_builtin("альфа банк")
    assert [h.track_id for h in hits][:2] == [ALFA, DELIM]
    assert ds.search_builtin("ltkbv dvtcnt")[0].track_id == DELIM   # «делим вместе»


@pytest.mark.parametrize("q", ["ВКонтакте", "в", "", "   ", "zzzzzz", "т"])
def test_builtin_no_false_hits(q):
    assert ds.search_builtin(q) == []


def test_builtin_t_bank_not_vtb():
    assert all(h.track_id != VTB for h in ds.search_builtin("т-банк"))


def test_builtin_strict_mode_only_link_verified():
    strict = ds.builtin_entries(strict=True)
    assert strict and all(e.link_verified for e in strict)
    assert SIRIUS not in {e.track_id for e in strict}
    assert ds.search_builtin("сириус", strict=True) == []


def test_strict_is_default_and_only_tier_a():
    assert ds.BUILTIN_STRICT is True
    assert {e.track_id for e in ds.builtin_entries()} == TIER_A
    assert ds.builtin_entries() == ds.builtin_entries(strict=True)
    # уровень B лежит в данных, но выключен
    assert {e.track_id for e in ds.builtin_entries(strict=False)} > TIER_A
    s, _ = ds.DelistedSearch(fetcher=FakeArchive()), None
    assert s.search("сириус") == []                            # по умолчанию без сети и без B


def test_tier_a_has_post_archive_copy_and_date():
    """LEGAL.md §1.13 п.1: ссылка на пост, архивная копия поста, дата проверки."""
    for e in ds.builtin_entries():
        assert e.tier_a_ok, e.name
        assert e.post_url.startswith("https://") and e.checked_date == "2026-10-10"
        m = re.match(r"^https://web\.archive\.org/web/\d{14}/(https://\S+)$", e.archive_url)
        # снимок именно этого поста/страницы (t.me/<c>/<n> ≡ t.me/s/<c>/<n>)
        assert m and m.group(1).replace("/s/", "/") == e.post_url, e.name
        if e.link_archive_url:
            assert ds._ARCHIVE_URL_RE.match(e.link_archive_url)


def test_entry_without_archive_copy_is_dropped(monkeypatch):
    import dataclasses
    broken = tuple(dataclasses.replace(e, archive_url=None) if e.track_id == DELIM else e
                   for e in ds.BUILTIN)
    monkeypatch.setattr(ds, "BUILTIN", broken)
    assert DELIM not in {e.track_id for e in ds.builtin_entries()}
    assert all(h.track_id != DELIM for h in ds.search_builtin("делим вместе"))


def test_archive_must_be_copy_of_that_post(monkeypatch):
    """§1.13 п.1: снимок другого поста/страницы не считается архивной копией."""
    import dataclasses
    e = next(e for e in ds.BUILTIN if e.track_id == DRIVE)
    assert e.archive_of_post and e.tier_a_ok
    other = dataclasses.replace(
        e, archive_url="https://web.archive.org/web/20260421032214/https://t.me/tbank/10590")
    not_wb = dataclasses.replace(e, archive_url="https://archive.ph/abcd/https://t.me/tbank/10591")
    no_date = dataclasses.replace(e, checked_date=None)
    for bad in (other, not_wb, no_date):
        assert not bad.tier_a_ok
    monkeypatch.setattr(ds, "BUILTIN", tuple(other if x.track_id == DRIVE else x
                                             for x in ds.BUILTIN))
    assert DRIVE not in {x.track_id for x in ds.builtin_entries()}
    assert all(h.track_id != DRIVE for h in ds.search_builtin("drive transit"))


def test_originals_sber_tbank_tier_a():
    """Оригиналы Сбера и Т-Банка: разработчик — банк, ссылка с официального сайта на id."""
    for tid, dev, host in ((SBER, "Сбербанк России", "www.sberbank.ru"),
                           (TINK, "Tinkoff Bank", "www.tinkoff.ru")):
        e = next(e for e in ds.builtin_entries() if e.track_id == tid)
        assert e.tier_a_ok and e.developer_is_bank and e.developer == dev
        assert urllib.parse.urlparse(e.post_url).netloc == host
        assert f"id{tid}" in e.evidence
    t = next(e for e in ds.BUILTIN if e.track_id == TINK)
    assert f"id{TINK}" in t.link_archive_url          # архив трекера appsflyer → id
    # оригинал выше клона при одинаковом совпадении
    assert [h.track_id for h in ds.search_builtin("т банк")][:2] == [TINK, DRIVE]
    assert ds.search_builtin("сбербанк")[0].track_id == SBER


def test_developer_is_bank_flag():
    want = {VTB: True, ALFA: True, DELIM: False, DRIVE: False, SBER: True, TINK: True}
    assert {e.track_id: e.developer_is_bank for e in ds.builtin_entries()} == want
    for q, tid in (("втб", VTB), ("альфа банк", ALFA), ("делим вместе", DELIM),
                   ("drive transit", DRIVE), ("сбербанк онлайн", SBER), ("тинькофф", TINK)):
        h = next(h for h in ds.search_builtin(q) if h.track_id == tid)
        assert h.developer_is_bank is want[tid] and h.to_dict()["developer_is_bank"] is want[tid]
    # Сириус (если когда-нибудь включат B): разработчик — физлицо, не банк
    assert ds.search_builtin("сириус", strict=False)[0].developer_is_bank is False


def test_no_marketing_text_in_module():
    src = open(ds.__file__, encoding="utf-8").read().lower()
    for bad in ("замаскир", "обход блокир", "— это втб", "это втб онлайн"):
        assert bad not in src, bad


# --- «сириус» кириллицей ↔ «Cириус» с латинской C ---------------------------------
SIRIUS_LAT = "\u0043\u0438\u0440\u0438\u0443\u0441"   # «Cириус», C — латинская U+0043


def test_sirius_stored_name_has_latin_c():
    e = next(e for e in ds.BUILTIN if e.track_id == SIRIUS)
    assert e.name == SIRIUS_LAT and e.name[0] == "C" and ord(e.name[0]) == 0x43


@pytest.mark.parametrize("q", ["сириус", "Сириус", "СИРИУС", "сирИус", SIRIUS_LAT,
                               "cbhbec", "сирус"])
def test_cyrillic_query_matches_latin_c_builtin(q):
    """Builtin: кириллический запрос (и раскладка, и опечатка) находит «Cириус»."""
    assert all(ord(ch) > 0x400 for ch in "сириус")
    hits = ds.search_builtin(q, strict=False)
    assert hits and hits[0].track_id == SIRIUS and hits[0].name == SIRIUS_LAT


def sirius_archive():
    lat = "https://apps.apple.com/us/app/c%D0%B8%D1%80%D0%B8%D1%83%D1%81/id6749962031"
    other = "https://apps.apple.com/ru/app/%D1%81%D0%B8%D1%80%D0%B8%D1%83%D1%81/id1450605483"
    cdx = {("us", "c" + "ириус"): [[lat, "20260605074643", "200"]],
           ("ru", "сириус"): [[other, "20220625131210", "200"]]}
    pages = {lat: page_svelte(SIRIUS, SIRIUS_LAT, "Sergei Smirnov", "com.sm.FlowTime"),
             other: page_svelte(1450605483, "Сириус", "Other Dev", "ru.other.sirius")}
    return FakeArchive(cdx=cdx, pages=pages)


@pytest.mark.parametrize("q", ["сириус", "Сириус", SIRIUS_LAT, "cbhbec"])
def test_cyrillic_query_matches_latin_c_wayback(q):
    """Wayback: slug «cириус» (латинская c) ищется и для кириллического запроса."""
    fa = sirius_archive()
    s, _ = mk(fa)
    hits = s.search(q, allow_network=True)           # strict по умолчанию: builtin пуст
    by_id = {h.track_id: h for h in hits}
    assert SIRIUS in by_id, [c[0] for c in fa.calls]
    h = by_id[SIRIUS]
    assert h.source is ds.HitSource.WAYBACK and h.name == SIRIUS_LAT
    assert h.developer == "Sergei Smirnov" and h.snapshot == "20260605" and h.storefront == "us"
    assert h.developer_is_bank is None               # wayback: вторую строку не пишем
    cdx_urls = [urllib.parse.parse_qs(urllib.parse.urlparse(u).query)["url"][0]
                for u, _ in fa.calls if "/cdx/" in u]
    lat_prefix = "apps.apple.com/us/app/" + urllib.parse.quote(SIRIUS_LAT.lower())
    assert lat_prefix in cdx_urls and lat_prefix.split("/app/")[1][0] == "c"  # латинская c
    assert len(cdx_urls) <= 2 * ds.MAX_SLUGS


def test_homoglyph_first_latin():
    assert ds.homoglyph_first_latin("сириус") == "c" + "ириус"
    assert ds.homoglyph_first_latin("втб") is None and ds.homoglyph_first_latin("sber") is None


def test_builtin_data_integrity():
    ids = [e.track_id for e in ds.BUILTIN]
    assert len(ids) == len(set(ids))
    for e in ds.BUILTIN:
        # официальный источник банка и дата — обязательны (правило Лены)
        assert re.search(r"t\.me/(bankvtb|AlfaBank|sberbank|tbank)/\d+|vtb\.ru|alfabank\.ru|"
                         r"sberbank\.ru|tinkoff\.ru",
                         e.evidence), e.name
        assert re.search(r"\d{2}\.\d{2}\.20\d{2}", e.evidence), e.name
        assert "Wayback" in e.evidence and e.developer
        assert e.icon_url is None or ds._ICON_OK.match(e.icon_url)


def test_id_or_url_query_is_not_ours():
    s, _ = mk(FakeArchive())
    assert s.search("472951966") == [] and s.search("https://apps.apple.com/ru/app/id1") == []


# --- сеть: только с разрешения, builtin сначала --------------------------------
def test_no_network_by_default():
    fa = FakeArchive()
    s, _ = mk(fa)
    assert s.search("неизвестное приложение") == []
    assert fa.calls == []


def test_builtin_hit_skips_network():
    fa = FakeArchive()
    s, _ = mk(fa)
    hits = s.search("втб", allow_network=True)
    assert hits[0].track_id == VTB and fa.calls == []


def two_apps_archive():
    o1 = "https://apps.apple.com/ru/app/%D0%B4%D0%B5%D0%BC%D0%BE-%D0%B1%D0%B0%D0%BD%D0%BA/id555000111"
    o2 = "https://apps.apple.com/ru/app/%D0%B4%D0%B5%D0%BC%D0%BE-%D0%B1%D0%B0%D0%BD%D0%BA-%D0%BB%D0%B0%D0%B9%D1%82/id555000222"
    rows = [[o1, "20220101000000", "200"], [o1 + "?l=en", "20220301000000", "200"],
            [o2, "20210101000000", "200"], [JUNK, "20210106045123", "200"],
            ["https://apps.apple.com/ru/app/x/id777", "20210101000000", "404"],
            ["https://itunes.apple.com/ru/app/x/id888", "20200101000000", "200"],
            ["https://apps.apple.com/us/app/x/id999", "20200101000000", "200"]]
    pages = {o1 + "?l=en": page_svelte(555000111, "Демо Банк", "Demo LLC", "ru.demo.bank"),
             o2: page_ember_shoebox(555000222, "Демо Банк Лайт", "Demo LLC", "ru.demo.lite")}
    return FakeArchive(cdx={("ru", "демо-банк"): rows}, pages=pages)


def test_wayback_parse_and_filter():
    fa = two_apps_archive()
    s, _ = mk(fa)
    hits = s.search("Демо Банк", storefronts=("ru",), allow_network=True)
    assert [h.track_id for h in hits] == [555000111, 555000222]   # мусор/404/itunes/us отброшены
    h = hits[0]
    assert (h.name, h.developer, h.bundle_id) == ("Демо Банк", "Demo LLC", "ru.demo.bank")
    assert h.source is ds.HitSource.WAYBACK and h.storefront == "ru" and h.snapshot == "20220301"
    assert h.icon_url.endswith("/512x512bb.png") and ds._ICON_OK.match(h.icon_url)
    assert hits[1].bundle_id == "ru.demo.lite"                    # не ru.decoy.bank
    assert all(h.confidence <= 0.8 for h in hits)


def test_bundle_only_own_app():
    raw = page_svelte(42424242, "X", "Dev", "own.bundle", decoy_bundle="decoy.bundle")
    assert ds.parse_snapshot(raw, 42424242)["bundle_id"] == "own.bundle"
    raw = page_ember_store(43434343, "Y", "Dev", "store.bundle")
    assert ds.parse_snapshot(gzip.compress(raw), 43434343)["bundle_id"] == "store.bundle"
    # страница без привязки bundle к id — bundle не угадываем
    raw = page_svelte(42424242, "X", "Dev", "own.bundle").replace(b'"adamId": "42424242"', b'"adamId": "1"')
    assert ds.parse_snapshot(raw, 42424242)["bundle_id"] is None
    # снимок не про этот id — None
    assert ds.parse_snapshot(page_svelte(1, "X", "D", "b"), 987654321) is None


def test_snapshot_failure_falls_back_to_slug():
    fa = two_apps_archive()
    fa.pages = {}
    s, _ = mk(fa)
    orig_fetch = fa.__call__

    def fetch(url, headers, timeout):
        if "/web/" in url:
            raise ds.WaybackError("HTTP 404", 404)
        return orig_fetch(url, headers, timeout)
    s._fetch = fetch
    hits = s.search_wayback("демо банк", storefronts=("ru",))
    assert hits[0].name == "демо банк" and hits[0].developer is None and hits[0].confidence <= 0.5


# --- кэш и rate-limit ---------------------------------------------------------
def test_cache_hits_and_ttl():
    fa = two_apps_archive()
    s, clock = mk(fa)
    s.search_wayback("демо банк", storefronts=("ru",))
    n = len(fa.calls)
    assert n == 3                                  # 1 CDX + 2 снимка
    s.search_wayback("Демо-Банк", storefronts=("ru",))
    assert len(fa.calls) == n                      # всё из кэша
    clock.t += ds.CDX_TTL_S + 1
    s.search_wayback("демо банк", storefronts=("ru",))
    assert len(fa.calls) == n + 1                  # CDX заново, снимки ещё в кэше


def test_negative_cache_and_errors_not_cached():
    fa = FakeArchive(fail=[500])
    s, clock = mk(fa)
    assert s.search_wayback("пусто", storefronts=("ru",)) == [] and s.errors == 1
    s.search_wayback("пусто", storefronts=("ru",))   # ошибка не закэширована → запрос
    assert len(fa.calls) == 2
    s.search_wayback("пусто", storefronts=("ru",))   # пустой ответ закэширован
    assert len(fa.calls) == 2


def test_rate_limit_one_per_second():
    fa = two_apps_archive()
    s, clock = mk(fa)
    s.search_wayback("демо банк", storefronts=("ru",))
    assert len(fa.calls) == 3
    assert sum(clock.slept) >= 2 * ds.MIN_INTERVAL_S - 1e-9   # 3 запроса → ≥2 паузы по 1 с


def test_retry_once_on_503_with_backoff():
    fa = two_apps_archive()
    fa.fail = [503]
    s, clock = mk(fa)
    hits = s.search_wayback("демо банк", storefronts=("ru",))
    assert hits and ds.RETRY_BACKOFF_S in clock.slept
    fa2 = FakeArchive(fail=[429, 429])
    s2, _ = mk(fa2)
    assert s2.search_wayback("демо банк", storefronts=("ru",)) == [] and len(fa2.calls) == 2


def test_max_snapshots_bound():
    fa = two_apps_archive()
    s, _ = mk(fa, max_snapshots=1)
    hits = s.search_wayback("демо банк", storefronts=("ru",))
    assert sum(1 for u, _ in fa.calls if "/web/" in u) == 1 and len(hits) == 2


# --- анонимность и безопасность -------------------------------------------------
def test_requests_are_anonymous():
    fa = two_apps_archive()
    s, _ = mk(fa)
    s.search_wayback("демо банк", storefronts=("ru",))
    for url, headers in fa.calls:
        assert set(headers) == {"User-Agent", "Accept"}
        assert headers["User-Agent"] == ds.USER_AGENT and "python" not in headers["User-Agent"].lower()
        low = url.lower()
        for bad in ("dsid", "cookie", "token", "storefront", "x-apple", "appleid", "password"):
            assert bad not in low


def test_default_fetcher_has_no_cookie_or_auth_handlers(monkeypatch):
    seen = {}

    class Resp:
        status = 200
        def read(self): return b"[]"
        def __enter__(self): return self
        def __exit__(self, *a): return False

    real = urllib.request.build_opener

    def spy(*handlers):
        opener = real(*handlers)
        seen["handlers"] = [type(h).__name__ for h in opener.handlers]

        def fake_open(req, timeout=None):
            seen["headers"] = dict(req.header_items())
            return Resp()
        opener.open = fake_open
        return opener
    monkeypatch.setattr(urllib.request, "build_opener", spy)
    ds._default_fetcher("https://web.archive.org/cdx/search/cdx?url=x", ds.HEADERS, 5)
    assert not any("Cookie" in h or "Auth" in h for h in seen["handlers"])
    assert set(k.lower() for k in seen["headers"]) == {"user-agent", "accept"}


def test_module_does_not_touch_account_code():
    src = open(ds.__file__, encoding="utf-8").read()
    assert not re.search(r"^\s*(import|from)\s+\S*(ipatool_api|license_guard)", src, re.M)
    assert "HTTPCookieProcessor" not in src.replace("без HTTPCookieProcessor", "")


def test_logs_have_counts_only(caplog):
    fa = two_apps_archive()
    s, _ = mk(fa)
    with caplog.at_level(logging.DEBUG, logger="apprestore.delisted_search"):
        s.search("Демо Банк", storefronts=("ru",), allow_network=True)
        s.search("Сириус")
    text = caplog.text.lower()
    assert "демо" not in text and "сириус" not in text and "555000111" not in text
    assert "hits" in text


def test_no_ipa_or_foreign_links_in_output():
    evil = page_svelte(555000111, "Демо Банк https://evil.example/app.ipa", "Dev http://x.ipa",
                       "ru.demo.bank", image="https://evil.example/files/app.ipa")
    fa = two_apps_archive()
    fa.pages[list(fa.pages)[0]] = evil
    s, _ = mk(fa)
    hits = s.search("Демо Банк", storefronts=("ru",), allow_network=True)
    hits += ds.search_builtin("втб") + ds.search_builtin("альфа")
    for h in hits:
        for k, v in h.to_dict().items():
            if isinstance(v, str):
                assert ".ipa" not in v.lower(), (k, v)
                if "://" in v:
                    assert k == "icon_url" and ds._ICON_OK.match(v), (k, v)


@pytest.mark.parametrize("sf", [("kz",), ("ru", "us", "ru2"), (), ("RU", "US", "BY")])
def test_storefronts_fixed(sf):
    s, _ = mk(FakeArchive())
    with pytest.raises(ValueError):
        s.search("x y z", storefronts=sf, allow_network=True)


def test_slugify_and_variants():
    assert ds.slugify("Делим Вместе!") == "делим-вместе"
    assert ds.slugify("Office-Capital") == "office-capital"
    assert ("втб", 0.95) in ds.query_variants("dn,")
    assert ds.query_variants("Cириус")[0][0] == "сириус"


def test_search_delisted_wrapper(monkeypatch):
    monkeypatch.setattr(ds, "_default", None)
    hits = ds.search_delisted("втб")
    assert hits[0].to_dict()["source"] == "builtin" and hits[0].track_id == VTB
    assert ds.search_delisted("сириус") == []        # strict, без сети
