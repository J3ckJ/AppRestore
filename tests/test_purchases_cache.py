"""purchases-cache.json: only four fields, hashed account, 0600/0700, atomic,
deleted on sign-out / account switch, damaged file ignored and rewritten."""

from __future__ import annotations

import json
import os
import stat
from datetime import datetime, timezone
from pathlib import Path

import pytest

from apprestore_core.ipatool_api import Purchase
from apprestore_core.purchases_cache import (
    CachedPurchase,
    PurchasesCache,
    account_key,
)

EMAIL = "person@example.com"
OTHER = "other@example.com"


def _items(*ids: int) -> list[CachedPurchase]:
    return [CachedPurchase(i, f"com.example.app{i}", f"App {i}", "2024-01-0%dT10:00:00+00:00" % (i % 9 + 1)) for i in ids]


def test_account_key_is_a_hash_not_the_email() -> None:
    key = account_key(" Person@Example.com ")
    assert key == account_key(EMAIL)
    assert len(key) == 64 and "@" not in key and "person" not in key
    assert key != account_key(OTHER)
    assert account_key("") == ""


@pytest.mark.skipif(os.name == "nt", reason="POSIX permissions")
def test_file_is_0600_and_folder_0700(tmp_path: Path) -> None:
    root = tmp_path / "purchases"
    root.mkdir(mode=0o755)
    cache = PurchasesCache(root)
    old = os.umask(0o000)
    try:
        cache.save(account_key(EMAIL), _items(1, 2))
    finally:
        os.umask(old)
    assert stat.S_IMODE(cache.path.stat().st_mode) == 0o600
    assert stat.S_IMODE(root.stat().st_mode) == 0o700
    # A second save (os.replace) keeps 0600 and leaves no temp files behind.
    cache.save(account_key(EMAIL), _items(3))
    assert stat.S_IMODE(cache.path.stat().st_mode) == 0o600
    assert sorted(p.name for p in root.iterdir()) == ["purchases-cache.json"]


def test_file_has_no_email_guid_or_extra_fields(tmp_path: Path) -> None:
    cache = PurchasesCache(tmp_path)
    purchase = Purchase(
        track_id=42,
        bundle_id="com.example.bank",
        name="Bank",
        purchase_date=datetime(2020, 5, 1, 12, 0, tzinfo=timezone.utc),
        version="9.9",
        price=0.0,
        platforms=("iphone",),
    )
    cache.save(account_key(EMAIL), [CachedPurchase.from_purchase(purchase)])
    text = cache.path.read_text(encoding="utf-8")
    assert EMAIL not in text and "@" not in text and "guid" not in text.lower()
    payload = json.loads(text)
    assert set(payload) == {"version", "account", "updated", "complete", "total", "items"}
    assert payload["items"] == [
        {"track_id": 42, "bundle_id": "com.example.bank", "name": "Bank", "purchase_date": "2020-05-01T12:00:00+00:00"}
    ]


def test_dedupe_by_track_id_keeps_first(tmp_path: Path) -> None:
    cache = PurchasesCache(tmp_path)
    key = account_key(EMAIL)
    items = _items(1, 2) + [CachedPurchase(1, "com.example.renamed", "Renamed", "")]
    cache.save(key, items, total=2)
    snapshot = cache.load(key)
    assert snapshot is not None
    assert [i.track_id for i in snapshot.items] == [1, 2]
    assert snapshot.items[0].name == "App 1"
    assert snapshot.total == 2 and snapshot.complete


def test_other_account_file_is_deleted_on_load(tmp_path: Path) -> None:
    cache = PurchasesCache(tmp_path)
    cache.save(account_key(OTHER), _items(1))
    assert cache.load(account_key(EMAIL)) is None
    assert not cache.path.exists()


@pytest.mark.parametrize(
    "content",
    [
        b"",
        b"{not json",
        b"\xff\xfe\x00",
        b"[]",
        json.dumps({"version": 99, "account": "x", "items": []}).encode(),
        b'{"version":1,"account":"KEY","items":[{"track_id":"1"}]}',
        b'{"version":1,"account":"KEY","items":"nope"}',
    ],
)
def test_damaged_cache_is_ignored_then_rewritten(tmp_path: Path, content: bytes) -> None:
    cache = PurchasesCache(tmp_path)
    key = account_key(EMAIL)
    cache.path.parent.mkdir(parents=True, exist_ok=True)
    cache.path.write_bytes(content.replace(b"KEY", key.encode()))
    assert cache.load(key) is None
    cache.save(key, _items(7))
    snapshot = cache.load(key)
    assert snapshot is not None and [i.track_id for i in snapshot.items] == [7]


def test_delete_removes_file_and_leftover_temp(tmp_path: Path) -> None:
    cache = PurchasesCache(tmp_path)
    cache.save(account_key(EMAIL), _items(1))
    (tmp_path / ".purchases-abc.tmp").write_text("x")
    cache.delete()
    assert not cache.path.exists()
    assert not list(tmp_path.glob(".purchases-*"))
    cache.delete()  # idempotent
