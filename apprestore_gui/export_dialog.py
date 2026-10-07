"""Pick installed apps and save their App Store IPAs into the library."""

from __future__ import annotations

from PySide6.QtCore import Qt, QSize, QTimer, Signal
from PySide6.QtGui import QBrush, QColor, QPixmap
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from apprestore_core.models import InstalledApp
from apprestore_gui.icons_cache import ArtworkCache
from apprestore_gui.service_adapter import GuiService
from apprestore_gui.theme import ACCENT_SOFT, BAD, LINE, MUTED, OK, PANEL
from apprestore_gui.workers import run_in_thread


class _AppCell(QWidget):
    def __init__(self, pixmap: QPixmap, name: str, subtitle: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(4, 4, 8, 4)
        layout.setSpacing(10)
        icon = QLabel()
        icon.setFixedSize(36, 36)
        icon.setPixmap(pixmap)
        icon.setStyleSheet("border:none;background:transparent;")
        texts = QVBoxLayout()
        texts.setContentsMargins(0, 0, 0, 0)
        texts.setSpacing(1)
        title = QLabel(name)
        title.setStyleSheet("font-size:13px;font-weight:650;border:none;background:transparent;")
        sub = QLabel(subtitle)
        sub.setStyleSheet(
            f"color:{MUTED};font-size:11px;font-weight:400;border:none;background:transparent;"
        )
        texts.addWidget(title)
        texts.addWidget(sub)
        layout.addWidget(icon, 0, Qt.AlignmentFlag.AlignVCenter)
        layout.addLayout(texts, 1)


def _export_error(message: str) -> str:
    low = message.lower()
    if "not authenticated" in low or "passphrase is required" in low:
        return (
            "Сессия Apple ID закрыта. Откройте её в разделе Apple ID "
            "и повторите выгрузку."
        )
    line = message.splitlines()[-1].strip() if message else "не скачалось"
    return line[:240]


class ExportFromDeviceDialog(QDialog):
    progress = Signal(str)

    def __init__(
        self,
        parent: QWidget,
        *,
        service: GuiService,
        artwork: ArtworkCache,
        udid: str,
        signed_in: bool,
    ) -> None:
        super().__init__(parent)
        self.service = service
        self.artwork = artwork
        self._udid = udid
        self._signed_in = signed_in
        self._busy = False
        self._closed = False
        self._generation = 0
        self._rows: list[InstalledApp] = []
        self._fill_at = 0
        self.saved_any = 0

        self.setWindowTitle("Выгрузить с устройства")
        self.setModal(True)
        self.resize(760, 560)
        self.progress.connect(self._set_status)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 16, 18, 16)
        layout.setSpacing(10)
        lead = QLabel(
            "Список читается с iPhone. IPA скачивается из App Store на ваш Apple ID "
            "и сохраняется в библиотеку."
        )
        lead.setWordWrap(True)
        lead.setStyleSheet(f"color:{MUTED};font-weight:400;border:none;")
        layout.addWidget(lead)

        self.filter = QLineEdit()
        self.filter.setObjectName("export_filter")
        self.filter.setPlaceholderText("фильтр по имени или идентификатору")
        self.filter.textChanged.connect(self._apply_filter)
        layout.addWidget(self.filter)

        self.table = QTableWidget(0, 3)
        self.table.setObjectName("export_table")
        self.table.setHorizontalHeaderLabels(["", "Приложение", "Статус"])
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Fixed)
        self.table.setColumnWidth(0, 36)
        self.table.horizontalHeader().setSectionResizeMode(
            2, QHeaderView.ResizeMode.ResizeToContents
        )
        self.table.setWordWrap(False)
        self.table.setShowGrid(False)
        self.table.setAlternatingRowColors(False)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        self.table.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.table.verticalHeader().setVisible(False)
        self.table.setIconSize(QSize(36, 36))
        header = self.table.horizontalHeader()
        header.setDefaultAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        header.setHighlightSections(False)
        self.table.setStyleSheet(
            f"""
            QTableWidget {{
              background: {PANEL};
              border: 1px solid {LINE};
              border-radius: 8px;
              gridline-color: transparent;
            }}
            QTableWidget::item {{ padding: 4px; }}
            QHeaderView::section {{
              background: #F7F7F9;
              color: {MUTED};
              border: none;
              border-bottom: 1px solid {LINE};
              padding: 8px 12px;
              font-weight: 600;
            }}
            """
        )
        self.table.itemChanged.connect(lambda _item=None: self._on_checks_changed())
        layout.addWidget(self.table, 1)

        self.acquire = QCheckBox("Получить бесплатно в App Store на ваш Apple ID")
        self.acquire.setChecked(False)
        layout.addWidget(self.acquire)

        actions = QHBoxLayout()
        select_all = QPushButton("Выбрать все")
        select_all.clicked.connect(self._check_visible)
        self.status = QLabel("Читаем приложения с iPhone…")
        self.status.setObjectName("export_status")
        self.status.setStyleSheet(f"color:{MUTED};font-weight:400;border:none;")
        self.go = QPushButton("Скачать в библиотеку")
        self.go.setObjectName("primary")
        self.go.setEnabled(False)
        self.close_button = QPushButton("Закрыть")
        self.close_button.clicked.connect(self.reject)
        actions.addWidget(select_all)
        actions.addWidget(self.status, 1)
        actions.addWidget(self.close_button)
        actions.addWidget(self.go)
        layout.addLayout(actions)
        self.go.clicked.connect(self._start_download)
        QTimer.singleShot(0, self._load)

    def reject(self) -> None:  # noqa: D102
        if self._busy:
            return
        self._closed = True
        super().reject()

    def _set_status(self, text: str) -> None:
        self.status.setText(text)

    def _load(self) -> None:
        udid = self._udid

        def job() -> list[InstalledApp]:
            return self.service.installed_apps(udid)

        run_in_thread(
            self,
            job,
            on_finished=self._show_apps,
            on_failed=self._load_failed,
        )

    def _load_failed(self, message: str) -> None:
        if self._closed:
            return
        self.status.setText(_export_error(message))

    def _show_apps(self, apps: object) -> None:
        if self._closed:
            return
        rows = list(apps) if isinstance(apps, list) else []
        self._generation += 1
        generation = self._generation
        self._rows = [app for app in rows if isinstance(app, InstalledApp)]
        self._fill_at = 0
        self.table.blockSignals(True)
        self.table.setRowCount(0)
        self.table.setRowCount(len(self._rows))
        self.table.blockSignals(False)
        store_count = sum(1 for app in self._rows if app.store_id)
        self.status.setText(
            f"{len(self._rows)} на iPhone, из магазина {store_count}"
            if self._rows
            else "на iPhone нет установленных приложений"
        )
        self._fill_chunk(generation)

    def _fill_chunk(self, generation: int) -> None:
        if self._closed or generation != self._generation:
            return
        start = self._fill_at
        end = min(start + 40, len(self._rows))
        self.table.blockSignals(True)
        self.table.setUpdatesEnabled(False)
        for row in range(start, end):
            app = self._rows[row]
            check = QTableWidgetItem()
            if app.store_id:
                check.setFlags(
                    Qt.ItemFlag.ItemIsUserCheckable
                    | Qt.ItemFlag.ItemIsEnabled
                    | Qt.ItemFlag.ItemIsSelectable
                )
                check.setCheckState(Qt.CheckState.Unchecked)
            else:
                check.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
            check.setData(Qt.ItemDataRole.UserRole, app)
            check.setData(
                Qt.ItemDataRole.UserRole + 1,
                f"{app.name} {app.bundle_id}".casefold(),
            )
            self.table.setItem(row, 0, check)
            pix = self.artwork.cached_pixmap(
                bundle_id=app.bundle_id,
                store_id=app.store_id,
                name=app.name,
                size=36,
            )
            self.table.setCellWidget(row, 1, _AppCell(pix, app.name, f"версия {app.version}"))
            name_item = QTableWidgetItem("")
            name_item.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
            self.table.setItem(row, 1, name_item)
            if app.local_ipa is not None:
                status_text, color = "уже в библиотеке", OK
            elif app.store_id:
                status_text, color = "на iPhone", MUTED
            else:
                status_text, color = "не из App Store", MUTED
            status = QTableWidgetItem(status_text)
            status.setForeground(QColor(color))
            status.setTextAlignment(
                Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
            )
            self.table.setItem(row, 2, status)
            self.table.setRowHeight(row, 52)
        self.table.setUpdatesEnabled(True)
        self.table.blockSignals(False)
        self._fill_at = end
        self._tint()
        self._apply_filter()
        if end < len(self._rows):
            QTimer.singleShot(0, lambda gen=generation: self._fill_chunk(gen))
            return
        self._on_checks_changed()

    def _apply_filter(self) -> None:
        needle = self.filter.text().strip().casefold()
        for row in range(self.table.rowCount()):
            item = self.table.item(row, 0)
            blob = ""
            if item is not None:
                blob = str(item.data(Qt.ItemDataRole.UserRole + 1) or "")
            self.table.setRowHidden(row, bool(needle) and needle not in blob)

    def _check_visible(self) -> None:
        self.table.blockSignals(True)
        for row in range(self.table.rowCount()):
            if self.table.isRowHidden(row):
                continue
            item = self.table.item(row, 0)
            if item and item.flags() & Qt.ItemFlag.ItemIsUserCheckable:
                item.setCheckState(Qt.CheckState.Checked)
        self.table.blockSignals(False)
        self._on_checks_changed()

    def _selected(self) -> list[InstalledApp]:
        chosen: list[InstalledApp] = []
        for row in range(self.table.rowCount()):
            item = self.table.item(row, 0)
            if item is None or item.checkState() != Qt.CheckState.Checked:
                continue
            app = item.data(Qt.ItemDataRole.UserRole)
            if isinstance(app, InstalledApp) and app.store_id:
                chosen.append(app)
        return chosen

    def _on_checks_changed(self) -> None:
        self._tint()
        count = len(self._selected())
        self.go.setText(
            "Скачать в библиотеку" if count == 0 else f"Скачать в библиотеку · {count}"
        )
        self.go.setEnabled(count > 0 and not self._busy)

    def _tint(self) -> None:
        soft = QBrush(QColor(ACCENT_SOFT))
        clear = QBrush(QColor(PANEL))
        self.table.blockSignals(True)
        try:
            for row in range(self.table.rowCount()):
                item = self.table.item(row, 0)
                checked = bool(item and item.checkState() == Qt.CheckState.Checked)
                brush = soft if checked else clear
                for column in range(self.table.columnCount()):
                    cell = self.table.item(row, column)
                    if cell:
                        cell.setBackground(brush)
                widget = self.table.cellWidget(row, 1)
                if widget:
                    widget.setStyleSheet(
                        f"background:{ACCENT_SOFT if checked else 'transparent'};border:none;"
                    )
        finally:
            self.table.blockSignals(False)

    def _row_for(self, bundle_id: str) -> int | None:
        for row in range(self.table.rowCount()):
            item = self.table.item(row, 0)
            app = item.data(Qt.ItemDataRole.UserRole) if item else None
            if isinstance(app, InstalledApp) and app.bundle_id == bundle_id:
                return row
        return None

    def _mark(self, bundle_id: str, text: str, color: str) -> None:
        row = self._row_for(bundle_id)
        if row is None:
            return
        status = self.table.item(row, 2)
        if status is None:
            return
        status.setText(text)
        status.setForeground(QColor(color))

    def _start_download(self) -> None:
        if self._busy:
            return
        apps = self._selected()
        if not apps:
            return
        if not self.service.demo_mode and not self._signed_in:
            QMessageBox.information(
                self,
                "Нужен вход",
                "Откройте раздел Apple ID и войдите. IPA скачивается из App Store на этот аккаунт.",
            )
            return
        if len(apps) > 8:
            answer = QMessageBox.question(
                self,
                "Скачать несколько IPA",
                f"Скачать {len(apps)} приложений в библиотеку? "
                "Это может занять много времени и места.",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if answer != QMessageBox.StandardButton.Yes:
                return
        acquire = self.acquire.isChecked()
        self._busy = True
        self.go.setEnabled(False)
        self.acquire.setEnabled(False)
        self.filter.setEnabled(False)
        self.progress.emit(f"Скачиваем 1 из {len(apps)}…")

        def job() -> list[tuple[str, str, bool, str]]:
            outcome: list[tuple[str, str, bool, str]] = []
            for index, app in enumerate(apps, 1):
                self.progress.emit(f"{index} из {len(apps)} · {app.name}")
                try:
                    saved = self.service.download_to_library(
                        app,
                        acquire_license=acquire,
                    )
                except Exception as exc:  # noqa: BLE001
                    outcome.append((app.bundle_id, app.name, False, _export_error(str(exc))))
                else:
                    outcome.append((app.bundle_id, app.name, True, saved))
            return outcome

        run_in_thread(
            self,
            job,
            on_finished=self._download_finished,
            on_failed=self._download_failed,
        )

    def _download_failed(self, message: str) -> None:
        self._busy = False
        self.acquire.setEnabled(True)
        self.filter.setEnabled(True)
        self._on_checks_changed()
        if self._closed:
            return
        self.status.setText(_export_error(message))

    def _download_finished(self, outcome: object) -> None:
        self._busy = False
        self.acquire.setEnabled(True)
        self.filter.setEnabled(True)
        if self._closed:
            return
        rows = list(outcome) if isinstance(outcome, list) else []
        failed: list[str] = []
        saved = 0
        for item in rows:
            if not isinstance(item, tuple) or len(item) != 4:
                continue
            bundle_id, name, ok, detail = item
            if not isinstance(bundle_id, str) or not isinstance(name, str):
                continue
            if ok:
                saved += 1
                self._mark(bundle_id, "в библиотеке", OK)
            else:
                text = detail if isinstance(detail, str) else "не скачалось"
                failed.append(f"{name}: {text}")
                self._mark(bundle_id, "ошибка", BAD)
        self.saved_any += saved
        self._on_checks_changed()
        if failed:
            self.status.setText(f"в библиотеке {saved}, с ошибкой {len(failed)}")
            QMessageBox.warning(self, "Не все скачались", "\n\n".join(failed))
            return
        self.status.setText("Готово")
        if saved:
            self.accept()
