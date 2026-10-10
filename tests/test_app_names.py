"""Тесты app_names — без сети: HTTP подменён, сокеты заблокированы."""
import json
import random
import socket
import urllib.parse

import pytest

try:
    from apprestore_core import app_names as an
    from apprestore_core import region_probe as rp
except ImportError:
    import app_names as an
    import region_probe as rp


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def boom(*a, **k):
        raise AssertionError("реальная сеть в тестах запрещена")
    monkeypatch.setattr(socket.socket, "connect", boom)
    monkeypatch.setattr(socket, "create_connection", boom)


class FakeStore:
    """catalog: country(lower) -> {track_id: (bundleId, trackName[, price])}.

    Ответ перемешан (порядок lookup не гарантирован), ненайденные опущены.
    """

    def __init__(self, catalog, fail=(), shuffle=True):
        self.catalog = {k.lower(): dict(v) for k, v in catalog.items()}
        self.fail = {c.lower() for c in fail}
        self.shuffle = shuffle
        self.calls = []  # (param, country, [keys], headers)

    def __call__(self, url, headers, timeout):
        parsed = urllib.parse.urlparse(url)
        assert f"{parsed.scheme}://{parsed.netloc}{parsed.path}" == an.LOOKUP_URL
        q = urllib.parse.parse_qs(parsed.query)
        assert len(q) == 2 and "country" in q
        (param,) = set(q) - {"country"}
        assert param in ("id", "bundleId")
        country = q["country"][0]
        assert country == country.lower()
        keys = q[param][0].split(",")
        self.calls.append((param, country, keys, dict(headers)))
        if country in self.fail:
            raise rp.LookupError_("HTTP 503")
        cat = self.catalog.get(country, {})
        res = []
        for tid, info in cat.items():
            bundle, name = info[0], info[1]
            hit = (str(tid) in keys) if param == "id" else (bundle.casefold() in
                                                             [k.casefold() for k in keys])
            if hit:
                item = {"trackId": tid, "bundleId": bundle, "trackName": name}
                if len(info) > 2:
                    item["price"] = info[2]
                res.append(item)
        if self.shuffle:
            random.Random(len(keys)).shuffle(res)
            res.reverse()
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


def make(store, min_interval_s=0, **kw):
    clock = Clock()
    probe = rp.RegionProbe(fetcher=store, clock=clock, sleep=clock.sleep,
                           min_interval_s=min_interval_s)
    return an.AppNames(probe, **kw), probe, clock


RU = {"ru": {1: ("com.a", "Сбербанк"), 2: ("com.b", "Тинькофф")},
      "us": {1: ("com.a", "Sberbank"), 2: ("com.b", "Tinkoff"), 3: ("com.c", "Clubhouse")}}


# --- батчи --------------------------------------------------------------------
def test_comma_batch_single_request():
    store = FakeStore(RU)
    names, _, _ = make(store)
    out = names.display_names([1, 2], country="RU")
    assert out == {"1": "Сбербанк", "2": "Тинькофф"}
    assert len(store.calls) == 1
    assert store.calls[0][:3] == ("id", "ru", ["1", "2"])


def test_chunks_of_100():
    ids = list(range(1, 251))
    store = FakeStore({"us": {i: (f"b.{i}", f"App {i}") for i in ids}})
    names, _, _ = make(store)
    out = names.display_names(ids, country="us")
    assert [len(c[2]) for c in store.calls] == [100, 100, 50]
    assert len(out) == 250 and out["250"] == "App 250"
    assert an.BATCH_SIZE == 100


def test_batch_size_cannot_exceed_100():
    ids = list(range(1, 202))
    store = FakeStore({"us": {}})
    names, _, _ = make(store, batch_size=500)
    names.display_names(ids, country="us")
    assert max(len(c[2]) for c in store.calls) == 100


def test_map_back_by_id_not_index():
    # Ответ перемешан и без «дыр»: сопоставление по индексу перепутало бы имена.
    store = FakeStore({"us": {10: ("x.ten", "Ten"), 30: ("x.thirty", "Thirty")}})
    names, _, _ = make(store)
    out = names.display_names([30, 20, 10], country="US")
    assert out == {"30": "Thirty", "10": "Ten"}


def test_missing_ids_absent():
    store = FakeStore(RU)
    names, _, _ = make(store)
    out = names.display_names([3, 999], country="ru")
    assert out == {}
    assert "999" not in names.display_names([999], countries=["us"])


def test_results_for_unrequested_ids_ignored():
    def fetch(url, headers, timeout):
        return json.dumps({"results": [{"trackId": 5, "trackName": "Чужое"},
                                       {"trackId": 1, "trackName": "Своё"}]}).encode()
    names, _, _ = make(fetch)
    assert names.display_names([1], country="us") == {"1": "Своё"}


# --- ключи выхода = входной токен -------------------------------------------
def test_output_keyed_by_input_token_strings():
    store = FakeStore({"us": {284882215: ("com.facebook.Facebook", "Facebook")}})
    names, _, _ = make(store)
    assert names.display_names([284882215], country="us") == {"284882215": "Facebook"}
    assert names.display_names(["284882215"], country="us") == {"284882215": "Facebook"}


def test_bundle_ids_keyed_by_bundle_string():
    store = FakeStore(RU)
    names, _, _ = make(store)
    out = names.display_names(bundle_ids=["com.b", "com.A", "com.none"], country="ru")
    assert out == {"com.b": "Тинькофф", "com.A": "Сбербанк"}
    assert store.calls[0][0] == "bundleId"
    assert sorted(store.calls[0][2]) == ["com.a", "com.b", "com.none"]


def test_ids_and_bundles_together_separate_requests():
    store = FakeStore(RU)
    names, _, _ = make(store)
    out = names.display_names([1], bundle_ids=["com.b"], country="ru")
    assert out == {"1": "Сбербанк", "com.b": "Тинькофф"}
    assert [c[0] for c in store.calls] == ["id", "bundleId"]


def test_bundle_key_kept_through_fallthrough():
    store = FakeStore(RU)
    names, _, _ = make(store)
    out = names.display_names(bundle_ids=["com.c"], account_country="RU", device_locale="ru_RU")
    assert out == {"com.c": "Clubhouse"}
    assert [(c[0], c[1]) for c in store.calls] == [("bundleId", "ru"), ("bundleId", "us")]


def test_duplicate_tokens_dedup_request_but_all_keys_filled():
    store = FakeStore(RU)
    names, _, _ = make(store)
    out = names.display_names([1, "1", " 1 "], country="ru")
    assert out == {"1": "Сбербанк", " 1 ": "Сбербанк"}
    assert store.calls[0][2] == ["1"]


def test_invalid_inputs_skipped():
    store = FakeStore(RU)
    names, _, _ = make(store)
    out = names.display_names([None, "abc", -5, 0, 1], bundle_ids=[None, "", "bad id", 7],
                              country="ru")
    assert out == {"1": "Сбербанк"}
    assert len(store.calls) == 1


def test_empty_input_no_requests():
    store = FakeStore({})
    names, _, _ = make(store)
    assert names.display_names([], country="us") == {}
    assert store.calls == []


def test_too_many_ids_rejected():
    names, _, _ = make(FakeStore({}))
    with pytest.raises(ValueError):
        names.display_names(range(1, an.MAX_IDS_PER_CALL + 2), country="us")


def test_empty_trackname_is_not_a_name():
    def fetch(url, headers, timeout):
        return json.dumps({"results": [{"trackId": 1, "trackName": "  "}]}).encode()
    names, _, _ = make(fetch)
    assert names.display_names([1], country="us") == {}


# --- цепочка стран ------------------------------------------------------------
def test_chain_logged_in():
    assert an.country_chain("RU", "en_DE") == ("ru", "de", "us")


def test_chain_not_logged_in():
    assert an.country_chain(None, "ru_RU") == ("ru", "us")


def test_chain_dedup_and_bare_language():
    assert an.country_chain("US", "en_US") == ("us",)
    assert an.country_chain(None, "en") == ("us",)      # нет региона -> сразу US
    assert an.country_chain(None, None) == ("us",)
    assert an.country_chain("143441-1,34", "garbage") == ("us",)


def test_fallthrough_only_unresolved():
    store = FakeStore(RU)
    names, _, _ = make(store)
    out = names.display_names([1, 3, 404], account_country="RU", device_locale="ru_RU")
    assert out == {"1": "Сбербанк", "3": "Clubhouse"}
    assert [(c[1], sorted(c[2])) for c in store.calls] == [("ru", ["1", "3", "404"]),
                                                            ("us", ["3", "404"])]


def test_fallthrough_order_account_then_device_then_us():
    store = FakeStore({"de": {}, "kz": {7: ("k", "Kaspi")}, "us": {8: ("e", "Eight")}})
    names, _, _ = make(store)
    out = names.display_names([7, 8], account_country="DE", device_locale="ru_KZ")
    assert out == {"7": "Kaspi", "8": "Eight"}
    assert [c[1] for c in store.calls] == ["de", "kz", "us"]


def test_before_login_device_region_first():
    store = FakeStore(RU)
    names, _, _ = make(store)
    assert names.display_names([2], device_locale="ru_RU") == {"2": "Тинькофф"}
    assert [c[1] for c in store.calls] == ["ru"]


def test_error_in_country_falls_through_and_not_cached():
    store = FakeStore(RU, fail={"ru"})
    names, _, _ = make(store)
    assert names.display_names([1], device_locale="ru_RU") == {"1": "Sberbank"}
    store.fail.clear()
    assert names.display_names([1], device_locale="ru_RU") == {"1": "Сбербанк"}


def test_explicit_countries_no_auto_us():
    store = FakeStore(RU)
    names, _, _ = make(store)
    assert names.display_names([3], countries=["RU"]) == {}
    assert [c[1] for c in store.calls] == ["ru"]


def test_explicit_countries_validation():
    names, _, _ = make(FakeStore({}))
    for bad in (["USA"], ["ru_RU"], [], ["us", "ru", "de", "gb"], [None]):
        with pytest.raises(ValueError):
            names.display_names([1], countries=bad)
    with pytest.raises(ValueError):
        names.display_names([1], country="us", account_country="RU")
    with pytest.raises(ValueError):
        names.display_names([1], country="us", countries=["ru"])


def test_no_country_enumeration():
    store = FakeStore({})
    names, _, _ = make(store)
    names.display_names(list(range(1, 30)), account_country="DE", device_locale="fr_FR")
    assert {c[1] for c in store.calls} == {"de", "fr", "us"}


# --- нормализация ------------------------------------------------------------
@pytest.mark.parametrize("locale,expected", [
    ("ru_RU", "ru"),            # регион RU -> витрина ru
    ("en_RU", "ru"),            # язык английский, но витрина — RU
    ("en_US", "us"),
    ("zh-Hans_CN", "cn"),
    ("zh_Hant_TW", "tw"),
    ("en_US@calendar=gregorian", "us"),
    (" de_DE ", "de"),
    ("en", None),               # голый язык — региона нет
    ("ru", None),
    ("en_001", None),
    ("", None),
    (None, None),
])
def test_region_from_locale(locale, expected):
    assert an.region_from_locale(locale) == expected


def test_en_RU_locale_queries_ru_storefront():
    store = FakeStore(RU)
    names, _, _ = make(store)
    assert names.display_names([1], device_locale="en_RU") == {"1": "Сбербанк"}
    assert store.calls[0][1] == "ru"


def test_ru_RU_locale_queries_ru_storefront():
    store = FakeStore(RU)
    names, _, _ = make(store)
    assert names.display_names([1], device_locale="ru_RU") == {"1": "Сбербанк"}
    assert store.calls[0][1] == "ru"


def test_bare_language_locale_goes_straight_to_us():
    store = FakeStore(RU)
    names, _, _ = make(store)
    assert names.display_names([1], device_locale="ru") == {"1": "Sberbank"}
    assert [c[1] for c in store.calls] == ["us"]


@pytest.mark.parametrize("raw,expected", [("RU", "ru"), ("ru", "ru"), (" us ", "us"),
                                          ("USA", None), ("143441-1,34", None), (None, None)])
def test_norm_storefront(raw, expected):
    assert an.norm_storefront(raw) == expected


def test_uppercase_storefront_sent_lowercase():
    store = FakeStore(RU)
    names, _, _ = make(store)
    names.display_names([1], account_country="RU")
    assert store.calls[0][1] == "ru"


# --- кэш ---------------------------------------------------------------------
def test_cache_hit_avoids_second_request():
    store = FakeStore(RU)
    names, _, _ = make(store)
    first = names.display_names([1, 3], account_country="RU")
    n = len(store.calls)
    assert names.display_names([3, 1], account_country="RU") == first
    assert len(store.calls) == n


def test_negative_cache_also_avoids_request():
    store = FakeStore(RU)
    names, _, _ = make(store)
    names.display_names([404], country="us")
    names.display_names([404], country="us")
    assert len(store.calls) == 1


def test_cache_ttl_expires():
    store = FakeStore(RU)
    names, _, clock = make(store, positive_ttl_s=100)
    names.display_names([1], country="ru")
    clock.t += 101
    names.display_names([1], country="ru")
    assert len(store.calls) == 2


def test_clear_cache():
    store = FakeStore(RU)
    names, _, _ = make(store)
    names.display_names([1], country="ru")
    names.clear_cache()
    names.display_names([1], country="ru")
    assert len(store.calls) == 2


def test_id_lookup_warms_region_probe_cache():
    store = FakeStore({"ru": {1: ("com.a", "Сбербанк")}})
    names, probe, _ = make(store)
    names.display_names([1], country="ru")
    n = len(store.calls)
    assert probe.classify([1], "RU") == {1: rp.RegionStatus.AVAILABLE}
    assert len(store.calls) == n           # из общего кэша, без запроса


# --- rate-limit --------------------------------------------------------------
def test_rate_limit_respected():
    store = FakeStore({})
    names, _, clock = make(store, min_interval_s=1.0)
    names.display_names([1], account_country="DE", device_locale="fr_FR")
    assert len(store.calls) == 3
    assert clock.sleeps == [1.0, 1.0]


def test_rate_limit_shared_with_region_probe():
    store = FakeStore({"us": {1: ("com.a", "A")}})
    names, probe, clock = make(store, min_interval_s=1.0)
    probe.classify([1], "US")
    names.display_names([2], country="us")
    assert clock.sleeps == [1.0]           # второй модуль ждёт за первым


# --- анонимность -------------------------------------------------------------
FORBIDDEN = ("cookie", "authorization", "x-apple-store-front", "x-dsid",
             "x-token", "x-apple-tz", "icloud-dsid", "x-apple-i-md")


def test_request_is_anonymous_headers():
    store = FakeStore(RU)
    names, _, _ = make(store)
    names.display_names([1, 3], bundle_ids=["com.c"], account_country="RU",
                        device_locale="en_RU")
    assert store.calls
    for *_, headers in store.calls:
        assert {k.lower() for k in headers} == {"user-agent", "accept"}
        assert headers == dict(rp.HEADERS)
        for k in headers:
            assert k.lower() not in FORBIDDEN


def test_uses_region_probe_anonymous_fetcher_by_default():
    probe = rp.RegionProbe()
    assert probe._fetch is rp._default_fetcher
    assert an.AppNames(probe).probe._fetch is rp._default_fetcher


def test_url_has_only_key_param_and_country():
    seen = []
    def fetch(url, headers, timeout):
        seen.append(url)
        return b'{"resultCount":0,"results":[]}'
    names, _, _ = make(fetch)
    names.display_names([42], bundle_ids=["com.x"], country="us")
    assert len(seen) == 2
    for url in seen:
        assert url.startswith("https://itunes.apple.com/lookup?")
        q = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)
        assert set(q) in ({"id", "country"}, {"bundleId", "country"})


def test_module_does_not_import_session_code():
    import inspect
    src = inspect.getsource(an)
    for bad in ("import ipatool_api", "from ipatool_api", "import license_guard",
                "http.cookiejar", "HTTPCookieProcessor(", "X-Apple-Store-Front\":",
                "build_opener("):
        assert bad not in src


# --- модульная обёртка -------------------------------------------------------
def test_module_display_names_uses_shared_probe(monkeypatch):
    store = FakeStore(RU)
    clock = Clock()
    probe = rp.RegionProbe(fetcher=store, clock=clock, sleep=clock.sleep, min_interval_s=0)
    monkeypatch.setattr(rp, "_default_probe", probe)
    monkeypatch.setattr(an, "_default", None)
    assert an.display_names([1], device_locale="en_RU") == {"1": "Сбербанк"}
    assert an.default_resolver().probe is probe is rp.default_probe()


# --- запасное имя из встроенного списка delisted_search (Лена) -----------------
try:
    from apprestore_core import delisted_search as ds
except ImportError:
    import delisted_search as ds

_A = [e for e in ds.builtin_entries()]               # уровень A (BUILTIN_STRICT)
_A_WITH_BUNDLE = [e for e in _A if e.bundle_id]
_B_ONLY = [e for e in ds.BUILTIN if e not in _A]    # уровень B — в запасные имена не идёт


@pytest.fixture
def forbid_archive(monkeypatch):
    """Любой путь в Internet Archive / сеть delisted_search -> исключение + учёт."""
    hits = []

    def boom(*a, **k):
        hits.append((a, k))
        raise AssertionError("запрос в web.archive.org / сеть delisted_search запрещён")
    monkeypatch.setattr(ds, "_default_fetcher", boom)
    monkeypatch.setattr(ds.DelistedSearch, "search", boom)
    monkeypatch.setattr(ds, "search_delisted", boom)
    monkeypatch.setattr(ds, "default_search", boom)
    import urllib.request as ur
    monkeypatch.setattr(ur, "urlopen", boom)
    monkeypatch.setattr(ur, "build_opener", boom)
    return hits


def test_builtin_fallback_never_requests_archive(forbid_archive):
    assert _A, "во встроенном списке должны быть записи уровня A"
    store = FakeStore({})                              # Apple не знает ни одного
    names, _, _ = make(store)
    ids = [e.track_id for e in _A]
    out = names.display_names(ids, account_country="RU", device_locale="en_RU")
    assert out == {str(e.track_id): e.name for e in _A}
    assert forbid_archive == []                        # ни одного вызова архива/сети
    # FakeStore проверяет, что каждый URL — ровно itunes.apple.com/lookup
    assert store.calls and {c[1] for c in store.calls} == {"ru", "us"}
    assert an.LOOKUP_URL.startswith("https://itunes.apple.com/")


def test_builtin_fallback_when_apple_lookup_fails_entirely(forbid_archive):
    store = FakeStore({}, fail={"ru", "us"})
    names, _, _ = make(store)
    e = _A[0]
    assert names.display_names([e.track_id], device_locale="ru_RU") == {str(e.track_id): e.name}
    assert forbid_archive == []


def test_builtin_fallback_by_bundle_id(forbid_archive):
    assert _A_WITH_BUNDLE
    e = _A_WITH_BUNDLE[0]
    store = FakeStore({})
    names, _, _ = make(store)
    token = e.bundle_id.upper()                        # регистр bundleId не важен, ключ — как передан
    assert names.display_names(bundle_ids=[token], country="ru") == {token: e.name}
    assert forbid_archive == []


def test_apple_name_not_overridden_and_fallback_fills_only_missing(forbid_archive):
    a, b = _A[0], _A[1]
    # Apple знает a (со своим именем), не знает b и 404.
    store = FakeStore({"us": {a.track_id: ("x.apple", "Apple Name")}})
    names, _, _ = make(store)
    out = names.display_names([a.track_id, b.track_id, 404], device_locale="en")
    assert out == {str(a.track_id): "Apple Name", str(b.track_id): b.name}
    assert "404" not in out
    assert forbid_archive == []


def test_bundle_apple_name_not_overridden(forbid_archive):
    e = _A_WITH_BUNDLE[0]
    store = FakeStore({"ru": {e.track_id: (e.bundle_id, "Из Apple")}})
    names, _, _ = make(store)
    assert names.display_names(bundle_ids=[e.bundle_id], country="ru") == {e.bundle_id: "Из Apple"}


def test_fallback_keyed_by_exact_tokens(forbid_archive):
    e = _A[0]
    store = FakeStore({})
    names, _, _ = make(store)
    out = names.display_names([e.track_id, f" {e.track_id} "], country="us")
    assert out == {str(e.track_id): e.name, f" {e.track_id} ": e.name}


def test_tier_b_entries_not_used_as_fallback(forbid_archive):
    assert _B_ONLY
    store = FakeStore({})
    names, _, _ = make(store)
    assert names.display_names([e.track_id for e in _B_ONLY], country="us") == {}


def test_fallback_can_be_disabled():
    store = FakeStore({})
    names, _, _ = make(store, builtin_fallback=False)
    assert names.display_names([_A[0].track_id], country="us") == {}


def test_fallback_still_chunks_and_chains_for_apple(forbid_archive):
    e = _A[0]
    ids = list(range(1, 201)) + [e.track_id]
    store = FakeStore({"us": {5: ("b.5", "Five")}})
    names, _, _ = make(store)
    out = names.display_names(ids, account_country="DE", device_locale="en_RU")
    assert out == {"5": "Five", str(e.track_id): e.name}
    assert [(c[1], len(c[2])) for c in store.calls] == [
        ("de", 100), ("de", 100), ("de", 1), ("ru", 100), ("ru", 100), ("ru", 1),
        ("us", 100), ("us", 100), ("us", 1)]
