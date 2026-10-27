"""Tableau de bord (§2.1 A)."""
from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor, QPainter, QPen, QPixmap
from PySide6.QtWidgets import (QGraphicsOpacityEffect, QGridLayout, QHBoxLayout, QLabel, QProgressBar, QScrollArea,
                               QVBoxLayout, QWidget)

from ...core import AppContext
from .. import theme
from ..widgets import (DimOnHover, PLATFORM_COLORS, STATUS_LABELS, Card, EmptyState, PageHeader, button, color_dot, label)


def _ring(size: int, col: str) -> QPixmap:
    pm = QPixmap(size * 2, size * 2)
    pm.setDevicePixelRatio(2)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    p.setPen(QPen(QColor(col), 1.6))
    p.drawEllipse(2, 2, size - 4, size - 4)
    p.end()
    return pm


class DashboardPage(QWidget):
    new_project = Signal(object)      # concept_id | None
    open_project = Signal(int)
    edit_concept = Signal(object)     # concept_id | None
    show_jobs = Signal()
    open_calendar = Signal()
    import_project = Signal()
    all_projects = Signal()
    open_settings = Signal(str)       # nom de l'onglet des paramètres

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
        self.lay.setContentsMargins(36, 30, 36, 20)
        self.lay.setSpacing(22)
        self.refresh()

    def _clear(self) -> None:
        while self.lay.count():
            it = self.lay.takeAt(0)
            w = it.widget()
            if w:
                w.hide()                  # sinon son affichage différé en fait une fenêtre
                w.setParent(None)
                w.deleteLater()
            elif it.layout():
                _delete_layout(it.layout())

    def refresh(self) -> None:
        self._clear()
        head = PageHeader("Accueil")
        head.add_action(button("Importer un projet", "ghost", "Ouvrir une archive .studioproj",
                               self.import_project.emit, icon="upload"))
        self.lay.addWidget(head)
        self.lay.addLayout(self._alerts())
        start = self._getting_started()
        if start is not None:
            self.lay.addWidget(start)

        # Le reste est discret : il s'éclaire au survol (seuls les premiers pas ressortent)
        rest = DimOnHover(0.38 if start is not None else 1.0)
        rl = QVBoxLayout(rest)
        rl.setContentsMargins(0, 0, 0, 0)
        rl.setSpacing(18)
        concepts = self.app.concepts.list_concepts()
        row = QHBoxLayout()
        row.addWidget(label("Concepts éditoriaux", "h2"))
        row.addStretch(1)
        row.addWidget(button("Concept", "ghost", on_click=lambda: self.edit_concept.emit(None), icon="plus"))
        rl.addLayout(row)
        grid = QGridLayout()
        grid.setSpacing(12)
        for i, c in enumerate(concepts):
            grid.addWidget(self._concept_card(c), i // 3, i % 3)
        for col in range(3):
            grid.setColumnStretch(col, 1)
        rl.addLayout(grid)
        cols = QHBoxLayout()
        cols.setSpacing(16)
        cols.addWidget(self._recent_projects(), 1)
        cols.addWidget(self._jobs_card(), 1)
        rl.addLayout(cols)
        cols2 = QHBoxLayout()
        cols2.setSpacing(16)
        cols2.addWidget(self._exports_card(), 1)
        cols2.addWidget(self._publications_card(), 1)
        rl.addLayout(cols2)
        self.lay.addWidget(rest)
        self.lay.addStretch(1)

    def setup_items(self) -> list[tuple[str, bool, str, str]]:
        """(libellé, fait, texte du bouton, onglet des paramètres) — état réel de l'installation."""
        from ...ai import piper
        from ...edit import separate
        from ...transcribe import engines
        models = self.app.paths.models
        accounts = self.app.concepts.list_accounts()
        llm = self.app.settings.get("llm", None) or {}
        return [
            ("Comptes sociaux enregistrés", bool(accounts), "Ajouter", "Concepts et comptes"),
            ("Modèle de transcription téléchargé", bool(engines.available() and engines.installed_models(models)),
             "Télécharger", "Matériel et moteurs"),
            ("Séparation voix / musique prête", bool(separate.installed(models)), "Télécharger", "Matériel et moteurs"),
            ("Voix hors ligne installée", bool(piper.exe_path(models) and piper.installed_voices(models)), "Installer",
             "Matériel et moteurs"),
            ("Modèle de langage configuré (facultatif)", llm.get("kind") in ("ollama", "openai"), "Configurer",
             "Matériel et moteurs"),
            ("Un compte connecté pour publier automatiquement", any(a.connection_status == "connected"
                                                                    for a in accounts), "Connecter", "Publication"),
        ]

    def _getting_started(self):
        if self.app.settings.get("hide_getting_started", False):
            return None
        items = self.setup_items()
        if all(done for _, done, _, _ in items):
            return None
        card = Card(padding=20, spacing=6)
        done_n = sum(d for _, d, _, _ in items)
        head = QHBoxLayout()
        head.addWidget(label("Premiers pas", "h1"))
        head.addSpacing(8)
        head.addWidget(label(f"{done_n}/{len(items)}", "badge"))
        head.addStretch(1)
        head.addWidget(button("Masquer", "ghost", on_click=self._hide_start))
        card.lay.addLayout(head)
        bar = QProgressBar()
        bar.setRange(0, len(items))
        bar.setValue(done_n)
        card.lay.addWidget(bar)
        card.lay.addSpacing(6)
        for text, done, action, tab in items:
            row = QHBoxLayout()
            row.setSpacing(10)
            ic = QLabel()
            col = theme.color("accent") if done else theme.color("faint")
            ic.setPixmap(theme.icon("check" if done else "circle-help", col, 18).pixmap(18, 18)) if done else \
                ic.setPixmap(_ring(18, col))
            row.addWidget(ic)
            row.addWidget(label(text, "muted" if done else None))
            row.addStretch(1)
            if not done:
                row.addWidget(button(action, "soft", on_click=lambda _=False, t=tab: self.open_settings.emit(t)))
            card.lay.addLayout(row)
        return card

    def _hide_start(self) -> None:
        self.app.settings.set("hide_getting_started", True)
        self.refresh()

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
        hh = QHBoxLayout()
        hh.addWidget(label("Projets récents", "h2"))
        hh.addStretch(1)
        hh.addWidget(button("Tous les projets…", "ghost", on_click=self.all_projects.emit))
        card.lay.addLayout(hh)
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
        from ...publish.rules import RULES
        rows = self.app.publications.upcoming(8)
        if not rows:
            card.lay.addWidget(label("Aucune publication préparée. Après un export : « Programmer la "
                                     "publication », ou Calendrier → Nouvelle publication.", "faint", wrap=True))
        for r in rows:
            line = Card(clickable=True, padding=8, spacing=2)
            line.setStyleSheet("QFrame[role='card']{border:none;}")
            h = QHBoxLayout()
            when = (r["scheduled_at"] or "")
            when = f"{when[8:10]}/{when[5:7]} {when[11:16]}" if when else "sans date"
            mark = "● " if r["status"] in ("manual_action", "failed") else ""
            h.addWidget(label(f"{mark}{when} · {RULES[r['platform']].label}"))
            h.addWidget(label((r["title"] or "Sans titre")[:40], "muted"))
            h.addStretch(1)
            h.addWidget(label(r["concept"], "faint"))
            line.lay.addLayout(h)
            line.clicked.connect(self.open_calendar.emit)
            card.lay.addWidget(line)
        card.lay.addStretch(1)
        return card


def _delete_layout(layout) -> None:
    while layout.count():
        it = layout.takeAt(0)
        w = it.widget()
        if w:
            w.hide()                  # sinon son affichage différé en fait une fenêtre
            w.setParent(None)
            w.deleteLater()
        elif it.layout():
            _delete_layout(it.layout())
