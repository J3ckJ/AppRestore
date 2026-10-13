"""Explicit, bounded restoration experiment using the normal license gate.

Default mode requests download metadata only. --acquire-license authorizes
the normal guarded license request for the selected IDs. No IPA is downloaded
or installed; an offered URL/SINF is deliberately not called a restored app.
"""

from __future__ import annotations

import argparse
import getpass
import json
import os
import re
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from . import delisted_attempt
from .download_probe import positive_id
from .ipatool_api import classify_error
from .license_gate import run_with_free_license
from .license_journal import PurchaseRefused
from .region_probe import RegionProbe
from .tools import ToolUnavailable


def parse_targets(values: list[str]) -> list[tuple[str, str]]:
    result = []
    for value in values:
        parts = value.split(":")
        if len(parts) not in (1, 2):
            raise ValueError("use APP_ID or APP_ID:VERSION_ID")
        row = (positive_id(parts[0]), positive_id(parts[1]) if len(parts) == 2 else "")
        if row not in result:
            result.append(row)
    if not 1 <= len(result) <= 8:
        raise ValueError("select between one and eight apps")
    if len({sid for sid, _ in result}) != len(result):
        raise ValueError("select at most one version per app in one experiment")
    return result


def write_report(path: Path, report: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=".restore-probe-", dir=path.parent)
    temporary = Path(name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(report, stream, ensure_ascii=False, indent=2, allow_nan=False)
            stream.write("\n")
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def probe_outcome(probe: dict) -> str:
    channels = probe.get("channels", [])
    if any(c.get("status") in {"authentication_required", "store_mismatch"} for c in channels):
        return "account_unavailable"
    if any(c.get("status") == "package_and_license_offered" for c in channels):
        return "package_and_license_offered"
    # A refusal from a secondary channel alone is insufficient to start a
    # transaction: use the same primary license signal as a normal download.
    if any(c.get("channel") == "volume_unpinned" and c.get("status") == "license_required" for c in channels):
        return "license_required"
    return "not_offered"


def run_report(tools, targets: list[tuple[str, str]], output: Path, *,
               acquire_license: bool = False, expected_country: str = "",
               journal: Path | None = None, region_probe=None,
               progress: Callable[[str, str], None] | None = None) -> dict:
    targets = parse_targets([f"{sid}:{version}" if version else sid for sid, version in targets])
    expected_country = expected_country.strip().upper()
    if expected_country and not re.fullmatch(r"[A-Z]{2}", expected_country):
        raise ValueError("expected a two-letter account country")
    report = {"schemaVersion": 1, "readOnly": not acquire_license, "state": "starting",
              "startedAt": datetime.now(timezone.utc).isoformat(), "results": []}
    write_report(output, report)
    try:
        info = tools.ipatool_auth_info()
        account = str(info.get("email") or "").strip().casefold()
        country = tools.account_country().upper()
        if not account or not re.fullmatch(r"[A-Z]{2}", country):
            raise ValueError("account missing")
    except Exception:
        report["state"] = "authentication_required"
        write_report(output, report)
        return report
    report["country"] = country
    if expected_country and country != expected_country:
        report["state"] = "country_mismatch"
        write_report(output, report)
        return report
    delisted_attempt.on_account(account)
    if acquire_license:
        regions = (region_probe or RegionProbe()).classify_detailed([sid for sid, _ in targets], country)
        delisted_attempt.record_region_probe({str(sid): row.status for sid, row in regions.items()})
        delisted_attempt.record_prices({str(sid): row.known_price for sid, row in regions.items()})
    report["state"] = "running"
    for sid, version in targets:
        row = {"appId": sid, "requestedVersionId": version, "probes": []}
        report["results"].append(row)
        tools.last_purchase_diagnostic = {"appId": sid, "attempts": []}

        def check_session():
            try:
                current = tools.ipatool_auth_info()
                if str(current.get("email") or "").strip().casefold() != account or tools.account_country().upper() != country:
                    raise ToolUnavailable("not authenticated: account changed")
            except Exception:
                row["outcome"] = "account_unavailable"
                raise

        def attempt():
            # Check the local session before every operation, including the
            # download probe immediately after an optional license request.
            check_session()
            if progress:
                progress(sid, "probe_download")
            probe = tools.probe_download(sid, version_id=version)
            row["probes"].append(probe)
            row["outcome"] = probe_outcome(probe)
            write_report(output, report)
            if row["outcome"] == "license_required":
                raise ToolUnavailable("license is required")
            if row["outcome"] != "package_and_license_offered":
                raise ToolUnavailable("download channels did not offer a package and license")
            return probe

        def before_purchase(_notice):
            try:
                check_session()
            except Exception as exc:
                # No transaction was sent: do not record an uncertain purchase.
                raise PurchaseRefused("session unavailable before purchase") from exc
            if progress:
                progress(sid, "license_request")

        try:
            run_with_free_license(sid, attempt, tools=tools, acquire=acquire_license,
                                  journal=journal, mode="diagnostic",
                                  notify=before_purchase)
        except Exception as exc:
            row["errorCategory"] = classify_error(str(exc)).value
        row["purchase"] = tools.last_purchase_diagnostic
        if (len(row["probes"]) == 1 and row.get("outcome") == "license_required"
                and any(a.get("outcome") == "uncertain" for a in row["purchase"].get("attempts", []))):
            # The transaction may have succeeded despite a lost/malformed
            # reply. Check access once, without sending another purchase.
            try:
                attempt()
            except Exception:
                pass
        write_report(output, report)
        if row.get("outcome") == "account_unavailable" or row.get("errorCategory") in {
            "not_signed_in", "token_expired", "account_disabled", "rate_limited",
        }:
            report["state"] = "account_unavailable"
            break
    else:
        report["state"] = "complete"
    write_report(output, report)
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--app", action="append", required=True, metavar="APP_ID[:VERSION_ID]")
    parser.add_argument("--country", default="", help="require the actual account country; never change it")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--acquire-license", action="store_true", help="authorize the normal guarded license request for these IDs")
    args = parser.parse_args(argv)
    try:
        targets = parse_targets(args.app)
    except ValueError as exc:
        parser.error(str(exc))
    from apprestore_gui.auth_pty import KeychainRunner
    from .tools import AppRestoreTools
    passphrase = getpass.getpass("Пароль локальной связки ipatool (не Apple ID): ")
    tools = AppRestoreTools(KeychainRunner(lambda: passphrase))
    report = run_report(tools, targets, args.output, acquire_license=args.acquire_license,
                        expected_country=args.country,
                        progress=lambda sid, phase: print(f"{sid}: {phase}", flush=True))
    passphrase = ""
    print(f"{report['state']}: {args.output}")
    return 0 if report["state"] == "complete" else 1


if __name__ == "__main__":
    raise SystemExit(main())
