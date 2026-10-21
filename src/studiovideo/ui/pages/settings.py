"""Paramètres (§2.1 F) : concepts et comptes, général, stockage, matériel, à propos."""
from __future__ import annotations

import os
import platform
import shutil
import sys
from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (QAbstractItemView, QComboBox, QFormLayout, QHBoxLayout, QHeaderView, QListWidget,
                               QListWidgetItem, QMessageBox, QTableWidget, QTableWidgetItem, QTabWidget,
                               QVBoxLayout, QWidget)

from ... import __version__
from ...core import AppContext
from ...media import downloader, ffmpeg
from ...services import secrets
from ...services.concepts import ConceptError
from ...transcribe import engines as asr
from ...update import rollback
from ..dialogs import AccountDialog
from ..widgets import Card, PageHeader, button, label

CONNECTION_LABELS = {"disconnected": "Non connecté", "connected": "Connecté", "expired": "Expiré",
                     "revoked": "Révoqué", "error": "Erreur"}


class SettingsPage(QWidget):
    edit_concept = Signal(object)
    theme_changed = Signal(str)
    check_updates = Signal()
    llm_changed = Signal()

    def __init__(self, app: AppContext, updater_status: callable, parent=None):
        super().__init__(parent)
        self.app = app
        self.updater_status = updater_status
        lay = QVBoxLayout(self)
        lay.setContentsMargins(32, 28, 32, 28)
        lay.setSpacing(14)
        lay.addWidget(PageHeader("Paramètres"))
        self.tabs = QTabWidget()
        self.tabs.addTab(self._concepts_tab(), "Concepts et comptes")
        self.tabs.addTab(self._general_tab(), "Général")
        self.tabs.addTab(self._storage_tab(), "Stockage et sauvegardes")
        from PySide6.QtWidgets import QScrollArea
        hw = QScrollArea()
        hw.setWidgetResizable(True)
        hw.setFrameShape(QScrollArea.NoFrame)
        hw.setWidget(self._hardware_tab())
        self.tabs.addTab(hw, "Matériel et moteurs")
        self.tabs.addTab(self._about_tab(), "À propos")
        lay.addWidget(self.tabs, 1)

    def show_about(self) -> None:
        self.tabs.setCurrentIndex(4)

    # -- concepts et comptes ------------------------------------------------
    def _concepts_tab(self) -> QWidget:
        w = QWidget()
        h = QHBoxLayout(w)
        h.setContentsMargins(0, 16, 0, 0)
        h.setSpacing(16)
        left = Card()
        left.lay.addWidget(label("Concepts", "h2"))
        self.concept_list = QListWidget()
        self.concept_list.itemDoubleClicked.connect(lambda it: self.edit_concept.emit(it.data(Qt.UserRole)))
        left.lay.addWidget(self.concept_list, 1)
        lb = QHBoxLayout()
        lb.addWidget(button("Nouveau", on_click=lambda: self.edit_concept.emit(None)))
        lb.addWidget(button("Modifier", on_click=self._edit_selected_concept))
        lb.addWidget(button("Archiver", on_click=self._archive_concept))
        left.lay.addLayout(lb)
        h.addWidget(left, 1)

        right = Card()
        right.lay.addWidget(label("Comptes sociaux", "h2"))
        right.lay.addWidget(label("Un compte peut appartenir à plusieurs concepts. La connexion officielle "
                                  "(OAuth) aux plateformes arrive avec le module de publication (V3).",
                                  "muted", wrap=True))
        self.acc_table = QTableWidget(0, 5)
        self.acc_table.setHorizontalHeaderLabels(["Plateforme", "Nom", "Langue", "Connexion", "Concepts"])
        self.acc_table.verticalHeader().hide()
        self.acc_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.acc_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.acc_table.horizontalHeader().setSectionResizeMode(4, QHeaderView.Stretch)
        self.acc_table.setShowGrid(False)
        right.lay.addWidget(self.acc_table, 1)
        rb = QHBoxLayout()
        rb.addWidget(button("Nouveau compte", on_click=self._new_account))
        rb.addWidget(button("Supprimer", "danger", on_click=self._delete_account))
        rb.addStretch(1)
        right.lay.addLayout(rb)
        h.addWidget(right, 2)
        self.refresh()
        return w

    def refresh(self) -> None:
        self.concept_list.clear()
        for c in self.app.concepts.list_concepts():
            n = len(self.app.concepts.concept_accounts(c.id))
            it = QListWidgetItem(f"{c.name}   ·  {n} compte(s)")
            it.setData(Qt.UserRole, c.id)
            self.concept_list.addItem(it)
        accounts = self.app.concepts.list_accounts()
        self.acc_table.setRowCount(len(accounts))
        for i, a in enumerate(accounts):
            concepts = ", ".join(c.name for c in self.app.concepts.concepts_of_account(a.id)) or "—"
            for j, v in enumerate((a.platform.label, a.name, a.language.upper(),
                                   CONNECTION_LABELS.get(a.connection_status, a.connection_status), concepts)):
                item = QTableWidgetItem(v)
                item.setData(Qt.UserRole, a.id)
                self.acc_table.setItem(i, j, item)

    def _edit_selected_concept(self) -> None:
        it = self.concept_list.currentItem()
        if it:
            self.edit_concept.emit(it.data(Qt.UserRole))

    def _archive_concept(self) -> None:
        it = self.concept_list.currentItem()
        if it and QMessageBox.question(self, "Archiver", "Archiver ce concept ? Il disparaîtra des listes, "
                                       "ses projets et médias sont conservés.") == QMessageBox.Yes:
            self.app.concepts.update_concept(it.data(Qt.UserRole), archived=1)
            self.refresh()

    def _new_account(self) -> None:
        if AccountDialog(self.app, self).exec():
            self.refresh()

    def _delete_account(self) -> None:
        row = self.acc_table.currentRow()
        if row < 0:
            return
        aid = self.acc_table.item(row, 0).data(Qt.UserRole)
        if QMessageBox.question(self, "Supprimer", "Supprimer ce compte ? Il sera retiré de tous les concepts."
                                ) != QMessageBox.Yes:
            return
        try:
            self.app.concepts.delete_account(aid)
        except ConceptError as exc:
            QMessageBox.warning(self, "Compte", str(exc))
        self.refresh()

    # -- général ------------------------------------------------------------
    def _general_tab(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(0, 16, 0, 0)
        card = Card()
        form = QFormLayout()
        self.theme_combo = QComboBox()
        self.theme_combo.addItem("Sombre", "dark")
        self.theme_combo.addItem("Clair", "light")
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
        lay.setContentsMargins(0, 16, 0, 0)
        p = self.app.paths
        info = Card()
        info.lay.addWidget(label("Emplacement des données", "h2"))
        info.lay.addWidget(label("Projets, réglages, historique et modèles sont conservés ici, séparément du "
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

        bk = Card()
        bk.lay.addWidget(label("Sauvegardes de la base", "h2"))
        bk.lay.addWidget(label("Une sauvegarde est créée automatiquement avant chaque migration et chaque "
                               "mise à jour. Les 20 plus récentes sont conservées.", "muted", wrap=True))
        self.backup_list = QListWidget()
        bk.lay.addWidget(self.backup_list, 1)
        hb2 = QHBoxLayout()
        hb2.addWidget(button("Sauvegarder maintenant", on_click=self._backup_now))
        hb2.addWidget(button("Restaurer la sélection…", on_click=self._restore))
        hb2.addStretch(1)
        bk.lay.addLayout(hb2)
        lay.addWidget(bk, 1)
        self._refresh_backups()
        return w

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
        if QMessageBox.question(self, "Restaurer", "Remplacer les données actuelles par cette sauvegarde ?\n"
                                "Une sauvegarde de l'état actuel sera faite d'abord.\n"
                                "Le logiciel devra être redémarré.") == QMessageBox.Yes:
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
        lay.setContentsMargins(0, 16, 0, 0)
        card = Card()
        form = QFormLayout()
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
            lb = label(v, wrap=True)
            lb.setTextInteractionFlags(Qt.TextSelectableByMouse)
            form.addRow(label(k, "muted"), lb)
        card.lay.addLayout(form)
        lay.addWidget(card)
        lay.addWidget(self._models_card(ram))
        lay.addWidget(self._llm_card())
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
        self.models_table.setMinimumHeight(230)
        card.lay.addWidget(self.models_table)
        self._model_jobs: dict[int, str] = {}
        self._rec_model = rec
        self._fill_models()
        return card

    def _llm_card(self) -> Card:
        from PySide6.QtWidgets import QLineEdit
        card = Card()
        card.lay.addWidget(label("Modèle de langage (facultatif)", "h2"))
        card.lay.addWidget(label(
            "Utilisé par « Moments forts » pour mieux comprendre le sens des échanges. Sans lui, l'analyse locale "
            "fonctionne hors ligne. Ollama tourne sur votre PC (rien ne sort) ; une API distante reçoit le texte "
            "de la transcription analysée.", "muted", wrap=True))
        cfg = self.app.settings.get("llm", None) or {}
        form = QFormLayout()
        self.llm_kind = QComboBox()
        for k, n in (("none", "Aucun"), ("ollama", "Ollama (local, sur ce PC)"),
                     ("openai", "API compatible OpenAI (OpenAI, Mistral, Groq, LM Studio…)")):
            self.llm_kind.addItem(n, k)
        self.llm_kind.setCurrentIndex(max(0, self.llm_kind.findData(cfg.get("kind", "none"))))
        self.llm_url = QLineEdit(cfg.get("base_url", ""))
        self.llm_url.setPlaceholderText("Adresse (vide = valeur par défaut)")
        self.llm_model = QComboBox()
        self.llm_model.setEditable(True)
        self.llm_model.setCurrentText(cfg.get("model", ""))
        self.llm_key = QLineEdit()
        self.llm_key.setEchoMode(QLineEdit.Password)
        self.llm_key.setPlaceholderText("Clé API (enregistrée dans le coffre Windows)" if secrets.available()
                                        else "Coffre d'identifiants indisponible")
        self.llm_key.setEnabled(secrets.available())
        form.addRow("Type", self.llm_kind)
        form.addRow("Adresse", self.llm_url)
        form.addRow("Modèle", self.llm_model)
        form.addRow("Clé API", self.llm_key)
        card.lay.addLayout(form)
        row = QHBoxLayout()
        row.addWidget(button("Tester la connexion", on_click=self._llm_test))
        row.addWidget(button("Enregistrer", "primary", on_click=self._llm_save))
        row.addStretch(1)
        card.lay.addLayout(row)
        self.llm_status = label("", "faint", wrap=True)
        card.lay.addWidget(self.llm_status)
        card.lay.addWidget(label("Traduction (facultatif)", "h2"))
        card.lay.addWidget(label("Clés personnelles pour DeepL ou Google Cloud Translation. Le modèle de langage "
                                 "ci-dessus peut aussi traduire (hors ligne avec Ollama).", "muted", wrap=True))
        tf = QFormLayout()
        self.deepl_key = QLineEdit()
        self.google_key = QLineEdit()
        self.eleven_key = QLineEdit()
        for w, name in ((self.deepl_key, "deepl_api_key"), (self.google_key, "google_api_key"),
                        (self.eleven_key, "elevenlabs_api_key")):
            w.setEchoMode(QLineEdit.Password)
            w.setEnabled(secrets.available())
            w.setPlaceholderText("Enregistrée" if secrets.get_secret(name) else
                                 ("Clé API" if secrets.available() else "Coffre d'identifiants indisponible"))
        tf.addRow("DeepL", self.deepl_key)
        tf.addRow("Google", self.google_key)
        tf.addRow("ElevenLabs (voix)", self.eleven_key)
        card.lay.addLayout(tf)
        card.lay.addWidget(button("Enregistrer les clés de traduction et de voix", on_click=self._save_translation_keys))
        self.llm_kind.currentIndexChanged.connect(self._llm_kind_changed)
        self._llm_kind_changed()
        return card

    def _save_translation_keys(self) -> None:
        try:
            for w, name in ((self.deepl_key, "deepl_api_key"), (self.google_key, "google_api_key"),
                            (self.eleven_key, "elevenlabs_api_key")):
                if w.text().strip():
                    secrets.set_secret(name, w.text().strip())
                    w.clear()
                    w.setPlaceholderText("Enregistrée")
        except secrets.SecretStoreUnavailable as exc:
            QMessageBox.warning(self, "Clés", str(exc))
            return
        self.llm_status.setText("Clés enregistrées dans le coffre Windows.")

    def _llm_kind_changed(self) -> None:
        k = self.llm_kind.currentData()
        for w in (self.llm_url, self.llm_model):
            w.setEnabled(k != "none")
        self.llm_key.setEnabled(k == "openai" and secrets.available())

    def _llm_test(self) -> None:
        from PySide6.QtWidgets import QApplication

        from ...ai.highlights import LLMError, check_llm
        k = self.llm_kind.currentData()
        if k == "none":
            return
        self.llm_status.setText("Connexion…")
        QApplication.processEvents()
        key = self.llm_key.text().strip() or (secrets.get_secret("llm_api_key") or "")
        try:
            models = check_llm(k, self.llm_url.text().strip(), key)
        except LLMError as exc:
            self.llm_status.setText(f"⚠ {exc}")
            return
        cur = self.llm_model.currentText()
        self.llm_model.clear()
        self.llm_model.addItems(models)
        self.llm_model.setCurrentText(cur or (models[0] if models else ""))
        self.llm_status.setText(f"Connexion réussie · {len(models)} modèle(s) disponible(s).")

    def _llm_save(self) -> None:
        k = self.llm_kind.currentData()
        cfg = {"kind": k, "base_url": self.llm_url.text().strip(), "model": self.llm_model.currentText().strip()}
        if k != "none" and not cfg["model"]:
            self.llm_status.setText("⚠ Indiquez le nom du modèle (bouton « Tester la connexion » pour la liste).")
            return
        if k == "openai" and self.llm_key.text().strip():
            try:
                secrets.set_secret("llm_api_key", self.llm_key.text().strip())
            except secrets.SecretStoreUnavailable as exc:
                self.llm_status.setText(f"⚠ {exc}")
                return
            self.llm_key.clear()
        self.app.settings.set("llm", cfg)
        self.llm_status.setText("Enregistré.")
        self.llm_changed.emit()

    def _model_rows(self) -> list[tuple]:
        """(clé, libellé, taille Mo, info-bulle, installé, moteur disponible) : transcription puis séparation."""
        from ...edit import separate
        from ...transcribe import engines
        rows = [(m.key, m.label + ("  ★ recommandé" if m.key == self._rec_model else ""), m.size_mb,
                 m.note + f" Mémoire conseillée : {m.min_ram_gb:g} Go.",
                 engines.is_installed(self.app.paths.models, m.key), engines.available()) for m in engines.MODELS]
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
                b = button("Télécharger", on_click=lambda _=False, k=key: self._download_model(k))
                b.setEnabled(avail)
            t.setCellWidget(i, 3, b)
        t.resizeColumnsToContents()
        t.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)

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
        if QMessageBox.question(self, "Modèle", f"Supprimer le modèle « {key} » ? Il pourra être retéléchargé."
                                ) == QMessageBox.Yes:
            if key.startswith("sep:"):
                from ...edit import separate
                separate.model_path(self.app.paths.models, separate.BY_KEY[key[4:]]).unlink(missing_ok=True)
            else:
                engines.delete_model(self.app.paths.models, key)
            self._fill_models()

    def show_models(self) -> None:
        self.tabs.setCurrentIndex(3)

    def on_job(self, job) -> None:
        if job.id in getattr(self, "_model_jobs", {}):
            if job.status in ("succeeded", "failed", "cancelled"):
                self._model_jobs.pop(job.id, None)
                if job.status == "failed":
                    QMessageBox.warning(self, "Téléchargement du modèle", job.error[:600])
                self._fill_models()
            elif job.status == "running" and job.progress:
                row = next((i for i, r in enumerate(self._model_rows()) if r[0] == self._model_jobs[job.id]), None)
                if row is not None:
                    self.models_table.setItem(row, 2, QTableWidgetItem(f"Téléchargement… {int(job.progress * 100)} %"))

    # -- à propos -----------------------------------------------------------
    def _about_tab(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(0, 16, 0, 0)
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
        if QMessageBox.question(self, "Revenir à la version précédente",
                                "Studio Vidéo va se fermer, réinstaller la version précédente et restaurer "
                                "la base telle qu'elle était avant la mise à jour.\nContinuer ?") != QMessageBox.Yes:
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
