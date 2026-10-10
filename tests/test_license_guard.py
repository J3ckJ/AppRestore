"""Тесты лицензионного гарда: цена/лимиты, статусы, выровненные поля, общий путь."""

from __future__ import annotations

import datetime as dt
import json
import time
from pathlib import Path

import pytest

try:
    from apprestore_core import license_guard as lg
except ImportError:
    import license_guard as lg


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


# -------------------------------------------------------------- next_daily_slot


def _row(hours_ago, track, status="acquired", eid=None):
    row = {"time": (FIXED - dt.timedelta(hours=hours_ago)).isoformat(timespec="seconds"),
           "track_id": str(track), "status": status}
    if eid:
        row["id"] = eid
    return row


def test_next_daily_slot_none_when_slot_free(tmp_path):
    j = tmp_path / "j.jsonl"
    assert lg.next_daily_slot(journal_path=j, now=_now) is None          # пустой журнал
    _seed(j, [_row(h, h) for h in (1, 2, 3, 4)])                          # 4 из 5
    assert lg.next_daily_slot(journal_path=j, now=_now) is None
    assert "next_daily_slot" in lg.__all__


def test_next_daily_slot_five_in_window_returns_oldest_plus_24h(tmp_path):
    j = tmp_path / "j.jsonl"
    _seed(j, [_row(h, h) for h in (1, 20, 5, 3, 2)])                      # порядок в файле не важен
    when = lg.next_daily_slot(journal_path=j, now=_now)
    assert when == FIXED + dt.timedelta(hours=4)                          # (FIXED-20ч)+24ч
    assert when.tzinfo is not None and when.utcoffset() == dt.timedelta(0)
    # согласовано с read_counts: сразу после when слот есть
    later = lambda: when + dt.timedelta(seconds=1)  # noqa: E731
    assert lg.read_counts(j, now=later)[0] == 4
    assert lg.next_daily_slot(journal_path=j, now=later) is None


def test_next_daily_slot_returns_utc_for_offset_times(tmp_path):
    j = tmp_path / "j.jsonl"
    msk = dt.timezone(dt.timedelta(hours=3))
    rows = [{"time": (FIXED - dt.timedelta(hours=h)).astimezone(msk).isoformat(),
             "track_id": str(h), "status": "acquired"} for h in (1, 2, 3, 4, 10)]
    _seed(j, rows)
    when = lg.next_daily_slot(journal_path=j, now=_now)
    assert when == FIXED + dt.timedelta(hours=14)
    assert when.tzinfo == dt.timezone.utc


def test_next_daily_slot_over_limit_needs_several_to_expire(tmp_path):
    j = tmp_path / "j.jsonl"
    _seed(j, [_row(h, h) for h in (23, 22, 21, 2, 1, 0)])                 # 6 в окне
    # нужно, чтобы выпали 2 самые старые: слот — после (FIXED-22ч)+24ч
    assert lg.next_daily_slot(journal_path=j, now=_now) == FIXED + dt.timedelta(hours=2)


def test_next_daily_slot_ignores_old_voided_and_amended(tmp_path):
    j = tmp_path / "j.jsonl"
    rows = [
        _row(30, 100),                                         # старше 24ч — не в окне
        _row(23, 101, eid="void-me"),                          # погашена voids
        {"status": "voided", "voids": "void-me", "track_id": "101",
         "time": FIXED.isoformat()},
        _row(22, 102, status="purchase_uncertain", eid="ref"),  # amend -> refused: 0
        {"status": "refused", "amends": "ref", "id": "a1", "track_id": "102",
         "time": FIXED.isoformat()},
        _row(21, 103, status="refused"),                       # не взята
        _row(19, 104, status="purchase_uncertain", eid="unc"),  # amend -> acquired: 1, время исходной
        {"status": "acquired", "amends": "unc", "id": "a2", "track_id": "104",
         "time": FIXED.isoformat()},
        _row(5, 105), _row(4, 106), _row(3, 107),
    ]
    _seed(j, rows)
    assert lg.read_counts(j, now=_now)[0] == 4
    assert lg.next_daily_slot(journal_path=j, now=_now) is None
    with j.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(_row(1, 108)) + "\n")
    assert lg.read_counts(j, now=_now)[0] == 5
    # самая старая считающаяся — 104 (время ИСХОДНОЙ записи 19ч назад, не время amend)
    assert lg.next_daily_slot(journal_path=j, now=_now) == FIXED + dt.timedelta(hours=5)


def test_next_daily_slot_boundary_exactly_24h(tmp_path):
    j = tmp_path / "j.jsonl"
    _seed(j, [_row(24, 1), _row(4, 2), _row(3, 3), _row(2, 4), _row(1, 5)])
    # запись ровно 24ч назад ещё в окне (как read_counts) -> слот освобождается сразу после now
    assert lg.read_counts(j, now=_now)[0] == 5
    assert lg.next_daily_slot(journal_path=j, now=_now) == FIXED
    later = lambda: FIXED + dt.timedelta(seconds=1)  # noqa: E731
    assert lg.next_daily_slot(journal_path=j, now=later) is None


def test_next_daily_slot_undated_rows_block_forever(tmp_path):
    j = tmp_path / "j.jsonl"
    _seed(j, [{"time": "garbage", "track_id": str(i)} for i in range(5)])
    assert lg.next_daily_slot(journal_path=j, now=_now) is lg.NEVER


def test_next_daily_slot_respects_custom_limit(tmp_path):
    j = tmp_path / "j.jsonl"
    _seed(j, [_row(10, 1), _row(2, 2)])
    assert lg.next_daily_slot(journal_path=j, daily_limit=2, now=_now) == FIXED + dt.timedelta(hours=14)
    assert lg.next_daily_slot(journal_path=j, daily_limit=3, now=_now) is None


# ------------------------------------------------------------------ preflight


@pytest.mark.parametrize("gate", [False, "нужна патченая сборка"])
def test_preflight_block_skips_purchase_and_journal(tmp_path, gate):
    journal = tmp_path / "j" / "licenses_acquired.jsonl"
    calls = []
    res = lg.acquire_and_record("1", 0, purchase=lambda: calls.append(1) or "purchase_uncertain",
                                preflight=lambda: gate, journal_path=journal)
    assert calls == [] and not res.allowed and not res.recorded
    assert res.code == lg.PREFLIGHT_BLOCKED and (res.used_today, res.used_total) == (-1, -1)
    assert res.reason == (gate if isinstance(gate, str) else "preflight запретил взятие лицензии")
    assert not journal.parent.exists()


@pytest.mark.parametrize("gate", [None, True])
def test_preflight_pass_keeps_normal_path(tmp_path, gate):
    journal = tmp_path / "licenses_acquired.jsonl"
    res = lg.acquire_and_record("1", 0, purchase=lambda: "acquired", preflight=lambda: gate,
                                journal_path=journal)
    assert res.recorded and res.code is None and res.used_total == 1


# -------------------------------------------------------------- LEGAL.md §1.14
# Удалённое приложение (region_probe: DELISTED / NOT_IN_REGION) с неизвестной ценой.


def _lines(path: Path):
    if not path.is_file():
        return []
    return [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines() if x.strip()]


def _ok_preflight():
    return True


def _region(path, outcome="acquired", *, price=None, session=None, flag=True, **kw):
    calls = []

    def purchase():
        calls.append(1)
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome

    sess = lg.RegionAttemptSession() if session is None else session
    kw.setdefault("preflight", _ok_preflight)  # §1.14 п.3: на пути исключения обязателен
    res = lg.acquire_and_record("555000111", price, purchase=purchase, journal_path=path,
                                region_unavailable=flag, region_session=sess,
                                bundle_id="ru.bank.app", storefront="143469", mode="gui",
                                now=_now, **kw)
    return res, calls, sess


def test_region_flag_off_unknown_price_still_refused(tmp_path):
    path = tmp_path / "j.jsonl"
    v = lg.check_can_acquire("555000111", None, journal_path=path, now=_now)
    assert not v.allowed and "цена не подтверждена" in v.reason
    assert v.region_exception is False
    res, calls, _ = _region(path, flag=False)
    assert not res.allowed and not res.recorded and calls == []
    assert "цена не подтверждена" in res.reason
    assert not path.exists() or _lines(path) == []


@pytest.mark.parametrize("truthy", [1, "true", "DELISTED", object()])
def test_region_flag_must_be_strictly_true(tmp_path, truthy):
    v = lg.check_can_acquire("555000111", None, journal_path=tmp_path / "j.jsonl",
                             region_unavailable=truthy, now=_now)
    assert not v.allowed and "цена не подтверждена" in v.reason


def test_region_flag_on_unknown_price_allowed(tmp_path):
    v = lg.check_can_acquire("555000111", None, journal_path=tmp_path / "j.jsonl",
                             region_unavailable=True, now=_now)
    assert v.allowed and v.reason == "ok" and v.region_exception is True


@pytest.mark.parametrize("price", [0.99, 1, "149", 379.0])
def test_region_flag_on_known_paid_price_refused(tmp_path, price):
    path = tmp_path / "j.jsonl"
    v = lg.check_can_acquire("555000111", price, journal_path=path,
                             region_unavailable=True, now=_now)
    assert not v.allowed and "платное" in v.reason and v.region_exception is False
    res, calls, sess = _region(path, price=price)
    assert not res.allowed and "платное" in res.reason and calls == []
    assert len(sess) == 0  # попытки не было — сессия не тратится


@pytest.mark.parametrize("price", ["abc", float("nan"), float("inf"), True])
def test_region_garbage_price_does_not_open_exception(tmp_path, price):
    path = tmp_path / "j.jsonl"
    res, calls, _ = _region(path, price=price)
    assert not res.allowed and calls == []
    assert "цена не подтверждена" in res.reason


def test_nan_price_no_longer_passes_as_free(tmp_path):
    v = lg.check_can_acquire("123", float("nan"), journal_path=tmp_path / "j.jsonl", now=_now)
    assert not v.allowed and "цена не подтверждена" in v.reason


def test_region_known_zero_price_is_normal_path_without_price_source(tmp_path):
    path = tmp_path / "j.jsonl"
    res, calls, sess = _region(path, price=0)
    assert res.recorded and res.status == "acquired" and calls == [1]
    assert "price_source" not in res.entry and res.entry["price"] == 0.0
    assert len(sess) == 0  # обычный путь сессию §1.14 не трогает


def test_region_granted_records_acquired_with_price_source(tmp_path):
    path = tmp_path / "j.jsonl"
    res, calls, sess = _region(path, "acquired")
    assert res.allowed and res.recorded and res.status == "acquired" and calls == [1]
    assert res.entry["price_source"] == "apple_fixed_0" == lg.PRICE_SOURCE_APPLE_FIXED_0
    assert res.entry["price"] is None
    assert res.used_today == 1 and res.used_total == 1
    [line] = _lines(path)
    assert line["status"] == "acquired" and line["price_source"] == "apple_fixed_0"
    assert line["track_id"] == "555000111" and line["price"] is None
    assert lg.read_counts(path, now=_now) == (1, 1)
    assert sess.attempted("555000111")


@pytest.mark.parametrize("outcome", [True, "ok", "success", "ACQUIRED"])
def test_region_success_variants(tmp_path, outcome):
    res, _, _ = _region(tmp_path / "j.jsonl", outcome)
    assert res.recorded and res.status == "acquired"


@pytest.mark.parametrize("outcome", [False, "refused", "failed", "apple_refused", "Rejected"])
def test_region_explicit_refusal_not_recorded_not_counted(tmp_path, outcome):
    path = tmp_path / "j.jsonl"
    res, calls, sess = _region(path, outcome)
    assert res.allowed and not res.recorded and res.status is None and calls == [1]
    assert res.code == lg.APPLE_REFUSED and res.entry is None
    assert _lines(path) == []
    assert lg.read_counts(path, now=_now) == (0, 0)
    # повтора нет: вторая попытка в той же сессии отклоняется без purchase()
    res2, calls2, _ = _region(path, "acquired", session=sess)
    assert not res2.allowed and res2.code == lg.REGION_ALREADY_ATTEMPTED and calls2 == []
    assert _lines(path) == []


@pytest.mark.parametrize("outcome", [None, "purchase_uncertain", "uncertain", "timeout", "", "weird"])
def test_region_unclear_records_uncertain_and_counts(tmp_path, outcome):
    path = tmp_path / "j.jsonl"
    res, _, _ = _region(path, outcome)
    assert res.recorded and res.status == "purchase_uncertain"
    assert res.entry["price_source"] == "apple_fixed_0"
    assert lg.read_counts(path, now=_now) == (1, 1)


def test_region_exception_in_purchase_records_uncertain_and_reraises(tmp_path):
    path = tmp_path / "j.jsonl"
    sess = lg.RegionAttemptSession()
    with pytest.raises(TimeoutError):
        _region(path, TimeoutError("ipatool timeout"), session=sess)
    [line] = _lines(path)
    assert line["status"] == "purchase_uncertain" and line["price_source"] == "apple_fixed_0"
    assert lg.read_counts(path, now=_now) == (1, 1)
    assert sess.attempted("555000111")


def test_region_uncertain_can_be_amended_like_any_entry(tmp_path):
    path = tmp_path / "j.jsonl"
    res, _, _ = _region(path, None)
    lg.record_amend(res.entry["id"], "refused", "история покупок: нет", journal_path=path, now=_now)
    assert lg.read_counts(path, now=_now) == (0, 0)


def test_region_one_attempt_per_app_per_session(tmp_path):
    path = tmp_path / "j.jsonl"
    sess = lg.RegionAttemptSession()
    first, _, _ = _region(path, "acquired", session=sess)
    assert first.recorded
    again, calls, _ = _region(path, "acquired", session=sess)
    assert not again.allowed and again.code == lg.REGION_ALREADY_ATTEMPTED and calls == []
    v = lg.check_can_acquire("555000111", None, journal_path=path, region_unavailable=True,
                             region_session=sess, now=_now)
    assert not v.allowed and v.code == lg.REGION_ALREADY_ATTEMPTED
    # другое приложение в той же сессии — можно
    other = lg.acquire_and_record("555000222", None, purchase=lambda: "acquired",
                                  preflight=_ok_preflight,
                                  journal_path=path, region_unavailable=True,
                                  region_session=sess, now=_now)
    assert other.recorded
    # новая сессия (после «Выйти») — снова можно
    sess.reset()
    third, calls3, _ = _region(path, "acquired", session=sess)
    assert third.recorded and calls3 == [1]
    assert lg.read_counts(path, now=_now) == (3, 3)


def test_region_session_not_marked_when_checks_refuse(tmp_path):
    path = tmp_path / "j.jsonl"
    _seed(path, [{"time": (FIXED - dt.timedelta(hours=1)).isoformat(), "track_id": str(i)}
                 for i in range(5)])
    sess = lg.RegionAttemptSession()
    res, calls, _ = _region(path, session=sess)
    assert not res.allowed and "сутки" in res.reason and calls == []
    assert not sess.attempted("555000111")


def test_region_requires_session_object(tmp_path):
    path = tmp_path / "j.jsonl"
    called = []
    res = lg.acquire_and_record("555000111", None, purchase=lambda: called.append(1),
                                preflight=lambda: called.append("pf"),
                                journal_path=path, region_unavailable=True, now=_now)
    assert not res.allowed and res.code == lg.REGION_SESSION_REQUIRED and called == []
    assert not path.exists()


def test_region_daily_limit_enforced(tmp_path):
    path = tmp_path / "j.jsonl"
    _seed(path, [{"time": (FIXED - dt.timedelta(hours=2)).isoformat(), "track_id": str(i)}
                 for i in range(5)])
    res, calls, _ = _region(path)
    assert not res.allowed and "сутки" in res.reason and calls == []
    assert len(_lines(path)) == 5


def test_region_total_limit_enforced(tmp_path):
    path = tmp_path / "j.jsonl"
    _seed(path, [{"time": (FIXED - dt.timedelta(days=3 + i)).isoformat(), "track_id": str(i)}
                 for i in range(15)])
    res, calls, _ = _region(path)
    assert not res.allowed and "всего" in res.reason and calls == []


def test_region_entries_count_toward_limit_for_normal_path(tmp_path):
    path = tmp_path / "j.jsonl"
    sess = lg.RegionAttemptSession()
    for i in range(3):
        lg.acquire_and_record(f"70000{i}", None, purchase=lambda: "acquired", journal_path=path,
                              preflight=_ok_preflight,
                              region_unavailable=True, region_session=sess, now=_now)
    for i in range(2):
        lg.acquire_and_record(f"80000{i}", None, purchase=lambda: None, journal_path=path,
                              preflight=_ok_preflight,
                              region_unavailable=True, region_session=sess, now=_now)
    assert lg.read_counts(path, now=_now) == (5, 5)
    v = lg.check_can_acquire("389801252", 0, journal_path=path, now=_now)
    assert not v.allowed and "сутки" in v.reason


def test_region_preflight_still_runs_first(tmp_path):
    path = tmp_path / "j.jsonl"
    res, calls, sess = _region(path, preflight=lambda: "нужен патченый ipatool")
    assert not res.allowed and res.code == lg.PREFLIGHT_BLOCKED and calls == []
    assert res.reason == "нужен патченый ipatool"
    assert not path.exists() and not sess.attempted("555000111")


def test_region_never_substitutes_price(tmp_path):
    """Гард не передаёт цену в purchase(): колбэк без аргументов, price в записи — null."""
    path = tmp_path / "j.jsonl"
    res, _, _ = _region(path, "acquired")
    assert res.entry["price"] is None and res.entry["price_source"] == "apple_fixed_0"


def test_normal_entries_have_no_price_source(tmp_path):
    path = tmp_path / "j.jsonl"
    res = lg.acquire_and_record("389801252", 0, purchase=lambda: "acquired",
                                journal_path=path, now=_now)
    assert "price_source" not in res.entry
    e = lg.record_acquire("389801253", journal_path=path, price=0, now=_now)
    assert "price_source" not in e


def test_parallel_region_attempts_respect_limit(tmp_path):
    import threading

    path = tmp_path / "j.jsonl"
    sess = lg.RegionAttemptSession()
    results = []

    def worker(i):
        results.append(lg.acquire_and_record(f"9000{i:02d}", None, purchase=lambda: "acquired",
                                             journal_path=path, region_unavailable=True,
                                             preflight=_ok_preflight,
                                             region_session=sess, daily_limit=5, now=_now))

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(12)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert sum(r.recorded for r in results) == 5
    assert lg.read_counts(path, now=_now) == (5, 5)


# ------------------------------------------------- §1.14 п.3: preflight обязателен

def test_region_requires_preflight(tmp_path):
    """region_unavailable=True + price=None + preflight=None -> отказ до журнала/purchase."""
    path = tmp_path / "j.jsonl"
    called = []
    sess = lg.RegionAttemptSession()
    res = lg.acquire_and_record("555000111", None, purchase=lambda: called.append(1),
                                journal_path=path, region_unavailable=True,
                                region_session=sess, now=_now)
    assert not res.allowed and not res.recorded and res.status is None
    assert res.code == lg.REGION_PREFLIGHT_REQUIRED == "region_preflight_required"
    assert res.entry is None and res.used_today == -1 and res.used_total == -1
    assert called == []
    assert not path.exists()
    assert len(sess) == 0 and not sess.attempted("555000111")


def test_region_explicit_none_preflight_refused_via_helper(tmp_path):
    path = tmp_path / "j.jsonl"
    res, calls, sess = _region(path, "acquired", preflight=None)
    assert not res.allowed and res.code == lg.REGION_PREFLIGHT_REQUIRED and calls == []
    assert not path.exists() and len(sess) == 0


def test_region_session_checked_before_preflight_requirement(tmp_path):
    """Без сессии и без preflight — прежний код REGION_SESSION_REQUIRED."""
    path = tmp_path / "j.jsonl"
    res = lg.acquire_and_record("555000111", None, purchase=lambda: 1 / 0,
                                journal_path=path, region_unavailable=True, now=_now)
    assert res.code == lg.REGION_SESSION_REQUIRED and not path.exists()


def test_region_with_preflight_proceeds(tmp_path):
    path = tmp_path / "j.jsonl"
    pf = []
    res, calls, sess = _region(path, "acquired", preflight=lambda: pf.append(1) or True)
    assert res.allowed and res.recorded and res.status == "acquired"
    assert pf == [1] and calls == [1]
    assert res.entry["price_source"] == lg.PRICE_SOURCE_APPLE_FIXED_0
    assert sess.attempted("555000111")


def test_normal_path_without_preflight_unchanged(tmp_path):
    path = tmp_path / "j.jsonl"
    res = lg.acquire_and_record("389801252", 0, purchase=lambda: "acquired",
                                journal_path=path, now=_now)
    assert res.allowed and res.recorded and res.status == "acquired" and res.code is None
    # и с флагом, но известной ценой 0 — обычный путь, preflight не требуется
    res2 = lg.acquire_and_record("389801253", 0, purchase=lambda: "acquired",
                                 journal_path=path, region_unavailable=True,
                                 region_session=lg.RegionAttemptSession(), now=_now)
    assert res2.recorded and "price_source" not in res2.entry


def test_flag_off_unknown_price_without_preflight_is_plain_refusal(tmp_path):
    path = tmp_path / "j.jsonl"
    res = lg.acquire_and_record("555000111", None, purchase=lambda: 1 / 0,
                                journal_path=path, now=_now)
    assert not res.allowed and "цена не подтверждена" in res.reason
    assert res.code != lg.REGION_PREFLIGHT_REQUIRED


@pytest.mark.parametrize("price", [0.99, 149.0])
def test_known_paid_price_with_flag_no_preflight_refused_as_paid(tmp_path, price):
    """Цена известна (max известных > 0): не путь §1.14, отказ «платное»."""
    path = tmp_path / "j.jsonl"
    res = lg.acquire_and_record("555000111", price, purchase=lambda: 1 / 0,
                                journal_path=path, region_unavailable=True,
                                region_session=lg.RegionAttemptSession(), now=_now)
    assert not res.allowed and "платное" in res.reason
