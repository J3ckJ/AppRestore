from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from apprestore_core import delisted_attempt, license_gate
from apprestore_core.region_probe import RegionResult, RegionStatus
from apprestore_core.restore_diagnostics import parse_targets, run_report
from apprestore_core.tools import ToolUnavailable


class Tools:
    def __init__(self):
        self.probes = 0
        self.purchases = 0
        self.country = "ru"
        self.email = "private@example.invalid"
        self.infos = 0
        self.change_at = 0
        self.refuse = False

    def ipatool_auth_info(self):
        self.infos += 1
        return {"email": "other@example.invalid" if self.infos == self.change_at else self.email}

    def account_country(self):
        return self.country

    def license_preflight(self):
        return None

    def probe_download(self, sid, *, version_id=""):
        self.probes += 1
        offered = self.purchases > 0
        return {"appId": sid, "country": "RU", "channels": [{
            "channel": "volume_unpinned", "status": "package_and_license_offered" if offered else "license_required",
            "identityMatches": offered, "packageOffered": offered, "licenseMaterial": offered,
        }]}

    def purchase_license(self, sid, *, grant):
        from apprestore_core.purchase_grant import require_grant
        require_grant(grant, sid)
        self.purchases += 1
        self.last_purchase_diagnostic = {"appId": sid, "attempts": [{"pricingParameters": "STDQ", "outcome": "refused" if self.refuse else "granted", "httpStatus": 200}]}
        if self.refuse:
            raise ToolUnavailable("failed to purchase item: item is temporarily unavailable")
        return {"success": True}


@pytest.fixture(autouse=True)
def isolate(monkeypatch):
    delisted_attempt.forget_all()
    monkeypatch.setattr(license_gate, "lookup_offer", lambda *_: None)
    yield
    delisted_attempt.forget_all()


def run(tmp_path, tools, **kwargs):
    regions = SimpleNamespace(classify_detailed=lambda *_: {1117817509: RegionResult(RegionStatus.DELISTED)})
    return run_report(tools, [("1117817509", "")], tmp_path / "report.json", journal=tmp_path / "licenses.jsonl",
                      region_probe=regions, **kwargs)


def test_default_probe_never_purchases_or_writes_license_journal(tmp_path):
    tools = Tools()
    report = run(tmp_path, tools)
    assert report["readOnly"] and report["state"] == "complete"
    assert tools.probes == 1 and tools.purchases == 0
    assert not (tmp_path / "licenses.jsonl").exists()
    assert report["results"][0]["outcome"] == "license_required"
    assert "private" not in (tmp_path / "report.json").read_text()


def test_explicit_acquisition_uses_gate_and_records_before_after(tmp_path):
    tools = Tools()
    report = run(tmp_path, tools, acquire_license=True, expected_country="RU")
    row = report["results"][0]
    assert tools.purchases == 1 and tools.probes == 2
    assert len(row["probes"]) == 2 and row["outcome"] == "package_and_license_offered"
    assert row["purchase"]["attempts"][0]["outcome"] == "granted"
    entry = json.loads((tmp_path / "licenses.jsonl").read_text().splitlines()[0])
    assert entry["price_source"] == "apple_fixed_0"
    assert entry["status"] == "acquired"
    assert not report["readOnly"]


def test_actual_country_mismatch_stops_before_any_store_request(tmp_path):
    tools = Tools()
    report = run(tmp_path, tools, acquire_license=True, expected_country="US")
    assert report["state"] == "country_mismatch"
    assert tools.probes == tools.purchases == 0


def test_account_change_before_purchase_stops_transaction(tmp_path):
    tools = Tools()
    tools.change_at = 3  # initial auth info, initial probe, purchase notification
    report = run(tmp_path, tools, acquire_license=True)
    assert tools.purchases == 0 and tools.probes == 1
    assert report["state"] == "account_unavailable"
    assert not (tmp_path / "licenses.jsonl").exists()


def test_session_read_failure_stops_without_leaking_the_error(tmp_path):
    tools = Tools()
    auth_info = tools.ipatool_auth_info

    def unavailable():
        if tools.infos:
            raise ToolUnavailable("private@example.invalid session failure")
        return auth_info()

    tools.ipatool_auth_info = unavailable
    report = run(tmp_path, tools, acquire_license=True)
    assert report["state"] == "account_unavailable"
    assert tools.purchases == tools.probes == 0
    assert "private" not in (tmp_path / "report.json").read_text()


def test_explicit_apple_refusal_is_reported_without_retry(tmp_path):
    tools = Tools()
    tools.refuse = True
    report = run(tmp_path, tools, acquire_license=True)
    assert tools.probes == tools.purchases == 1
    assert report["results"][0]["purchase"]["attempts"][0]["outcome"] == "refused"
    assert not (tmp_path / "licenses.jsonl").exists()


def test_known_paid_offer_cannot_use_delisted_exception(tmp_path):
    tools = Tools()
    delisted_attempt.record_prices({"1117817509": 9.99})
    run(tmp_path, tools, acquire_license=True)
    assert tools.purchases == 0


def test_uncertain_purchase_checks_access_without_repeating_transaction(tmp_path):
    tools = Tools()
    purchase = tools.purchase_license

    def uncertain(sid, *, grant):
        purchase(sid, grant=grant)
        tools.last_purchase_diagnostic["attempts"][0]["outcome"] = "uncertain"
        raise ToolUnavailable("purchase response unconfirmed")

    tools.purchase_license = uncertain
    report = run(tmp_path, tools, acquire_license=True)
    assert tools.purchases == 1 and tools.probes == 2
    assert report["results"][0]["outcome"] == "package_and_license_offered"
    entry = json.loads((tmp_path / "licenses.jsonl").read_text().splitlines()[0])
    assert entry["status"] == "purchase_uncertain"


@pytest.mark.parametrize("values", [[], ["0"], ["42:bad"], ["42:1", "42:2"], [str(i) for i in range(1, 10)]])
def test_bounded_explicit_targets(values):
    with pytest.raises(ValueError):
        parse_targets(values)
