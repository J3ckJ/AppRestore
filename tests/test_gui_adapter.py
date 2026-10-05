from __future__ import annotations

from apprestore_gui.service_adapter import GuiService


def test_demo_adapter_lists_apps() -> None:
    svc = GuiService(demo_mode=True)
    devices = svc.devices()
    assert devices and devices[0].name == "iPhone 14"
    apps = svc.offloaded(devices[0].udid)
    assert len(apps) >= 3
    assert svc.missing(devices[0].udid)
    assert svc.doctor()
    assert svc.search("spot")
