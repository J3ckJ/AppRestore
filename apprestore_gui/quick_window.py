"""New AppRestore window on the Qt Quick scene graph.

Opened with ``--ui quick``. The packaged exe uses this window.
The list on the home screen is the phone's offloaded apps.
"""

from __future__ import annotations

import sys
from pathlib import Path

from PySide6.QtCore import QUrl
from PySide6.QtGui import QGuiApplication
from PySide6.QtQml import QQmlApplicationEngine
from PySide6.QtQuickControls2 import QQuickStyle

from apprestore_gui.icons_cache import IconBook
from apprestore_gui.popular_apps import popular_apps
from apprestore_gui.quick_session import QuickSession
from apprestore_gui.ui_icons import app_icon


def qml_path() -> Path:
    return Path(__file__).resolve().parent / "qml" / "AppRestore.qml"


def main() -> int:
    QQuickStyle.setStyle("Basic")
    app = QGuiApplication([sys.argv[0]])
    app.setApplicationName("AppRestore")
    app.setWindowIcon(app_icon())
    session = QuickSession()
    icon_book = IconBook()
    engine = QQmlApplicationEngine()
    engine.rootContext().setContextProperty("popularApps", popular_apps())
    engine.rootContext().setContextProperty("iconBook", icon_book)
    engine.rootContext().setContextProperty("session", session)

    def queue_icons() -> None:
        items: list[tuple[str, str, str]] = []
        for row in popular_apps():
            items.append((str(row.get("storeId") or ""), "", str(row.get("site") or "")))
        live = list(session.apps) + list(session.phoneApps) + list(session.libraryFiles)
        for row in live:
            if isinstance(row, dict):
                items.append(
                    (str(row.get("storeId") or ""), str(row.get("bundleId") or ""), "")
                )
        icon_book.consider_async(items)

    session.changed.connect(queue_icons)
    session.filesChanged.connect(queue_icons)
    queue_icons()
    engine.load(QUrl.fromLocalFile(str(qml_path())))
    if not engine.rootObjects():
        return 1
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
