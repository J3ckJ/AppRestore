from __future__ import annotations

from pathlib import Path
from typing import Any

from PySide6.QtCore import Qt, QSize, QTimer, Signal, QEvent
from PySide6.QtGui import QColor, QBrush, QFont, QIcon, QPixmap
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QDialog,
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

from apprestore_core import __version__ as APP_VERSION
from apprestore_core.models import MissingApp, OffloadedApp
from apprestore_gui import demo
from apprestore_gui.auth_pty import (
    AppleLogin,
    AuthResult,
    keychain_has_saved_account,
    probe_keychain,
    unlock_keychain,
)
from apprestore_gui.icons_cache import ArtworkCache
from apprestore_gui.service_adapter import GuiService
from apprestore_gui.theme import (
    ACCENT,
    ACCENT_SOFT,
    BAD,
    INK,
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


def file_install_prompt(message: str, app_name: str = "") -> str | None:
    """Russian explanation when native redownload did not clearly start.

    The CLI asks the same question and then retries with
    ``try_device_redownload=False``. ``None`` means this is a different error.
    """
    text = message.lower()
    if "refusing a competing ipa install" not in text:
        return None
    name = app_name.strip() or "Приложение"
    if "did not finish downloading" in text:
        return (
            f"{name}: iPhone, похоже, ещё качает это приложение. "
            "Пока загрузка идёт, ставить файл параллельно нельзя. "
            "Если на телефоне загрузка уже остановилась, можно поставить его файлом с компьютера."
        )
    return (
        f"{name}: iPhone не начал загрузку сам, приложение всё ещё сгружено. "
        "Если на телефоне сейчас ничего не качается, можно поставить его файлом с компьютера."
    )


def friendly_restore_error(message: str) -> str:
    low = message.lower()
    if "not authenticated" in low or "passphrase is required" in low:
        return (
            "Сессия Apple ID закрыта. Откройте её в разделе Apple ID "
            "(пароль связки вводится один раз при открытии окна) и повторите."
        )
    return message


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
        self._device = None
        self._device_watch_busy = False
        self._icons_busy = False
        self._offloaded_inflight = False
        self._offloaded_waiters: list[tuple[Any, Any]] = []
        self._offloaded_generation = 0
        self._apple_signed_in = False
        self._apple_session = "unknown"
        self._pending_file_apps: list[Any] = []
        self._auth_started = False
        self._auth_phase = "idle"
        self._auth_job: AppleLogin | None = None
        self._udid_misses = 0
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

        self._show_page("overview")
        if self.service.demo_mode:
            self.refresh_device()
        else:
            self._apply_devices([])
            self._device_timer = QTimer(self)
            self._device_timer.setInterval(3000)
            self._device_timer.timeout.connect(self._watch_devices)
            self._device_timer.start()
            QTimer.singleShot(0, self._watch_devices)

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

    def _watch_devices(self) -> None:
        if self._device_watch_busy or self.service.demo_mode:
            return
        self._device_watch_busy = True
        run_in_thread(
            self,
            self.service.connected_udids,
            on_finished=self._on_udids,
            on_failed=self._on_udids_failed,
        )
        if not self._auth_started:
            self._auth_started = True
            run_in_thread(
                self,
                self._read_apple_session,
                self.service,
                on_finished=self._on_apple_session,
                on_failed=lambda _message: None,
            )

    def _on_udids(self, udids: object) -> None:
        self._device_watch_busy = False
        self._udid_misses = 0
        found = [str(item) for item in udids] if isinstance(udids, list) else []
        current = found[0] if found else None
        if current == self._device_udid:
            return
        if current is None:
            self.service.device_error = ""
            self.log("iPhone отключён")
            self._apply_devices([])
            self._refresh_visible_lists()
            return
        self._device_watch_busy = True
        run_in_thread(
            self,
            self.service.devices,
            on_finished=self._on_devices_found,
            on_failed=self._on_devices_failed,
        )

    def _on_udids_failed(self, message: str) -> None:
        self._device_watch_busy = False
        self.service.device_error = message
        self._udid_misses += 1
        if self._device_udid and self._udid_misses < 2:
            return
        if self._device_udid:
            self.log("iPhone отключён")
        self._apply_devices([])
        self._refresh_visible_lists()

    def _refresh_visible_lists(self) -> None:
        keys = [key for key, _label in NAV]
        index = self.stack.currentIndex()
        key = keys[index] if 0 <= index < len(keys) else ""
        if key == "offloaded":
            self.reload_offloaded()
        elif key == "install":
            self.reload_missing()

    @staticmethod
    def _read_apple_session(service: GuiService) -> str:
        del service
        try:
            return probe_keychain()
        except Exception:
            return "out"

    def _apple_auth_label(self) -> tuple[str, str]:
        if self._apple_session == "in" or self._apple_signed_in:
            return "вход есть", OK
        if self._apple_session == "locked":
            return "нужен пароль связки", WARN
        return "нужен вход", "#A1A1A6"

    def _on_apple_session(self, state: object) -> None:
        if state is True or state == "in":
            session = "in"
        elif state == "locked":
            session = "locked"
        else:
            session = "out"
        self._apple_session = session
        self._apple_signed_in = session == "in"
        self.refresh_overview_meta()
        if self._device is not None:
            self._apply_devices([self._device])
        self._sync_auth_entry()
        if session == "locked" and not self.service.keychain_ready():
            QTimer.singleShot(0, self._ask_keychain_passphrase)

    def _ask_keychain_passphrase(self) -> None:
        if self.service.demo_mode or self.service.keychain_ready():
            return
        if getattr(self, "_keychain_dialog", None) is not None:
            return
        saved_account = keychain_has_saved_account()
        dialog = QDialog(self)
        self._keychain_dialog = dialog
        dialog.setWindowTitle("Пароль связки ключей")
        dialog.setModal(True)
        dialog.setMinimumWidth(440)
        layout = QVBoxLayout(dialog)
        layout.setContentsMargins(18, 16, 18, 16)
        layout.setSpacing(10)
        title = QLabel("Откройте связку один раз" if saved_account else "Пароль для новой связки")
        title.setStyleSheet("font-size:15px;font-weight:650;border:none;")
        if saved_account:
            explanation = (
                "Это не пароль Apple ID. ipatool спрашивает его у каждого своего запуска. "
                "Введите его здесь один раз — дальше AppRestore подставит его сам, без чёрного окна."
            )
        else:
            explanation = (
                "Сохранённого входа нет: он удалён при выходе. Это не пароль Apple ID. "
                "ipatool зашифрует этим паролем новый вход. Введите его один раз — "
                "дальше AppRestore подставит его сам, без чёрного окна."
            )
        text = QLabel(explanation)
        text.setWordWrap(True)
        text.setStyleSheet(f"color:{MUTED};font-weight:400;border:none;")
        edit = QLineEdit()
        edit.setEchoMode(QLineEdit.EchoMode.Password)
        edit.setPlaceholderText("пароль связки ключей")
        status = QLabel("")
        status.setWordWrap(True)
        status.setStyleSheet(f"color:{WARN};font-weight:500;border:none;")
        buttons = QHBoxLayout()
        later = QPushButton("Позже")
        open_button = QPushButton("Открыть" if saved_account else "Запомнить")
        open_button.setObjectName("primary")
        buttons.addWidget(later)
        buttons.addStretch(1)
        buttons.addWidget(open_button)
        layout.addWidget(title)
        layout.addWidget(text)
        layout.addWidget(edit)
        layout.addWidget(status)
        layout.addLayout(buttons)

        def close_dialog() -> None:
            self._keychain_dialog = None

        def finish(result: object) -> None:
            auth = result if isinstance(result, AuthResult) else AuthResult(False, "Не удалось открыть связку.")
            if auth.ok:
                self.service.remember_keychain_passphrase(
                    edit.text(),
                    session_open=auth.session_open,
                )
                if auth.session_open:
                    self._apple_session = "in"
                    self._apple_signed_in = True
                else:
                    self._apple_session = "out"
                    self._apple_signed_in = False
                    if self._auth_job is None:
                        self._set_auth_status(auth.message, WARN)
                self.refresh_overview_meta()
                if self._device is not None:
                    self._apply_devices([self._device])
                self._sync_auth_entry()
                close_dialog()
                if dialog.isVisible():
                    dialog.accept()
                return
            if not dialog.isVisible():
                return
            edit.setEnabled(True)
            open_button.setEnabled(True)
            later.setEnabled(True)
            edit.setFocus()
            edit.selectAll()
            status.setText(auth.message or "Не удалось открыть связку.")

        def submit() -> None:
            secret = edit.text()
            if not secret:
                status.setText("Введите пароль связки ключей.")
                edit.setFocus()
                return
            edit.setEnabled(False)
            open_button.setEnabled(False)
            later.setEnabled(False)
            status.setText(
                "Проверяем пароль. Это не пароль Apple ID."
                if saved_account
                else "Запоминаем пароль связки."
            )
            run_in_thread(
                dialog,
                lambda: unlock_keychain(secret),
                on_finished=finish,
                on_failed=lambda message: finish(AuthResult(False, message)),
            )

        open_button.clicked.connect(submit)
        edit.returnPressed.connect(submit)
        later.clicked.connect(dialog.reject)
        dialog.finished.connect(close_dialog)
        edit.setFocus()
        dialog.exec()

    def _on_devices_found(self, devices: object) -> None:
        self._device_watch_busy = False
        found = list(devices) if isinstance(devices, list) else []
        if found and (self._device is None or found[0].udid != self._device_udid):
            name = getattr(found[0], "name", "iPhone")
            self.log(f"iPhone подключён: {name}")
        self._apply_devices(found)
        self._refresh_visible_lists()

    def _on_devices_failed(self, message: str) -> None:
        self._device_watch_busy = False
        self.service.device_error = message
        if self._device_udid:
            return
        self._apply_devices([])

    def _apply_devices(self, devices: list[Any]) -> None:
        if not devices:
            self._device_udid = None
            self._device = None
            detail = (self.service.device_error or "Подключите по USB").replace("<", " ")
            if len(detail) > 160:
                detail = detail[:157] + "..."
            self.sidebar.set_device_text(f"Нет подключённого iPhone<br>{detail}")
            self._update_phone_home([])
            self.refresh_overview_meta()
            return
        device = devices[0]
        self._device = device
        self._device_udid = device.udid
        auth, auth_color = self._apple_auth_label()
        self.sidebar.set_device_text(
            f"<b>{device.name}</b><br>"
            f"<span style='color:#A1A1A6'>iOS {device.ios_version} · USB</span><br>"
            f"<span style='color:#30D158'>● подключён</span><br>"
            f"<span style='color:{auth_color}'>{auth}</span>"
        )
        self.refresh_overview_meta()
        QTimer.singleShot(0, self._refresh_phone_from_device)

    def refresh_device(self) -> None:
        self._apply_devices(self.service.devices())

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
        udid = self._device_udid
        name = self._device.name if self._device is not None else "iPhone"
        if self._icons_busy:
            return
        self._icons_busy = True

        def done(apps: list[Any]) -> None:
            self._icons_busy = False
            if self._device_udid != udid:
                return
            icons = []
            for app in list(apps)[:16]:
                pix = self.artwork.cached_pixmap(
                    bundle_id=app.bundle_id,
                    store_id=app.store_id,
                    name=app.name,
                    size=64,
                )
                icons.append((pix, True))
            self._update_phone_home(icons, device_name=name, offloaded_n=len(list(apps)))

        def failed(_message: str) -> None:
            self._icons_busy = False
            if self._device_udid == udid:
                self._update_phone_home([], device_name=name)

        self._request_offloaded(udid, done, failed)

    def _request_offloaded(
        self,
        udid: str,
        on_finished: Any,
        on_failed: Any,
    ) -> None:
        """One USB app list at a time. A second caller waits for the same result."""
        self._offloaded_waiters.append((on_finished, on_failed))
        if self._offloaded_inflight:
            return
        self._offloaded_inflight = True

        def done(apps: object) -> None:
            self._offloaded_inflight = False
            waiters = self._offloaded_waiters
            self._offloaded_waiters = []
            rows = list(apps) if isinstance(apps, list) else []
            for ok, _fail in waiters:
                ok(rows)

        def failed(message: str) -> None:
            self._offloaded_inflight = False
            waiters = self._offloaded_waiters
            self._offloaded_waiters = []
            for _ok, fail in waiters:
                fail(message)

        run_in_thread(
            self,
            self.service.offloaded,
            udid,
            on_finished=done,
            on_failed=failed,
        )

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
        device = self._device
        if device is not None:
            if model:
                model.setText(device.name)
            if system:
                system.setText(f"iOS {device.ios_version}")
        else:
            if model:
                model.setText("-")
            if system:
                system.setText("-")
        if apple:
            if self._apple_session == "in" or self._apple_signed_in:
                apple.setText("сессия есть")
                apple.setStyleSheet(f"color:{OK};font-size:13px;font-weight:650;border:none;background:transparent;")
            elif self._apple_session == "locked":
                apple.setText("нужен пароль связки")
                apple.setStyleSheet(f"color:{WARN};font-size:13px;font-weight:650;border:none;background:transparent;")
            else:
                apple.setText("нужен вход")
                apple.setStyleSheet(f"color:{WARN};font-size:13px;font-weight:650;border:none;background:transparent;")

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
        table.blockSignals(True)
        try:
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
        finally:
            table.blockSignals(False)

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
            pix = self.artwork.cached_pixmap(
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
        self._offloaded_restore_btn = restore
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
            self._offloaded_generation += 1
            table.setRowCount(0)
            if meta:
                meta.setText("нет устройства")
            if self._progress:
                self._progress.setText("")
            return
        udid = self._device_udid
        if meta:
            meta.setText("обновляем…" if table.rowCount() else "смотрим iPhone…")
        if self._progress:
            self._progress.setText("Читаем приложения с iPhone. Окно можно двигать.")

        def done(apps: list[Any]) -> None:
            if self._device_udid != udid:
                return
            self._show_offloaded_apps(apps)

        def failed(message: str) -> None:
            if self._device_udid != udid:
                return
            if meta:
                meta.setText("не прочиталось")
            lowered = message.casefold()
            if "device not found" in lowered or "usbmux" in lowered or "connectionterminated" in lowered:
                text = "iPhone не ответил. Разблокируйте его и откройте этот раздел ещё раз."
            else:
                text = message.splitlines()[-1][:240]
            if self._progress:
                self._progress.setText(text)
            self.log("err  " + text)

        self._request_offloaded(udid, done, failed)

    def _show_offloaded_apps(self, apps: list[Any]) -> None:
        table = self.pages["offloaded"].findChild(QTableWidget, "offloaded_table")
        meta = self.pages["offloaded"].findChild(QLabel, "meta")
        if not table:
            return
        self._offloaded_generation += 1
        generation = self._offloaded_generation
        rows = [(a, a.name, f"версия {a.version}", a.bundle_id, a.store_id) for a in apps]
        self._offloaded_rows = rows
        self._offloaded_fill_at = 0
        table.blockSignals(True)
        table.setRowCount(0)
        table.setRowCount(len(rows))
        table.blockSignals(False)
        if meta:
            meta.setText(f"{len(apps)} на устройстве")
        if self._progress:
            self._progress.setText("Показываем список…" if rows else "")
        self._fill_offloaded_chunk(generation)

    def _fill_offloaded_chunk(self, generation: int) -> None:
        if generation != self._offloaded_generation:
            return
        table = self.pages["offloaded"].findChild(QTableWidget, "offloaded_table")
        rows = getattr(self, "_offloaded_rows", [])
        if not table:
            return
        start = self._offloaded_fill_at
        end = min(start + 40, len(rows))
        table.blockSignals(True)
        table.setUpdatesEnabled(False)
        for r in range(start, end):
            obj, name, version, bundle_id, store_id = rows[r]
            check = QTableWidgetItem()
            check.setFlags(
                Qt.ItemFlag.ItemIsUserCheckable
                | Qt.ItemFlag.ItemIsEnabled
                | Qt.ItemFlag.ItemIsSelectable
            )
            check.setCheckState(
                Qt.CheckState.Checked
                if self.service.demo_mode and r < 3
                else Qt.CheckState.Unchecked
            )
            check.setData(Qt.ItemDataRole.UserRole, obj)
            table.setItem(r, 0, check)
            pix = self.artwork.cached_pixmap(
                bundle_id=bundle_id, store_id=store_id, name=name, size=36
            )
            table.setCellWidget(r, 1, AppCell(pix, name, version))
            name_item = QTableWidgetItem("")
            name_item.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
            table.setItem(r, 1, name_item)
            status = QTableWidgetItem("сгружено")
            status.setForeground(QColor(WARN))
            status.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            table.setItem(r, 2, status)
            table.setRowHeight(r, 52)
        table.setUpdatesEnabled(True)
        table.blockSignals(False)
        self._offloaded_fill_at = end
        if end < len(rows):
            QTimer.singleShot(0, lambda gen=generation: self._fill_offloaded_chunk(gen))
            return
        self._on_offloaded_changed()
        if self._progress:
            self._progress.setText("")

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
        self._start_offloaded_restore(apps, try_device_redownload=True)

    def _offloaded_restore_button(self) -> QPushButton | None:
        button = getattr(self, "_offloaded_restore_btn", None)
        if isinstance(button, QPushButton):
            return button
        return None

    def _start_offloaded_restore(
        self,
        apps: list[Any],
        *,
        try_device_redownload: bool,
    ) -> None:
        if not apps or not self._device_udid:
            return
        button = self._offloaded_restore_button()
        if button is not None:
            button.setEnabled(False)
        if try_device_redownload:
            self._progress.setText("Просим iPhone скачать само. Окно можно двигать.")
        else:
            self._progress.setText("Ставим файлом с компьютера. Окно можно двигать.")
        udid = self._device_udid

        def job() -> list[tuple[Any, str | None, str | None]]:
            out: list[tuple[Any, str | None, str | None]] = []
            for app in apps:
                try:
                    status = self.service.restore_offloaded(
                        udid,
                        app,
                        try_device_redownload=try_device_redownload,
                    )
                except Exception as exc:
                    out.append((app, None, str(exc)))
                else:
                    out.append((app, status, None))
            return out

        def done(results: list[tuple[Any, str | None, str | None]]) -> None:
            succeeded = [
                (app, status)
                for app, status, err in results
                if err is None and status is not None
            ]
            failed = [(app, err) for app, _status, err in results if err]
            for app, status in succeeded:
                self.log(f"restore {app.name}: {status}")
            fallback: list[tuple[Any, str]] = []
            other: list[tuple[Any, str]] = []
            for app, err in failed:
                self.log(f"err  {app.name}: {err}")
                prompt = (
                    file_install_prompt(err, app.name)
                    if try_device_redownload
                    else None
                )
                if prompt:
                    fallback.append((app, prompt))
                else:
                    other.append((app, err))
            if fallback:
                self._offer_file_install(fallback, other, succeeded)
                return
            self._finish_offloaded_restore(succeeded, other)

        def fail(msg: str) -> None:
            self._set_offloaded_restore_enabled(True)
            self._progress.setText("")
            self.log(f"err  {msg}")
            QMessageBox.warning(self, "Ошибка", friendly_restore_error(msg))

        run_in_thread(self, job, on_finished=done, on_failed=fail)

    def _set_offloaded_restore_enabled(self, enabled: bool) -> None:
        button = self._offloaded_restore_button()
        if button is not None:
            button.setEnabled(enabled)

    def _offer_file_install(
        self,
        fallback: list[tuple[Any, str]],
        other: list[tuple[Any, str]],
        succeeded: list[tuple[Any, str]],
    ) -> None:
        self._progress.setText("")
        text = "\n\n".join(prompt for _app, prompt in fallback)
        session_open = self._apple_session == "in" or self._apple_signed_in
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Icon.Warning)
        box.setWindowTitle("AppRestore")
        box.setText("Загрузка на iPhone не подтвердилась")
        locked = self._apple_session == "locked" and not session_open
        if session_open:
            box.setInformativeText(text)
        elif locked:
            box.setInformativeText(
                text
                + "\n\nЧтобы поставить файлом, откройте связку. "
                "Пароль вводится один раз и в консоль больше не попадает."
            )
        else:
            box.setInformativeText(
                text
                + "\n\nЧтобы поставить файлом, сначала войдите в Apple ID. "
                "После этого установка начнётся сама."
            )
        if session_open:
            accept = box.addButton("Поставить файлом", QMessageBox.ButtonRole.AcceptRole)
            box.addButton("Не сейчас", QMessageBox.ButtonRole.RejectRole)
            box.exec()
            if box.clickedButton() is accept:
                if other:
                    self._show_restore_errors(other)
                self._start_offloaded_restore(
                    [app for app, _prompt in fallback],
                    try_device_redownload=False,
                )
                return
        else:
            accept = box.addButton(
                "Открыть связку" if locked else "К Apple ID",
                QMessageBox.ButtonRole.AcceptRole,
            )
            box.addButton("Закрыть", QMessageBox.ButtonRole.RejectRole)
            box.exec()
            if box.clickedButton() is accept:
                self._set_offloaded_restore_enabled(True)
                if locked:
                    self._ask_keychain_passphrase()
                    if self.service.keychain_ready():
                        self._start_offloaded_restore(
                            [app for app, _prompt in fallback],
                            try_device_redownload=False,
                        )
                        return
                else:
                    self._pending_file_apps = [app for app, _prompt in fallback]
                    if other:
                        self._show_restore_errors(other)
                    self._show_page("account")
                    return
        self._finish_offloaded_restore(succeeded, other)

    def _finish_offloaded_restore(
        self,
        succeeded: list[tuple[Any, str]],
        failed: list[tuple[Any, str]],
    ) -> None:
        self._set_offloaded_restore_enabled(True)
        if failed:
            self._progress.setText("")
            self._show_restore_errors(failed)
            if succeeded:
                self.reload_offloaded()
            return
        if succeeded:
            self._progress.setText("Готово")
            QMessageBox.information(
                self,
                "Готово",
                "Выбранные приложения обработаны. Посмотрите домашний экран iPhone.",
            )
            self.reload_offloaded()
            return
        self._progress.setText("")

    def _continue_pending_file_install(self) -> None:
        pending = self._pending_file_apps
        self._pending_file_apps = []
        if not pending or not self._device_udid:
            return
        if not (self._apple_session == "in" or self._apple_signed_in):
            return
        keys = [key for key, _label in NAV]
        self.sidebar.nav.blockSignals(True)
        self.sidebar.select("offloaded")
        self.sidebar.nav.blockSignals(False)
        self.stack.setCurrentIndex(keys.index("offloaded"))
        self._start_offloaded_restore(pending, try_device_redownload=False)

    def _show_restore_errors(self, failed: list[tuple[Any, str]]) -> None:
        lines = [
            f"{app.name}: {friendly_restore_error(err)}" for app, err in failed
        ]
        QMessageBox.warning(self, "Ошибка", "\n\n".join(lines))

    def _build_install(self) -> QWidget:
        page, layout, meta = self._page_shell("Найти и поставить")
        bar = QHBoxLayout()
        search = QLineEdit()
        search.setObjectName("install_search")
        search.setPlaceholderText("имя, ссылка или номер в магазине")
        find = QPushButton("Искать")
        find.setObjectName("primary")
        find.clicked.connect(self._run_search)
        search.returnPressed.connect(self._run_search)
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
        # Off by default: getting an app adds it to the Apple ID purchase history.
        self.acquire.setChecked(False)
        layout.addWidget(self.acquire)
        table = QTableWidget(0, 3)
        table.setObjectName("install_table")
        table.setHorizontalHeaderLabels(["", "Приложение", "Статус"])
        table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Fixed)
        table.setColumnWidth(0, 36)
        # Keep "нет на телефоне" on one line instead of wrapping in a narrow column.
        table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        table.setWordWrap(False)
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
        if not table:
            return
        if not self._device_udid:
            table.setRowCount(0)
            if meta:
                meta.setText("нет устройства")
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
        meta = self.pages["install"].findChild(QLabel, "meta")
        if meta:
            meta.setText("ищем…")

        def job() -> list[dict[str, str]]:
            return self.service.search(term)

        def done(rows: list[dict[str, str]]) -> None:
            built = []
            for r, row in enumerate(rows):
                bundle_id = (row.get("bundleId") or "").strip()
                store_id = (row.get("storeId") or "").strip() or None
                app = MissingApp(
                    bundle_id=bundle_id,
                    name=row.get("name") or "App",
                    store_id=store_id,
                    store_match="search",
                    source=row.get("source") or "search",
                )
                detail = bundle_id or (
                    f"номер {store_id}" if store_id else ""
                )
                built.append((app, app.name, detail, bundle_id or None, store_id))
            self._fill_app_table(
                table,
                built,
                status_text="можно скачать",
                status_color=MUTED,
                precheck=1 if built else 0,
            )
            if meta:
                meta.setText(
                    f"найдено: {len(built)}" if built else "ничего не найдено"
                )
            self.log(f"search {term}: {len(rows)}")

        def failed(message: str) -> None:
            if meta:
                meta.setText("поиск не удался")
            QMessageBox.warning(self, "Поиск", message)

        run_in_thread(
            self,
            job,
            on_finished=done,
            on_failed=failed,
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
        refresh.clicked.connect(lambda: (self.reload_doctor(), self.refresh_device()))
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

        def done(notes: list[str]) -> None:
            self.log("setup " + "; ".join(notes))
            self.reload_doctor()
            self.refresh_device()
            self.refresh_overview_meta()
            QMessageBox.information(self, "Проверки", "\n".join(notes))

        run_in_thread(
            self,
            job,
            on_finished=done,
            on_failed=lambda m: QMessageBox.warning(self, "Проверки", m),
        )

    def _build_account(self) -> QWidget:
        page, layout, meta = self._page_shell("Apple ID")
        meta.setText("не вошли")
        meta.setStyleSheet(f"color:{WARN};font-size:12px;font-weight:600;border:none;")
        lead = QLabel(
            "Сначала почта и пароль. Поле кода появится только если Apple попросит его после входа."
        )
        lead.setWordWrap(True)
        lead.setStyleSheet(f"color:{MUTED};font-weight:400;border:none;")
        layout.addWidget(lead)

        def field(label: str, obj: str, *, password: bool = False, placeholder: str = "") -> QLineEdit:
            caption = _muted_label(label, 12)
            caption.setObjectName(f"{obj}_label")
            layout.addWidget(caption)
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
        field("Код из сообщения", "auth_code", placeholder="6 цифр из сообщения Apple")
        for name in ("auth_email", "auth_password", "auth_code"):
            page.findChild(QLineEdit, name).returnPressed.connect(self._login)
        self._sync_code_field(page)

        self._auth_transcript = QTextEdit()
        self._auth_transcript.setObjectName("auth_transcript")
        self._auth_transcript.setReadOnly(True)
        self._auth_transcript.setFixedHeight(132)
        self._auth_transcript.setPlaceholderText(
            "Ход входа появится здесь. Пароль и код в этот журнал не пишутся."
        )
        self._auth_transcript.setStyleSheet(
            f"QTextEdit {{ background:{PANEL}; border:1px solid {LINE}; border-radius:8px; color:{INK}; }}"
        )
        layout.addWidget(self._auth_transcript)

        note = QLabel(
            "Пароль связки спрашивается один раз при открытии программы, не на этой вкладке."
        )
        note.setWordWrap(True)
        note.setStyleSheet(f"color:{MUTED};font-size:11.5px;font-weight:400;border:none;")
        layout.addWidget(note)
        self._auth_status = QLabel("")
        self._auth_status.setObjectName("auth_status")
        self._auth_status.setWordWrap(True)
        self._auth_status.hide()
        layout.addWidget(self._auth_status)
        actions = QHBoxLayout()
        revoke = QPushButton("Выйти")
        revoke.setObjectName("danger")
        revoke.clicked.connect(self._revoke)
        self._auth_cancel = QPushButton("Отмена")
        self._auth_cancel.hide()
        self._auth_cancel.clicked.connect(self._cancel_login)
        self._auth_login = QPushButton("Войти")
        self._auth_login.setObjectName("primary")
        self._auth_login.setMinimumWidth(148)
        self._auth_login.clicked.connect(self._login)
        actions.addWidget(revoke)
        actions.addStretch(1)
        actions.addWidget(self._auth_cancel)
        actions.addWidget(self._auth_login)
        layout.addLayout(actions)
        layout.addStretch(1)
        return page

    def _set_auth_status(self, text: str, color: str) -> None:
        meta = self.pages["account"].findChild(QLabel, "meta")
        if not text:
            self._auth_status.hide()
        else:
            self._auth_status.setText(text)
            self._auth_status.setStyleSheet(
                f"color:{color}; background:{PANEL}; border:1px solid {LINE}; "
                "border-radius:8px; padding:8px 10px;"
            )
            self._auth_status.show()
        if meta and self._auth_phase == "idle" and self._apple_signed_in:
            meta.setText("сессия есть")
            meta.setStyleSheet(f"color:{OK};font-size:12px;font-weight:600;border:none;")
        elif meta and self._auth_phase == "running":
            meta.setText("входим…")
            meta.setStyleSheet(f"color:{WARN};font-size:12px;font-weight:600;border:none;")
        elif meta and self._auth_phase == "need_code":
            meta.setText("нужен код")
            meta.setStyleSheet(f"color:{WARN};font-size:12px;font-weight:600;border:none;")
        elif meta and self._auth_phase == "need_passphrase":
            meta.setText("нужен пароль связки")
            meta.setStyleSheet(f"color:{WARN};font-size:12px;font-weight:600;border:none;")
        elif meta and self._auth_phase == "idle" and self._apple_session == "locked":
            meta.setText("связка закрыта")
            meta.setStyleSheet(f"color:{WARN};font-size:12px;font-weight:600;border:none;")
        elif meta and not self._apple_signed_in:
            meta.setText("не вошли")
            meta.setStyleSheet(f"color:{WARN};font-size:12px;font-weight:600;border:none;")

    def _sync_code_field(self, page: QWidget | None = None) -> None:
        host = page if page is not None else self.pages.get("account")
        if host is None:
            return
        self._set_code_field_visible(self._auth_phase == "need_code", host)

    def _set_code_field_visible(self, visible: bool, page: QWidget | None = None) -> None:
        if page is None:
            page = self.pages.get("account")
        if page is None:
            return
        for name in ("auth_code_label", "auth_code"):
            widget = page.findChild(QWidget, name)
            if widget is not None:
                widget.setVisible(visible)

    def _mark_auth_field(self, name: str | None) -> None:
        page = self.pages["account"]
        for field_name in ("auth_email", "auth_password", "auth_code"):
            edit = page.findChild(QLineEdit, field_name)
            if edit is None:
                continue
            if field_name == name:
                edit.setFocus()
                edit.selectAll()

    def _reset_auth_buttons(self) -> None:
        self._auth_phase = "idle"
        self._auth_login.setEnabled(True)
        self._auth_login.setText("Войти")
        self._auth_cancel.hide()
        self._mark_auth_field(None)
        page = self.pages["account"]
        for name in ("auth_email", "auth_password", "auth_code"):
            edit = page.findChild(QLineEdit, name)
            if edit is not None:
                edit.setEnabled(True)
        self._sync_code_field()

    def _sync_auth_entry(self) -> None:
        if self._auth_phase != "idle":
            return
        if self._apple_session == "locked" and not self.service.keychain_ready():
            self._auth_login.setText("Открыть связку")
            self._set_auth_status(
                "Сессия Apple ID уже сохранена. Пароль связки вводится один раз в отдельном окне, не на этой вкладке.",
                WARN,
            )
            return
        self._auth_login.setText("Войти")

    def _login(self) -> None:
        page = self.pages["account"]
        email = page.findChild(QLineEdit, "auth_email").text().strip()
        password = page.findChild(QLineEdit, "auth_password").text()
        code_edit = page.findChild(QLineEdit, "auth_code")
        code = code_edit.text().strip() if code_edit is not None else ""
        passphrase = self.service.keychain_passphrase()
        if self.service.demo_mode:
            self._set_auth_status("В демо-режиме вход в Apple ID не выполняется.", WARN)
            return
        if self._auth_phase == "need_code":
            if not code:
                self._set_auth_status("Введите код из сообщения Apple.", WARN)
                self._mark_auth_field("auth_code")
                return
            if self._auth_job is None:
                self._reset_auth_buttons()
                self._set_auth_status("Вход уже завершился. Нажмите «Войти» ещё раз.", WARN)
                return
            self._auth_job.submit("code", code)
            self._auth_phase = "running"
            self._auth_login.setEnabled(False)
            self._auth_login.setText("Входим…")
            self._set_auth_status("Код отправлен. Ждём ответ Apple…", MUTED)
            self._mark_auth_field(None)
            return
        if self._auth_phase == "need_passphrase" or (
            self._apple_session == "locked" and self._auth_job is None and not passphrase
        ):
            self._ask_keychain_passphrase()
            passphrase = self.service.keychain_passphrase()
            if not passphrase:
                return
            if self._auth_job is not None:
                self._auth_job.submit("passphrase", passphrase)
                self._auth_phase = "running"
                self._auth_login.setEnabled(False)
                self._auth_login.setText("Входим…")
                self._set_auth_status("Пароль связки отправлен.", MUTED)
                self._mark_auth_field(None)
                return
            self._reset_auth_buttons()
            self._sync_auth_entry()
            return
        if self._auth_phase == "running":
            return
        if "@" not in email:
            self._set_auth_status("Укажите email Apple ID, например name@icloud.com.", WARN)
            self._mark_auth_field("auth_email")
            return
        if not password:
            self._set_auth_status("Введите пароль. AppRestore его не сохраняет.", WARN)
            self._mark_auth_field("auth_password")
            return

        self._start_auth_job(email, password, code, passphrase, "Входим…")

    def _start_auth_job(
        self,
        email: str,
        password: str,
        code: str,
        passphrase: str,
        button_text: str,
    ) -> None:
        page = self.pages["account"]
        self._auth_phase = "running"
        self._auth_transcript.clear()
        self._auth_login.setEnabled(False)
        self._auth_login.setText(button_text)
        self._auth_cancel.show()
        for name in ("auth_email", "auth_password"):
            edit = page.findChild(QLineEdit, name)
            if edit is not None:
                edit.setEnabled(False)
        self._set_auth_status(button_text, MUTED)
        self.log("вход  запрос отправлен")
        job = AppleLogin(email, password, code, passphrase)
        job.output.connect(self._on_auth_output)
        job.status.connect(self._on_auth_status)
        job.need_input.connect(self._on_auth_need)
        job.done.connect(self._on_auth_done)
        self._auth_job = job
        job.start()

    def _on_auth_output(self, text: str) -> None:
        self._auth_transcript.insertPlainText(text)
        bar = self._auth_transcript.verticalScrollBar()
        bar.setValue(bar.maximum())

    def _on_auth_status(self, text: str) -> None:
        if self._auth_phase in ("need_code", "need_passphrase"):
            return
        self._set_auth_status(text, MUTED if self._auth_phase == "running" else WARN)

    def _on_auth_need(self, kind: str) -> None:
        if kind == "code":
            self._auth_phase = "need_code"
            self._set_code_field_visible(True)
            self._auth_login.setEnabled(True)
            self._auth_login.setText("Отправить код")
            self._mark_auth_field("auth_code")
            self._set_auth_status(
                "Apple просит код из сообщения. Введите его и нажмите «Отправить код».",
                WARN,
            )
            return
        if kind == "passphrase":
            secret = self.service.keychain_passphrase()
            if secret and self._auth_job is not None:
                self._auth_job.submit("passphrase", secret)
                self._auth_phase = "running"
                self._auth_login.setEnabled(False)
                self._auth_login.setText("Входим…")
                self._set_auth_status("Пароль связки отправлен.", MUTED)
                return
            self._auth_phase = "need_passphrase"
            self._auth_login.setEnabled(True)
            self._auth_login.setText("Открыть связку")
            self._set_auth_status(
                "Нужен пароль связки ключей. Это не пароль Apple ID.",
                WARN,
            )
            self._ask_keychain_passphrase()
            secret = self.service.keychain_passphrase()
            if secret and self._auth_job is not None:
                self._auth_job.submit("passphrase", secret)
                self._auth_phase = "running"
                self._auth_login.setEnabled(False)
                self._auth_login.setText("Входим…")
                self._set_auth_status("Пароль связки отправлен.", MUTED)
                self._mark_auth_field(None)

    def _on_auth_done(self, result: object) -> None:
        auth = result if isinstance(result, AuthResult) else AuthResult(False, "Вход завершился без ответа.")
        self._auth_job = None
        if not auth.ok and "связки" in auth.message:
            self._apple_session = "locked"
            self._apple_signed_in = False
            self._auth_phase = "need_passphrase"
            self._auth_login.setEnabled(True)
            self._auth_login.setText("Открыть связку")
            self._auth_cancel.hide()
            page = self.pages["account"]
            for name in ("auth_email", "auth_password", "auth_code"):
                edit = page.findChild(QLineEdit, name)
                if edit is not None:
                    edit.setEnabled(True)
            self._set_auth_status(auth.message, WARN)
            self.log("err  " + auth.message)
            if self._device is not None:
                self._apply_devices([self._device])
            else:
                self.refresh_overview_meta()
            return
        self._reset_auth_buttons()
        page = self.pages["account"]
        if auth.ok:
            self._apple_session = "in"
            self._apple_signed_in = True
            for name in ("auth_password", "auth_code"):
                edit = page.findChild(QLineEdit, name)
                if edit is not None:
                    edit.clear()
            self._set_code_field_visible(False)
            self._set_auth_status(auth.message, OK)
            self.log("ok  вход выполнен")
            self._continue_pending_file_install()
            if self._device is not None:
                self._apply_devices([self._device])
            else:
                self.refresh_overview_meta()
            return
        self._set_auth_status(auth.message, BAD)
        self.log("err  " + auth.message)

    def _cancel_login(self) -> None:
        job = self._auth_job
        if job is not None:
            job.cancel()
        self._set_auth_status("Останавливаем вход…", MUTED)

    def _revoke(self) -> None:
        if self._auth_job is not None:
            self._auth_job.cancel()
        try:
            self.service.revoke()
        except Exception as exc:
            self._set_auth_status(str(exc), BAD)
            self.log("err  " + str(exc))
            return
        self._apple_signed_in = False
        self._apple_session = "out"
        self.service.clear_keychain_passphrase()
        self._reset_auth_buttons()
        self._set_auth_status("Сессия Apple ID завершена.", MUTED)
        self.log("ok  выход выполнен")
        if self._device is not None:
            self._apply_devices([self._device])
        else:
            self.refresh_overview_meta()

    def closeEvent(self, event) -> None:  # noqa: N802 - Qt naming
        if self._auth_job is not None:
            self._auth_job.cancel()
        super().closeEvent(event)

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
        meta.setText(f"Версия {APP_VERSION}")
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
        check = QPushButton("Проверить обновления")
        check.setObjectName("checkUpdates")
        check.clicked.connect(self.check_updates)
        self.update_check_button = check
        row = QHBoxLayout()
        row.addWidget(check)
        row.addStretch(1)
        row.addWidget(save)
        layout.addLayout(row)
        self.update_status = QLabel("")
        self.update_status.setWordWrap(True)
        self.update_status.setStyleSheet(f"color:{MUTED};font-size:12px;font-weight:400;border:none;")
        layout.addWidget(self.update_status)
        layout.addStretch(1)
        return page

    # ----- updates -------------------------------------------------------
    def check_updates(self) -> None:
        from apprestore_gui import updater

        self.update_check_button.setEnabled(False)
        self.update_status.setText("Проверяю новую версию на GitHub…")
        self._update_thread = run_in_thread(
            self,
            updater.check_for_update,
            on_finished=self._on_update_checked,
            on_failed=self._on_update_check_failed,
        )

    def _on_update_check_failed(self, message: str) -> None:
        self.update_check_button.setEnabled(True)
        self.update_status.setText(f"Не удалось проверить обновления: {message}")

    def _on_update_checked(self, info: Any) -> None:
        self.update_check_button.setEnabled(True)
        if not info.newer:
            self.update_status.setText(
                f"У вас последняя версия ({info.current}). На GitHub сейчас {info.latest}."
            )
            return
        self.update_status.setText(f"Доступна версия {info.latest}.")
        self.show_update_dialog(info)

    def show_update_dialog(self, info: Any) -> None:
        from apprestore_gui.update_dialog import UpdateDialog

        self._update_dialog = UpdateDialog(info, self)
        self._update_dialog.open()
