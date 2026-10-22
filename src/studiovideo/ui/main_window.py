"""Fenêtre principale : navigation latérale permanente, six espaces, tâches."""
from __future__ import annotations

import logging

from PySide6.QtCore import QByteArray, QSettings, Qt, QTimer, Signal
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import (QApplication, QButtonGroup, QFrame, QHBoxLayout, QMainWindow, QMessageBox,
                               QSplitter, QStackedWidget, QVBoxLayout, QWidget)

from .. import __version__
from ..core import AppContext
from ..update.updater import BaseUpdater, ShutdownCoordinator, UpdateJournal, create_updater
from . import theme
from .dialogs import ConceptDialog, NewProjectDialog
from .importing import pick_and_import
from .jobs_panel import JobsPanel
from .pages.dashboard import DashboardPage
from .pages.analytics import AnalyticsPage
from .pages.calendar import CalendarPage, now_local
from .pages.library import LibraryPage
from .pages.settings import SettingsPage
from .pages.studio import StudioPage
from .widgets import JobBridge, button, label

log = logging.getLogger(__name__)

NAV = [("Tableau de bord", "Ctrl+1"), ("Studio", "Ctrl+2"), ("Bibliothèque", "Ctrl+3"),
       ("Calendrier", "Ctrl+4"), ("Analytiques", "Ctrl+5"), ("Paramètres", "Ctrl+6")]


class MainWindow(QMainWindow):
    _shutdown_requested = Signal()

    def __init__(self, app: AppContext, startup_message: str | None = None):
        super().__init__()
        self.app = app
        self._closing_for_update = False
        self.setWindowTitle("Studio Vidéo")
        self.resize(1360, 860)
        self.setMinimumSize(1040, 680)

        # mises à jour
        self.journal = UpdateJournal(app.paths.root)
        self.coord = ShutdownCoordinator(app, self.journal)
        self._shutdown_requested.connect(self._shutdown_for_update, Qt.QueuedConnection)
        self.updater: BaseUpdater = create_updater(app.settings.get("update_channel"), self.coord.can_shutdown,
                                                   self._shutdown_requested.emit)

        central = QWidget()
        root = QHBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        self.setCentralWidget(central)

        # navigation latérale
        side = QFrame()
        side.setProperty("role", "sidebar")
        side.setFixedWidth(208)
        sl = QVBoxLayout(side)
        sl.setContentsMargins(12, 18, 12, 12)
        sl.setSpacing(2)
        brand = label("Studio Vidéo", "h2")
        brand.setContentsMargins(10, 0, 0, 14)
        sl.addWidget(brand)
        self.nav_group = QButtonGroup(self)
        self.nav_group.setExclusive(True)
        for i, (name, key) in enumerate(NAV):
            b = button(name, "nav", f"{name} ({key})")
            b.setCheckable(True)
            self.nav_group.addButton(b, i)
            sl.addWidget(b)
            QShortcut(QKeySequence(key), self, activated=lambda i=i: self.go(i))
        self.nav_group.idClicked.connect(self.go)
        sl.addStretch(1)
        self.jobs_btn = button("Aucune tâche", "nav", "Afficher les tâches (Ctrl+J)", self.toggle_jobs)
        sl.addWidget(self.jobs_btn)
        sl.addWidget(label(f"v{__version__}", "faint"))
        root.addWidget(side)

        # pages + panneau des tâches
        self.vsplit = QSplitter(Qt.Vertical)
        self.pages = QStackedWidget()
        self.dashboard = DashboardPage(app)
        self.studio = StudioPage(app)
        self.library = LibraryPage(app)
        self.settings_page = SettingsPage(app, lambda: (self.updater.available, self.updater.reason))
        self.calendar = CalendarPage(app)
        self.analytics = AnalyticsPage(app)
        for p in (self.dashboard, self.studio, self.library, self.calendar, self.analytics, self.settings_page):
            self.pages.addWidget(p)
        self.vsplit.addWidget(self.pages)
        self.jobs_panel = JobsPanel(app)
        self.vsplit.addWidget(self.jobs_panel)
        self.vsplit.setStretchFactor(0, 4)
        self.vsplit.setStretchFactor(1, 1)
        self.jobs_panel.hide()
        root.addWidget(self.vsplit, 1)

        self.statusBar().showMessage(f"Données : {app.paths.root}")

        # signaux
        for page in (self.dashboard, self.studio):
            page.new_project.connect(self.new_project)
        self.studio.open_models.connect(lambda: (self.go(5), self.settings_page.show_models()))
        self.studio.open_ai_settings.connect(lambda: (self.go(5), self.settings_page.show_models()))
        self.settings_page.llm_changed.connect(self.studio.highlights.refresh_engines)
        self.dashboard.open_project.connect(self.open_project)
        self.dashboard.edit_concept.connect(self.edit_concept)
        self.dashboard.show_jobs.connect(lambda: self.toggle_jobs(True))
        self.dashboard.open_calendar.connect(lambda: self.go(3))
        self.dashboard.import_project.connect(self.import_project)
        self.dashboard.open_settings.connect(lambda tab: (self.go(5), self.settings_page.show_tab(tab)))
        self.settings_page.edit_concept.connect(self.edit_concept)
        self.settings_page.theme_changed.connect(lambda m: theme.apply(QApplication.instance(), m))
        self.settings_page.check_updates.connect(self.updater.check_now)

        self.bridge = JobBridge()
        self.bridge.changed.connect(self._on_job, Qt.QueuedConnection)
        app.jobs.add_listener(self.bridge)

        QShortcut(QKeySequence("Ctrl+N"), self, activated=lambda: self.new_project(None))
        QShortcut(QKeySequence("Ctrl+I"), self, activated=lambda: pick_and_import(self.app, self))
        QShortcut(QKeySequence("Ctrl+J"), self, activated=self.toggle_jobs)
        QShortcut(QKeySequence("F1"), self, activated=self.show_shortcuts)

        self._restore_layout()
        self.go(0)
        self._update_jobs_btn()
        app.importer().refresh_missing()
        resumed = app.jobs.resume_pending()
        if resumed:
            self.statusBar().showMessage(f"{resumed} tâche(s) en attente reprise(s).", 6000)
        if startup_message:
            QTimer.singleShot(400, lambda: QMessageBox.information(self, "Studio Vidéo", startup_message))
        QTimer.singleShot(1500, self._start_updater)
        self.studio.plan_publication.connect(lambda vid: (self.go(3), self.calendar.new_publication(variant_id=vid)))
        # rappels de publication (§19) : vérifiés chaque minute tant que le logiciel est ouvert
        self._reminders = QTimer(self)
        self._reminders.timeout.connect(self.check_reminders)
        self._reminders.start(60_000)
        QTimer.singleShot(3000, self.check_reminders)

    # -- navigation ---------------------------------------------------------
    def go(self, index: int) -> None:
        self.nav_group.button(index).setChecked(True)
        self.pages.setCurrentIndex(index)
        page = self.pages.currentWidget()
        if hasattr(page, "refresh"):
            page.refresh()

    def check_reminders(self) -> int:
        due = self.app.publications.due(now_local())
        auto = bool(self.app.settings.get("auto_upload", True))
        manual = []
        for r in due:
            acc = self.app.concepts.get_account(r["account_id"])
            if auto and r["platform"] in ("youtube", "tiktok", "instagram", "facebook", "x") \
                    and acc.connection_status == "connected" \
                    and r["output_path"]:
                from ..domain.models import PublicationStatus
                self.app.publications.set_status(r["id"], PublicationStatus.UPLOADING)
                self.app.jobs.submit("publish_upload", f"{acc.platform.label} · {r['title'] or 'sans titre'}",
                                     {"publication_id": r["id"]}, critical=True)
            else:
                self.app.publications.mark_manual(r["id"], "Heure de publication atteinte : à publier.")
                manual.append(r)
        due = manual
        n = self.calendar.pending_count()
        self.nav_group.button(3).setText("Calendrier" + (f"  ● {n}" if n else ""))
        if due:
            from ..publish.rules import RULES
            names = ", ".join(f"{RULES[r['platform']].label} {r['account']} ({r['title'] or 'sans titre'})"
                              for r in due[:3])
            self.statusBar().showMessage(f"À publier maintenant : {names}" + (" …" if len(due) > 3 else ""), 60_000)
            if self.pages.currentWidget() is self.calendar:
                self.calendar.refresh()
            QApplication.alert(self)
        return len(due)

    def show_shortcuts(self) -> None:
        from .shortcuts import ShortcutsDialog
        ShortcutsDialog(self).exec()

    def toggle_jobs(self, force: bool | None = None) -> None:
        show = (not self.jobs_panel.isVisible()) if force is None or not isinstance(force, bool) else force
        self.jobs_panel.setVisible(show)
        if show:
            self.jobs_panel.refresh()

    # -- actions ------------------------------------------------------------
    def new_project(self, concept_id=None) -> None:
        if not self.app.concepts.list_concepts():
            QMessageBox.information(self, "Nouveau projet", "Créez d'abord un concept éditorial.")
            self.edit_concept(None)
            if not self.app.concepts.list_concepts():
                return
        dlg = NewProjectDialog(self.app, concept_id, self)
        if dlg.exec() and dlg.project_id:
            self.open_project(dlg.project_id)

    def open_project(self, project_id: int) -> None:
        self.go(1)
        self.studio.open_project(project_id)
        p = self.app.projects.get(project_id)
        if p and p["mode"] == "smart_clipping":
            self.studio.left_tabs.setCurrentIndex(2 if self.app.transcripts.has(self.studio.asset_id or 0) else 1)

    def import_project(self) -> None:
        from PySide6.QtWidgets import QFileDialog

        from .pages.studio import default_export_dir
        path, _ = QFileDialog.getOpenFileName(self, "Importer un projet", str(default_export_dir()),
                                              "Projet Studio Vidéo (*.studioproj)")
        if not path:
            return
        self._import_job = self.app.jobs.submit("import_project", "Import du projet", {
            "path": path, "media_dir": str(default_export_dir() / "Projets importés")})
        self.statusBar().showMessage("Import du projet en cours…", 10000)

    def _on_project_imported(self, j) -> None:
        if j.status == "succeeded":
            r = j.result
            msg = f"Projet « {r['name']} » importé."
            if r["missing"]:
                msg += f"\n\nMédias introuvables ({len(r['missing'])}) : {', '.join(r['missing'][:5])}"
            QMessageBox.information(self, "Importer un projet", msg)
            self.dashboard.refresh()
            self.open_project(r["project_id"])
        elif j.status == "failed":
            QMessageBox.warning(self, "Importer un projet", j.error[:600])

    def edit_concept(self, concept_id=None) -> None:
        if ConceptDialog(self.app, concept_id, self).exec():
            self.dashboard.refresh()
            self.settings_page.refresh()

    def _on_job(self, job_id: int) -> None:
        self._update_jobs_btn()
        if self.jobs_panel.isVisible():
            self.jobs_panel.schedule_refresh()
        j = self.app.jobs.get(job_id)
        if j:
            if j.id == getattr(self, "_import_job", None) and j.status in ("succeeded", "failed", "cancelled"):
                self._import_job = None
                self._on_project_imported(j)
            self.studio.on_job(j)
            self.settings_page.on_job(j)
            self.analytics.on_job(j)
        if j and j.status == "succeeded" and j.kind.startswith("import"):
            if not hasattr(self, "_import_refresh"):
                self._import_refresh = QTimer(self, singleShot=True, interval=300)
                self._import_refresh.timeout.connect(self._refresh_after_import)
            self._import_refresh.start()
        elif j and j.status == "succeeded" and isinstance(j.result, dict) and j.result.get("warning"):
            self.statusBar().showMessage(f"{j.label} : {j.result['warning']}", 15000)
        elif j and j.status == "failed" and j.kind != "preview_frame":
            self.statusBar().showMessage(f"Échec : {j.label} — {j.error.splitlines()[0] if j.error else ''}", 10000)

    def _refresh_after_import(self) -> None:
        page = self.pages.currentWidget()
        if page is self.studio:
            self.studio.refresh()
        elif hasattr(page, "refresh"):
            page.refresh()

    def _update_jobs_btn(self) -> None:
        n = self.app.jobs.active_count()
        failed = self.app.db.query_one("SELECT COUNT(*) n FROM job WHERE status='failed' AND "
                                       "finished_at > strftime('%Y-%m-%dT%H:%M:%SZ','now','-1 hour')")["n"]
        txt = f"Tâches · {n} en cours" if n else "Tâches"
        if failed:
            txt += f" · {failed} échec"
        self.jobs_btn.setText(txt)

    # -- mises à jour -------------------------------------------------------
    def _start_updater(self) -> None:
        try:
            self.updater.start()
        except Exception as exc:
            log.warning("Mises à jour indisponibles : %s", exc)
            self.journal.write(f"Démarrage du module de mise à jour impossible : {exc}")

    def _shutdown_for_update(self) -> None:
        try:
            self.coord.prepare()
        except Exception as exc:
            self.journal.write(f"Préparation de la mise à jour échouée : {exc}")
            QMessageBox.critical(self, "Mise à jour", f"La mise à jour est annulée : {exc}")
            return
        self._closing_for_update = True
        self.close()

    # -- disposition --------------------------------------------------------
    def _restore_layout(self) -> None:
        s = QSettings(str(self.app.paths.root / "ui.ini"), QSettings.IniFormat)
        geo = s.value("geometry")
        if isinstance(geo, QByteArray):
            self.restoreGeometry(geo)
        sp = s.value("vsplit")
        if isinstance(sp, QByteArray):
            self.vsplit.restoreState(sp)

    def closeEvent(self, e) -> None:
        if not self._closing_for_update:
            critical = self.app.jobs.critical_running()
            if critical:
                r = QMessageBox.question(self, "Quitter", f"{len(critical)} export(s) ou publication(s) en cours. "
                                         "Quitter maintenant les interrompra. Quitter quand même ?")
                if r != QMessageBox.Yes:
                    e.ignore()
                    return
        self.studio.flush()
        s = QSettings(str(self.app.paths.root / "ui.ini"), QSettings.IniFormat)
        s.setValue("geometry", self.saveGeometry())
        s.setValue("vsplit", self.vsplit.saveState())
        self.studio.player.unload()
        self.studio.mplayer.stop()
        self.updater.stop()
        super().closeEvent(e)
