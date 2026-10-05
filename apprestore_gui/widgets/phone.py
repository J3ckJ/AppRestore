from __future__ import annotations

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QWidget


class PhoneWidget(QWidget):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setMinimumSize(180, 360)
        self.setMaximumWidth(220)

    def paintEvent(self, _event) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        w, h = self.width(), self.height()
        margin = 8
        frame = QRectF(margin, margin, w - 2 * margin, h - 2 * margin)
        path = QPainterPath()
        path.addRoundedRect(frame, 36, 36)
        p.fillPath(path, QColor("#1C1C1E"))
        inner = frame.adjusted(6, 6, -6, -6)
        ip = QPainterPath()
        ip.addRoundedRect(inner, 30, 30)
        p.fillPath(ip, QColor("#F2F2F7"))
        # Dynamic Island
        island = QRectF(inner.center().x() - 32, inner.top() + 10, 64, 18)
        p.setBrush(QColor("#0A0A0B"))
        p.setPen(Qt.PenStyle.NoPen)
        p.drawRoundedRect(island, 9, 9)
        # Fake app grid
        colors = [
            "#8E8E93",
            "#34C759",
            "#E8873A",
            "#636366",
            "#AF52DE",
            "#007AFF",
            "#30B0C7",
            "#FF2D55",
            "#FFCC00",
            "#34C759",
            "#1C7ED6",
            "#5856D6",
        ]
        grid_top = inner.top() + 44
        left = inner.left() + 16
        size = 28
        gap = 10
        for i, color in enumerate(colors):
            row, col = divmod(i, 4)
            x = left + col * (size + gap)
            y = grid_top + row * (size + gap + 8)
            p.setBrush(QColor(color))
            p.drawRoundedRect(QRectF(x, y, size, size), 7, 7)
            if i in {1, 2, 4, 6}:
                p.setBrush(QColor("#FFFFFF"))
                p.drawEllipse(QRectF(x + size - 10, y + size - 10, 11, 11))
                p.setPen(QPen(QColor(color), 1.4))
                p.drawArc(QRectF(x + size - 8, y + size - 9, 8, 6), 30 * 16, 200 * 16)
                p.setPen(Qt.PenStyle.NoPen)
        # home indicator
        p.setBrush(QColor(28, 28, 30, 60))
        p.drawRoundedRect(
            QRectF(inner.center().x() - 28, inner.bottom() - 14, 56, 4), 2, 2
        )
        p.end()
