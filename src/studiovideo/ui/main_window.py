"""Fenêtre principale : navigation latérale permanente, six espaces, tâches."""
from __future__ import annotations

import logging
from pathlib import Path

from PySide6.QtCore import QByteArray, QSettings, Qt, QTimer, Signal
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import (QApplication, QHBoxLayout, QMainWindow, QMessageBox, QStackedWidget, QVBoxLayout,
                               QWidget)

from .. import __version__
from ..core import AppContext
from ..update.updater import BaseUpdater, ShutdownCoordinator, UpdateJournal, create_updater
from . import theme
from .dialogs import ConceptDialog, NewProjectDialog
from .importing import pick_and_import
from .jobs_panel import JobsPanel
from .shell import Dock, Drawer, ParcoursSheet, Popover, Toast
from .pages.dashboard import DashboardPage
from .pages.analytics import AnalyticsPage
from .pages.calendar import CalendarPage, now_local
from .pages.concepts import ConceptsPage
from .pages.library import LibraryPage
from .pages.settings import SettingsPage
from .pages.studio import StudioPage
from .widgets import JobBridge, button, label

log = logging.getLogger(__name__)

# (index de page, nom, icône, raccourci)
NAV = [(0, "Accueil", "house", "Ctrl+1"), (1, "Studio", "clapperboard", "Ctrl+2"), (2, "Extraits", "film", "Ctrl+3"),
       (3, "Calendrier", "calendar-days", "Ctrl+4"), (4, "Analytics", "chart-no-axes-column", "Ctrl+5"),
       (6, "Concepts et projets", "folder-kanban", "Ctrl+6"), (5, "Paramètres", "settings", "Ctrl+7")]


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
        root = QVBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        self.setCentralWidget(central)

        # pages
        self.pages = QStackedWidget()
        self.dashboard = DashboardPage(app)
        self.studio = StudioPage(app)
        self.library = LibraryPage(app)
        self.settings_page = SettingsPage(app, lambda: (self.updater.available, self.updater.reason))
        self.calendar = CalendarPage(app)
        self.analytics = AnalyticsPage(app)
        self.concepts_page = ConceptsPage(app, self.settings_page)
        for p in (self.dashboard, self.studio, self.library, self.calendar, self.analytics, self.settings_page,
                  self.concepts_page):
            self.pages.addWidget(p)
        root.addWidget(self.pages, 1)

        # dock centré en bas (parcours) + tiroir de navigation + tâches
        bottom = QHBoxLayout()
        bottom.setContentsMargins(0, 4, 0, 12)
        bottom.addStretch(1)
        self.dock = Dock()
        bottom.addWidget(self.dock)
        bottom.addStretch(1)
        root.addLayout(bottom)
        self.drawer = Drawer([(i, name, ic) for i, name, ic, _k in NAV], __version__, self)
        self.nav_group = self.drawer.group
        for i, name, _ic, key in NAV:
            QShortcut(QKeySequence(key), self, activated=lambda i=i: self.go(i))
        self.drawer.navigate.connect(self.go)
        self.dock.menu_clicked.connect(lambda: self.drawer.show_above(self.dock.menu_btn, "left"))
        self.dock.parcours_clicked.connect(self.start_parcours)
        self.jobs_pop = Popover(self)
        self.jobs_pop.frame.setFixedSize(720, 380)
        self.jobs_panel = JobsPanel(app)
        self.jobs_pop.lay.setContentsMargins(4, 4, 4, 4)
        self.jobs_pop.lay.addWidget(self.jobs_panel)
        self.dock.jobs_clicked.connect(lambda: self.toggle_jobs(True))
        self.toast = Toast(self.dock)
        self.statusBar().hide()

        # signaux
        for page in (self.dashboard, self.studio):
            page.new_project.connect(self.new_project)
        self.studio.open_models.connect(lambda: (self.go(5), self.settings_page.show_models()))
        self.studio.open_ai_settings.connect(lambda: (self.go(5), self.settings_page.show_models()))
        self.concepts_page.edit_concept.connect(self.edit_concept)
        self.concepts_page.open_project.connect(self.open_project)
        self.settings_page.llm_changed.connect(self.studio.highlights.refresh_engines)
        self.dashboard.open_project.connect(self.open_project)
        self.dashboard.edit_concept.connect(self.edit_concept)
        self.dashboard.show_jobs.connect(lambda: self.toggle_jobs(True))
        self.dashboard.open_calendar.connect(lambda: self.go(3))
        self.dashboard.import_project.connect(self.import_project)
        self.dashboard.all_projects.connect(self.show_projects)
        self.dashboard.open_settings.connect(
            lambda tab: self.go(6) if tab == "Concepts et comptes" else (self.go(5), self.settings_page.show_tab(tab)))
        self.settings_page.edit_concept.connect(self.edit_concept)
        self.settings_page.theme_changed.connect(self.set_theme)
        self.settings_page.check_updates.connect(self.updater.check_now)

        self.bridge = JobBridge()
        self.bridge.changed.connect(self._on_job, Qt.QueuedConnection)
        app.jobs.add_listener(self.bridge)

        QShortcut(QKeySequence("Ctrl+N"), self, activated=lambda: self.new_project(None))
        QShortcut(QKeySequence("Ctrl+I"), self, activated=lambda: pick_and_import(self.app, self))
        QShortcut(QKeySequence("Ctrl+J"), self, activated=self.toggle_jobs)
        QShortcut(QKeySequence("F1"), self, activated=self.show_shortcuts)
        from ..media.watch import FolderWatcher
        self.watcher = FolderWatcher(app.db, app.workspace_id)
        self._watch_timer = QTimer(self, interval=15000, timeout=self.check_watch_folder)
        self._watch_timer.start()

        self.setAcceptDrops(True)
        self._restore_layout()
        self.go(0)
        self._update_jobs_btn()
        app.importer().refresh_missing()
        resumed = app.jobs.resume_pending()
        if resumed:
            self.notify(f"{resumed} tâche(s) en attente reprise(s).", 6000)
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
        b = self.nav_group.button(index)
        if b is not None:
            b.setChecked(True)
        self.pages.setCurrentIndex(index)
        self._sync_dock()
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
        self.dock.menu_btn.badge = f"{n} à publier" if n else ""
        self.dock.menu_btn.update()
        if due:
            from ..publish.rules import RULES
            names = ", ".join(f"{RULES[r['platform']].label} {r['account']} ({r['title'] or 'sans titre'})"
                              for r in due[:3])
            self.notify(f"À publier maintenant : {names}" + (" …" if len(due) > 3 else ""), 60_000)
            if self.pages.currentWidget() is self.calendar:
                self.calendar.refresh()
            QApplication.alert(self)
        return len(due)

    def check_watch_folder(self) -> int:
        """Importe les médias nouvellement déposés dans le dossier surveillé (Paramètres → Stockage)."""
        folder = self.app.settings.get("watch_folder", "") or ""
        if not folder:
            return 0
        try:
            files = self.watcher.scan(folder)
        except OSError:
            return 0
        for f in files:
            self.app.jobs.submit("import_file", f"Import (dossier surveillé) · {f.name}", {"path": str(f)})
        return len(files)

    def open_media_at(self, asset_id: int, t: float) -> bool:
        """Ouvre un média dans le Studio (son projet, ou un nouveau projet) et place la lecture à t."""
        a = self.app.library.get(asset_id)
        if a is None:
            return False
        pid = a["project_id"]
        if pid is None or self.app.projects.get(pid) is None:
            if QMessageBox.question(self, "Ouvrir", f"« {a['file_name']} » n'appartient à aucun projet.\n\n"
                                    "Créer un projet pour ce média ?") != QMessageBox.Yes:
                return False
            concepts = self.app.concepts.list_concepts()
            pid = self.app.projects.create(concepts[0].id, Path(a["file_name"]).stem[:60] or "Projet")
            self.app.db.connect().execute("UPDATE source_asset SET project_id = ? WHERE id = ?", (pid, asset_id))
        self.open_project(pid)
        self.studio._select_asset(asset_id)
        self.studio._seek_source(t)
        return True

    def show_projects(self) -> None:
        from .projects_dialog import ProjectsDialog
        dlg = ProjectsDialog(self.app, self, self.studio.project_id)
        dlg.open_project.connect(self.open_project)
        dlg.exec()
        self.dashboard.refresh() if hasattr(self.dashboard, "refresh") else None
        self.studio.refresh()

    def show_shortcuts(self) -> None:
        from .shortcuts import ShortcutsDialog
        ShortcutsDialog(self).exec()

    def toggle_jobs(self, force: bool | None = None) -> None:
        show = (not self.jobs_pop.isVisible()) if force is None or not isinstance(force, bool) else force
        if show:
            self.jobs_panel.refresh()
            self.jobs_pop.show_above(self.dock.jobs_btn, "right")
        else:
            self.jobs_pop.hide()

    # -- glisser-déposer n'importe où ----------------------------------------
    def dragEnterEvent(self, e) -> None:
        if e.mimeData().hasUrls():
            e.acceptProposedAction()

    def dropEvent(self, e) -> None:
        paths = [u.toLocalFile() for u in e.mimeData().urls() if u.isLocalFile()]
        if paths:
            self.drop_files(paths)

    def drop_files(self, paths: list[str]) -> int | None:
        """Studio ouvert : ajout au projet. Ailleurs : projet « Brouillon » créé, extraits cherchés d'office."""
        from ..media.importer import VIDEO_EXT
        if self.pages.currentWidget() is self.studio and self.studio.project_id:
            from .importing import import_paths
            import_paths(self.app, self, paths, self.studio.project_id)
            return self.studio.project_id
        vids = [Path(p) for p in paths if Path(p).suffix.lower() in VIDEO_EXT and Path(p).is_file()]
        if not vids:
            from .importing import import_paths
            import_paths(self.app, self, paths)
            self.notify("Ajouté à Extraits.")
            return None
        pid = self.app.projects.quick_create(self.app.concepts, "smart_clipping", vids[0].stem[:60], "9:16")
        for v in vids:
            self.app.jobs.submit("import_file", f"Import · {v.name}",
                                 {"path": str(v), "project_id": pid, "auto_clips": True})
        self.open_project(pid)
        self.notify("Projet brouillon créé : transcription et recherche des moments forts en cours…", 7000)
        return pid

    def notify(self, text: str, ms: int = 5000) -> None:
        self.toast.show_message(text, ms)

    # -- parcours (dock) ------------------------------------------------------
    def start_parcours(self, mode: str) -> None:
        """Clic sur un parcours : reprendre un projet ou en commencer un (format seulement)."""
        cur = self.app.projects.get(self.studio.project_id) if self.studio.project_id else None
        sheet = ParcoursSheet(mode, self.app.projects.by_mode(mode), self)
        sheet.open_project.connect(self.open_project)
        sheet.create.connect(self.create_project)
        self._sheet = sheet
        self._sync_dock()
        sheet.show_above(self.dock.buttons[mode])
        if cur is not None and cur["mode"] == mode and self.pages.currentWidget() is not self.studio:
            pass

    def create_project(self, mode: str, aspect: str, name: str = "") -> int:
        pid = self.app.projects.quick_create(self.app.concepts, mode, name, aspect)
        self.open_project(pid)
        return pid

    def _sync_dock(self) -> None:
        mode = None
        if self.pages.currentWidget() is self.studio and self.studio.project_id:
            p = self.app.projects.get(self.studio.project_id)
            mode = p["mode"] if p else None
        self.dock.set_active(mode)
        col = theme.PARCOURS[mode][2] if mode in theme.PARCOURS else None
        theme.set_accent(self, col)
        self.studio.panel._tab_title(self.studio.panel.currentIndex())

    def set_theme(self, mode: str) -> None:
        theme.apply(QApplication.instance(), mode)
        self.setStyleSheet("")
        self.dock.restyle()
        self.drawer.restyle()
        self._sync_dock()

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
        self.studio.open_project(project_id)
        self.go(1)
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
        self.notify("Import du projet en cours…", 10000)

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
        if self.jobs_pop.isVisible():
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
        if j and j.kind == "auto_clips" and j.status in ("succeeded", "failed"):
            r = j.result or {}
            if j.status == "failed":
                self.notify(f"Extraits automatiques : {j.error.splitlines()[-1][:200] if j.error else 'échec'}", 10000)
            elif r.get("skipped"):
                self.notify(r["skipped"], 12000)
            else:
                self.notify(f"{r.get('count', 0)} moment(s) fort(s) proposé(s).", 8000)
        elif j and j.status == "succeeded" and isinstance(j.result, dict) and j.result.get("warning"):
            self.notify(f"{j.label} : {j.result['warning']}", 15000)
        elif j and j.status == "failed" and j.kind != "preview_frame":
            self.notify(f"Échec : {j.label} — {j.error.splitlines()[0] if j.error else ''}", 10000)

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
        self.dock.set_jobs(n, failed)

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
        self.studio.player.unload()
        self.studio.mplayer.stop()
        self.updater.stop()
        super().closeEvent(e)
