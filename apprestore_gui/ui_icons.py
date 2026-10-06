"""Render Lucide-style and v5 tile SVGs shipped under resources/icons."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from PySide6.QtCore import QByteArray, QRectF, Qt, QSize
from PySide6.QtGui import QIcon, QImage, QPainter, QPixmap, QColor
from PySide6.QtSvg import QSvgRenderer

from apprestore_gui.theme import ACCENT, resources_dir


def icons_dir() -> Path:
    return resources_dir() / "icons"


@lru_cache(maxsize=64)
def _svg_bytes(name: str) -> bytes:
    path = icons_dir() / f"{name}.svg"
    return path.read_bytes()


def svg_pixmap(name: str, size: int = 16, *, color: str | None = None) -> QPixmap:
    data = _svg_bytes(name)
    if color:
        text = data.decode("utf-8")
        # Replace default stroke/fill greys used in nav icons.
        text = text.replace("#D1D1D6", color).replace("currentColor", color)
        data = text.encode("utf-8")
    renderer = QSvgRenderer(QByteArray(data))
    image = QImage(size, size, QImage.Format.Format_ARGB32_Premultiplied)
    image.fill(Qt.GlobalColor.transparent)
    painter = QPainter(image)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    renderer.render(painter, QRectF(0, 0, size, size))
    painter.end()
    return QPixmap.fromImage(image)


def svg_icon(name: str, size: int = 16, *, color: str | None = None) -> QIcon:
    return QIcon(svg_pixmap(name, size, color=color))


def logo_mark_pixmap(size: int = 28) -> QPixmap:
    """Terracotta rounded square with white phone+download glyph."""
    out = QPixmap(size, size)
    out.fill(Qt.GlobalColor.transparent)
    painter = QPainter(out)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    painter.setBrush(QColor(ACCENT))
    painter.setPen(Qt.PenStyle.NoPen)
    painter.drawRoundedRect(0, 0, size, size, 7, 7)
    glyph = svg_pixmap("logo-mark", max(12, int(size * 0.58)))
    x = (size - glyph.width()) // 2
    y = (size - glyph.height()) // 2
    painter.drawPixmap(x, y, glyph)
    painter.end()
    return out


NAV_ICON_NAMES = {
    "overview": "layout-grid",
    "offloaded": "cloud",
    "install": "plus",
    "library": "folder",
    "doctor": "key",
    "account": "user",
    "log": "list",
    "settings": "settings",
}

TILE_ICON_NAMES = {
    "offloaded": "tile-offloaded",
    "install": "tile-install",
    "library": "tile-library",
    "account": "tile-account",
    "doctor": "tile-doctor",
    "log": "tile-log",
}


def app_icon() -> "QIcon":
    """Application/window icon rendered by packaging/make_icons.py."""

    from PySide6.QtGui import QIcon

    from apprestore_gui.theme import resources_dir

    icon = QIcon()
    for size in (64, 256):
        path = resources_dir() / "icons" / f"app-icon-{size}.png"
        if path.exists():
            icon.addFile(str(path))
    return icon
