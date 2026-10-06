from __future__ import annotations

import os
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("pytestqt")

from PySide6.QtWidgets import QApplication

from apprestore_gui import updater
from apprestore_gui.update_dialog import UpdateDialog

DL = "https://github.com/J3ckJ/AppRestore/releases/download/v0.3.0/"


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


def _info(installable=True, latest="0.3.0"):
    return updater.UpdateInfo(
        current="0.2.4",
        latest=latest,
        tag="v" + latest,
        notes="## Что нового\n* Окно",
        page_url="https://github.com/J3ckJ/AppRestore/releases/tag/v" + latest,
        asset_name="AppRestore-GUI-Windows.zip" if installable else None,
        asset_url=DL + "AppRestore-GUI-Windows.zip" if installable else None,
        asset_size=5 * 1024 * 1024,
        checksums_url=DL + "SHA256SUMS.txt",
    )


def test_dialog_does_nothing_without_consent(qapp, qtbot):
    prepared = []
    dialog = UpdateDialog(_info(), frozen=True, prepare=lambda *a: prepared.append(a),
                          confirm=lambda *_: False, launch=lambda s: None, quit_app=lambda: None)
    qtbot.addWidget(dialog)
    assert "Окно" in dialog.notes.toPlainText()
    dialog.update_button.click()
    assert prepared == []
    assert dialog.update_button.isEnabled()


def test_dialog_downloads_then_launches_swap_and_quits(qapp, qtbot, tmp_path):
    staged = updater.StagedUpdate(info=None, staging_dir=tmp_path, new_app=tmp_path / "n", target_app=tmp_path / "t")
    events = []

    def prepare(info, progress):
        progress(1, 2)
        events.append("prepare")
        return staged

    dialog = UpdateDialog(_info(), frozen=True, prepare=prepare, confirm=lambda *_: True,
                          launch=lambda s: events.append(("launch", s)), quit_app=lambda: events.append("quit"))
    qtbot.addWidget(dialog)
    dialog.update_button.click()
    qtbot.waitUntil(lambda: "quit" in events, timeout=5000)
    assert events == ["prepare", ("launch", staged), "quit"]
    dialog.wait_for_worker()


def test_dialog_reports_failure_and_keeps_current_version(qapp, qtbot):
    def prepare(info, progress):
        raise updater.UpdateError("контрольная сумма не совпала")

    launched = []
    dialog = UpdateDialog(_info(), frozen=True, prepare=prepare, confirm=lambda *_: True,
                          launch=launched.append, quit_app=lambda: launched.append("quit"))
    qtbot.addWidget(dialog)
    dialog.update_button.click()
    qtbot.waitUntil(lambda: "Не получилось" in dialog.status.text(), timeout=5000)
    assert "Текущая версия не тронута" in dialog.status.text()
    assert launched == []
    assert dialog.update_button.isEnabled()
    dialog.wait_for_worker()


@pytest.mark.parametrize("installable,frozen", [(False, True), (True, False)])
def test_update_button_disabled_when_not_possible(qapp, qtbot, installable, frozen):
    dialog = UpdateDialog(_info(installable=installable), frozen=frozen)
    qtbot.addWidget(dialog)
    assert not dialog.update_button.isEnabled()
    assert dialog.status.text()


def test_no_long_dashes_in_update_strings():
    root = Path(__file__).resolve().parents[1] / "apprestore_gui"
    for name in ("update_dialog.py", "updater.py"):
        assert "\u2014" not in (root / name).read_text(encoding="utf-8")


def test_settings_button_checks_and_reports_latest(qapp, qtbot, monkeypatch):
    from apprestore_gui.icons_cache import ArtworkCache
    from apprestore_gui.main_window import MainWindow
    from apprestore_gui.service_adapter import GuiService

    monkeypatch.setattr(updater, "check_for_update", lambda *a, **k: _info(latest="0.2.4"))
    window = MainWindow(GuiService(demo_mode=True), ArtworkCache())
    qtbot.addWidget(window)
    window.update_check_button.click()
    qtbot.waitUntil(lambda: "последняя версия" in window.update_status.text(), timeout=5000)
    assert window.update_check_button.isEnabled()

    shown = []
    monkeypatch.setattr(updater, "check_for_update", lambda *a, **k: _info())
    monkeypatch.setattr(window, "show_update_dialog", shown.append)
    window.update_check_button.click()
    qtbot.waitUntil(lambda: bool(shown), timeout=5000)
    assert shown[0].latest == "0.3.0"
