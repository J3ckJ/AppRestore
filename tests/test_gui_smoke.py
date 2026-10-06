from __future__ import annotations

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("pytestqt")

from PySide6.QtWidgets import QApplication

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
    table = window.pages["install"].findChild(QTableWidget, "install_table")
    assert table is not None
    assert table.wordWrap() is False
    assert (
        table.horizontalHeader().sectionResizeMode(2)
        == QHeaderView.ResizeMode.ResizeToContents
    )
