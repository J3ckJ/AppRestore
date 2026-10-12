from types import SimpleNamespace


def test_usbmux_selftest_reports_count_without_device_identity(monkeypatch):
    from apprestore_core import tools
    from apprestore_gui.selftest import _usbmux_client

    monkeypatch.setattr(tools, "AppRestoreTools", lambda: SimpleNamespace(
        _list_udids_in_process=lambda **kwargs: ["private-device-id"],
    ))
    result = _usbmux_client()
    assert result == {"device_count": 1}
    assert "private-device-id" not in repr(result)
