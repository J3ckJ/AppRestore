import sys
from pathlib import Path

import pytest

from apprestore_gui import account_vault as vault
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


@pytest.fixture
def sessions(tmp_path):
    live, root = tmp_path / "live", tmp_path / "vaults"
    live.mkdir()
    for email, label in (("a@example.com", "A"), ("b@example.com", "B")):
        for name in ("account", "cookies", "kbsync-v1"):
            (live / name).write_text(f"{label}-{name}")
        assert save_session(email, live=live, root=root)
    return live, root


def snapshot(path):
    return {str(p.relative_to(path)): p.read_bytes() for p in path.rglob("*") if p.is_file()}


@pytest.mark.parametrize("saving", [False, True])
@pytest.mark.parametrize("step", ["account", "cookies", "kbsync-v1", "old", "new", "marker"])
def test_io_failure_keeps_previous_session_and_marker(sessions, monkeypatch, saving, step):
    live, root = sessions
    dest = vault.vault_path("a@example.com", root) if saving else live
    source = live if saving else vault.vault_path("a@example.com", root)
    before = snapshot(live.parent)
    copy2, rename, replace = vault.shutil.copy2, Path.rename, Path.replace

    def copy(origin, target, *args, **kwargs):
        if Path(origin).parent == source and Path(origin).name == step:
            raise OSError("simulated copy failure")
        return copy2(origin, target, *args, **kwargs)

    def move(path, target):
        if (step == "old" and path == dest) or (step == "new" and path.name == "new"):
            raise OSError("simulated rename failure")
        return rename(path, target)

    def mark(path, target):
        if step == "marker" and Path(target) == root / "active.txt":
            raise OSError("simulated marker failure")
        return replace(path, target)

    monkeypatch.setattr(vault.shutil, "copy2", copy)
    monkeypatch.setattr(Path, "rename", move)
    monkeypatch.setattr(Path, "replace", mark)
    operation = save_session if saving else restore_session
    with pytest.raises(OSError, match="simulated"):
        operation("a@example.com", live=live, root=root)
    assert snapshot(live.parent) == before
    assert not list(dest.parent.glob(f".{dest.name}-session-*"))
    assert not list(root.glob(".active-*"))


def test_restore_preserves_other_files_and_removes_absent_optional_credentials(sessions):
    live, root = sessions
    source = vault.vault_path("a@example.com", root)
    (source / "cookies").unlink()
    (source / "kbsync-v1").unlink()
    (live / "settings.json").write_text("keep me")
    assert restore_session("a@example.com", live=live, root=root)
    assert (live / "account").read_text() == "A-account"
    assert not (live / "cookies").exists()
    assert not (live / "kbsync-v1").exists()
    assert (live / "settings.json").read_text() == "keep me"
    assert vault.active_email(root) == "a@example.com"


def test_failed_rollback_retains_the_complete_previous_session(sessions, monkeypatch):
    live, root = sessions
    before = snapshot(live)
    rename = Path.rename

    def move(path, target):
        if path.name == "previous":
            raise OSError("rollback blocked")
        return rename(path, target)

    def fail_marker(*args):
        raise OSError("marker blocked")

    monkeypatch.setattr(Path, "rename", move)
    monkeypatch.setattr(vault, "_mark_active", fail_marker)
    with pytest.raises(vault.SessionRecoveryError) as error:
        restore_session("a@example.com", live=live, root=root)
    assert snapshot(error.value.backup) == before
    assert vault.active_email(root) == "b@example.com"


def test_forget_without_an_account_does_not_delete_the_vault(sessions):
    _live, root = sessions
    before = snapshot(root)
    forget_session("", root)
    assert snapshot(root) == before


@pytest.mark.skipif(sys.platform == "win32", reason="symlink creation requires privileges on Windows")
def test_credential_symlink_is_rejected_without_changing_live_files(sessions):
    live, root = sessions
    source = vault.vault_path("a@example.com", root)
    (source / "cookies").unlink()
    (source / "cookies").symlink_to(live / "cookies")
    before = snapshot(live)
    with pytest.raises(OSError, match="ссылку"):
        restore_session("a@example.com", live=live, root=root)
    assert snapshot(live) == before
