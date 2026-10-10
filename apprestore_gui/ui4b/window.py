"""Open the 4b window (default of ``--ui quick``; old one: ``--ui quick-legacy``)."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

from PySide6.QtCore import QObject, QSettings, QUrl
from PySide6.QtQml import QQmlApplicationEngine

QML_DIR = Path(__file__).resolve().parent.parent / "qml4b"
ONBOARDED_KEY = "ui4b/onboarded"


def qml_path() -> Path:
    return QML_DIR / "Main.qml"


def load(engine: QQmlApplicationEngine, controller: QObject, icon_book: QObject) -> bool:
    context = engine.rootContext()
    context.setContextProperty("ui", controller)
    context.setContextProperty("iconBook", icon_book)
    engine.load(QUrl.fromLocalFile(str(qml_path())))
    return bool(engine.rootObjects())


def build(settings: QSettings) -> tuple[QQmlApplicationEngine | None, list[Any]]:
    """The real window: QuickSession (pymobiledevice3, ipatool_api, purchases cache,
    license_gate/license_guard), IconBook (icons_cache), region_probe. No fakes here:
    FakeSource lives only in tests, ``selftest`` and ``scripts/ui4b_screenshots.py``."""

    from apprestore_gui.icons_cache import IconBook
    from apprestore_gui.quick_session import QuickSession
    from apprestore_gui.ui4b.qt_bridge import Restore4b, SessionSource
    from apprestore_gui.ui4b.settings import ARCHIVE_DEFAULT, ARCHIVE_KEY

    session = QuickSession()
    icon_book = IconBook()
    source = SessionSource(session)
    controller = Restore4b(
        source,
        onboarded=bool(settings.value(ONBOARDED_KEY, False, type=bool)),
        archive_search=bool(settings.value(ARCHIVE_KEY, ARCHIVE_DEFAULT, type=bool)),
    )

    def remember_onboarding() -> None:
        if controller.onboarding.finished and not settings.value(ONBOARDED_KEY, False, type=bool):
            settings.setValue(ONBOARDED_KEY, True)

    def queue_icons() -> None:
        items: list[tuple[str, str, str]] = []
        for item in controller.selection.items:
            items.append((item.store_id, item.bundle_id, ""))
        # phone tiles too: artwork from icons_cache, otherwise a Theme.iconPlaceholder square
        for app in source.phone_apps():
            items.append((app.store_id, app.bundle_id, ""))
        icon_book.consider_async(items)

    def open_doc(path: str) -> None:
        from PySide6.QtCore import QUrl
        from PySide6.QtGui import QDesktopServices

        QDesktopServices.openUrl(QUrl.fromLocalFile(path))

    def show_update(info: Any) -> None:
        # the old window's dialog (download, checksum, swap); needs QApplication
        from PySide6.QtWidgets import QApplication

        if QApplication.instance() is None or not isinstance(QApplication.instance(), QApplication):
            return
        from apprestore_gui.update_dialog import UpdateDialog

        dialog = UpdateDialog(info, None)
        keep.append(dialog)
        dialog.open()

    controller.changed.connect(remember_onboarding)
    controller.archiveSearchChanged.connect(lambda on: settings.setValue(ARCHIVE_KEY, bool(on)))
    controller.updateAvailable.connect(show_update)
    controller.openDocRequested.connect(open_doc)
    source.changed.connect(queue_icons)
    engine = QQmlApplicationEngine()
    keep: list[Any] = [session, source, controller, icon_book, engine]
    if not load(engine, controller, icon_book):
        return None, keep
    session.refresh()
    return engine, keep


def main() -> int:
    from PySide6.QtQuickControls2 import QQuickStyle
    from PySide6.QtWidgets import QApplication

    from apprestore_gui.ui_icons import app_icon

    QQuickStyle.setStyle("Basic")
    # QApplication (not QGuiApplication): «Проверить обновления» reuses the old UpdateDialog
    app = QApplication([sys.argv[0]])
    app.setApplicationName("AppRestore")
    app.setWindowIcon(app_icon())
    engine, keep = build(QSettings("AppRestore", "AppRestore"))
    if engine is None:
        return 1
    app.exec()
    del keep
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
