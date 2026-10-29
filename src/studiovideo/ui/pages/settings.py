"""Paramètres (§2.1 F) : concepts et comptes, général, stockage, matériel, à propos."""
from __future__ import annotations

import os
import platform
import shutil
import sys
from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (QAbstractItemView, QCheckBox, QComboBox, QFormLayout, QHBoxLayout, QHeaderView,
                               QListWidget, QListWidgetItem, QMessageBox, QTableWidget, QTableWidgetItem, QTabWidget,
                               QVBoxLayout, QWidget)

from ... import __version__
from ...core import AppContext
from ...media import downloader, ffmpeg
from ...services import secrets
from ...transcribe import engines as asr
from ...update import rollback
from .. import theme
from ..widgets import Card, PageHeader, button, label
from ..widgets import ask

CONNECTION_LABELS = {"disconnected": "Non connecté", "connected": "Connecté", "expired": "Expiré",
                     "revoked": "Révoqué", "error": "Erreur"}


def _cell(w: QWidget) -> QWidget:
    """Bouton centré dans une cellule de tableau (sans être étiré)."""
    box = QWidget()
    h = QHBoxLayout(box)
    h.setContentsMargins(4, 3, 8, 3)
    h.addStretch(1)
    h.addWidget(w)
    return box


def _fit_height(t: QTableWidget) -> None:
    """Le tableau prend la hauteur de ses lignes : pas de barre de défilement interne."""
    h = t.horizontalHeader().height() + sum(t.rowHeight(i) for i in range(t.rowCount())) + 4
    t.setFixedHeight(max(80, h))
    t.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)



class SettingsPage(QWidget):
    theme_changed = Signal(str)
    check_updates = Signal()
    llm_changed = Signal()

    def __init__(self, app: AppContext, updater_status: callable, parent=None):
        super().__init__(parent)
        self.app = app
        self.updater_status = updater_status
        lay = QVBoxLayout(self)
        lay.setContentsMargins(36, 30, 36, 8)
        lay.setSpacing(12)
        lay.addWidget(PageHeader("Paramètres"))
        self.tabs = QTabWidget()
        from PySide6.QtWidgets import QScrollArea

        def column(*widgets, width=860):
            body = QWidget()
            col = QVBoxLayout(body)
            col.setContentsMargins(0, 8, 0, 16)
            col.setSpacing(14)
            for x in widgets:
                col.addWidget(x)
            col.addStretch(1)
            body.setMaximumWidth(width)
            box = QWidget()
            bl = QHBoxLayout(box)
            bl.setContentsMargins(0, 0, 0, 0)
            bl.addWidget(body, 1)
            bl.addStretch(0)
            sc = QScrollArea()
            sc.setWidgetResizable(True)
            sc.setFrameShape(QScrollArea.NoFrame)
            sc.setWidget(box)
            return sc

        self.tabs.addTab(column(self._general_tab(), self._storage_tab(), self._about_tab()), "Général")
        # (applications des réseaux : « Ajouter un compte » de l'onglet Export ; voix hors ligne et traduction :
        # roues crantées de l'onglet Traduction)
        self.tabs.addTab(column(self._hardware_tab(), width=820), "Matériel et moteurs")
        lay.addWidget(self.tabs, 1)

    def show_about(self) -> None:
        self.show_tab("À propos")

    def refresh(self) -> None:
        """Les concepts et leurs chaînes sont dans l'onglet Export ; rien à relire ici."""

    def _general_tab(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(0, 0, 0, 0)
        card = Card()
        form = QFormLayout()
        self.theme_combo = QComboBox()
        self.theme_combo.addItem("Clair", "light")
        self.theme_combo.addItem("Sombre", "dark")
        self.theme_combo.setCurrentIndex(self.theme_combo.findData(self.app.settings.get("theme")))
        self.theme_combo.currentIndexChanged.connect(self._theme)
        form.addRow("Thème", self.theme_combo)
        self.workers = QComboBox()
        for n in (1, 2, 3, 4):
            self.workers.addItem(f"{n} tâche(s) simultanée(s)", n)
        self.workers.setCurrentIndex(self.workers.findData(int(self.app.settings.get("max_workers"))))
        self.workers.currentIndexChanged.connect(
            lambda: self.app.settings.set("max_workers", self.workers.currentData()))
        form.addRow("Traitements en parallèle", self.workers)
        self.proxies_cb = QCheckBox("Créer un aperçu léger des vidéos lourdes (4K, HEVC…) pour une lecture fluide")
        self.proxies_cb.setChecked(bool(self.app.settings.get("proxies", True)))
        self.proxies_cb.toggled.connect(lambda v: self.app.settings.set("proxies", bool(v)))
        form.addRow("Aperçus", self.proxies_cb)
        self.auto_tr_cb = QCheckBox("Transcrire automatiquement chaque vidéo importée (langue détectée)")
        self.auto_tr_cb.setChecked(bool(self.app.settings.get("auto_transcribe", True)))
        self.auto_tr_cb.toggled.connect(lambda v: self.app.settings.set("auto_transcribe", bool(v)))
        form.addRow("Transcription", self.auto_tr_cb)
        self.yt_auto = QCheckBox("Mettre en ligne automatiquement à l'heure prévue (comptes connectés)")
        self.yt_auto.setChecked(bool(self.app.settings.get("auto_upload", True)))
        self.yt_auto.toggled.connect(lambda v: self.app.settings.set("auto_upload", bool(v)))
        form.addRow("Publications programmées", self.yt_auto)
        card.lay.addLayout(form)
        card.lay.addWidget(label("Le nombre de traitements parallèles s'applique au prochain démarrage.",
                                 "faint"))
        lay.addWidget(card)
        lay.addStretch(1)
        return w

    def _theme(self) -> None:
        mode = self.theme_combo.currentData()
        self.app.settings.set("theme", mode)
        self.theme_changed.emit(mode)

    # -- stockage -----------------------------------------------------------
    def _storage_tab(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(0, 0, 0, 0)
        p = self.app.paths
        info = Card()
        info.lay.addWidget(label("Emplacement des données", "h2"))
        info.lay.addWidget(label("Vidéos, réglages, historique et modèles sont conservés ici, séparément du "
                                 "programme : une mise à jour ne les supprime jamais.", "muted", wrap=True))
        loc = label(str(p.root))
        loc.setTextInteractionFlags(Qt.TextSelectableByMouse)
        info.lay.addWidget(loc)
        usage = shutil.disk_usage(p.root)
        info.lay.addWidget(label(f"Espace libre sur ce disque : {usage.free / 2**30:.1f} Go", "faint"))
        hb = QHBoxLayout()
        hb.addWidget(button("Ouvrir le dossier", on_click=lambda: _open_folder(p.root)))
        hb.addWidget(button("Vider le cache des miniatures", on_click=self._clear_cache))
        hb.addStretch(1)
        info.lay.addLayout(hb)
        lay.addWidget(info)

        wf = Card()
        wf.lay.addWidget(label("Dossier surveillé", "h2"))
        wf.lay.addWidget(label("Les vidéos, sons et images déposés dans ce dossier (ou un de ses sous-dossiers "
                               "directs) sont importés automatiquement dans Médias, une fois la copie "
                               "terminée. Les doublons sont ignorés ; les fichiers ne sont ni déplacés ni modifiés.",
                               "muted", wrap=True))
        self.watch_lbl = label("", wrap=True)
        wf.lay.addWidget(self.watch_lbl)
        hw = QHBoxLayout()
        hw.addWidget(button("Choisir le dossier…", on_click=self._pick_watch))
        self.watch_off = button("Arrêter la surveillance", "ghost", on_click=lambda: self._set_watch(""))
        hw.addWidget(self.watch_off)
        hw.addStretch(1)
        wf.lay.addLayout(hw)
        lay.addWidget(wf)
        self._show_watch()

        bk = Card()
        bk.lay.addWidget(label("Sauvegardes de la base", "h2"))
        bk.lay.addWidget(label("Une sauvegarde est créée automatiquement chaque jour au démarrage (les 7 dernières "
                               "sont gardées), ainsi qu'avant chaque migration et chaque mise à jour (les 20 plus "
                               "récentes).", "muted", wrap=True))
        self.backup_list = QListWidget()
        self.backup_list.setMaximumHeight(170)
        bk.lay.addWidget(self.backup_list, 1)
        hb2 = QHBoxLayout()
        hb2.addWidget(button("Sauvegarder maintenant", on_click=self._backup_now))
        hb2.addWidget(button("Restaurer la sélection…", on_click=self._restore))
        hb2.addStretch(1)
        bk.lay.addLayout(hb2)
        lay.addWidget(bk, 1)
        self._refresh_backups()
        return w

    def _show_watch(self) -> None:
        f = self.app.settings.get("watch_folder", "") or ""
        self.watch_lbl.setText(f"Surveillé : {f}" if f else "Aucun dossier surveillé.")
        self.watch_off.setEnabled(bool(f))

    def _pick_watch(self) -> None:
        from PySide6.QtWidgets import QFileDialog
        f = QFileDialog.getExistingDirectory(self, "Dossier à surveiller", self.app.settings.get("watch_folder", "") or "")
        if f:
            self._set_watch(f)

    def _set_watch(self, folder: str) -> None:
        self.app.settings.set("watch_folder", folder)
        self._show_watch()

    def _refresh_backups(self) -> None:
        self.backup_list.clear()
        for b in self.app.db.list_backups():
            it = QListWidgetItem(f"{b.name}   ({b.stat().st_size // 1024} Ko)")
            it.setData(Qt.UserRole, str(b))
            self.backup_list.addItem(it)

    def _backup_now(self) -> None:
        self.app.db.backup("manuelle")
        self._refresh_backups()

    def _restore(self) -> None:
        it = self.backup_list.currentItem()
        if not it:
            return
        if self.app.jobs.active_count():
            QMessageBox.warning(self, "Restaurer", "Attendez la fin des tâches en cours avant de restaurer.")
            return
        if ask(self, "Restaurer", "Remplacer les données actuelles par cette sauvegarde ?\n"
                                "Une sauvegarde de l'état actuel sera faite d'abord.\n"
                                "Le logiciel devra être redémarré."):
            self.app.db.restore(Path(it.data(Qt.UserRole)))
            QMessageBox.information(self, "Restaurer", "Sauvegarde restaurée. Redémarrez Studio Vidéo.")
            self._refresh_backups()

    def _clear_cache(self) -> None:
        for f in self.app.paths.thumbnails.glob("*.jpg"):
            f.unlink(missing_ok=True)
        self.app.db.connect().execute("UPDATE source_asset SET thumbnail_path = ''")
        QMessageBox.information(self, "Cache", "Cache vidé. Les miniatures seront recréées à la réimportation.")

    # -- matériel -----------------------------------------------------------
    def _hardware_tab(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(14)
        card = Card(padding=14, spacing=4)
        form = QFormLayout()
        form.setVerticalSpacing(3)
        ram = _total_ram_gb()
        rows = [("Système", f"{platform.system()} {platform.release()} ({platform.machine()})"),
                ("Processeur", platform.processor() or platform.machine()),
                ("Cœurs logiques", str(os.cpu_count())),
                ("Mémoire vive", f"{ram:.1f} Go" if ram else "inconnue"),
                ("FFmpeg", ffmpeg.version() if ffmpeg.available() else "introuvable"),
                ("Téléchargement (yt-dlp)", downloader.yt_dlp_version() or "non installé"),
                ("Coffre d'identifiants", "Gestionnaire d'identifiants Windows" if secrets.available()
                 else "indisponible : clés API non enregistrables"),
                ("Transcription locale", ("faster-whisper " + _fw_version()) if _fw_version() else "non incluse"),
                ("Calcul de la transcription", "Processeur (CPU, int8). L'accélération par carte graphique "
                                               "n'est pas encore prise en charge.")]
        for k, v in rows:
            lb = label(v, "muted", wrap=True, hint=False)
            lb.setTextInteractionFlags(Qt.TextSelectableByMouse)
            form.addRow(label(k, "faint"), lb)
        card.lay.addLayout(form)
        from ..widgets import DimOnHover
        dim = DimOnHover(0.45)
        dl = QVBoxLayout(dim)
        dl.setContentsMargins(0, 0, 0, 0)
        dl.addWidget(card)
        lay.addWidget(dim)
        lay.addWidget(self._models_card(ram))
        from ..engine_panels import LlmPanel
        llm = Card()
        llm.lay.addWidget(label("Modèle de langage (facultatif)", "h2"))
        llm.lay.addWidget(label("Utilisé par Clipping pour mieux comprendre le sens des échanges, et par Traduction "
                                "s'il est choisi. Sans lui, tout fonctionne hors ligne. Ollama tourne sur votre PC "
                                "(rien ne sort) ; une API distante reçoit le texte analysé.", "muted", wrap=True))
        self.llm_panel = LlmPanel(self.app)
        self.llm_panel.changed.connect(self.llm_changed.emit)
        llm.lay.addWidget(self.llm_panel)
        lay.addWidget(llm)
        lay.addStretch(1)
        return w

    def _models_card(self, ram) -> Card:
        from ...transcribe import engines
        card = Card()
        card.lay.addWidget(label("Modèles de transcription (reconnaissance vocale)", "h2"))
        rec = engines.recommended_model(ram, os.cpu_count())
        card.lay.addWidget(label(f"Les modèles sont téléchargés une fois depuis Hugging Face puis fonctionnent "
                                 f"hors ligne. Recommandé pour ce PC : « {rec} ».", "muted", wrap=True))
        self.models_table = QTableWidget(0, 4)
        self.models_table.setHorizontalHeaderLabels(["Modèle", "Taille", "État", ""])
        self.models_table.verticalHeader().hide()
        self.models_table.setShowGrid(False)
        self.models_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.models_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        self.models_table.verticalHeader().setDefaultSectionSize(40)
        card.lay.addWidget(self.models_table)
        self._model_jobs: dict[int, str] = {}
        self._rec_model = rec
        self._fill_models()
        return card

    def _model_rows(self) -> list[tuple]:
        """(clé, libellé, taille Mo, info-bulle, installé, moteur disponible) : transcription puis séparation."""
        from ...edit import separate
        from ...transcribe import engines
        rows = [(m.key, m.label + ("  ★ recommandé" if m.key == self._rec_model else ""), m.size_mb,
                 m.note + f" Mémoire conseillée : {m.min_ram_gb:g} Go.",
                 engines.is_installed(self.app.paths.models, m.key), engines.available()) for m in engines.MODELS]
        from ...ai import diarize
        spk_inst = diarize.installed(self.app.paths.models)
        rows += [("spk:" + m.key, "Intervenants — " + m.label, m.size_mb,
                  "Reconnaît qui parle, phrase par phrase, sur votre PC. WeSpeaker (Apache-2.0), entraîné sur VoxCeleb.",
                  m.key in spk_inst, separate.available()) for m in diarize.MODELS]
        sep_inst = separate.installed(self.app.paths.models)
        rows += [("sep:" + m.key, "Séparation voix / musique — " + m.label, m.size_mb,
                  "Isole la voix du reste (musique, ambiance). Projet Ultimate Vocal Remover, licence MIT.",
                  m.key in sep_inst, separate.available()) for m in separate.MODELS]
        return rows

    def _fill_models(self) -> None:
        t = self.models_table
        rows = self._model_rows()
        t.setRowCount(len(rows))
        running = set(self._model_jobs.values())
        for i, (key, text, size, tip, inst, avail) in enumerate(rows):
            name = QTableWidgetItem(text)
            name.setToolTip(tip)
            t.setItem(i, 0, name)
            t.setItem(i, 1, QTableWidgetItem(f"{size} Mo"))
            state = "Téléchargement…" if key in running else ("Installé" if inst else "Non installé")
            t.setItem(i, 2, QTableWidgetItem(state))
            if key in running:
                b = button("Annuler", "ghost", on_click=lambda _=False, k=key: self._cancel_model(k))
            elif inst:
                b = button("Supprimer", "ghost", on_click=lambda _=False, k=key: self._delete_model(k))
            else:
                b = button("Télécharger", "soft", on_click=lambda _=False, k=key: self._download_model(k),
                           icon="download")
                b.setEnabled(avail)
            st = t.item(i, 2)
            st.setForeground(QColor(theme.color("ok") if inst else theme.color("muted")))
            t.setCellWidget(i, 3, _cell(b))
        t.resizeColumnsToContents()
        t.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        _fit_height(t)

    def _download_model(self, key: str) -> None:
        jid = self.app.jobs.submit("download_model", f"Téléchargement du modèle {key}", {"model": key})
        self._model_jobs[jid] = key
        self._fill_models()

    def _cancel_model(self, key: str) -> None:
        for jid, k in list(self._model_jobs.items()):
            if k == key:
                self.app.jobs.cancel(jid)

    def _delete_model(self, key: str) -> None:
        from ...transcribe import engines
        if ask(self, "Modèle", f"Supprimer le modèle « {key} » ? Il pourra être retéléchargé."
                                ):
            if key.startswith("spk:"):
                from ...ai import diarize
                diarize.model_path(self.app.paths.models, diarize.BY_KEY[key[4:]]).unlink(missing_ok=True)
            elif key.startswith("sep:"):
                from ...edit import separate
                separate.model_path(self.app.paths.models, separate.BY_KEY[key[4:]]).unlink(missing_ok=True)
            else:
                engines.delete_model(self.app.paths.models, key)
            self._fill_models()

    def show_tab(self, name: str) -> None:
        name = {"À propos": "Général", "Stockage et sauvegardes": "Général"}.get(name, name)
        for i in range(self.tabs.count()):
            if self.tabs.tabText(i) == name:
                self.tabs.setCurrentIndex(i)
                return

    def show_models(self) -> None:
        self.show_tab("Matériel et moteurs")

    def on_job(self, job) -> None:
        if job.kind == "connect_account" and job.status in ("succeeded", "failed", "cancelled"):
            win = self.window()
            if hasattr(win, "notify"):
                win.notify(f"✓ Connecté à la chaîne « {job.result.get('channel')} »." if job.status == "succeeded"
                           else f"⚠ Connexion impossible : {(job.error or 'annulée')[:300]}", 8000)
        if job.id in getattr(self, "_model_jobs", {}):
            if job.status in ("succeeded", "failed", "cancelled"):
                self._model_jobs.pop(job.id, None)
                if job.status == "failed":
                    QMessageBox.warning(self, "Téléchargement", job.error[:600])
                self._fill_models()
            elif job.status == "running" and job.progress:
                row = next((i for i, r in enumerate(self._model_rows()) if r[0] == self._model_jobs[job.id]), None)
                if row is not None:
                    self.models_table.setItem(row, 2, QTableWidgetItem(f"Téléchargement… {int(job.progress * 100)} %"))

    # -- à propos -----------------------------------------------------------
    def _about_tab(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(0, 0, 0, 0)
        card = Card()
        card.lay.addWidget(label("Studio Vidéo", "h1"))
        card.lay.addWidget(label(f"Version {__version__}", "muted"))
        available, reason = self.updater_status()
        form = QFormLayout()
        self.channel = QComboBox()
        self.channel.addItem("Stable", "stable")
        self.channel.addItem("Bêta (versions de test)", "beta")
        self.channel.setCurrentIndex(self.channel.findData(self.app.settings.get("update_channel")))
        self.channel.currentIndexChanged.connect(self._channel)
        form.addRow("Canal de mise à jour", self.channel)
        card.lay.addLayout(form)
        hb = QHBoxLayout()
        b = button("Rechercher des mises à jour", "primary", on_click=self.check_updates.emit)
        b.setEnabled(available)
        hb.addWidget(b)
        prev = rollback.previous_version(self.app.paths)
        rb = button(f"Revenir à la version {prev}" if prev else "Revenir à la version précédente",
                    on_click=self._rollback)
        rb.setEnabled(bool(prev) and sys.platform == "win32")
        rb.setToolTip("Réinstalle la version précédente conservée lors de la dernière mise à jour.")
        hb.addWidget(rb)
        hb.addStretch(1)
        card.lay.addLayout(hb)
        if not available:
            card.lay.addWidget(label(reason, "faint", wrap=True))
        card.lay.addWidget(label(f"Journal des mises à jour : {self.app.paths.logs / 'updates.log'}", "faint",
                                 wrap=True))
        lay.addWidget(card)
        lay.addStretch(1)
        return w

    def _channel(self) -> None:
        self.app.settings.set("update_channel", self.channel.currentData())
        QMessageBox.information(self, "Canal", "Le nouveau canal sera utilisé au prochain démarrage.")

    def _rollback(self) -> None:
        if self.app.jobs.active_count():
            QMessageBox.warning(self, "Restauration", "Attendez la fin des tâches en cours.")
            return
        if not ask(self, "Revenir à la version précédente",
                                "Studio Vidéo va se fermer, réinstaller la version précédente et restaurer "
                                "la base telle qu'elle était avant la mise à jour.\nContinuer ?"):
            return
        try:
            self.app.db.backup("avant-retour-version-precedente")
            rollback.launch_rollback(self.app.paths)
        except RuntimeError as exc:
            QMessageBox.warning(self, "Restauration", str(exc))
            return
        self.window().close()


def _fw_version() -> str | None:
    try:
        import faster_whisper
        return faster_whisper.__version__
    except Exception:
        return None


def _open_folder(p: Path) -> None:
    from PySide6.QtCore import QUrl
    from PySide6.QtGui import QDesktopServices
    QDesktopServices.openUrl(QUrl.fromLocalFile(str(p)))


def _total_ram_gb() -> float | None:
    try:
        if sys.platform == "win32":
            import ctypes

            class MS(ctypes.Structure):
                _fields_ = [("l", ctypes.c_ulong), ("load", ctypes.c_ulong), ("total", ctypes.c_ulonglong),
                            ("avail", ctypes.c_ulonglong), ("tp", ctypes.c_ulonglong), ("ap", ctypes.c_ulonglong),
                            ("tv", ctypes.c_ulonglong), ("av", ctypes.c_ulonglong), ("ae", ctypes.c_ulonglong)]
            m = MS()
            m.l = ctypes.sizeof(MS)
            ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(m))
            return m.total / 2**30
        return os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES") / 2**30
    except (OSError, ValueError, AttributeError):
        return None
