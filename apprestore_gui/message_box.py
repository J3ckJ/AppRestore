"""Error dialogs that wrap a sentence instead of stretching the window."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QLabel, QMessageBox, QWidget

from apprestore_gui.errors import explain_user_error


def show_message(parent: QWidget | None, title: str, text: str) -> None:
    box = QMessageBox(parent)
    box.setIcon(QMessageBox.Icon.Warning)
    box.setWindowTitle(title)
    box.setText(text)
    box.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
    for label in box.findChildren(QLabel):
        if label.text() == text:
            label.setWordWrap(True)
            label.setFixedWidth(420)
    box.exec()


def show_error(parent: QWidget | None, title: str, message: str) -> None:
    show_message(parent, title, explain_user_error(message))
