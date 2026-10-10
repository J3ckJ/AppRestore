"""Тесты region_probe — без сети: HTTP подменён, сокеты заблокированы."""
import io
import json
import socket
import urllib.parse
import urllib.request

import pytest

import apprestore_core.region_probe as rp
from apprestore_core.region_probe import RegionProbe, RegionStatus


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def boom(*a, **k):
        raise AssertionError("реальная сеть в тестах запрещена")
    monkeypatch.setattr(socket.socket, "connect", boom)
    monkeypatch.setattr(socket, "create_connection", boom)


class FakeStore:
    """catalog: country -> set(track_id). fail: set стран, где lookup падает."""

    def __init__(self, catalog, fail=()):
        self.catalog = {k.upper(): set(v) for k, v in catalog.items()}
        self.fail = {c.upper() for c in fail}
        self.calls = []  # (country, [ids], headers)

    def __call__(self, url, headers, timeout):
        parsed = urllib.parse.urlparse(url)
        assert f"{parsed.scheme}://{parsed.netloc}{parsed.path}" == rp.LOOKUP_URL
        q = urllib.parse.parse_qs(parsed.query)
        assert set(q) == {"id", "country"}
        country = q["country"][0].upper()
        ids = [int(x) for x in q["id"][0].split(",")]
        self.calls.append((country, ids, dict(headers)))
        if country in self.fail:
            raise rp.LookupError_("HTTP 503")
        have = self.catalog.get(country, set())
        res = [{"trackId": i, "kind": "software"} for i in ids if i in have]
        return json.dumps({"resultCount": len(res), "results": res}).encode()


class Clock:
    def __init__(self):
        self.t = 1000.0
        self.sleeps = []

    def __call__(self):
        return self.t

    def sleep(self, s):
        self.sleeps.append(s)
        self.t += s


def make(store, **kw):
    clock = Clock()
    kw.setdefault("min_interval_s", 0)
    probe = RegionProbe(fetcher=store, clock=clock, sleep=clock.sleep, **kw)
    return probe, clock


# --- 4 исхода ---------------------------------------------------------------
def test_available():
    store = FakeStore({"RU": {1}})
    probe, _ = make(store)
    assert probe.classify([1], "ru") == {1: RegionStatus.AVAILABLE}
    assert [c[0] for c in store.calls] == ["RU"]  # эталонные не нужны


def test_not_in_region():
    store = FakeStore({"RU": set(), "US": {2}})
    probe, _ = make(store)
    assert probe.classify([2], "RU") == {2: RegionStatus.NOT_IN_REGION}


def test_not_in_region_via_second_reference():
    store = FakeStore({"DE": set(), "US": set(), "RU": {3}})
    probe, _ = make(store)
    assert probe.classify([3], "DE") == {3: RegionStatus.NOT_IN_REGION}


def test_delisted():
    store = FakeStore({"DE": set(), "US": set(), "RU": set()})
    probe, _ = make(store)
    assert probe.classify([4], "DE") == {4: RegionStatus.DELISTED}


def test_unknown_on_account_error():
    store = FakeStore({"US": {5}}, fail={"RU"})
    probe, _ = make(store)
    assert probe.classify([5], "RU") == {5: RegionStatus.UNKNOWN}


def test_unknown_when_reference_ambiguous():
    # аккаунт: нет; US: нет; RU: ошибка -> различить нельзя
    store = FakeStore({"DE": set(), "US": set()}, fail={"RU"})
    probe, _ = make(store)
    assert probe.classify([6], "DE") == {6: RegionStatus.UNKNOWN}


def test_reference_error_but_other_reference_present():
    store = FakeStore({"DE": set(), "RU": {7}}, fail={"US"})
    probe, _ = make(store)
    assert probe.classify([7], "DE") == {7: RegionStatus.NOT_IN_REGION}


def test_bad_json_is_unknown():
    probe, _ = make(lambda u, h, t: b"<html>")
    assert probe.classify([8], "US") == {8: RegionStatus.UNKNOWN}


def test_mixed_batch_one_request_per_store():
    store = FakeStore({"DE": {1}, "US": {2}, "RU": set()})
    probe, _ = make(store)
    out = probe.classify([1, 2, 3], "DE")
    assert out == {1: RegionStatus.AVAILABLE, 2: RegionStatus.NOT_IN_REGION,
                   3: RegionStatus.DELISTED}
    assert [(c, ids) for c, ids, _ in store.calls] == [
        ("DE", [1, 2, 3]), ("US", [2, 3]), ("RU", [3])]


# --- кэш --------------------------------------------------------------------
def test_cache_second_call_no_network():
    store = FakeStore({"DE": {1}, "US": {2}, "RU": set()})
    probe, _ = make(store)
    first = probe.classify([1, 2, 3], "DE")
    n = len(store.calls)
    assert probe.classify([3, 2, 1], "DE") == first
    assert len(store.calls) == n


def test_cache_ttl_expires():
    store = FakeStore({"US": {1}})
    probe, clock = make(store, positive_ttl_s=100)
    probe.classify([1], "US")
    clock.t += 101
    probe.classify([1], "US")
    assert len(store.calls) == 2


def test_errors_not_cached():
    store = FakeStore({"US": {1}}, fail={"US"})
    probe, _ = make(store)
    assert probe.classify([1], "US")[1] is RegionStatus.UNKNOWN
    store.fail.clear()
    assert probe.classify([1], "US")[1] is RegionStatus.AVAILABLE


# --- дедуп ------------------------------------------------------------------
def test_dedup_and_invalid_ids():
    store = FakeStore({"US": {1, 2}})
    probe, _ = make(store)
    out = probe.classify([1, "1", 1, 2, " 2 ", None, "abc", -5, 0], "US")
    assert out == {1: RegionStatus.AVAILABLE, 2: RegionStatus.AVAILABLE}
    assert store.calls[0][1] == [1, 2]


def test_empty_input_no_requests():
    store = FakeStore({})
    probe, _ = make(store)
    assert probe.classify([], "US") == {}
    assert store.calls == []


def test_batching():
    ids = list(range(1, 121))
    store = FakeStore({"US": set(ids)})
    probe, _ = make(store)
    probe.classify(ids, "US")
    assert [len(c[1]) for c in store.calls] == [50, 50, 20]


# --- анонимность ------------------------------------------------------------
FORBIDDEN = ("cookie", "authorization", "x-apple-store-front", "x-dsid",
             "x-token", "x-apple-tz", "icloud-dsid", "x-apple-i-md")


def test_request_is_anonymous_headers():
    store = FakeStore({"US": set(), "RU": set(), "DE": set()})
    probe, _ = make(store)
    probe.classify([10, 11], "DE")
    for _, _, headers in store.calls:
        assert {k.lower() for k in headers} == {"user-agent", "accept"}
        assert headers["User-Agent"] == rp.USER_AGENT
        for k in headers:
            assert k.lower() not in FORBIDDEN


def test_url_has_only_ids_and_country():
    seen = []
    def fetch(url, headers, timeout):
        seen.append(url)
        return b'{"resultCount":0,"results":[]}'
    probe, _ = make(fetch)
    probe.classify([42], "US")
    for url in seen:
        q = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)
        assert set(q) == {"id", "country"}
        assert url.startswith("https://itunes.apple.com/lookup?")


def test_default_fetcher_no_cookie_handler(monkeypatch):
    captured = {}

    class FakeResp(io.BytesIO):
        status = 200
        def __enter__(self): return self
        def __exit__(self, *a): return False

    class FakeOpener:
        addheaders = [("User-agent", "Python-urllib")]
        def open(self, req, timeout):
            captured["req"] = req
            captured["addheaders"] = list(self.addheaders)
            return FakeResp(b'{"resultCount":0,"results":[]}')

    def fake_build_opener(*handlers):
        captured["handlers"] = handlers
        return FakeOpener()

    monkeypatch.setattr(urllib.request, "build_opener", fake_build_opener)
    rp._default_fetcher("https://itunes.apple.com/lookup?id=1&country=us",
                        rp.HEADERS, 5)
    assert captured["handlers"] == ()  # без HTTPCookieProcessor/auth
    assert captured["addheaders"] == []
    req = captured["req"]
    assert req.get_method() == "GET"
    names = {k.lower() for k, _ in req.header_items()}
    assert names == {"user-agent", "accept"}
    assert req.data is None


def test_module_does_not_import_session_code():
    import inspect
    src = inspect.getsource(rp)
    for bad in ("import ipatool_api", "from ipatool_api", "import license_guard",
                "http.cookiejar", "HTTPCookieProcessor(", "X-Apple-Store-Front\":"):
        assert bad not in src


# --- нет перебора стран -----------------------------------------------------
def test_max_two_references_plus_account():
    store = FakeStore({c: set() for c in ("DE", "US", "RU")})
    probe, _ = make(store)
    probe.classify(list(range(1, 30)), "DE")
    countries = {c for c, _, _ in store.calls}
    assert countries <= {"DE"} | set(rp.REFERENCE_STOREFRONTS)
    assert len(countries) <= 3


def test_more_than_two_references_rejected():
    with pytest.raises(ValueError):
        RegionProbe(reference_storefronts=("US", "RU", "GB"))
    with pytest.raises(ValueError):
        RegionProbe(reference_storefronts=())


def test_default_references_fixed():
    assert rp.REFERENCE_STOREFRONTS == ("US", "RU")
    assert len(rp.REFERENCE_STOREFRONTS) <= rp.MAX_REFERENCE_STOREFRONTS


def test_account_equal_reference_not_requested_twice():
    store = FakeStore({"US": set(), "RU": set()})
    probe, _ = make(store)
    assert probe.classify([9], "US") == {9: RegionStatus.DELISTED}
    assert [c for c, _, _ in store.calls] == ["US", "RU"]


def test_invalid_account_country():
    probe, _ = make(FakeStore({}))
    for bad in ("", "USA", "1", "143441-1,34", None):
        with pytest.raises(ValueError):
            probe.classify([1], bad)


def test_too_many_ids_rejected():
    probe, _ = make(FakeStore({}))
    with pytest.raises(ValueError):
        probe.classify(range(1, rp.MAX_IDS_PER_CALL + 2), "US")


# --- rate-limit -------------------------------------------------------------
def test_rate_limit_spacing():
    store = FakeStore({"DE": set(), "US": set(), "RU": set()})
    probe, clock = make(store, min_interval_s=1.0)
    probe.classify([1], "DE")
    assert len(store.calls) == 3
    assert clock.sleeps == [1.0, 1.0]


# --- подписи ----------------------------------------------------------------
def test_labels_ru_no_actions():
    assert rp.label_ru(RegionStatus.AVAILABLE) is None
    for status in RegionStatus:
        text = (rp.LABELS_RU[status] or "").lower()
        for word in ("vpn", "купить", "смен", "регион", "войти", "перейти"):
            assert word not in text
    for text in rp.GROUP_TITLES_RU.values():
        assert "vpn" not in text.lower()


# --- осторожность DELISTED (уточнение Лены) --------------------------------
def test_single_reference_never_delisted():
    store = FakeStore({"DE": set(), "US": set()})
    probe, _ = make(store, reference_storefronts=("US",))
    assert probe.classify([1], "DE") == {1: RegionStatus.UNKNOWN}


def test_single_reference_equal_account_never_delisted():
    store = FakeStore({"US": set()})
    probe, _ = make(store, reference_storefronts=("US",))
    assert probe.classify([1], "US") == {1: RegionStatus.UNKNOWN}
    assert [c for c, _, _ in store.calls] == ["US"]


def test_single_reference_present_is_not_in_region():
    store = FakeStore({"DE": set(), "US": {1}})
    probe, _ = make(store, reference_storefronts=("US",))
    assert probe.classify([1], "DE") == {1: RegionStatus.NOT_IN_REGION}


def test_delisted_requires_all_references_checked():
    # аккаунт DE: нет; US: нет; RU: ошибка -> не DELISTED
    store = FakeStore({"DE": set(), "US": set()}, fail={"RU"})
    probe, _ = make(store)
    assert probe.classify([1], "DE")[1] is RegionStatus.UNKNOWN
    store.fail.clear()
    store.catalog["RU"] = set()
    assert probe.classify([1], "DE")[1] is RegionStatus.DELISTED


# --- настраиваемые подписи --------------------------------------------------
def test_labels_cautious_defaults():
    assert rp.DEFAULT_LABELS_RU[RegionStatus.NOT_IN_REGION] == "Нет в App Store вашей страны"
    assert rp.DEFAULT_LABELS_RU[RegionStatus.DELISTED] == "Удалено из App Store"


def test_labels_overridable():
    labels = rp.make_labels({"delisted": "Похоже, удалено из App Store",
                             RegionStatus.UNKNOWN: None})
    assert rp.label_ru(RegionStatus.DELISTED, labels) == "Похоже, удалено из App Store"
    assert rp.label_ru(RegionStatus.UNKNOWN, labels) is None
    assert rp.label_ru(RegionStatus.NOT_IN_REGION, labels) == "Нет в App Store вашей страны"
    # дефолты не мутируются
    assert rp.DEFAULT_LABELS_RU[RegionStatus.DELISTED] == "Удалено из App Store"
    titles = rp.make_group_titles({"not_in_region": "Нет в вашей стране"})
    assert titles[RegionStatus.NOT_IN_REGION] == "Нет в вашей стране"
    with pytest.raises(ValueError):
        rp.make_labels({"bogus": "x"})
