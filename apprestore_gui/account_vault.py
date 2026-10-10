"""One encrypted ipatool session per Apple ID.

ipatool keeps a single live directory, ``~/.ipatool``. After a session is
open, a copy of those files is kept under ``~/.apprestore/keychains``. The
copy stays encrypted with the keychain passphrase. Neither that passphrase
nor the Apple ID password is written here.
"""

from __future__ import annotations

import shutil
from pathlib import Path

_FILES = ("account", "cookies", "kbsync-v1")


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
    root.mkdir(parents=True, exist_ok=True)
    (root / "active.txt").write_text(email.strip(), encoding="utf-8")


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


def save_session(email: str, *, live: Path | None = None, root: Path | None = None) -> bool:
    email = email.strip()
    if "@" not in email:
        return False
    source = live or live_dir()
    if not (source / "account").is_file():
        return False
    dest = vault_path(email, root)
    store = dest.parent
    dest.mkdir(parents=True, exist_ok=True)
    _copy_session(source, dest)
    _mark_active(email, store)
    return True


def restore_session(email: str, *, live: Path | None = None, root: Path | None = None) -> bool:
    source = vault_path(email, root)
    if not (source / "account").is_file():
        return False
    dest = live or live_dir()
    dest.mkdir(parents=True, exist_ok=True)
    _copy_session(source, dest)
    _mark_active(email, source.parent)
    return True


def forget_session(email: str, root: Path | None = None) -> None:
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


def _copy_session(source: Path, dest: Path) -> None:
    for name in _FILES:
        origin = source / name
        target = dest / name
        if origin.is_file():
            shutil.copy2(origin, target)
        elif target.exists():
            target.unlink()
