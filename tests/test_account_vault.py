from pathlib import Path

from apprestore_gui.account_vault import (
    forget_session,
    has_session,
    next_account_step,
    restore_session,
    save_session,
)


def test_saved_session_can_be_restored_without_touching_the_other(tmp_path: Path) -> None:
    live = tmp_path / "live"
    root = tmp_path / "vaults"
    live.mkdir()
    (live / "account").write_text("secret-a", encoding="utf-8")
    (live / "cookies").write_text("cookies-a", encoding="utf-8")
    assert save_session("One@iCloud.com", live=live, root=root)
    (live / "account").write_text("secret-b", encoding="utf-8")
    (live / "cookies").write_text("cookies-b", encoding="utf-8")
    assert save_session("two@icloud.com", live=live, root=root)

    assert has_session("one@icloud.com", root)
    assert restore_session("one@icloud.com", live=live, root=root)
    assert (live / "account").read_text(encoding="utf-8") == "secret-a"
    assert (live / "cookies").read_text(encoding="utf-8") == "cookies-a"
    assert has_session("two@icloud.com", root)


def test_forget_removes_only_that_account(tmp_path: Path) -> None:
    live = tmp_path / "live"
    root = tmp_path / "vaults"
    live.mkdir()
    (live / "account").write_text("secret-a", encoding="utf-8")
    save_session("one@icloud.com", live=live, root=root)
    (live / "account").write_text("secret-b", encoding="utf-8")
    save_session("two@icloud.com", live=live, root=root)
    forget_session("one@icloud.com", root)
    assert not has_session("one@icloud.com", root)
    assert has_session("two@icloud.com", root)


def test_next_account_step_switches_only_a_known_different_session() -> None:
    assert next_account_step("a@icloud.com", "a@icloud.com", saved=True, kept=False) == "stay"
    assert next_account_step("a@icloud.com", "b@icloud.com", saved=True, kept=False) == "switch"
    assert next_account_step("a@icloud.com", "b@icloud.com", saved=False, kept=False) == "login"
    assert next_account_step("a@icloud.com", "b@icloud.com", saved=True, kept=True) == "stay"
    assert next_account_step("", "b@icloud.com", saved=True, kept=False) == "stay"
