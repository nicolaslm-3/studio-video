"""Calendrier : les vidéos programmées, mois par mois, comme un agenda.

Un menu « Concept » montre les vidéos d'un concept, ou de tous. Chaque vidéo est un petit rectangle à sa date : le
concept, le nom de la vidéo et trois petits logos (YouTube, TikTok, Meta) qu'on met en couleur ou qu'on grise d'un
clic, pour s'organiser — le logiciel ne publie rien à partir de ces cases. « Nouvelle publication » (ou un double-clic
sur un jour, ou un clic sur une vidéo pour la modifier) ouvre un rectangle volant : concept, vidéo, un seul bloc de
texte, date et heure, logos (`PublicationService.plan`).
"""
from __future__ import annotations

import calendar as _cal
from datetime import date, datetime, timedelta

from PySide6.QtCore import QDate, QDateTime, QLocale, QSize, Qt, QTime, Signal
from PySide6.QtWidgets import (QComboBox, QFrame, QGraphicsOpacityEffect, QGridLayout, QHBoxLayout, QLabel,
                               QPlainTextEdit, QScrollArea, QSizePolicy, QToolButton, QVBoxLayout,
                               QWidget)

from ...core import AppContext
from .. import theme
from ..publish_workshop import VideoTile, reveal
from ..when_picker import WhenPicker
from ..widgets import PageHeader, Popover, button, label, tool
from ..widgets import ask
from ..widgets import dot_icon

MONTHS = ["janvier", "février", "mars", "avril", "mai", "juin", "juillet", "août", "septembre", "octobre",
          "novembre", "décembre"]
DAYS = ["lun.", "mar.", "mer.", "jeu.", "ven.", "sam.", "dim."]
NETS = (("youtube", "YouTube"), ("tiktok", "TikTok"), ("meta", "Meta"))


class NetToggle(QToolButton):
    """Petit logo d'un réseau : en couleur (coché) ou grisé ; le nom du réseau seulement au survol."""

    def __init__(self, key: str, name: str, size: int = 18, parent=None):
        super().__init__(parent)
        self.key = key
        self.setCheckable(True)
        self.setFixedSize(size + 6, size + 6)
        self.setIconSize(QSize(size, size))
        self.setCursor(Qt.PointingHandCursor)
        self.setToolTip(name)
        self.setStyleSheet("QToolButton { border: none; background: transparent; padding: 0; }")
        self._size = size
        self.toggled.connect(lambda _on: self._paint())
        self._paint()

    def _paint(self) -> None:
        self.setIcon(theme.platform_icon(self.key, self._size) if self.isChecked()
                     else theme.icon(theme.PLATFORMS[self.key][0], theme.color("faint"), self._size))


class PlanChip(QFrame):
    """Une vidéo programmée : concept, nom de la vidéo, trois petits logos."""
    open = Signal(int)
    toggled = Signal(int)                                # un logo changé : partout ailleurs aussi

    def __init__(self, app: AppContext, row: dict, with_date: bool = False, parent=None):
        super().__init__(parent)
        self.app, self.mid = app, row["id"]
        self.setProperty("role", "projitem")
        self.setCursor(Qt.PointingHandCursor)
        self.setStyleSheet(f"QFrame {{ border-left: 3px solid {row['color'] or '#8A8F98'}; }}")
        self.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
        v = QVBoxLayout(self)
        v.setContentsMargins(6, 2, 4, 2)
        v.setSpacing(0)
        when = (row["planned_at"] or "")[11:16]
        if with_date and row["planned_at"]:
            d = QDate.fromString(row["planned_at"][:10], "yyyy-MM-dd")
            when = QLocale(QLocale.French).toString(d, "ddd d MMM") + " · " + when
        top = label(f"{when}  {row['concept']}" if when else row["concept"], "faint", hint=False)
        top.setStyleSheet("font-size: 10px; border: none;")
        v.addWidget(top)
        name = row["video"] or row["title"] or "Sans titre"
        nm = QLabel(name)
        nm.setStyleSheet("font-size: 11px; font-weight: 700; border: none;")
        nm.setMinimumWidth(0)
        nm.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        v.addWidget(nm)
        h = QHBoxLayout()
        h.setContentsMargins(0, 0, 0, 0)
        h.setSpacing(0)
        self.toggles: dict[str, NetToggle] = {}
        for key, nm_ in NETS:
            t = NetToggle(key, nm_, 13)
            t.setChecked(row["networks"].get(key, False))
            t.clicked.connect(lambda _=False, k=key: (self.app.publications.toggle_network(self.mid, k),
                                                      self.toggled.emit(self.mid)))
            h.addWidget(t)
            self.toggles[key] = t
        h.addStretch(1)
        v.addLayout(h)
        self.setToolTip(f"{row['concept']} · {name}\n{row['text'][:300]}")

    def mouseReleaseEvent(self, e) -> None:
        if e.button() == Qt.LeftButton:
            self.open.emit(self.mid)
        super().mouseReleaseEvent(e)


class DayCell(QFrame):
    open_master = Signal(int)
    toggled = Signal(int)
    new_on_day = Signal(str)
    MAX = 3

    def __init__(self, app: AppContext, parent=None):
        super().__init__(parent)
        self.app = app
        self.setProperty("role", "card")
        self.setMinimumHeight(96)
        self.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Expanding)
        self.lay = QVBoxLayout(self)
        self.lay.setContentsMargins(4, 3, 4, 3)
        self.lay.setSpacing(2)
        self.day = ""

    def set_day(self, d: date, in_month: bool, today: bool, items: list) -> None:
        while self.lay.count():
            w = self.lay.takeAt(0).widget()
            if w:
                w.hide()                  # sinon son affichage différé en fait une fenêtre
                w.setParent(None)
                w.deleteLater()
        self.day = d.isoformat()
        if in_month:
            self.setGraphicsEffect(None)
        else:
            fade = QGraphicsOpacityEffect(self)
            fade.setOpacity(0.3)
            self.setGraphicsEffect(fade)
        head = label(("● " if today else "") + str(d.day), "h2" if today else ("muted" if in_month else "faint"))
        self.lay.addWidget(head)
        for r in items[:self.MAX]:
            c = PlanChip(self.app, r)
            c.open.connect(self.open_master.emit)
            c.toggled.connect(self.toggled.emit)
            self.lay.addWidget(c)
        if len(items) > self.MAX:
            more = label(f"+ {len(items) - self.MAX} autre(s)", "faint", hint=False)
            more.setToolTip("\n".join(f"{r['concept']} · {r['video'] or r['title'] or 'Sans titre'}"
                                      for r in items[self.MAX:]))
            self.lay.addWidget(more)
        self.lay.addStretch(1)

    def mouseDoubleClickEvent(self, e):
        self.new_on_day.emit(self.day)
        super().mouseDoubleClickEvent(e)


class PlanPopover(Popover):
    """Nouvelle publication (ou modification) : concept, vidéo, texte, date et heure, logos des réseaux."""
    saved = Signal()

    def __init__(self, app: AppContext, master_id: int | None = None, day: str | None = None,
                 concept_id: int | None = None, project_id: int | None = None, parent=None, text: str = ""):
        super().__init__(parent)
        self.app, self.mid = app, master_id
        row = app.publications.plan_of(master_id) if master_id else None
        self.setFixedWidth(420)
        self.body.addWidget(label("Modifier la publication" if row else "Nouvelle publication", "h2"))
        self.concept = QComboBox()
        self.concept.setPlaceholderText("Concept")
        for c in app.concepts.user_concepts():                       # « Brouillons » caché
            self.concept.addItem(dot_icon(c.color), c.name, c.id)
        want = row["concept_id"] if row else concept_id
        if want is None and project_id:                               # le concept de la vidéo
            p = app.projects.get(project_id)
            want = p["concept_id"] if p else None
        self.concept.setCurrentIndex(max(0, self.concept.findData(want)) if self.concept.count() else -1)
        self.concept.setToolTip("Concept")
        self.body.addWidget(self.concept)
        self.video = QComboBox()
        self.video.setPlaceholderText("Vidéo")
        self.video.setToolTip("Vidéo")
        for p in app.projects.all(include_archived=False):
            self.video.addItem(p["name"], p["id"])
        want = row["project_id"] if row else project_id
        self.video.setCurrentIndex(self.video.findData(want) if want else -1)
        self.body.addWidget(self.video)
        # la vidéo exportée : à glisser dans la page d'envoi, ou à montrer dans l'Explorateur
        vr = QHBoxLayout()
        vr.setSpacing(6)
        self.tile = VideoTile("")
        vr.addWidget(self.tile, 1)
        self.reveal_btn = tool("folder-open", "Afficher dans l'Explorateur", lambda: reveal(self.tile.path))
        vr.addWidget(self.reveal_btn)
        self.body.addLayout(vr)
        self.video.currentIndexChanged.connect(self._show_video)
        self._show_video()
        self.text = QPlainTextEdit(row["text"] if row else text)
        self.text.setPlaceholderText("Texte : titre, description, hashtags…")
        self.text.setFixedHeight(110)
        self.body.addWidget(self.text)
        self.when = WhenPicker()
        if row and row["planned_at"]:
            self.when.cal.setMinimumDate(QDate(2000, 1, 1))
            self.when.setDateTime(QDateTime.fromString(row["planned_at"][:16], "yyyy-MM-ddTHH:mm"))
        elif day:
            d = QDate.fromString(day, "yyyy-MM-dd")
            self.when.cal.setMinimumDate(min(d, QDate.currentDate()))
            self.when.setDateTime(QDateTime(d, QTime(18, 0)))
        self.when.setToolTip("Date et heure")
        self.body.addWidget(self.when)
        nets = QHBoxLayout()
        nets.setSpacing(4)
        self.nets: dict[str, NetToggle] = {}
        for key, nm in NETS:
            t = NetToggle(key, nm, 20)
            t.setChecked(row["networks"].get(key, False) if row else True)
            if row:                                       # le même logo que dans le calendrier : changé aussitôt
                t.clicked.connect(lambda _=False, k=key: (self.app.publications.toggle_network(self.mid, k),
                                                          self.saved.emit()))
            nets.addWidget(t)
            self.nets[key] = t
        nets.addStretch(1)
        self.body.addLayout(nets)
        foot = QHBoxLayout()
        if row:
            foot.addWidget(button("Supprimer", "ghost", icon="trash-2", on_click=self._delete))
        foot.addStretch(1)
        self.save_btn = button("Enregistrer", "primary", on_click=self._save)
        foot.addWidget(self.save_btn)
        self.body.addLayout(foot)
        self.msg = label("", "muted", wrap=True, hint=False)
        self.msg.hide()
        self.body.addWidget(self.msg)

    def _show_video(self) -> None:
        """Le dernier export de la vidéo choisie ; pas exporté ou fichier déplacé : grisé, « non disponible »."""
        pid = self.video.currentData()
        path = (self.app.projects.load_data(pid).get("export_last") or {}).get("path", "") if pid else ""
        self.tile.set_video(path, missing="Pas encore exportée (onglet Export)" if pid else "Choisis une vidéo")
        self.reveal_btn.setEnabled(self.tile.isEnabled())
        self.reveal_btn.setToolTip("Afficher dans l'Explorateur" if self.tile.isEnabled() else "Non disponible")

    def _save(self) -> None:
        cid = self.concept.currentData()
        if cid is None:
            self.msg.setText("Crée d'abord un concept (onglet Export, en bas à droite).")
            self.msg.show()
            return
        when = self.when.dateTime().toString("yyyy-MM-ddTHH:mm")
        self.mid = self.app.publications.plan(cid, self.video.currentData(), self.text.toPlainText(), when,
                                              {k: t.isChecked() for k, t in self.nets.items()}, self.mid)
        self.saved.emit()
        self.close()

    def _delete(self) -> None:
        if not ask(self, "Supprimer", "Retirer cette publication du calendrier ?"):
            return
        try:
            self.app.publications.delete_master(self.mid)
        except Exception as exc:                                   # déjà publiée sur un réseau
            self.msg.setText(str(exc))
            self.msg.show()
            return
        self.saved.emit()
        self.close()


class CalendarPage(QWidget):
    def __init__(self, app: AppContext, parent=None):
        super().__init__(parent)
        self.app = app
        self.pop: PlanPopover | None = None
        t = date.today()
        self.year, self.month = t.year, t.month
        lay = QVBoxLayout(self)
        lay.setContentsMargins(28, 22, 28, 16)
        lay.addWidget(PageHeader("Calendrier"))
        self.concept = QComboBox()
        self.concept.setToolTip("Les vidéos d'un concept, ou de tous")
        self.concept.currentIndexChanged.connect(self.refresh)
        self.new_btn = tool("plus", "Nouvelle publication", lambda: self.new_publication(), size=30)
        nav = QHBoxLayout()
        nav.addWidget(tool("chevron-left", "Mois précédent", lambda: self._shift(-1)))
        self.title = label("", "h2")
        self.title.setToolTip("Double-clic sur un jour : nouvelle publication ce jour-là")
        nav.addWidget(self.title)
        nav.addWidget(tool("chevron-right", "Mois suivant", lambda: self._shift(1)))
        nav.addWidget(button("Aujourd'hui", "ghost", on_click=self._today))
        self.sync_btn = tool("refresh-cw", "Retrouver les vidéos publiées ces 20 derniers jours sur les chaînes des "
                             "concepts (YouTube, TikTok)", self.sync, size=28, icon_px=15)
        nav.addWidget(self.sync_btn)
        self.sync_note = label("", "faint", hint=False)
        nav.addWidget(self.sync_note)
        nav.addStretch(1)
        lay.addLayout(nav)
        body = QHBoxLayout()
        body.setSpacing(24)
        gridw = QWidget()
        self.grid = QGridLayout(gridw)
        self.grid.setSpacing(4)
        self.grid.setContentsMargins(0, 0, 0, 0)
        for i, d in enumerate(DAYS):
            h = label(d, "faint")
            h.setAlignment(Qt.AlignCenter)
            self.grid.addWidget(h, 0, i)
        self.cells: list[DayCell] = []
        for k in range(42):
            c = DayCell(app)
            c.open_master.connect(lambda m, c=c: self.open_master(m, c))
            c.toggled.connect(self.net_toggled)
            c.new_on_day.connect(lambda d, c=c: self.new_publication(day=d, anchor=c))
            self.grid.addWidget(c, 1 + k // 7, k % 7)
            self.cells.append(c)
        for i in range(7):
            self.grid.setColumnStretch(i, 1)
        for r in range(1, 7):
            self.grid.setRowStretch(r, 1)
        body.addWidget(gridw, 1)
        side = QVBoxLayout()
        side.setSpacing(8)
        side.addWidget(self.concept)
        side.addWidget(label("À venir", "h2"))
        self.up_host = QWidget()
        self.upcoming = QVBoxLayout(self.up_host)
        self.upcoming.setContentsMargins(0, 0, 6, 0)
        self.upcoming.setSpacing(6)
        up = QScrollArea()
        up.setWidgetResizable(True)
        up.setFrameShape(QFrame.NoFrame)
        up.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        up.setWidget(self.up_host)
        side.addWidget(up, 1)
        self.unplanned = QComboBox()                              # vidéos jamais mises dans le calendrier
        self.unplanned.setPlaceholderText("Vidéos pas encore au calendrier")
        self.unplanned.setToolTip("Vidéos pas encore au calendrier : en choisir une pour la programmer")
        self.unplanned.activated.connect(self._plan_unplanned)
        side.addWidget(self.unplanned)
        side.addWidget(self.new_btn, 0, Qt.AlignLeft)           # en bas, discret
        sw = QWidget()
        sw.setLayout(side)
        sw.setFixedWidth(270)
        body.addWidget(sw)
        lay.addLayout(body, 1)

    # -- navigation ----------------------------------------------------------------------------
    def _shift(self, n: int) -> None:
        m = self.month - 1 + n
        self.year, self.month = self.year + m // 12, m % 12 + 1
        self.refresh()

    def _today(self) -> None:
        t = date.today()
        self.year, self.month = t.year, t.month
        self.refresh()

    def _fill_concepts(self) -> None:
        cur = self.concept.currentData()
        self.concept.blockSignals(True)
        self.concept.clear()
        self.concept.addItem("Tous les concepts", None)
        for c in self.app.concepts.user_concepts():
            self.concept.addItem(dot_icon(c.color), c.name, c.id)
        self.concept.setCurrentIndex(max(0, self.concept.findData(cur)))
        self.concept.blockSignals(False)

    def refresh(self) -> None:
        self._fill_concepts()
        cid = self.concept.currentData()
        self.title.setText(f"{MONTHS[self.month - 1].capitalize()} {self.year}")
        first = date(self.year, self.month, 1)
        start = first - timedelta(days=first.weekday())
        end = start + timedelta(days=42)
        by_day: dict[str, list] = {}
        for r in self.app.publications.planned(start.isoformat() + "T00:00", end.isoformat() + "T00:00", cid):
            by_day.setdefault(r["planned_at"][:10], []).append(r)
        today = date.today()
        for k, cell in enumerate(self.cells):
            d = start + timedelta(days=k)
            cell.set_day(d, d.month == self.month, d == today, by_day.get(d.isoformat(), []))
        self._fill_upcoming(cid)
        self._fill_unplanned()

    def _fill_unplanned(self) -> None:
        planned = {r["project_id"] for r in self.app.db.query(
            "SELECT DISTINCT project_id FROM master_publication WHERE project_id IS NOT NULL")}
        self.unplanned.blockSignals(True)
        self.unplanned.clear()
        for p in self.app.projects.all(include_archived=False):
            if p["id"] not in planned and '"parent"' not in (p["data_json"] or ""):
                self.unplanned.addItem(p["name"], p["id"])
        self.unplanned.setCurrentIndex(-1)
        self.unplanned.setVisible(self.unplanned.count() > 0)
        self.unplanned.blockSignals(False)

    def _plan_unplanned(self, index: int) -> None:
        pid = self.unplanned.itemData(index)
        self.unplanned.setCurrentIndex(-1)
        if pid:
            self._popover(PlanPopover(self.app, project_id=pid, parent=self), self.unplanned)

    def net_toggled(self, mid: int) -> None:
        """Un petit logo changé (calendrier, « À venir » ou rectangle) : tous les autres suivent."""
        from PySide6.QtCore import QTimer
        QTimer.singleShot(0, lambda: self._sync_nets(mid))   # après le clic : le petit bouton peut être recréé

    def _sync_nets(self, mid: int) -> None:
        self.refresh()
        pop = self.pop
        if pop is not None and pop.isVisible() and pop.mid == mid:
            nets = self.app.publications.plan_of(mid)["networks"]
            for k, t in pop.nets.items():
                t.blockSignals(True)
                t.setChecked(nets.get(k, False))
                t.blockSignals(False)
                t._paint()

    def _fill_upcoming(self, cid: int | None) -> None:
        """Toutes les vidéos à venir (du concept choisi, ou de tous), de la plus proche à la plus lointaine."""
        while self.upcoming.count():
            w = self.upcoming.takeAt(0).widget()
            if w:
                w.hide()
                w.setParent(None)
                w.deleteLater()
        rows = self.app.publications.planned(now_local(), "9999-12-31T23:59", cid)
        for r in rows:
            c = PlanChip(self.app, r, with_date=True)
            c.open.connect(lambda m, c=c: self.open_master(m, c))
            c.toggled.connect(self.net_toggled)
            self.upcoming.addWidget(c)
        if not rows:
            self.upcoming.addWidget(label("Aucune vidéo programmée.", "faint", hint=False))
        self.upcoming.addStretch(1)

    def sync(self) -> int | None:
        """Bouton discret : les vidéos publiées ces 20 derniers jours entrent dans le calendrier."""
        if self.app.db.query_one("SELECT 1 FROM job WHERE kind = 'sync_calendar' AND status IN ('pending','running')"):
            return None
        self.sync_btn.setEnabled(False)
        self.sync_note.setText("Recherche des vidéos publiées…")
        return self.app.jobs.submit("sync_calendar", "Vidéos publiées → calendrier", {})

    def on_job(self, job) -> None:
        if job.kind != "sync_calendar" or job.status in ("pending", "running"):
            return
        self.sync_btn.setEnabled(True)
        r = job.result or {}
        if job.status == "succeeded":
            self.sync_note.setText(f"✓ {r.get('added', 0)} ajoutée(s), {r.get('matched', 0)} reliée(s)"
                                   + (" · ⚠ " + r["errors"][0] if r.get("errors") else ""))
        else:
            self.sync_note.setText("⚠ " + ((job.error or "Arrêtée").splitlines() or ["Arrêtée"])[0][:160])
        self.refresh()

    def pending_count(self) -> int:
        """Plus de liste « à faire » : rien à signaler à côté du nom de la page."""
        return 0

    # -- actions -------------------------------------------------------------------------------
    def _popover(self, pop: PlanPopover, anchor: QWidget | None) -> PlanPopover:
        pop.saved.connect(self.refresh)
        self.pop = pop
        pop.show_at(anchor or self.new_btn)
        return pop

    def new_publication(self, variant_id: int | None = None, day: str | None = None,
                        anchor: QWidget | None = None) -> PlanPopover:
        project = None
        if variant_id:
            r = self.app.db.query_one("SELECT c.project_id FROM video_variant v JOIN clip c ON c.id = v.clip_id "
                                      "WHERE v.id = ?", (variant_id,))
            project = r["project_id"] if r else None
        return self._popover(PlanPopover(self.app, day=day, concept_id=self.concept.currentData(),
                                         project_id=project, parent=self), anchor)

    def open_master(self, master_id: int, anchor: QWidget | None = None) -> PlanPopover:
        return self._popover(PlanPopover(self.app, master_id, parent=self), anchor)


def now_local() -> str:
    return datetime.now().strftime("%Y-%m-%dT%H:%M")


def month_days(year: int, month: int) -> int:
    return _cal.monthrange(year, month)[1]
