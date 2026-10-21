"""Tableau de bord (§2.1 A)."""
from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (QGridLayout, QHBoxLayout, QScrollArea, QVBoxLayout, QWidget)

from ...core import AppContext
from ..widgets import (PLATFORM_COLORS, STATUS_LABELS, Card, EmptyState, PageHeader, button, color_dot, label)


class DashboardPage(QWidget):
    new_project = Signal(object)      # concept_id | None
    open_project = Signal(int)
    edit_concept = Signal(object)     # concept_id | None
    show_jobs = Signal()

    def __init__(self, app: AppContext, parent=None):
        super().__init__(parent)
        self.app = app
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.NoFrame)
        outer.addWidget(scroll)
        self.body = QWidget()
        scroll.setWidget(self.body)
        self.lay = QVBoxLayout(self.body)
        self.lay.setContentsMargins(32, 28, 32, 28)
        self.lay.setSpacing(22)
        self.refresh()

    def _clear(self) -> None:
        while self.lay.count():
            it = self.lay.takeAt(0)
            w = it.widget()
            if w:
                w.setParent(None)
                w.deleteLater()
            elif it.layout():
                _delete_layout(it.layout())

    def refresh(self) -> None:
        self._clear()
        head = PageHeader("Tableau de bord", "Vos concepts, projets et tâches en un coup d'œil")
        head.add_action(button("Nouveau projet", "primary", "Ctrl+N", lambda: self.new_project.emit(None)))
        self.lay.addWidget(head)

        concepts = self.app.concepts.list_concepts()
        if not concepts:
            self.lay.addWidget(EmptyState(
                "Commencez par créer un concept",
                "Un concept regroupe une chaîne ou un univers (ex. Poussey Pass, Pokémon) et les comptes "
                "sociaux sur lesquels il publie. Chaque vidéo appartient à un concept.",
                "Créer un concept", lambda: self.edit_concept.emit(None)), 1)
            return

        self.lay.addLayout(self._alerts())
        # Concepts
        row = QHBoxLayout()
        row.addWidget(label("Concepts éditoriaux", "h2"))
        row.addStretch(1)
        row.addWidget(button("+ Concept", "ghost", on_click=lambda: self.edit_concept.emit(None)))
        self.lay.addLayout(row)
        grid = QGridLayout()
        grid.setSpacing(12)
        for i, c in enumerate(concepts):
            grid.addWidget(self._concept_card(c), i // 3, i % 3)
        for col in range(3):
            grid.setColumnStretch(col, 1)
        self.lay.addLayout(grid)

        cols = QHBoxLayout()
        cols.setSpacing(16)
        cols.addWidget(self._recent_projects(), 1)
        cols.addWidget(self._jobs_card(), 1)
        self.lay.addLayout(cols)
        cols2 = QHBoxLayout()
        cols2.setSpacing(16)
        cols2.addWidget(self._exports_card(), 1)
        cols2.addWidget(self._publications_card(), 1)
        self.lay.addLayout(cols2)
        self.lay.addStretch(1)

    def _alerts(self):
        lay = QVBoxLayout()
        failed = self.app.db.query_one("SELECT COUNT(*) n FROM job WHERE status='failed' AND "
                                       "finished_at > strftime('%Y-%m-%dT%H:%M:%SZ','now','-1 day')")["n"]
        missing = self.app.db.query_one("SELECT COUNT(*) n FROM source_asset WHERE missing = 1")["n"]
        for n, txt, cb in ((failed, "tâche(s) en échec ces dernières 24 h", self.show_jobs.emit),
                           (missing, "fichier(s) source introuvable(s) — déplacés ou supprimés", None)):
            if n:
                card = Card(clickable=cb is not None, padding=10)
                card.lay.addWidget(label(f"⚠  {n} {txt}", "error"))
                if cb:
                    card.clicked.connect(cb)
                lay.addWidget(card)
        return lay

    def _concept_card(self, c) -> Card:
        card = Card(clickable=True)
        card.clicked.connect(lambda cid=c.id: self.edit_concept.emit(cid))
        card.setToolTip("Modifier le concept et ses comptes")
        top = QHBoxLayout()
        top.addWidget(color_dot(c.color, 10))
        top.addWidget(label(c.name, "h2"))
        top.addStretch(1)
        card.lay.addLayout(top)
        if c.theme:
            card.lay.addWidget(label(c.theme, "muted"))
        accs = self.app.concepts.concept_accounts(c.id)
        plats = QHBoxLayout()
        plats.setSpacing(6)
        if not accs:
            plats.addWidget(label("Aucun compte associé", "faint"))
        for a, dflt in accs:
            b = label(a.platform.label + ("" if dflt else " ·"), "badge")
            b.setStyleSheet(f"color:{PLATFORM_COLORS[a.platform.value]};")
            b.setToolTip(a.name + ("" if dflt else " (non présélectionné)"))
            plats.addWidget(b)
        plats.addStretch(1)
        card.lay.addLayout(plats)
        n = self.app.db.query_one("SELECT COUNT(*) n FROM project WHERE concept_id=?", (c.id,))["n"]
        foot = QHBoxLayout()
        foot.addWidget(label(f"{n} projet(s)", "faint"))
        foot.addStretch(1)
        nb = button("Nouveau projet", "ghost", on_click=lambda _=False, cid=c.id: self.new_project.emit(cid))
        foot.addWidget(nb)
        card.lay.addLayout(foot)
        return card

    def _recent_projects(self) -> Card:
        card = Card()
        card.lay.addWidget(label("Projets récents", "h2"))
        rows = self.app.projects.recent(6)
        if not rows:
            card.lay.addWidget(label("Aucun projet pour l'instant.", "faint"))
        for r in rows:
            line = Card(clickable=True, padding=8, spacing=2)
            line.setStyleSheet("QFrame[role='card']{border:none;}")
            h = QHBoxLayout()
            h.addWidget(color_dot(r["concept_color"], 8))
            h.addWidget(label(r["name"]))
            h.addStretch(1)
            h.addWidget(label(r["concept_name"], "faint"))
            line.lay.addLayout(h)
            line.clicked.connect(lambda pid=r["id"]: self.open_project.emit(pid))
            card.lay.addWidget(line)
        card.lay.addStretch(1)
        return card

    def _jobs_card(self) -> Card:
        card = Card(clickable=True)
        card.clicked.connect(self.show_jobs.emit)
        card.lay.addWidget(label("Tâches", "h2"))
        jobs = [j for j in self.app.jobs.list(limit=20) if j.kind != "preview_frame"][:5]
        if not jobs:
            card.lay.addWidget(label("Aucune tâche.", "faint"))
        for j in jobs:
            h = QHBoxLayout()
            h.addWidget(label(j.label))
            h.addStretch(1)
            st = label(STATUS_LABELS[j.status], "error" if j.status == "failed" else "faint")
            h.addWidget(st)
            card.lay.addLayout(h)
        card.lay.addStretch(1)
        return card

    def _exports_card(self) -> Card:
        from pathlib import Path
        card = Card()
        card.lay.addWidget(label("Dernières vidéos produites", "h2"))
        rows = self.app.montage.exports(limit=6)
        if not rows:
            card.lay.addWidget(label("Aucun export pour l'instant. Montez un projet puis « Exporter ».", "faint",
                                     wrap=True))
        for r in rows:
            line = Card(clickable=True, padding=8, spacing=2)
            line.setStyleSheet("QFrame[role='card']{border:none;}")
            h = QHBoxLayout()
            h.addWidget(label(Path(r["output_path"]).name))
            h.addStretch(1)
            h.addWidget(label(f"{r['aspect']} · {r['project_name']}", "faint"))
            line.lay.addLayout(h)
            line.setToolTip(r["output_path"])
            line.clicked.connect(lambda p=r["output_path"]: __import__(
                "studiovideo.ui.pages.library", fromlist=["reveal"]).reveal(p))
            card.lay.addWidget(line)
        card.lay.addStretch(1)
        return card

    def _publications_card(self) -> Card:
        card = Card()
        card.lay.addWidget(label("Publications programmées", "h2"))
        card.lay.addWidget(label("La publication multiplateforme arrive en V3. Les publications préparées "
                                 "apparaîtront ici.", "faint", wrap=True))
        return card


def _delete_layout(layout) -> None:
    while layout.count():
        it = layout.takeAt(0)
        w = it.widget()
        if w:
            w.setParent(None)
            w.deleteLater()
        elif it.layout():
            _delete_layout(it.layout())
