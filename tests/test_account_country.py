"""Account country for prices and purchases: ``countryCode``, else ``storeFront``,
else unknown (purchase refused). Includes Макс's real patched ipatool binary,
run with an empty HOME so no real session is ever touched."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from apprestore_core import storefronts, tools as tools_module
from apprestore_core.tools import AppRestoreTools

# `auth info --format json` from Макс's patch (auth-info-country.md), no email.
SIGNED_IN = {
    "level": "info",
    "name": "Test User",
    "storeFront": "143441-1,34",
    "countryCode": "US",
    "success": True,
    "time": "2026-10-10T17:00:00+03:00",
}

MAKS_BINARY = Path(
    os.environ.get(
        "APPRESTORE_TEST_IPATOOL_COUNTRY",
        "/workspace/apprestore/maks-scratch/ipatool-country-bin/ipatool",
    )
)


def test_signed_in_fixture_is_us() -> None:
    assert storefronts.account_country(SIGNED_IN) == "us"


def test_country_code_wins_over_store_front() -> None:
    payload = dict(SIGNED_IN, storeFront="143469-16,29")  # RU storefront
    assert storefronts.account_country(payload) == "us"


def test_store_front_when_country_code_is_missing() -> None:
    payload = {k: v for k, v in SIGNED_IN.items() if k != "countryCode"}
    assert storefronts.account_country(payload) == "us"
    assert storefronts.account_country({"storeFront": "143469-16,29", "success": True}) == "ru"


def test_unknown_country_code_falls_to_store_front() -> None:
    assert storefronts.account_country({"countryCode": "ZZ", "storeFront": "143441-1,34"}) == "us"


@pytest.mark.parametrize(
    "payload",
    [
        {"level": "info", "name": "x", "email": "x@example.com", "success": True},  # old cde7d00
        {"storeFront": "", "success": True},
        {"storeFront": "999999-1,1", "success": True},
        None,
        "garbage",
    ],
)
def test_no_country_is_unknown_not_ru(payload: object) -> None:
    assert storefronts.account_country(payload) == ""


class _Runner:
    def __init__(self, stdout: str, returncode: int = 0) -> None:
        self.stdout, self.returncode = stdout, returncode

    def run(self, args, **kwargs):  # noqa: ANN001
        from apprestore_core.command import CommandResult

        return CommandResult(tuple(args), self.returncode, self.stdout, "")


def test_tools_account_country_from_patched_auth_info(monkeypatch: pytest.MonkeyPatch) -> None:
    import json

    monkeypatch.setattr(tools_module, "resolve_tool", lambda name: "/fake/ipatool")
    tools = AppRestoreTools(_Runner(json.dumps(SIGNED_IN)))  # type: ignore[arg-type]
    monkeypatch.setattr(tools, "_ipatool_env", lambda: {})
    assert tools.account_country() == "us"


def test_tools_account_country_not_signed_in(monkeypatch: pytest.MonkeyPatch) -> None:
    out = '{"level":"error","error":"failed to get account: ... not be found in the keyring","success":false}'
    monkeypatch.setattr(tools_module, "resolve_tool", lambda name: "/fake/ipatool")
    tools = AppRestoreTools(_Runner(out, returncode=1))  # type: ignore[arg-type]
    monkeypatch.setattr(tools, "_ipatool_env", lambda: {})
    assert tools.account_country() == ""


@pytest.mark.skipif(os.name == "nt" or not MAKS_BINARY.is_file(), reason="Макс's patched ipatool binary not here")
def test_real_patched_binary_without_session_is_not_signed_in(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Runs only `auth info` with an empty HOME: no keyring item, no network."""

    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    for name in ("IPATOOL_KEYCHAIN_PASSPHRASE", "XDG_CONFIG_HOME", "XDG_DATA_HOME"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(tools_module, "resolve_tool", lambda name: str(MAKS_BINARY))
    tools = AppRestoreTools()
    monkeypatch.setattr(tools, "_ipatool_env", lambda: {})

    raw = tools.runner.run([str(MAKS_BINARY), "--format", "json", "auth", "info", "--non-interactive"],
                           capture=True, timeout=30)
    assert raw.returncode != 0
    assert '"success":false' in raw.stdout.replace(" ", "")
    assert "keyring" in raw.stdout or "account" in raw.stdout

    assert tools.ipatool_auth_info() is None
    assert tools.account_country() == ""
