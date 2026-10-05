from __future__ import annotations

from PySide6.QtCore import QRectF, Qt, QSize
from PySide6.QtGui import QColor, QPainter, QPainterPath, QPen, QPixmap
from PySide6.QtWidgets import QWidget


class PhoneWidget(QWidget):
    """Realistic phone frame with a home-screen grid of real app icons."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setMinimumSize(180, 360)
        self.setMaximumWidth(220)
        self._icons: list[tuple[QPixmap, bool]] = []

    def set_icons(self, icons: list[tuple[QPixmap, bool]]) -> None:
        self._icons = list(icons)
        self.update()

    def sizeHint(self) -> QSize:  # noqa: N802
        return QSize(200, 400)

    def paintEvent(self, _event) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
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

        cols = 4
        rows = 4
        pad_x = 14.0
        grid_top = inner.top() + 40
        avail_w = inner.width() - 2 * pad_x
        gap = 8.0
        size = (avail_w - gap * (cols - 1)) / cols
        left = inner.left() + pad_x

        icons = self._icons[: cols * rows]
        # Fill remaining slots with subtle empty squircles if fewer icons.
        while len(icons) < cols * rows:
            empty = QPixmap(int(size), int(size))
            empty.fill(Qt.GlobalColor.transparent)
            icons.append((empty, False))

        for i, (pix, offloaded) in enumerate(icons):
            row, col = divmod(i, cols)
            x = left + col * (size + gap)
            y = grid_top + row * (size + gap + 6)
            target = QRectF(x, y, size, size)
            if pix.isNull() or pix.width() == 0:
                p.setBrush(QColor("#D8D8DC"))
                p.setPen(Qt.PenStyle.NoPen)
                p.drawRoundedRect(target, size * 0.22, size * 0.22)
            else:
                scaled = pix.scaled(
                    int(size),
                    int(size),
                    Qt.AspectRatioMode.IgnoreAspectRatio,
                    Qt.TransformationMode.SmoothTransformation,
                )
                clip = QPainterPath()
                clip.addRoundedRect(target, size * 0.22, size * 0.22)
                p.save()
                p.setClipPath(clip)
                p.drawPixmap(target.toRect(), scaled)
                p.restore()
            if offloaded and not pix.isNull() and pix.width() > 0:
                badge = QRectF(x + size - 12, y + size - 12, 13, 13)
                p.setBrush(QColor(255, 255, 255, 235))
                p.setPen(Qt.PenStyle.NoPen)
                p.drawEllipse(badge)
                p.setPen(QPen(QColor("#6C6C70"), 1.3))
                # tiny cloud glyph
                cx, cy = badge.center().x(), badge.center().y()
                p.drawArc(QRectF(cx - 4, cy - 3.5, 7, 5), 20 * 16, 200 * 16)
                p.drawLine(int(cx - 3), int(cy + 1), int(cx + 3), int(cy + 1))
                p.setPen(Qt.PenStyle.NoPen)

        # home indicator
        p.setBrush(QColor(28, 28, 30, 60))
        p.drawRoundedRect(
            QRectF(inner.center().x() - 28, inner.bottom() - 14, 56, 4), 2, 2
        )
        p.end()
