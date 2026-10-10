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


def main() -> int:
    from PySide6.QtGui import QGuiApplication
    from PySide6.QtQuickControls2 import QQuickStyle

    from apprestore_gui.icons_cache import IconBook
    from apprestore_gui.quick_session import QuickSession
    from apprestore_gui.ui4b.qt_bridge import Restore4b, SessionSource
    from apprestore_gui.ui_icons import app_icon

    QQuickStyle.setStyle("Basic")
    app = QGuiApplication([sys.argv[0]])
    app.setApplicationName("AppRestore")
    app.setWindowIcon(app_icon())
    settings = QSettings("AppRestore", "AppRestore")
    session = QuickSession()
    icon_book = IconBook()
    source = SessionSource(session)
    controller = Restore4b(source, onboarded=bool(settings.value(ONBOARDED_KEY, False, type=bool)))

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

    controller.changed.connect(remember_onboarding)
    source.changed.connect(queue_icons)
    engine = QQmlApplicationEngine()
    keep: list[Any] = [session, source, controller, icon_book]
    if not load(engine, controller, icon_book):
        return 1
    session.refresh()
    app.exec()
    del keep
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
