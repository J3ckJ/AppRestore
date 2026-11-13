"""One encrypted ipatool session per Apple ID.

ipatool keeps a single live directory, ``~/.ipatool``. After a session is
open, a copy of those files is kept under ``~/.apprestore/keychains``. The
copy stays encrypted with the keychain passphrase. Neither that passphrase
nor the Apple ID password is written here.
"""

from __future__ import annotations

import os
import shutil
import tempfile
import threading
from pathlib import Path

_FILES = ("account", "cookies", "kbsync-v1")
_SESSION_LOCK = threading.RLock()


class SessionRecoveryError(OSError):
    """A failed rollback left the previous session in a private backup."""

    def __init__(self, backup: Path) -> None:
        self.backup = backup
        super().__init__(f"Не удалось вернуть прежнюю сессию. Резервная копия: {backup}")


def live_dir() -> Path:
    return Path.home() / ".ipatool"


def vault_root() -> Path:
    return Path.home() / ".apprestore" / "keychains"


def active_email(root: Path | None = None) -> str:
    """Which Apple ID the live ipatool directory was last saved as."""

    try:
        text = ((root or vault_root()) / "active.txt").read_text(encoding="utf-8").strip()
    except OSError:
        return ""
    return text if "@" in text else ""


def _mark_active(email: str, root: Path) -> None:
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=root,
                                         prefix=".active-", delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(email.strip())
            stream.flush()
            os.fsync(stream.fileno())
        temporary.replace(root / "active.txt")
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def next_account_step(current: str, wanted: str, *, saved: bool, kept: bool) -> str:
    """What to do when the selected phone wants a different Apple ID.

    ``stay`` leaves the open session alone. ``switch`` restores a saved
    session. ``login`` asks for the Apple ID password, which is not stored.
    An unknown current session stays put so its files are not overwritten.
    """

    wanted = wanted.strip()
    current = current.strip()
    if kept or not wanted or not current or wanted.lower() == current.lower():
        return "stay"
    return "switch" if saved else "login"


def _folder_name(email: str) -> str:
    cleaned = email.strip().lower()
    return "".join("_" if char in '<>:"/\\|?*' else char for char in cleaned)


def vault_path(email: str, root: Path | None = None) -> Path:
    return (root or vault_root()) / _folder_name(email)


def has_session(email: str, root: Path | None = None) -> bool:
    if "@" not in email:
        return False
    return (vault_path(email, root) / "account").is_file()


def saved_accounts(root: Path | None = None) -> list[str]:
    """List saved sessions without opening encrypted credential files."""

    store = root or vault_root()
    try:
        folders = list(store.iterdir())
    except OSError:
        return []
    result: dict[str, str] = {}
    for folder in folders:
        if folder.is_symlink() or not folder.is_dir() or not (folder / "account").is_file():
            continue
        try:
            email = (folder / "email.txt").read_text(encoding="utf-8").strip()
        except (OSError, UnicodeError):
            email = folder.name  # Sessions saved before multi-account enumeration.
        if "@" in email and _folder_name(email) == folder.name:
            result[email.lower()] = email
    return [result[key] for key in sorted(result)]


def save_session(email: str, *, live: Path | None = None, root: Path | None = None) -> bool:
    email = email.strip()
    if "@" not in email:
        return False
    source = live or live_dir()
    dest = vault_path(email, root)
    with _SESSION_LOCK:
        if not (source / "account").is_file():
            return False
        _replace_session(source, dest, email, dest.parent, saved=True)
        return True


def restore_session(email: str, *, live: Path | None = None, root: Path | None = None) -> bool:
    email = email.strip()
    if "@" not in email:
        return False
    source = vault_path(email, root)
    dest = live or live_dir()
    with _SESSION_LOCK:
        if not (source / "account").is_file():
            return False
        _replace_session(source, dest, email, source.parent, saved=False)
        return True


def forget_session(email: str, root: Path | None = None) -> None:
    if "@" not in email:
        return
    with _SESSION_LOCK:
        _forget_session(email, root)


def _forget_session(email: str, root: Path | None = None) -> None:
    store = root or vault_root()
    folder = vault_path(email, store)
    if folder.is_dir():
        shutil.rmtree(folder)
    marker = store / "active.txt"
    if active_email(store).lower() == email.strip().lower():
        try:
            marker.unlink()
        except OSError:
            pass


def _replace_session(source: Path, dest: Path, email: str, root: Path, *, saved: bool) -> None:
    """Stage a complete directory before replacing it, retaining rollback data.

    The caller holds _SESSION_LOCK and QuickSession excludes ipatool work while
    switching. Directory renames stay on the same volume. Unrelated ipatool
    files are preserved. This handles I/O failures; it is not a cross-process
    lock or a guarantee against power loss between the directory renames.
    """

    if source.is_symlink() or dest.is_symlink():
        raise OSError("Нельзя использовать ссылку вместо каталога сессии")
    dest.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    transaction = Path(tempfile.mkdtemp(prefix=f".{dest.name}-session-", dir=dest.parent))
    staged = transaction / "new"
    backup = transaction / "previous"
    moved = installed = False
    keep_backup = False
    try:
        if dest.exists():
            shutil.copytree(dest, staged, symlinks=True)
            staged.chmod(0o700)
        else:
            staged.mkdir(mode=0o700)
        _copy_session(source, staged)
        if saved:
            label = staged / "email.txt"
            label.unlink(missing_ok=True)
            label.write_text(email, encoding="utf-8")
            label.chmod(0o600)
        if dest.exists():
            dest.rename(backup)
            moved = True
        staged.rename(dest)
        installed = True
        _mark_active(email, root)
    except BaseException:
        try:
            if installed:
                dest.rename(staged)
            if moved:
                backup.rename(dest)
        except OSError as rollback_error:
            keep_backup = True
            raise SessionRecoveryError(backup if moved else dest) from rollback_error
        raise
    finally:
        # Never delete the user's previous session if rollback itself failed.
        if not keep_backup:
            shutil.rmtree(transaction, ignore_errors=True)


def _copy_session(source: Path, dest: Path) -> None:
    for name in _FILES:
        origin = source / name
        target = dest / name
        # A staged symlink must never redirect a write outside the transaction.
        target.unlink(missing_ok=True)
        if origin.is_symlink():
            raise OSError("Нельзя использовать ссылку вместо файла сессии")
        if origin.is_file():
            shutil.copy2(origin, target)
            target.chmod(0o600)
