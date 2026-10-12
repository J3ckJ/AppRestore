"""Explicit diagnostic launch: wait for GUI unlock, probe once, save safe data.

Normal app launches never run this. Credentials stay in the existing GUI stdin
runner; the report contains no email, tokens, URLs, cookies, GUID or SINF data.
"""

from __future__ import annotations

import json
import os
import re
import tempfile
import threading
import time
from collections.abc import Sequence
from datetime import datetime, timezone
from pathlib import Path

from PySide6.QtCore import QObject, QTimer

from apprestore_core.download_probe import positive_id


def parse_targets(values: Sequence[str]) -> list[tuple[str, str]]:
    targets = []
    for value in values:
        parts = value.split(":")
        if len(parts) not in (1, 2):
            raise ValueError("use APP_ID or APP_ID:VERSION_ID")
        target = (positive_id(parts[0]), positive_id(parts[1]) if len(parts) == 2 else "")
        if target not in targets:
            targets.append(target)
    if not 1 <= len(targets) <= 8:
        raise ValueError("choose between one and eight diagnostic apps")
    return targets


def diagnostic_country(value: str) -> str:
    country = value.strip().upper()
    if country and not re.fullmatch(r"[A-Z]{2}", country):
        raise ValueError("expected a two-letter account country")
    return country


def purchased_control(rows: Sequence[object], targets: list[tuple[str, str]]) -> str:
    from collections.abc import Mapping

    excluded = {sid for sid, _ in targets}
    for row in rows:
        if not isinstance(row, Mapping):
            continue
        try:
            sid = positive_id(row.get("trackId"))
        except ValueError:
            continue
        if sid not in excluded:
            return sid
    return ""


def write_report(path: Path, report: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=".download-probe-", dir=path.parent)
    temporary = Path(name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(report, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


class DownloadDiagnostics(QObject):
    def __init__(self, session, targets: list[tuple[str, str]], output: Path, *, expected_country: str = "", owned_control: bool = False) -> None:
        super().__init__()
        self.session, self.targets, self.output = session, targets, output
        self.started = False
        self.expected_country = diagnostic_country(expected_country)
        self.owned_control = owned_control
        self.retry_at = 0.0
        self.report: dict[str, object] = {
            "schemaVersion": 1, "readOnly": True, "state": "waiting_for_keychain",
            "startedAt": datetime.now(timezone.utc).isoformat(), "results": [],
        }
        self._write()
        self.timer = QTimer(self)
        self.timer.setInterval(500)
        self.timer.timeout.connect(self._consider)
        self.timer.start()

    def _write(self) -> None:
        try:
            write_report(self.output, self.report)
        except OSError:
            pass  # Never interrupt the real window for an optional report.

    def _consider(self) -> None:
        s = self.session
        if self.started or time.monotonic() < getattr(self, "retry_at", 0) or not s.signedIn or not s.accountEmail or s.authPhase != "in" or not s.service.keychain_ready():
            return
        with s._lock:
            busy = s._busy or s._files_busy
        if busy or s.purchasesBusy or s.sessionState == "checking":
            return
        if getattr(self, "owned_control", False):
            loader = s._purchases
            if loader.account != loader._cache.account_key(s.accountEmail):
                return  # GUI auth can finish just before history follows this account.
        self.started = True
        account = s.accountEmail
        threading.Thread(target=self._run, args=(account,), name="apprestore-download-probes", daemon=True).start()

    def _run(self, account: str) -> None:
        expected_country = getattr(self, "expected_country", "")
        if expected_country:
            with self.session._tool_lock:
                if self.session.accountEmail != account or not self.session.service.keychain_ready():
                    self.report["state"] = "account_changed_or_locked"
                    self._write()
                    return
                try:
                    actual_country = diagnostic_country(self.session.service.core.tools.account_country() or "")
                except Exception:
                    self.report["state"] = "account_country_unavailable"
                    self._write()
                    return
            if actual_country != expected_country:
                self.report.update(state="waiting_for_country", requiredCountry=expected_country)
                self._write()
                self.retry_at = time.monotonic() + 10
                self.started = False  # Qt timer remains armed; never change account/storefront.
                return
        targets = list(self.targets)
        if getattr(self, "owned_control", False):
            loader = self.session._purchases
            rows = loader.view.rows if loader.account == loader._cache.account_key(account) else ()
            sid = purchased_control(rows, targets)
            if sid:
                targets.insert(0, (sid, ""))
                self.report["purchasedControlId"] = sid
            else:
                self.report["purchasedControlMissing"] = True
        self.report["state"] = "running"
        self._write()
        results = self.report["results"]
        for store_id, version_id in targets:
            s = self.session
            with s._tool_lock:
                if not s.signedIn or s.accountEmail != account or s.authPhase != "in" or not s.service.keychain_ready():
                    self.report["state"] = "account_changed_or_locked"
                    self._write()
                    return
                try:
                    result = s.service.core.tools.probe_download(store_id, version_id=version_id)
                except Exception:  # Raw errors can contain private response data.
                    result = {"appId": store_id, "status": "probe_failed"}
            if s.accountEmail != account or not s.signedIn:
                self.report["state"] = "account_changed_or_locked"
                self._write()
                return
            results.append(result)
            self._write()
        self.report["state"] = "complete"
        self._write()
