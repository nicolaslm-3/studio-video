"""Coque de l'application : dock centré en bas (parcours), tiroir de navigation, bulles et fenêtres flottantes."""
from __future__ import annotations

from PySide6.QtCore import QPoint, QSize, Qt, Signal
from PySide6.QtGui import QColor, QPainter
from PySide6.QtWidgets import (QApplication, QButtonGroup, QFrame, QGraphicsDropShadowEffect, QHBoxLayout, QLabel,
                               QPushButton, QToolButton, QVBoxLayout, QWidget)

from . import theme
from .widgets import label


def shadow(w: QWidget, blur: int = 28, dy: int = 6, alpha: int = 40) -> None:
    eff = QGraphicsDropShadowEffect(w)
    eff.setBlurRadius(blur)
    eff.setOffset(0, dy)
    c = QColor(theme.color("shadow"))
    c.setAlpha(alpha if theme.mode_name == "light" else 120)
    eff.setColor(c)
    w.setGraphicsEffect(eff)


class Bubble(QLabel):
    """Nom affiché au survol, sans délai (au-dessus du dock)."""

    def __init__(self):
        super().__init__(None, Qt.ToolTip | Qt.FramelessWindowHint)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        self.setProperty("role", "bubble")

    def show_for(self, w: QWidget, text: str, above: bool = True) -> None:
        self.setText(text)
        self.adjustSize()
        g = w.mapToGlobal(QPoint(w.width() // 2, 0 if above else w.height()))
        x = g.x() - self.width() // 2
        y = g.y() - self.height() - 8 if above else g.y() + 8
        self.move(x, y)
        self.show()
        self.raise_()


_bubble: Bubble | None = None


def bubble() -> Bubble:
    global _bubble
    if _bubble is None:
        _bubble = Bubble()
    return _bubble


class DockButton(QToolButton):
    """Bouton d'icône du dock : couleur propre, nom au survol, point sous l'élément actif."""

    def __init__(self, icon_name: str, text: str, color: str | None = None, checkable: bool = True, parent=None):
        super().__init__(parent)
        self.icon_name, self.text_, self.color = icon_name, text, color
        self.setCheckable(checkable)
        self.setCursor(Qt.PointingHandCursor)
        self.setFixedSize(48, 48)
        self.setIconSize(QSize(23, 23))
        self.setAccessibleName(text)
        self.badge = ""
        self.restyle()

    def restyle(self) -> None:
        col = self.color or theme.color("muted")
        self.setIcon(theme.icon(self.icon_name, col, 23))
        tint = QColor(col)
        tint.setAlpha(34 if theme.mode_name == "light" else 60)
        hover = QColor(col)
        hover.setAlpha(20 if theme.mode_name == "light" else 40)
        self.setStyleSheet(
            f"QToolButton {{ border-radius: 14px; background: transparent; }}"
            f"QToolButton:hover {{ background: rgba({hover.red()},{hover.green()},{hover.blue()},{hover.alpha()}); }}"
            f"QToolButton:checked {{ background: rgba({tint.red()},{tint.green()},{tint.blue()},{tint.alpha()}); }}")

    def enterEvent(self, e):
        bubble().show_for(self, self.text_ + (f" · {self.badge}" if self.badge else ""))
        super().enterEvent(e)

    def leaveEvent(self, e):
        bubble().hide()
        super().leaveEvent(e)

    def mousePressEvent(self, e):
        bubble().hide()
        super().mousePressEvent(e)

    def paintEvent(self, e):
        super().paintEvent(e)
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setPen(Qt.NoPen)
        if self.isChecked():
            p.setBrush(QColor(self.color or theme.color("text")))
            p.drawEllipse(self.width() // 2 - 2, self.height() - 6, 4, 4)
        if self.badge:
            p.setBrush(QColor(theme.color("danger") if self.badge.startswith("!") else self.color
                              or theme.color("accent")))
            p.drawEllipse(self.width() - 13, 6, 8, 8)
        p.end()


class Dock(QFrame):
    """Barre centrée en bas : menu · 6 parcours · tâches."""
    parcours_clicked = Signal(str)
    menu_clicked = Signal()
    jobs_clicked = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setProperty("role", "dock")
        lay = QHBoxLayout(self)
        lay.setContentsMargins(8, 6, 8, 6)
        lay.setSpacing(4)
        self.menu_btn = DockButton("menu", "Menu", None, checkable=False)
        self.menu_btn.clicked.connect(self.menu_clicked.emit)
        lay.addWidget(self.menu_btn)
        lay.addWidget(self._divider())
        self.group = QButtonGroup(self)
        self.group.setExclusive(True)
        self.buttons: dict[str, DockButton] = {}
        for mode, (name, ic, col, _desc) in theme.PARCOURS.items():
            b = DockButton(ic, name, col)
            b.clicked.connect(lambda _=False, m=mode: self.parcours_clicked.emit(m))
            self.group.addButton(b)
            self.buttons[mode] = b
            lay.addWidget(b)
        lay.addWidget(self._divider())
        self.jobs_btn = DockButton("loader-circle", "Tâches", None, checkable=False)
        self.jobs_btn.clicked.connect(self.jobs_clicked.emit)
        lay.addWidget(self.jobs_btn)
        shadow(self, 30, 8, 45)

    @staticmethod
    def _divider() -> QFrame:
        d = QFrame()
        d.setProperty("role", "divider")
        d.setFixedSize(1, 28)
        return d

    def set_active(self, mode: str | None) -> None:
        self.group.setExclusive(False)
        for m, b in self.buttons.items():
            b.setChecked(m == mode)
        self.group.setExclusive(True)

    def set_jobs(self, running: int, failed: int) -> None:
        b = self.jobs_btn
        b.badge = f"!{failed} échec" if failed else (f"{running} en cours" if running else "")
        b.color = theme.color("accent") if running else None
        b.text_ = "Tâches"
        b.restyle()
        b.update()

    def restyle(self) -> None:
        for b in [self.menu_btn, self.jobs_btn, *self.buttons.values()]:
            b.restyle()
        shadow(self, 30, 8, 45)


class Popover(QFrame):
    """Fenêtre flottante qui se ferme d'un clic à l'extérieur."""

    def __init__(self, parent=None):
        super().__init__(parent, Qt.Popup | Qt.FramelessWindowHint)
        self.setAttribute(Qt.WA_TranslucentBackground)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(14, 14, 14, 18)
        self.frame = QFrame()
        self.frame.setProperty("role", "popover")
        shadow(self.frame, 34, 8, 50)
        outer.addWidget(self.frame)
        self.lay = QVBoxLayout(self.frame)
        self.lay.setContentsMargins(14, 14, 14, 14)
        self.lay.setSpacing(6)

    def show_above(self, anchor: QWidget, align: str = "center") -> None:
        self.adjustSize()
        g = anchor.mapToGlobal(QPoint(0, 0))
        if align == "left":
            x = g.x() - 14
        elif align == "right":
            x = g.x() + anchor.width() - self.width() + 14
        else:
            x = g.x() + anchor.width() // 2 - self.width() // 2
        y = g.y() - self.height() + 6
        scr = QApplication.screenAt(g) or QApplication.primaryScreen()
        if scr:
            r = scr.availableGeometry()
            x = max(r.left() + 4, min(x, r.right() - self.width() - 4))
            y = max(r.top() + 4, y)
        self.move(x, y)
        self.show()


class Drawer(Popover):
    """Navigation : Accueil, Studio, Extraits, Calendrier, Analytics, Concepts, Paramètres."""
    navigate = Signal(int)

    def __init__(self, items: list[tuple[int, str, str]], version: str, parent=None):
        super().__init__(parent)
        self.setFixedWidth(250)
        head = QHBoxLayout()
        head.addWidget(label("Studio Vidéo", "h2"))
        head.addStretch(1)
        head.addWidget(label(f"v{version}", "faint"))
        self.lay.addLayout(head)
        self.lay.addSpacing(6)
        self.group = QButtonGroup(self)
        self.group.setExclusive(True)
        for idx, name, ic in items:
            b = QPushButton(name)
            b.setProperty("kind", "nav")
            b.setCheckable(True)
            b.setCursor(Qt.PointingHandCursor)
            b.setIcon(theme.icon(ic, theme.color("muted"), 18))
            b.setIconSize(QSize(18, 18))
            b.icon_name = ic
            self.group.addButton(b, idx)
            self.lay.addWidget(b)
            b.clicked.connect(lambda _=False, i=idx: (self.hide(), self.navigate.emit(i)))

    def button(self, idx: int):
        return self.group.button(idx)

    def restyle(self) -> None:
        for b in self.group.buttons():
            b.setIcon(theme.icon(b.icon_name, theme.color("muted"), 18))
        shadow(self.frame, 34, 8, 50)


FORMATS = [("9:16", "Vertical", "smartphone"), ("16:9", "Horizontal", "monitor"), ("1:1", "Carré", "square"),
           ("4:5", "Portrait", "rectangle-vertical")]


class ParcoursSheet(Popover):
    """Au clic sur un parcours : reprendre un projet récent, ou en commencer un en choisissant le format."""
    open_project = Signal(int)
    create = Signal(str, str)          # parcours, format

    def __init__(self, mode: str, recent: list, parent=None):
        super().__init__(parent)
        self.mode = mode
        name, ic, col, desc = theme.PARCOURS[mode]
        self.frame.setFixedWidth(340)
        head = QHBoxLayout()
        ib = QLabel()
        ib.setPixmap(theme.icon(ic, col, 22).pixmap(22, 22))
        head.addWidget(ib)
        t = label(name, "h2")
        t.setToolTip(desc)
        head.addWidget(t)
        head.addStretch(1)
        self.lay.addLayout(head)
        self.lay.addSpacing(4)
        if recent:
            for r in recent[:4]:
                b = QPushButton(r["name"])
                b.setProperty("kind", "nav")
                b.setCursor(Qt.PointingHandCursor)
                b.setIcon(theme.icon("folder-open", theme.color("muted"), 16))
                b.setToolTip(r["concept_name"])
                b.clicked.connect(lambda _=False, pid=r["id"]: (self.hide(), self.open_project.emit(pid)))
                self.lay.addWidget(b)
            d = QFrame()
            d.setProperty("role", "divider")
            d.setFixedHeight(1)
            self.lay.addSpacing(4)
            self.lay.addWidget(d)
            self.lay.addSpacing(4)
        fl = QHBoxLayout()
        fl.setSpacing(6)
        self.formats = QButtonGroup(self)
        for i, (asp, lbl, fic) in enumerate(FORMATS):
            b = QToolButton()
            b.setCheckable(True)
            b.setCursor(Qt.PointingHandCursor)
            b.setToolButtonStyle(Qt.ToolButtonTextUnderIcon)
            b.setIcon(theme.icon(fic, theme.color("muted"), 20))
            b.setIconSize(QSize(20, 20))
            b.setText(f"{lbl}\n{asp}")
            b.setFixedSize(70, 62)
            b.setProperty("aspect", asp)
            tint = QColor(col)
            b.setStyleSheet(
                f"QToolButton {{ border: 1px solid {theme.color('border')}; border-radius: 12px; font-size: 11px; "
                f"color: {theme.color('muted')}; }} QToolButton:checked {{ border: 2px solid {col}; color: "
                f"{theme.color('text')}; background: rgba({tint.red()},{tint.green()},{tint.blue()},28); }}")
            self.formats.addButton(b, i)
            fl.addWidget(b)
            if i == 0:
                b.setChecked(True)
        self.lay.addLayout(fl)
        go = QPushButton("Nouveau projet")
        go.setCursor(Qt.PointingHandCursor)
        go.setStyleSheet(f"QPushButton {{ background: {col}; color: white; border: none; border-radius: 10px; "
                         f"padding: 9px; font-weight: 600; }} QPushButton:hover {{ background: "
                         f"{QColor(col).darker(110).name()}; }}")
        go.clicked.connect(self._create)
        self.lay.addSpacing(4)
        self.lay.addWidget(go)
        self.go_btn = go

    def aspect(self) -> str:
        b = self.formats.checkedButton()
        return b.property("aspect") if b else "9:16"

    def _create(self) -> None:
        self.hide()
        self.create.emit(self.mode, self.aspect())


class Toast(QLabel):
    """Message bref au-dessus du dock (remplace la barre d'état)."""

    def __init__(self, host: QWidget):
        super().__init__(None, Qt.ToolTip | Qt.FramelessWindowHint)
        self.host = host
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        self.setProperty("role", "bubble")
        self.setWordWrap(True)
        self.setMaximumWidth(560)
        from PySide6.QtCore import QTimer
        self.timer = QTimer(self, singleShot=True)
        self.timer.timeout.connect(self.hide)

    def show_message(self, text: str, ms: int = 5000, anchor: QWidget | None = None) -> None:
        self.setText(text)
        self.adjustSize()
        a = anchor or self.host
        g = a.mapToGlobal(QPoint(a.width() // 2, 0))
        self.move(g.x() - self.width() // 2, g.y() - self.height() - 12)
        if self.host.isVisible():
            self.show()
            self.raise_()
        self.timer.start(ms)

    def currentMessage(self) -> str:                  # compatibilité avec QStatusBar
        return self.text() if self.isVisible() or self.timer.isActive() else ""
