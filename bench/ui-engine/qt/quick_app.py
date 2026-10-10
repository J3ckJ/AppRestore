"""Qt Quick/QML candidate: empty window (Controls Basic, like PR #8) + core call."""

from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from bench_result import flush, mark  # noqa: E402

mark("process_start")

from PySide6.QtCore import QTimer, QUrl  # noqa: E402
from PySide6.QtGui import QGuiApplication  # noqa: E402
from PySide6.QtQml import QQmlApplicationEngine  # noqa: E402
from PySide6.QtQuickControls2 import QQuickStyle  # noqa: E402

from core_probe import core_call  # noqa: E402


def qml_file() -> Path:
    base = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
    return base / "bench.qml"


def main() -> int:
    QQuickStyle.setStyle("Basic")
    app = QGuiApplication(sys.argv[:1])
    engine = QQmlApplicationEngine()
    engine.load(QUrl.fromLocalFile(str(qml_file())))
    roots = engine.rootObjects()
    if not roots:
        return 1
    window = roots[0]
    state = {"seen": False}

    def first_frame() -> None:
        if state["seen"]:
            return
        state["seen"] = True
        mark("window_shown")
        QTimer.singleShot(0, call_core)

    def call_core() -> None:
        mark("core_answer", **core_call())
        flush()
        if os.environ.get("BENCH_OUT"):
            app.quit()

    window.frameSwapped.connect(first_frame)
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
