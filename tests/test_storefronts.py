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
    def __init__(self, stdout: str) -> None:
        self.stdout = stdout

    def run(self, args, **_kwargs) -> CommandResult:
        return CommandResult(tuple(args), 0, self.stdout, "")


def test_tools_account_country_uses_auth_info_json() -> None:
    tools = AppRestoreTools(_Runner(json.dumps({"email": "a@b.c", "storeFront": "143469-16,29", "success": True})))  # type: ignore[arg-type]
    with patch("apprestore_core.tools.resolve_tool", return_value="ipatool"), patch.object(
        AppRestoreTools, "_ipatool_env", return_value={}
    ):
        assert tools.account_country() == "ru"
