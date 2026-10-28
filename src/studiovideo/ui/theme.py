"""Thème : clair crème doux (par défaut) ou sombre doux ; une couleur pastel par parcours ; icônes Lucide.

Les couleurs sont des jetons (bg, surface, raised…) : les widgets dessinés à la main lisent `current`.
"""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from PySide6.QtCore import QByteArray, QRectF, QSize, Qt
from PySide6.QtGui import QColor, QFont, QIcon, QPainter, QPalette, QPixmap
from PySide6.QtWidgets import QApplication

ICONS = Path(__file__).parent / "icons"
CHECK = (ICONS / "_check-on.svg").as_posix()

LIGHT = {
    "bg": "#F4F0E9", "surface": "#FBF9F5", "raised": "#FFFFFF", "hover": "#EDE7DC", "sunken": "#EBE5DA",
    "border": "#E3DCD0", "text": "#2C2A27", "muted": "#7A746A", "faint": "#ADA698",
    "accent": "#2E7D74", "accent_text": "#FFFFFF", "accent_soft": "#DCEDEA",
    "danger": "#C8574D", "ok": "#3E8E5E", "warn": "#C08A2E", "shadow": "#D9D1C4",
}
DARK = {
    "bg": "#1D1E21", "surface": "#24262A", "raised": "#2B2D32", "hover": "#33363C", "sunken": "#191A1D",
    "border": "#383B42", "text": "#ECEAE6", "muted": "#A19D96", "faint": "#6E6B66",
    "accent": "#5CC2B4", "accent_text": "#14231F", "accent_soft": "#26403C",
    "danger": "#E08078", "ok": "#7FC79A", "warn": "#E2B75E", "shadow": "#121315",
}

# Parcours de montage : libellé, icône, couleur (pastel vive), description courte (infobulle)
PARCOURS = {
    "free_edit": ("Montage libre", "clapperboard", "#4F8EF7", "Monter librement une vidéo"),
    "smart_clipping": ("Clipping", "sparkles", "#D35BC8", "Trouver et découper les meilleurs extraits"),
    "translation": ("Traduction", "languages", "#26A69A", "Sous-titrer, traduire et doubler"),
    "original": ("Création originale", "wand-sparkles", "#F0894A", "Créer une vidéo à partir d'un texte"),
    "texts": ("Textes", "type", "#8E6CEF", "Textes à l'écran, sous-titres et leur mise en forme"),
}

# Parcours dont l'icône est grisée dans le dock (toujours utilisables)
DIMMED = {"original"}

# Dernier onglet du dock : l'export (titre, description, réseaux, publication) de la vidéo du projet
EXPORT_TAB = ("Export", "share-2", "#E5484D", "Titre, description, réseaux et publication")

# Format d'un nouveau projet selon le parcours (modifiable ensuite en haut du Studio)
DEFAULT_ASPECT = {"free_edit": "9:16", "smart_clipping": "9:16", "translation": "16:9", "original": "9:16",
                  "texts": "9:16"}

PLATFORMS = {"youtube": ("youtube", "#FF2D2D"), "tiktok": ("music", "#FE2C55"), "instagram": ("instagram", "#E1306C"),
             "facebook": ("facebook", "#1877F2"), "x": ("twitter", None),
             "meta": ("infinity", "#0866FF")}


def platform_icon(platform: str, size: int = 18) -> QIcon:
    ic, col = PLATFORMS.get(platform, ("globe", None))
    return icon(ic, col or current["text"], size)


mode_name = "light"
current: dict[str, str] = dict(LIGHT)
_accent = LIGHT["accent"]


def _mix(a: str, b: str, t: float) -> str:
    ca, cb = QColor(a), QColor(b)
    return QColor(round(ca.red() + (cb.red() - ca.red()) * t), round(ca.green() + (cb.green() - ca.green()) * t),
                  round(ca.blue() + (cb.blue() - ca.blue()) * t)).name()


def tinted_svg(name: str, color_hex: str) -> str:
    """Copie colorée d'une icône, en fichier (les feuilles de style Qt ne lisent qu'une image sur disque)."""
    import tempfile
    folder = Path(tempfile.gettempdir()) / "studiovideo-icons"
    out = folder / f"{name}-{color_hex.lstrip('#')}.svg"
    if not out.exists():
        try:
            folder.mkdir(parents=True, exist_ok=True)
            out.write_text(_svg(name).replace("currentColor", color_hex), encoding="utf-8")
        except OSError:
            return ""
    return out.as_posix()


def qss(c: dict[str, str]) -> str:
    arrow = tinted_svg("chevron-down", c["muted"])
    return f"""
* {{ outline: none; }}
QWidget {{ background: {c['bg']}; color: {c['text']}; font-size: 13px; }}
QToolTip {{ background: {c['raised']}; color: {c['text']}; border: 1px solid {c['accent']}; padding: 5px 9px; }}
QLabel {{ background: transparent; }}
QLabel[role="h1"] {{ font-size: 24px; font-weight: 600; }}
QLabel[role="h2"] {{ font-size: 15px; font-weight: 600; }}
QLabel[role="muted"] {{ color: {c['muted']}; }}
QLabel[role="faint"] {{ color: {c['faint']}; font-size: 12px; }}
QLabel[role="badge"] {{ color: {c['accent']}; background: {c['accent_soft']}; border-radius: 9px; padding: 2px 9px; font-size: 11px; font-weight: 600; }}
QLabel[role="error"] {{ color: {c['danger']}; }}
QLabel[role="bubble"] {{ background: transparent; color: {c['text']}; padding: 6px 12px; font-size: 12px; font-weight: 500; }}
QFrame[role="card"] {{ background: {c['raised']}; border: 1px solid {c['border']}; border-radius: 16px; }}
QFrame[role="card"]:hover[clickable="true"] {{ border-color: {c['faint']}; }}
QFrame[role="dim"] {{ background: transparent; }}
QFrame[role="dock"] {{ background: {c['raised']}; border: 1px solid {c['border']}; border-radius: 20px; }}
QFrame[role="drawer"] {{ background: {c['raised']}; border: 1px solid {c['border']}; border-radius: 18px; }}
QFrame[role="popover"] {{ background: {c['raised']}; border: 1px solid {c['border']}; border-radius: 16px; }}
QFrame[role="sidebar"] {{ background: {c['bg']}; border: none; }}
QFrame[role="dropzone"] {{ border: 2px dashed {c['border']}; border-radius: 16px; background: {c['surface']}; }}
QFrame[role="dropzone"][active="true"] {{ border-color: {c['accent']}; background: {c['accent_soft']}; }}
QFrame[role="divider"] {{ background: {c['border']}; border: none; }}
QPushButton {{ background: {c['raised']}; border: 1px solid {c['border']}; border-radius: 10px; padding: 7px 16px; font-weight: 500; }}
QPushButton:hover {{ background: {c['hover']}; border-color: {c['faint']}; }}
QPushButton:pressed {{ background: {c['sunken']}; }}
QPushButton:disabled {{ color: {c['faint']}; background: {c['surface']}; border-color: {c['border']}; }}
QPushButton[kind="primary"] {{ background: {c['accent']}; color: {c['accent_text']}; border: none; font-weight: 600; padding: 8px 18px; }}
QPushButton[kind="primary"]:hover {{ background: {_mix(c['accent'], c['text'], 0.12)}; }}
QPushButton[kind="primary"]:pressed {{ background: {_mix(c['accent'], c['text'], 0.25)}; }}
QPushButton[kind="primary"]:disabled {{ background: {c['hover']}; color: {c['faint']}; }}
QPushButton[kind="soft"] {{ background: {c['accent_soft']}; color: {c['accent']}; border: none; font-weight: 600; }}
QPushButton[kind="soft"]:hover {{ background: {_mix(c['accent_soft'], c['accent'], 0.15)}; }}
QPushButton[kind="soft"]:disabled {{ background: {c['surface']}; color: {c['faint']}; }}
QPushButton[kind="danger"] {{ color: {c['danger']}; }}
QPushButton[kind="ghost"] {{ background: transparent; border: none; color: {c['muted']}; padding: 6px 10px; }}
QPushButton[kind="ghost"]:hover {{ color: {c['text']}; background: {c['hover']}; }}
QPushButton[kind="ghost"]:disabled {{ color: {c['faint']}; background: transparent; }}
QPushButton[kind="nav"] {{ background: transparent; border: none; border-radius: 10px; text-align: left; padding: 9px 12px; color: {c['muted']}; font-size: 13px; }}
QPushButton[kind="nav"]:hover {{ background: {c['hover']}; color: {c['text']}; }}
QPushButton[kind="nav"]:checked {{ background: {c['accent_soft']}; color: {c['accent']}; font-weight: 600; }}
QPushButton[kind="pill"] {{ background: {c['surface']}; border: 1px solid {c['border']}; border-radius: 14px; padding: 5px 14px; color: {c['muted']}; }}
QPushButton[kind="pill"]:checked {{ background: {c['accent']}; border-color: {c['accent']}; color: {c['accent_text']}; font-weight: 600; }}
QToolButton {{ background: transparent; border: none; border-radius: 12px; padding: 6px; }}
QToolButton:hover {{ background: {c['hover']}; }}
QToolButton:checked {{ background: {c['accent_soft']}; }}
QToolButton[kind="export"] {{ background: {EXPORT_TAB[2]}; border-radius: 10px; }}
QToolButton[kind="export"]:hover {{ background: {_mix(EXPORT_TAB[2], '#000000', 0.12)}; }}
QToolButton[kind="export"]:disabled {{ background: {_mix(c['raised'], EXPORT_TAB[2], 0.35)}; }}
QToolButton[kind="soft"] {{ background: {c['accent_soft']}; border-radius: 10px; }}
QToolButton[kind="soft"]:hover {{ background: {_mix(c['accent_soft'], c['accent'], 0.15)}; }}
QLineEdit, QPlainTextEdit, QTextEdit, QComboBox, QFontComboBox, QSpinBox, QDoubleSpinBox, QDateTimeEdit, QDateEdit, QTimeEdit {{
    background: {c['raised']}; border: 1px solid {c['border']}; border-radius: 10px; padding: 6px 10px;
    selection-background-color: {c['accent']}; selection-color: {c['accent_text']}; }}
QLineEdit:hover, QComboBox:hover, QSpinBox:hover, QDoubleSpinBox:hover, QDateTimeEdit:hover {{ border-color: {c['faint']}; }}
QLineEdit:focus, QPlainTextEdit:focus, QTextEdit:focus, QComboBox:focus, QSpinBox:focus, QDoubleSpinBox:focus, QDateTimeEdit:focus {{ border: 1px solid {c['accent']}; }}
QLineEdit:disabled, QComboBox:disabled, QSpinBox:disabled, QDoubleSpinBox:disabled {{ color: {c['faint']}; background: {c['surface']}; }}
QComboBox::drop-down, QDateTimeEdit::drop-down {{ border: none; width: 26px; }}
QComboBox::down-arrow, QDateTimeEdit::down-arrow {{ image: url({arrow}); width: 14px; height: 14px; }}
QSpinBox::up-button, QSpinBox::down-button, QDoubleSpinBox::up-button, QDoubleSpinBox::down-button {{ border: none; width: 16px; background: transparent; }}
QComboBox QAbstractItemView {{ background: {c['raised']}; border: 1px solid {c['border']}; border-radius: 10px; padding: 4px;
    selection-background-color: {c['accent_soft']}; selection-color: {c['text']}; }}
QTableView, QTreeView, QListView, QTableWidget, QListWidget {{ background: {c['raised']}; border: 1px solid {c['border']}; border-radius: 12px;
    gridline-color: {c['border']}; alternate-background-color: {c['surface']}; selection-background-color: {c['accent_soft']}; selection-color: {c['text']}; }}
QListView::item, QListWidget::item {{ border-radius: 8px; padding: 4px; }}
QListView::item:hover, QListWidget::item:hover {{ background: {c['hover']}; }}
QListView::item:selected, QListWidget::item:selected {{ background: {c['accent_soft']}; color: {c['text']}; }}
QHeaderView {{ background: transparent; }}
QHeaderView::section {{ background: {c['raised']}; color: {c['muted']}; border: none; border-bottom: 1px solid {c['border']}; padding: 7px 8px; font-weight: 600; font-size: 12px; }}
QTableCornerButton::section {{ background: {c['raised']}; border: none; }}
QTabWidget::pane {{ border: none; }}
QTabBar {{ background: transparent; }}
QTabBar::tab {{ background: transparent; color: {c['muted']}; padding: 7px 14px; margin: 2px 2px 6px 0; border-radius: 10px; font-weight: 500; }}
QTabBar::tab:selected {{ background: {c['raised']}; color: {c['text']}; font-weight: 600; border: 1px solid {c['border']}; }}
QTabBar::tab:hover:!selected {{ color: {c['text']}; background: {c['hover']}; }}
QScrollArea {{ border: none; background: transparent; }}
QScrollArea > QWidget > QWidget {{ background: transparent; }}
QScrollBar:vertical {{ background: transparent; width: 10px; margin: 3px; }}
QScrollBar::handle:vertical {{ background: {c['border']}; border-radius: 4px; min-height: 30px; }}
QScrollBar::handle:vertical:hover {{ background: {c['faint']}; }}
QScrollBar:horizontal {{ background: transparent; height: 10px; margin: 3px; }}
QScrollBar::handle:horizontal {{ background: {c['border']}; border-radius: 4px; min-width: 30px; }}
QScrollBar::add-line, QScrollBar::sub-line, QScrollBar::add-page, QScrollBar::sub-page {{ width: 0; height: 0; background: transparent; }}
QProgressBar {{ background: {c['sunken']}; border: none; border-radius: 4px; height: 8px; max-height: 8px; text-align: center; color: transparent; }}
QProgressBar::chunk {{ background: {c['accent']}; border-radius: 4px; }}
QSlider {{ background: transparent; }}
QSlider:horizontal {{ min-height: 22px; }}
QSlider::groove:horizontal {{ height: 6px; background: {c['sunken']}; border-radius: 3px; }}
QSlider::sub-page:horizontal {{ background: {c['accent']}; border-radius: 3px; }}
QSlider::handle:horizontal {{ background: {c['raised']}; border: 2px solid {c['accent']}; width: 12px; height: 12px; margin: -5px 0; border-radius: 8px; }}
QCheckBox, QRadioButton {{ background: transparent; spacing: 8px; }}
QCheckBox::indicator {{ width: 17px; height: 17px; border: 1px solid {c['faint']}; border-radius: 5px; background: {c['raised']}; }}
QCheckBox::indicator:hover {{ border-color: {c['accent']}; }}
QCheckBox::indicator:checked {{ background: {c['accent']}; border-color: {c['accent']}; image: url({CHECK}); }}
QCheckBox::indicator:disabled {{ border-color: {c['border']}; background: {c['surface']}; }}
QRadioButton::indicator {{ width: 16px; height: 16px; border: 1px solid {c['faint']}; border-radius: 9px; background: {c['raised']}; }}
QRadioButton::indicator:checked {{ width: 10px; height: 10px; border: 4px solid {c['accent']}; border-radius: 9px; }}
QSplitter {{ background: transparent; }}
QSplitter::handle {{ background: transparent; }}
QStatusBar {{ background: {c['bg']}; color: {c['muted']}; }}
QMenu {{ background: {c['raised']}; border: 1px solid {c['border']}; border-radius: 12px; padding: 6px; }}
QMenu::item {{ padding: 7px 20px; border-radius: 8px; }}
QMenu::item:selected {{ background: {c['accent_soft']}; color: {c['text']}; }}
QMenu::separator {{ height: 1px; background: {c['border']}; margin: 4px 8px; }}
QDialog {{ background: {c['bg']}; }}
QMessageBox {{ background: {c['bg']}; }}
QCalendarWidget QWidget {{ alternate-background-color: {c['surface']}; }}
QCalendarWidget QToolButton {{ color: {c['text']}; font-weight: 600; }}
QCalendarWidget QAbstractItemView {{ selection-background-color: {c['accent']}; selection-color: {c['accent_text']}; border-radius: 8px; }}
QFrame[role="moment"] QLabel, QFrame[role="moment"] QCheckBox {{ background: transparent; }}
QLabel[role="step"] {{ color: {c['muted']}; font-size: 11px; font-weight: 700; letter-spacing: 1px; }}
QLabel[role="title"] {{ font-size: 22px; font-weight: 700; }}
QLabel[role="ready"], QFrame[role="moment"] QLabel[role="ready"] {{ color: {c['ok']}; background: {_mix(c['raised'], c['ok'], 0.16)}; border-radius: 8px; padding: 2px 8px; font-size: 11px; font-weight: 600; }}
QPushButton[kind="chip"] {{ background: {c['surface']}; border: 1px solid {c['border']}; border-radius: 15px; padding: 7px 10px; font-weight: 600; }}
QPushButton[kind="chip"]:hover {{ border-color: {c['faint']}; }}
QPushButton[kind="chip"]:checked {{ background: {c['accent_soft']}; border: 1px solid {c['accent']}; color: {c['text']}; }}
QPushButton[kind="chip"]:disabled {{ color: {c['faint']}; background: {c['surface']}; }}
QPushButton[kind="big"] {{ background: {c['accent']}; color: {c['accent_text']}; border: none; border-radius: 12px; padding: 14px; font-size: 15px; font-weight: 700; }}
QPushButton[kind="big"]:hover {{ background: {_mix(c['accent'], c['text'], 0.12)}; }}
QPushButton[kind="big"]:disabled {{ background: {_mix(c['raised'], c['accent'], 0.3)}; color: {_mix(c['text'], c['raised'], 0.45)}; }}
QFrame[role="banner"] {{ background: {c['accent']}; border: none; border-radius: 16px; }}
QFrame[role="banner"] QLabel {{ background: transparent; color: {c['accent_text']}; }}
QPushButton[kind="banner"] {{ background: {c['accent_text']}; color: {c['accent']}; border: none; border-radius: 12px; padding: 10px 22px; font-size: 14px; font-weight: 700; }}
QPushButton[kind="banner"]:hover {{ background: {_mix(c['accent_text'], c['accent'], 0.2)}; }}
QFrame[role="moment"] {{ background: {c['raised']}; border: 1px solid {c['border']}; border-radius: 10px; }}
QFrame[role="moment"]:hover {{ border-color: {c['faint']}; }}
QFrame[role="moment"][selected="true"] {{ border: 2px solid {c['text']}; }}
QFrame[role="stepbar"] {{ background: {c['border']}; border: none; border-radius: 2px; }}
QFrame[role="stepbar"][state="done"] {{ background: {c['text']}; }}
QFrame[role="stepbar"][state="current"] {{ background: {c['accent']}; }}
QFrame[role="projitem"] {{ background: transparent; border: none; border-radius: 10px; }}
QFrame[role="projitem"]:hover {{ background: {c['hover']}; }}
QFrame[role="projitem"][selected="true"] {{ background: {c['raised']}; }}
QFrame[role="projitem"] QLabel {{ background: transparent; }}
QFrame[role="scrub"] {{ background: {c['surface']}; border: 1px solid {c['border']}; border-radius: 10px; }}
QFrame[role="scrub"]:hover {{ border-color: {c['faint']}; }}
QFrame[role="scrub"] QToolButton {{ background: transparent; border: none; border-radius: 4px; color: {c['muted']}; font-size: 12px; font-weight: 700; padding: 0; }}
QFrame[role="scrub"] QToolButton:hover {{ color: {c['text']}; background: {c['hover']}; }}
QPushButton[kind="mini"] {{ background: {c['surface']}; border: 1px solid {c['border']}; border-radius: 11px; padding: 3px 9px; font-size: 12px; font-weight: 500; }}
QPushButton[kind="mini"]:hover {{ border-color: {c['faint']}; }}
QPushButton[kind="mini"]:checked {{ background: {c['accent_soft']}; border: 1px solid {c['accent']}; color: {c['text']}; font-weight: 600; }}
QPushButton[kind="mini"]:disabled {{ color: {c['faint']}; }}
QFrame[role="sep"] {{ background: {c['border']}; border: none; max-height: 1px; }}
QFrame[role="vsep"] {{ background: {c['border']}; border: none; }}
QToolButton[role="flag"] {{ background: transparent; border: 2px solid transparent; border-radius: 7px; padding: 3px; }}
QToolButton[role="flag"]:hover {{ background: {c['hover']}; }}
QToolButton[role="flag"]:checked {{ background: {c['accent_soft']}; border: 2px solid {c['accent']}; }}
QPushButton[kind="link"] {{ background: transparent; border: none; padding: 2px 4px; color: {c['text']}; font-weight: 600; text-align: left; }}
QPushButton[kind="link"]:hover {{ color: {c['accent']}; background: transparent; }}
QPushButton[kind="link"]:disabled {{ color: {c['faint']}; background: transparent; }}
QToolButton[role="field"] {{ background: {c['raised']}; border: 1px solid {c['border']}; border-radius: 10px; padding: 6px 10px; }}
QToolButton[role="field"]:hover {{ border-color: {c['faint']}; background: {c['raised']}; }}
QToolButton[role="field"]:disabled {{ color: {c['faint']}; background: {c['surface']}; }}
QCalendarWidget[role="cal"] QWidget {{ background: {c['raised']}; }}
QCalendarWidget[role="cal"] QWidget#qt_calendar_navigationbar {{ background: {c['raised']}; border: none; padding: 4px; }}
QCalendarWidget[role="cal"] QToolButton {{ color: {c['text']}; font-weight: 600; background: transparent; border-radius: 8px; padding: 4px 8px; }}
QCalendarWidget[role="cal"] QToolButton:hover {{ background: {c['hover']}; }}
QCalendarWidget[role="cal"] QToolButton::menu-indicator {{ image: none; }}
QCalendarWidget[role="cal"] QToolButton#qt_calendar_prevmonth {{ qproperty-icon: url({tinted_svg('chevron-left', c['muted'])}); }}
QCalendarWidget[role="cal"] QToolButton#qt_calendar_nextmonth {{ qproperty-icon: url({tinted_svg('chevron-right', c['muted'])}); }}
QCalendarWidget[role="cal"] QSpinBox {{ border: none; background: transparent; }}
QCalendarWidget[role="cal"] QAbstractItemView {{ background: {c['raised']}; border: none; outline: none; selection-background-color: {c['accent']}; selection-color: {c['accent_text']}; }}
QCalendarWidget[role="cal"] QAbstractItemView:disabled {{ color: {c['faint']}; }}
"""


def apply(app: QApplication, mode: str | None = "light", accent: str | None = None) -> None:
    global current, mode_name, _accent
    mode_name = "dark" if mode == "dark" else "light"
    base = DARK if mode_name == "dark" else LIGHT
    current = dict(base)
    if accent:
        _accent = accent
        set_accent_tokens(accent)
    else:
        _accent = base["accent"]
    app.setStyle("Fusion")
    pal = QPalette()
    for role, key in ((QPalette.Window, "bg"), (QPalette.Base, "raised"), (QPalette.AlternateBase, "surface"),
                      (QPalette.Text, "text"), (QPalette.WindowText, "text"), (QPalette.Button, "raised"),
                      (QPalette.ButtonText, "text"), (QPalette.Highlight, "accent"),
                      (QPalette.HighlightedText, "accent_text"), (QPalette.PlaceholderText, "faint"),
                      (QPalette.ToolTipBase, "text"), (QPalette.ToolTipText, "bg"), (QPalette.Mid, "border"),
                      (QPalette.Light, "raised"), (QPalette.Dark, "border")):
        pal.setColor(role, QColor(current[key]))
    app.setPalette(pal)
    f = QFont("Segoe UI Variable Text")
    f.setFamilies(["Segoe UI Variable Text", "Segoe UI", "Inter", "Noto Sans", "sans-serif"])
    f.setPointSizeF(9.75)
    app.setFont(f)
    app.setStyleSheet(qss(current))
    icon.cache_clear()


def set_accent_tokens(hex_color: str) -> None:
    """Accent du parcours en cours ; les variantes douces sont dérivées du fond."""
    current["accent"] = hex_color
    current["accent_soft"] = _mix(current["raised"], hex_color, 0.16 if mode_name == "light" else 0.28)
    current["accent_text"] = "#FFFFFF" if mode_name == "light" else "#15171A"


def set_accent(target, hex_color: str | None) -> None:
    """Change la couleur d'accent (parcours) sans changer de mode.

    `target` : la fenêtre principale (seuls ses widgets sont restylés, c'est rapide) ; ne fait rien si la couleur
    est déjà active."""
    hex_color = hex_color or (DARK if mode_name == "dark" else LIGHT)["accent"]
    if target is None or hex_color.lower() == current["accent"].lower():
        return
    set_accent_tokens(hex_color)
    if isinstance(target, QApplication):
        target.setStyleSheet(qss(current))
    else:
        target.setStyleSheet(qss(current))


def color(key: str) -> str:
    return current[key]


@lru_cache(maxsize=512)
def _svg(name: str) -> str:
    p = ICONS / f"{name}.svg"
    return p.read_text(encoding="utf-8") if p.exists() else ""


@lru_cache(maxsize=512)
def icon(name: str, color_hex: str | None = None, size: int = 20, stroke: float = 2.0) -> QIcon:
    """Icône Lucide colorée (QIcon vide si l'icône n'existe pas)."""
    from PySide6.QtSvg import QSvgRenderer
    svg = _svg(name)
    if not svg:
        return QIcon()
    col = color_hex or current["text"]
    svg = svg.replace("currentColor", col).replace('stroke-width="2"', f'stroke-width="{stroke}"')
    r = QSvgRenderer(QByteArray(svg.encode()))
    ic = QIcon()
    for scale in (1, 2):
        pm = QPixmap(size * scale, size * scale)
        pm.fill(Qt.transparent)
        p = QPainter(pm)
        p.setRenderHint(QPainter.Antialiasing)
        r.render(p, QRectF(0, 0, size * scale, size * scale))
        p.end()
        pm.setDevicePixelRatio(scale)
        ic.addPixmap(pm)
    return ic


def icon_size(px: int = 20) -> QSize:
    return QSize(px, px)
