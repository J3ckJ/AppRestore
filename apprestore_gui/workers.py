"""Background workers so the Qt UI stays responsive."""

from __future__ import annotations

import threading
import traceback
from collections.abc import Callable
from typing import Any

from PySide6.QtCore import QObject, Signal


class _Bridge(QObject):
    """Lives on the GUI thread. Worker threads only emit its signals."""

    finished = Signal(object)
    failed = Signal(str)
    progress = Signal(str)

    def __init__(
        self,
        parent: QObject,
        on_finished: Callable[[Any], None] | None,
        on_failed: Callable[[str], None] | None,
        on_progress: Callable[[str], None] | None,
    ) -> None:
        super().__init__(parent)
        if on_finished is not None:
            self.finished.connect(on_finished)
        if on_failed is not None:
            self.failed.connect(on_failed)
        if on_progress is not None:
            self.progress.connect(on_progress)
        self.finished.connect(self._release)
        self.failed.connect(self._release)

    def _release(self, _value: object = None) -> None:
        self.deleteLater()


def run_in_thread(
    parent: QObject,
    fn: Callable[..., Any],
    *args: Any,
    on_finished: Callable[[Any], None] | None = None,
    on_failed: Callable[[str], None] | None = None,
    on_progress: Callable[[str], None] | None = None,
    **kwargs: Any,
) -> _Bridge:
    bridge = _Bridge(parent, on_finished, on_failed, on_progress)

    def _run() -> None:
        try:
            result = fn(*args, **kwargs)
        except Exception as exc:
            traceback.print_exc()
            bridge.failed.emit(str(exc))
            return
        bridge.finished.emit(result)

    threading.Thread(target=_run, name="apprestore-worker", daemon=True).start()
    return bridge
