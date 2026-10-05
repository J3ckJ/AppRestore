from __future__ import annotations

from pathlib import Path
from typing import Any

from PySide6.QtCore import Qt, QSize, Signal
from PySide6.QtGui import QAction, QIcon, QPixmap
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
    QScrollArea,
    QSizePolicy,
    QStackedWidget,
    QTableWidget,
    QTableWidgetItem,
    QTextEdit,
    QVBoxLayout,
    QWidget,
    QFileDialog,
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


class Sidebar(QFrame):
    navigated = Signal(str)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("sidebar")
        self.setFixedWidth(240)
        self.setStyleSheet(
            f"""
            QFrame#sidebar {{ background: {SIDE}; }}
            QLabel {{ color: {SIDE_TEXT}; }}
            QListWidget {{
              background: transparent; border: none; color: {SIDE_TEXT};
              outline: none;
            }}
            QListWidget::item {{
              padding: 8px 10px; border-radius: 6px; margin: 1px 4px;
            }}
            QListWidget::item:selected {{
              background: #3A3A3E; color: white; font-weight: 600;
            }}
            """
        )
        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 14, 10, 10)
        layout.setSpacing(6)

        brand = QHBoxLayout()
        mark = QLabel("AR")
        mark.setFixedSize(28, 28)
        mark.setAlignment(Qt.AlignmentFlag.AlignCenter)
        mark.setStyleSheet(
            f"background:{ACCENT};color:white;border-radius:7px;font-weight:700;font-size:11px;"
        )
        title = QLabel("<b>AppRestore</b><br><span style='color:#A1A1A6;font-size:11px'>возврат приложений</span>")
        brand.addWidget(mark)
        brand.addWidget(title, 1)
        layout.addLayout(brand)

        self.device = QLabel("Устройство не выбрано")
        self.device.setWordWrap(True)
        self.device.setStyleSheet(
            f"background:{SIDE_2};border-radius:8px;padding:10px;color:{SIDE_TEXT};"
        )
        layout.addWidget(self.device)

        section = QLabel("РАЗДЕЛЫ")
        section.setStyleSheet(
            f"color:{SIDE_MUTED};font-size:10px;font-weight:700;letter-spacing:1px;padding:8px 6px 2px;"
        )
        layout.addWidget(section)

        self.nav = QListWidget()
        for key, label in NAV:
            item = QListWidgetItem(label)
            item.setData(Qt.ItemDataRole.UserRole, key)
            self.nav.addItem(item)
        self.nav.setCurrentRow(0)
        self.nav.currentItemChanged.connect(self._on_nav)
        layout.addWidget(self.nav, 1)

        foot = QLabel("Данные остаются на этом компьютере")
        foot.setStyleSheet(f"color:{SIDE_MUTED};font-size:11px;")
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


class Tile(QPushButton):
    def __init__(self, title: str, subtitle: str, parent=None) -> None:
        super().__init__(parent)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setMinimumHeight(72)
        self.setStyleSheet(
            f"""
            QPushButton {{
              text-align: left; padding: 12px 14px; background: {PANEL};
              border: 1px solid {LINE}; border-radius: 8px;
            }}
            QPushButton:hover {{ border-color: #C4C4C8; background: #FAFAFB; }}
            """
        )
        self.setText(f"{title}\n{subtitle}")


class MainWindow(QMainWindow):
    def __init__(self, service: GuiService, artwork: ArtworkCache) -> None:
        super().__init__()
        self.service = service
        self.artwork = artwork
        self.setWindowTitle("AppRestore")
        self.resize(1200, 760)
        self._device_udid: str | None = None
        self._log_lines: list[str] = []

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
        label.setStyleSheet("font-size:14px;font-weight:650;")
        meta = QLabel("")
        meta.setStyleSheet(f"color:{MUTED};font-size:12px;")
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
            self.sidebar.set_device_text("Нет подключённого iPhone\nПодключите по USB")
            return
        device = devices[0]
        self._device_udid = device.udid
        auth = "вход есть" if self.service.authenticated() else "нужен вход"
        self.sidebar.set_device_text(
            f"<b>{device.name}</b><br>iOS {device.ios_version} · USB<br>"
            f"<span style='color:#30D158'>● подключён</span><br>"
            f"<span style='color:#A1A1A6'>{auth}</span>"
        )

    def refresh_overview_meta(self) -> None:
        page = self.pages["overview"]
        meta = page.findChild(QLabel, "meta")
        if meta:
            meta.setText("готово к работе" if self._device_udid else "нет устройства")
            meta.setStyleSheet(f"color:{OK if self._device_udid else WARN};font-size:12px;font-weight:600;")
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
                apple.setStyleSheet(f"color:{OK};font-weight:650;")
            else:
                apple.setText("нужен вход")
                apple.setStyleSheet(f"color:{WARN};font-weight:650;")

    def _build_overview(self) -> QWidget:
        page, layout, meta = self._page_shell("Обзор устройства")
        meta.setText("готово к работе")
        body = QHBoxLayout()
        left = QVBoxLayout()
        phone = PhoneWidget()
        left.addWidget(phone, 0, Qt.AlignmentFlag.AlignHCenter)
        cap = QLabel("iPhone\nприложение на экране")
        cap.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        cap.setStyleSheet(f"color:{MUTED};")
        left.addWidget(cap)
        left.addStretch(1)
        body.addLayout(left, 0)

        right = QVBoxLayout()
        info = QHBoxLayout()
        for key, title in (
            ("info_model", "Модель"),
            ("info_system", "Система"),
            ("info_apple", "Apple ID"),
        ):
            box = QFrame()
            box.setStyleSheet(
                f"QFrame{{background:{PANEL};border:1px solid {LINE};border-radius:8px;}}"
            )
            bl = QVBoxLayout(box)
            bl.setContentsMargins(10, 8, 10, 8)
            t = QLabel(title)
            t.setStyleSheet(f"color:{MUTED};font-size:11px;")
            v = QLabel("-")
            v.setObjectName(key)
            v.setStyleSheet("font-weight:650;")
            bl.addWidget(t)
            bl.addWidget(v)
            info.addWidget(box)
        right.addLayout(info)

        grid = QVBoxLayout()
        row1 = QHBoxLayout()
        row2 = QHBoxLayout()
        tiles = [
            ("Вернуть сгруженные", "Ярлыки на месте, приложения выгружены", "offloaded"),
            ("Найти и поставить", "По имени, ссылке или из истории", "install"),
            ("Библиотека IPA", "Файлы уже на компьютере", "library"),
            ("Войти в Apple ID", "Нужно для загрузки из магазина", "account"),
            ("Проверки", "Связь с телефоном и готовность", "doctor"),
            ("Операции", "Журнал последних действий", "log"),
        ]
        for i, (title, sub, target) in enumerate(tiles):
            tile = Tile(title, sub)
            tile.clicked.connect(lambda _=False, t=target: self._show_page(t))
            (row1 if i < 3 else row2).addWidget(tile)
        grid.addLayout(row1)
        grid.addLayout(row2)
        right.addLayout(grid)
        right.addStretch(1)
        body.addLayout(right, 1)
        layout.addLayout(body, 1)
        return page

    def _fill_app_table(
        self,
        table: QTableWidget,
        rows: list[tuple[Any, str, str, str | None, str | None]],
    ) -> None:
        table.setRowCount(0)
        table.setRowCount(len(rows))
        for r, (obj, name, version, bundle_id, store_id) in enumerate(rows):
            check = QTableWidgetItem()
            check.setFlags(
                Qt.ItemFlag.ItemIsUserCheckable
                | Qt.ItemFlag.ItemIsEnabled
                | Qt.ItemFlag.ItemIsSelectable
            )
            check.setCheckState(Qt.CheckState.Unchecked)
            check.setData(Qt.ItemDataRole.UserRole, obj)
            table.setItem(r, 0, check)
            pix = self.artwork.pixmap_for_app(
                bundle_id=bundle_id, store_id=store_id, name=name, size=36
            )
            name_item = QTableWidgetItem(f"{name}\n{version}")
            name_item.setData(Qt.ItemDataRole.DecorationRole, QIcon(pix))
            table.setItem(r, 1, name_item)
            status = QTableWidgetItem("сгружено")
            table.setItem(r, 2, status)
            table.setRowHeight(r, 48)

    def _build_offloaded(self) -> QWidget:
        page, layout, meta = self._page_shell("Сгруженные")
        meta.setObjectName("meta")
        lead = QLabel(
            "Сначала пробуем докачку самого iPhone. Если не выйдет, загрузим через ваш Apple ID."
        )
        lead.setStyleSheet(f"color:{MUTED};")
        lead.setWordWrap(True)
        layout.addWidget(lead)
        table = QTableWidget(0, 3)
        table.setObjectName("offloaded_table")
        table.setHorizontalHeaderLabels(["", "Приложение", "Статус"])
        table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        table.verticalHeader().setVisible(False)
        table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        table.setIconSize(QSize(36, 36))
        layout.addWidget(table, 1)
        actions = QHBoxLayout()
        select_all = QPushButton("Выбрать все")
        select_all.clicked.connect(lambda: self._check_all(table, True))
        restore = QPushButton("Восстановить")
        restore.setObjectName("primary")
        restore.clicked.connect(self._restore_selected_offloaded)
        hint = QLabel("Экран телефона лучше оставить разблокированным")
        hint.setStyleSheet(f"color:{MUTED};font-size:11.5px;")
        actions.addWidget(select_all)
        actions.addStretch(1)
        actions.addWidget(hint)
        actions.addWidget(restore)
        layout.addLayout(actions)
        self._progress = QLabel("")
        self._progress.setStyleSheet(f"color:{MUTED};")
        layout.addWidget(self._progress)
        return page

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
        self._fill_app_table(table, rows)
        for r in range(table.rowCount()):
            table.item(r, 2).setText("сгружено")
            table.item(r, 2).setForeground(Qt.GlobalColor.darkYellow)
        if meta:
            meta.setText(f"{len(apps)} на устройстве")

    def _check_all(self, table: QTableWidget, checked: bool) -> None:
        state = Qt.CheckState.Checked if checked else Qt.CheckState.Unchecked
        for r in range(table.rowCount()):
            item = table.item(r, 0)
            if item:
                item.setCheckState(state)

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
        note.setStyleSheet(f"color:{MUTED};font-size:11.5px;")
        layout.addWidget(note)
        self.acquire = QCheckBox("Получить бесплатно в App Store на ваш Apple ID")
        self.acquire.setChecked(True)
        layout.addWidget(self.acquire)
        table = QTableWidget(0, 3)
        table.setObjectName("install_table")
        table.setHorizontalHeaderLabels(["", "Приложение", "Статус"])
        table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        table.verticalHeader().setVisible(False)
        table.setIconSize(QSize(36, 36))
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
        if not table or not self._device_udid:
            return
        apps = self.service.missing(self._device_udid)
        table.setRowCount(len(apps))
        for r, app in enumerate(apps):
            check = QTableWidgetItem()
            check.setFlags(
                Qt.ItemFlag.ItemIsUserCheckable
                | Qt.ItemFlag.ItemIsEnabled
                | Qt.ItemFlag.ItemIsSelectable
            )
            check.setCheckState(Qt.CheckState.Unchecked)
            check.setData(Qt.ItemDataRole.UserRole, app)
            table.setItem(r, 0, check)
            pix = self.artwork.pixmap_for_app(
                bundle_id=app.bundle_id, store_id=app.store_id, name=app.name, size=36
            )
            name_item = QTableWidgetItem(f"{app.name}\n{app.bundle_id}")
            name_item.setData(Qt.ItemDataRole.DecorationRole, QIcon(pix))
            table.setItem(r, 1, name_item)
            st = QTableWidgetItem("нет на телефоне")
            st.setForeground(Qt.GlobalColor.red)
            table.setItem(r, 2, st)
            table.setRowHeight(r, 48)

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
            table.setRowCount(len(rows))
            for r, row in enumerate(rows):
                app = MissingApp(
                    bundle_id=row.get("bundleId") or f"unknown.{r}",
                    name=row.get("name") or "App",
                    store_id=row.get("storeId") or None,
                    store_match="search",
                    source=row.get("source") or "search",
                )
                check = QTableWidgetItem()
                check.setFlags(
                    Qt.ItemFlag.ItemIsUserCheckable
                    | Qt.ItemFlag.ItemIsEnabled
                    | Qt.ItemFlag.ItemIsSelectable
                )
                check.setCheckState(
                    Qt.CheckState.Checked if r == 0 else Qt.CheckState.Unchecked
                )
                check.setData(Qt.ItemDataRole.UserRole, app)
                table.setItem(r, 0, check)
                pix = self.artwork.pixmap_for_app(
                    bundle_id=app.bundle_id,
                    store_id=app.store_id,
                    name=app.name,
                    size=36,
                )
                name_item = QTableWidgetItem(f"{app.name}\n{app.bundle_id}")
                name_item.setData(Qt.ItemDataRole.DecorationRole, QIcon(pix))
                table.setItem(r, 1, name_item)
                table.setItem(r, 2, QTableWidgetItem(row.get("source") or "поиск"))
                table.setRowHeight(r, 48)
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
        table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        table.verticalHeader().setVisible(False)
        table.setIconSize(QSize(28, 28))
        layout.addWidget(table, 1)
        actions = QHBoxLayout()
        scan = QPushButton("Сканировать")
        scan.clicked.connect(self.reload_library)
        pick = QPushButton("Выбрать файл")
        pick.clicked.connect(self._pick_ipa)
        actions.addWidget(scan)
        actions.addWidget(pick)
        actions.addStretch(1)
        layout.addLayout(actions)
        return page

    def reload_library(self) -> None:
        table = self.pages["library"].findChild(QTableWidget, "library_table")
        if not table:
            return
        entries = self.service.scan_local()
        if self.service.demo_mode:
            table.setRowCount(len(entries))
            for r, (fname, bundle, name) in enumerate(entries):
                table.setItem(r, 0, QTableWidgetItem(fname))
                pix = self.artwork.pixmap_for_app(bundle_id=bundle, name=name, size=28)
                item = QTableWidgetItem(name)
                item.setData(Qt.ItemDataRole.DecorationRole, QIcon(pix))
                table.setItem(r, 1, item)
                table.setItem(r, 2, QTableWidgetItem("Установить"))
                table.setRowHeight(r, 40)
            return
        table.setRowCount(len(entries))
        for r, entry in enumerate(entries):
            path = getattr(entry, "path", None) or Path(str(entry))
            name = getattr(entry, "name", path.stem)
            bundle = getattr(entry, "bundle_id", "")
            table.setItem(r, 0, QTableWidgetItem(Path(path).name))
            pix = self.artwork.pixmap_for_app(bundle_id=bundle, name=name, size=28)
            item = QTableWidgetItem(name)
            item.setData(Qt.ItemDataRole.DecorationRole, QIcon(pix))
            item.setData(Qt.ItemDataRole.UserRole, str(path))
            table.setItem(r, 1, item)
            table.setItem(r, 2, QTableWidgetItem("Установить"))
            table.setRowHeight(r, 40)

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
        table.verticalHeader().setVisible(False)
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
        if not table:
            return
        checks = self.service.doctor()
        table.setRowCount(len(checks))
        for r, check in enumerate(checks):
            table.setItem(r, 0, QTableWidgetItem(check.name))
            item = QTableWidgetItem(check.detail if check.ok else check.detail)
            item.setForeground(Qt.GlobalColor.darkGreen if check.ok else Qt.GlobalColor.red)
            table.setItem(r, 1, item)

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
        lead = QLabel(
            "Пароль и код подтверждения вводятся здесь и не сохраняются в AppRestore."
        )
        lead.setWordWrap(True)
        lead.setStyleSheet(f"color:{MUTED};")
        layout.addWidget(lead)
        form = QFormLayout()
        email = QLineEdit()
        email.setObjectName("auth_email")
        password = QLineEdit()
        password.setObjectName("auth_password")
        password.setEchoMode(QLineEdit.EchoMode.Password)
        code = QLineEdit()
        code.setObjectName("auth_code")
        passphrase = QLineEdit()
        passphrase.setObjectName("auth_passphrase")
        passphrase.setEchoMode(QLineEdit.EchoMode.Password)
        form.addRow("Email", email)
        form.addRow("Пароль", password)
        form.addRow("Код из сообщения", code)
        form.addRow("Passphrase (если спросит)", passphrase)
        layout.addLayout(form)
        note = QLabel("Первый вход иногда занимает несколько минут.")
        note.setStyleSheet(f"color:{MUTED};font-size:11.5px;")
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
        for line in self._log_lines:
            view.append(line)
        return page

    def _build_settings(self) -> QWidget:
        page, layout, meta = self._page_shell("Настройки")
        meta.setText("0.3.0-gui")
        form = QFormLayout()
        ipa = QLineEdit(str(Path.home() / "AppRestore" / "ipa"))
        cache = QLineEdit(str(Path.home() / "AppRestore" / "cache"))
        updates = QComboBox()
        updates.addItems(["Спрашивать о новой версии", "Только вручную"])
        form.addRow("Папка с файлами IPA", ipa)
        form.addRow("Кэш", cache)
        form.addRow("Обновления", updates)
        layout.addLayout(form)
        note = QLabel("Обновление ставится с проверкой целостности файла.")
        note.setStyleSheet(f"color:{MUTED};font-size:11.5px;")
        layout.addWidget(note)
        save = QPushButton("Сохранить")
        save.setObjectName("primary")
        save.clicked.connect(
            lambda: QMessageBox.information(self, "Настройки", "Сохранено локально в этом сеансе.")
        )
        row = QHBoxLayout()
        row.addStretch(1)
        row.addWidget(save)
        layout.addLayout(row)
        layout.addStretch(1)
        return page
