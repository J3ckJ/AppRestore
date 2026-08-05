"""Network bounds, caching, and deterministic search concurrency tests."""

from __future__ import annotations

import json
import threading
import time
import unittest
import urllib.parse
from unittest import mock

from apprestore_core import catalog, web_discovery


class _FakeResponse:
    def __init__(
        self,
        body: bytes,
        *,
        content_type: str = "application/json",
        content_length: int | None = None,
    ) -> None:
        self.body = body
        self.headers = {"Content-Type": content_type}
        if content_length is not None:
            self.headers["Content-Length"] = str(content_length)

    def __enter__(self) -> _FakeResponse:
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def read(self, size: int = -1) -> bytes:
        return self.body if size < 0 else self.body[:size]


def _json_response(payload: object) -> _FakeResponse:
    return _FakeResponse(json.dumps(payload).encode("utf-8"))


class HttpSafetyAndCacheTests(unittest.TestCase):
    def setUp(self) -> None:
        catalog._HTTP_JSON_CACHE.clear()
        web_discovery._TEXT_CACHE.clear()
        web_discovery._WEB_CACHE.clear()

    def tearDown(self) -> None:
        catalog._HTTP_JSON_CACHE.clear()
        web_discovery._TEXT_CACHE.clear()
        web_discovery._WEB_CACHE.clear()

    def test_json_cache_uses_monotonic_ttl_and_returns_copies(self) -> None:
        clock = [100.0]
        payload = {"results": [{"trackId": 123456789}]}
        responses = mock.Mock(
            side_effect=[_json_response(payload), _json_response(payload)]
        )
        with (
            mock.patch("apprestore_core.catalog.time.monotonic", side_effect=lambda: clock[0]),
            mock.patch("urllib.request.urlopen", responses),
        ):
            first = catalog._http_json("https://example.test/cache-positive")
            self.assertIsInstance(first, dict)
            first["results"][0]["trackId"] = 1

            clock[0] = 399.0
            second = catalog._http_json("https://example.test/cache-positive")
            self.assertEqual(second["results"][0]["trackId"], 123456789)
            self.assertEqual(responses.call_count, 1)

            clock[0] = 401.0
            catalog._http_json("https://example.test/cache-positive")
            self.assertEqual(responses.call_count, 2)

    def test_empty_json_has_only_short_negative_ttl(self) -> None:
        clock = [10.0]
        responses = mock.Mock(
            side_effect=[
                _json_response({"resultCount": 0, "results": []}),
                _json_response(
                    {"resultCount": 1, "results": [{"trackId": 123456789}]}
                ),
            ]
        )
        with (
            mock.patch("apprestore_core.catalog.time.monotonic", side_effect=lambda: clock[0]),
            mock.patch("urllib.request.urlopen", responses),
        ):
            first = catalog._http_json("https://example.test/cache-negative")
            self.assertEqual(first["results"], [])
            clock[0] = 14.0
            catalog._http_json("https://example.test/cache-negative")
            self.assertEqual(responses.call_count, 1)
            clock[0] = 16.0
            refreshed = catalog._http_json("https://example.test/cache-negative")
            self.assertEqual(refreshed["results"][0]["trackId"], 123456789)
            self.assertEqual(responses.call_count, 2)

    def test_json_rejects_html_and_oversized_responses(self) -> None:
        too_large = b"{" + b" " * catalog._NETWORK_JSON_LIMIT + b"}"
        responses = mock.Mock(
            side_effect=[
                _FakeResponse(b"<html>blocked</html>", content_type="text/html"),
                _FakeResponse(too_large),
                _FakeResponse(
                    b"{}",
                    content_length=catalog._NETWORK_JSON_LIMIT + 1,
                ),
                _FakeResponse(b"[1, 2, 3]"),
            ]
        )
        with mock.patch("urllib.request.urlopen", responses):
            self.assertIsNone(catalog._http_json("https://example.test/html"))
            self.assertIsNone(catalog._http_json("https://example.test/large"))
            self.assertIsNone(catalog._http_json("https://example.test/declared"))
            self.assertIsNone(catalog._http_json("https://example.test/array"))

    def test_html_fetch_rejects_non_html_and_oversized_body(self) -> None:
        too_large = b"x" * (web_discovery._NETWORK_TEXT_LIMIT + 1)
        responses = mock.Mock(
            side_effect=[
                _FakeResponse(b"{}", content_type="application/json"),
                _FakeResponse(too_large, content_type="text/html"),
            ]
        )
        with mock.patch("urllib.request.urlopen", responses):
            self.assertIsNone(web_discovery._http_text("https://example.test/json"))
            self.assertIsNone(web_discovery._http_text("https://example.test/huge"))

    def test_ttl_cache_is_lru_bounded(self) -> None:
        cache = catalog._BoundedTtlCache(max_entries=2, clock=lambda: 0.0)
        cache.put("a", 1, ttl=10)
        cache.put("b", 2, ttl=10)
        self.assertEqual(cache.get("a"), (True, 1))
        cache.put("c", 3, ttl=10)
        self.assertEqual(cache.get("b"), (False, None))
        self.assertEqual(cache.get("a"), (True, 1))
        self.assertEqual(cache.get("c"), (True, 3))
        self.assertEqual(len(cache), 2)


class ConcurrentSearchTests(unittest.TestCase):
    def test_store_id_parser_rejects_implausible_lengths(self) -> None:
        self.assertIsNone(catalog._store_id_from_value(1234567))
        self.assertEqual(catalog._store_id_from_value(12345678), "12345678")
        self.assertEqual(
            catalog._store_id_from_value("123456789012"),
            "123456789012",
        )
        self.assertIsNone(catalog._store_id_from_value("1234567890123"))

    def test_bundle_lookup_requires_an_exact_bundle_identity(self) -> None:
        payloads = (
            {"results": [{"trackId": 123456789}]},
            {
                "results": [
                    {
                        "trackId": 987654321,
                        "bundleId": "com.example.other",
                    }
                ]
            },
        )
        with mock.patch(
            "apprestore_core.catalog._http_json",
            side_effect=payloads,
        ):
            result = catalog.lookup_itunes_store_id(
                "com.example.expected",
                countries=("ru", "us"),
            )

        self.assertIsNone(result)

    def test_bundle_lookup_can_use_a_later_exact_result(self) -> None:
        payload = {
            "results": [
                {
                    "trackId": 987654321,
                    "bundleId": "com.example.other",
                },
                {
                    "trackId": 123456789,
                    "bundleId": "com.example.expected",
                },
            ]
        }
        with mock.patch(
            "apprestore_core.catalog._http_json",
            return_value=payload,
        ):
            result = catalog.lookup_itunes_store_id(
                "com.example.expected",
                countries=("ru",),
            )

        self.assertEqual(result, "123456789")

    def test_itunes_search_skips_rows_without_a_valid_bundle_id(self) -> None:
        payload = {
            "results": [
                {"trackId": 123456789, "trackName": "Missing"},
                {
                    "trackId": 234567890,
                    "bundleId": "bad/bundle",
                    "trackName": "Invalid",
                },
                {
                    "trackId": 345678901,
                    "bundleId": "com.example.valid",
                    "trackName": "Valid",
                },
            ]
        }
        with mock.patch(
            "apprestore_core.catalog._http_json",
            return_value=payload,
        ):
            result = catalog.search_itunes_apps(
                "Example",
                limit=10,
                countries=("ru",),
            )

        self.assertEqual(
            result,
            [
                {
                    "storeId": "345678901",
                    "bundleId": "com.example.valid",
                    "name": "Valid",
                    "source": "itunes",
                }
            ],
        )

    def test_store_lookup_requires_matching_id_and_valid_bundle(self) -> None:
        payloads = (
            {
                "results": [
                    {
                        "trackId": 987654321,
                        "bundleId": "com.example.wrong",
                    }
                ]
            },
            {
                "results": [
                    {"trackId": 123456789, "trackName": "Missing bundle"}
                ]
            },
        )
        with mock.patch(
            "apprestore_core.catalog._http_json",
            side_effect=payloads,
        ):
            result = catalog.lookup_itunes_app_by_store_id(
                "123456789",
                countries=("ru", "us"),
            )

        self.assertIsNone(result)

    def test_duckduckgo_redirect_decoder_checks_the_hostname_boundary(self) -> None:
        target = "https://apps.apple.com/app/example/id123456789"
        legitimate = (
            "https://html.duckduckgo.com/l/?uddg="
            + urllib.parse.quote(target, safe="")
        )
        lookalike = (
            "https://notduckduckgo.com/l/?uddg="
            + urllib.parse.quote(target, safe="")
        )

        self.assertEqual(
            web_discovery._decode_duckduckgo_redirect(legitimate),
            target,
        )
        self.assertEqual(
            web_discovery._decode_duckduckgo_redirect(lookalike),
            lookalike,
        )

    def test_repeated_deadlines_do_not_create_unbounded_worker_pools(self) -> None:
        release = threading.Event()

        def blocked(value: int) -> int:
            release.wait(timeout=2)
            return value

        try:
            for _ in range(20):
                catalog._ordered_parallel_map(
                    blocked,
                    range(4),
                    max_workers=4,
                    timeout=0.001,
                )
            workers = [
                thread
                for thread in threading.enumerate()
                if thread.name.startswith("apprestore-net")
            ]
            self.assertLessEqual(len(workers), catalog._NETWORK_WORKERS)
        finally:
            release.set()

    def test_nested_parallel_work_uses_a_separate_orchestration_pool(self) -> None:
        def nested(value: int) -> int:
            inner = catalog._ordered_parallel_map(
                lambda item: item + value,
                range(3),
                max_workers=3,
                timeout=2,
            )
            return sum(item for item in inner if item is not None)

        outer = catalog._ordered_parallel_map(
            nested,
            range(6),
            max_workers=6,
            timeout=3,
            executor=catalog._ORCHESTRATION_EXECUTOR,
        )

        self.assertEqual(outer, [3, 6, 9, 12, 15, 18])

    def test_lookup_keeps_storefront_precedence_when_later_one_is_faster(self) -> None:
        barrier = threading.Barrier(2)

        def fake_http(url: str, *, timeout: float = 20) -> object:
            del timeout
            country = urllib.parse.parse_qs(
                urllib.parse.urlparse(url).query
            )["country"][0]
            barrier.wait(timeout=2)
            if country == "ru":
                time.sleep(0.02)
            return {
                "results": [
                    {
                        "bundleId": "com.example.alpha",
                        "trackId": 111111111 if country == "ru" else 222222222,
                    }
                ]
            }

        with mock.patch("apprestore_core.catalog._http_json", side_effect=fake_http):
            store_id = catalog.lookup_itunes_store_id(
                "com.example.alpha",
                countries=("ru", "us"),
            )

        self.assertEqual(store_id, "111111111")

    def test_storefronts_run_concurrently_but_merge_in_country_order(self) -> None:
        countries = ("ru", "us", "kz", "de", "gb", "tr")
        store_ids = {
            country: 100_000_000 + index
            for index, country in enumerate(countries, 1)
        }
        barrier = threading.Barrier(4)
        state_lock = threading.Lock()
        active = 0
        maximum_active = 0
        calls: list[str] = []

        def fake_http(url: str, *, timeout: float = 20) -> object:
            del timeout
            nonlocal active, maximum_active
            country = urllib.parse.parse_qs(
                urllib.parse.urlparse(url).query
            )["country"][0]
            with state_lock:
                active += 1
                maximum_active = max(maximum_active, active)
                calls.append(country)
            try:
                barrier.wait(timeout=2)
                # Reverse completion order relative to storefront priority.
                time.sleep((4 - countries.index(country)) * 0.003)
                return {
                    "results": [
                        {
                            "trackId": store_ids[country],
                            "bundleId": f"com.example.{country}",
                            "trackName": f"Alpha {country}",
                        }
                    ]
                }
            finally:
                with state_lock:
                    active -= 1

        with mock.patch("apprestore_core.catalog._http_json", side_effect=fake_http):
            apps = catalog.search_itunes_apps(
                "Alpha",
                limit=4,
                countries=countries,
            )

        self.assertEqual(
            [app["storeId"] for app in apps],
            [str(store_ids[country]) for country in countries[:4]],
        )
        self.assertEqual(maximum_active, 4)
        # The first wave satisfied the limit, so the later storefronts were skipped.
        self.assertEqual(set(calls), set(countries[:4]))

    def test_catalog_sources_run_concurrently(self) -> None:
        barrier = threading.Barrier(2)
        state_lock = threading.Lock()
        active = 0
        maximum_active = 0

        def row(source: str) -> list[dict[str, str]]:
            nonlocal active, maximum_active
            with state_lock:
                active += 1
                maximum_active = max(maximum_active, active)
            try:
                barrier.wait(timeout=2)
                return [
                    {
                        "storeId": "123456789" if source == "itunes" else "987654321",
                        "bundleId": "com.example.alpha" if source == "itunes" else "",
                        "name": "Alpha",
                        "source": source,
                    }
                ]
            finally:
                with state_lock:
                    active -= 1

        with (
            mock.patch(
                "apprestore_core.catalog.search_itunes_apps",
                side_effect=lambda *_args, **_kwargs: row("itunes"),
            ),
            mock.patch(
                "apprestore_core.catalog.search_ipafilezone_apps",
                side_effect=lambda *_args, **_kwargs: row("ipafilezone"),
            ),
            mock.patch(
                "apprestore_core.catalog.lookup_itunes_app_by_store_id",
                return_value=None,
            ),
        ):
            apps = catalog.search_app_catalogs("Alpha", limit=2)

        self.assertEqual(maximum_active, 2)
        self.assertEqual(
            {app["storeId"] for app in apps},
            {"123456789", "987654321"},
        )

    def test_web_page_wave_is_ordered_and_stops_after_strong_hit(self) -> None:
        barrier = threading.Barrier(3)
        state_lock = threading.Lock()
        calls: list[str] = []
        active = 0
        maximum_active = 0
        bodies = {
            "search.brave.com": (
                '<a href="https://apps.apple.com/ru/app/alpha/id111111111">'
                "Alpha</a>"
            ),
            "html.duckduckgo.com": (
                '<a href="https://apps.apple.com/ru/app/alpha/id222222222">'
                "Alpha</a>"
            ),
            "lite.duckduckgo.com": (
                '<a href="https://apps.apple.com/ru/app/alpha/id333333333">'
                "Alpha</a>"
            ),
        }

        def fake_text(url: str, *, timeout: float = 20) -> str:
            del timeout
            nonlocal active, maximum_active
            host = urllib.parse.urlparse(url).netloc
            with state_lock:
                calls.append(host)
                active += 1
                maximum_active = max(maximum_active, active)
            try:
                barrier.wait(timeout=2)
                if host == "search.brave.com":
                    time.sleep(0.02)
                return bodies[host]
            finally:
                with state_lock:
                    active -= 1

        with mock.patch(
            "apprestore_core.web_discovery._http_text",
            side_effect=fake_text,
        ):
            pages = web_discovery._search_pages("Alpha")

        expected_hosts = [
            "search.brave.com",
            "html.duckduckgo.com",
            "lite.duckduckgo.com",
        ]
        self.assertEqual(pages, [bodies[host] for host in expected_hosts])
        self.assertEqual(set(calls), set(expected_hosts))
        self.assertEqual(maximum_active, 3)


class WebResultCacheTests(unittest.TestCase):
    def setUp(self) -> None:
        web_discovery._WEB_CACHE.clear()

    def tearDown(self) -> None:
        web_discovery._WEB_CACHE.clear()

    def test_empty_result_expires_quickly(self) -> None:
        clock = [50.0]
        found = [
            {
                "storeId": "123456789",
                "bundleId": "com.example.alpha",
                "name": "Alpha",
                "source": "web",
            }
        ]
        search = mock.Mock(side_effect=[[], found])
        with (
            mock.patch("apprestore_core.web_discovery.time.monotonic", side_effect=lambda: clock[0]),
            mock.patch(
                "apprestore_core.web_discovery.search_web_app_store_ids",
                search,
            ),
        ):
            self.assertEqual(
                web_discovery.search_web_app_store_ids_safe("Alpha"),
                [],
            )
            clock[0] = 54.0
            self.assertEqual(
                web_discovery.search_web_app_store_ids_safe("Alpha"),
                [],
            )
            self.assertEqual(search.call_count, 1)
            clock[0] = 56.0
            refreshed = web_discovery.search_web_app_store_ids_safe("Alpha")
            self.assertEqual(refreshed, found)
            self.assertEqual(search.call_count, 2)

            # Callers cannot mutate the cached representation.
            refreshed[0]["name"] = "Changed"
            self.assertEqual(
                web_discovery.search_web_app_store_ids_safe("Alpha")[0]["name"],
                "Alpha",
            )


if __name__ == "__main__":
    unittest.main()
