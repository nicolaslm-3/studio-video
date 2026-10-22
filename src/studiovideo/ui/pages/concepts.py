"""Concepts et projets : concepts éditoriaux, comptes sociaux (avec leur logo) et tous les projets."""
from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtWidgets import QHBoxLayout, QVBoxLayout, QWidget

from ...core import AppContext
from ..projects_dialog import ProjectsPanel
from ..widgets import Card, PageHeader


class ConceptsPage(QWidget):
    edit_concept = Signal(object)
    open_project = Signal(int)

    def __init__(self, app: AppContext, settings_page, parent=None):
        super().__init__(parent)
        self.app, self.settings_page = app, settings_page
        lay = QVBoxLayout(self)
        lay.setContentsMargins(36, 30, 36, 12)
        lay.setSpacing(16)
        lay.addWidget(PageHeader("Concepts et projets"))
        top = settings_page._concepts_tab()              # concepts + comptes (logique dans SettingsPage)
        lay.addWidget(top, 3)
        card = Card(padding=16)
        self.projects = ProjectsPanel(app, card)
        self.projects.open_project.connect(self.open_project.emit)
        card.lay.addWidget(self.projects)
        lay.addWidget(card, 4)

    def refresh(self) -> None:
        self.settings_page.refresh()
        self.projects.current_id = None
        self.projects.refresh()
