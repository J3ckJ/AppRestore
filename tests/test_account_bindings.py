from pathlib import Path

from apprestore_gui.account_bindings import account_email, load_bindings, remember_binding


def test_binding_remembers_an_email_for_one_device(tmp_path: Path) -> None:
    path = tmp_path / "device-accounts.json"
    remember_binding("UDID-A", "one@icloud.com", path)
    remember_binding("UDID-B", "two@icloud.com", path)
    assert load_bindings(path) == {
        "UDID-A": "one@icloud.com",
        "UDID-B": "two@icloud.com",
    }


def test_binding_ignores_a_value_that_is_not_an_email(tmp_path: Path) -> None:
    path = tmp_path / "device-accounts.json"
    remember_binding("UDID-A", "not-an-email", path)
    assert load_bindings(path) == {}


def test_account_email_reads_the_identity_ipatool_returns() -> None:
    assert account_email({"email": "one@icloud.com"}) == "one@icloud.com"
    assert account_email({"appleId": "two@icloud.com"}) == "two@icloud.com"
    assert account_email({"authenticated": True}) == ""
    assert account_email(None) == ""
