"""Gestionnaire de tâches visible (§17.3)."""
from __future__ import annotations

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (QAbstractItemView, QComboBox, QHBoxLayout, QHeaderView, QProgressBar, QTableWidget,
                               QTableWidgetItem, QVBoxLayout, QWidget)

from ..core import AppContext
from .widgets import STATUS_LABELS, button, label

FILTERS = [("Toutes", None), ("Actives", ("pending", "running")), ("Échouées", ("failed",)),
           ("Terminées", ("succeeded",)), ("Annulées", ("cancelled",))]


class JobsPanel(QWidget):
    def __init__(self, app: AppContext, parent=None):
        super().__init__(parent)
        self.app = app
        lay = QVBoxLayout(self)
        lay.setContentsMargins(16, 10, 16, 12)
        head = QHBoxLayout()
        head.addWidget(label("Tâches", "h2"))
        self.filter = QComboBox()
        for name, val in FILTERS:
            self.filter.addItem(name, val)
        self.filter.currentIndexChanged.connect(self.refresh)
        head.addWidget(self.filter)
        head.addStretch(1)
        self.cancel_btn = button("Annuler", on_click=self._cancel)
        self.retry_btn = button("Relancer", on_click=self._retry)
        head.addWidget(self.cancel_btn)
        head.addWidget(self.retry_btn)
        lay.addLayout(head)
        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(["Tâche", "État", "Progression", "Détail"])
        self.table.verticalHeader().hide()
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setShowGrid(False)
        hh = self.table.horizontalHeader()
        hh.setSectionResizeMode(0, QHeaderView.Stretch)
        hh.setSectionResizeMode(3, QHeaderView.Stretch)
        hh.resizeSection(2, 160)
        self.table.itemSelectionChanged.connect(self._update_buttons)
        lay.addWidget(self.table)
        self._pending_refresh = QTimer(self, singleShot=True, interval=150)
        self._pending_refresh.timeout.connect(self.refresh)
        self.refresh()

    def schedule_refresh(self, *_):
        if not self._pending_refresh.isActive():
            self._pending_refresh.start()

    def refresh(self, *_):
        sel = self._selected()
        jobs = [j for j in self.app.jobs.list(self.filter.currentData(), limit=300) if j.kind != "preview_frame"]
        self.table.setRowCount(len(jobs))
        for i, j in enumerate(jobs):
            it = QTableWidgetItem(j.label)
            it.setData(Qt.UserRole, j.id)
            it.setToolTip(f"#{j.id} · {j.kind} · créée le {j.created_at.replace('T', ' ')[:16]}")
            self.table.setItem(i, 0, it)
            st = QTableWidgetItem(STATUS_LABELS[j.status])
            if j.status == "failed":
                st.setForeground(Qt.red)
            self.table.setItem(i, 1, st)
            bar = QProgressBar()
            bar.setRange(0, 100)
            bar.setValue(int(j.progress * 100))
            bar.setFixedHeight(6)
            wrap = QWidget()
            wl = QVBoxLayout(wrap)
            wl.setContentsMargins(6, 0, 6, 0)
            wl.addWidget(bar)
            self.table.setCellWidget(i, 2, wrap)
            detail = j.error or j.message or ("; ".join(j.result.get("warnings", [])) if j.result else "")
            d = QTableWidgetItem(detail.splitlines()[0] if detail else "")
            d.setToolTip(detail)
            self.table.setItem(i, 3, d)
            if j.id == sel:
                self.table.selectRow(i)
        self._update_buttons()

    def _selected(self) -> int | None:
        r = self.table.currentRow()
        it = self.table.item(r, 0) if r >= 0 else None
        return it.data(Qt.UserRole) if it else None

    def _update_buttons(self) -> None:
        jid = self._selected()
        j = self.app.jobs.get(jid) if jid else None
        self.cancel_btn.setEnabled(bool(j and j.status in ("pending", "running")))
        self.retry_btn.setEnabled(bool(j and j.status in ("failed", "cancelled")))

    def _cancel(self) -> None:
        jid = self._selected()
        if jid:
            self.app.jobs.cancel(jid)

    def _retry(self) -> None:
        jid = self._selected()
        if jid:
            try:
                self.app.jobs.retry(jid)
            except ValueError:
                pass
