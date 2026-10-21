"""Thèmes sombre (par défaut) et clair : palette neutre, un seul accent désaturé."""
from __future__ import annotations

from PySide6.QtGui import QColor, QFont, QPalette
from PySide6.QtWidgets import QApplication

DARK = {
    "bg": "#141517", "surface": "#1B1C1F", "raised": "#222327", "hover": "#2A2B30",
    "border": "#2E3035", "text": "#E4E5E7", "muted": "#8E9198", "faint": "#5D6067",
    "accent": "#7C9CC4", "accent_text": "#0F1115", "danger": "#D07A72", "ok": "#7FB08A", "warn": "#CDB06E",
}
LIGHT = {
    "bg": "#F4F4F2", "surface": "#FFFFFF", "raised": "#F9F9F8", "hover": "#ECECEA",
    "border": "#DCDCD8", "text": "#1E1F22", "muted": "#6A6D73", "faint": "#A0A3A8",
    "accent": "#4A6E9A", "accent_text": "#FFFFFF", "danger": "#B4544B", "ok": "#3F7D51", "warn": "#9A7B2C",
}

current: dict[str, str] = dict(DARK)


def qss(c: dict[str, str]) -> str:
    return f"""
* {{ outline: none; }}
QWidget {{ background: {c['bg']}; color: {c['text']}; font-size: 13px; }}
QToolTip {{ background: {c['raised']}; color: {c['text']}; border: 1px solid {c['border']}; padding: 6px; }}
QLabel {{ background: transparent; }}
QLabel[role="h1"] {{ font-size: 22px; font-weight: 600; }}
QLabel[role="h2"] {{ font-size: 15px; font-weight: 600; }}
QLabel[role="muted"] {{ color: {c['muted']}; }}
QLabel[role="faint"] {{ color: {c['faint']}; font-size: 12px; }}
QLabel[role="badge"] {{ color: {c['muted']}; border: 1px solid {c['border']}; border-radius: 9px; padding: 1px 8px; font-size: 11px; }}
QLabel[role="error"] {{ color: {c['danger']}; }}
QFrame[role="card"] {{ background: {c['surface']}; border: 1px solid {c['border']}; border-radius: 10px; }}
QFrame[role="card"]:hover[clickable="true"] {{ border-color: {c['faint']}; }}
QFrame[role="sidebar"] {{ background: {c['surface']}; border-right: 1px solid {c['border']}; }}
QFrame[role="dropzone"] {{ border: 1px dashed {c['faint']}; border-radius: 10px; background: {c['surface']}; }}
QFrame[role="dropzone"][active="true"] {{ border-color: {c['accent']}; }}
QPushButton {{ background: {c['raised']}; border: 1px solid {c['border']}; border-radius: 7px; padding: 6px 14px; }}
QPushButton:hover {{ background: {c['hover']}; }}
QPushButton:pressed {{ background: {c['border']}; }}
QPushButton:disabled {{ color: {c['faint']}; background: {c['surface']}; }}
QPushButton[kind="primary"] {{ background: {c['accent']}; color: {c['accent_text']}; border: none; font-weight: 600; }}
QPushButton[kind="primary"]:hover {{ background: {c['accent']}; border: 1px solid {c['text']}; }}
QPushButton[kind="primary"]:disabled {{ background: {c['raised']}; color: {c['faint']}; font-weight: 500; }}
QPushButton[kind="danger"] {{ color: {c['danger']}; }}
QPushButton[kind="ghost"] {{ background: transparent; border: none; color: {c['muted']}; }}
QPushButton[kind="ghost"]:hover {{ color: {c['text']}; }}
QPushButton[kind="nav"] {{ background: transparent; border: none; border-radius: 7px; text-align: left; padding: 9px 12px; color: {c['muted']}; font-size: 13px; }}
QPushButton[kind="nav"]:hover {{ background: {c['hover']}; color: {c['text']}; }}
QPushButton[kind="nav"]:checked {{ background: {c['raised']}; color: {c['text']}; font-weight: 600; }}
QLineEdit, QPlainTextEdit, QTextEdit, QComboBox, QSpinBox, QDoubleSpinBox, QDateTimeEdit {{
    background: {c['surface']}; border: 1px solid {c['border']}; border-radius: 7px; padding: 5px 8px;
    selection-background-color: {c['accent']}; selection-color: {c['accent_text']}; }}
QLineEdit:focus, QPlainTextEdit:focus, QComboBox:focus {{ border-color: {c['accent']}; }}
QComboBox::drop-down {{ border: none; width: 22px; }}
QComboBox QAbstractItemView {{ background: {c['raised']}; border: 1px solid {c['border']}; selection-background-color: {c['hover']}; }}
QTableView, QTreeView, QListView {{ background: {c['surface']}; border: 1px solid {c['border']}; border-radius: 8px;
    gridline-color: {c['border']}; alternate-background-color: {c['raised']}; selection-background-color: {c['hover']}; selection-color: {c['text']}; }}
QHeaderView::section {{ background: {c['surface']}; color: {c['muted']}; border: none; border-bottom: 1px solid {c['border']}; padding: 6px 8px; font-weight: 500; }}
QTabWidget::pane {{ border: none; }}
QTabBar::tab {{ background: transparent; color: {c['muted']}; padding: 8px 14px; border-bottom: 2px solid transparent; }}
QTabBar::tab:selected {{ color: {c['text']}; border-bottom-color: {c['accent']}; }}
QTabBar::tab:hover {{ color: {c['text']}; }}
QScrollBar:vertical {{ background: transparent; width: 10px; margin: 2px; }}
QScrollBar::handle:vertical {{ background: {c['border']}; border-radius: 4px; min-height: 30px; }}
QScrollBar:horizontal {{ background: transparent; height: 10px; margin: 2px; }}
QScrollBar::handle:horizontal {{ background: {c['border']}; border-radius: 4px; min-width: 30px; }}
QScrollBar::add-line, QScrollBar::sub-line {{ width: 0; height: 0; }}
QProgressBar {{ background: {c['raised']}; border: none; border-radius: 3px; height: 6px; text-align: center; color: transparent; }}
QProgressBar::chunk {{ background: {c['accent']}; border-radius: 3px; }}
QSlider::groove:horizontal {{ height: 4px; background: {c['border']}; border-radius: 2px; }}
QSlider::sub-page:horizontal {{ background: {c['accent']}; border-radius: 2px; }}
QSlider::handle:horizontal {{ background: {c['text']}; width: 12px; height: 12px; margin: -4px 0; border-radius: 6px; }}
QCheckBox {{ background: transparent; spacing: 8px; }}
QCheckBox::indicator {{ width: 15px; height: 15px; border: 1px solid {c['faint']}; border-radius: 4px; background: {c['surface']}; }}
QCheckBox::indicator:checked {{ background: {c['accent']}; border-color: {c['accent']}; }}
QCheckBox::indicator:disabled {{ border-color: {c['border']}; background: {c['bg']}; }}
QSplitter::handle {{ background: {c['border']}; }}
QStatusBar {{ background: {c['surface']}; border-top: 1px solid {c['border']}; color: {c['muted']}; }}
QMenu {{ background: {c['raised']}; border: 1px solid {c['border']}; padding: 4px; }}
QMenu::item {{ padding: 6px 18px; border-radius: 5px; }}
QMenu::item:selected {{ background: {c['hover']}; }}
QDialog {{ background: {c['bg']}; }}
"""


def apply(app: QApplication, mode: str = "dark") -> None:
    global current
    current = dict(LIGHT if mode == "light" else DARK)
    app.setStyle("Fusion")
    pal = QPalette()
    for role, key in ((QPalette.Window, "bg"), (QPalette.Base, "surface"), (QPalette.AlternateBase, "raised"),
                      (QPalette.Text, "text"), (QPalette.WindowText, "text"), (QPalette.Button, "raised"),
                      (QPalette.ButtonText, "text"), (QPalette.Highlight, "accent"),
                      (QPalette.HighlightedText, "accent_text"), (QPalette.PlaceholderText, "faint"),
                      (QPalette.ToolTipBase, "raised"), (QPalette.ToolTipText, "text")):
        pal.setColor(role, QColor(current[key]))
    app.setPalette(pal)
    f = QFont("Segoe UI Variable Text")
    f.setFamilies(["Segoe UI Variable Text", "Segoe UI", "Inter", "Noto Sans", "sans-serif"])
    f.setPointSizeF(9.75)
    app.setFont(f)
    app.setStyleSheet(qss(current))


def color(key: str) -> str:
    return current[key]
