from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from apprestore_core.models import DeviceAppState, MissingApp, OffloadedApp
from apprestore_core.service import AppRestoreError, AppRestoreService
from apprestore_core.tools import InstallRequestState
from tests.helpers import make_ipa


@pytest.fixture(autouse=True)
def only_test_library(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "apprestore_core.service.ipa_search_roots", lambda library: [library]
    )
    monkeypatch.setattr(
        "apprestore_core.service.remember_known_app", lambda **kwargs: None
    )


class AccountTools:
    def __init__(self, download: Path | None = None) -> None:
        self.email: str | None = "one@example.com"
        self.download = download
        self.installs = 0
        self.downloads = 0

    def ipatool_auth_info(self) -> dict[str, object] | None:
        return {"email": self.email, "success": True} if self.email else None

    def ipatool_authenticated(self) -> bool:
        return self.email is not None

    def download_ipa(self, output: Path, **kwargs: object) -> bool:
        self.downloads += 1
        assert self.download is not None
        shutil.copyfile(self.download, output)
        return True

    def device_app_snapshot(self, *args: str) -> tuple[DeviceAppState, str]:
        return DeviceAppState.INSTALLED, "1.0"

    def install_ipa(self, *args: object) -> InstallRequestState:
        self.installs += 1
        return InstallRequestState.COMPLETED


def service(tmp_path: Path) -> tuple[AppRestoreService, AccountTools]:
    tools = AccountTools()
    core = AppRestoreService(
        tools, library=tmp_path / "ipas", cache=tmp_path / "cache"
    )
    return core, tools


def commit(core: AppRestoreService, tmp_path: Path, name: str = "download") -> Path:
    path = make_ipa(tmp_path / f"{name}.ipa")
    verified = core._verify_ipa(path)
    key = core._ipa_account_key()
    assert key is not None
    return core._commit_downloaded_ipa(
        path, verified, expected_bundle_id="com.example.alpha", account_key=key
    )[0]


def test_switch_filters_cached_inventory_and_keeps_each_accounts_file(tmp_path: Path) -> None:
    core, tools = service(tmp_path)
    first = commit(core, tmp_path)
    assert core.find_local("com.example.alpha") == first
    tools.email = "two@example.com"
    assert core.find_local("com.example.alpha") is None
    second = commit(core, tmp_path, "second")
    assert first != second and first.exists() and second.exists()
    core.scan_local(refresh=True)
    assert core.find_local("com.example.alpha") == second
    tools.email = "one@example.com"
    assert core.find_local("com.example.alpha") == first


def test_legacy_cache_is_visible_but_not_selected_or_installed(tmp_path: Path) -> None:
    core, tools = service(tmp_path)
    legacy = make_ipa(core.library / "legacy.ipa")
    assert core.find_local("com.example.alpha") is None
    assert len(core.scan_local()[0]) == 1
    with pytest.raises(AppRestoreError, match="no verified owner"):
        core.install("device", legacy)
    assert tools.installs == 0


def test_wrong_account_and_signed_out_session_block_cached_install(tmp_path: Path) -> None:
    core, tools = service(tmp_path)
    path = commit(core, tmp_path)
    for email in ("two@example.com", None):
        tools.email = email
        with pytest.raises(AppRestoreError, match="another Apple ID"):
            core.install("device", path)
    assert tools.installs == 0


def test_external_manual_install_still_works_offline(tmp_path: Path) -> None:
    core, tools = service(tmp_path)
    tools.email = None
    path = make_ipa(tmp_path / "external.ipa")
    assert core.install("device", path).bundle_id == "com.example.alpha"
    assert tools.installs == 1
    assert core.find_local("com.example.alpha") is None


def test_switch_during_install_callback_stops_before_device_request(tmp_path: Path) -> None:
    core, tools = service(tmp_path)
    path = commit(core, tmp_path)
    core.on_device_install = lambda: setattr(tools, "email", "two@example.com")
    with pytest.raises(AppRestoreError, match="changed before installation"):
        core.install("device", path)
    assert tools.installs == 0


def test_account_changed_during_download_cannot_commit(tmp_path: Path) -> None:
    core, tools = service(tmp_path)
    key = core._ipa_account_key()
    assert key is not None
    path = make_ipa(tmp_path / "download.ipa")
    verified = core._verify_ipa(path)
    tools.email = "two@example.com"
    with pytest.raises(AppRestoreError, match="changed during download"):
        core._commit_downloaded_ipa(
            path, verified, expected_bundle_id="com.example.alpha", account_key=key
        )
    assert path.exists()


@pytest.mark.parametrize("symlink_parent", [False, True])
def test_symlink_account_directory_is_rejected(tmp_path: Path, symlink_parent: bool) -> None:
    core, _tools = service(tmp_path)
    key = core._ipa_account_key()
    assert key is not None
    directory = core._account_ipa_directory(key)
    outside = tmp_path / "outside"
    outside.mkdir()
    try:
        if symlink_parent:
            directory.parent.symlink_to(outside, target_is_directory=True)
        else:
            directory.parent.mkdir()
            directory.symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip("symlink creation is unavailable")
    assert not core._ipa_matches_account(make_ipa(outside / "test.ipa"))
    make_ipa(directory / "cached.ipa")
    with pytest.raises(AppRestoreError, match="another Apple ID"):
        core.install("device", directory / "cached.ipa")
    with pytest.raises(AppRestoreError, match="symlink"):
        commit(core, tmp_path)


@pytest.mark.parametrize("by_store_id", [False, True])
def test_public_download_is_saved_under_live_account(tmp_path: Path, by_store_id: bool) -> None:
    core, tools = service(tmp_path)
    tools.download = make_ipa(tmp_path / "source.ipa")
    path = (
        core.download_by_store_id("123456789")
        if by_store_id else core.download("com.example.alpha", "123456789")
    )
    key = core._ipa_account_key()
    assert key is not None
    assert path.parent == core._account_ipa_directory(key)
    assert core.find_local("com.example.alpha") == path
    tools.email = "two@example.com"
    assert core.find_local("com.example.alpha") is None


def test_unknown_account_refuses_download_before_network_request(tmp_path: Path) -> None:
    core, tools = service(tmp_path)
    tools.email = None
    with pytest.raises(AppRestoreError, match="Sign in"):
        core.download("com.example.alpha", "123456789")
    assert tools.downloads == 0


@pytest.mark.parametrize("missing", [False, True])
def test_stale_app_object_cannot_bypass_filter(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, missing: bool
) -> None:
    core, tools = service(tmp_path)
    path = commit(core, tmp_path)
    tools.email = "two@example.com"

    def fresh_download(*args: object, **kwargs: object) -> Path:
        raise AppRestoreError("fresh download requested")

    monkeypatch.setattr(core, "download", fresh_download)
    monkeypatch.setattr(core, "resolve_store_id", lambda *args: "123456789")
    monkeypatch.setattr(core, "resolve_missing_store_id", lambda *args: "123456789")
    app = (MissingApp if missing else OffloadedApp)(
        bundle_id="com.example.alpha", name="Alpha", version="1.0", local_ipa=path
    )
    with pytest.raises(AppRestoreError, match="fresh download requested"):
        if missing:
            core.restore_missing("device", app)
        else:
            core.restore_offloaded("device", app, try_device_redownload=False)
    assert tools.installs == 0
