from __future__ import annotations

import json
from pathlib import Path

from apprestore_core import license_journal
from apprestore_core.license_guard import read_counts


def test_record_then_update_keeps_one_line(tmp_path: Path) -> None:
    journal = tmp_path / "licenses_acquired.jsonl"
    other = license_journal.record("111", journal_path=journal, price=0.0)
    entry = license_journal.record("222", bundle_id="com.x", storefront="ru", price=0.0, journal_path=journal)
    assert entry["status"] == license_journal.ACQUIRED
    assert license_journal.update_status(entry, license_journal.ACQUIRED_DOWNLOAD_FAILED, journal_path=journal)
    rows = [json.loads(line) for line in journal.read_text(encoding="utf-8").splitlines()]
    assert [row["track_id"] for row in rows] == ["111", "222"]
    assert rows[0]["status"] == "acquired" and rows[0]["time"] == other["time"]
    assert rows[1]["status"] == "acquired_download_failed"
    assert set(rows[1]) <= {"id", "time", "track_id", "app_id", "bundle_id", "storefront", "price", "mode", "status"}
    assert read_counts(journal)[1] == 2


def test_update_of_unknown_entry_is_false(tmp_path: Path) -> None:
    journal = tmp_path / "j.jsonl"
    assert not license_journal.update_status({"time": "x", "track_id": "1"}, "acquired", journal_path=journal)
