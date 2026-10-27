"""Paramètres (§2.1 F) : concepts et comptes, général, stockage, matériel, à propos."""
from __future__ import annotations

import os
import platform
import shutil
import sys
from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor, QIcon
from PySide6.QtWidgets import (QAbstractItemView, QCheckBox, QComboBox, QFormLayout, QHBoxLayout, QHeaderView, QLabel,
                               QLineEdit, QListWidget, QListWidgetItem, QMessageBox, QTableWidget, QTableWidgetItem, QTabWidget,
                               QVBoxLayout, QWidget)

from ... import __version__
from ...core import AppContext
from ...media import downloader, ffmpeg
from ...services import secrets
from ...services.concepts import ConceptError
from ...transcribe import engines as asr
from ...update import rollback
from ..dialogs import AccountDialog
from .. import theme
from ..widgets import Card, PageHeader, button, hint_icon, label

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



class _ModelCombo(QComboBox):
    """Menu déroulant des modèles : la liste est demandée au service à la première ouverture."""

    def __init__(self, loader):
        super().__init__()
        self.loader, self.loaded = loader, False
        self.setMinimumWidth(260)

    def showPopup(self) -> None:
        if not self.loaded:
            self.loader()
        super().showPopup()

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
        self.tabs.addTab(self._publishing_tab(), "Publication")
        self.tabs.addTab(column(self._hardware_tab(), width=820), "Matériel et moteurs")
        lay.addWidget(self.tabs, 1)

    def show_about(self) -> None:
        self.show_tab("À propos")

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
        lb.addWidget(button("Nouveau", "soft", on_click=lambda: self.edit_concept.emit(None), icon="plus"))
        lb.addWidget(button("Modifier", "ghost", on_click=self._edit_selected_concept))
        lb.addWidget(button("Archiver", "ghost", on_click=self._archive_concept))
        lb.addStretch(1)
        left.lay.addLayout(lb)
        h.addWidget(left, 1)

        right = Card()
        right.lay.addWidget(label("Comptes sociaux", "h2"))
        right.lay.addWidget(label("Un compte peut appartenir à plusieurs concepts. Connecter un compte permet la "
                                  "mise en ligne automatique (YouTube, TikTok, Instagram, Facebook, X) ; sinon la publication est "
                                  "assistée.", "muted", wrap=True))
        self.acc_table = QTableWidget(0, 5)
        self.acc_table.setHorizontalHeaderLabels(["Plateforme", "Nom", "Langue", "Connexion", "Concepts"])
        self.acc_table.verticalHeader().hide()
        self.acc_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.acc_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.acc_table.horizontalHeader().setSectionResizeMode(4, QHeaderView.Stretch)
        self.acc_table.setShowGrid(False)
        right.lay.addWidget(self.acc_table, 1)
        rb = QHBoxLayout()
        rb.addWidget(button("Nouveau compte", "soft", on_click=self._new_account, icon="plus"))
        rb.addWidget(button("Supprimer", "ghost", on_click=self._delete_account))
        rb.addStretch(1)
        rb.addWidget(button("Déconnecter", "ghost", on_click=self._disconnect_account, icon="unplug"))
        self.connect_btn = button("Connecter", "primary", on_click=self._connect_account, icon="plug-zap")
        rb.addWidget(self.connect_btn)
        right.lay.addLayout(rb)
        self.acc_status = label("", "muted", wrap=True)
        right.lay.addWidget(self.acc_status)

        h.addWidget(right, 2)
        self.refresh()
        return w

    def refresh(self) -> None:
        self.concept_list.clear()
        for c in self.app.concepts.list_concepts():
            n = len(self.app.concepts.concept_accounts(c.id))
            it = QListWidgetItem(f"{c.name}   ·  {n} compte(s)")
            from ..widgets import color_dot
            it.setIcon(QIcon(color_dot(c.color, 12).pixmap()))
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
                if j == 0:
                    item.setIcon(theme.platform_icon(a.platform.value, 18))
                if j == 3:
                    from PySide6.QtGui import QColor
                    item.setForeground(QColor(theme.color("ok") if a.connection_status == "connected" else
                                              theme.color("danger") if a.connection_status in ("expired", "revoked",
                                                                                                "error")
                                              else theme.color("muted")))
                self.acc_table.setItem(i, j, item)

    def _platform_card(self, platform: str, title: str, guide: str, fields: list, on_save, configured: bool):
        """Carte compacte d'une plateforme : logo, état, champs, enregistrement ; le mode d'emploi en infobulle."""
        card = Card(padding=16, spacing=8)
        head = QHBoxLayout()
        ic = QLabel()
        ic.setPixmap(theme.platform_icon(platform, 22).pixmap(22, 22))
        head.addWidget(ic)
        head.addWidget(label(title, "h2"))
        head.addWidget(hint_icon(guide))
        head.addStretch(1)
        st = label("Configuré" if configured else "À configurer", "badge")
        if not configured:
            st.setStyleSheet(f"color:{theme.color('muted')}; background:{theme.color('hover')};")
        head.addWidget(st)
        card.lay.addLayout(head)
        form = QFormLayout()
        form.setHorizontalSpacing(12)
        form.setVerticalSpacing(8)
        form.setFieldGrowthPolicy(QFormLayout.AllNonFixedFieldsGrow)
        for name, w in fields:
            w.setMaximumWidth(340)
            form.addRow(label(name, "muted"), w)
        card.lay.addLayout(form)
        row = QHBoxLayout()
        row.addStretch(1)
        row.addWidget(button("Enregistrer", "soft", on_click=on_save, icon="save"))
        card.lay.addLayout(row)
        return card

    def _publishing_tab(self) -> QWidget:
        from PySide6.QtWidgets import QGridLayout, QScrollArea
        body = QWidget()
        outer = QVBoxLayout(body)
        outer.setContentsMargins(0, 12, 0, 12)
        outer.setSpacing(14)
        self.yt_id = QLineEdit(self.app.settings.get("youtube_client_id", "") or "")
        self.yt_id.setPlaceholderText("xxxx.apps.googleusercontent.com")
        self.yt_secret = QLineEdit()
        self.yt_secret.setEchoMode(QLineEdit.Password)
        self.yt_secret.setEnabled(secrets.available())
        self.yt_secret.setPlaceholderText("Enregistré" if secrets.get_secret("youtube_client_secret") else "Code secret")
        self.yt_privacy = QComboBox()
        for k, n in (("public", "Publique"), ("unlisted", "Non répertoriée"), ("private", "Privée")):
            self.yt_privacy.addItem(n, k)
        self.yt_privacy.setCurrentIndex(max(0, self.yt_privacy.findData(self.app.settings.get("youtube_privacy",
                                                                                               "public"))))
        self.yt_auto = QCheckBox("Mettre en ligne automatiquement à l'heure prévue (comptes connectés)")
        self.yt_auto.setChecked(bool(self.app.settings.get("auto_upload", True)))
        self.yt_auto.toggled.connect(lambda v: self.app.settings.set("auto_upload", bool(v)))
        top = QHBoxLayout()
        top.addWidget(self.yt_auto)
        top.addStretch(1)
        top.addWidget(hint_icon("Chaque plateforme exige votre propre application développeur (gratuite). Une fois "
                                "ses réglages enregistrés, connectez le compte dans Concepts et projets."))
        outer.addLayout(top)

        self.tk_key = QLineEdit(self.app.settings.get("tiktok_client_key", "") or "")
        self.tk_secret = QLineEdit()
        self.tk_secret.setEchoMode(QLineEdit.Password)
        self.tk_secret.setEnabled(secrets.available())
        self.tk_secret.setPlaceholderText("Enregistré" if secrets.get_secret("tiktok_client_secret") else "Code secret")
        self.tk_privacy = QComboBox()
        for k, n in (("PUBLIC_TO_EVERYONE", "Tout le monde"), ("MUTUAL_FOLLOW_FRIENDS", "Amis"),
                     ("FOLLOWER_OF_CREATOR", "Abonnés"), ("SELF_ONLY", "Moi uniquement")):
            self.tk_privacy.addItem(n, k)
        self.tk_privacy.setCurrentIndex(max(0, self.tk_privacy.findData(
            self.app.settings.get("tiktok_privacy", "PUBLIC_TO_EVERYONE"))))
        self.meta_id = QLineEdit(self.app.settings.get("meta_app_id", "") or "")
        self.meta_secret = QLineEdit()
        self.meta_secret.setEchoMode(QLineEdit.Password)
        self.meta_secret.setEnabled(secrets.available())
        self.meta_secret.setPlaceholderText("Enregistrée" if secrets.get_secret("meta_app_secret") else "Clé secrète")
        self.x_id = QLineEdit(self.app.settings.get("x_client_id", "") or "")
        self.x_secret = QLineEdit()
        self.x_secret.setEchoMode(QLineEdit.Password)
        self.x_secret.setEnabled(secrets.available())
        self.x_secret.setPlaceholderText("Enregistré" if secrets.get_secret("x_client_secret") else
                                         "Facultatif (Native App)")
        cards = [
            self._platform_card("youtube", "YouTube", (
                "console.cloud.google.com → nouveau projet → activer « YouTube Data API v3 » → écran de consentement "
                "(Externe, votre adresse en utilisateur test) → Identifiants → « ID client OAuth », type « Application "
                "de bureau ». Guide : docs/YOUTUBE.md."),
                [("ID client", self.yt_id), ("Code secret", self.yt_secret), ("Visibilité", self.yt_privacy)],
                self._save_youtube, bool(self.yt_id.text() and secrets.get_secret("youtube_client_secret"))),
            self._platform_card("tiktok", "TikTok", (
                "developers.tiktok.com → application (ordinateur), « Login Kit » avec l'adresse de retour "
                "http://127.0.0.1:*/callback/ et « Content Posting API » (Direct Post). Tant que TikTok n'a pas audité "
                "l'application, les vidéos sont publiées en privé. Guide : docs/TIKTOK.md."),
                [("Clé client", self.tk_key), ("Code secret", self.tk_secret), ("Visibilité", self.tk_privacy)],
                self._save_tiktok, bool(self.tk_key.text() and secrets.get_secret("tiktok_client_secret"))),
            self._platform_card("instagram", "Instagram et Facebook", (
                "developers.facebook.com → application (Entreprise), « Facebook Login for Business » avec la "
                "redirection http://localhost:8767/callback/ et le produit Instagram. Le compte Instagram doit être "
                "professionnel et relié à une Page Facebook. Guide : docs/META.md."),
                [("Identifiant", self.meta_id), ("Clé secrète", self.meta_secret)],
                self._save_meta, bool(self.meta_id.text() and secrets.get_secret("meta_app_secret"))),
            self._platform_card("x", "X", (
                "developer.x.com → projet et application, OAuth 2.0 (Web App ou Native App), lecture et écriture, "
                "rappel http://127.0.0.1:8768/callback. L'écriture dépend de votre offre X. Guide : docs/X.md."),
                [("Client ID", self.x_id), ("Code secret", self.x_secret)],
                self._save_x, bool(self.x_id.text())),
        ]
        grid = QGridLayout()
        grid.setSpacing(14)
        for i, c in enumerate(cards):
            grid.addWidget(c, i // 2, i % 2)
        outer.addLayout(grid)
        outer.addStretch(1)
        box = QWidget()
        bl = QHBoxLayout(box)
        bl.setContentsMargins(0, 0, 0, 0)
        body.setMaximumWidth(980)
        bl.addWidget(body, 1)
        bl.addStretch(0)
        sc = QScrollArea()
        sc.setWidgetResizable(True)
        sc.setFrameShape(QScrollArea.NoFrame)
        sc.setWidget(box)
        return sc

    def _saved(self, what: str) -> None:
        msg = f"Réglages {what} enregistrés."
        self.acc_status.setText(msg) if hasattr(self, "acc_status") else None
        win = self.window()
        if hasattr(win, "notify"):
            win.notify(msg, 3000)

    def _save_youtube(self) -> None:
        self.app.settings.set("youtube_client_id", self.yt_id.text().strip())
        self.app.settings.set("youtube_privacy", self.yt_privacy.currentData())
        self.app.settings.set("auto_upload", self.yt_auto.isChecked())
        if self.yt_secret.text().strip():
            try:
                secrets.set_secret("youtube_client_secret", self.yt_secret.text().strip())
            except secrets.SecretStoreUnavailable as exc:
                QMessageBox.warning(self, "YouTube", str(exc))
                return
            self.yt_secret.clear()
            self.yt_secret.setPlaceholderText("Enregistré")
        self._saved("YouTube")

    def _save_x(self) -> None:
        self.app.settings.set("x_client_id", self.x_id.text().strip())
        if self.x_secret.text().strip():
            try:
                secrets.set_secret("x_client_secret", self.x_secret.text().strip())
            except secrets.SecretStoreUnavailable as exc:
                QMessageBox.warning(self, "X", str(exc))
                return
            self.x_secret.clear()
            self.x_secret.setPlaceholderText("Enregistré")
        self._saved("X")

    def _save_meta(self) -> None:
        self.app.settings.set("meta_app_id", self.meta_id.text().strip())
        if self.meta_secret.text().strip():
            try:
                secrets.set_secret("meta_app_secret", self.meta_secret.text().strip())
            except secrets.SecretStoreUnavailable as exc:
                QMessageBox.warning(self, "Meta", str(exc))
                return
            self.meta_secret.clear()
            self.meta_secret.setPlaceholderText("Enregistrée")
        self._saved("Meta")

    def _save_tiktok(self) -> None:
        self.app.settings.set("tiktok_client_key", self.tk_key.text().strip())
        self.app.settings.set("tiktok_privacy", self.tk_privacy.currentData())
        if self.tk_secret.text().strip():
            try:
                secrets.set_secret("tiktok_client_secret", self.tk_secret.text().strip())
            except secrets.SecretStoreUnavailable as exc:
                QMessageBox.warning(self, "TikTok", str(exc))
                return
            self.tk_secret.clear()
            self.tk_secret.setPlaceholderText("Enregistré")
        self._saved("TikTok")

    def _selected_account(self):
        row = self.acc_table.currentRow()
        if row < 0:
            QMessageBox.information(self, "Comptes", "Sélectionnez un compte dans la liste.")
            return None
        return self.app.concepts.get_account(self.acc_table.item(row, 0).data(Qt.UserRole))

    def _connect_account(self) -> None:
        acc = self._selected_account()
        if acc is None:
            return
        if acc.platform.value == "tiktok":
            if not (self.app.settings.get("tiktok_client_key", "") and secrets.get_secret("tiktok_client_secret")):
                QMessageBox.information(self, "Connexion", "Renseignez d'abord la clé et le code secret de votre "
                                        "application TikTok, puis « Enregistrer les réglages TikTok ».")
                return
            self._connect_job = self.app.jobs.submit("connect_account", f"Connexion · {acc.display}",
                                                     {"account_id": acc.id})
            self.acc_status.setText("Autorisez l'accès dans la page TikTok ouverte dans votre navigateur…")
            return
        if acc.platform.value == "x":
            if not self.app.settings.get("x_client_id", ""):
                QMessageBox.information(self, "Connexion", "Renseignez d'abord le Client ID de votre application X "
                                        "(Paramètres → Publication).")
                return
            self._connect_job = self.app.jobs.submit("connect_account", f"Connexion · {acc.display}",
                                                     {"account_id": acc.id})
            self.acc_status.setText("Autorisez l'accès dans la page X ouverte dans votre navigateur…")
            return
        if acc.platform.value in ("instagram", "facebook"):
            if not (self.app.settings.get("meta_app_id", "") and secrets.get_secret("meta_app_secret")):
                QMessageBox.information(self, "Connexion", "Renseignez d'abord l'identifiant et la clé secrète de "
                                        "votre application Meta (Paramètres → Publication).")
                return
            self._connect_job = self.app.jobs.submit("connect_account", f"Connexion · {acc.display}",
                                                     {"account_id": acc.id})
            self.acc_status.setText("Autorisez l'accès dans la page Facebook ouverte dans votre navigateur…")
            return
        if acc.platform.value != "youtube":
            QMessageBox.information(self, "Connexion", f"La connexion {acc.platform.label} n'est pas encore "
                                    "disponible : ses publications restent assistées.")
            return
        if not (self.app.settings.get("youtube_client_id", "") and secrets.get_secret("youtube_client_secret")):
            QMessageBox.information(self, "Connexion", "Renseignez d'abord l'ID client et le code secret de votre "
                                    "projet Google, puis « Enregistrer les réglages YouTube ».")
            return
        self._connect_job = self.app.jobs.submit("connect_account", f"Connexion · {acc.display}",
                                                 {"account_id": acc.id})
        self.acc_status.setText("Autorisez l'accès dans la page Google ouverte dans votre navigateur…")

    def _disconnect_account(self) -> None:
        acc = self._selected_account()
        if acc is None:
            return
        for name in (f"youtube_refresh_{acc.id}", f"tiktok_refresh_{acc.id}", f"meta_token_{acc.id}",
                     f"x_refresh_{acc.id}"):
            try:
                secrets.delete_secret(name)
            except Exception:
                pass
        with self.app.db.transaction() as c:
            c.execute("UPDATE social_account SET connection_status = 'disconnected', remote_id = '' WHERE id = ?",
                      (acc.id,))
        self.acc_status.setText(f"{acc.display} déconnecté.")
        self.refresh()

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
        lay.addWidget(self._voices_card())
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
        self.models_table.verticalHeader().setDefaultSectionSize(40)
        card.lay.addWidget(self.models_table)
        self._model_jobs: dict[int, str] = {}
        self._rec_model = rec
        self._fill_models()
        return card

    # -- voix hors ligne (Piper) ----------------------------------------------------------
    def _voices_card(self) -> Card:
        from ..dialogs import LANGUAGES
        card = Card()
        card.lay.addWidget(label("Voix hors ligne (gratuites, sur ce PC)", "h2"))
        card.lay.addWidget(label("Pour le doublage et la voix off sans service payant : le moteur Piper (licence "
                                 "MIT, 22 Mo) puis une ou plusieurs voix (20 à 120 Mo chacune, licence indiquée "
                                 "dans la fiche de chaque voix). Rien n'est envoyé sur Internet pendant la lecture.",
                                 "muted", wrap=True))
        row = QHBoxLayout()
        self.piper_state = label("", "muted")
        row.addWidget(self.piper_state, 1)
        self.piper_btn = button("Installer le moteur", "soft", on_click=lambda: self._piper_job("bin"), icon="download")
        row.addWidget(self.piper_btn)
        card.lay.addLayout(row)
        row2 = QHBoxLayout()
        self.piper_lang = QComboBox()
        for c, n in LANGUAGES:
            self.piper_lang.addItem(n, c)
        self.piper_lang.currentIndexChanged.connect(lambda _i: self._fill_voices())
        row2.addWidget(label("Langue", "muted"))
        row2.addWidget(self.piper_lang)
        row2.addWidget(button("Actualiser le catalogue", "ghost", on_click=lambda: self._piper_job("catalog")))
        row2.addStretch(1)
        card.lay.addLayout(row2)
        self.voices_table = QTableWidget(0, 4)
        self.voices_table.setHorizontalHeaderLabels(["Voix", "Qualité", "Taille", ""])
        self.voices_table.verticalHeader().hide()
        self.voices_table.setShowGrid(False)
        self.voices_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.voices_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        self.voices_table.verticalHeader().setDefaultSectionSize(40)
        self.voices_table.setMinimumHeight(200)
        self.voices_table.setMaximumHeight(320)
        card.lay.addWidget(self.voices_table)
        self._fill_voices()
        return card

    def _fill_voices(self) -> None:
        from ...ai import piper
        models = self.app.paths.models
        exe = piper.exe_path(models)
        inst = piper.installed_voices(models)
        running = set(self._model_jobs.values()) if hasattr(self, "_model_jobs") else set()
        self.piper_state.setText(("✓ Moteur installé" if exe else "Moteur non installé") +
                                 (f" · {len(inst)} voix : {', '.join(inst[:4])}{'…' if len(inst) > 4 else ''}"
                                  if inst else ""))
        self.piper_btn.setVisible(exe is None)
        self.piper_btn.setEnabled("piper:bin" not in running)
        t = self.voices_table
        try:
            cat = [v for v in piper.catalog(models) if v.family == self.piper_lang.currentData()] \
                if (piper.root(models) / "voices.json").exists() else None
        except Exception:
            cat = None
        if cat is None:
            t.setRowCount(1)
            t.setItem(0, 0, QTableWidgetItem("Catalogue non chargé : « Actualiser le catalogue »."))
            for j in (1, 2, 3):
                t.setItem(0, j, QTableWidgetItem(""))
            t.removeCellWidget(0, 3)
            return
        t.setRowCount(len(cat))
        for i, v in enumerate(cat):
            t.setItem(i, 0, QTableWidgetItem(f"{v.name} ({v.language})" + (f" · {v.speakers} voix" if v.speakers > 1
                                                                               else "")))
            t.setItem(i, 1, QTableWidgetItem({"x_low": "très basse", "low": "basse", "medium": "moyenne",
                                              "high": "haute"}.get(v.quality, v.quality)))
            t.setItem(i, 2, QTableWidgetItem(f"{v.size_mb:.0f} Mo"))
            key = "piper:" + v.key
            if key in running:
                b = button("Téléchargement…", "ghost")
                b.setEnabled(False)
            elif v.key in inst:
                b = button("Supprimer", "ghost", on_click=lambda _=False, k=v.key: self._delete_voice(k))
            else:
                b = button("Télécharger", "soft", on_click=lambda _=False, k=v.key: self._piper_job(k),
                           icon="download")
            t.setCellWidget(i, 3, _cell(b))
        if not cat:
            t.setRowCount(1)
            t.setItem(0, 0, QTableWidgetItem("Aucune voix Piper pour cette langue."))

    def _piper_job(self, what: str) -> None:
        jid = self.app.jobs.submit("download_model", {"bin": "Installation du moteur de voix",
                                                      "catalog": "Catalogue des voix"}.get(what, f"Voix {what}"),
                                   {"model": "piper:" + what})
        self._model_jobs[jid] = "piper:" + what
        self._fill_voices()

    def _delete_voice(self, key: str) -> None:
        from ...ai import piper
        if QMessageBox.question(self, "Voix", f"Supprimer la voix « {key} » ?") == QMessageBox.Yes:
            piper.delete_voice(self.app.paths.models, key)
            self._fill_voices()

    def _llm_card(self) -> Card:
        from PySide6.QtWidgets import QLineEdit
        card = Card()
        card.lay.addWidget(label("Modèle de langage (facultatif)", "h2"))
        card.lay.addWidget(label(
            "Utilisé par « Extraits » pour mieux comprendre le sens des échanges. Sans lui, l'analyse locale "
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
        self.llm_model = _ModelCombo(self._llm_load_models)   # liste des modèles du service, rien à taper
        if cfg.get("model"):
            self.llm_model.addItem(cfg["model"])
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
        row.addWidget(button("Actualiser la liste des modèles", on_click=self._llm_test))
        row.addWidget(button("Enregistrer", "primary", on_click=self._llm_save))
        row.addStretch(1)
        card.lay.addLayout(row)
        self.llm_status = label("", "faint", wrap=True)
        card.lay.addWidget(self.llm_status)
        card.lay.addWidget(label("Traduction (facultatif)", "h2"))
        card.lay.addWidget(label("Clés personnelles pour DeepL ou Google Cloud Translation. Le modèle de langage "
                                 "ci-dessus peut aussi traduire (hors ligne avec Ollama). Tous les moteurs, gratuits "
                                 "compris, se règlent aussi depuis l'icône réglages de l'onglet Traduction.", "muted",
                                 wrap=True))
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
        self.llm_kind.currentIndexChanged.connect(lambda _i: self._llm_load_models())
        self.llm_url.editingFinished.connect(self._llm_load_models)
        self.llm_key.editingFinished.connect(self._llm_load_models)
        self._llm_kind_changed()
        return card

    def _save_translation_keys(self) -> None:
        el = self.eleven_key.text().strip()
        if el and not el.startswith("sk_"):
            QMessageBox.warning(self, "Clé ElevenLabs", "Ceci ressemble à l'identifiant de la clé, pas à la clé : une "
                                "clé ElevenLabs commence par « sk_ » et n'est affichée qu'au moment de sa création "
                                "(ElevenLabs → Developers → API Keys → Create). Elle n'a pas été enregistrée.")
            return
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

    def _llm_load_models(self, quiet: bool = False) -> bool:
        """Remplit la liste des modèles en interrogeant le service (Ollama ou API compatible OpenAI)."""
        from PySide6.QtWidgets import QApplication

        from ...ai.highlights import LLMError, chat_models, check_llm
        k = self.llm_kind.currentData()
        if k == "none":
            return False
        if not quiet:
            self.llm_status.setText("Recherche des modèles disponibles…")
            QApplication.processEvents()
        key = self.llm_key.text().strip() or (secrets.get_secret("llm_api_key") or "")
        try:
            models = chat_models(check_llm(k, self.llm_url.text().strip(), key if k == "openai" else ""),
                                 self.llm_url.text().strip(), k)
        except LLMError as exc:
            self.llm_status.setText(f"⚠ {exc}")
            return False
        cur = self.llm_model.currentText()
        self.llm_model.clear()
        self.llm_model.addItems(models)
        if cur in models:
            self.llm_model.setCurrentText(cur)
        self.llm_model.loaded = True
        self.llm_status.setText(f"{len(models)} modèle(s) disponible(s) : choisissez-en un, puis Enregistrer."
                                if models else "⚠ Aucun modèle disponible chez ce service.")
        return True

    def _llm_test(self) -> None:
        self._llm_load_models()

    def _llm_save(self) -> None:
        k = self.llm_kind.currentData()
        cfg = {"kind": k, "base_url": self.llm_url.text().strip(), "model": self.llm_model.currentText().strip()}
        if k != "none" and not cfg["model"]:
            self.llm_status.setText("⚠ Choisissez un modèle dans la liste.")
            return
        if k == "openai" and self.llm_key.text().strip():
            try:
                secrets.set_secret("llm_api_key", self.llm_key.text().strip())
            except secrets.SecretStoreUnavailable as exc:
                self.llm_status.setText(f"⚠ {exc}")
                return
            self.llm_key.clear()
        note = "Enregistré."
        if k != "none":
            # nom de modèle vérifié auprès du service : une faute de frappe ferait échouer chaque traduction
            from ...ai.highlights import LLMError, check_llm
            try:
                models = check_llm(k, cfg["base_url"], (secrets.get_secret("llm_api_key") or "") if k == "openai"
                                   else "")
            except LLMError as exc:
                models, note = None, f"Enregistré, mais le service n'a pas pu être vérifié : {exc}"
            if models is not None and cfg["model"] not in models:
                self._llm_load_models(quiet=True)
                self.llm_status.setText(f"⚠ Le modèle « {cfg['model']} » n'existe pas chez ce service : choisissez-en "
                                        "un dans la liste.")
                return
        self.app.settings.set("llm", cfg)
        self.llm_status.setText(note)
        self.llm_changed.emit()

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
        if QMessageBox.question(self, "Modèle", f"Supprimer le modèle « {key} » ? Il pourra être retéléchargé."
                                ) == QMessageBox.Yes:
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
            if job.status == "succeeded":
                self.acc_status.setText(f"✓ Connecté à la chaîne « {job.result.get('channel')} ».")
            else:
                self.acc_status.setText(f"⚠ Connexion impossible : {(job.error or 'annulée')[:300]}")
            self.refresh()
        if job.id in getattr(self, "_model_jobs", {}):
            if job.status in ("succeeded", "failed", "cancelled"):
                key = self._model_jobs.pop(job.id, None) or ""
                if job.status == "failed":
                    QMessageBox.warning(self, "Téléchargement", job.error[:600])
                if key.startswith("piper:"):
                    self._fill_voices()
                    return
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
