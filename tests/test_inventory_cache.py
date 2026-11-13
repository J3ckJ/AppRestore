"""Concurrent GUI inventories must stay fresh across refreshes and installs."""
from concurrent.futures import ThreadPoolExecutor
from threading import Event, Lock
from types import SimpleNamespace

import pytest

from apprestore_core.models import DeviceAppState
from apprestore_core.service import AppRestoreService
from apprestore_core.tools import InstallRequestState
from tests.helpers import make_ipa


def service_at(tmp_path, tools):
    service = AppRestoreService(tools=tools, library=tmp_path / "ipas", cache=tmp_path / "cache")
    service.device_inventory_ttl = 5.0
    return service


def test_concurrent_readers_share_inventory(tmp_path):
    entered, release = Event(), Event()
    calls = []

    def read(udid):
        calls.append(udid)
        entered.set()
        assert release.wait(5)
        return {"app": {"CFBundleIdentifier": "app"}}

    service = service_at(tmp_path, SimpleNamespace(list_apps=read))
    with ThreadPoolExecutor(max_workers=3) as pool:
        jobs = [pool.submit(service._device_apps, "phone") for _ in range(3)]
        assert entered.wait(5)
        release.set()
        results = [job.result(5) for job in jobs]
    assert calls == ["phone"]
    assert results == [results[0]] * 3


def test_refresh_does_not_wait_for_or_cache_an_old_inflight_read(tmp_path):
    entered, release = Event(), Event()
    counter = Lock()
    calls = []

    def read(udid):
        with counter:
            calls.append(udid)
            n = len(calls)
        if n == 1:
            entered.set()
            assert release.wait(5)
        return {"generation": n}

    service = service_at(tmp_path, SimpleNamespace(list_apps=read))
    with ThreadPoolExecutor(max_workers=2) as pool:
        old = pool.submit(service._device_apps, "phone")
        assert entered.wait(5)
        service.invalidate_device_apps()
        try:
            assert pool.submit(service._device_apps, "phone").result(5) == {"generation": 2}
        finally:
            release.set()
        assert old.result(5) == {"generation": 1}
    assert service._device_apps("phone") == {"generation": 2}
    assert len(calls) == 2


def test_inventory_expiry_device_separation_and_cli_freshness(tmp_path, monkeypatch):
    now = [100.0]
    monkeypatch.setattr("apprestore_core.service.time.monotonic", lambda: now[0])
    calls = []

    def read(udid):
        calls.append(udid)
        return {"revision": len(calls)}

    service = service_at(tmp_path, SimpleNamespace(list_apps=read))
    assert service._device_apps("one") == service._device_apps("one") == {"revision": 1}
    assert service._device_apps("two") == {"revision": 2}
    now[0] += 6
    assert service._device_apps("one") == {"revision": 3}
    service.device_inventory_ttl = 0
    assert service._device_apps("one") == {"revision": 4}
    assert service._device_apps("one") == {"revision": 5}


def test_inventory_errors_are_retried(tmp_path):
    calls = []

    def read(udid):
        calls.append(udid)
        if len(calls) == 1:
            raise OSError("USB disconnected")
        return {}

    service = service_at(tmp_path, SimpleNamespace(list_apps=read))
    with pytest.raises(OSError, match="disconnected"):
        service._device_apps("phone")
    assert service._device_apps("phone") == {}
    assert len(calls) == 2


def test_install_verifies_live_state_and_invalidates_inventory(tmp_path):
    inventories, snapshots = [], []

    def read(udid):
        inventories.append(udid)
        return {"revision": len(inventories)}

    def snapshot(udid, bundle):
        snapshots.append((udid, bundle))
        return (DeviceAppState.INSTALLED, "1.0")

    service = service_at(tmp_path, SimpleNamespace(list_apps=read, device_app_snapshot=snapshot))

    def install(udid, path):
        # Even an inventory requested during installation is discarded afterwards.
        service._device_apps(udid)
        return InstallRequestState.COMPLETED

    service.tools.install_ipa = install
    service._device_apps("phone")
    service.install("phone", make_ipa(tmp_path / "app.ipa"))
    assert len(snapshots) >= 2
    assert service._device_apps("phone") == {"revision": 3}


def test_parallel_library_views_scan_files_once(tmp_path, monkeypatch):
    entered, release = Event(), Event()
    calls = []

    def scan(*args, **kwargs):
        calls.append(1)
        entered.set()
        assert release.wait(5)
        return [], []

    monkeypatch.setattr("apprestore_core.service.scan_ipas", scan)
    service = service_at(tmp_path, SimpleNamespace())
    with ThreadPoolExecutor(max_workers=3) as pool:
        jobs = [pool.submit(service.scan_local) for _ in range(3)]
        assert entered.wait(5)
        release.set()
        assert [job.result(5) for job in jobs] == [([], [])] * 3
    assert calls == [1]
