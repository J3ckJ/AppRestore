"""Visual tokens matching gui-plan/v5."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QFont, QFontDatabase
from PySide6.QtWidgets import QApplication

BG = "#E8E8ED"
PANEL = "#FFFFFF"
SIDE = "#1F1F23"
SIDE_2 = "#2A2A2F"
INK = "#1C1C1E"
MUTED = "#6C6C70"
LINE = "#D8D8DC"
ACCENT = "#C45C2A"
ACCENT_HOVER = "#A84C22"
ACCENT_SOFT = "#F8E8DF"
OK = "#1B7F3A"
WARN = "#9A6700"
BAD = "#C0392B"
SIDE_TEXT = "#D1D1D6"
SIDE_MUTED = "#8E8E93"


def resources_dir() -> Path:
    return Path(__file__).resolve().parent / "resources"


def load_fonts() -> str:
    """Register Onest and return the family name to use."""
    fonts = resources_dir() / "fonts"
    family = "Onest"
    for path in sorted(fonts.glob("Onest*.ttf")) + sorted(fonts.glob("Onest*.otf")):
        fid = QFontDatabase.addApplicationFont(str(path))
        if fid >= 0:
            families = QFontDatabase.applicationFontFamilies(fid)
            if families:
                family = families[0]
    return family


def pin_light_native_chrome(app: QApplication) -> None:
    """Keep system menus light, matching the window.

    macOS draws the right-click menu in the system appearance. A dark menu
    plus the dark text from the application stylesheet is unreadable.
    """

    app.styleHints().setColorScheme(Qt.ColorScheme.Light)


def app_font(family: str, size: int = 13, weight: int = QFont.Weight.Normal) -> QFont:
    font = QFont(family)
    font.setPixelSize(size)
    font.setWeight(weight)
    font.setStyleHint(QFont.StyleHint.SansSerif)
    font.setHintingPreference(QFont.HintingPreference.PreferFullHinting)
    return font


STYLESHEET = f"""
QMainWindow, QDialog {{
  background: {BG};
  color: {INK};
}}
QWidget {{
  color: {INK};
}}
QMenu {{
  background: {PANEL};
  color: {INK};
  border: 1px solid {LINE};
}}
QMenu::item {{
  background: transparent;
  color: {INK};
  padding: 4px 24px;
}}
QMenu::item:selected {{
  background: {BG};
  color: {INK};
}}
QMenu::item:disabled {{
  color: {MUTED};
}}
QLabel {{
  border: none;
  background: transparent;
}}
QScrollArea {{
  border: none;
  background: transparent;
}}
QLineEdit, QComboBox {{
  background: {PANEL};
  border: 1px solid {LINE};
  border-radius: 6px;
  padding: 4px 8px;
  min-height: 28px;
  selection-background-color: {ACCENT};
}}
QLineEdit:focus, QComboBox:focus {{
  border: 1px solid {ACCENT};
}}
QPushButton {{
  background: {PANEL};
  border: 1px solid {LINE};
  border-radius: 6px;
  padding: 6px 12px;
  min-height: 28px;
  font-weight: 600;
}}
QPushButton:hover {{
  background: #F7F7F8;
}}
QPushButton:disabled {{
  color: {MUTED};
  background: #F0F0F2;
}}
QPushButton#primary {{
  background: {ACCENT};
  border: 1px solid {ACCENT};
  color: white;
}}
QPushButton#primary:hover {{
  background: {ACCENT_HOVER};
  border-color: {ACCENT_HOVER};
}}
QPushButton#primary:disabled {{
  background: #E4E4E7;
  border-color: #E4E4E7;
  color: {MUTED};
}}
QPushButton#danger {{
  color: {BAD};
  border-color: #E5B4AE;
}}
QTableWidget {{
  background: {PANEL};
  border: 1px solid {LINE};
  border-radius: 8px;
  gridline-color: #EBEBEF;
  selection-background-color: {ACCENT_SOFT};
  selection-color: {INK};
}}
QHeaderView::section {{
  background: #F7F7F9;
  color: {MUTED};
  border: none;
  border-bottom: 1px solid {LINE};
  padding: 8px 12px;
  font-weight: 600;
}}
QCheckBox {{
  spacing: 8px;
}}
QCheckBox::indicator {{
  width: 15px;
  height: 15px;
}}
QProgressBar {{
  border: 1px solid {LINE};
  border-radius: 4px;
  background: #F0F0F2;
  text-align: center;
  max-height: 8px;
}}
QProgressBar::chunk {{
  background: {ACCENT};
  border-radius: 3px;
}}
QTextEdit#log {{
  background: #1F1F23;
  color: #D1D1D6;
  border-radius: 8px;
  border: none;
  font-family: ui-monospace, SF Mono, Menlo, Consolas, monospace;
  font-size: 12px;
}}
"""
