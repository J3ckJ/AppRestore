from __future__ import annotations

from pathlib import Path
from typing import Any

from PySide6.QtCore import Qt, QSize, Signal, QEvent
from PySide6.QtGui import QColor, QBrush, QFont, QIcon, QPixmap
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QSizePolicy,
    QStackedWidget,
    QTableWidget,
    QTableWidgetItem,
    QTextEdit,
    QVBoxLayout,
    QWidget,
    QFileDialog,
    QApplication,
)

from apprestore_core.models import MissingApp, OffloadedApp
from apprestore_gui import demo
from apprestore_gui.auth_pty import login_with_prompts
from apprestore_gui.icons_cache import ArtworkCache
from apprestore_gui.service_adapter import GuiService
from apprestore_gui.theme import (
    ACCENT,
    ACCENT_SOFT,
    BAD,
    LINE,
    MUTED,
    OK,
    PANEL,
    SIDE,
    SIDE_2,
    SIDE_MUTED,
    SIDE_TEXT,
    WARN,
)
from apprestore_gui.ui_icons import (
    NAV_ICON_NAMES,
    TILE_ICON_NAMES,
    logo_mark_pixmap,
    svg_icon,
    svg_pixmap,
)
from apprestore_gui.widgets.phone import PhoneWidget
from apprestore_gui.workers import run_in_thread


NAV = [
    ("overview", "Обзор"),
    ("offloaded", "Сгруженные"),
    ("install", "Найти и поставить"),
    ("library", "Библиотека IPA"),
    ("doctor", "Проверки"),
    ("account", "Apple ID"),
    ("log", "Операции"),
    ("settings", "Настройки"),
]


def _muted_label(text: str, size: int = 11) -> QLabel:
    label = QLabel(text)
    label.setStyleSheet(f"color:{MUTED};font-size:{size}px;font-weight:500;border:none;background:transparent;")
    return label


def _value_label(text: str = "-", *, object_name: str | None = None) -> QLabel:
    label = QLabel(text)
    if object_name:
        label.setObjectName(object_name)
    label.setStyleSheet("font-size:13px;font-weight:650;border:none;background:transparent;")
    return label


class AppCell(QWidget):
    """Icon + bold name + muted subtitle (version / bundle)."""

    def __init__(self, pixmap: QPixmap, name: str, subtitle: str, parent=None) -> None:
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
        sub.setStyleSheet(f"color:{MUTED};font-size:11px;font-weight:400;border:none;background:transparent;")
        texts.addWidget(title)
        texts.addWidget(sub)
        layout.addWidget(icon, 0, Qt.AlignmentFlag.AlignVCenter)
        layout.addLayout(texts, 1)


class ActionTile(QFrame):
    clicked = Signal()

    def __init__(self, title: str, subtitle: str, icon_name: str, parent=None) -> None:
        super().__init__(parent)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setMinimumHeight(72)
        self.setStyleSheet(
            f"""
            QFrame {{
              background: {PANEL};
              border: 1px solid {LINE};
              border-radius: 8px;
            }}
            QFrame:hover {{
              border-color: #C4C4C8;
              background: #FAFAFB;
            }}
            QLabel {{ border: none; background: transparent; }}
            """
        )
        row = QHBoxLayout(self)
        row.setContentsMargins(12, 10, 12, 10)
        row.setSpacing(12)
        icon = QLabel()
        icon.setFixedSize(44, 44)
        icon.setPixmap(svg_pixmap(icon_name, 44))
        texts = QVBoxLayout()
        texts.setContentsMargins(0, 0, 0, 0)
        texts.setSpacing(2)
        t = QLabel(title)
        t.setStyleSheet("font-size:13px;font-weight:650;")
        s = QLabel(subtitle)
        s.setWordWrap(True)
        s.setStyleSheet(f"color:{MUTED};font-size:11.5px;font-weight:400;line-height:1.35;")
        texts.addWidget(t)
        texts.addWidget(s)
        row.addWidget(icon, 0, Qt.AlignmentFlag.AlignVCenter)
        row.addLayout(texts, 1)

    def mousePressEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit()
        super().mousePressEvent(event)


class Sidebar(QFrame):
    navigated = Signal(str)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("sidebar")
        self.setFixedWidth(240)
        self.setStyleSheet(
            f"""
            QFrame#sidebar {{ background: {SIDE}; border: none; }}
            QFrame#sidebar QLabel {{ color: {SIDE_TEXT}; border: none; }}
            QListWidget {{
              background: transparent; border: none; color: {SIDE_TEXT};
              outline: none; padding: 0 2px;
            }}
            QListWidget::item {{
              padding: 7px 10px; border-radius: 6px; margin: 1px 4px;
              color: {SIDE_TEXT};
            }}
            QListWidget::item:selected {{
              background: #3A3A3E; color: white;
            }}
            """
        )
        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 14, 10, 10)
        layout.setSpacing(8)

        brand = QHBoxLayout()
        brand.setSpacing(10)
        mark = QLabel()
        mark.setFixedSize(28, 28)
        mark.setPixmap(logo_mark_pixmap(28))
        mark.setStyleSheet("border:none;background:transparent;")
        titles = QVBoxLayout()
        titles.setContentsMargins(0, 0, 0, 0)
        titles.setSpacing(0)
        name = QLabel("AppRestore")
        name.setStyleSheet("color:#FFFFFF;font-size:13px;font-weight:700;border:none;")
        tag = QLabel("возврат приложений")
        tag.setStyleSheet(f"color:{SIDE_MUTED};font-size:11px;font-weight:400;border:none;")
        titles.addWidget(name)
        titles.addWidget(tag)
        brand.addWidget(mark, 0, Qt.AlignmentFlag.AlignVCenter)
        brand.addLayout(titles, 1)
        layout.addLayout(brand)

        device_wrap = QFrame()
        device_wrap.setObjectName("deviceCard")
        device_wrap.setStyleSheet(
            f"QFrame#deviceCard{{background:{SIDE_2};border-radius:8px;border:none;}}"
            f"QFrame#deviceCard QLabel{{border:none;background:transparent;}}"
        )
        dw = QHBoxLayout(device_wrap)
        dw.setContentsMargins(10, 10, 10, 10)
        dw.setSpacing(8)
        phone_icon = QLabel()
        phone_icon.setFixedSize(18, 28)
        phone_icon.setPixmap(svg_pixmap("smartphone", 18, color="#D1D1D6"))
        phone_icon.setAlignment(Qt.AlignmentFlag.AlignTop)
        self.device = QLabel("Устройство не выбрано")
        self.device.setWordWrap(True)
        self.device.setStyleSheet(f"color:{SIDE_TEXT};")
        dw.addWidget(phone_icon, 0, Qt.AlignmentFlag.AlignTop)
        dw.addWidget(self.device, 1)
        layout.addWidget(device_wrap)

        section = QLabel("РАЗДЕЛЫ")
        section.setStyleSheet(
            f"color:{SIDE_MUTED};font-size:10px;font-weight:700;letter-spacing:1px;padding:6px 6px 2px;border:none;"
        )
        layout.addWidget(section)

        self.nav = QListWidget()
        self.nav.setIconSize(QSize(16, 16))
        self.nav.setSpacing(1)
        for key, label in NAV:
            item = QListWidgetItem(label)
            item.setData(Qt.ItemDataRole.UserRole, key)
            icon_name = NAV_ICON_NAMES.get(key)
            if icon_name:
                item.setIcon(svg_icon(icon_name, 16, color="#D1D1D6"))
            self.nav.addItem(item)
        self.nav.setCurrentRow(0)
        self.nav.currentItemChanged.connect(self._on_nav)
        layout.addWidget(self.nav, 1)

        foot = QLabel("Данные остаются на этом компьютере")
        foot.setStyleSheet(f"color:{SIDE_MUTED};font-size:11px;border:none;")
        foot.setWordWrap(True)
        layout.addWidget(foot)

    def _on_nav(self, current: QListWidgetItem | None, _prev: QListWidgetItem | None) -> None:
        if current:
            self.navigated.emit(str(current.data(Qt.ItemDataRole.UserRole)))

    def set_device_text(self, text: str) -> None:
        self.device.setText(text)

    def select(self, key: str) -> None:
        for i in range(self.nav.count()):
            item = self.nav.item(i)
            if item and item.data(Qt.ItemDataRole.UserRole) == key:
                self.nav.setCurrentRow(i)
                break


class MainWindow(QMainWindow):
    def __init__(self, service: GuiService, artwork: ArtworkCache) -> None:
        super().__init__()
        self.service = service
        self.artwork = artwork
        self.setWindowTitle("AppRestore")
        self.resize(1200, 760)
        self._device_udid: str | None = None
        self._log_lines: list[str] = []
        self._phone: PhoneWidget | None = None
        self._phone_cap_title: QLabel | None = None
        self._phone_cap_sub: QLabel | None = None

        root = QWidget()
        self.setCentralWidget(root)
        outer = QHBoxLayout(root)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        self.sidebar = Sidebar()
        self.sidebar.navigated.connect(self._show_page)
        outer.addWidget(self.sidebar)

        self.stack = QStackedWidget()
        outer.addWidget(self.stack, 1)

        self.pages: dict[str, QWidget] = {}
        self._build_pages()
        for key, _ in NAV:
            self.stack.addWidget(self.pages[key])

        self.refresh_device()
        self._show_page("overview")

    def log(self, message: str) -> None:
        self._log_lines.append(message)
        if "log" in self.pages:
            view = self.pages["log"].findChild(QTextEdit, "log")
            if view:
                view.append(message)

    def _page_shell(self, title: str) -> tuple[QWidget, QVBoxLayout, QLabel]:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(16, 12, 16, 12)
        layout.setSpacing(10)
        head = QHBoxLayout()
        label = QLabel(title)
        label.setStyleSheet("font-size:14px;font-weight:650;border:none;background:transparent;")
        meta = QLabel("")
        meta.setStyleSheet(f"color:{MUTED};font-size:12px;font-weight:500;border:none;background:transparent;")
        meta.setObjectName("meta")
        head.addWidget(label)
        head.addStretch(1)
        head.addWidget(meta)
        layout.addLayout(head)
        return page, layout, meta

    def _build_pages(self) -> None:
        self.pages["overview"] = self._build_overview()
        self.pages["offloaded"] = self._build_offloaded()
        self.pages["install"] = self._build_install()
        self.pages["library"] = self._build_library()
        self.pages["doctor"] = self._build_doctor()
        self.pages["account"] = self._build_account()
        self.pages["log"] = self._build_log()
        self.pages["settings"] = self._build_settings()

    def _show_page(self, key: str) -> None:
        keys = [k for k, _ in NAV]
        if key in keys:
            self.stack.setCurrentIndex(keys.index(key))
            self.sidebar.select(key)
            if key == "offloaded":
                self.reload_offloaded()
            elif key == "install":
                self.reload_missing()
            elif key == "library":
                self.reload_library()
            elif key == "doctor":
                self.reload_doctor()
            elif key == "overview":
                self.refresh_overview_meta()

    def refresh_device(self) -> None:
        devices = self.service.devices()
        if not devices:
            self._device_udid = None
            self.sidebar.set_device_text("Нет подключённого iPhone<br>Подключите по USB")
            self._update_phone_home([])
            return
        device = devices[0]
        self._device_udid = device.udid
        auth = "вход есть" if self.service.authenticated() else "нужен вход"
        auth_color = OK if self.service.authenticated() else "#A1A1A6"
        self.sidebar.set_device_text(
            f"<b>{device.name}</b><br>"
            f"<span style='color:#A1A1A6'>iOS {device.ios_version} · USB</span><br>"
            f"<span style='color:#30D158'>● подключён</span><br>"
            f"<span style='color:{auth_color}'>{auth}</span>"
        )
        self._refresh_phone_from_device()

    def _refresh_phone_from_device(self) -> None:
        if self.service.demo_mode:
            apps = demo.DEMO_HOME
            icons: list[tuple[QPixmap, bool]] = []
            for app in apps[:16]:
                pix = self.artwork.pixmap_for_app(
                    bundle_id=app.bundle_id,
                    store_id=app.store_id,
                    name=app.name,
                    size=64,
                )
                icons.append((pix, app.offloaded))
            offloaded_n = sum(1 for a in apps if a.offloaded)
            self._update_phone_home(icons, device_name="iPhone 14", offloaded_n=offloaded_n)
            return
        if not self._device_udid:
            self._update_phone_home([])
            return
        # Live: show offloaded + fill from missing if needed.
        off = self.service.offloaded(self._device_udid)
        icons = []
        for app in off[:16]:
            pix = self.artwork.pixmap_for_app(
                bundle_id=app.bundle_id, store_id=app.store_id, name=app.name, size=64
            )
            icons.append((pix, True))
        devices = self.service.devices()
        name = devices[0].name if devices else "iPhone"
        self._update_phone_home(icons, device_name=name, offloaded_n=len(off))

    def _update_phone_home(
        self,
        icons: list[tuple[QPixmap, bool]],
        *,
        device_name: str = "iPhone",
        offloaded_n: int = 0,
    ) -> None:
        if self._phone:
            self._phone.set_icons(icons)
        if self._phone_cap_title:
            self._phone_cap_title.setText(device_name)
        if self._phone_cap_sub:
            if offloaded_n:
                self._phone_cap_sub.setText(f"{offloaded_n} сгруженных на экране")
            elif icons:
                self._phone_cap_sub.setText("приложения на экране")
            else:
                self._phone_cap_sub.setText("нет данных экрана")

    def refresh_overview_meta(self) -> None:
        page = self.pages["overview"]
        meta = page.findChild(QLabel, "meta")
        if meta:
            meta.setText("готово к работе" if self._device_udid else "нет устройства")
            meta.setStyleSheet(
                f"color:{OK if self._device_udid else WARN};font-size:12px;font-weight:600;border:none;background:transparent;"
            )
        model = page.findChild(QLabel, "info_model")
        system = page.findChild(QLabel, "info_system")
        apple = page.findChild(QLabel, "info_apple")
        devices = self.service.devices()
        if devices:
            d = devices[0]
            if model:
                model.setText(d.name)
            if system:
                system.setText(f"iOS {d.ios_version}")
        if apple:
            if self.service.authenticated():
                apple.setText("сессия есть")
                apple.setStyleSheet(f"color:{OK};font-size:13px;font-weight:650;border:none;background:transparent;")
            else:
                apple.setText("нужен вход")
                apple.setStyleSheet(f"color:{WARN};font-size:13px;font-weight:650;border:none;background:transparent;")
        self._refresh_phone_from_device()

    def _info_card(self, title: str, value_name: str) -> QFrame:
        box = QFrame()
        box.setObjectName("infoCard")
        box.setStyleSheet(
            f"""
            QFrame#infoCard {{
              background: {PANEL};
              border: 1px solid {LINE};
              border-radius: 8px;
            }}
            QFrame#infoCard QLabel {{
              border: none;
              background: transparent;
            }}
            """
        )
        bl = QVBoxLayout(box)
        bl.setContentsMargins(12, 10, 12, 10)
        bl.setSpacing(3)
        bl.addWidget(_muted_label(title, 11))
        bl.addWidget(_value_label("-", object_name=value_name))
        return box

    def _build_overview(self) -> QWidget:
        page, layout, meta = self._page_shell("Обзор устройства")
        meta.setText("готово к работе")
        body = QHBoxLayout()
        body.setSpacing(16)
        left = QVBoxLayout()
        left.setSpacing(8)
        self._phone = PhoneWidget()
        left.addWidget(self._phone, 0, Qt.AlignmentFlag.AlignHCenter)
        self._phone_cap_title = QLabel("iPhone")
        self._phone_cap_title.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        self._phone_cap_title.setStyleSheet("font-size:13px;font-weight:700;border:none;background:transparent;")
        self._phone_cap_sub = QLabel("")
        self._phone_cap_sub.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        self._phone_cap_sub.setStyleSheet(f"color:{MUTED};font-size:11.5px;font-weight:400;border:none;background:transparent;")
        left.addWidget(self._phone_cap_title)
        left.addWidget(self._phone_cap_sub)
        left.addStretch(1)
        body.addLayout(left, 0)

        right = QVBoxLayout()
        right.setSpacing(10)
        info = QHBoxLayout()
        info.setSpacing(8)
        for key, title in (
            ("info_model", "Модель"),
            ("info_system", "Система"),
            ("info_apple", "Apple ID"),
        ):
            info.addWidget(self._info_card(title, key))
        right.addLayout(info)

        grid = QVBoxLayout()
        grid.setSpacing(8)
        tiles = [
            ("Вернуть сгруженные", "Ярлыки на месте, приложения выгружены", "offloaded"),
            ("Найти и поставить", "По имени, ссылке или из истории", "install"),
            ("Библиотека IPA", "Файлы уже на компьютере", "library"),
            ("Войти в Apple ID", "Нужно для загрузки из магазина", "account"),
            ("Проверки", "Связь с телефоном и готовность", "doctor"),
            ("Операции", "Журнал последних действий", "log"),
        ]
        # v5 CSS: two columns, three rows
        for i in range(0, len(tiles), 2):
            row = QHBoxLayout()
            row.setSpacing(8)
            for title, sub, target in tiles[i : i + 2]:
                tile = ActionTile(title, sub, TILE_ICON_NAMES[target])
                tile.clicked.connect(lambda t=target: self._show_page(t))
                row.addWidget(tile)
            grid.addLayout(row)
        right.addLayout(grid)
        right.addStretch(1)
        body.addLayout(right, 1)
        layout.addLayout(body, 1)
        return page

    def _prepare_table(self, table: QTableWidget) -> None:
        table.setShowGrid(False)
        table.setAlternatingRowColors(False)
        table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        table.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        table.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        table.verticalHeader().setVisible(False)
        table.setIconSize(QSize(36, 36))
        header = table.horizontalHeader()
        header.setDefaultAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        header.setHighlightSections(False)
        table.setStyleSheet(
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
              text-align: left;
            }}
            """
        )

    def _tint_checked_rows(self, table: QTableWidget) -> None:
        soft = QBrush(QColor(ACCENT_SOFT))
        clear = QBrush(QColor(PANEL))
        for r in range(table.rowCount()):
            item = table.item(r, 0)
            checked = bool(item and item.checkState() == Qt.CheckState.Checked)
            brush = soft if checked else clear
            for c in range(table.columnCount()):
                cell = table.item(r, c)
                if cell:
                    cell.setBackground(brush)
            widget = table.cellWidget(r, 1)
            if widget:
                widget.setStyleSheet(
                    f"background:{ACCENT_SOFT if checked else 'transparent'};border:none;"
                )

    def _fill_app_table(
        self,
        table: QTableWidget,
        rows: list[tuple[Any, str, str, str | None, str | None]],
        *,
        status_text: str = "сгружено",
        status_color: str = WARN,
        precheck: int = 0,
    ) -> None:
        table.blockSignals(True)
        table.setRowCount(0)
        table.setRowCount(len(rows))
        for r, (obj, name, version, bundle_id, store_id) in enumerate(rows):
            check = QTableWidgetItem()
            check.setFlags(
                Qt.ItemFlag.ItemIsUserCheckable
                | Qt.ItemFlag.ItemIsEnabled
                | Qt.ItemFlag.ItemIsSelectable
            )
            check.setCheckState(
                Qt.CheckState.Checked if r < precheck else Qt.CheckState.Unchecked
            )
            check.setData(Qt.ItemDataRole.UserRole, obj)
            table.setItem(r, 0, check)
            pix = self.artwork.pixmap_for_app(
                bundle_id=bundle_id, store_id=store_id, name=name, size=36
            )
            cell = AppCell(pix, name, version)
            table.setCellWidget(r, 1, cell)
            # placeholder item so background tint works
            name_item = QTableWidgetItem("")
            name_item.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
            table.setItem(r, 1, name_item)
            status = QTableWidgetItem(status_text)
            status.setForeground(QColor(status_color))
            status.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            table.setItem(r, 2, status)
            table.setRowHeight(r, 52)
        table.blockSignals(False)
        self._tint_checked_rows(table)

    def _build_offloaded(self) -> QWidget:
        page, layout, meta = self._page_shell("Сгруженные")
        lead = QLabel(
            "Сначала пробуем докачку самого iPhone. Если не выйдет, загрузим через ваш Apple ID."
        )
        lead.setStyleSheet(f"color:{MUTED};font-weight:400;border:none;background:transparent;")
        lead.setWordWrap(True)
        layout.addWidget(lead)
        table = QTableWidget(0, 3)
        table.setObjectName("offloaded_table")
        table.setHorizontalHeaderLabels(["", "Приложение", "Статус"])
        table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Fixed)
        table.setColumnWidth(0, 36)
        table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        self._prepare_table(table)
        table.itemChanged.connect(lambda _=None: self._on_offloaded_changed())
        layout.addWidget(table, 1)
        actions = QHBoxLayout()
        select_all = QPushButton("Выбрать все")
        select_all.clicked.connect(lambda: self._check_all(table, True))
        restore = QPushButton("Восстановить")
        restore.setObjectName("primary")
        restore.clicked.connect(self._restore_selected_offloaded)
        hint = QLabel("Экран телефона лучше оставить разблокированным")
        hint.setStyleSheet(f"color:{MUTED};font-size:11.5px;font-weight:400;border:none;")
        actions.addWidget(select_all)
        actions.addStretch(1)
        actions.addWidget(hint)
        actions.addWidget(restore)
        layout.addLayout(actions)
        self._progress = QLabel("")
        self._progress.setStyleSheet(f"color:{MUTED};border:none;")
        layout.addWidget(self._progress)
        return page

    def _on_offloaded_changed(self) -> None:
        table = self.pages["offloaded"].findChild(QTableWidget, "offloaded_table")
        meta = self.pages["offloaded"].findChild(QLabel, "meta")
        if not table:
            return
        self._tint_checked_rows(table)
        total = table.rowCount()
        selected = sum(
            1
            for r in range(total)
            if table.item(r, 0) and table.item(r, 0).checkState() == Qt.CheckState.Checked
        )
        if meta and total:
            meta.setText(f"выбрано {selected} из {total}")

    def reload_offloaded(self) -> None:
        table = self.pages["offloaded"].findChild(QTableWidget, "offloaded_table")
        meta = self.pages["offloaded"].findChild(QLabel, "meta")
        if not table:
            return
        if not self._device_udid:
            table.setRowCount(0)
            if meta:
                meta.setText("нет устройства")
            return
        apps = self.service.offloaded(self._device_udid)
        rows = [
            (a, a.name, f"версия {a.version}", a.bundle_id, a.store_id) for a in apps
        ]
        precheck = 3 if self.service.demo_mode and len(rows) >= 3 else 0
        self._fill_app_table(
            table,
            rows,
            status_text="сгружено",
            status_color=WARN,
            precheck=precheck,
        )
        if meta:
            if precheck:
                meta.setText(f"выбрано {precheck} из {len(apps)}")
            else:
                meta.setText(f"{len(apps)} на устройстве")

    def _check_all(self, table: QTableWidget, checked: bool) -> None:
        state = Qt.CheckState.Checked if checked else Qt.CheckState.Unchecked
        table.blockSignals(True)
        for r in range(table.rowCount()):
            item = table.item(r, 0)
            if item:
                item.setCheckState(state)
        table.blockSignals(False)
        self._tint_checked_rows(table)
        if table.objectName() == "offloaded_table":
            self._on_offloaded_changed()

    def _selected_apps(self, table: QTableWidget) -> list[Any]:
        out = []
        for r in range(table.rowCount()):
            item = table.item(r, 0)
            if item and item.checkState() == Qt.CheckState.Checked:
                out.append(item.data(Qt.ItemDataRole.UserRole))
        return out

    def _restore_selected_offloaded(self) -> None:
        table = self.pages["offloaded"].findChild(QTableWidget, "offloaded_table")
        if not table or not self._device_udid:
            return
        apps = self._selected_apps(table)
        if not apps:
            QMessageBox.information(self, "AppRestore", "Выберите хотя бы одно приложение.")
            return
        self._progress.setText("Восстановление…")
        udid = self._device_udid

        def job() -> list[str]:
            results = []
            for app in apps:
                self.log(f"restore {app.name}")
                status = self.service.restore_offloaded(udid, app)
                results.append(status)
            return results

        def done(results: list[str]) -> None:
            self._progress.setText("Готово")
            self.log("ok  " + "; ".join(results))
            QMessageBox.information(
                self,
                "Готово",
                "Выбранные приложения обработаны. Посмотрите домашний экран iPhone.",
            )
            self.reload_offloaded()

        def fail(msg: str) -> None:
            self._progress.setText("")
            self.log(f"err  {msg}")
            QMessageBox.warning(self, "Ошибка", msg)

        run_in_thread(self, job, on_finished=done, on_failed=fail)

    def _build_install(self) -> QWidget:
        page, layout, meta = self._page_shell("Найти и поставить")
        bar = QHBoxLayout()
        search = QLineEdit()
        search.setObjectName("install_search")
        search.setPlaceholderText("имя, ссылка или номер в магазине")
        find = QPushButton("Искать")
        find.setObjectName("primary")
        find.clicked.connect(self._run_search)
        bar.addWidget(search, 1)
        bar.addWidget(find)
        layout.addLayout(bar)
        note = QLabel(
            "Если приложения ещё не было на вашем Apple ID. Для бесплатного можно получить "
            "его в магазине на этот аккаунт. Платное без оплаты не ставится."
        )
        note.setWordWrap(True)
        note.setStyleSheet(f"color:{MUTED};font-size:11.5px;font-weight:400;border:none;")
        layout.addWidget(note)
        self.acquire = QCheckBox("Получить бесплатно в App Store на ваш Apple ID")
        self.acquire.setChecked(True)
        layout.addWidget(self.acquire)
        table = QTableWidget(0, 3)
        table.setObjectName("install_table")
        table.setHorizontalHeaderLabels(["", "Приложение", "Статус"])
        table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Fixed)
        table.setColumnWidth(0, 36)
        self._prepare_table(table)
        table.itemChanged.connect(lambda _=None: self._tint_checked_rows(table))
        layout.addWidget(table, 1)
        actions = QHBoxLayout()
        go = QPushButton("Скачать и установить")
        go.setObjectName("primary")
        go.clicked.connect(self._install_selected_missing)
        actions.addStretch(1)
        actions.addWidget(go)
        layout.addLayout(actions)
        return page

    def reload_missing(self) -> None:
        table = self.pages["install"].findChild(QTableWidget, "install_table")
        meta = self.pages["install"].findChild(QLabel, "meta")
        if not table or not self._device_udid:
            return
        apps = self.service.missing(self._device_udid)
        rows = [
            (a, a.name, a.bundle_id, a.bundle_id, a.store_id) for a in apps
        ]
        self._fill_app_table(
            table,
            rows,
            status_text="нет на телефоне",
            status_color=BAD,
            precheck=0,
        )
        if meta:
            meta.setText("нет на телефоне")

    def _run_search(self) -> None:
        search = self.pages["install"].findChild(QLineEdit, "install_search")
        table = self.pages["install"].findChild(QTableWidget, "install_table")
        if not search or not table:
            return
        term = search.text().strip()
        if not term:
            return

        def job() -> list[dict[str, str]]:
            return self.service.search(term)

        def done(rows: list[dict[str, str]]) -> None:
            built = []
            for r, row in enumerate(rows):
                app = MissingApp(
                    bundle_id=row.get("bundleId") or f"unknown.{r}",
                    name=row.get("name") or "App",
                    store_id=row.get("storeId") or None,
                    store_match="search",
                    source=row.get("source") or "search",
                )
                built.append((app, app.name, app.bundle_id, app.bundle_id, app.store_id))
            self._fill_app_table(
                table,
                built,
                status_text="поиск",
                status_color=MUTED,
                precheck=1 if built else 0,
            )
            self.log(f"search {term}: {len(rows)}")

        run_in_thread(
            self,
            job,
            on_finished=done,
            on_failed=lambda m: QMessageBox.warning(self, "Поиск", m),
        )

    def _install_selected_missing(self) -> None:
        table = self.pages["install"].findChild(QTableWidget, "install_table")
        if not table or not self._device_udid:
            return
        apps = self._selected_apps(table)
        if not apps:
            QMessageBox.information(self, "AppRestore", "Выберите приложение.")
            return
        acquire = self.acquire.isChecked()
        udid = self._device_udid

        def job() -> list[str]:
            out = []
            for app in apps:
                out.append(
                    self.service.restore_missing(
                        udid, app, acquire_license=acquire
                    )
                )
            return out

        def done(results: list[str]) -> None:
            self.log("ok  " + "; ".join(results))
            QMessageBox.information(self, "Готово", "Установка завершена.")

        run_in_thread(
            self,
            job,
            on_finished=done,
            on_failed=lambda m: QMessageBox.warning(self, "Ошибка", m),
        )

    def _build_library(self) -> QWidget:
        page, layout, meta = self._page_shell("Библиотека IPA")
        table = QTableWidget(0, 3)
        table.setObjectName("library_table")
        table.setHorizontalHeaderLabels(["Файл", "Приложение", ""])
        table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        self._prepare_table(table)
        layout.addWidget(table, 1)
        actions = QHBoxLayout()
        scan = QPushButton("Сканировать")
        scan.clicked.connect(self.reload_library)
        pick = QPushButton("Выбрать файл")
        pick.clicked.connect(self._pick_ipa)
        install = QPushButton("Установить")
        install.setObjectName("primary")
        actions.addWidget(scan)
        actions.addWidget(pick)
        actions.addStretch(1)
        actions.addWidget(install)
        layout.addLayout(actions)
        return page

    def reload_library(self) -> None:
        table = self.pages["library"].findChild(QTableWidget, "library_table")
        meta = self.pages["library"].findChild(QLabel, "meta")
        if not table:
            return
        entries = self.service.scan_local()
        table.setRowCount(0)
        table.setRowCount(len(entries))
        if self.service.demo_mode:
            for r, (fname, bundle, name) in enumerate(entries):
                table.setItem(r, 0, QTableWidgetItem(fname))
                pix = self.artwork.pixmap_for_app(bundle_id=bundle, name=name, size=36)
                table.setCellWidget(r, 1, AppCell(pix, name, bundle))
                table.setItem(r, 1, QTableWidgetItem(""))
                link = QTableWidgetItem("Установить")
                link.setForeground(QColor(ACCENT))
                table.setItem(r, 2, link)
                table.setRowHeight(r, 52)
            if meta:
                meta.setText(f"{len(entries)} файла" if len(entries) == 3 else f"{len(entries)} файлов")
            return
        for r, entry in enumerate(entries):
            path = getattr(entry, "path", None) or Path(str(entry))
            name = getattr(entry, "name", path.stem)
            bundle = getattr(entry, "bundle_id", "")
            table.setItem(r, 0, QTableWidgetItem(Path(path).name))
            pix = self.artwork.pixmap_for_app(bundle_id=bundle, name=name, size=36)
            table.setCellWidget(r, 1, AppCell(pix, name, bundle or "IPA"))
            item = QTableWidgetItem("")
            item.setData(Qt.ItemDataRole.UserRole, str(path))
            table.setItem(r, 1, item)
            link = QTableWidgetItem("Установить")
            link.setForeground(QColor(ACCENT))
            table.setItem(r, 2, link)
            table.setRowHeight(r, 52)
        if meta:
            meta.setText(f"{len(entries)} файлов")

    def _pick_ipa(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "Выберите IPA", "", "IPA (*.ipa)"
        )
        if path and self._device_udid:
            udid = self._device_udid

            def job() -> str:
                return self.service.install_ipa(udid, Path(path))

            run_in_thread(
                self,
                job,
                on_finished=lambda s: QMessageBox.information(self, "Установка", s),
                on_failed=lambda m: QMessageBox.warning(self, "Ошибка", m),
            )

    def _build_doctor(self) -> QWidget:
        page, layout, meta = self._page_shell("Проверки")
        table = QTableWidget(0, 2)
        table.setObjectName("doctor_table")
        table.setHorizontalHeaderLabels(["Проверка", "Результат"])
        table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self._prepare_table(table)
        layout.addWidget(table, 1)
        actions = QHBoxLayout()
        refresh = QPushButton("Обновить")
        refresh.clicked.connect(self.reload_doctor)
        setup = QPushButton("Починить на Windows")
        setup.clicked.connect(self._run_setup)
        to_auth = QPushButton("Ко входу")
        to_auth.setObjectName("primary")
        to_auth.clicked.connect(lambda: self._show_page("account"))
        actions.addWidget(refresh)
        actions.addWidget(setup)
        actions.addStretch(1)
        actions.addWidget(to_auth)
        layout.addLayout(actions)
        return page

    def reload_doctor(self) -> None:
        table = self.pages["doctor"].findChild(QTableWidget, "doctor_table")
        meta = self.pages["doctor"].findChild(QLabel, "meta")
        if not table:
            return
        checks = self.service.doctor()
        table.setRowCount(len(checks))
        any_bad = False
        for r, check in enumerate(checks):
            name = QTableWidgetItem(check.name)
            name.setFlags(Qt.ItemFlag.ItemIsEnabled)
            table.setItem(r, 0, name)
            item = QTableWidgetItem(check.detail)
            item.setFlags(Qt.ItemFlag.ItemIsEnabled)
            item.setForeground(QColor(OK if check.ok else BAD))
            table.setItem(r, 1, item)
            table.setRowHeight(r, 40)
            if not check.ok:
                any_bad = True
        if meta:
            meta.setText("нужен вход" if any_bad else "всё в порядке")
            meta.setStyleSheet(
                f"color:{WARN if any_bad else OK};font-size:12px;font-weight:600;border:none;"
            )

    def _run_setup(self) -> None:
        def job() -> list[str]:
            return self.service.setup()

        run_in_thread(
            self,
            job,
            on_finished=lambda notes: (
                self.log("setup " + "; ".join(notes)),
                self.reload_doctor(),
            ),
            on_failed=lambda m: QMessageBox.warning(self, "Setup", m),
        )

    def _build_account(self) -> QWidget:
        page, layout, meta = self._page_shell("Apple ID")
        meta.setText("не вошли")
        meta.setStyleSheet(f"color:{WARN};font-size:12px;font-weight:600;border:none;")
        lead = QLabel(
            "Пароль и код подтверждения вводятся здесь и не сохраняются в AppRestore."
        )
        lead.setWordWrap(True)
        lead.setStyleSheet(f"color:{MUTED};font-weight:400;border:none;")
        layout.addWidget(lead)

        def field(label: str, obj: str, *, password: bool = False, placeholder: str = "") -> QLineEdit:
            layout.addWidget(_muted_label(label, 12))
            edit = QLineEdit()
            edit.setObjectName(obj)
            if password:
                edit.setEchoMode(QLineEdit.EchoMode.Password)
            if placeholder:
                edit.setPlaceholderText(placeholder)
            layout.addWidget(edit)
            return edit

        field("Email", "auth_email", placeholder="you@example.com")
        field("Пароль", "auth_password", password=True)
        field("Код из сообщения", "auth_code")
        field("Passphrase (если спросит)", "auth_passphrase", password=True)
        note = QLabel("Первый вход иногда занимает несколько минут.")
        note.setStyleSheet(f"color:{MUTED};font-size:11.5px;font-weight:400;border:none;")
        layout.addWidget(note)
        actions = QHBoxLayout()
        revoke = QPushButton("Выйти")
        revoke.setObjectName("danger")
        revoke.clicked.connect(self._revoke)
        login = QPushButton("Войти")
        login.setObjectName("primary")
        login.clicked.connect(self._login)
        actions.addWidget(revoke)
        actions.addStretch(1)
        actions.addWidget(login)
        layout.addLayout(actions)
        layout.addStretch(1)
        return page

    def _login(self) -> None:
        page = self.pages["account"]
        email = page.findChild(QLineEdit, "auth_email").text()
        password = page.findChild(QLineEdit, "auth_password").text()
        code = page.findChild(QLineEdit, "auth_code").text()
        passphrase = page.findChild(QLineEdit, "auth_passphrase").text()
        if self.service.demo_mode:
            QMessageBox.information(
                self, "Демо", "В демо-режиме вход в Apple ID не выполняется."
            )
            return

        def job():
            return login_with_prompts(
                email, password, code, passphrase, on_output=lambda t: None
            )

        def done(result) -> None:
            self.log(("ok  " if result.ok else "err  ") + result.message)
            if result.ok:
                QMessageBox.information(self, "Apple ID", result.message)
            else:
                QMessageBox.warning(self, "Apple ID", result.message)
            self.refresh_device()
            self.refresh_overview_meta()

        run_in_thread(self, job, on_finished=done, on_failed=lambda m: QMessageBox.warning(self, "Apple ID", m))

    def _revoke(self) -> None:
        try:
            self.service.revoke()
            self.log("ok  signed out")
            QMessageBox.information(self, "Apple ID", "Сессия завершена.")
        except Exception as exc:
            QMessageBox.warning(self, "Apple ID", str(exc))
        self.refresh_device()

    def _build_log(self) -> QWidget:
        page, layout, meta = self._page_shell("Операции")
        meta.setText("без паролей")
        view = QTextEdit()
        view.setObjectName("log")
        view.setReadOnly(True)
        layout.addWidget(view, 1)
        if self.service.demo_mode and not self._log_lines:
            demo_lines = [
                "22:01:12  ok  connected iPhone 14",
                "22:01:40  ok  signed in",
                "22:02:05  ok  restore Instagram",
                "22:02:18  err  Spotify: phone locked",
                "22:02:18  hint unlock the phone and retry",
            ]
            for line in demo_lines:
                self._log_lines.append(line)
                view.append(line)
        else:
            for line in self._log_lines:
                view.append(line)
        actions = QHBoxLayout()
        copy = QPushButton("Копировать очищенное")
        copy.clicked.connect(lambda: QApplication.clipboard().setText("\n".join(self._log_lines)))
        actions.addWidget(copy)
        actions.addStretch(1)
        layout.addLayout(actions)
        return page

    def _build_settings(self) -> QWidget:
        page, layout, meta = self._page_shell("Настройки")
        meta.setText("0.2.4")
        layout.addWidget(_muted_label("Папка с файлами IPA", 12))
        ipa = QLineEdit(str(Path.home() / "AppRestore" / "ipa"))
        layout.addWidget(ipa)
        layout.addWidget(_muted_label("Кэш", 12))
        cache = QLineEdit(str(Path.home() / "AppRestore" / "cache"))
        layout.addWidget(cache)
        layout.addWidget(_muted_label("Обновления", 12))
        updates = QComboBox()
        updates.addItems(["Спрашивать о новой версии", "Только вручную"])
        layout.addWidget(updates)
        note = QLabel("Обновление ставится с проверкой целостности файла.")
        note.setStyleSheet(f"color:{MUTED};font-size:11.5px;font-weight:400;border:none;")
        layout.addWidget(note)
        save = QPushButton("Сохранить")
        save.setObjectName("primary")
        save.clicked.connect(
            lambda: QMessageBox.information(self, "Настройки", "Сохранено локально в этом сеансе.")
        )
        check = QPushButton("Проверить обновление")
        row = QHBoxLayout()
        row.addWidget(check)
        row.addStretch(1)
        row.addWidget(save)
        layout.addLayout(row)
        layout.addStretch(1)
        return page
