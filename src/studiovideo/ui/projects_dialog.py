"""Tous les projets : ouvrir, renommer, dupliquer, changer de concept, archiver, supprimer."""
from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (QAbstractItemView, QCheckBox, QDialog, QWidget, QHBoxLayout, QHeaderView, QInputDialog,
                               QMessageBox, QTableWidget, QTableWidgetItem, QVBoxLayout)

from ..core import AppContext
from .widgets import button, label, local_time

COLS = ["Projet", "Concept", "Médias", "Vidéos", "Modifié", "État"]


class ProjectsPanel(QWidget):
    open_project = Signal(int)

    def __init__(self, app: AppContext, parent=None, current_id: int | None = None, closable: bool = False):
        super().__init__(parent)
        self.app, self.current_id = app, current_id
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        top = QHBoxLayout()
        top.addWidget(label("Projets", "h2"))
        top.addStretch(1)
        self.show_archived = QCheckBox("Archivés")
        self.show_archived.setChecked(True)
        self.show_archived.toggled.connect(self.refresh)
        top.addWidget(self.show_archived)
        lay.addLayout(top)
        self.table = QTableWidget(0, len(COLS))
        self.table.setHorizontalHeaderLabels(COLS)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.verticalHeader().hide()
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        for j in range(1, len(COLS)):
            self.table.horizontalHeader().setSectionResizeMode(j, QHeaderView.ResizeToContents)
        self.table.setShowGrid(False)
        self.table.verticalHeader().setDefaultSectionSize(36)
        self.table.itemDoubleClicked.connect(lambda _i: self._open())
        self.table.itemSelectionChanged.connect(self._sel_changed)
        lay.addWidget(self.table, 1)
        row = QHBoxLayout()
        self.b_open = button("Ouvrir", "primary", on_click=self._open)
        self.b_rename = button("Renommer", "ghost", on_click=self._rename)
        self.b_dup = button("Dupliquer", "ghost", on_click=self._duplicate)
        self.b_concept = button("Concept…", "ghost", tooltip="Ranger dans un autre concept", on_click=self._concept)
        self.b_arch = button("Archiver", "ghost", on_click=self._archive)
        self.b_del = button("Supprimer", "danger", tooltip="Les fichiers ne sont jamais effacés : le montage et les "
                            "clips sont retirés, les médias restent dans Médias.", on_click=self._delete)
        for b in (self.b_open, self.b_rename, self.b_dup, self.b_concept, self.b_arch):
            row.addWidget(b)
        row.addStretch(1)
        row.addWidget(self.b_del)
        self.closable = closable
        lay.addLayout(row)
        self.refresh()

    def accept(self) -> None:          # remplacé par la fenêtre qui contient le panneau
        pass

    def refresh(self, *_):
        keep = self.selected()
        rows = self.app.projects.all(self.show_archived.isChecked())
        self.table.setRowCount(len(rows))
        for i, r in enumerate(rows):
            vals = [r["name"], r["concept_name"], str(r["n_media"]), str(r["n_videos"]),
                    local_time(r["updated_at"]), "archivé" if r["status"] == "archived" else ""]
            for j, v in enumerate(vals):
                it = QTableWidgetItem(v)
                it.setData(Qt.UserRole, r["id"])
                if r["status"] == "archived":
                    it.setForeground(Qt.gray)
                self.table.setItem(i, j, it)
            if r["id"] == keep:
                self.table.selectRow(i)
        self._sel_changed()

    def selected(self) -> int | None:
        it = self.table.item(self.table.currentRow(), 0) if self.table.currentRow() >= 0 else None
        return it.data(Qt.UserRole) if it else None

    def _sel_changed(self) -> None:
        pid = self.selected()
        p = self.app.projects.get(pid) if pid else None
        for b in (self.b_open, self.b_rename, self.b_dup, self.b_concept, self.b_arch, self.b_del):
            b.setEnabled(p is not None)
        self.b_arch.setText("Désarchiver" if p is not None and p["status"] == "archived" else "Archiver")

    def _open(self) -> None:
        pid = self.selected()
        if pid:
            self.open_project.emit(pid)
            self.accept()

    def _rename(self) -> None:
        pid = self.selected()
        p = self.app.projects.get(pid)
        name, ok = QInputDialog.getText(self, "Renommer", "Nouveau nom :", text=p["name"])
        if ok and name.strip():
            self.app.projects.rename(pid, name)
            self.refresh()

    def _duplicate(self) -> None:
        new = self.app.projects.duplicate(self.selected())
        self.refresh()
        for i in range(self.table.rowCount()):
            if self.table.item(i, 0).data(Qt.UserRole) == new:
                self.table.selectRow(i)

    def _concept(self) -> None:
        pid = self.selected()
        concepts = self.app.concepts.list_concepts()
        names = [c.name for c in concepts]
        cur = self.app.projects.get(pid)["concept_id"]
        idx = next((i for i, c in enumerate(concepts) if c.id == cur), 0)
        choice, ok = QInputDialog.getItem(self, "Changer de concept", "Concept :", names, idx, False)
        if ok:
            self.app.projects.set_concept(pid, concepts[names.index(choice)].id)
            self.refresh()

    def _archive(self) -> None:
        pid = self.selected()
        self.app.projects.set_archived(pid, self.app.projects.get(pid)["status"] != "archived")
        self.refresh()

    def _delete(self) -> None:
        pid = self.selected()
        p = self.app.projects.get(pid)
        if pid == self.current_id:
            QMessageBox.information(self, "Supprimer", "Ce projet est ouvert dans le Studio : ouvrez-en un autre "
                                    "avant de le supprimer.")
            return
        if QMessageBox.question(self, "Supprimer", f"Supprimer le projet « {p['name']} » ?\n\nSon montage, ses clips "
                                "et l'historique de ses exports sont retirés. Les fichiers vidéo (sources et "
                                "exports) restent sur le disque et les médias dans Médias.") != QMessageBox.Yes:
            return
        try:
            self.app.projects.delete(pid)
        except ValueError as exc:
            QMessageBox.information(self, "Supprimer", str(exc))
            return
        self.refresh()


class ProjectsDialog(QDialog):
    """Fenêtre « Tous les projets » (le panneau est aussi dans la page Concepts et projets)."""
    open_project = Signal(int)

    def __init__(self, app: AppContext, parent=None, current_id: int | None = None):
        super().__init__(parent)
        self.setWindowTitle("Tous les projets")
        self.resize(820, 520)
        lay = QVBoxLayout(self)
        self.panel = ProjectsPanel(app, self, current_id)
        self.panel.accept = self.accept
        self.panel.open_project.connect(self.open_project.emit)
        lay.addWidget(self.panel)

    def __getattr__(self, name):
        panel = self.__dict__.get("panel") if "panel" in self.__dict__ else None
        if panel is None:
            raise AttributeError(name)
        return getattr(panel, name)
