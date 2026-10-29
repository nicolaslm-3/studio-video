"""Fenêtre principale : dock des six parcours, menu de navigation, tâches."""
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
from .shell import Dock, Drawer, Popover, TipFilter, Toast
from .pages.analytics import AnalyticsPage
from .pages.calendar import CalendarPage, now_local
from .pages.archives import ArchivesPage
from .pages.library import LibraryPage
from .pages.settings import SettingsPage
from .pages.studio import StudioPage
from .widgets import JobBridge, button, label

log = logging.getLogger(__name__)

# (index de page, nom, icône, raccourci)
# pas de page d'accueil : le logiciel s'ouvre dans Montage libre (la page 0 reste vide, les numéros ne changent pas)
NAV = [(1, "Studio", "clapperboard", "Ctrl+1"), (2, "Médias", "film", "Ctrl+2"),
       (3, "Calendrier", "calendar-days", "Ctrl+3"), (4, "Analytics", "chart-no-axes-column", "Ctrl+4"),
       (6, "Archives", "archive", "Ctrl+5"), (5, "Paramètres", "settings", "Ctrl+6")]


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
        self.studio = StudioPage(app)
        self.library = LibraryPage(app)
        self.settings_page = SettingsPage(app, lambda: (self.updater.available, self.updater.reason))
        self.calendar = CalendarPage(app)
        self.analytics = AnalyticsPage(app)
        self.archives = ArchivesPage(app)
        for p in (QWidget(), self.studio, self.library, self.calendar, self.analytics, self.settings_page,
                  self.archives):
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
        self.studio.jobs_btn.clicked.connect(lambda: self.toggle_jobs(True))
        self.studio.saved.connect(self.jobs_panel.set_saved)          # « Enregistré » en haut des tâches
        self.studio.save_lbl.changed.connect(self.jobs_panel.set_message)
        self.toast = Toast(self.dock)
        self.tips = TipFilter(self)                      # infobulles aux couleurs du logiciel
        QApplication.instance().installEventFilter(self.tips)
        self.statusBar().hide()

        # signaux
        self.studio.new_project.connect(self.new_project)
        self.studio.open_models.connect(lambda: (self.go(5), self.settings_page.show_models()))
        self.studio.new_blank.connect(self.new_blank)
        self.studio.view_changed.connect(self._sync_dock)
        self.studio.goto_tab.connect(self.start_parcours)
        self.studio.open_ai_settings.connect(lambda: (self.go(5), self.settings_page.show_models()))
        self.archives.restored.connect(lambda _pid: self.studio.refresh())
        self.settings_page.llm_changed.connect(self.studio.highlights.refresh_engines)
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
        app.projects.purge_untouched()
        self.start_parcours("free_edit")                  # ouverture dans Montage libre
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
        # Atelier de publication : lien des vidéos publiées à la main, cherché au démarrage puis toutes les heures
        self._links = QTimer(self)
        self._links.timeout.connect(self.check_links)
        self._links.start(30_000)                          # premier passage 30 s après l'ouverture, puis toutes les heures

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

    def check_links(self) -> int | None:
        """Toutes les heures : relevé des statistiques arrivées à leur étape (6 h, J+1, J+3…), puis recherche des
        liens (tâche « match_published ») s'il y a des publications à confirmer."""
        self._links.setInterval(3_600_000)
        self.check_stats()
        if not self.app.publications.awaiting_link():
            return None
        if self.app.db.query_one("SELECT 1 FROM job WHERE kind = 'match_published' AND status IN "
                                 "('pending','running')"):
            return None
        return self.app.jobs.submit("match_published", "Recherche des liens des vidéos publiées", {})

    def check_stats(self) -> int | None:
        """Relevé automatique des pages publiques, seulement si une vidéo est arrivée à une étape du calendrier."""
        from datetime import datetime, timezone

        from ..publish import public_stats
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        if not any(public_stats.due(t["published_at"], t["last_at"], now) for t in self.app.analytics.public_targets()):
            return None
        if self.app.db.query_one("SELECT 1 FROM job WHERE kind = 'refresh_stats' AND status IN ('pending','running')"):
            return None
        return self.app.jobs.submit("refresh_stats", "Statistiques des vidéos publiées", {})

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
            if QMessageBox.question(self, "Ouvrir", f"« {a['file_name']} » n'appartient à aucune vidéo du Studio.\n\n"
                                    "Créer une vidéo à partir de ce média ?") != QMessageBox.Yes:
                return False
            concepts = self.app.concepts.list_concepts()
            pid = self.app.projects.create(concepts[0].id, Path(a["file_name"]).stem[:60] or "Vidéo")
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
        self.studio.refresh()

    def show_shortcuts(self) -> None:
        from .shortcuts import ShortcutsDialog
        ShortcutsDialog(self).exec()

    def toggle_jobs(self, force: bool | None = None) -> None:
        show = (not self.jobs_pop.isVisible()) if force is None or not isinstance(force, bool) else force
        if show:
            self.jobs_panel.refresh()
            b = self.studio.jobs_btn                     # bouton en haut du Studio ; ailleurs, au-dessus du dock
            if b.isVisible():
                self.jobs_pop.show_above(b, "right", below=True)
            else:
                self.jobs_pop.show_above(self.dock, "center")
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
            clipping = self.studio._mode() == "smart_clipping"
            import_paths(self.app, self, paths, self.studio.project_id, {"auto_clips": True} if clipping else None)
            return self.studio.project_id
        vids = [Path(p) for p in paths if Path(p).suffix.lower() in VIDEO_EXT and Path(p).is_file()]
        if not vids:
            from .importing import import_paths
            import_paths(self.app, self, paths)
            self.notify("Ajouté à Médias.")
            return None
        pid = self.app.projects.quick_create(self.app.concepts, "smart_clipping", vids[0].stem[:60], "9:16")
        for v in vids:
            self.app.jobs.submit("import_file", f"Import · {v.name}",
                                 {"path": str(v), "project_id": pid, "auto_clips": True})
        self.open_project(pid)
        self.notify("Vidéo créée : transcription et recherche des extraits en cours…", 7000)
        return pid

    def notify(self, text: str, ms: int = 5000) -> None:
        self.toast.show_message(text, ms)

    # -- parcours (dock) ------------------------------------------------------
    def start_parcours(self, mode: str) -> None:
        """Clic sur un parcours : le projet ouvert passe dans cette interface (un projet traverse tous les
        parcours). Sans projet ouvert : le dernier projet, sinon un projet vierge.

        Un projet vierge n'est conservé qu'à la première modification (voir `ProjectService.is_untouched`).
        Le bouton « Nouveau projet » en ouvre un nouveau dans le parcours affiché."""
        pid = self.studio.project_id
        p = self.app.projects.get(pid) if pid else None
        if p is not None and mode != "export" and p["mode"] != mode and self.app.projects.is_untouched(pid) \
                and self.studio.tl.format.aspect == theme.DEFAULT_ASPECT.get(p["mode"], "9:16"):
            self.new_blank(mode)                   # projet vierge intact (format non choisi) : refait pour cet onglet
            self.app.projects.discard_if_untouched(pid)
            self.go(1)
            return
        if not (pid and self.app.projects.get(pid)):
            recent = self.app.projects.recent(1)
            if not recent:
                self.new_blank("free_edit" if mode == "export" else mode)
                if mode == "export":
                    self.studio.show_export()
                return
            pid = recent[0]["id"]
        if mode == "export":                       # onglet Export : la vidéo du projet ouvert
            if self.studio.project_id != pid:
                self.studio.open_project(pid)
            self.go(1)
            self.studio.show_export()
            return
        self.studio.open_project(pid, mode)
        self.go(1)

    def new_blank(self, mode: str) -> int:
        pid = self.app.projects.quick_create(self.app.concepts, mode, "", theme.DEFAULT_ASPECT.get(mode, "9:16"),
                                             blank=True)
        self.open_project(pid)
        return pid

    def _sync_dock(self) -> None:
        mode = None
        if self.pages.currentWidget() is self.studio and self.studio.project_id:
            p = self.app.projects.get(self.studio.project_id)
            mode = "export" if self.studio._export_mode else p["mode"] if p else None
        self.dock.set_active(mode)
        col = (theme.EXPORT_TAB[2] if mode == "export" else theme.PARCOURS[mode][2] if mode in theme.PARCOURS
               else None)
        theme.set_accent(self, col)
        self.studio.restyle_accent()
        self.studio.panel._tab_title(self.studio.panel.currentIndex())

    def set_theme(self, mode: str) -> None:
        theme.apply(QApplication.instance(), mode)
        self.setStyleSheet("")
        self.dock.restyle()
        self.drawer.restyle()
        self.studio.format_pick.restyle()
        self.studio.jobs_btn.restyle()
        self._sync_dock()

    # -- actions ------------------------------------------------------------
    def new_project(self, concept_id=None) -> None:
        if not self.app.concepts.list_concepts():
            QMessageBox.information(self, "Nouvelle vidéo", "Créez d'abord un concept éditorial.")
            self.edit_concept(None)
            if not self.app.concepts.list_concepts():
                return
        dlg = NewProjectDialog(self.app, concept_id, self)
        if dlg.exec() and dlg.project_id:
            self.open_project(dlg.project_id)

    def open_project(self, project_id: int) -> None:
        self.studio.open_project(project_id)
        self.go(1)

    def import_project(self) -> None:
        from PySide6.QtWidgets import QFileDialog

        from .pages.studio import default_export_dir
        path, _ = QFileDialog.getOpenFileName(self, "Importer une vidéo enregistrée", str(default_export_dir()),
                                              "Vidéo Studio Vidéo (*.studioproj)")
        if not path:
            return
        self._import_job = self.app.jobs.submit("import_project", "Import de la vidéo", {
            "path": path, "media_dir": str(default_export_dir() / "Vidéos importées")})
        self.notify("Import de la vidéo en cours…", 10000)

    def _on_project_imported(self, j) -> None:
        if j.status == "succeeded":
            r = j.result
            msg = f"Vidéo « {r['name']} » importée."
            if r["missing"]:
                msg += f"\n\nMédias introuvables ({len(r['missing'])}) : {', '.join(r['missing'][:5])}"
            QMessageBox.information(self, "Importer une vidéo enregistrée", msg)
            self.open_project(r["project_id"])
        elif j.status == "failed":
            QMessageBox.warning(self, "Importer une vidéo enregistrée", j.error[:600])

    def edit_concept(self, concept_id=None) -> None:
        if ConceptDialog(self.app, concept_id, self).exec():
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
        if j and j.kind == "match_published" and j.status == "succeeded":
            r = j.result or {}
            if r.get("confirmed"):
                names = ", ".join(c["account"] for c in r["confirmed"][:3])
                self.notify(f"Lien retrouvé et publication confirmée : {names}", 10000)
            if r.get("proposed"):
                self.notify(f"{r['proposed']} lien(s) retrouvé(s) à vérifier (Calendrier → À faire maintenant).",
                            12000)
            if self.pages.currentWidget() is self.calendar:
                self.calendar.refresh()
            return
        if j and j.kind == "auto_clips" and j.status in ("succeeded", "failed"):
            r = j.result or {}
            if j.status == "failed":
                self.notify(f"Extraits automatiques : {j.error.splitlines()[-1][:200] if j.error else 'échec'}", 10000)
            elif r.get("skipped"):
                self.notify(r["skipped"], 12000)
            else:
                self.notify(f"{r.get('count', 0)} extrait(s) proposé(s).", 8000)
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
        self.studio.jobs_btn.set_jobs(n, failed)

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
        if hasattr(self, "_import_refresh"):
            self._import_refresh.stop()            # fermeture : plus de rafraîchissement différé
        self._links.stop()
        self._reminders.stop()
        self.studio.leave_project()
        s = QSettings(str(self.app.paths.root / "ui.ini"), QSettings.IniFormat)
        s.setValue("geometry", self.saveGeometry())
        self.studio.player.unload()
        self.studio.mplayer.stop()
        self.updater.stop()
        super().closeEvent(e)
