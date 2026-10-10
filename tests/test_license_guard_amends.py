"""Семантика цепочек журнала (Лена, 2026-10-10) для license_guard.

* ``voids: <id>`` гасит ошибочную запись: в лимит не идёт.
* ``amends: <id>`` + ``status`` меняет статус цепочки; в лимит идёт последний
  статус (acquired / acquired_download_failed / purchase_uncertain — да,
  voided — нет); сама поправка не считается (нет двойного счёта).
* Ссылка на несуществующий id игнорируется.
* Старые записи без id гасятся только явным ``voids`` по паре; по ``amends``
  не догадываемся (псевдонима amends=voids нет).

Запуск: положить рядом проверяемый license_guard.py (или PYTHONPATH) и
``pytest test_license_guard_amends.py``. На текущем maks-share/license_guard.py
(sha256 fc1a2a71…ea23) падают тесты с пометкой [fails on fc1a2a71].
"""

from __future__ import annotations

import datetime as dt
import json
from pathlib import Path

import apprestore_core.license_guard as lg

FIXED = dt.datetime(2026, 10, 10, 12, 0, 0, tzinfo=dt.timezone.utc)


def _now():
    return FIXED


def _append(path: Path, entry: dict) -> None:
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(entry, ensure_ascii=False) + "\n")


def _amend(path: Path, target: str, status: str, track: str = "389801252", link_id: str = "") -> None:
    _append(path, {"id": link_id or f"amend-{target}-{status}", "time": "2026-10-10T12:00:05+00:00",
                   "track_id": track, "status": status, "amends": target, "reason": "test"})


def test_acquired_then_amended_to_download_failed_counts_once(tmp_path):
    """[fails on fc1a2a71: 2/2] acquired → amends: acquired_download_failed = 1."""
    j = tmp_path / "j.jsonl"
    e = lg.record_acquire("389801252", status="acquired", journal_path=j, now=_now)
    _amend(j, e["id"], "acquired_download_failed")
    assert lg.read_counts(j, now=_now) == (1, 1)


def test_purchase_uncertain_then_voided_counts_zero(tmp_path):
    j = tmp_path / "j.jsonl"
    e = lg.record_acquire("492224193", status="purchase_uncertain", journal_path=j, now=_now)
    lg.record_void(e["id"], "2040 refusal", track_id="492224193", journal_path=j, now=_now)
    assert lg.read_counts(j, now=_now) == (0, 0)


def test_amended_then_voided_counts_zero(tmp_path):
    """[fails on fc1a2a71: 1/1, поправка считается отдельно]"""
    j = tmp_path / "j.jsonl"
    e = lg.record_acquire("389801252", status="acquired", journal_path=j, now=_now)
    _amend(j, e["id"], "acquired_download_failed")
    lg.record_void(e["id"], "mistake", track_id="389801252", journal_path=j, now=_now)
    assert lg.read_counts(j, now=_now) == (0, 0)


def test_last_status_of_chain_wins(tmp_path):
    """[fails on fc1a2a71] uncertain → acquired → download_failed = 1; → voided via amends = 0."""
    j = tmp_path / "j.jsonl"
    e = lg.record_acquire("389801252", status="purchase_uncertain", journal_path=j, now=_now)
    _amend(j, e["id"], "acquired", link_id="a1")
    _amend(j, e["id"], "acquired_download_failed", link_id="a2")
    assert lg.read_counts(j, now=_now) == (1, 1)
    _amend(j, "a2", "voided", link_id="a3")  # поправка на поправку → та же цепочка
    assert lg.read_counts(j, now=_now) == (0, 0)


def test_void_with_unknown_id_is_ignored(tmp_path):
    j = tmp_path / "j.jsonl"
    lg.record_acquire("389801252", status="acquired", journal_path=j, now=_now)
    lg.record_void("no-such-id", "typo", track_id="389801252", journal_path=j, now=_now)
    assert lg.read_counts(j, now=_now) == (1, 1)


def test_amend_with_unknown_id_is_ignored_and_not_counted(tmp_path):
    """[fails on fc1a2a71: 2/2]"""
    j = tmp_path / "j.jsonl"
    lg.record_acquire("389801252", status="acquired", journal_path=j, now=_now)
    _amend(j, "no-such-id", "acquired_download_failed")
    assert lg.read_counts(j, now=_now) == (1, 1)


def test_two_attempts_same_second_only_one_voided(tmp_path):
    j = tmp_path / "j.jsonl"
    a = lg.record_acquire("492224193", status="purchase_uncertain", journal_path=j, now=_now)
    b = lg.record_acquire("492224193", status="purchase_uncertain", journal_path=j, now=_now)
    assert (a["time"], a["track_id"]) == (b["time"], b["track_id"])
    lg.record_void(a["id"], "only A", track_id="492224193", journal_path=j, now=_now)
    assert lg.read_counts(j, now=_now) == (1, 1)


def test_two_attempts_same_second_amend_one_void_other(tmp_path):
    """[fails on fc1a2a71: 2/2]"""
    j = tmp_path / "j.jsonl"
    a = lg.record_acquire("492224193", status="acquired", journal_path=j, now=_now)
    b = lg.record_acquire("492224193", status="purchase_uncertain", journal_path=j, now=_now)
    _amend(j, a["id"], "acquired_download_failed", track="492224193")
    lg.record_void(b["id"], "only B", track_id="492224193", journal_path=j, now=_now)
    assert lg.read_counts(j, now=_now) == (1, 1)


def test_legacy_attempt_a_voided_by_pair_counts_zero(tmp_path):
    """Реальная строка попытки A (без id) + voids по паре → 0/0."""
    j = tmp_path / "j.jsonl"
    _append(j, {"time": "2026-10-10T15:16:42+00:00", "track_id": "492224193", "app_id": "492224193",
                "bundle_id": "", "storefront": "us", "status": "purchase_uncertain", "price": 0.0,
                "mode": "real"})
    assert lg.read_counts(j) == (1, 1)
    lg.record_void(None, "erroneous: 2040", legacy_match=("2026-10-10T15:16:42+00:00", "492224193"),
                   journal_path=j)
    assert lg.read_counts(j) == (0, 0)


def test_legacy_row_is_not_voided_by_amends(tmp_path):
    """[fails on fc1a2a71: 0/0] без id гасит только явный voids, amends-словарь не понимаем."""
    j = tmp_path / "j.jsonl"
    _append(j, {"time": "2026-10-10T15:16:42+00:00", "track_id": "492224193",
                "status": "purchase_uncertain", "price": 0.0})
    _append(j, {"id": "x", "time": "2026-10-10T15:20:00+00:00", "track_id": "492224193",
                "status": "voided", "amends": {"time": "2026-10-10T15:16:42+00:00", "track_id": "492224193"}})
    assert lg.read_counts(j) == (1, 1)


def test_amendment_keeps_original_time_for_daily_window(tmp_path):
    """[fails on fc1a2a71] суточное окно — по времени исходной записи, а не поправки."""
    j = tmp_path / "j.jsonl"
    old = FIXED - dt.timedelta(days=2)
    e = lg.record_acquire("389801252", status="acquired", journal_path=j, now=lambda: old)
    _amend(j, e["id"], "acquired_download_failed")  # поправка «сегодня»
    assert lg.read_counts(j, now=_now) == (0, 1)


def test_record_amend_api(tmp_path):
    """[fails on fc1a2a71: нет record_amend] append-only, ссылка по id, неизвестный id → None."""
    j = tmp_path / "j.jsonl"
    e = lg.record_acquire("389801252", status="acquired", journal_path=j, now=_now)
    before = j.read_bytes()
    link = lg.record_amend(e["id"], "acquired_download_failed", "download failed",
                           track_id="389801252", journal_path=j, now=_now)
    assert link["amends"] == e["id"] and link["status"] == "acquired_download_failed"
    assert j.read_bytes().startswith(before)
    assert lg.read_counts(j, now=_now) == (1, 1)
    size = j.stat().st_size
    assert lg.record_amend("no-such-id", "acquired", journal_path=j) is None
    assert j.stat().st_size == size
