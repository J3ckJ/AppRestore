"""Тесты лицензионного гарда: бесплатное/платное, лимиты, запись+подсчёт."""

from __future__ import annotations

import datetime as dt
import json
from pathlib import Path

import pytest

import apprestore_core.license_guard as lg


FIXED = dt.datetime(2026, 10, 10, 12, 0, tzinfo=dt.timezone.utc)


def _now():
    return FIXED


def _seed(path: Path, entries):
    path.write_text("\n".join(json.dumps(e) for e in entries) + "\n", encoding="utf-8")


def test_free_app_allowed(tmp_path):
    v = lg.check_can_acquire("389801252", 0, journal_path=tmp_path / "j.jsonl", now=_now)
    assert v.allowed and v.reason == "ok"
    assert v.used_today == 0 and v.used_total == 0
    assert bool(v) is True


def test_paid_app_denied(tmp_path):
    v = lg.check_can_acquire("123", 9.99, journal_path=tmp_path / "j.jsonl", now=_now)
    assert not v.allowed
    assert "платное" in v.reason


def test_price_unknown_denied(tmp_path):
    v = lg.check_can_acquire("123", None, journal_path=tmp_path / "j.jsonl", now=_now)
    assert not v.allowed and "цена не подтверждена" in v.reason


def test_missing_track_id_denied(tmp_path):
    v = lg.check_can_acquire("", 0, journal_path=tmp_path / "j.jsonl", now=_now)
    assert not v.allowed and "track_id" in v.reason


def test_daily_limit_denied(tmp_path):
    path = tmp_path / "j.jsonl"
    _seed(path, [{"time": (FIXED - dt.timedelta(hours=1)).isoformat(), "track_id": str(i)}
                 for i in range(5)])
    v = lg.check_can_acquire("999", 0, journal_path=path, daily_limit=5, now=_now)
    assert not v.allowed and "сутки" in v.reason
    assert v.used_today == 5


def test_daily_limit_resets_after_24h(tmp_path):
    path = tmp_path / "j.jsonl"
    # 5 лицензий, но все старше 24 ч -> за сутки 0, всего 5 (ниже total_limit)
    _seed(path, [{"time": (FIXED - dt.timedelta(hours=30)).isoformat(), "track_id": str(i)}
                 for i in range(5)])
    v = lg.check_can_acquire("999", 0, journal_path=path, daily_limit=5, total_limit=15, now=_now)
    assert v.allowed
    assert v.used_today == 0 and v.used_total == 5


def test_total_limit_denied(tmp_path):
    path = tmp_path / "j.jsonl"
    _seed(path, [{"time": (FIXED - dt.timedelta(days=10)).isoformat(), "track_id": str(i)}
                 for i in range(15)])
    v = lg.check_can_acquire("999", 0, journal_path=path, total_limit=15, now=_now)
    assert not v.allowed and "всего" in v.reason
    assert v.used_total == 15 and v.used_today == 0


def test_record_then_recount(tmp_path):
    path = tmp_path / "j.jsonl"
    entry = lg.record_acquire("389801252", bundle_id="com.x.y", storefront="us",
                              journal_path=path, now=_now)
    assert entry["track_id"] == "389801252"
    assert entry["bundle_id"] == "com.x.y" and entry["storefront"] == "us"
    assert lg.read_counts(path, now=_now) == (1, 1)
    # повторная запись увеличивает счётчики
    lg.record_acquire("301", journal_path=path, now=_now)
    assert lg.read_counts(path, now=_now) == (2, 2)


def test_record_has_no_secrets(tmp_path):
    path = tmp_path / "j.jsonl"
    lg.record_acquire("389801252", bundle_id="com.x", storefront="ru",
                      journal_path=path, now=_now)
    line = json.loads(path.read_text(encoding="utf-8").strip())
    assert set(line.keys()) == {"time", "track_id", "bundle_id", "storefront"}
    for banned in ("email", "appleid", "password", "token", "udid"):
        assert banned not in {k.lower() for k in line}


def test_record_enables_limit_enforcement(tmp_path):
    path = tmp_path / "j.jsonl"
    for i in range(5):
        lg.record_acquire(str(1000 + i), journal_path=path, now=_now)
    v = lg.check_can_acquire("2000", 0, journal_path=path, daily_limit=5, now=_now)
    assert not v.allowed and "сутки" in v.reason


def test_corrupt_line_ignored_but_counted_strict(tmp_path):
    path = tmp_path / "j.jsonl"
    path.write_text(
        json.dumps({"time": FIXED.isoformat(), "track_id": "1"}) + "\n"
        + "not json\n"
        + json.dumps({"track_id": "2"}) + "\n",  # нет time -> считается свежей
        encoding="utf-8",
    )
    used_today, used_total = lg.read_counts(path, now=_now)
    assert used_total == 2  # битая строка пропущена
    assert used_today == 2  # валидная свежая + запись без даты
