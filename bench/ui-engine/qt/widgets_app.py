"""Qt Widgets candidate (current 0.3.x UI stack): empty window + core call."""

from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from bench_result import flush, mark  # noqa: E402

mark("process_start")

from PySide6.QtCore import QTimer  # noqa: E402
from PySide6.QtWidgets import QApplication, QLabel, QPushButton, QVBoxLayout, QWidget  # noqa: E402

from core_probe import core_call  # noqa: E402


class Window(QWidget):
    def __init__(self, app: QApplication) -> None:
        super().__init__()
        self._app = app
        self._seen = False
        self.setWindowTitle("AppRestore engine bench (Qt Widgets)")
        self.resize(960, 640)
        layout = QVBoxLayout(self)
        layout.addStretch(1)
        layout.addWidget(QLabel("AppRestore"))
        layout.addWidget(QPushButton("Find apps"))
        layout.addStretch(1)

    def paintEvent(self, event) -> None:  # noqa: N802
        super().paintEvent(event)
        if not self._seen:
            self._seen = True
            QTimer.singleShot(0, self._shown)

    def _shown(self) -> None:
        mark("window_shown")
        QTimer.singleShot(0, self._call_core)

    def _call_core(self) -> None:
        mark("core_answer", **core_call())
        flush()
        if os.environ.get("BENCH_OUT"):
            self._app.quit()


def main() -> int:
    app = QApplication(sys.argv[:1])
    window = Window(app)
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
