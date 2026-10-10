"""Atomic limit → purchase → journal step (license_journal.acquire_and_record):
a hung purchase is killed, journaled as purchase_uncertain and frees the lock;
an explicit refusal is never journaled; N processes at limit 5 → exactly 5."""

from __future__ import annotations

import json
import multiprocessing
import os
import threading
import time
from pathlib import Path

import pytest

from apprestore_core import license_guard, license_journal
from apprestore_core import tools as tools_module
from apprestore_core.command import CommandError
from apprestore_core.license_gate import run_with_free_license
from apprestore_core.license_journal import PurchaseRefused, acquire_and_record
from apprestore_core.tools import AppRestoreTools, ToolUnavailable

STORE = "1234567890"
MISSING = f"store={STORE} without --purchase: license is required"


def _entries(journal: Path) -> list[dict]:
    if not journal.exists():
        return []
    return [json.loads(line) for line in journal.read_text(encoding="utf-8").splitlines() if line.strip()]


def _lookup(store_id, countries):
    return {"storeId": store_id, "bundleId": "com.example.free", "price": 0.0, "country": countries[0]}


class _QuickTools:
    def __init__(self) -> None:
        self.licensed = False

    def account_country(self) -> str:
        return "us"

    def purchase_license(self, store_id: str, *, grant: object) -> dict:
        from apprestore_core.purchase_grant import require_grant

        require_grant(grant, store_id)
        self.licensed = True
        return {"success": True}


def _attempt_for(tools) -> callable:
    def attempt() -> str:
        if getattr(tools, "licensed", False):
            return "installed"
        raise RuntimeError(MISSING)

    return attempt


def test_purchase_timeout_is_ninety_seconds() -> None:
    assert tools_module.PURCHASE_TIMEOUT_SECONDS == 90.0


@pytest.mark.skipif(os.name == "nt", reason="POSIX shell script stands in for ipatool")
def test_hung_purchase_is_killed_journaled_uncertain_and_frees_the_lock(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pid_file = tmp_path / "pid"
    script = tmp_path / "ipatool"
    script.write_text(f'#!/bin/sh\necho $$ > "{pid_file}"\nexec sleep 60\n', encoding="utf-8")
    script.chmod(0o755)
    monkeypatch.setattr(tools_module, "PURCHASE_TIMEOUT_SECONDS", 1.0)
    monkeypatch.setattr(tools_module, "resolve_tool", lambda name: str(script))
    tools = AppRestoreTools()
    monkeypatch.setattr(tools, "_ipatool_env", lambda: {})
    monkeypatch.setattr(tools, "account_country", lambda: "us")
    journal = tmp_path / "licenses_acquired.jsonl"

    started = time.monotonic()
    with pytest.raises(CommandError, match="timed out"):
        run_with_free_license(STORE, _attempt_for(tools), tools=tools, lookup=_lookup, journal=journal)
    assert time.monotonic() - started < 20
    [entry] = _entries(journal)
    assert entry["status"] == "purchase_uncertain" and entry["track_id"] == STORE
    pid = int(pid_file.read_text().strip())
    with pytest.raises(ProcessLookupError):
        os.kill(pid, 0)  # ipatool was killed, not left hanging

    # The lock is free: another acquisition on the same journal finishes at once.
    quick = _QuickTools()
    done: list[str] = []
    worker = threading.Thread(
        target=lambda: done.append(
            run_with_free_license("222", _attempt_for(quick), tools=quick, lookup=_lookup, journal=journal)
        ),
        daemon=True,
    )
    worker.start()
    worker.join(10)
    assert done == ["installed"]
    assert [e["status"] for e in _entries(journal)] == ["purchase_uncertain", "acquired"]


@pytest.mark.parametrize("error", [ToolUnavailable("net/http: TLS handshake timeout"), KeyboardInterrupt()])
def test_any_other_exception_is_uncertain(tmp_path: Path, error: BaseException) -> None:
    journal = tmp_path / "j.jsonl"

    def purchase() -> None:
        raise error

    with pytest.raises(type(error)):
        acquire_and_record(STORE, 0.0, purchase, journal_path=journal, mode="gui")
    assert [e["status"] for e in _entries(journal)] == ["purchase_uncertain"]


@pytest.mark.parametrize(
    "message",
    ["paid app: price is not zero", "the app is not available in this storefront", "not logged in"],
)
def test_explicit_refusal_signal_is_not_journaled(tmp_path: Path, message: str) -> None:
    journal = tmp_path / "j.jsonl"

    def purchase() -> None:
        raise PurchaseRefused(message)

    with pytest.raises(PurchaseRefused):
        acquire_and_record(STORE, 0.0, purchase, journal_path=journal, mode="gui")
    assert _entries(journal) == []


def test_gate_turns_apple_refusal_into_the_signal_and_reraises_the_original(tmp_path: Path) -> None:
    class Refusing(_QuickTools):
        def purchase_license(self, store_id: str, *, grant: object) -> dict:
            raise ToolUnavailable("failed to purchase app: paid app")

    tools, journal = Refusing(), tmp_path / "j.jsonl"
    with pytest.raises(ToolUnavailable, match="paid app"):
        run_with_free_license(STORE, _attempt_for(tools), tools=tools, lookup=_lookup, journal=journal)
    assert _entries(journal) == []


def test_limit_refusal_never_calls_purchase(tmp_path: Path) -> None:
    journal = tmp_path / "j.jsonl"
    for index in range(5):
        license_guard.record_acquire(str(100 + index), journal_path=journal)
    called: list[int] = []
    verdict, entry = acquire_and_record(STORE, 0.0, lambda: called.append(1), journal_path=journal)
    assert entry is None and not verdict.allowed and called == []


# ---------------------------------------------------------------- N processes, limit 5


def _worker(journal: str, purchases: str, store_id: str, barrier) -> None:  # noqa: ANN001
    class Tools(_QuickTools):
        def purchase_license(self, sid: str, *, grant: object) -> dict:
            super().purchase_license(sid, grant=grant)
            time.sleep(0.05)  # widen the race window
            with open(purchases, "a", encoding="utf-8") as stream:
                stream.write(sid + "\n")
            return {"success": True}

    tools = Tools()
    barrier.wait(30)
    try:
        run_with_free_license(store_id, _attempt_for(tools), tools=tools, lookup=_lookup, journal=Path(journal))
    except Exception:  # noqa: BLE001 - LicenseDenied over the limit is expected
        pass


def test_parallel_processes_never_pass_the_daily_limit(tmp_path: Path) -> None:
    ctx = multiprocessing.get_context("spawn")
    journal, purchases, n = tmp_path / "licenses_acquired.jsonl", tmp_path / "purchases.log", 12
    barrier = ctx.Barrier(n)
    procs = [
        ctx.Process(target=_worker, args=(str(journal), str(purchases), str(1000 + i), barrier))
        for i in range(n)
    ]
    for proc in procs:
        proc.start()
    for proc in procs:
        proc.join(60)
        assert proc.exitcode == 0
    assert len(purchases.read_text(encoding="utf-8").splitlines()) == 5
    assert [e["status"] for e in _entries(journal)] == ["acquired"] * 5
    assert license_journal.read_counts(journal) == (5, 5)


def test_update_status_waits_for_the_same_journal_lock(tmp_path: Path) -> None:
    journal = tmp_path / "j.jsonl"
    entry = license_guard.record_acquire(STORE, journal_path=journal, status="acquired")
    finished = threading.Event()

    def update() -> None:
        license_journal.update_status(
            entry["id"], "acquired_download_failed", track_id=STORE, journal_path=journal
        )
        finished.set()

    with license_guard.journal_lock(journal):
        worker = threading.Thread(target=update, daemon=True)
        worker.start()
        assert not finished.wait(0.3)  # blocked on <journal>.lock
    assert finished.wait(5)
    assert [e["status"] for e in _entries(journal)] == ["acquired", "acquired_download_failed"]
