from __future__ import annotations

import json
from threading import Lock
from types import SimpleNamespace

import pytest

from apprestore_core.download_probe import positive_id, sanitize_download_probe
from apprestore_core.models import CommandResult
from apprestore_core.tools import AppRestoreTools, ToolUnavailable


def probe_payload():
    return {"appId": 1117817509, "country": "RU", "channels": [{
        "channel": "redownload_unpinned", "status": "package_and_license_offered",
        "httpStatus": 200, "versionId": "123", "identityMatches": True,
        "packageOffered": True, "licenseMaterial": True,
    }]}


def test_safe_probe_projects_only_allowlisted_fields():
    payload = probe_payload()
    payload.update(email="private@example.com", token="secret", url="https://private.invalid")
    payload["channels"][0].update(cookie="secret", sinf="private", guid="secret", customerMessage="email")
    clean = sanitize_download_probe(payload, "1117817509")
    assert clean["channels"][0]["status"] == "package_and_license_offered"
    assert not any(private in json.dumps(clean) for private in ("private", "secret", "customerMessage", "cookie", "sinf", "guid"))


@pytest.mark.parametrize("value", [None, True, "", "0", "-1", "1;echo secret", "123/4", "1234567890123456789"])
def test_positive_ids_reject_invalid_input(value):
    with pytest.raises(ValueError):
        positive_id(value)


def test_probe_does_not_accept_another_app_or_unverified_offer():
    with pytest.raises(ValueError):
        sanitize_download_probe(probe_payload(), "1406492297")
    payload = probe_payload()
    payload["channels"][0]["identityMatches"] = False
    clean = sanitize_download_probe(payload, "1117817509")
    assert clean["channels"][0]["status"] == "invalid_response"
    assert not clean["channels"][0]["packageOffered"] and not clean["channels"][0]["licenseMaterial"]


@pytest.mark.parametrize("changes", [
    {"httpStatus": "secret", "failureType": "token=secret", "versionId": "private"},
    {"httpStatus": True, "failureType": "2059; email", "versionId": None},
])
def test_untrusted_diagnostic_scalars_do_not_survive(changes):
    payload = probe_payload()
    payload["channels"][0].update(changes)
    row = sanitize_download_probe(payload, "1117817509")["channels"][0]
    assert "httpStatus" not in row and "failureType" not in row and "versionId" not in row


@pytest.mark.parametrize("value", [[], {}, None, True])
def test_malformed_channel_names_are_rejected(value):
    payload = probe_payload()
    payload["channels"][0]["channel"] = value
    with pytest.raises(ValueError):
        sanitize_download_probe(payload, "1117817509")


@pytest.mark.parametrize("value", [[], {}, None, True])
def test_malformed_status_is_not_copied(value):
    payload = probe_payload()
    payload["channels"][0]["status"] = value
    clean = sanitize_download_probe(payload, "1117817509")
    assert clean["channels"][0]["status"] == "invalid_response"


def test_tools_probe_is_readonly_and_never_echoes_raw_errors(monkeypatch):
    from apprestore_core import tools as tm

    monkeypatch.setattr(tm, "resolve_tool", lambda name: "ipatool.exe")
    calls = []

    def run(args, **kwargs):
        calls.append((args, kwargs))
        return CommandResult(tuple(args), 0, json.dumps({"success": True, "probe": probe_payload()}), "")

    tools = AppRestoreTools(runner=SimpleNamespace(run=run))
    tools._ipatool_env = lambda: {}
    result = tools.probe_download("1117817509", version_id="123")
    assert result["country"] == "RU"
    args, kwargs = calls[0]
    assert "probe-download" in args and "--non-interactive" in args
    assert "--purchase" not in args and "purchase" not in args and "download" not in args
    assert args[args.index("--external-version-id") + 1] == "123"
    assert kwargs["timeout"] == 90
    tools.runner.run = lambda args, **kw: CommandResult(tuple(args), 1, "email=private@example.com token=secret", "secret")
    with pytest.raises(ToolUnavailable) as caught:
        tools.probe_download("1117817509")
    assert "private" not in str(caught.value) and "secret" not in str(caught.value)


def test_auth_info_refreshes_country_after_gui_account_switch(monkeypatch):
    from apprestore_core import tools as tm

    monkeypatch.setattr(tm, "resolve_tool", lambda name: "ipatool.exe")
    responses = iter([
        {"success": True, "email": "one@example.com", "countryCode": "RU"},
        {"success": True, "email": "two@example.com", "storeFront": "143441-1"},
        {"success": True, "email": "three@example.com"},
    ])
    runner = SimpleNamespace(run=lambda args, **kw: CommandResult(tuple(args), 0, json.dumps(next(responses)), ""))
    tools = AppRestoreTools(runner=runner)
    tools._ipatool_env = lambda: {}
    assert tools.ipatool_auth_info() and tools._account_country == "ru"
    assert tools.ipatool_auth_info() and tools._account_country == "us"
    assert tools.ipatool_auth_info() and tools._account_country is None


def test_diagnostic_targets_and_atomic_report(tmp_path):
    pytest.importorskip("PySide6")
    from apprestore_gui.download_diagnostics import parse_targets, write_report

    assert parse_targets(["1117817509", "610003290:838594835", "1117817509"]) == [
        ("1117817509", ""), ("610003290", "838594835"),
    ]
    for values in ([], ["1:2:3"], ["0"], ["1:0"], [str(i) for i in range(1, 10)]):
        with pytest.raises(ValueError):
            parse_targets(values)
    target = tmp_path / "diagnostics" / "report.json"
    write_report(target, {"readOnly": True, "results": [probe_payload()]})
    assert json.loads(target.read_text(encoding="utf-8"))["readOnly"] is True
    assert not list(target.parent.glob(".download-probe-*"))


def test_diagnostic_worker_aborts_account_switch_and_never_calls_purchase(tmp_path):
    pytest.importorskip("PySide6")
    from apprestore_gui.download_diagnostics import DownloadDiagnostics

    calls = []
    session = SimpleNamespace(
        _tool_lock=Lock(), signedIn=True, accountEmail="person@example.com", authPhase="in",
        service=SimpleNamespace(keychain_ready=lambda: True),
    )

    def probe(sid, **kw):
        calls.append(sid)
        session.accountEmail = "other@example.com"
        return probe_payload()

    session.service.core = SimpleNamespace(tools=SimpleNamespace(probe_download=probe))
    # Exercise the worker without starting a real Qt timer or account.
    fake = SimpleNamespace(session=session, targets=[("1117817509", ""), ("1406492297", "")],
                           report={"results": []}, _write=lambda: None)
    DownloadDiagnostics._run(fake, "person@example.com")
    assert calls == ["1117817509"] and fake.report == {"results": [], "state": "account_changed_or_locked"}


def test_locked_diagnostics_do_not_start_or_request_credentials():
    pytest.importorskip("PySide6")
    from apprestore_gui.download_diagnostics import DownloadDiagnostics

    fake = SimpleNamespace(started=False, session=SimpleNamespace(signedIn=False))
    DownloadDiagnostics._consider(fake)
    assert fake.started is False


@pytest.mark.parametrize("busy", ["restoring", "files", "purchases", "checking"])
def test_diagnostics_wait_for_other_work(busy):
    pytest.importorskip("PySide6")
    from apprestore_gui.download_diagnostics import DownloadDiagnostics

    session = SimpleNamespace(
        signedIn=True, accountEmail="person@example.com", authPhase="in", _lock=Lock(),
        _busy=busy == "restoring", _files_busy=busy == "files", purchasesBusy=busy == "purchases",
        sessionState="checking" if busy == "checking" else "ready",
        service=SimpleNamespace(keychain_ready=lambda: True),
    )
    fake = SimpleNamespace(started=False, session=session)
    DownloadDiagnostics._consider(fake)
    assert fake.started is False


def test_ready_diagnostics_start_once_without_auth_or_purchase(monkeypatch):
    pytest.importorskip("PySide6")
    from apprestore_gui import download_diagnostics as module

    calls = []
    monkeypatch.setattr(module.threading, "Thread", lambda **kw: SimpleNamespace(start=lambda: calls.append(kw)))
    session = SimpleNamespace(
        signedIn=True, accountEmail="person@example.com", authPhase="in", _lock=Lock(),
        _busy=False, _files_busy=False, purchasesBusy=False, sessionState="ready",
        service=SimpleNamespace(keychain_ready=lambda: True),
    )
    fake = SimpleNamespace(started=False, session=session, timer=SimpleNamespace(stop=lambda: None), _run=lambda: None)
    module.DownloadDiagnostics._consider(fake)
    module.DownloadDiagnostics._consider(fake)
    assert fake.started and len(calls) == 1
    assert calls[0]["args"] == ("person@example.com",)


def test_diagnostics_finish_and_mask_exceptions():
    pytest.importorskip("PySide6")
    from apprestore_gui.download_diagnostics import DownloadDiagnostics

    def probe(sid, **kw):
        if sid == "1406492297":
            raise RuntimeError("token=secret private@example.com")
        return sanitize_download_probe(probe_payload(), sid)

    session = SimpleNamespace(
        _tool_lock=Lock(), signedIn=True, accountEmail="person@example.com", authPhase="in",
        service=SimpleNamespace(keychain_ready=lambda: True, core=SimpleNamespace(tools=SimpleNamespace(probe_download=probe))),
    )
    fake = SimpleNamespace(session=session, targets=[("1117817509", ""), ("1406492297", "")],
                           report={"results": []}, _write=lambda: None)
    DownloadDiagnostics._run(fake, "person@example.com")
    assert fake.report["state"] == "complete"
    assert fake.report["results"][1] == {"appId": "1406492297", "status": "probe_failed"}
    assert "secret" not in json.dumps(fake.report) and "@" not in json.dumps(fake.report)


def test_diagnostic_country_and_owned_control_are_only_local_selection():
    pytest.importorskip("PySide6")
    from apprestore_gui.download_diagnostics import diagnostic_country, purchased_control

    assert diagnostic_country("us") == "US" and diagnostic_country("") == ""
    for value in ("USA", "US;echo secret", "12"):
        with pytest.raises(ValueError):
            diagnostic_country(value)
    rows = [{"trackId": "1117817509"}, {"trackId": None}, {"trackId": "123", "email": "private"}]
    assert purchased_control(rows, [("1117817509", "")]) == "123"
    assert purchased_control([], [("1117817509", "")]) == ""


def test_wrong_country_diagnostics_wait_without_probing_or_changing_store():
    pytest.importorskip("PySide6")
    from apprestore_gui.download_diagnostics import DownloadDiagnostics

    session = SimpleNamespace(
        _tool_lock=Lock(), accountEmail="person@example.com",
        service=SimpleNamespace(keychain_ready=lambda: True, core=SimpleNamespace(tools=SimpleNamespace(account_country=lambda: "RU"))),
    )
    fake = SimpleNamespace(session=session, expected_country="US", targets=[("1117817509", "")],
                           started=True, report={"results": []}, _write=lambda: None)
    DownloadDiagnostics._run(fake, "person@example.com")
    assert fake.report == {"results": [], "state": "waiting_for_country", "requiredCountry": "US"}
    assert fake.started is False and fake.retry_at > 0


def test_diagnostics_compare_requested_apps_with_a_purchased_control():
    pytest.importorskip("PySide6")
    from apprestore_gui.download_diagnostics import DownloadDiagnostics

    calls = []
    session = SimpleNamespace(
        _tool_lock=Lock(), signedIn=True, accountEmail="person@example.com", authPhase="in",
        _purchases=SimpleNamespace(account="current-account-key", _cache=SimpleNamespace(account_key=lambda account: "current-account-key"),
                                   view=SimpleNamespace(rows=[{"trackId": "123", "name": "private"}])),
        service=SimpleNamespace(keychain_ready=lambda: True, core=SimpleNamespace(tools=SimpleNamespace(
            account_country=lambda: "US", probe_download=lambda sid, **kw: calls.append(sid) or {"appId": sid, "country": "US"}))),
    )
    fake = SimpleNamespace(session=session, expected_country="US", owned_control=True, targets=[("1117817509", "")],
                           started=True, report={"results": []}, _write=lambda: None)
    DownloadDiagnostics._run(fake, "person@example.com")
    assert fake.report["state"] == "complete" and fake.report["purchasedControlId"] == "123"
    assert calls == ["123", "1117817509"]
    assert "private" not in json.dumps(fake.report) and "@" not in json.dumps(fake.report)


def test_diagnostics_never_use_another_accounts_purchase_control():
    pytest.importorskip("PySide6")
    from apprestore_gui.download_diagnostics import DownloadDiagnostics

    calls = []
    session = SimpleNamespace(
        _tool_lock=Lock(), signedIn=True, accountEmail="person@example.com", authPhase="in",
        _purchases=SimpleNamespace(account="old-account-key", _cache=SimpleNamespace(account_key=lambda account: "current-account-key"),
                                   view=SimpleNamespace(rows=[{"trackId": "123"}])),
        service=SimpleNamespace(keychain_ready=lambda: True, core=SimpleNamespace(tools=SimpleNamespace(
            probe_download=lambda sid, **kw: calls.append(sid) or {"appId": sid}))),
    )
    fake = SimpleNamespace(session=session, owned_control=True, targets=[("1117817509", "")],
                           report={"results": []}, _write=lambda: None)
    DownloadDiagnostics._run(fake, "person@example.com")
    assert fake.report["purchasedControlMissing"] and "purchasedControlId" not in fake.report
    assert calls == ["1117817509"]
