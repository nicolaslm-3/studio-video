"""Atelier de publication : publier sans API de plateforme, le clic final « Publier » restant à l'utilisateur.

Niveau 2 (assistance) : la page d'envoi s'ouvre dans le navigateur dédié du compte (déjà connecté), la vidéo se
glisse directement depuis l'Atelier vers la page ; le texte du réseau est copié en l'ouvrant. Niveau 3 : une fois
la vidéo en ligne, son lien est retrouvé tout seul sur la page publique du compte (tâche « match_published », toutes
les heures) ; un doute est proposé ici (« Est-ce cette vidéo ? ») ; en dernier recours, on colle le lien.

Les mêmes morceaux (vidéo à glisser, page d'envoi, lien) servent dans l'onglet Export (une carte par réseau) et
dans la fenêtre ouverte depuis le Calendrier.
"""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QMimeData, QSize, Qt, QUrl, Signal
from PySide6.QtGui import QDesktopServices, QDrag, QPixmap
from PySide6.QtWidgets import (QApplication, QButtonGroup, QDialog, QFrame, QHBoxLayout, QLabel, QLineEdit,
                               QPushButton, QVBoxLayout, QWidget)

from ..core import AppContext
from ..publish import reconcile, rules
from ..services.publications import PublicationError
from . import browser_login, theme
from .widgets import button, label

# page d'envoi de Meta (Meta Business Suite) : une vidéo pour la Page Facebook et le compte Instagram reliés
META_UPLOAD_URL = "https://business.facebook.com/latest/reels_composer"


def upload_url(platform: str) -> str:
    """Page d'envoi du réseau ; Facebook et Instagram : celle de Meta."""
    return META_UPLOAD_URL if platform in ("meta", "facebook", "instagram") else rules.RULES[platform].upload_url


def video_info(row) -> dict | None:
    """Fichier de la publication (existe-t-il, format, durée) pour vérifier les règles du réseau."""
    if not row or not row["output_path"]:
        return None
    from ..media import ffmpeg
    p = Path(row["output_path"])
    info = {"exists": p.exists(), "aspect": row["aspect"]}
    if p.exists():
        try:
            info["duration"] = ffmpeg.probe(p).duration_s
        except Exception:
            pass
    return info


def reveal(path: str) -> None:
    from .pages.library import reveal as _reveal
    _reveal(path)


class VideoTile(QFrame):
    """La vidéo à envoyer : à glisser telle quelle dans la page d'envoi du navigateur."""

    def __init__(self, path: str, thumb: str = "", parent=None):
        super().__init__(parent)
        self.setProperty("role", "card")
        self.setCursor(Qt.OpenHandCursor)
        self.setToolTip("Glisse la vidéo dans la page d'envoi du navigateur")
        self.setMaximumWidth(300)                     # étroite : juste l'image et le nom
        lay = QHBoxLayout(self)
        lay.setContentsMargins(8, 6, 10, 6)
        self.pic = QLabel()
        self.pic.setFixedSize(40, 54)
        self.pic.setAlignment(Qt.AlignCenter)
        lay.addWidget(self.pic)
        col = QVBoxLayout()
        col.setSpacing(2)
        self.name = label("", None, wrap=True, hint=False)
        col.addWidget(self.name)
        self.hint = label("À glisser dans la page d'envoi", "faint", hint=False)
        col.addWidget(self.hint)
        lay.addLayout(col, 1)
        self.set_video(path, thumb)

    def set_video(self, path: str, thumb: str = "") -> None:
        self.path = path or ""
        pix = QPixmap(thumb) if thumb and Path(thumb).exists() else QPixmap()
        if pix.isNull():
            pix = theme.icon("film", theme.color("muted"), 26).pixmap(QSize(26, 26))
        else:
            pix = pix.scaled(40, 54, Qt.KeepAspectRatio, Qt.SmoothTransformation)
        self.pic.setPixmap(pix)
        ok = bool(path) and Path(path).exists()
        self.name.setText(Path(path).name if ok else "Aucune vidéo exportée")
        self.hint.setText("À glisser dans la page d'envoi" if ok else "Exporte-la d'abord (à gauche)")
        self.setEnabled(ok)

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


class LinkBox(QWidget):
    """Le lien de la vidéo publiée : retrouvé tout seul (page publique du compte), proposé (« Est-ce cette
    vidéo ? »), ou collé à la main."""
    changed = Signal()

    def __init__(self, app: AppContext, open_url, parent=None):
        super().__init__(parent)
        self.app, self.open_url, self.pid = app, open_url, None
        v = QVBoxLayout(self)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(6)
        self.auto_note = label("", "muted", wrap=True, hint=False)
        v.addWidget(self.auto_note)
        self.props = QVBoxLayout()
        self.props.setSpacing(4)
        v.addLayout(self.props)
        lr = QHBoxLayout()
        lr.setSpacing(6)
        self.url = QLineEdit()
        lr.addWidget(self.url, 1)
        self.confirm_btn = button("Confirmer", "soft", on_click=self._confirm)
        lr.addWidget(self.confirm_btn)
        v.addLayout(lr)
        self.msg = label("", "muted", wrap=True, hint=False)
        v.addWidget(self.msg)

    def set_publication(self, pid: int | None) -> None:
        if pid != self.pid:
            self.msg.setText("")
            self.url.clear()
        self.pid = pid
        self.refresh()

    def refresh(self) -> None:
        r = self.app.publications.get(self.pid) if self.pid else None
        self.setVisible(r is not None)
        if r is None:
            return
        done = r["status"] in ("published", "cancelled")
        if not self.url.text() and r["public_url"]:
            self.url.setText(r["public_url"])
        self.url.setPlaceholderText("Lien de la vidéo publiée : " + {
            "youtube": "https://youtu.be/…", "tiktok": "https://www.tiktok.com/@…/video/…"}.get(r["platform"],
                                                                                              "https://…"))
        acc = self.app.concepts.get_account(r["account_id"])
        if done:
            self.auto_note.setText(f"✓ En ligne : {r['public_url']}" if r["public_url"] else "Annulée.")
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
            self.auto_note.setText("Renseigne l'adresse publique du compte (Réglages du compte) pour que le lien "
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
        self.url.setVisible(not done)
        self.confirm_btn.setVisible(not done)

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

    def _accept(self, match_id: int) -> None:
        try:
            self.app.publications.accept_match(match_id)
        except PublicationError as exc:
            self.msg.setText(f"⚠ {exc}")
        self.refresh()
        self.changed.emit()

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
        self.changed.emit()


class PublishWorkshop(QDialog):
    """Une ou plusieurs publications (une par réseau) à faire à la main, l'une après l'autre."""

    def __init__(self, app: AppContext, publication_ids, parent=None, open_url=None):
        super().__init__(parent)
        self.app = app
        self.ids = [publication_ids] if isinstance(publication_ids, int) else list(publication_ids)
        self.open_url = open_url or (lambda u: QDesktopServices.openUrl(QUrl(u)))
        self.setWindowTitle("Atelier de publication")
        self.setMinimumWidth(600)
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
        # 1. la vidéo
        self.body.addWidget(label("1. La vidéo", "h2"))
        vr = QHBoxLayout()
        path = r["output_path"] or ""
        self.tile = VideoTile(path, r["thumbnail"] or "")
        vr.addWidget(self.tile)
        show = button("Afficher dans l'Explorateur", "ghost", icon="folder-open", on_click=lambda: reveal(path))
        show.setEnabled(bool(path) and Path(path).exists())
        vr.addWidget(show, 0, Qt.AlignVCenter)
        vr.addStretch(1)
        self.body.addLayout(vr)
        # 2. la page d'envoi, dans le navigateur dédié du compte
        self.body.addWidget(label("2. La page d'envoi", "h2"))
        br = QHBoxLayout()
        self.open_btn = button(f"Ouvrir la page d'envoi {pl.label}", "primary", icon="upload", on_click=self._open)
        br.addWidget(self.open_btn)
        br.addStretch(1)
        self.body.addLayout(br)
        self.page_note = label("", "muted", wrap=True, hint=False)
        self.body.addWidget(self.page_note)
        # 3. le lien de la vidéo publiée
        self.body.addWidget(label("3. Le lien de la vidéo publiée", "h2"))
        self.link = LinkBox(self.app, self.open_url)
        self.link.changed.connect(self.refresh)
        self.body.addWidget(self.link)
        self.msg = self.link.msg
        self.link.set_publication(self.pid)
        self.refresh()

    def refresh(self) -> None:
        r = self.row = self.app.publications.get(self.pid)
        done = r["status"] in ("published", "cancelled")
        self.state.setText({"published": "✓ Publiée", "cancelled": "Annulée"}.get(r["status"], "À publier"))
        self.page_note.setText("La page s'ouvre dans un nouvel onglet de ton navigateur, avec tes comptes. Glisse "
                               "la vidéo dans la page, colle le texte (copié en ouvrant la page), vérifie, puis "
                               "clique toi-même sur « Publier ».")
        self.link.refresh()
        self.open_btn.setEnabled(not done)

    # -- actions --------------------------------------------------------------------------
    def _open(self) -> None:
        r = self.row
        if r["status"] not in ("published", "cancelled", "manual_action"):
            self.app.publications.mark_manual(self.pid, "Envoyée depuis l'Atelier de publication : lien à confirmer.")
        c = rules.compose(r["platform"], r["title"] or "", r["description"] or "", r["hashtags"] or "")
        QApplication.clipboard().setText(c["title"] or c["text"])      # le texte, prêt à coller dans la page
        note = browser_login.open_page(self.app, r["account_id"], upload_url(r["platform"]), self.open_url)
        self.msg.setText(note or ("Page d'envoi ouverte dans un nouvel onglet de ton navigateur ; "
                                  + ("titre copié." if c["title"] else "texte copié.")))
        self.refresh()
