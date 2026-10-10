"""Append-only license journal: ``update_status`` adds an ``amends`` line.

Лена's semantics (2026-10-10): ``voids`` cancels an erroneous line; ``amends``
changes the chain's status and the limit counts the chain's last status
(acquired / acquired_download_failed / purchase_uncertain yes, voided no); the
amendment line itself is not counted; legacy lines without ``id`` are voided
only by an explicit ``voids``. The counting half is ``license_guard``'s (Макс);
tests it does not pass yet are strict xfail pointing at the proposal.
"""

from __future__ import annotations

import datetime as dt
import json
import threading
from pathlib import Path

import pytest

from apprestore_core import license_guard, license_journal
from apprestore_core.license_guard import read_counts, record_void

PROPOSAL = "maks-share/license_guard-amends-proposal/"
PENDING = pytest.mark.xfail(
    strict=True,
    reason=f"license_guard fc1a2a71 counts `amends` lines / reads amends as voids; see {PROPOSAL}",
)
FIXED = dt.datetime(2026, 10, 10, 12, 0, 0, tzinfo=dt.timezone.utc)


def _now() -> dt.datetime:
    return FIXED


def _rows(journal: Path) -> list[dict]:
    return [json.loads(line) for line in journal.read_text(encoding="utf-8").splitlines()]


def _append(journal: Path, entry: dict) -> None:
    with journal.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(entry) + "\n")


# ------------------------------------------------------------ append-only writes


def test_update_appends_amends_line_and_keeps_earlier_bytes(tmp_path: Path) -> None:
    journal = tmp_path / "licenses_acquired.jsonl"
    license_journal.record("111", journal_path=journal, price=0.0)
    entry = license_journal.record("222", bundle_id="com.x", storefront="ru", price=0.0, journal_path=journal)
    assert entry["status"] == license_journal.ACQUIRED and entry["id"]
    before = journal.read_bytes()
    line = license_journal.update_status(
        entry["id"], license_journal.ACQUIRED_DOWNLOAD_FAILED, track_id="222", journal_path=journal
    )
    after = journal.read_bytes()
    assert after.startswith(before) and len(after) > len(before)  # only grew
    rows = _rows(journal)
    assert [row["track_id"] for row in rows] == ["111", "222", "222"]
    assert rows[1]["status"] == "acquired"  # the purchase line is untouched
    assert rows[2] == line
    assert set(line) == {"id", "time", "track_id", "status", "amends", "reason"}
    assert line["amends"] == entry["id"] and line["id"] != entry["id"]
    assert line["status"] == "acquired_download_failed" and "voids" not in line
    # a second update appends again, still nothing rewritten
    again = journal.read_bytes()
    license_journal.update_status(entry["id"], "acquired", track_id="222", journal_path=journal)
    assert journal.read_bytes().startswith(again) and len(_rows(journal)) == 4


def test_update_of_unknown_or_missing_id_writes_nothing(tmp_path: Path) -> None:
    journal = tmp_path / "j.jsonl"
    assert license_journal.update_status("nope", "acquired", journal_path=journal) is None
    assert not journal.exists()
    license_journal.record("111", journal_path=journal, price=0.0)
    size = journal.stat().st_size
    assert license_journal.update_status("nope", "acquired", journal_path=journal) is None
    assert license_journal.update_status("", "acquired", journal_path=journal) is None
    assert journal.stat().st_size == size


def test_legacy_line_without_id_is_never_amended(tmp_path: Path) -> None:
    journal = tmp_path / "j.jsonl"
    _append(journal, {"time": "2026-10-10T15:16:42+00:00", "track_id": "492224193", "status": "purchase_uncertain"})
    size = journal.stat().st_size
    assert license_journal.update_status("", "acquired_download_failed", track_id="492224193", journal_path=journal) is None
    assert journal.stat().st_size == size


def test_update_holds_the_journal_lock(tmp_path: Path) -> None:
    journal = tmp_path / "j.jsonl"
    entry = license_journal.record("111", journal_path=journal, price=0.0)
    done = threading.Event()

    def worker() -> None:
        license_journal.update_status(entry["id"], "acquired_download_failed", journal_path=journal)
        done.set()

    with license_guard.journal_lock(journal):
        thread = threading.Thread(target=worker, daemon=True)
        thread.start()
        assert not done.wait(0.3)
    assert done.wait(5)


# ------------------------------------------------------------ counting (license_guard)


@PENDING
def test_chain_acquired_then_download_failed_counts_once(tmp_path: Path) -> None:
    journal = tmp_path / "j.jsonl"
    entry = license_guard.record_acquire("389801252", status="acquired", journal_path=journal, now=_now)
    license_journal.update_status(entry["id"], "acquired_download_failed", track_id="389801252", journal_path=journal)
    assert read_counts(journal, now=_now) == (1, 1)


def test_chain_purchase_uncertain_then_voids_counts_zero(tmp_path: Path) -> None:
    journal = tmp_path / "j.jsonl"
    entry = license_guard.record_acquire("492224193", status="purchase_uncertain", journal_path=journal, now=_now)
    record_void(entry["id"], "2040 refusal", track_id="492224193", journal_path=journal, now=_now)
    assert read_counts(journal, now=_now) == (0, 0)


@PENDING
def test_chain_amended_then_voided_counts_zero(tmp_path: Path) -> None:
    journal = tmp_path / "j.jsonl"
    entry = license_guard.record_acquire("389801252", status="acquired", journal_path=journal, now=_now)
    license_journal.update_status(entry["id"], "acquired_download_failed", journal_path=journal)
    record_void(entry["id"], "mistake", track_id="389801252", journal_path=journal, now=_now)
    assert read_counts(journal, now=_now) == (0, 0)


def test_void_with_unknown_id_is_ignored(tmp_path: Path) -> None:
    journal = tmp_path / "j.jsonl"
    license_guard.record_acquire("389801252", status="acquired", journal_path=journal, now=_now)
    record_void("no-such-id", "typo", track_id="389801252", journal_path=journal, now=_now)
    assert read_counts(journal, now=_now) == (1, 1)


@PENDING
def test_amends_with_unknown_id_is_ignored_and_not_counted(tmp_path: Path) -> None:
    journal = tmp_path / "j.jsonl"
    license_guard.record_acquire("389801252", status="acquired", journal_path=journal, now=_now)
    _append(journal, {"id": "x", "time": "2026-10-10T12:00:05+00:00", "track_id": "389801252",
                      "status": "acquired_download_failed", "amends": "no-such-id", "reason": ""})
    assert read_counts(journal, now=_now) == (1, 1)


def test_two_attempts_same_second_only_one_voided(tmp_path: Path) -> None:
    journal = tmp_path / "j.jsonl"
    a = license_guard.record_acquire("492224193", status="purchase_uncertain", journal_path=journal, now=_now)
    b = license_guard.record_acquire("492224193", status="purchase_uncertain", journal_path=journal, now=_now)
    assert (a["time"], a["track_id"]) == (b["time"], b["track_id"]) and a["id"] != b["id"]
    record_void(a["id"], "only A", track_id="492224193", journal_path=journal, now=_now)
    assert read_counts(journal, now=_now) == (1, 1)


@PENDING
def test_two_attempts_same_second_amend_one_void_other(tmp_path: Path) -> None:
    journal = tmp_path / "j.jsonl"
    a = license_guard.record_acquire("492224193", status="acquired", journal_path=journal, now=_now)
    b = license_guard.record_acquire("492224193", status="purchase_uncertain", journal_path=journal, now=_now)
    license_journal.update_status(a["id"], "acquired_download_failed", track_id="492224193", journal_path=journal)
    record_void(b["id"], "only B", track_id="492224193", journal_path=journal, now=_now)
    assert read_counts(journal, now=_now) == (1, 1)


def test_legacy_attempt_a_voided_by_pair_counts_zero(tmp_path: Path) -> None:
    journal = tmp_path / "j.jsonl"
    _append(journal, {"time": "2026-10-10T15:16:42+00:00", "track_id": "492224193", "app_id": "492224193",
                      "bundle_id": "", "storefront": "us", "status": "purchase_uncertain", "price": 0.0,
                      "mode": "real"})
    assert read_counts(journal) == (1, 1)
    record_void(None, "erroneous: 2040", legacy_match=("2026-10-10T15:16:42+00:00", "492224193"),
                journal_path=journal)
    assert read_counts(journal) == (0, 0)
    assert len(_rows(journal)) == 2


@PENDING
def test_legacy_line_is_not_voided_by_amends(tmp_path: Path) -> None:
    journal = tmp_path / "j.jsonl"
    _append(journal, {"time": "2026-10-10T15:16:42+00:00", "track_id": "492224193", "status": "purchase_uncertain"})
    _append(journal, {"id": "x", "time": "2026-10-10T15:20:00+00:00", "track_id": "492224193", "status": "voided",
                      "amends": {"time": "2026-10-10T15:16:42+00:00", "track_id": "492224193"}})
    assert read_counts(journal) == (1, 1)
