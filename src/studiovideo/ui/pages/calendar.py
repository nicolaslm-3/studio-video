"""Calendrier éditorial (V3, §19) : toutes les publications, tous concepts et comptes, par jour."""
from __future__ import annotations

import calendar as _cal
from datetime import date, datetime, timedelta

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (QComboBox, QFrame, QGridLayout, QHBoxLayout, QListWidget, QListWidgetItem,
                               QPushButton, QSizePolicy, QSplitter, QVBoxLayout, QWidget)

from ...core import AppContext
from ..publication_dialog import PLATFORM_SHORT, STATUS, AssistedPublishDialog, PublicationDialog
from ..widgets import PageHeader, button, label, tool

MONTHS = ["janvier", "février", "mars", "avril", "mai", "juin", "juillet", "août", "septembre", "octobre",
          "novembre", "décembre"]
DAYS = ["lun.", "mar.", "mer.", "jeu.", "ven.", "sam.", "dim."]
STATUS_MARK = {"published": "✓ ", "failed": "⚠ ", "manual_action": "● ", "cancelled": "✕ "}


class DayCell(QFrame):
    open_master = Signal(int)
    new_on_day = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setProperty("role", "card")
        self.setMinimumHeight(96)
        self.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Expanding)
        self.lay = QVBoxLayout(self)
        self.lay.setContentsMargins(6, 4, 6, 4)
        self.lay.setSpacing(2)
        self.day = ""

    def set_day(self, d: date, in_month: bool, today: bool, items: list) -> None:
        while self.lay.count():
            w = self.lay.takeAt(0).widget()
            if w:
                w.setParent(None)
                w.deleteLater()
        self.day = d.isoformat()
        head = label(("● " if today else "") + str(d.day), "h2" if today else ("muted" if in_month else "faint"))
        self.lay.addWidget(head)
        for r in items[:4]:
            t = (r["at"] or "")[11:16]
            b = QPushButton(f"{STATUS_MARK.get(r['status'], '')}{t} {PLATFORM_SHORT.get(r['platform'], '')} · "
                            f"{(r['title'] or 'Sans titre')[:22]}")
            b.setFlat(True)
            b.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
            b.setMinimumWidth(0)
            b.setCursor(Qt.PointingHandCursor)
            b.setToolTip(f"{r['concept']} · {r['account']} · {STATUS.get(r['status'], r['status'])}\n{r['title'] or ''}")
            b.setStyleSheet(f"text-align:left; padding:1px 4px; border-left:3px solid {r['color'] or '#8A8F98'};")
            b.clicked.connect(lambda _=False, m=r["master_id"]: self.open_master.emit(m))
            self.lay.addWidget(b)
        if len(items) > 4:
            self.lay.addWidget(label(f"+ {len(items) - 4} autre(s)", "faint"))
        self.lay.addStretch(1)

    def mouseDoubleClickEvent(self, e):
        self.new_on_day.emit(self.day)
        super().mouseDoubleClickEvent(e)


class CalendarPage(QWidget):
    def __init__(self, app: AppContext, parent=None):
        super().__init__(parent)
        self.app = app
        t = date.today()
        self.year, self.month = t.year, t.month
        lay = QVBoxLayout(self)
        lay.setContentsMargins(28, 22, 28, 16)
        hdr = PageHeader("Calendrier")
        self.concept = QComboBox()
        self.concept.currentIndexChanged.connect(self.refresh)
        hdr.add_action(self.concept)
        hdr.add_action(button("Nouvelle publication", "primary", on_click=lambda: self.new_publication(), icon="plus"))
        lay.addWidget(hdr)
        nav = QHBoxLayout()
        nav.addWidget(tool("chevron-left", "Mois précédent", lambda: self._shift(-1)))
        self.title = label("", "h2")
        self.title.setToolTip("Double-clic sur un jour : nouvelle publication ce jour-là")
        nav.addWidget(self.title)
        nav.addWidget(tool("chevron-right", "Mois suivant", lambda: self._shift(1)))
        nav.addWidget(button("Aujourd'hui", "ghost", on_click=self._today))
        nav.addStretch(1)
        lay.addLayout(nav)
        split = QSplitter(Qt.Horizontal)
        gridw = QWidget()
        self.grid = QGridLayout(gridw)
        self.grid.setSpacing(4)
        self.grid.setContentsMargins(0, 0, 0, 0)
        for i, d in enumerate(DAYS):
            h = label(d, "faint")
            h.setAlignment(Qt.AlignCenter)
            self.grid.addWidget(h, 0, i)
        self.cells: list[DayCell] = []
        for k in range(42):
            c = DayCell()
            c.open_master.connect(self.open_master)
            c.new_on_day.connect(lambda d: self.new_publication(day=d))
            self.grid.addWidget(c, 1 + k // 7, k % 7)
            self.cells.append(c)
        for i in range(7):
            self.grid.setColumnStretch(i, 1)
        for r in range(1, 7):
            self.grid.setRowStretch(r, 1)
        split.addWidget(gridw)
        side = QWidget()
        sl = QVBoxLayout(side)
        sl.setContentsMargins(8, 0, 0, 0)
        self.todo_lbl = label("À faire maintenant", "h2")
        sl.addWidget(self.todo_lbl)
        self.todo = QListWidget()
        self.todo.itemActivated.connect(self._todo_open)
        self.todo.itemDoubleClicked.connect(self._todo_open)
        sl.addWidget(self.todo, 1)
        self.undated_lbl = label("Sans date", "h2")
        sl.addWidget(self.undated_lbl)
        self.undated = QListWidget()
        self.undated.itemDoubleClicked.connect(lambda it: self.open_master(it.data(Qt.UserRole)))
        sl.addWidget(self.undated, 1)
        sl.addStretch(0)
        self.side = side
        split.addWidget(side)
        split.setStretchFactor(0, 4)
        split.setStretchFactor(1, 1)
        lay.addWidget(split, 1)

    # -- navigation ----------------------------------------------------------------------------
    def _shift(self, n: int) -> None:
        m = self.month - 1 + n
        self.year, self.month = self.year + m // 12, m % 12 + 1
        self.refresh()

    def _today(self) -> None:
        t = date.today()
        self.year, self.month = t.year, t.month
        self.refresh()

    def _fill_concepts(self) -> None:
        cur = self.concept.currentData()
        self.concept.blockSignals(True)
        self.concept.clear()
        self.concept.addItem("Tous les concepts", None)
        for c in self.app.concepts.list_concepts():
            self.concept.addItem(c.name, c.id)
        self.concept.setCurrentIndex(max(0, self.concept.findData(cur)))
        self.concept.blockSignals(False)

    def refresh(self) -> None:
        self._fill_concepts()
        cid = self.concept.currentData()
        self.title.setText(f"{MONTHS[self.month - 1].capitalize()} {self.year}")
        first = date(self.year, self.month, 1)
        start = first - timedelta(days=first.weekday())
        end = start + timedelta(days=42)
        rows = self.app.publications.calendar(start.isoformat() + "T00:00", end.isoformat() + "T00:00", cid)
        by_day: dict[str, list] = {}
        for r in rows:
            by_day.setdefault(r["at"][:10], []).append(r)
        today = date.today()
        for k, cell in enumerate(self.cells):
            d = start + timedelta(days=k)
            cell.set_day(d, d.month == self.month, d == today, by_day.get(d.isoformat(), []))
        self.todo.clear()
        for r in self.app.db.query(self.app.publications._CAL + "WHERE p.status IN ('manual_action','failed') "
                                   + ("AND c.id = ? " if cid else "") + "ORDER BY at", (cid,) if cid else ()):
            it = QListWidgetItem(f"{STATUS_MARK.get(r['status'], '')}{PLATFORM_SHORT.get(r['platform'], '')} "
                                 f"{r['account']} · {r['title'] or 'Sans titre'}")
            it.setToolTip(r["last_error"] or STATUS.get(r["status"], ""))
            it.setData(Qt.UserRole, r["id"])
            self.todo.addItem(it)
        has_todo = self.todo.count() > 0
        self.undated.clear()
        seen = set()
        for r in self.app.publications.undated(cid):
            if r["master_id"] in seen:
                continue
            seen.add(r["master_id"])
            it = QListWidgetItem(f"{r['concept']} · {r['title'] or 'Sans titre'}")
            it.setData(Qt.UserRole, r["master_id"])
            self.undated.addItem(it)
        has_undated = self.undated.count() > 0
        for w, on in ((self.todo_lbl, has_todo), (self.todo, has_todo), (self.undated_lbl, has_undated),
                      (self.undated, has_undated)):
            w.setVisible(on)
        self.side.setVisible(has_todo or has_undated)           # colonne masquée quand il n'y a rien

    def pending_count(self) -> int:
        return self.app.db.query_one("SELECT COUNT(*) n FROM publication WHERE status = 'manual_action'")["n"]

    # -- actions -------------------------------------------------------------------------------
    def new_publication(self, variant_id: int | None = None, day: str | None = None) -> None:
        if not self.app.concepts.list_concepts():
            from PySide6.QtWidgets import QMessageBox
            QMessageBox.information(self, "Publication", "Ajoutez d'abord un compte dans Concepts et projets.")
            return
        if PublicationDialog(self.app, variant_id=variant_id, day=day, parent=self).exec():
            self.refresh()

    def open_master(self, master_id: int) -> None:
        PublicationDialog(self.app, master_id, parent=self).exec()
        self.refresh()

    def _todo_open(self, it: QListWidgetItem) -> None:
        pid = it.data(Qt.UserRole)
        if pid:
            AssistedPublishDialog(self.app, pid, self).exec()
            self.refresh()


def now_local() -> str:
    return datetime.now().strftime("%Y-%m-%dT%H:%M")


def month_days(year: int, month: int) -> int:
    return _cal.monthrange(year, month)[1]
