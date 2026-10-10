"""Тесты лицензионного гарда: цена/лимиты, статусы, выровненные поля, общий путь."""

from __future__ import annotations

import datetime as dt
import json
import time
from pathlib import Path

import pytest

import apprestore_core.license_guard as lg


FIXED = dt.datetime(2026, 10, 10, 12, 0, tzinfo=dt.timezone.utc)


def _now():
    return FIXED


def _seed(path: Path, entries):
    path.write_text("\n".join(json.dumps(e) for e in entries) + "\n", encoding="utf-8")


# -------------------------------------------------------------- цена/лимиты


def test_free_app_allowed(tmp_path):
    v = lg.check_can_acquire("389801252", 0, journal_path=tmp_path / "j.jsonl", now=_now)
    assert v.allowed and v.reason == "ok"
    assert v.used_today == 0 and v.used_total == 0
    assert bool(v) is True


def test_paid_app_denied(tmp_path):
    v = lg.check_can_acquire("123", 9.99, journal_path=tmp_path / "j.jsonl", now=_now)
    assert not v.allowed and "платное" in v.reason


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


# -------------------------------------------------------------- статусы в лимите


def test_download_failed_status_counts_toward_limit(tmp_path):
    """Лицензия взята, но скачивание упало — всё равно считается в лимите."""
    path = tmp_path / "j.jsonl"
    _seed(path, [
        {"time": (FIXED - dt.timedelta(hours=1)).isoformat(), "track_id": "1", "status": "acquired"},
        {"time": (FIXED - dt.timedelta(hours=1)).isoformat(), "track_id": "2",
         "status": "acquired_download_failed"},
    ])
    assert lg.read_counts(path, now=_now) == (2, 2)


def test_record_download_failed_status(tmp_path):
    path = tmp_path / "j.jsonl"
    entry = lg.record_acquire("389801252", bundle_id="com.x", storefront="us",
                              status="acquired_download_failed", price=0.0, mode="mock",
                              journal_path=path, now=_now)
    assert entry["status"] == "acquired_download_failed"
    assert lg.read_counts(path, now=_now) == (1, 1)


# -------------------------------------------------------------- выровненные поля


def test_record_aligned_fields(tmp_path):
    path = tmp_path / "j.jsonl"
    entry = lg.record_acquire("389801252", bundle_id="com.x.y", storefront="ru",
                              price=0.0, mode="gui", journal_path=path, now=_now)
    assert entry["track_id"] == "389801252"
    assert entry["app_id"] == "389801252"           # alias дублирует track_id
    assert entry["bundle_id"] == "com.x.y"
    assert entry["storefront"] == "ru"
    assert entry["status"] == "acquired"
    assert entry["price"] == 0.0
    assert entry["mode"] == "gui"
    line = json.loads(path.read_text(encoding="utf-8").strip())
    assert set(line.keys()) == {"id", "time", "track_id", "app_id", "bundle_id",
                                "storefront", "status", "price", "mode"}


def test_record_app_id_override(tmp_path):
    path = tmp_path / "j.jsonl"
    entry = lg.record_acquire("111", app_id="222", journal_path=path, now=_now)
    assert entry["track_id"] == "111" and entry["app_id"] == "222"


def test_record_has_no_secrets(tmp_path):
    path = tmp_path / "j.jsonl"
    lg.record_acquire("389801252", bundle_id="com.x", storefront="ru",
                      journal_path=path, now=_now)
    line = json.loads(path.read_text(encoding="utf-8").strip())
    for banned in ("email", "appleid", "password", "token", "udid", "dsid"):
        assert banned not in {k.lower() for k in line}


# -------------------------------------------------------------- запись + подсчёт


def test_record_then_recount(tmp_path):
    path = tmp_path / "j.jsonl"
    lg.record_acquire("389801252", bundle_id="com.x.y", storefront="us",
                      journal_path=path, now=_now)
    assert lg.read_counts(path, now=_now) == (1, 1)
    lg.record_acquire("301", journal_path=path, now=_now)
    assert lg.read_counts(path, now=_now) == (2, 2)


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
        + json.dumps({"track_id": "2"}) + "\n",
        encoding="utf-8",
    )
    used_today, used_total = lg.read_counts(path, now=_now)
    assert used_total == 2
    assert used_today == 2


# -------------------------------------------------------------- общий путь журнала


def test_default_journal_path_from_env(tmp_path, monkeypatch):
    target = tmp_path / "custom" / "lic.jsonl"
    monkeypatch.setenv(lg.JOURNAL_ENV, str(target))
    assert lg.default_journal_path() == target
    lg.record_acquire("1", now=_now)  # journal_path опущен -> берётся из env
    assert target.is_file()
    assert lg.read_counts(now=_now) == (1, 1)


def test_default_journal_path_without_env(monkeypatch):
    monkeypatch.delenv(lg.JOURNAL_ENV, raising=False)
    assert lg.default_journal_path() == Path.home() / ".apprestore" / "licenses_acquired.jsonl"


def test_purchase_uncertain_counts_toward_limit(tmp_path):
    """purchase упал по сети/таймауту — при сомнении считаем лицензию взятой."""
    path = tmp_path / "j.jsonl"
    _seed(path, [
        {"time": (FIXED - dt.timedelta(hours=1)).isoformat(), "track_id": "1", "status": "acquired"},
        {"time": (FIXED - dt.timedelta(hours=1)).isoformat(), "track_id": "2",
         "status": "purchase_uncertain"},
    ])
    assert lg.read_counts(path, now=_now) == (2, 2)
    assert "purchase_uncertain" in lg.ACQUIRED_STATUSES


def test_record_purchase_uncertain_status(tmp_path):
    path = tmp_path / "j.jsonl"
    entry = lg.record_acquire("389801252", status="purchase_uncertain",
                              price=0.0, mode="mock", journal_path=path, now=_now)
    assert entry["status"] == "purchase_uncertain"
    assert lg.read_counts(path, now=_now) == (1, 1)


# -------------------------------------------------------------- блокировка / гонки


def test_journal_lock_context_manager(tmp_path):
    path = tmp_path / "j.jsonl"
    with lg.journal_lock(path):
        assert (tmp_path / "j.jsonl.lock").is_file()  # отдельный lock-файл рядом


def test_acquire_and_record_allowed(tmp_path):
    path = tmp_path / "j.jsonl"
    res = lg.acquire_and_record("389801252", 0, purchase=lambda: "acquired",
                                journal_path=path, bundle_id="com.x", storefront="us",
                                mode="gui", now=_now)
    assert res.allowed and res.recorded and res.status == "acquired"
    assert lg.read_counts(path, now=_now) == (1, 1)


def test_acquire_and_record_denied_does_not_call_purchase(tmp_path):
    path = tmp_path / "j.jsonl"
    calls = []
    res = lg.acquire_and_record("123", 4.99, purchase=lambda: calls.append(1) or "acquired",
                                journal_path=path, now=_now)
    assert not res.allowed and not res.recorded
    assert calls == []  # purchase не вызывался
    assert lg.read_counts(path, now=_now) == (0, 0)


def test_acquire_and_record_purchase_failed_not_written(tmp_path):
    path = tmp_path / "j.jsonl"
    res = lg.acquire_and_record("389801252", 0, purchase=lambda: False,
                                journal_path=path, now=_now)
    assert res.allowed and not res.recorded and res.status is None
    assert lg.read_counts(path, now=_now) == (0, 0)


def test_acquire_and_record_uncertain_counts(tmp_path):
    path = tmp_path / "j.jsonl"
    res = lg.acquire_and_record("389801252", 0, purchase=lambda: "purchase_uncertain",
                                journal_path=path, now=_now)
    assert res.recorded and res.status == "purchase_uncertain"
    assert lg.read_counts(path, now=_now) == (1, 1)


def test_parallel_acquire_respects_limit(tmp_path):
    """N параллельных попыток на лимите 2 -> ровно 2 записи."""
    import threading
    path = tmp_path / "j.jsonl"
    recorded = []
    lock = threading.Lock()

    def attempt(i):
        def purchase():
            time.sleep(0.01)  # растягиваем окно гонки
            return "acquired"
        res = lg.acquire_and_record(str(100000 + i), 0, purchase=purchase,
                                    journal_path=path, daily_limit=2, total_limit=2,
                                    mode="mock", now=_now)
        if res.recorded:
            with lock:
                recorded.append(res.entry["track_id"])

    threads = [threading.Thread(target=attempt, args=(i,)) for i in range(10)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert len(recorded) == 2, recorded
    assert lg.read_counts(path, now=_now) == (2, 2)
    # В журнале ровно 2 строки.
    assert len([ln for ln in path.read_text(encoding="utf-8").splitlines() if ln.strip()]) == 2


# -------------------------------------------------------------- APPEND-ONLY: voided

def _append_raw(path, entry):
    import json as _j
    with path.open("a", encoding="utf-8") as h:
        h.write(_j.dumps(entry, ensure_ascii=False) + "\n")


def test_voided_pair_counts_zero(tmp_path):
    """Исходная (purchase_uncertain) + voided со ссылкой -> счёт 0, обе строки видны."""
    journal = tmp_path / "licenses_acquired.jsonl"
    orig = {"time": "2026-10-10T15:16:42+00:00", "track_id": "492224193",
            "app_id": "492224193", "bundle_id": "", "storefront": "us",
            "status": "purchase_uncertain", "price": 0.0, "mode": "real"}
    _append_raw(journal, orig)
    assert lg.read_counts(journal) == (1, 1)          # до гашения считается
    lg.record_void(None, "erroneous: 2040", legacy_match=(orig["time"], orig["track_id"]), journal_path=journal)
    assert lg.read_counts(journal) == (0, 0)          # после гашения — 0/0
    lines = [l for l in journal.read_text(encoding="utf-8").splitlines() if l.strip()]
    assert len(lines) == 2                            # обе строки на месте (append-only)


def test_void_without_ref_does_not_break_counts(tmp_path):
    """voided без корректного `voids` ничего не гасит и сам не считается."""
    journal = tmp_path / "licenses_acquired.jsonl"
    good = {"time": "2026-10-10T10:00:00+00:00", "track_id": "111", "status": "acquired"}
    _append_raw(journal, good)
    _append_raw(journal, {"time": "2026-10-10T10:05:00+00:00", "track_id": "999",
                          "status": "voided"})                 # без поля voids
    _append_raw(journal, {"time": "2026-10-10T10:06:00+00:00", "track_id": "999",
                          "status": "voided", "voids": {"time": "nope", "track_id": "nope"}})
    assert lg.read_counts(journal) == (1, 1)          # считается только 'good'


def test_void_matches_only_same_time_and_track(tmp_path):
    """voided гасит ровно одну исходную по паре (time, track_id)."""
    journal = tmp_path / "licenses_acquired.jsonl"
    a = {"time": "2026-10-10T09:00:00+00:00", "track_id": "111", "status": "acquired"}
    b = {"time": "2026-10-10T09:30:00+00:00", "track_id": "111", "status": "acquired"}
    _append_raw(journal, a)
    _append_raw(journal, b)
    assert lg.read_counts(journal) == (2, 2)
    lg.record_void(None, "fix", legacy_match=(a["time"], a["track_id"]), journal_path=journal)
    assert lg.read_counts(journal) == (1, 1)          # погашена только a, b осталась


# -------------------------------------------------------------- id + void по id

def test_new_record_has_uuid_id(tmp_path):
    journal = tmp_path / "licenses_acquired.jsonl"
    e = lg.record_acquire("389801252", bundle_id="com.x", storefront="us", journal_path=journal)
    import uuid as _uuid
    assert "id" in e and isinstance(e["id"], str)
    _uuid.UUID(e["id"])                       # валидный uuid4 (иначе бросит)


def test_void_by_id_cancels_exactly_that_entry(tmp_path):
    journal = tmp_path / "licenses_acquired.jsonl"
    e = lg.record_acquire("389801252", status="purchase_uncertain", journal_path=journal)
    assert lg.read_counts(journal) == (1, 1)
    lg.record_void(e["id"], "mistake", track_id="389801252", journal_path=journal)
    assert lg.read_counts(journal) == (0, 0)
    # ссылка записана как id-строка, не словарь
    lines = journal.read_text(encoding="utf-8").splitlines()
    import json as _j
    void = _j.loads(lines[-1])
    assert void["status"] == "voided" and void["voids"] == e["id"]


def test_two_same_second_same_track_voided_individually_by_id(tmp_path):
    """Две записи одного track_id в ту же секунду -> гасятся поотдельно по id."""
    journal = tmp_path / "licenses_acquired.jsonl"
    fixed = dt.datetime(2026, 10, 10, 12, 0, 0, tzinfo=dt.timezone.utc)
    a = lg.record_acquire("492224193", status="purchase_uncertain",
                          journal_path=journal, now=lambda: fixed)
    b = lg.record_acquire("492224193", status="purchase_uncertain",
                          journal_path=journal, now=lambda: fixed)
    assert a["id"] != b["id"]
    assert a["time"] == b["time"] and a["track_id"] == b["track_id"]  # неотличимы по паре
    assert lg.read_counts(journal, now=lambda: fixed) == (2, 2)
    lg.record_void(a["id"], "only A", track_id="492224193", journal_path=journal)
    # погашена ровно A, B осталась (пара бы погасила обе — здесь нет)
    assert lg.read_counts(journal, now=lambda: fixed) == (1, 1)


def test_amends_to_voided_status_counts_zero(tmp_path):
    """`amends` НЕ алиас voids: это смена статуса; последний статус voided -> 0."""
    journal = tmp_path / "licenses_acquired.jsonl"
    e = lg.record_acquire("389801252", status="acquired", journal_path=journal)
    assert lg.read_counts(journal) == (1, 1)
    _append_raw(journal, {"id": "void-x", "time": "2026-10-10T13:00:00+00:00",
                          "track_id": "389801252", "status": "voided", "amends": e["id"],
                          "reason": "via update_status"})
    assert lg.read_counts(journal) == (0, 0)


def test_legacy_pair_still_works_without_id(tmp_path):
    """Старая запись без id + voided-словарь по паре -> 0/0 (обратная совместимость)."""
    journal = tmp_path / "licenses_acquired.jsonl"
    _append_raw(journal, {"time": "2026-10-10T15:16:42+00:00", "track_id": "492224193",
                          "status": "purchase_uncertain", "price": 0.0})   # без id
    assert lg.read_counts(journal) == (1, 1)
    lg.record_void(None, "legacy fix",
                   legacy_match=("2026-10-10T15:16:42+00:00", "492224193"), journal_path=journal)
    assert lg.read_counts(journal) == (0, 0)


# -------------------------------------------------------------- цепочки amends


def _amend_raw(path, target, status, *, link_id, track="389801252", when="2026-10-10T12:00:05+00:00"):
    _append_raw(path, {"id": link_id, "time": when, "track_id": track,
                       "status": status, "amends": target, "reason": "test"})


def test_amend_uncertain_to_acquired_counts_one(tmp_path):
    j = tmp_path / "j.jsonl"
    e = lg.record_acquire("389801252", status="purchase_uncertain", journal_path=j, now=_now)
    link = lg.record_amend(e["id"], "acquired", "verified via list-purchases",
                           track_id="389801252", journal_path=j, now=_now)
    assert link["amends"] == e["id"] and link["status"] == "acquired"
    assert link["id"] != e["id"]
    import uuid as _uuid
    _uuid.UUID(link["id"])
    assert lg.read_counts(j, now=_now) == (1, 1)


def test_amend_uncertain_to_refused_counts_zero(tmp_path):
    j = tmp_path / "j.jsonl"
    e = lg.record_acquire("389801252", status="purchase_uncertain", journal_path=j, now=_now)
    assert lg.read_counts(j, now=_now) == (1, 1)
    lg.record_amend(e["id"], "refused", "-128 Account Not In This Store", journal_path=j, now=_now)
    assert lg.read_counts(j, now=_now) == (0, 0)
    assert lg.check_can_acquire("1", 0, journal_path=j, daily_limit=1, now=_now).allowed


def test_amend_chain_of_two_last_status_wins(tmp_path):
    j = tmp_path / "j.jsonl"
    e = lg.record_acquire("389801252", status="purchase_uncertain", journal_path=j, now=_now)
    a1 = lg.record_amend(e["id"], "refused", journal_path=j, now=_now)
    assert lg.read_counts(j, now=_now) == (0, 0)
    a2 = lg.record_amend(a1["id"], "acquired", journal_path=j, now=_now)   # amends на amends
    assert a2["amends"] == a1["id"]
    assert lg.read_counts(j, now=_now) == (1, 1)                           # не 2 и не 3
    lg.record_amend(e["id"], "acquired_download_failed", journal_path=j, now=_now)
    assert lg.read_counts(j, now=_now) == (1, 1)


def test_amend_then_void_root_counts_zero_and_stays_voided(tmp_path):
    j = tmp_path / "j.jsonl"
    e = lg.record_acquire("389801252", status="purchase_uncertain", journal_path=j, now=_now)
    lg.record_amend(e["id"], "acquired", journal_path=j, now=_now)
    lg.record_void(e["id"], "mistake", track_id="389801252", journal_path=j, now=_now)
    assert lg.read_counts(j, now=_now) == (0, 0)
    lg.record_amend(e["id"], "acquired", journal_path=j, now=_now)   # не оживляет
    assert lg.read_counts(j, now=_now) == (0, 0)


def test_void_of_amend_line_reverts_only_that_amend(tmp_path):
    j = tmp_path / "j.jsonl"
    e = lg.record_acquire("389801252", status="purchase_uncertain", journal_path=j, now=_now)
    bad = lg.record_amend(e["id"], "refused", "wrong guess", journal_path=j, now=_now)
    assert lg.read_counts(j, now=_now) == (0, 0)
    lg.record_void(bad["id"], "amend was wrong", journal_path=j, now=_now)
    assert lg.read_counts(j, now=_now) == (1, 1)    # снова purchase_uncertain


def test_amend_keeps_original_time_for_daily_window(tmp_path):
    j = tmp_path / "j.jsonl"
    old = FIXED - dt.timedelta(days=2)
    e = lg.record_acquire("389801252", status="purchase_uncertain", journal_path=j, now=lambda: old)
    lg.record_amend(e["id"], "acquired", journal_path=j, now=_now)
    assert lg.read_counts(j, now=_now) == (0, 1)


def test_amend_unknown_id_writes_nothing(tmp_path):
    j = tmp_path / "j.jsonl"
    lg.record_acquire("389801252", journal_path=j, now=_now)
    size = j.stat().st_size
    assert lg.record_amend("no-such-id", "refused", journal_path=j, now=_now) is None
    assert lg.record_amend("", "refused", journal_path=j, now=_now) is None
    assert j.stat().st_size == size
    _amend_raw(j, "no-such-id", "acquired", link_id="x")   # чужая строка на неизвестный id
    assert lg.read_counts(j, now=_now) == (1, 1)


def test_amend_empty_status_rejected(tmp_path):
    j = tmp_path / "j.jsonl"
    e = lg.record_acquire("389801252", journal_path=j, now=_now)
    with pytest.raises(ValueError):
        lg.record_amend(e["id"], "", journal_path=j, now=_now)


def test_record_amend_is_append_only(tmp_path):
    j = tmp_path / "j.jsonl"
    e = lg.record_acquire("389801252", status="purchase_uncertain", journal_path=j, now=_now)
    before = j.read_bytes()
    lg.record_amend(e["id"], "acquired", journal_path=j, now=_now)
    assert j.read_bytes().startswith(before)
    assert len(j.read_text(encoding="utf-8").splitlines()) == 2


def test_mixed_legacy_and_new_format(tmp_path):
    """Старые строки без id + новые с id/amends/voids в одном журнале."""
    j = tmp_path / "j.jsonl"
    # legacy: без status (=взята) и purchase_uncertain без id
    _append_raw(j, {"time": "2026-10-10T09:00:00+00:00", "track_id": "111"})
    _append_raw(j, {"time": "2026-10-10T10:00:00+00:00", "track_id": "222",
                    "status": "purchase_uncertain", "price": 0.0})
    a = lg.record_acquire("333", status="purchase_uncertain", journal_path=j, now=_now)
    b = lg.record_acquire("444", status="purchase_uncertain", journal_path=j, now=_now)
    assert lg.read_counts(j, now=_now) == (4, 4)
    lg.record_amend(a["id"], "acquired", journal_path=j, now=_now)         # 333: 1
    lg.record_amend(b["id"], "refused", journal_path=j, now=_now)          # 444: 0
    # legacy-строку amends-словарь не меняет (по паре только voids)
    _append_raw(j, {"id": "x", "time": "2026-10-10T12:00:01+00:00", "track_id": "222",
                    "status": "refused",
                    "amends": {"time": "2026-10-10T10:00:00+00:00", "track_id": "222"}})
    assert lg.read_counts(j, now=_now) == (3, 3)
    lg.record_void(None, "legacy fix", legacy_match=("2026-10-10T10:00:00+00:00", "222"),
                   journal_path=j, now=_now)                                 # 222: 0
    assert lg.read_counts(j, now=_now) == (2, 2)                            # 111 + 333


def test_amend_lines_never_double_count_with_limit(tmp_path):
    j = tmp_path / "j.jsonl"
    e = lg.record_acquire("389801252", status="purchase_uncertain", journal_path=j, now=_now)
    for status in ("acquired", "acquired_download_failed", "acquired"):
        lg.record_amend(e["id"], status, journal_path=j, now=_now)
    v = lg.check_can_acquire("999", 0, journal_path=j, daily_limit=2, total_limit=2, now=_now)
    assert v.allowed and (v.used_today, v.used_total) == (1, 1)
