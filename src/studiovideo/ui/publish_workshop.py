"""Atelier de publication : publier sans API de plateforme, le clic final « Publier » restant à l'utilisateur.

Niveau 2 (assistance) : le texte est copié d'office, la page d'envoi s'ouvre dans le navigateur dédié du compte
(déjà connecté), la vidéo se glisse directement depuis l'Atelier vers la page. Niveau 3 : une fois la vidéo en ligne,
son lien est retrouvé tout seul sur la page publique du compte (tâche « match_published », toutes les heures) ;
un doute est proposé ici (« Est-ce cette vidéo ? ») ; en dernier recours, on colle le lien.
"""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QMimeData, QSize, Qt, QUrl
from PySide6.QtGui import QDesktopServices, QDrag, QPixmap
from PySide6.QtWidgets import (QApplication, QButtonGroup, QDialog, QFrame, QHBoxLayout, QLabel, QLineEdit,
                               QPlainTextEdit, QPushButton, QVBoxLayout, QWidget)

from ..core import AppContext
from ..publish import reconcile, rules
from ..services.publications import PublicationError
from . import browser_login, theme
from .publication_dialog import video_info
from .widgets import button, label


class VideoTile(QFrame):
    """La vidéo à envoyer : à glisser telle quelle dans la page d'envoi du navigateur."""

    def __init__(self, path: str, thumb: str = "", parent=None):
        super().__init__(parent)
        self.path = path
        self.setProperty("role", "card")
        self.setCursor(Qt.OpenHandCursor)
        self.setToolTip("Glisse la vidéo dans la page d'envoi du navigateur")
        lay = QHBoxLayout(self)
        lay.setContentsMargins(8, 8, 12, 8)
        self.pic = QLabel()
        self.pic.setFixedSize(54, 72)
        self.pic.setAlignment(Qt.AlignCenter)
        pix = QPixmap(thumb) if thumb and Path(thumb).exists() else QPixmap()
        if pix.isNull():
            pix = theme.icon("film", theme.color("muted"), 32).pixmap(QSize(32, 32))
        else:
            pix = pix.scaled(54, 72, Qt.KeepAspectRatio, Qt.SmoothTransformation)
        self.pic.setPixmap(pix)
        lay.addWidget(self.pic)
        col = QVBoxLayout()
        col.setSpacing(2)
        self.name = label(Path(path).name if path else "Aucune vidéo", None, wrap=True, hint=False)
        col.addWidget(self.name)
        col.addWidget(label("À glisser dans la page d'envoi", "faint", hint=False))
        lay.addLayout(col, 1)
        self.setEnabled(bool(path) and Path(path).exists())

    def mime(self) -> QMimeData:
        m = QMimeData()
        m.setUrls([QUrl.fromLocalFile(self.path)])
        return m

    def mousePressEvent(self, e) -> None:
        if e.button() == Qt.LeftButton and self.isEnabled():
            drag = QDrag(self)
            drag.setMimeData(self.mime())
            drag.setPixmap(self.pic.pixmap())
            drag.exec(Qt.CopyAction)
            return
        super().mousePressEvent(e)


class PublishWorkshop(QDialog):
    """Une ou plusieurs publications (une par réseau) à faire à la main, l'une après l'autre."""

    def __init__(self, app: AppContext, publication_ids, parent=None, open_url=None):
        super().__init__(parent)
        self.app = app
        self.ids = [publication_ids] if isinstance(publication_ids, int) else list(publication_ids)
        self.open_url = open_url or (lambda u: QDesktopServices.openUrl(QUrl(u)))
        self.login = None
        self.setWindowTitle("Atelier de publication")
        self.setMinimumWidth(640)
        lay = QVBoxLayout(self)
        lay.setSpacing(10)
        self.tabs = QHBoxLayout()
        self.tabs.setSpacing(6)
        self.group = QButtonGroup(self)
        self.group.setExclusive(True)
        for i, pid in enumerate(self.ids):
            r = app.publications.get(pid)
            b = QPushButton(r["account"] if r else "?")
            b.setCheckable(True)
            b.setProperty("kind", "ghost")
            if r:
                b.setIcon(theme.platform_icon(r["platform"], 16))
            self.group.addButton(b, i)
            self.tabs.addWidget(b)
        self.tabs.addStretch(1)
        tabs_w = QWidget()
        tabs_w.setLayout(self.tabs)
        tabs_w.setVisible(len(self.ids) > 1)
        lay.addWidget(tabs_w)
        self.body = QVBoxLayout()
        self.body.setSpacing(8)
        lay.addLayout(self.body)
        foot = QHBoxLayout()
        foot.addStretch(1)
        foot.addWidget(button("Fermer", "ghost", on_click=self.accept))
        lay.addLayout(foot)
        self.group.idClicked.connect(self.show_pub)
        self.pid = None
        if self.ids:
            self.group.button(0).setChecked(True)
            self.show_pub(0)

    # -- affichage d'une publication ---------------------------------------------------
    def _clear(self) -> None:
        def wipe(layout) -> None:
            while layout.count():
                it = layout.takeAt(0)
                if it.widget():
                    it.widget().deleteLater()
                elif it.layout():
                    wipe(it.layout())
        wipe(self.body)

    def show_pub(self, index: int) -> None:
        self._clear()
        self.pid = self.ids[index]
        r = self.row = self.app.publications.get(self.pid)
        if r is None:
            self.body.addWidget(label("Publication introuvable.", "error"))
            return
        pl = rules.RULES[r["platform"]]
        self.composed = rules.compose(r["platform"], r["title"] or "", r["description"] or "", r["hashtags"] or "")
        head = QHBoxLayout()
        ic = QLabel()
        ic.setPixmap(theme.platform_icon(r["platform"], 22).pixmap(QSize(22, 22)))
        head.addWidget(ic)
        head.addWidget(label(f"{pl.label} — {r['account']}", "h2"))
        head.addStretch(1)
        self.state = label("", "muted", hint=False)
        head.addWidget(self.state)
        self.body.addLayout(head)
        for w in rules.check(r["platform"], r["title"] or "", r["description"] or "", r["hashtags"] or "",
                             video_info(r)):
            self.body.addWidget(label("⚠ " + w, "muted", wrap=True, hint=False))
        # 1. le texte (copié d'office)
        self.body.addWidget(label("1. Le texte", "h2"))
        self.text = QPlainTextEdit(self.composed["text"])
        self.text.setReadOnly(True)
        self.text.setMaximumHeight(110)
        if self.composed["title"]:
            self.title = QLineEdit(self.composed["title"])
            self.title.setReadOnly(True)
            self.body.addWidget(self.title)
        self.body.addWidget(self.text)
        row = QHBoxLayout()
        if self.composed["title"]:
            row.addWidget(button("Recopier le titre", "ghost", icon="copy",
                                 on_click=lambda: self._copy(self.composed["title"], "Titre copié.")))
            row.addWidget(button("Copier la description", "ghost", icon="copy",
                                 on_click=lambda: self._copy(self.composed["text"], "Description copiée.")))
        else:
            row.addWidget(button("Recopier le texte", "ghost", icon="copy",
                                 on_click=lambda: self._copy(self.composed["text"], "Texte copié.")))
        self.copied = label("", "faint", hint=False)
        row.addWidget(self.copied)
        row.addStretch(1)
        self.body.addLayout(row)
        # 2. la vidéo
        self.body.addWidget(label("2. La vidéo", "h2"))
        vr = QHBoxLayout()
        path = r["output_path"] or ""
        self.tile = VideoTile(path, r["thumbnail"] or "")
        vr.addWidget(self.tile, 1)
        show = button("Afficher dans l'Explorateur", "ghost", icon="folder-open", on_click=lambda: self._reveal(path))
        show.setEnabled(bool(path) and Path(path).exists())
        vr.addWidget(show, 0, Qt.AlignVCenter)
        self.body.addLayout(vr)
        # 3. la page d'envoi, dans le navigateur dédié du compte
        self.body.addWidget(label("3. La page d'envoi", "h2"))
        br = QHBoxLayout()
        self.open_btn = button(f"Ouvrir la page d'envoi {pl.label}", "primary", icon="upload", on_click=self._open)
        br.addWidget(self.open_btn)
        self.login_btn = button("Se connecter à ce compte", "ghost", icon="log-in", on_click=self._login)
        br.addWidget(self.login_btn)
        br.addStretch(1)
        self.body.addLayout(br)
        self.page_note = label("", "muted", wrap=True, hint=False)
        self.body.addWidget(self.page_note)
        # 4. le lien de la vidéo publiée
        self.body.addWidget(label("4. Le lien de la vidéo publiée", "h2"))
        self.auto_note = label("", "muted", wrap=True, hint=False)
        self.body.addWidget(self.auto_note)
        self.props = QVBoxLayout()
        self.props.setSpacing(4)
        self.body.addLayout(self.props)
        lr = QHBoxLayout()
        self.url = QLineEdit(r["public_url"] or "")
        self.url.setPlaceholderText({"youtube": "https://youtu.be/…", "tiktok": "https://www.tiktok.com/@…/video/…"}
                                    .get(r["platform"], "https://…"))
        lr.addWidget(self.url, 1)
        self.confirm_btn = button("Confirmer", "soft", on_click=self._confirm)
        lr.addWidget(self.confirm_btn)
        self.body.addLayout(lr)
        self.msg = label("", "muted", wrap=True, hint=False)
        self.body.addWidget(self.msg)
        self._copy(self.composed["title"] or self.composed["text"],
                   "Titre copié dans le presse-papiers." if self.composed["title"] else
                   "Texte copié dans le presse-papiers.")
        browser_login.check_session(self.app, r["account_id"])
        self.refresh()

    def refresh(self) -> None:
        r = self.row = self.app.publications.get(self.pid)
        done = r["status"] in ("published", "cancelled")
        self.state.setText({"published": "✓ Publiée", "cancelled": "Annulée"}.get(r["status"], "À publier"))
        st = browser_login.session_state(self.app, r["account_id"])
        self.login_btn.setVisible(not st)
        name = browser_login.browser.browser_name(browser_login.browser.find_browser())
        self.page_note.setText(
            f"Navigateur dédié à ce compte ({name}), déjà connecté. Glisse la vidéo dans la page, colle le texte, "
            "vérifie, puis clique toi-même sur « Publier »." if st else
            "Ce compte n'est pas encore connecté dans son navigateur dédié : « Se connecter à ce compte », puis "
            "ferme la fenêtre une fois connecté.")
        acc = self.app.concepts.get_account(r["account_id"])
        if done:
            self.auto_note.setText(f"✓ En ligne : {r['public_url']}" if r["public_url"] else "")
        elif r["platform"] in reconcile.SUPPORTED and reconcile.listing_urls(r["platform"], acc.public_url):
            last = (self.app.concepts.account_settings(acc.id).get("reconcile") or {})
            when = ""
            if last.get("at"):
                from .widgets import local_time
                when = f" Dernière recherche : {local_time(last['at'])}" + (
                    f" ({last['error']})" if last.get("error") else ".")
            self.auto_note.setText("Une fois la vidéo publiée, son lien est retrouvé tout seul sur la page publique "
                                   "du compte (vérifié toutes les heures)." + when)
        elif r["platform"] in reconcile.SUPPORTED:
            self.auto_note.setText("Renseigne l'adresse publique du compte (Concepts et projets) pour que le lien "
                                   "soit retrouvé tout seul. En attendant, colle-le ici.")
        else:
            self.auto_note.setText(f"{rules.RULES[r['platform']].label} ne montre pas ses vidéos sans connexion : "
                                   "colle ici le lien de la vidéo une fois publiée.")
        while self.props.count():
            it = self.props.takeAt(0)
            if it.widget():
                it.widget().deleteLater()
        for m in ([] if done else self.app.publications.matches(self.pid)):
            self.props.addWidget(self._proposal(m))
        for w in (self.url, self.confirm_btn, self.open_btn):
            w.setEnabled(not done)

    def _proposal(self, m) -> QWidget:
        w = QFrame()
        w.setProperty("role", "card")
        h = QHBoxLayout(w)
        h.setContentsMargins(10, 6, 8, 6)
        h.addWidget(label("Est-ce cette vidéo ?", None, hint=False))
        t = label(m["title"] or m["url"], "muted", hint=False)
        t.setToolTip(m["url"])
        h.addWidget(t, 1)
        h.addWidget(button("Voir", "ghost", on_click=lambda: self.open_url(m["url"])))
        h.addWidget(button("Oui", "soft", on_click=lambda: self._accept(m["id"])))
        h.addWidget(button("Non", "ghost", on_click=lambda: self._reject(m["id"])))
        return w

    # -- actions --------------------------------------------------------------------------
    def _copy(self, text: str, msg: str) -> None:
        QApplication.clipboard().setText(text)
        self.copied.setText("✓ " + msg)

    def _reveal(self, path: str) -> None:
        from .pages.library import reveal
        reveal(path)

    def _open(self) -> None:
        r = self.row
        if r["status"] not in ("published", "cancelled", "manual_action"):
            self.app.publications.mark_manual(self.pid, "Envoyée depuis l'Atelier de publication : lien à confirmer.")
        note = browser_login.open_page(self.app, r["account_id"], rules.RULES[r["platform"]].upload_url,
                                       self.open_url)
        self.msg.setText(note or "Page d'envoi ouverte dans le navigateur du compte.")
        self.refresh()

    def _login(self) -> None:
        self.login = browser_login.BrowserLogin(self.app, self.row["account_id"], self)
        err = self.login.start()
        if err:
            self.msg.setText(err)
            return
        self.login_btn.setEnabled(False)
        self.msg.setText("Connecte-toi dans la fenêtre ouverte, puis ferme-la.")
        self.login.finished.connect(self._logged)

    def _logged(self, found) -> None:
        self.login_btn.setEnabled(True)
        self.msg.setText("✓ Compte connecté dans son navigateur." if found else
                         "Aucune session trouvée : la connexion n'a pas abouti." if found is False else
                         "Impossible de vérifier la connexion (navigateur encore ouvert ?).")
        self.refresh()

    def _accept(self, match_id: int) -> None:
        try:
            self.app.publications.accept_match(match_id)
        except PublicationError as exc:
            self.msg.setText(f"⚠ {exc}")
        self.refresh()

    def _reject(self, match_id: int) -> None:
        self.app.publications.reject_match(match_id)
        self.refresh()

    def _confirm(self) -> None:
        try:
            self.app.publications.confirm_published(self.pid, self.url.text())
        except PublicationError as exc:
            self.msg.setText(f"⚠ {exc}")
            return
        self.msg.setText("✓ Publication confirmée.")
        self.refresh()
