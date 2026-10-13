from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from apprestore_core.license_gate import purchase_outcome, run_with_free_license
from apprestore_core.models import CommandResult
from apprestore_core.purchase_diagnostics import purchase_diagnostics
from apprestore_core.tools import AppRestoreTools, ToolUnavailable


def line(outcome="refused", **extra):
    return json.dumps({"appId": 42, "purchaseAttempt": {
        "pricingParameters": "STDQ", "httpStatus": 200,
        "outcome": outcome, **extra,
    }})


def test_purchase_report_distinguishes_grant_owned_refusal_and_uncertainty():
    for outcome in ("granted", "already_owned", "refused", "uncertain"):
        report = purchase_diagnostics(line(outcome), "42")
        assert report["attempts"][0]["outcome"] == outcome


def test_purchase_report_projects_only_safe_matching_fields():
    output = "\n".join([
        'not json: private',
        line(failureType="2059", customerMessage="secret", token="private"),
        line("granted", pricingParameters="GAME"),
        line().replace('"appId": 42', '"appId": 43'),
        line(outcome={"private": "secret"}, failureType="private@example.com"),
        json.dumps({"success": True, "email": "private@example.com"}),
    ])
    report = purchase_diagnostics(output, "42")
    assert len(report["attempts"]) == 3
    assert report["attempts"][0]["failureType"] == "2059"
    assert report["attempts"][1]["pricingParameters"] == "GAME"
    assert report["attempts"][2]["outcome"] == "uncertain"
    assert "private" not in json.dumps(report) and "secret" not in json.dumps(report)


@pytest.mark.parametrize("extra", [{"httpStatus": 500}, {"httpStatus": True}, {"failureType": "2059"}])
def test_malformed_grant_is_not_success(extra):
    assert purchase_diagnostics(line("granted", **extra), "42")["attempts"][0]["outcome"] == "uncertain"


def test_tools_keep_safe_evidence_even_when_purchase_fails(monkeypatch):
    from apprestore_core.purchase_grant import _mint
    monkeypatch.setattr("apprestore_core.tools.resolve_tool", lambda _: "ipatool")
    output = line(failureType="2059") + '\n{"error":"item is temporarily unavailable","success":false}\n'
    result = CommandResult(("ipatool",), 1, output, "")
    tools = AppRestoreTools(runner=SimpleNamespace(run=lambda *a, **k: result))
    with pytest.raises(ToolUnavailable):
        tools.purchase_license("42", grant=_mint("42"))
    assert tools.last_purchase_diagnostic["attempts"][0]["failureType"] == "2059"


def test_ambiguous_purchase_is_journaled_as_uncertain_without_retry(tmp_path):
    from tests.test_license_gate import Tools, Download, STORE
    from apprestore_core.license_guard import read_counts
    message = "failed to purchase item with param 'STDQ': purchase response unconfirmed"
    tools = Tools(error=ToolUnavailable(message))
    download = Download(tools)
    journal = tmp_path / "licenses.jsonl"
    assert purchase_outcome(tools.error) == "uncertain"
    with pytest.raises(ToolUnavailable):
        run_with_free_license(STORE, download, tools=tools, journal=journal,
                              lookup=lambda *_: {"price": 0})
    rows = [json.loads(row) for row in journal.read_text().splitlines()]
    assert rows[0]["status"] == "purchase_uncertain"
    assert tools.purchases == [STORE]
    assert download.calls == 1
    assert read_counts(journal_path=journal)[1] == 1


def test_uncertain_purchase_is_not_presented_as_an_apple_refusal():
    from apprestore_core.ipatool_api import ErrorCode, classify_error
    from apprestore_gui.errors import explain_store_operation_error, explain_user_error
    message = "failed to purchase item: purchase response unconfirmed"
    assert classify_error(message) is ErrorCode.PURCHASE_UNCONFIRMED
    for text in (explain_user_error(message),
                 explain_store_operation_error(message, phase="license_request", country="RU")):
        assert "не подтвердила" in text
        assert "не выдала" not in text
