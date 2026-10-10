from __future__ import annotations

import json
from unittest.mock import patch

from apprestore_core.models import CommandResult
from apprestore_core.storefronts import account_country, country_from_storefront
from apprestore_core.tools import AppRestoreTools


def test_storefront_ids_map_to_lookup_country() -> None:
    assert country_from_storefront("143469-16,29") == "ru"
    assert country_from_storefront("143441") == "us"
    assert country_from_storefront("143517-2,32") == "kz"
    assert country_from_storefront("") == ""
    assert country_from_storefront("999999-1") == ""


def test_account_country_reads_auth_info_shapes() -> None:
    # ipatool cde7d00 prints only name/email/success: no country → fallback.
    assert account_country({"name": "A", "email": "a@b.c", "success": True}) == ""
    assert account_country({"storeFront": "143469-16,29", "success": True}) == "ru"
    assert account_country({"countryCode": "KZ"}) == "kz"
    assert account_country({"account": {"storeFront": "143441-1,29"}}) == "us"
    assert account_country(None) == ""


class _Runner:
    def __init__(self, stdout: str, returncode: int = 0) -> None:
        self.stdout = stdout
        self.returncode = returncode

    def run(self, args, **_kwargs) -> CommandResult:
        return CommandResult(tuple(args), self.returncode, self.stdout, "")


def test_tools_account_country_uses_auth_info_json() -> None:
    tools = AppRestoreTools(_Runner(json.dumps({"email": "a@b.c", "storeFront": "143469-16,29", "success": True})))  # type: ignore[arg-type]
    with patch("apprestore_core.tools.resolve_tool", return_value="ipatool"), patch.object(
        AppRestoreTools, "_ipatool_env", return_value={}
    ):
        assert tools.account_country() == "ru"


# Exact lines from AppRestore's ipatool build (cde7d00 + packaging/patches/
# ipatool-auth-info-country.patch), `auth info --format json`.
_PATCHED_AUTH_INFO = (
    '{"level":"info","name":"A","email":"a@b.c","storeFront":"143441-1,34",'
    '"countryCode":"US","success":true,"time":"2026-10-10T17:56:34+03:00"}\n'
)
_NO_SESSION = (
    '{"level":"error","error":"failed to get account: failed to get item: The specified '
    'item could not be found in the keyring","success":false,"time":"2026-10-10T17:56:34+03:00"}\n'
)


def _tools(stdout: str, returncode: int = 0) -> str:
    tools = AppRestoreTools(_Runner(stdout, returncode))  # type: ignore[arg-type]
    with patch("apprestore_core.tools.resolve_tool", return_value="ipatool"), patch.object(
        AppRestoreTools, "_ipatool_env", return_value={}
    ):
        return tools.account_country()


def test_tools_account_country_reads_patched_ipatool_line() -> None:
    assert _tools(_PATCHED_AUTH_INFO) == "us"


def test_tools_account_country_storefront_only_when_country_code_missing() -> None:
    # Storefront outside ipatool's map: the patch omits countryCode.
    line = '{"level":"info","name":"A","email":"a@b.c","storeFront":"143469-16,29","success":true}'
    assert _tools(line) == "ru"


def test_tools_account_country_is_unknown_without_session() -> None:
    assert _tools(_NO_SESSION, returncode=1) == ""
    assert _tools(_NO_SESSION, returncode=0) == ""
