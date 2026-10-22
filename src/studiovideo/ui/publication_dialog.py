"""Préparer une publication (§18) : une vidéo, un texte maître, des destinations précises, des horaires.

La mise en ligne automatique demande que chaque compte soit connecté à la plateforme : tant que ce n'est
pas le cas, la publication est « assistée » (texte copié, vidéo affichée, page d'envoi ouverte), puis
confirmée par le lien public de la vidéo.
"""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QDateTime, Qt, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (QApplication, QCheckBox, QComboBox, QDateTimeEdit, QDialog, QDialogButtonBox,
                               QFormLayout, QHBoxLayout, QHeaderView, QInputDialog, QLineEdit, QMenu, QMessageBox,
                               QPlainTextEdit, QTableWidget, QTableWidgetItem, QToolButton, QVBoxLayout, QWidget)

from ..core import AppContext
from ..domain.models import Platform, PublicationStatus
from ..publish import rules
from ..services.publications import ExternalAccountNotConfirmed, PublicationError
from .widgets import button, label

STATUS = {s.value: s.label for s in PublicationStatus}
NO_DATE = QDateTime.fromString("2000-01-01T00:00", "yyyy-MM-ddTHH:mm")
FMT = "yyyy-MM-ddTHH:mm"


def _when(ed: QDateTimeEdit) -> str | None:
    return None if ed.dateTime() <= NO_DATE else ed.dateTime().toString(FMT)


def _date_edit(value: str | None) -> QDateTimeEdit:
    ed = QDateTimeEdit()
    ed.setCalendarPopup(True)
    ed.setDisplayFormat("dd/MM/yyyy HH:mm")
    ed.setMinimumDateTime(NO_DATE)
    ed.setSpecialValueText("Non programmée")
    ed.setDateTime(QDateTime.fromString(value, FMT) if value else NO_DATE)
    return ed


def video_info(row) -> dict | None:
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


class AssistedPublishDialog(QDialog):
    """Publication manuelle guidée, confirmée par le lien public."""

    def __init__(self, app: AppContext, publication_id: int, parent=None, open_url=None):
        super().__init__(parent)
        self.app, self.pid = app, publication_id
        self.open_url = open_url or (lambda u: QDesktopServices.openUrl(QUrl(u)))
        r = app.publications.get(publication_id)
        self.row = r
        pl = rules.RULES[r["platform"]]
        self.setWindowTitle(f"Publier sur {pl.label} — {r['account']}")
        self.setMinimumWidth(620)
        c = rules.compose(r["platform"], r["title"] or "", r["description"] or "", r["hashtags"] or "")
        lay = QVBoxLayout(self)
        lay.addWidget(label("Ce compte n'est pas connecté à la plateforme : la mise en ligne se fait à la main, "
                            "en trois étapes guidées.", "muted", wrap=True))
        for w in rules.check(r["platform"], r["title"] or "", r["description"] or "", r["hashtags"] or "",
                             video_info(r)):
            lay.addWidget(label("⚠ " + w, "muted", wrap=True))
        lay.addWidget(label("1. Le texte", "h2"))
        if c["title"]:
            row = QHBoxLayout()
            self.title = QLineEdit(c["title"])
            self.title.setReadOnly(True)
            row.addWidget(self.title, 1)
            row.addWidget(button("Copier le titre", on_click=lambda: self._copy(c["title"])))
            lay.addLayout(row)
        self.text = QPlainTextEdit(c["text"])
        self.text.setReadOnly(True)
        self.text.setMaximumHeight(140)
        lay.addWidget(self.text)
        lay.addWidget(button("Copier le texte", on_click=lambda: self._copy(c["text"])))
        lay.addWidget(label("2. La vidéo", "h2"))
        vr = QHBoxLayout()
        path = r["output_path"] or ""
        vl = label(path or "Aucune vidéo associée.", "faint", wrap=True)
        vr.addWidget(vl, 1)
        show = button("Afficher le fichier", on_click=lambda: self._reveal(path))
        show.setEnabled(bool(path) and Path(path).exists())
        vr.addWidget(show)
        lay.addLayout(vr)
        lay.addWidget(button(f"Ouvrir la page d'envoi {pl.label}", "primary", on_click=self._open))
        lay.addWidget(label("3. Une fois en ligne, collez le lien public de la vidéo", "h2"))
        self.url = QLineEdit(r["public_url"] or "")
        self.url.setPlaceholderText({"youtube": "https://youtu.be/…", "tiktok": "https://www.tiktok.com/@…/video/…"}
                                    .get(r["platform"], "https://…"))
        lay.addWidget(self.url)
        self.msg = label("", "muted", wrap=True)
        lay.addWidget(self.msg)
        bb = QDialogButtonBox()
        ok = bb.addButton("Confirmer la publication", QDialogButtonBox.AcceptRole)
        ok.setProperty("kind", "primary")
        bb.addButton("Plus tard", QDialogButtonBox.RejectRole)
        ok.clicked.connect(self._confirm)
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)

    def _copy(self, text: str) -> None:
        QApplication.clipboard().setText(text)
        self.msg.setText("Copié dans le presse-papiers.")

    def _reveal(self, path: str) -> None:
        from .pages.library import reveal
        reveal(path)

    def _open(self) -> None:
        r = self.row
        if r["status"] not in ("published", "cancelled", "manual_action"):
            self.app.publications.mark_manual(self.pid, "En attente du lien public après envoi manuel.")
        self.open_url(rules.RULES[r["platform"]].upload_url)
        self.msg.setText("Page d'envoi ouverte dans votre navigateur.")

    def _confirm(self) -> None:
        try:
            self.app.publications.confirm_published(self.pid, self.url.text())
        except PublicationError as exc:
            self.msg.setText(f"⚠ {exc}")
            return
        self.accept()


class PublicationDialog(QDialog):
    def __init__(self, app: AppContext, master_id: int | None = None, variant_id: int | None = None,
                 day: str | None = None, parent=None):
        super().__init__(parent)
        self.app, self.master_id = app, master_id
        m = app.publications.master(master_id) if master_id else None
        self.setWindowTitle("Publication" if m else "Nouvelle publication")
        self.resize(820, 720)
        lay = QVBoxLayout(self)
        form = QFormLayout()
        self.video = QComboBox()
        self.video.addItem("Aucune vidéo (à choisir plus tard)", None)
        for v in app.montage.exports(limit=200):
            ok = Path(v["output_path"]).exists()
            self.video.addItem(f"{v['project_name']} · {v['label']} · {v['aspect']}" + ("" if ok else " (fichier absent)"),
                               v["id"])
        want = m["variant_id"] if m else variant_id
        self.video.setCurrentIndex(max(0, self.video.findData(want)))
        form.addRow("Vidéo", self.video)
        self.concept = QComboBox()
        for c in app.concepts.list_concepts():
            self.concept.addItem(c.name, c.id)
        if m:
            self.concept.setCurrentIndex(max(0, self.concept.findData(m["concept_id"])))
            self.concept.setEnabled(False)
        elif variant_id:
            pr = app.db.query_one("SELECT p.concept_id FROM video_variant v JOIN clip c ON c.id = v.clip_id "
                                  "JOIN project p ON p.id = c.project_id WHERE v.id = ?", (variant_id,))
            if pr:
                self.concept.setCurrentIndex(max(0, self.concept.findData(pr["concept_id"])))
        form.addRow("Concept", self.concept)
        self.title = QLineEdit(m["title"] if m else "")
        self.title.setPlaceholderText("Titre (YouTube) / première ligne de la légende")
        form.addRow("Titre", self.title)
        self.desc = QPlainTextEdit(m["description"] if m else "")
        self.desc.setMaximumHeight(110)
        form.addRow("Texte", self.desc)
        self.tags = QLineEdit(m["hashtags"] if m else "")
        self.tags.setPlaceholderText("#humour #sketch")
        form.addRow("Hashtags", self.tags)
        self.default_when = _date_edit(m["planned_at"] if m else (f"{day}T18:00" if day else None))
        form.addRow("Date par défaut", self.default_when)
        lay.addLayout(form)
        lay.addWidget(label("Destinations", "h2"))
        lay.addWidget(label("Seuls les comptes du concept sont proposés. Chaque destination a son propre horaire "
                            "et peut avoir son propre texte.", "muted", wrap=True))
        self.table = QTableWidget(0, 5)
        self.table.setHorizontalHeaderLabels(["", "Compte", "Horaire", "État", ""])
        self.table.verticalHeader().hide()
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        self.table.setSelectionMode(QTableWidget.NoSelection)
        lay.addWidget(self.table, 1)
        row = QHBoxLayout()
        row.addWidget(button("Ajouter un compte hors concept…", "ghost", on_click=self._add_external))
        row.addStretch(1)
        lay.addLayout(row)
        self.warn = label("", "muted", wrap=True)
        lay.addWidget(self.warn)
        bb = QDialogButtonBox()
        save = bb.addButton("Enregistrer", QDialogButtonBox.AcceptRole)
        save.setProperty("kind", "primary")
        if m:
            bb.addButton("Supprimer", QDialogButtonBox.DestructiveRole).clicked.connect(self._delete)
        bb.addButton("Fermer", QDialogButtonBox.RejectRole)
        save.clicked.connect(self._save)
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)
        self.rows: list[dict] = []
        self.external: set[int] = set()
        self.concept.currentIndexChanged.connect(self._fill)
        self.video.currentIndexChanged.connect(lambda _i: self._check())
        for w in (self.title, self.tags):
            w.textChanged.connect(self._check)
        self.desc.textChanged.connect(self._check)
        self._fill()

    # -- destinations -------------------------------------------------------------------------
    def _fill(self) -> None:
        cid = self.concept.currentData()
        existing = {p.account.id: p for p in (self.app.publications.list_for_master(self.master_id)
                                              if self.master_id else [])}
        accounts = []
        if cid:
            accounts = [(d.account, d.default_selected) for d in self.app.concepts.proposed_destinations(cid)]
        ids = {a.id for a, _ in accounts}
        for aid in list(existing) + sorted(self.external):
            if aid not in ids:
                accounts.append((self.app.concepts.get_account(aid), True))
                ids.add(aid)
        self.table.setRowCount(len(accounts))
        self.rows = []
        for i, (acc, default) in enumerate(accounts):
            pub = existing.get(acc.id)
            cb = QCheckBox()
            cb.setChecked(bool(pub) or default)
            cb.setEnabled(pub is None)
            cb.toggled.connect(lambda _v: self._check())
            cell = QWidget()
            cl = QHBoxLayout(cell)
            cl.setContentsMargins(8, 0, 0, 0)
            if pub is None:
                cl.addWidget(cb)
            else:
                cl.addWidget(label("✓", None))
            self.table.setCellWidget(i, 0, cell)
            name = acc.display + ("  (hors concept)" if acc.id in self.external or (pub and pub.is_exceptional) else "")
            self.table.setItem(i, 1, QTableWidgetItem(name))
            when = _date_edit(pub.scheduled_at if pub else None)
            when.setEnabled(not pub or pub.status not in (PublicationStatus.PUBLISHED, PublicationStatus.CANCELLED))
            self.table.setCellWidget(i, 2, when)
            st = STATUS[pub.status.value] if pub else "Nouvelle"
            if pub and pub.overrides:
                st += " · texte personnalisé"
            self.table.setItem(i, 3, QTableWidgetItem(st))
            if pub:
                tb = QToolButton()
                tb.setText("Actions ▾")
                tb.setPopupMode(QToolButton.InstantPopup)
                menu = QMenu(tb)
                done = pub.status in (PublicationStatus.PUBLISHED, PublicationStatus.CANCELLED)
                if acc.platform.value == "youtube":
                    up = menu.addAction("Mettre en ligne sur YouTube" + (" (à l'heure prévue)" if pub.scheduled_at
                                                                         else " maintenant"),
                                        lambda p=pub.id: self._upload(p))
                elif acc.platform.value in ("tiktok", "instagram", "facebook"):
                    up = menu.addAction(f"Publier sur {acc.platform.label} maintenant",
                                        lambda p=pub.id: self._upload(p, now=True))
                if acc.platform.value in ("youtube", "tiktok", "instagram", "facebook"):
                    up.setEnabled(not done and acc.connection_status == "connected"
                                  and pub.status != PublicationStatus.SCHEDULED_PLATFORM)
                    if acc.connection_status != "connected":
                        up.setText(up.text() + " — compte non connecté")
                menu.addAction("Publier (assisté)…", lambda p=pub.id: self._assisted(p)).setEnabled(not done)
                menu.addAction("Personnaliser le texte…", lambda p=pub: self._customize(p)).setEnabled(not done)
                menu.addAction("Annuler cette destination", lambda p=pub.id: self._cancel(p)).setEnabled(not done)
                tb.setMenu(menu)
                self.table.setCellWidget(i, 4, tb)
            self.rows.append({"account": acc, "check": cb, "when": when, "pub": pub})
        self.table.resizeColumnsToContents()
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        if not accounts:
            self.warn.setText("Ce concept n'a aucun compte associé : ajoutez-en dans Paramètres → Concepts.")
        else:
            self._check()

    def _check(self) -> None:
        vid = self.video.currentData()
        vrow = self.app.db.query_one("SELECT output_path, aspect FROM video_variant WHERE id = ?", (vid,)) if vid else None
        info = video_info(vrow)
        brief = self.app.campaigns.brief_for_variant(vid) if self.app.campaigns else None
        msgs = []
        for r in self.rows:
            if not r["check"].isChecked():
                continue
            p = r["pub"]
            t = p.title if p else self.title.text()
            d = p.description if p else self.desc.toPlainText()
            h = p.hashtags if p else self.tags.text()
            msgs += rules.check(r["account"].platform.value, t or "", d or "", h or "", info)
            if brief is not None:
                from ..edit import campaign as cp
                msgs += [f"Brief « {brief.name} » : {c.rule} manquant(e)" if c.ok is False and not
                         c.rule.startswith("Mot interdit") else f"Brief « {brief.name} » : {c.rule} {c.detail}"
                         for c in cp.check_text(brief, f"{t or ''} {d or ''} {h or ''}") if c.ok is False]
                if brief.platforms and r["account"].platform.value not in brief.platforms:
                    msgs.append(f"Brief « {brief.name} » : {r['account'].platform.label} n'est pas prévu.")
        self.warn.setText("\n".join("⚠ " + m for m in dict.fromkeys(msgs)) or "✓ Textes conformes aux limites des "
                          "plateformes choisies.")

    def _add_external(self) -> None:
        cid = self.concept.currentData()
        if not cid:
            return
        ext = [d.account for d in self.app.concepts.proposed_destinations(cid, include_external=True)
               if not d.in_concept and d.account.id not in self.external]
        if not ext:
            QMessageBox.information(self, "Compte hors concept", "Aucun autre compte enregistré.")
            return
        names = [a.display for a in ext]
        choice, ok = QInputDialog.getItem(self, "Compte hors concept", "Compte :", names, 0, False)
        if not ok:
            return
        acc = ext[names.index(choice)]
        if QMessageBox.question(self, "Compte hors concept",
                                f"« {acc.display} » n'appartient pas au concept « {self.concept.currentText()} ».\n"
                                "Publier quand même sur ce compte ?") != QMessageBox.Yes:
            return
        self.external.add(acc.id)
        self._fill()

    # -- enregistrement -----------------------------------------------------------------------
    def _save(self) -> None:
        ps = self.app.publications
        fields = {"title": self.title.text().strip(), "description": self.desc.toPlainText().strip(),
                  "hashtags": rules.normalize_hashtags(self.tags.text()), "planned_at": _when(self.default_when),
                  "variant_id": self.video.currentData()}
        if self.master_id:
            ps.update_master(self.master_id, **fields)
        else:
            cid = self.concept.currentData()
            if not cid:
                QMessageBox.information(self, "Publication", "Créez d'abord un concept.")
                return
            self.master_id = ps.create_master(cid, fields["title"], fields["description"], fields["hashtags"],
                                              variant_id=fields["variant_id"], planned_at=fields["planned_at"])
        chosen = [r["account"].id for r in self.rows if r["check"].isChecked() and r["pub"] is None]
        try:
            ps.set_destinations(self.master_id, chosen, confirmed_external=self.external)
        except ExternalAccountNotConfirmed as exc:
            QMessageBox.warning(self, "Publication", str(exc))
            return
        by_acc = {p.account.id: p for p in ps.list_for_master(self.master_id)}
        default = fields["planned_at"]
        for r in self.rows:
            p = by_acc.get(r["account"].id)
            if p is None or p.status in (PublicationStatus.PUBLISHED, PublicationStatus.CANCELLED):
                continue
            when = _when(r["when"]) or (default if r["pub"] is None else None)
            if when != p.scheduled_at:
                ps.schedule(p.id, when)
        self.accept()

    def _upload(self, pid: int, now: bool = False) -> None:
        r = self.app.publications.get(pid)
        if not r["output_path"] or not Path(r["output_path"]).exists():
            QMessageBox.warning(self, "YouTube", "Choisissez d'abord une vidéo existante pour cette publication.")
            return
        label_ = rules.RULES[r["platform"]].label
        problems = rules.check(r["platform"], r["title"] or "", r["description"] or "", r["hashtags"] or "")
        if problems:
            QMessageBox.warning(self, label_, "\n".join(problems))
            return
        self.app.jobs.submit("publish_upload", f"{label_} · {r['title'] or 'sans titre'}",
                             {"publication_id": pid, "now": now}, critical=True)
        self.warn.setText("Mise en ligne lancée (suivi dans les tâches).")

    def _assisted(self, pid: int) -> None:
        AssistedPublishDialog(self.app, pid, self).exec()
        self._fill()

    def _customize(self, pub) -> None:
        dlg = QDialog(self)
        dlg.setWindowTitle(f"Texte pour {pub.account.display}")
        dlg.setMinimumWidth(520)
        lay = QVBoxLayout(dlg)
        lay.addWidget(label("Ce texte ne s'applique qu'à cette destination. Videz un champ pour reprendre le "
                            "texte commun.", "muted", wrap=True))
        f = QFormLayout()
        t = QLineEdit(pub.title or "")
        d = QPlainTextEdit(pub.description or "")
        h = QLineEdit(pub.hashtags or "")
        f.addRow("Titre", t)
        f.addRow("Texte", d)
        f.addRow("Hashtags", h)
        lay.addLayout(f)
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.accepted.connect(dlg.accept)
        bb.rejected.connect(dlg.reject)
        lay.addWidget(bb)
        if not dlg.exec():
            return
        ps = self.app.publications
        for name, val in (("title", t.text().strip()), ("description", d.toPlainText().strip()),
                          ("hashtags", rules.normalize_hashtags(h.text()))):
            if val:
                ps.override(pub.id, **{name: val})
            else:
                ps.reset_to_master(pub.id, name)
        self._fill()

    def _cancel(self, pid: int) -> None:
        if QMessageBox.question(self, "Publication", "Annuler cette destination ?") == QMessageBox.Yes:
            self.app.publications.cancel(pid)
            self._fill()

    def _delete(self) -> None:
        if QMessageBox.question(self, "Publication", "Supprimer cette publication et toutes ses destinations ?"
                                ) != QMessageBox.Yes:
            return
        try:
            self.app.publications.delete_master(self.master_id)
        except PublicationError as exc:
            QMessageBox.warning(self, "Publication", str(exc))
            return
        self.accept()


PLATFORM_SHORT = {Platform.YOUTUBE.value: "YT", Platform.TIKTOK.value: "TT", Platform.INSTAGRAM.value: "IG",
                  Platform.FACEBOOK.value: "FB", Platform.X.value: "X"}
