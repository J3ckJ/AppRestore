"""Тесты bench_restore: маскирование, агрегация, лимиты лицензий, mock-прогон."""

from __future__ import annotations

import datetime as dt
import importlib.util
import json
from pathlib import Path

import pytest

_SPEC = importlib.util.spec_from_file_location(
    "bench_restore",
    Path(__file__).resolve().parents[1] / "scripts" / "bench_restore.py",
)
import sys
bench = importlib.util.module_from_spec(_SPEC)
assert _SPEC and _SPEC.loader
sys.modules["bench_restore"] = bench
_SPEC.loader.exec_module(bench)

FIXTURE = Path(__file__).resolve().parents[1] / "scripts" / "bench_fixtures" / "mock_transcripts.json"


# -------------------------------------------------------------- маскирование


def test_mask_text_strips_email_token_paths():
    text = ("login user@example.com token=ABCDEFGHIJKLMNOPQRSTUVWXYZ012345 "
            "at /Users/eugene/app and /home/eugene/x")
    out = bench.mask_text(text)
    assert "user@example.com" not in out and "[email]" in out
    assert "ABCDEFGHIJKLMNOPQRSTUVWXYZ012345" not in out and "[token]" in out
    assert "/Users/eugene" not in out and "/Users/[user]" in out
    assert "/home/eugene" not in out and "/home/[user]" in out


def test_mask_text_udid_and_phone():
    out = bench.mask_text("udid 00008110-000A1C2D3E4F001E phone +1 415 555 2671")
    assert "[udid]" in out and "00008110" not in out
    assert "[phone]" in out


def test_mask_obj_redacts_secret_keys_recursively():
    payload = {"email": "a@b.com", "appleId": "a@b.com", "ok": True,
               "nested": {"password": "hunter2", "note": "path /Users/bob/x"},
               "list": [{"token": "zzz"}]}
    out = bench.mask_obj(payload)
    assert out["email"] == "[redacted]"
    assert out["appleId"] == "[redacted]"
    assert out["nested"]["password"] == "[redacted]"
    assert out["list"][0]["token"] == "[redacted]"
    assert "/Users/bob" not in out["nested"]["note"]


def test_mask_obj_keeps_harmless_values():
    out = bench.mask_obj({"bundle_id": "com.x.y", "price": 0, "status": "ok"})
    assert out == {"bundle_id": "com.x.y", "price": 0, "status": "ok"}


def test_report_has_no_secret_after_emit(tmp_path):
    secret = "s3cr3tPassphraseValue"
    report = {"runs": [{"error": f"failed for user@apple.com with {secret}"}]}
    out = tmp_path / "r.json"
    bench._emit(report, str(out), secrets=(secret,))
    written = out.read_text(encoding="utf-8")
    assert secret not in written
    assert "user@apple.com" not in written


# -------------------------------------------------------------- коды ошибок


@pytest.mark.parametrize("text,code", [
    ("license is required", "license_required"),
    ("dial tcp: connection reset by peer", "network"),
    ("context deadline exceeded", "timeout"),
    ("app not found", "app_not_found"),
    ("keychain passphrase is required", "keychain_locked"),
    ("app is incompatible with minimumOSVersion", "incompatible"),
    ("something weird", "unknown"),
])
def test_classify_error(text, code):
    assert bench.classify_error(text) == code


def test_ipatool_error_prefers_json_error_field():
    line = '{"level":"error","error":"license is required","success":false}'
    assert bench.ipatool_error("", line) == "license is required"


# -------------------------------------------------------------- агрегация


def _rec(step_times, dl_fail=False, dl_code="network"):
    steps = {}
    for name, secs in step_times.items():
        steps[name] = {"status": "ok", "seconds": secs}
    if dl_fail:
        steps["download"] = {"status": "fail", "seconds": step_times.get("download", 1.0),
                             "code": dl_code}
    return {"run": 1, "app": "x", "steps": steps}


def test_percentile_interpolates():
    assert bench._percentile([1, 2, 3, 4], 90) == pytest.approx(3.7)
    assert bench._percentile([5], 90) == 5


def test_aggregate_median_p90_and_fail_share():
    records = [
        _rec({"session": 1.0, "download": 2.0}),
        _rec({"session": 2.0, "download": 4.0}),
        _rec({"session": 3.0, "download": 6.0}),
        _rec({"session": 1.0}, dl_fail=True, dl_code="timeout"),
    ]
    agg = bench.aggregate(records)
    assert agg["session"]["median_s"] == pytest.approx(1.5)
    assert agg["download"]["attempted"] == 4
    assert agg["download"]["failures"] == 1
    assert agg["download"]["fail_share"] == pytest.approx(0.25)
    assert agg["download"]["top_codes"][0]["code"] == "timeout"


def test_aggregate_skipped_not_counted():
    records = [{"run": 1, "app": "x", "steps": {
        "license": {"status": "skipped", "seconds": 0.0}}}]
    agg = bench.aggregate(records)
    assert agg["license"]["attempted"] == 0
    assert agg["license"]["fail_share"] is None


# -------------------------------------------------------------- лицензии: лимит


def _journal(tmp_path, entries=(), daily=5, total=15, now=None):
    path = tmp_path / "licenses_acquired.jsonl"
    if entries:
        path.write_text("\n".join(json.dumps(e) for e in entries) + "\n", encoding="utf-8")
    now = now or (lambda: dt.datetime(2026, 10, 10, 12, 0, tzinfo=dt.timezone.utc))
    return bench.LicenseJournal(path, daily_limit=daily, total_limit=total, now=now)


def test_license_daily_limit_refuses(tmp_path):
    base = dt.datetime(2026, 10, 10, 12, 0, tzinfo=dt.timezone.utc)
    entries = [{"time": (base - dt.timedelta(hours=1)).isoformat(), "bundle_id": f"a{i}"}
               for i in range(5)]
    journal = _journal(tmp_path, entries, daily=5, now=lambda: base)
    assert "сутки" in journal.refusal()


def test_license_total_limit_refuses(tmp_path):
    base = dt.datetime(2026, 10, 10, 12, 0, tzinfo=dt.timezone.utc)
    old = base - dt.timedelta(days=10)
    entries = [{"time": old.isoformat(), "bundle_id": f"a{i}"} for i in range(15)]
    journal = _journal(tmp_path, entries, daily=5, total=15, now=lambda: base)
    assert "всего" in journal.refusal()


def test_license_under_limit_ok(tmp_path):
    journal = _journal(tmp_path)
    assert journal.refusal() == ""
    entry = journal.append(bundle_id="com.x", app_id="1", storefront="us", price=0.0, mode="mock")
    assert entry["bundle_id"] == "com.x"
    assert journal.counts() == (1, 1)


# -------------------------------------------------------------- download безопасность


def test_download_command_never_has_purchase():
    app = bench.AppRef("com.x.y", "123")
    cmd = bench.download_command("ipatool", app, Path("/tmp/x.ipa"), version="900")
    assert "--purchase" not in cmd
    assert "--external-version-id" in cmd and "900" in cmd


# -------------------------------------------------------------- mock-прогон


def _run_mock(apps, opts, journal, runs=1):
    backend = bench.MockBackend(json.loads(FIXTURE.read_text(encoding="utf-8")))
    records = []
    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        for app in apps:
            for r in range(1, runs + 1):
                records.append(bench.run_one(backend, bench.AppRef.parse(app), r, opts,
                                             journal, Path(tmp)))
    return backend, records


def test_mock_free_app_download_ok(tmp_path):
    opts = bench.Options()
    _, records = _run_mock(["com.happy.free"], opts, _journal(tmp_path))
    steps = records[0]["steps"]
    assert steps["download"]["status"] == "ok"
    assert steps["download"]["mb_per_s"] > 0
    assert steps["total"]["status"] == "ok"


def test_mock_paid_app_license_denied(tmp_path):
    opts = bench.Options(allow_free_license=True)
    _, records = _run_mock(["com.paid.app"], opts, _journal(tmp_path))
    lic = records[0]["steps"]["license"]
    assert lic["status"] == "denied"
    assert lic["code"] == "paid_app"


def test_mock_free_license_acquired_and_journaled(tmp_path):
    opts = bench.Options(allow_free_license=True)
    journal = _journal(tmp_path)
    backend, records = _run_mock(["com.needs.license"], opts, journal)
    lic = records[0]["steps"]["license"]
    assert lic["status"] == "ok"
    assert records[0]["steps"]["download"]["status"] == "ok"
    assert backend.purchases == ["3003"]
    assert journal.counts() == (1, 1)


def test_mock_license_disabled_without_flag(tmp_path):
    opts = bench.Options(allow_free_license=False)
    _, records = _run_mock(["com.needs.license"], opts, _journal(tmp_path))
    lic = records[0]["steps"]["license"]
    assert lic["status"] == "denied" and lic["code"] == "license_disabled"


def test_mock_network_retry_backoff(tmp_path):
    opts = bench.Options(retries=2, backoff_base=1.0)
    backend, records = _run_mock(["com.flaky.net"], opts, _journal(tmp_path))
    attempts = records[0]["steps"]["download"]["attempts"]
    mechs = [a["mechanism"] for a in attempts]
    assert mechs.count("retry") == 2
    assert records[0]["steps"]["download"]["status"] == "ok"
    assert backend.slept == [1.0, 2.0]


def test_mock_old_version_fallback(tmp_path):
    opts = bench.Options()
    _, records = _run_mock(["com.delisted.old"], opts, _journal(tmp_path))
    attempts = records[0]["steps"]["download"]["attempts"]
    mechs = [a["mechanism"] for a in attempts]
    assert "old-versions" in mechs or "old-version" in mechs
    assert records[0]["steps"]["download"]["status"] == "ok"


def test_mock_gone_everywhere_fails_cleanly(tmp_path):
    opts = bench.Options()
    _, records = _run_mock(["com.gone.everywhere"], opts, _journal(tmp_path))
    assert records[0]["steps"]["download"]["status"] == "fail"
    assert records[0]["steps"]["total"]["status"] == "fail"


def test_mock_no_session_skips_rest(tmp_path):
    opts = bench.Options()
    _, records = _run_mock(["com.no.session"], opts, _journal(tmp_path))
    steps = records[0]["steps"]
    assert steps["session"]["status"] == "fail"
    assert steps["download"]["status"] == "skipped"


def test_mock_fallback_attempts_marked_legal_review(tmp_path):
    opts = bench.Options()
    _, records = _run_mock(["com.flaky.net"], opts, _journal(tmp_path))
    retries = [a for a in records[0]["steps"]["download"]["attempts"] if a["mechanism"] == "retry"]
    assert retries and all(a.get("legal_review") == bench.LEGAL_REVIEW for a in retries)
