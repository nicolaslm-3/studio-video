"""Archives (menu des trois barres) : les vidéos archivées depuis la barre de gauche ; on les restaure ou on les
supprime pour de bon."""
from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QComboBox, QHBoxLayout, QListWidget, QListWidgetItem, QMessageBox, QVBoxLayout, QWidget

from ...core import AppContext
from ..widgets import PageHeader, button, dot_icon, label, local_time
from ..widgets import ask


class ArchivesPage(QWidget):
    restored = Signal(int)

    def __init__(self, app: AppContext, parent=None):
        super().__init__(parent)
        self.app = app
        lay = QVBoxLayout(self)
        lay.setContentsMargins(36, 30, 36, 20)
        lay.setSpacing(12)
        hdr = PageHeader("Archives", "Les vidéos archivées : elles ne sont plus dans la barre de gauche")
        self.concept = QComboBox()
        self.concept.setToolTip("Concept")
        self.concept.currentIndexChanged.connect(self.refresh)
        hdr.add_action(self.concept)
        lay.addWidget(hdr)
        self.list = QListWidget()
        self.list.itemSelectionChanged.connect(self._buttons)
        self.list.itemDoubleClicked.connect(lambda _it: self.restore())
        lay.addWidget(self.list, 1)
        self.empty = label("Aucune vidéo archivée.", "muted", hint=False)
        lay.addWidget(self.empty)
        row = QHBoxLayout()
        self.restore_btn = button("Restaurer", "primary", icon="undo-2", on_click=self.restore)
        self.delete_btn = button("Supprimer", "ghost", icon="trash-2", on_click=self.delete)
        row.addWidget(self.restore_btn)
        row.addWidget(self.delete_btn)
        row.addStretch(1)
        lay.addLayout(row)
        self.refresh()

    def _fill_concepts(self) -> None:
        cur = self.concept.currentData()
        self.concept.blockSignals(True)
        self.concept.clear()
        self.concept.addItem("Tous les concepts", None)
        for c in self.app.concepts.user_concepts():
            self.concept.addItem(dot_icon(c.color), c.name, c.id)
        self.concept.setCurrentIndex(max(0, self.concept.findData(cur)))
        self.concept.blockSignals(False)

    def refresh(self) -> None:
        """Les vidéos archivées, rangées par concept (celles sans concept à la fin), d'un concept ou de tous."""
        self._fill_concepts()
        cid = self.concept.currentData()
        drafts = self.app.concepts.DRAFTS
        rows = [p for p in self.app.projects.all() if p["status"] == "archived"
                and '"parent"' not in (p["data_json"] or "")                    # versions : avec leur vidéo
                and (cid is None or p["concept_id"] == cid)]
        rows.sort(key=lambda p: (p["concept_name"] == drafts, (p["concept_name"] or "").lower(), p["name"].lower()))
        self.list.clear()
        for p in rows:
            concept = "Sans concept" if p["concept_name"] == drafts else p["concept_name"]
            it = QListWidgetItem(f"{concept}   ·   {p['name']}   ·   modifiée le {local_time(p['updated_at'])}")
            if p["concept_name"] != drafts:
                it.setIcon(dot_icon(p["concept_color"]))
            it.setData(Qt.UserRole, p["id"])
            self.list.addItem(it)
        self.empty.setVisible(self.list.count() == 0)
        self.list.setVisible(self.list.count() > 0)
        self._buttons()

    def _selected(self) -> int | None:
        it = self.list.currentItem()
        return it.data(Qt.UserRole) if it else None

    def _buttons(self) -> None:
        on = self._selected() is not None
        self.restore_btn.setEnabled(on)
        self.delete_btn.setEnabled(on)

    def restore(self) -> None:
        pid = self._selected()
        if pid:
            for v in self.app.projects.versions(pid):            # ses versions traduites reviennent avec elle
                self.app.projects.set_archived(v["id"], False)
            self.app.projects.set_archived(pid, False)
            self.refresh()
            self.restored.emit(pid)

    def delete(self) -> None:
        pid = self._selected()
        p = self.app.projects.get(pid) if pid else None
        if p is None:
            return
        if not ask(self, "Supprimer", f"Supprimer la vidéo « {p['name']} » ?\n\nSon montage et ses "
                                "extraits sont retirés. Les fichiers vidéo restent sur le disque."):
            return
        try:
            self.app.projects.delete(pid)
        except ValueError as exc:
            QMessageBox.information(self, "Supprimer", str(exc))
        self.refresh()
