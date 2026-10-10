"""«Поставить» / «Сохранить копии» may take a free license only through the gate."""

from __future__ import annotations

import datetime as dt
import json
from pathlib import Path

import pytest

from apprestore_core.license_guard import read_counts
from apprestore_gui.license_gate import (
    LICENSE_NOTICE,
    LicenseDenied,
    run_with_free_license,
)

MISSING = (
    "could not download App Store ID 1234567890 (store=1234567890 without "
    "--purchase: license is required). Typical causes: ... no purchase/license."
)


class Attempts:
    def __init__(self, *, owned: bool = False, licensed_ok: bool = True) -> None:
        self.owned = owned
        self.licensed_ok = licensed_ok
        self.calls: list[bool] = []

    def __call__(self, with_license: bool) -> str:
        self.calls.append(with_license)
        if with_license:
            if not self.licensed_ok:
                raise RuntimeError("store=1 with --purchase: failed to purchase app")
            return "installed"
        if self.owned:
            return "installed"
        raise RuntimeError(MISSING)


def _offer(price: object) -> dict[str, object]:
    return {"storeId": "1234567890", "bundleId": "com.example.free", "price": price, "country": "ru"}


def _fill(journal: Path, *, today: int = 0, old: int = 0) -> None:
    now = dt.datetime.now(dt.timezone.utc)
    lines = []
    for _ in range(today):
        lines.append({"time": now.isoformat(timespec="seconds"), "track_id": "1"})
    for _ in range(old):
        lines.append({"time": (now - dt.timedelta(days=3)).isoformat(timespec="seconds"), "app_id": "1"})
    journal.write_text("".join(json.dumps(line) + "\n" for line in lines), encoding="utf-8")


def test_owned_app_never_looks_up_or_purchases(tmp_path: Path) -> None:
    attempt = Attempts(owned=True)
    looked: list[str] = []
    result = run_with_free_license(
        "1234567890", attempt, lookup=lambda s: looked.append(s) or _offer(0), journal=tmp_path / "j.jsonl"
    )
    assert result == "installed"
    assert attempt.calls == [False]
    assert looked == []
    assert not (tmp_path / "j.jsonl").exists()


def test_free_app_is_acquired_once_with_notice_and_journal(tmp_path: Path) -> None:
    journal = tmp_path / "j.jsonl"
    attempt = Attempts()
    notes: list[str] = []
    result = run_with_free_license(
        "1234567890", attempt, lookup=lambda _s: _offer(0.0), journal=journal, notify=notes.append
    )
    assert result == "installed"
    assert attempt.calls == [False, True]
    assert notes == [LICENSE_NOTICE]
    entry = json.loads(journal.read_text(encoding="utf-8").strip())
    assert entry["track_id"] == "1234567890"
    assert entry["bundle_id"] == "com.example.free"
    assert entry["storefront"] == "ru"
    assert "@" not in journal.read_text(encoding="utf-8")
    assert read_counts(journal) == (1, 1)


@pytest.mark.parametrize("price", [0.99, 1, "149.0"])
def test_paid_app_is_refused_before_purchase(tmp_path: Path, price: object) -> None:
    attempt = Attempts()
    with pytest.raises(LicenseDenied, match="платное"):
        run_with_free_license("1234567890", attempt, lookup=lambda _s: _offer(price), journal=tmp_path / "j")
    assert attempt.calls == [False]
    assert not (tmp_path / "j").exists()


@pytest.mark.parametrize("offer", [None, _offer(None), _offer("free")])
def test_unknown_price_is_refused(tmp_path: Path, offer: object) -> None:
    attempt = Attempts()
    with pytest.raises(LicenseDenied, match="цену"):
        run_with_free_license("1234567890", attempt, lookup=lambda _s: offer, journal=tmp_path / "j")  # type: ignore[arg-type,return-value]
    assert attempt.calls == [False]


def test_lookup_failure_is_refused(tmp_path: Path) -> None:
    def boom(_s: str) -> dict[str, object]:
        raise OSError("offline")

    attempt = Attempts()
    with pytest.raises(LicenseDenied):
        run_with_free_license("1234567890", attempt, lookup=boom, journal=tmp_path / "j")
    assert attempt.calls == [False]


def test_daily_limit_of_five(tmp_path: Path) -> None:
    journal = tmp_path / "j.jsonl"
    _fill(journal, today=5)
    attempt = Attempts()
    with pytest.raises(LicenseDenied, match="5/5"):
        run_with_free_license("1234567890", attempt, lookup=lambda _s: _offer(0), journal=journal)
    assert attempt.calls == [False]
    assert read_counts(journal) == (5, 5)


def test_four_today_still_allows_the_fifth(tmp_path: Path) -> None:
    journal = tmp_path / "j.jsonl"
    _fill(journal, today=4)
    attempt = Attempts()
    run_with_free_license("1234567890", attempt, lookup=lambda _s: _offer(0), journal=journal)
    assert attempt.calls == [False, True]
    assert read_counts(journal) == (5, 5)


def test_total_limit_of_fifteen_counts_bench_lines_too(tmp_path: Path) -> None:
    journal = tmp_path / "j.jsonl"
    _fill(journal, old=15)  # bench format lines ("app_id") count the same
    attempt = Attempts()
    with pytest.raises(LicenseDenied, match="15/15"):
        run_with_free_license("1234567890", attempt, lookup=lambda _s: _offer(0), journal=journal)
    assert attempt.calls == [False]


def test_acquire_false_never_retries_with_license(tmp_path: Path) -> None:
    attempt = Attempts()
    with pytest.raises(RuntimeError, match="license is required"):
        run_with_free_license(
            "1234567890", attempt, acquire=False, lookup=lambda _s: _offer(0), journal=tmp_path / "j"
        )
    assert attempt.calls == [False]


def test_other_errors_are_not_a_reason_to_purchase(tmp_path: Path) -> None:
    calls: list[bool] = []

    def attempt(with_license: bool) -> str:
        calls.append(with_license)
        raise RuntimeError("store=1 without --purchase: net/http: TLS handshake timeout")

    with pytest.raises(RuntimeError, match="TLS"):
        run_with_free_license("1234567890", attempt, lookup=lambda _s: _offer(0), journal=tmp_path / "j")
    assert calls == [False]


def test_failed_purchase_is_not_journaled(tmp_path: Path) -> None:
    journal = tmp_path / "j.jsonl"
    attempt = Attempts(licensed_ok=False)
    with pytest.raises(RuntimeError, match="failed to purchase"):
        run_with_free_license("1234567890", attempt, lookup=lambda _s: _offer(0), journal=journal)
    assert attempt.calls == [False, True]
    assert not journal.exists()
