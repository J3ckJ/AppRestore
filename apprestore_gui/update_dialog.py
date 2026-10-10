"""Settings > "Проверить обновления": dialog, download thread, swap launch."""

from __future__ import annotations

import shutil
import tempfile
from pathlib import Path
from typing import Any, Callable

from PySide6.QtCore import QObject, QThread, QUrl, Signal
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)

from apprestore_core.frozen import is_frozen
from apprestore_gui.errors import explain_update_error
from apprestore_gui import updater


class _DownloadWorker(QObject):
    progress = Signal(int, int)
    finished = Signal(object)
    failed = Signal(str)

    def __init__(self, info: updater.UpdateInfo, prepare: Callable[..., Any]) -> None:
        super().__init__()
        self._info = info
        self._prepare = prepare

    def run(self) -> None:
        try:
            staged = self._prepare(self._info, lambda done, total: self.progress.emit(done, total))
        except Exception as exc:  # noqa: BLE001 - shown in the dialog
            self.failed.emit(str(exc))
            return
        self.finished.emit(staged)


def prepare_update(info: updater.UpdateInfo, progress: updater.Progress | None = None) -> updater.StagedUpdate:
    """Download + verify + unpack next to the app. Nothing is replaced yet."""

    workdir = Path(tempfile.mkdtemp(prefix="AppRestore-download-"))
    try:
        archive = updater.download_update(info, workdir, progress=progress)
        return updater.stage_update(archive, info)
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


def _mb(value: int) -> str:
    return f"{value / (1024 * 1024):.1f} МБ"


class UpdateDialog(QDialog):
    def __init__(
        self,
        info: updater.UpdateInfo,
        parent: QWidget | None = None,
        *,
        frozen: bool | None = None,
        prepare: Callable[..., Any] = prepare_update,
        launch: Callable[[updater.StagedUpdate], Any] = updater.launch_swap,
        confirm: Callable[[QWidget, str], bool] | None = None,
        quit_app: Callable[[], None] | None = None,
    ) -> None:
        super().__init__(parent)
        self.info = info
        self._frozen = is_frozen() if frozen is None else frozen
        self._prepare = prepare
        self._launch = launch
        self._confirm = confirm or _ask_yes_no
        self._quit = quit_app or QApplication.quit
        self._thread: QThread | None = None
        self._worker: _DownloadWorker | None = None
        self.setWindowTitle("Обновление AppRestore")
        self.setMinimumSize(520, 420)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(22, 20, 22, 18)
        layout.setSpacing(10)
        title = QLabel(f"Доступна версия {info.latest}")
        title.setStyleSheet("font-size:18px;font-weight:600;border:none;")
        layout.addWidget(title)
        layout.addWidget(QLabel(f"Сейчас установлена {info.current}. Что нового:"))

        self.notes = QTextBrowser()
        self.notes.setOpenExternalLinks(True)
        self.notes.setMarkdown(info.notes or "Описание изменений не указано.")
        layout.addWidget(self.notes, 1)

        self.status = QLabel("")
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        self.progress = QProgressBar()
        self.progress.setVisible(False)
        layout.addWidget(self.progress)

        buttons = QHBoxLayout()
        self.page_button = QPushButton("Страница релиза")
        self.page_button.clicked.connect(self._open_page)
        buttons.addWidget(self.page_button)
        buttons.addStretch(1)
        self.later_button = QPushButton("Позже")
        self.later_button.clicked.connect(self.reject)
        buttons.addWidget(self.later_button)
        self.update_button = QPushButton("Обновить")
        self.update_button.setObjectName("primary")
        self.update_button.clicked.connect(self.start_update)
        buttons.addWidget(self.update_button)
        layout.addLayout(buttons)

        if not info.installable:
            self.update_button.setEnabled(False)
            self.status.setText(
                "Для вашей системы в этом релизе нет готовой программы с окном. "
                "Её можно скачать вручную со страницы релиза."
            )
        elif not self._frozen:
            self.update_button.setEnabled(False)
            self.status.setText(
                "Программа запущена из исходников, обновите её через git или pip. "
                "Обновление в один клик работает в собранной программе."
            )
        else:
            size = f" ({_mb(info.asset_size)})" if info.asset_size else ""
            self.status.setText(
                f"Будет скачан {info.asset_name}{size} и проверен по SHA-256. "
                "Без вашего нажатия ничего не устанавливается."
            )

    def _open_page(self) -> None:
        QDesktopServices.openUrl(QUrl(self.info.page_url))

    def start_update(self) -> None:
        if not (self.info.installable and self._frozen):
            return
        question = (
            f"Скачать и установить версию {self.info.latest}?\n\n"
            "Программа закроется и запустится заново. Если что-то пойдёт не так, "
            "останется текущая версия."
        )
        if not self._confirm(self, question):
            return
        self.update_button.setEnabled(False)
        self.later_button.setEnabled(False)
        self.progress.setVisible(True)
        self.progress.setRange(0, 0)
        self.status.setText("Скачиваю обновление…")

        thread = QThread(self)
        worker = _DownloadWorker(self.info, self._prepare)
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.progress.connect(self._on_progress)
        worker.finished.connect(self._on_ready)
        worker.failed.connect(self._on_failed)
        worker.finished.connect(thread.quit)
        worker.failed.connect(thread.quit)
        thread.finished.connect(worker.deleteLater)
        self._thread, self._worker = thread, worker
        thread.start()

    def _on_progress(self, done: int, total: int) -> None:
        if total > 0:
            self.progress.setRange(0, 1000)
            self.progress.setValue(min(1000, int(done * 1000 / total)))
            self.status.setText(f"Скачиваю обновление… {_mb(done)} из {_mb(total)}")
        else:
            self.status.setText(f"Скачиваю обновление… {_mb(done)}")

    def _on_failed(self, message: str) -> None:
        self.progress.setVisible(False)
        self.later_button.setEnabled(True)
        self.update_button.setEnabled(True)
        self.status.setText(
            explain_update_error(message) + " Текущая версия не тронута."
        )

    def _on_ready(self, staged: updater.StagedUpdate) -> None:
        self.progress.setRange(0, 1)
        self.progress.setValue(1)
        self.status.setText("Файл проверен. Перезапускаю программу…")
        try:
            self._launch(staged)
        except Exception as exc:  # noqa: BLE001
            shutil.rmtree(staged.staging_dir, ignore_errors=True)
            self._on_failed(str(exc))
            return
        self._finish_worker()
        self.accept()
        self._quit()

    def _finish_worker(self) -> None:
        """Let the download thread exit before Qt tears the process down.

        Quitting while this QThread is still running hides the window and
        then blocks in the thread destructor, so the old exe stays locked
        and the swap script cannot replace it.
        """

        thread = self._thread
        if thread is None:
            return
        thread.quit()
        if thread.wait(5000):
            return
        thread.terminate()
        thread.wait(1000)

    def wait_for_worker(self, timeout_ms: int = 10000) -> None:
        if self._thread is not None:
            self._thread.wait(timeout_ms)


def _ask_yes_no(parent: QWidget, text: str) -> bool:
    answer = QMessageBox.question(
        parent,
        "Обновление AppRestore",
        text,
        QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        QMessageBox.StandardButton.No,
    )
    return answer == QMessageBox.StandardButton.Yes
