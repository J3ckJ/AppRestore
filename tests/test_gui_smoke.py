from __future__ import annotations

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("pytestqt")

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QPushButton

from apprestore_gui.export_dialog import ExportFromDeviceDialog

from apprestore_gui.icons_cache import ArtworkCache
from apprestore_gui.main_window import MainWindow, NAV
from apprestore_gui.service_adapter import GuiService
from apprestore_gui.theme import load_fonts, STYLESHEET


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    load_fonts()
    app.setStyleSheet(STYLESHEET)
    return app


def test_main_window_opens_all_pages(qapp, qtbot) -> None:
    service = GuiService(demo_mode=True)
    window = MainWindow(service, ArtworkCache())
    qtbot.addWidget(window)
    window.show()
    for key, _label in NAV:
        window._show_page(key)
        assert window.stack.currentIndex() >= 0


def test_store_acquire_checkbox_is_off_by_default(qapp, qtbot) -> None:
    from PySide6.QtWidgets import QHeaderView, QTableWidget

    service = GuiService(demo_mode=True)
    window = MainWindow(service, ArtworkCache())
    qtbot.addWidget(window)
    assert window.acquire.isChecked() is False
    export_button = window.pages["library"].findChild(QPushButton, "export_from_device")
    assert export_button is not None
    assert export_button.text() == "Выгрузить с устройства"
    table = window.pages["install"].findChild(QTableWidget, "install_table")
    assert table is not None
    assert table.wordWrap() is False
    assert (
        table.horizontalHeader().sectionResizeMode(2)
        == QHeaderView.ResizeMode.ResizeToContents
    )


def test_export_dialog_lists_installed_apps_and_skips_sideloads(qapp, qtbot) -> None:
    service = GuiService(demo_mode=True)
    window = MainWindow(service, ArtworkCache())
    qtbot.addWidget(window)
    dialog = ExportFromDeviceDialog(
        window,
        service=service,
        artwork=window.artwork,
        udid="DEMO-UDID-0001",
        signed_in=False,
    )
    qtbot.addWidget(dialog)
    dialog.show()
    qtbot.waitUntil(
        lambda: dialog.table.rowCount() == 3 and dialog._fill_at == 3,
        timeout=3000,
    )
    checkable = 0
    sideload_locked = False
    for row in range(dialog.table.rowCount()):
        item = dialog.table.item(row, 0)
        assert item is not None
        app = item.data(Qt.ItemDataRole.UserRole)
        if app.bundle_id == "com.example.sideload":
            sideload_locked = not bool(item.flags() & Qt.ItemFlag.ItemIsUserCheckable)
            assert dialog.table.item(row, 2).text() == "не из App Store"
        elif app.store_id:
            checkable += 1
            item.setCheckState(Qt.CheckState.Checked)
    assert sideload_locked
    assert checkable == 2
    assert dialog.go.isEnabled()
    assert "2" in dialog.go.text()
