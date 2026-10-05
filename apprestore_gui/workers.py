"""Background workers so the Qt UI stays responsive."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from PySide6.QtCore import QObject, QThread, Signal


class Worker(QObject):
    finished = Signal(object)
    failed = Signal(str)
    progress = Signal(str)

    def __init__(self, fn: Callable[..., Any], *args: Any, **kwargs: Any) -> None:
        super().__init__()
        self._fn = fn
        self._args = args
        self._kwargs = kwargs

    def run(self) -> None:
        try:
            result = self._fn(*self._args, **self._kwargs)
        except Exception as exc:  # noqa: BLE001 - surface to UI
            self.failed.emit(str(exc))
            return
        self.finished.emit(result)


def run_in_thread(
    parent: QObject,
    fn: Callable[..., Any],
    *args: Any,
    on_finished: Callable[[Any], None] | None = None,
    on_failed: Callable[[str], None] | None = None,
    on_progress: Callable[[str], None] | None = None,
    **kwargs: Any,
) -> tuple[QThread, Worker]:
    thread = QThread(parent)
    worker = Worker(fn, *args, **kwargs)
    worker.moveToThread(thread)
    thread.started.connect(worker.run)
    if on_finished:
        worker.finished.connect(on_finished)
    if on_failed:
        worker.failed.connect(on_failed)
    if on_progress:
        worker.progress.connect(on_progress)

    def _cleanup() -> None:
        thread.quit()
        thread.wait(5000)
        worker.deleteLater()
        thread.deleteLater()

    worker.finished.connect(lambda *_: _cleanup())
    worker.failed.connect(lambda *_: _cleanup())
    thread.start()
    return thread, worker
