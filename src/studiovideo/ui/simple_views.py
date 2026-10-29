"""Espaces simplifiés : Clipping (une liste d'extraits à cocher) et Traduction (un formulaire en 4 étapes).

Un projet = une vidéo, qui passe d'un écran à l'autre (onglets du dock) : même montage, même format. Clipping
choisit le passage (un projet par extrait coché), Traduction prépare les langues, l'onglet Export
(`export_view.py`) exporte et publie. L'import est dans l'en-tête commun du Studio. Les moteurs sont ceux du Studio : extraits
(`find_highlights`, `auto_clips`), suivi de la personne qui parle (`reframe`), traduction (`translate_video`)."""
from __future__ import annotations

import json
from pathlib import Path

from PySide6.QtCore import QSize, Qt, QTimer, QUrl, Signal
from PySide6.QtGui import QDesktopServices, QGuiApplication, QIcon
from PySide6.QtWidgets import (QButtonGroup, QCheckBox, QComboBox, QDialog, QDialogButtonBox,
                               QFrame, QGridLayout, QHBoxLayout, QInputDialog, QLabel, QLayout, QListWidget,
                               QListWidgetItem, QMessageBox, QProgressBar, QPushButton,
                               QScrollArea, QStackedWidget, QToolButton, QVBoxLayout, QWidget)

from ..ai.highlights import (CATEGORY_HELP, CATEGORY_HELP_COMMON, CLIP_DURATIONS, DEFAULT_CRITERIA, Candidate, Request,
                              criteria_request, summary)
from ..ai.lexicon import CATEGORIES
from ..core import AppContext
from ..edit import translation as trn
from ..edit.batch import BatchOptions, clip_timeline
from ..edit.model import Timeline
from ..media.ffmpeg import fmt_timecode
from . import theme
from .importing import DropZone, import_paths, pick_and_import
from .montage_player import MontagePlayer
from .scrub import ScrubNumber, ScrubTime, _tc
from .widgets import ElidedLabel, HistoryPopover, Popover, ToggleSwitch, button, label, tc_html, tool
from .widgets import ask

VIDEO_FILTER = "Vidéos (*.mp4 *.mov *.mkv *.webm *.avi *.m4v *.mts *.wmv)"


def _repolish(w: QWidget) -> None:
    w.style().unpolish(w)
    w.style().polish(w)


def _row(*items, spacing: int = 8) -> QHBoxLayout:
    h = QHBoxLayout()
    h.setContentsMargins(0, 0, 0, 0)
    h.setSpacing(spacing)
    for it in items:
        if it == "stretch":
            h.addStretch(1)
        elif isinstance(it, QLayout):
            h.addLayout(it)
        else:
            h.addWidget(it)
    return h


def _active_job(app: AppContext, kind: str, key: str, value) -> dict | None:
    """Tâche en file ou en cours de ce type pour ce média / projet (progression affichée dans l'écran)."""
    r = app.db.query_one(f"SELECT id, status, progress, message FROM job WHERE kind = ? AND status IN "
                         f"('pending','running') AND json_extract(params_json, '$.{key}') = ? ORDER BY id DESC "
                         "LIMIT 1", (kind, value))
    return dict(r) if r else None


def _last_job(app: AppContext, kind: str, key: str, value) -> dict | None:
    r = app.db.query_one(f"SELECT id, status, error, result_json FROM job WHERE kind = ? AND "
                         f"json_extract(params_json, '$.{key}') = ? ORDER BY id DESC LIMIT 1", (kind, value))
    if not r:
        return None
    d = dict(r)
    d["result"] = json.loads(d.pop("result_json") or "{}")
    return d


def _open_folder(path: str | Path) -> None:
    QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))


class _View(QWidget):
    """Base commune : projet ouvert dans le Studio (`host`), import, rafraîchissement sur les tâches."""
    open_editor = Signal()
    open_settings = Signal()

    def __init__(self, app: AppContext, host, parent=None):
        super().__init__(parent)
        self.app, self.host = app, host
        self._refresh_timer = QTimer(self, singleShot=True, interval=120)
        self._refresh_timer.timeout.connect(self.refresh)

    @property
    def pid(self) -> int | None:
        return self.host.project_id

    def import_extra(self) -> dict | None:
        return None

    def browse(self) -> None:
        if self.pid and pick_and_import(self.app, self, self.pid, self.import_extra(), VIDEO_FILTER):
            self.note("Import de la vidéo en cours…")

    def dropped(self, paths: list[str]) -> None:
        if self.pid and import_paths(self.app, self, paths, self.pid, self.import_extra()):
            self.note("Import de la vidéo en cours…")

    def note(self, text: str) -> None:
        pass

    def video(self) -> dict | None:
        """La vidéo du projet (la même dans tous les onglets)."""
        return self.host.project_video()

    def schedule_refresh(self) -> None:
        self._refresh_timer.start()

    def refresh(self) -> None:
        pass


# -- Clipping ---------------------------------------------------------------------------------------------
class CriteriaBox(QWidget):
    """Critères de recherche des extraits : catégories (aucune cochée = politique ; au survol, comment chacune
    est cherchée), durée, nombre. Petits boutons, le moins de place possible."""
    changed = Signal(dict)

    def __init__(self, full: bool = True, parent=None):
        super().__init__(parent)
        self._loading = False
        v = QVBoxLayout(self)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(8)
        self.cats: dict[str, QPushButton] = {}
        crow = []
        for k, name in CATEGORIES.items():
            b = button(name, "mini", tooltip=CATEGORY_HELP[k] + "\n\n" + CATEGORY_HELP_COMMON)
            b.setCheckable(True)
            b.toggled.connect(self._emit)
            crow.append(b)
            self.cats[k] = b
        v.addLayout(_row(*crow, "stretch", spacing=5))
        self.durs = QButtonGroup(self)
        self.durs.setExclusive(True)
        drow = []
        for k, (name, *_x) in CLIP_DURATIONS.items():
            b = button(name, "mini", tooltip="Durée des extraits")
            b.setCheckable(True)
            b.setProperty("dur", k)
            self.durs.addButton(b)
            drow.append(b)
        self.durs.buttonToggled.connect(lambda _b, on: on and self._emit())
        self.count = ScrubNumber(suffix=" extraits")
        self.count.setRange(1, 30)
        self.count.setFixedWidth(118)
        self.count.setToolTip("Nombre d'extraits proposés")
        self.count.edited.connect(self._emit)
        v.addLayout(_row(*drow, self.count, "stretch", spacing=5))

    def load(self, c: dict) -> None:
        self._loading = True
        try:
            for k, b in self.cats.items():
                b.setChecked(k in c["categories"])
            for b in self.durs.buttons():
                b.setChecked(b.property("dur") == c["duration"])
            self.count.setValue(int(c["count"]))
        finally:
            self._loading = False

    def value(self) -> dict:
        d = self.durs.checkedButton()
        return {"categories": [k for k, b in self.cats.items() if b.isChecked()],
                "duration": d.property("dur") if d else "moyen", "count": int(self.count.value()) or 8}

    def _emit(self, *_a) -> None:
        if not self._loading:
            self.changed.emit(self.value())


_Popover = Popover                                     # petit rectangle volant (`widgets.Popover`)


class CriteriaPopup(_Popover):
    """« Proposer d'autres extraits » : critères, durée, nombre, puis « Chercher »."""
    search = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.crit = CriteriaBox()
        self.body.addWidget(self.crit)
        self.go = button("Chercher", "primary", icon="sparkles", on_click=self._go)
        self.body.addLayout(_row("stretch", self.go))

    def _go(self) -> None:
        self.hide()
        self.search.emit()


CARD_H = 58                                  # hauteur fixe des cartes d'extraits
FULL = -1                                    # « Vidéo complète » sélectionnée


class MomentCard(QFrame):
    """Un extrait proposé, à hauteur fixe : case à cocher, titre en 3 à 8 mots, début de la transcription en gris,
    position et durée, vidéo déjà créée."""
    clicked = Signal(int)
    checked = Signal(int, bool)

    def __init__(self, cid: int, title: str, sub: str, right: str, made: str = "", checkable: bool = True,
                 parent=None):
        super().__init__(parent)
        self.cid = cid
        self.setProperty("role", "moment")
        self.setCursor(Qt.PointingHandCursor)
        self.setFixedHeight(CARD_H)
        h = QHBoxLayout(self)
        h.setContentsMargins(12, 6, 12, 6)
        h.setSpacing(10)
        self.box = QCheckBox()
        self.box.toggled.connect(lambda on: self.checked.emit(self.cid, on))
        if checkable:
            h.addWidget(self.box, 0, Qt.AlignVCenter)
        else:
            self.box.hide()
            icon_lb = QLabel()
            icon_lb.setPixmap(theme.icon("film", theme.color("muted"), 17).pixmap(17, 17))
            icon_lb.setFixedWidth(19)
            h.addWidget(icon_lb, 0, Qt.AlignVCenter)
        texts = QVBoxLayout()
        texts.setSpacing(1)
        self.title = ElidedLabel(title)
        self.title.setStyleSheet("font-size: 13px; font-weight: 700;")
        texts.addWidget(self.title)
        self.sub = ElidedLabel(sub, "muted")
        self.sub.setStyleSheet("font-size: 12px;")
        texts.addWidget(self.sub)
        h.addLayout(texts, 1)
        side = QVBoxLayout()
        side.setSpacing(2)
        side.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        pos = label(right, "faint")
        pos.setTextFormat(Qt.RichText)
        pos.setStyleSheet("font-size: 12px;")
        side.addWidget(pos, 0, Qt.AlignRight)
        if made:
            side.addWidget(label(made, "ready"), 0, Qt.AlignRight)
        h.addLayout(side)

    def set_selected(self, on: bool) -> None:
        self.setProperty("selected", "true" if on else "false")
        _repolish(self)

    def mouseReleaseEvent(self, e) -> None:
        if e.button() == Qt.LeftButton:
            self.clicked.emit(self.cid)
        super().mouseReleaseEvent(e)


class AddClipPopup(_Popover):
    """« + » sous la liste : un extrait choisi à la main, à partir d'un début (l'image de l'aperçu par défaut)."""
    add = Signal(float, float)                             # début, durée

    def __init__(self, parent=None):
        super().__init__(parent)
        self.body.addWidget(label("Début de l'extrait et sa durée", "muted", hint=False))
        self.start = ScrubTime(step=1.0)
        self.start.setToolTip("Début de l'extrait (l'image de l'aperçu par défaut)")
        self.length = ScrubNumber(suffix=" s", step=5)
        self.length.setRange(3, 600)
        self.length.setToolTip("Durée de l'extrait")
        self.body.addLayout(_row(self.start, self.length, spacing=8))
        self.go = button("Ajouter", "primary", icon="plus", on_click=self._go)
        self.body.addLayout(_row("stretch", self.go))

    def open(self, anchor: QWidget, start: float, length: float, total: float) -> None:
        self.start.setRange(0, max(0.0, total - 1))
        self.start.setValue(start)
        self.length.setValue(length)
        self.show_at(anchor)

    def _go(self) -> None:
        self.hide()
        self.add.emit(self.start.value(), float(self.length.value()))


class ClippingView(_View):
    """Clipping : vidéo → transcription (pendant laquelle on choisit ses critères) → extraits proposés → on
    coche → chaque extrait coché devient une vidéo (le premier, celle de ce projet ; les autres, un projet
    chacun), prête pour Traduction, Montage et Export (onglets du dock en bas)."""

    def __init__(self, app: AppContext, host, export_dir: Path, parent=None):
        super().__init__(app, host, parent)
        self.export_dir = export_dir
        self.sel: int | None = None
        self.cards: dict[int, MomentCard] = {}
        self.full_card: MomentCard | None = None
        self._tr: tuple[int, object] | None = None            # transcription en cache (média, transcription)
        root = QHBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # -- contenu (les projets sont dans la barre de gauche du Studio)
        main = QWidget()
        ml = QVBoxLayout(main)
        ml.setContentsMargins(6, 4, 0, 6)
        ml.setSpacing(12)
        # état du projet : une ligne (sans encadré), progression et action éventuelle
        self.banner = QWidget()
        bl = QHBoxLayout(self.banner)
        bl.setContentsMargins(2, 0, 0, 0)
        bl.setSpacing(12)
        bv = QVBoxLayout()
        bv.setSpacing(1)
        self.b_title = label("")
        self.b_title.setStyleSheet("font-size: 16px; font-weight: 700;")
        self.b_title.setWordWrap(True)
        self.b_desc = label("", "muted", hint=False)
        self.b_desc.setWordWrap(True)
        for w in (self.b_title, self.b_desc):
            bv.addWidget(w)
        bl.addLayout(bv, 1)
        self.b_progress = QProgressBar()
        self.b_progress.setRange(0, 100)
        self.b_progress.setFixedWidth(160)
        bl.addWidget(self.b_progress)
        self.b_action = button("", "primary")
        self.b_action.clicked.connect(lambda: self._banner_fn and self._banner_fn())
        bl.addWidget(self.b_action)
        self._banner_fn = None
        ml.addWidget(self.banner)

        self.body = QStackedWidget()
        ml.addWidget(self.body, 1)
        # page 0 : pas encore de vidéo — une zone de dépôt, petite, au centre
        dz = DropZone("Glisse ta vidéo ici")
        dz.dropped.connect(self.dropped)
        dz.setFixedSize(420, 200)
        drop_page = QWidget()
        dl = QVBoxLayout(drop_page)
        dl.addStretch(1)
        dl.addWidget(dz, 0, Qt.AlignHCenter)
        dl.addStretch(2)
        self.drop_zone = dz
        self.body.addWidget(drop_page)
        # page 1 : extraits + clip final
        work = QWidget()
        wl = QHBoxLayout(work)
        wl.setContentsMargins(0, 0, 0, 0)
        wl.setSpacing(34)                                  # de l'air entre les extraits et les outils
        wl.addStretch(1)                                   # les deux colonnes au centre, de l'espace sur les bords
        left = QVBoxLayout()
        left.setSpacing(8)
        self.full_slot = QVBoxLayout()                     # en haut : la vidéo complète
        self.full_slot.setContentsMargins(0, 0, 6, 4)
        left.addLayout(self.full_slot)
        self.count_lbl = label("Extraits", "h2")
        self.all_box = QCheckBox("Tout cocher")
        self.all_box.clicked.connect(self._check_all)
        left.addLayout(_row(self.count_lbl, "stretch", self.all_box))
        self.left_stack = QStackedWidget()
        # les extraits, puis « + »
        self.list_host = QWidget()
        self.list_lay = QVBoxLayout(self.list_host)
        self.list_lay.setContentsMargins(0, 0, 6, 0)
        self.list_lay.setSpacing(6)
        self.add_btn = tool("plus", "Ajouter un extrait : choisir son début (l'image de l'aperçu par défaut)",
                            self._add_clip, size=30)
        self.add_btn.setProperty("kind", "soft")
        self.list_lay.addLayout(_row(self.add_btn, "stretch"))
        self.list_lay.addStretch(1)
        sc = QScrollArea()
        sc.setWidgetResizable(True)
        sc.setFrameShape(QFrame.NoFrame)
        sc.setWidget(self.list_host)
        sc.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.left_stack.addWidget(sc)
        # pas encore d'extraits : les critères sont demandés (pendant la transcription)
        ask = QFrame()
        ask.setProperty("role", "card")
        al = QVBoxLayout(ask)
        al.setContentsMargins(22, 18, 22, 18)
        al.setSpacing(10)
        q = label("Sur quels critères chercher les extraits ?")
        q.setStyleSheet("font-size: 15px; font-weight: 700;")
        al.addWidget(q)
        self.ask_note = label("", "muted", wrap=True, hint=False)
        al.addWidget(self.ask_note)
        self.ask_crit = CriteriaBox(full=False)
        self.ask_crit.changed.connect(self._save_criteria)
        al.addWidget(self.ask_crit)
        self.ask_go = button("Chercher les extraits", "primary", icon="sparkles", on_click=self._find)
        al.addLayout(_row(self.ask_go, "stretch"))
        al.addStretch(1)
        self.left_stack.addWidget(ask)
        left.addWidget(self.left_stack, 1)
        self.more_btn = button("Proposer d'autres extraits", "ghost", icon="sparkles", on_click=self._more)
        left.addLayout(_row(self.more_btn, "stretch"))
        self.popup = CriteriaPopup(self)                  # critères, durée, nombre : dans la bulle volante
        self.crit = self.popup.crit
        self.crit.changed.connect(self._save_criteria)
        self.popup.search.connect(self._find)
        self.add_popup = AddClipPopup(self)
        self.add_popup.add.connect(self._add_clip_at)
        lw = QWidget()
        lw.setLayout(left)
        lw.setFixedWidth(460)                             # extraits moins larges
        wl.addWidget(lw)

        # colonne de droite : l'aperçu (le plus grand possible), début et fin, « Créer »
        right = QVBoxLayout()
        right.setContentsMargins(0, 0, 8, 0)
        right.setSpacing(8)
        self.player = MontagePlayer()
        self.player.note.setMaximumHeight(0)              # pas de note technique sous l'aperçu
        self.player.setMinimumHeight(380)
        self.player.fit_top = True                        # l'aperçu commence en haut de la colonne
        right.addWidget(self.player, 1)
        self.follow = tool("focus", "Suivre la personne qui parle : la vidéo est analysée et le cadre suit le "
                                    "visage de celui qui parle (vidéo horizontale mise en vertical). Désactivé : "
                                    "cadre centré.", checkable=True)
        self.follow.toggled.connect(self._toggle_follow)
        self.player.add_tool(self.follow)                 # à gauche des zones TikTok / Reels, même taille
        self.frame_note = label("", "faint", wrap=True, hint=False)
        right.addWidget(self.frame_note)
        self.edit_box = QWidget()                        # début et fin de l'extrait sélectionné
        self.in_s, self.out_s = ScrubTime(step=1.0), ScrubTime(step=1.0)     # + / − : une seconde
        self.in_s.edited.connect(lambda v: self._edit_times(start=v))
        self.out_s.edited.connect(lambda v: self._edit_times(end=v))
        self.in_s.setToolTip("Début de l'extrait")
        self.fin_lbl = label("Fin", "muted")
        tg = QGridLayout(self.edit_box)
        tg.setContentsMargins(0, 0, 0, 0)
        tg.setHorizontalSpacing(10)
        tg.setVerticalSpacing(3)
        tg.addWidget(label("Début", "muted"), 0, 0)
        tg.addWidget(self.fin_lbl, 0, 1)
        tg.addWidget(self.in_s, 1, 0)
        tg.addWidget(self.out_s, 1, 1)
        right.addWidget(self.edit_box)
        self.gen = button("Créer les vidéos", "primary", on_click=self._create)
        right.addLayout(_row("stretch", self.gen))
        right.addStretch(0)                               # la place en trop sous la colonne, jamais au-dessus
        rw = QWidget()
        rw.setLayout(right)
        rw.setMinimumWidth(320)
        rw.setMaximumWidth(500)                           # colonne d'outils plus étroite, l'aperçu en hauteur
        wl.addWidget(rw, 10)                              # s'élargit d'abord (jusqu'à 500), puis les bords
        wl.addStretch(1)
        self.body.addWidget(work)
        root.addWidget(main, 1)

    # -- données ------------------------------------------------------------------------
    def import_extra(self) -> dict:
        return {"auto_clips": True}          # transcription puis extraits, d'office

    def _data(self) -> dict:
        return self.app.projects.load_data(self.pid) if self.pid else {}

    def _made(self, data: dict) -> dict[int, int]:
        """Extraits déjà devenus une vidéo : {extrait: projet} (projets encore existants)."""
        return {int(k): v for k, v in (data.get("clip_projects") or {}).items() if self.app.projects.get(v)}

    def _criteria(self) -> dict:
        return {**DEFAULT_CRITERIA, **(self._data().get("clip_criteria") or {})}

    def _save_criteria(self, c: dict) -> None:
        if not self.pid:
            return
        data = self._data()
        data["clip_criteria"] = {**self._criteria(), **c}
        self.app.projects.save_data(self.pid, data)
        for box in (self.crit, self.ask_crit):
            if box is not self.sender():
                box.load(data["clip_criteria"])
        self._ask_text()

    def _title_of(self, row, data: dict | None = None) -> str:
        return (row["subject"] or "").strip() or (row["hook"] or "").strip() or "Extrait"

    def _rows(self):
        return [r for r in self.app.candidates.list(self.pid) if r["state"] != "rejected"] if self.pid else []

    def _transcript(self, aid: int):
        if self._tr is None or self._tr[0] != aid:
            self._tr = (aid, self.app.transcripts.get(aid) if self.app.transcripts.has(aid) else None)
        return self._tr[1]

    def _opening(self, r) -> str:
        """Le début de la transcription de l'extrait (ligne grise de sa carte)."""
        tr = self._transcript(r["source_asset_id"])
        if tr is None:
            return (r["hook"] or "").strip()
        words = " ".join(s.text.strip() for s in tr.segments if s.end > r["start_s"] + 0.05
                         and s.start < r["end_s"])
        return words[:220] or (r["hook"] or "").strip()          # aucune parole : son accroche

    # -- affichage ----------------------------------------------------------------------
    def refresh(self) -> None:
        if not self.pid:
            return
        self._tr = None
        v = self.video()
        self.body.setCurrentIndex(1 if v else 0)
        data = self._data()
        crit = {**DEFAULT_CRITERIA, **(data.get("clip_criteria") or {})}
        self.crit.load(crit)
        self.ask_crit.load(crit)
        self.follow.blockSignals(True)
        self.follow.setChecked(bool(crit["reframe"]))
        self.follow.blockSignals(False)
        made = self._made(data)
        rows = self._rows()
        self._fill_moments(rows, made, data)
        self._update_banner(v, rows, made, data)
        self._ask_text()
        self._auto_search(v, rows, data)

    def _clear_cards(self) -> None:
        for c in list(self.cards.values()) + ([self.full_card] if self.full_card else []):
            c.hide()
            c.setParent(None)                      # sinon son affichage différé en fait une fenêtre
            c.deleteLater()
        self.cards, self.full_card = {}, None

    def _fill_moments(self, rows, made: dict[int, int], data: dict) -> None:
        self._clear_cards()
        v = self.video()
        if v:                                      # en haut : la vidéo complète
            missing = " · fichier introuvable" if v.get("missing") else ""
            self.full_card = MomentCard(FULL, "Vidéo complète", v["file_name"] + missing,
                                        tc_html(v["duration_s"] or 0), checkable=False)
            self.full_card.clicked.connect(self._select)
            self.full_slot.addWidget(self.full_card)
        for r in rows:
            tag = ("Cette vidéo" if made.get(r["id"]) == self.pid else "Vidéo créée" if r["id"] in made
                   else "")
            dur = (r["end_s"] or 0) - (r["start_s"] or 0)
            card = MomentCard(r["id"], self._title_of(r, data), self._opening(r),
                              f"{tc_html(r['start_s'])} · {dur:.0f} s", tag)
            card.box.blockSignals(True)
            card.box.setChecked(r["state"] == "accepted")
            card.box.blockSignals(False)
            card.clicked.connect(self._select)
            card.checked.connect(self._check)
            self.list_lay.insertWidget(self.list_lay.count() - 2, card)     # avant « + »
            self.cards[r["id"]] = card
        self.left_stack.setCurrentIndex(0 if rows else 1)
        n_checked = sum(r["state"] == "accepted" for r in rows)
        todo = sum(r["state"] == "accepted" and r["id"] not in made for r in rows)
        self.count_lbl.setText(f"Extraits  {len(rows)}" if rows else "Extraits")
        self.all_box.setVisible(bool(rows))
        self.all_box.setChecked(bool(rows) and n_checked == len(rows))
        ready = made.get(data.get("clip_of")) == self.pid
        self.gen.setText(f"Créer {todo} vidéo(s)" if todo else "Coche des extraits")
        self.gen.setEnabled(bool(todo))
        self.gen.setVisible(bool(todo) or not ready)
        tr_ok = bool(v and self.app.transcripts.has(v["id"]))
        searching = self._searching()
        self.more_btn.setVisible(bool(rows))
        self.more_btn.setEnabled(tr_ok and not searching)
        self.ask_go.setEnabled(tr_ok and not searching)
        if self.sel != FULL and self.sel not in self.cards:
            self.sel = rows[0]["id"] if rows else None
        self._select(self.sel, keep=True)

    def _searching(self) -> bool:
        return bool(_active_job(self.app, "find_highlights", "project_id", self.pid)
                    or _active_job(self.app, "auto_clips", "project_id", self.pid))

    def _ask_text(self) -> None:
        cats = [c for c in self._criteria()["categories"] if c in CATEGORIES]
        v = self.video()
        tr_ok = bool(v and self.app.transcripts.has(v["id"]))
        when = ("Les extraits seront cherchés dès la fin de la transcription."
                if not tr_ok else "Lance la recherche quand tu es prêt.")
        self.ask_note.setText(when + ("" if cats else " Rien de coché : Politique."))

    def _auto_search(self, v, rows, data: dict) -> None:
        """Vidéo transcrite sans extraits ni recherche faite (importée dans un autre parcours, par exemple) :
        la recherche part d'elle-même, une fois, avec les critères choisis."""
        if not (v and self.isVisible() and not rows and self.app.transcripts.has(v["id"])):
            return
        if data.get("auto_searched") == v["id"] or self._searching():
            return
        if _last_job(self.app, "find_highlights", "project_id", self.pid):
            return
        data["auto_searched"] = v["id"]
        self.app.projects.save_data(self.pid, data)
        self._find()

    def _banner(self, title: str, desc: str = "", action: str = "", fn=None, step: int = 0,
                progress: float | None = None) -> None:
        self.b_title.setText(title)
        self.b_desc.setText(desc)
        self.b_desc.setVisible(bool(desc))
        self.b_action.setText(action)
        self.b_action.setVisible(bool(action and fn))
        self._banner_fn = fn
        self.b_progress.setVisible(progress is not None)
        if progress is not None:
            self.b_progress.setValue(int(progress * 100))
        # seulement s'il y a quelque chose à faire ou en cours : sinon, rien au-dessus de « Vidéo complète »
        self.banner.setVisible(progress is not None or bool(action and fn))

    def _update_banner(self, v, rows, made: dict[int, int], data: dict) -> None:
        from ..transcribe import engines
        if not v:
            return self._banner("")                         # la zone de dépôt, au centre, suffit
        aid = v["id"]
        if not self.app.transcripts.has(aid):
            for kind, key, val in (("auto_clips", "project_id", self.pid), ("transcribe", "asset_id", aid)):
                j = _active_job(self.app, kind, key, val)
                if j:
                    return self._banner("Transcription en cours…", j["message"] or "Pendant ce temps, choisis tes "
                                        "critères : les extraits seront cherchés ensuite.", step=1,
                                        progress=j["progress"])
            if not v.get("audio_tracks"):
                return self._banner("Cette vidéo n'a pas de son", "Pas de transcription possible : découpe tes "
                                    "extraits à la main avec « + » ou dans Montage libre.", "Montage libre",
                                    lambda: self.host.goto_tab.emit("free_edit"), 1)
            last = _last_job(self.app, "transcribe", "asset_id", aid)
            if last and last["status"] == "succeeded" and last["result"].get("segments") == 0:
                return self._banner("Aucune parole exploitable", "Aucun texte n'a été inventé. Tu peux découper "
                                    "tes extraits à la main avec « + ».", step=1)
            if not (engines.available() and engines.installed_models(self.app.paths.models)):
                return self._banner("Installe un modèle de transcription", "Il sert à repérer ce qui est dit. "
                                    "Une seule fois, puis tout se fait sur ce PC.", "Ouvrir les réglages",
                                    self.open_settings.emit, 1)
            return self._banner("Transcris la vidéo", "Puis les extraits seront proposés.", "Transcrire",
                                self._transcribe, 1)
        j = _active_job(self.app, "find_highlights", "project_id", self.pid) or \
            _active_job(self.app, "auto_clips", "project_id", self.pid)
        if j:
            return self._banner("Recherche des extraits…", j["message"] or "", step=2, progress=j["progress"])
        if not rows:
            return self._banner("")                         # la carte des critères, à gauche, suffit
        checked = [r for r in rows if r["state"] == "accepted"]
        todo = [r for r in checked if r["id"] not in made]
        if todo:
            return self._banner(f"Crée {len(todo)} vidéo(s)", "Le premier extrait devient cette vidéo, "
                                "les autres une vidéo chacun : format vertical, sous-titres et accroche.", step=2)
        if made.get(data.get("clip_of")) == self.pid:
            others = len([c for c, p in made.items() if p != self.pid])
            return self._banner("Ta vidéo est prête", "Elle passe telle quelle dans les autres onglets, en bas."
                                + (f" {others} autre(s) extrait(s) sont devenus des vidéos, à gauche." if others else ""),
                                step=3)
        return self._banner("Coche les extraits à garder", f"{len(rows)} extrait(s) proposé(s), du plus fort "
                            "au moins fort. Clique sur un extrait pour le voir.", step=2)

    # -- extrait sélectionné ------------------------------------------------------------
    def _select(self, cid: int | None, keep: bool = False) -> None:
        """Extrait (ou vidéo complète) affiché ; `keep` : rafraîchissement, l'aperçu n'est refait que s'il change.
        Un extrait choisi se lit depuis son début."""
        if not keep:
            self.player.rewind()
        self.sel = cid
        for k, c in self.cards.items():
            c.set_selected(k == cid)
        if self.full_card:
            self.full_card.set_selected(cid == FULL)
        if cid == FULL:
            self.edit_box.hide()
            self.frame_note.setText("")
            self._show_tl(self._full_tl(), keep)
            return
        r = self.app.candidates.get(cid) if cid else None
        self.edit_box.setVisible(r is not None)
        if r is None:
            self._show_tl(Timeline(), keep)
            self.frame_note.setText("")
            return
        v = self.host.assets.get(r["source_asset_id"]) or {}
        dur = float(v.get("duration_s") or 1e6)
        self.in_s.setRange(0, dur)
        self.out_s.setRange(0, dur)
        self.in_s.setValue(r["start_s"])
        self.out_s.setValue(r["end_s"])
        self.fin_lbl.setText(f"Fin · {r['end_s'] - r['start_s']:.0f} s")
        self._preview(r, keep)

    def _full_tl(self) -> Timeline:
        """La vidéo complète, au format du projet (aperçu de « Vidéo complète »)."""
        tl = Timeline()
        tl.set_aspect(self.host.tl.format.aspect, min(self.host.tl.format.width, self.host.tl.format.height))
        v = self.video()
        if v and (v["duration_s"] or 0) > 0.2:
            tl.add_segment(v["id"], 0.0, float(v["duration_s"]))
        return tl

    def _show_tl(self, tl: Timeline, keep: bool = False) -> None:
        """Aperçu : un rafraîchissement qui ne change rien ne touche pas au lecteur (la lecture continue)."""
        sig = json.dumps(tl.to_dict(), sort_keys=True)
        if keep and sig == getattr(self, "_shown", None):
            return
        self._shown = sig
        self.player.set_timeline(tl, self.host.assets)

    def _preview_sel(self) -> None:
        r = self.app.candidates.get(self.sel) if self.sel and self.sel != FULL else None
        if r is not None:
            self._preview(r)

    def _reframe_keys(self, r) -> list | None:
        f = (self._data().get("clip_reframe") or {}).get(str(r["id"])) or {}
        if f.get("in") == r["start_s"] and f.get("out") == r["end_s"]:
            return f.get("keys")
        return None

    def _clip_tl(self, r) -> Timeline:
        """La vidéo d'un extrait : passage choisi, format du projet, sous-titres, accroche, cadre."""
        aid = r["source_asset_id"]
        crit = self._criteria()
        tl = clip_timeline(self.host.tl, aid, r["start_s"], r["end_s"], self.host.tl.format.aspect,
                           BatchOptions(subtitles=True, hook_title=True, reframe=bool(crit["reframe"])),
                           self.app.transcripts.get(aid), r["hook"] or "", self.app.montage.text_style_for(self.pid))
        keys = self._reframe_keys(r) if tl.video.auto_reframe else None
        if keys:
            tl.segments[0].reframe = [list(k) for k in keys]
        return tl

    def _toggle_follow(self, on: bool) -> None:
        self._save_criteria({"reframe": bool(on)})
        r = self.app.candidates.get(self.sel) if self.sel and self.sel != FULL else None
        if r is not None:
            self._preview(r)
            self._sync_project(r)

    def _preview(self, r, keep: bool = False) -> None:
        aid = r["source_asset_id"]
        tl = self._clip_tl(r)
        a = self.host.assets.get(aid) or {}
        note = ""
        if tl.video.auto_reframe and (a.get("width") or 0) > (a.get("height") or 0) and not tl.segments[0].reframe:
            if not _active_job(self.app, "reframe", "candidate_id", r["id"]):
                self.app.jobs.submit("reframe", f"Cadrage · {self._title_of(r, self._data())}",
                                     {"asset_id": aid, "src_in": r["start_s"], "src_out": r["end_s"],
                                      "project_id": self.pid, "candidate_id": r["id"]})
            note = "Analyse des visages pour suivre la personne qui parle…"
        elif tl.video.auto_reframe and (a.get("width") or 0) <= (a.get("height") or 0):
            note = "Vidéo déjà au bon format : rien à recadrer."
        self.frame_note.setText(note)
        self.frame_note.setVisible(bool(note))
        self._show_tl(tl, keep)

    def _check(self, cid: int, on: bool) -> None:
        self.app.candidates.set_state(cid, "accepted" if on else "proposed")
        self.schedule_refresh()

    def _check_all(self, on: bool) -> None:
        for r in self._rows():
            self.app.candidates.set_state(r["id"], "accepted" if on else "proposed")
        self.schedule_refresh()

    def _edit_times(self, start: float | None = None, end: float | None = None) -> None:
        r = self.app.candidates.get(self.sel) if self.sel and self.sel != FULL else None
        if r is None:
            return
        a, b = r["start_s"] if start is None else start, r["end_s"] if end is None else end
        try:
            self.app.candidates.set_times(r["id"], a, b, json.loads(r["context_flags"] or "[]"))
        except ValueError as exc:
            QMessageBox.warning(self, "Extrait", str(exc))
            self.in_s.setValue(r["start_s"])
            self.out_s.setValue(r["end_s"])
            return
        self.fin_lbl.setText(f"Fin · {b - a:.0f} s")
        r = self.app.candidates.get(r["id"])
        self._preview(r)
        self._sync_project(r)
        self.player.seek(0 if end is None else max(0.0, (b - a) - 2.0))   # on voit l'endroit réglé
        self.schedule_refresh()

    def _sync_project(self, r) -> None:
        """Extrait devenu la vidéo de ce projet : la vidéo suit ses réglages (début, fin, accroche, cadre)."""
        if r is not None and self._data().get("clip_of") == r["id"]:
            self.host.replace_timeline(self._clip_tl(r))

    def _now(self) -> float:
        """Image affichée dans l'aperçu, en temps de la vidéo d'origine."""
        r = self.app.candidates.get(self.sel) if self.sel and self.sel != FULL else None
        return (r["start_s"] if r is not None else 0.0) + float(getattr(self.player, "t", 0.0) or 0.0)

    def _add_clip(self) -> None:
        """« + » : le petit rectangle du début et de la durée, à partir de l'image de l'aperçu."""
        v = self.video()
        if not v or not self.pid:
            return
        total = float(v["duration_s"] or 0)
        _n, _a, _b, target = CLIP_DURATIONS.get(self._criteria()["duration"], CLIP_DURATIONS["moyen"])
        self.add_popup.open(self.add_btn, min(self._now(), max(0.0, total - 1)), min(target, total) or target,
                            total)

    def _add_clip_at(self, a: float, length: float) -> None:
        """Extrait choisi à la main, coché d'office."""
        v = self.video()
        if not v or not self.pid:
            return
        total = float(v["duration_s"] or 0)
        b = min(total, a + length) if total else a + length
        if b - a < 1:
            QMessageBox.information(self, "Ajouter un extrait", "L'extrait est trop court.")
            return
        tr = self._transcript(v["id"])
        texts = [s.text for s in tr.segments if s.end > a and s.start < b] if tr else []
        cid = self.app.candidates.add(self.pid, v["id"], Candidate(
            start=a, end=b, score=0, category="", subject=summary(texts) if texts else f"Extrait à {_tc(a)}",
            rationale="Ajouté à la main", hook=texts[0].strip()[:160] if texts else ""))
        self.app.candidates.set_state(cid, "accepted")
        self.sel = cid
        self.refresh()

    def _transcribe(self) -> None:
        v = self.video()
        if v:
            self.app.jobs.submit("auto_clips", f"Extraits automatiques · {v['file_name']}",
                                 {"asset_id": v["id"], "project_id": self.pid})
            self.schedule_refresh()

    def _more(self) -> None:
        """« Proposer d'autres extraits » : les critères dans une bulle, puis « Chercher »."""
        self.crit.load(self._criteria())
        self.popup.show_at(self.more_btn)

    def _find(self) -> None:
        v = self.video()
        if not v or not self.app.transcripts.has(v["id"]) or self._searching():
            return
        llm = self.app.settings.get("llm", None) or {}
        eng = ({"kind": llm["kind"], "model": llm.get("model", ""), "base_url": llm.get("base_url", "")}
               if llm.get("kind") in ("ollama", "openai") and llm.get("model") else {"kind": "local"})
        req = criteria_request(self._criteria(), float(v["duration_s"] or 0))
        self.app.jobs.submit("find_highlights", f"Extraits · {v['file_name']}",
                             {"asset_id": v["id"], "project_id": self.pid, "request": req.__dict__, "engine": eng})
        self.schedule_refresh()

    def _create(self) -> None:
        """Chaque extrait coché devient une vidéo : le premier celle de ce projet (si elle n'est pas encore
        choisie), les autres un nouveau projet chacun, avec le même format et les mêmes réglages."""
        data = self._data()
        made = self._made(data)
        todo = [r for r in self._rows() if r["state"] == "accepted" and r["id"] not in made]
        if not todo:
            return
        self.host.flush()
        p = self.app.projects.get(self.pid)
        crit = self._criteria()
        here = data.get("clip_of") is None
        for k, r in enumerate(todo):
            tl = self._clip_tl(r)
            if k == 0 and here:
                self.host.replace_timeline(tl)
                data = self._data()
                data["clip_of"] = r["id"]
                made[r["id"]] = self.pid
                data["clip_projects"] = {str(c): v for c, v in made.items()}
                self.app.projects.save_data(self.pid, data)
                continue
            npid = self.app.projects.quick_create(self.app.concepts, "smart_clipping", self._title_of(r)[:60],
                                                  tl.format.aspect, concept_id=p["concept_id"])
            cid = self.app.candidates.replace_proposals(npid, r["source_asset_id"], [Candidate(
                start=r["start_s"], end=r["end_s"], score=r["score"] or 0, category=r["category"] or "",
                subject=r["subject"] or "", rationale=r["rationale"] or "", hook=r["hook"] or "")], Request())[0]
            self.app.candidates.set_state(cid, "accepted")
            nd = self.app.projects.load_data(npid)
            nd.update({"timeline": tl.to_dict(), "clip_of": cid, "clip_projects": {str(cid): npid},
                       "project_video": r["source_asset_id"], "auto_searched": r["source_asset_id"],
                       "clip_criteria": crit})
            keys = self._reframe_keys(r)
            if keys:
                nd["clip_reframe"] = {str(cid): {"in": r["start_s"], "out": r["end_s"], "keys": keys}}
            self.app.projects.save_data(npid, nd)
            made[r["id"]] = npid
        data = self._data()
        data["clip_projects"] = {str(c): v for c, v in made.items()}
        self.app.projects.save_data(self.pid, data)
        self.host.sidebar.refresh()                       # les nouveaux projets apparaissent à gauche
        self.refresh()

    def _rename(self) -> None:
        p = self.app.projects.get(self.pid) if self.pid else None
        if not p:
            return
        name, ok = QInputDialog.getText(self, "Renommer", "Nom de la vidéo :", text=p["name"])
        if ok and name.strip():
            self.app.projects.rename(self.pid, name.strip())
            self.schedule_refresh()

    def on_job(self, job) -> None:
        pid = job.params.get("project_id")
        if job.kind == "reframe" and job.params.get("candidate_id") and pid and self.app.projects.get(pid):
            if job.status == "succeeded":            # cadre calculé : gardé pour l'aperçu et l'export
                data = self.app.projects.load_data(pid)
                data.setdefault("clip_reframe", {})[str(job.params["candidate_id"])] = {
                    "in": job.params["src_in"], "out": job.params["src_out"], "keys": (job.result or {})["keys"]}
                self.app.projects.save_data(pid, data)
            if pid == self.pid and job.params["candidate_id"] == self.sel:
                if job.status == "failed":
                    self.frame_note.setText("Suivi des visages indisponible : " +
                                            ((job.error or "").strip().splitlines() or [""])[-1][:160])
                elif job.status == "succeeded":
                    self._preview_sel()
                    self._sync_project(self.app.candidates.get(self.sel))
            return
        if not self.pid or not self.isVisible():
            return
        if job.kind in ("import_file", "import_url", "transcribe", "auto_clips", "find_highlights"):
            if job.status in ("pending", "running"):
                v = self.video()
                if self.b_progress.isVisible() and v:      # progression seule, sans tout reconstruire
                    self.b_progress.setValue(int((job.progress or 0) * 100))
                    if job.message:
                        self.b_desc.setText(job.message)
                        self.b_desc.show()
                    return
            self.schedule_refresh()


# -- Traduction -------------------------------------------------------------------------------------------
LANGS = [("fr", "Français"), ("en", "Anglais"), ("de", "Allemand"), ("es", "Espagnol"), ("pt", "Portugais (BR)"),
         ("it", "Italien"), ("ja", "Japonais"), ("ru", "Russe"), ("ko", "Coréen"), ("ar", "Arabe"), ("tr", "Turc"),
         ("pl", "Polonais"), ("nl", "Néerlandais"), ("id", "Indonésien"), ("hi", "Hindi"), ("zh", "Chinois")]


class VoicePreview(QWidget):
    """Écoute d'une voix : l'extrait publié par le service (ElevenLabs, OpenAI, Piper), 5 secondes au plus ;
    rien n'est généré."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.hide()
        self.player = None
        self.timer = QTimer(self, singleShot=True, interval=5000)
        self.timer.timeout.connect(self.stop)

    def play(self, url: str) -> bool:
        from .montage_player import MULTIMEDIA
        if not url or not MULTIMEDIA or QGuiApplication.platformName() in ("offscreen", "minimal"):
            return False
        if self.player is None:
            from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer
            self.player, self.audio = QMediaPlayer(self), QAudioOutput(self)
            self.player.setAudioOutput(self.audio)
        self.player.stop()
        self.player.setSource(QUrl(url))
        self.player.play()
        self.timer.start()
        return True

    def stop(self) -> None:
        self.timer.stop()
        if self.player is not None:
            self.player.stop()


def voice_item(lst: QListWidget, text: str, data, preview: str = "", on_play=None) -> QListWidgetItem:
    """Une voix de la liste : son nom et, devant, le bouton qui fait écouter son extrait."""
    it = QListWidgetItem()
    it.setData(Qt.UserRole, data)
    it.setToolTip(text)
    w = QWidget()
    h = QHBoxLayout(w)
    h.setContentsMargins(4, 2, 4, 2)
    h.setSpacing(6)
    b = tool("play", "Écouter un extrait de 5 s" if preview else "Pas d'extrait à écouter pour cette voix",
             (lambda: on_play(preview)) if preview and on_play else None, size=26, icon_px=14)
    b.setEnabled(bool(preview))
    if data is None:
        b.setVisible(False)
    h.addWidget(b)
    h.addWidget(ElidedLabel(text), 1)
    w.play_btn = b
    lst.addItem(it)
    it.setSizeHint(QSize(100, 32))
    lst.setItemWidget(it, w)
    return it


class VoiceDialog(QDialog):
    """Choix de la voix du doublage (ElevenLabs, OpenAI, Piper), le doublage automatique d'ElevenLabs (à partir du
    son de la vidéo), ou aucune voix (sous-titres seulement). Chaque voix s'écoute (bouton lecture)."""

    def __init__(self, app: AppContext, current: dict | None, parent=None):
        super().__init__(parent)
        from ..ai import tts
        from ..services import secrets
        self.app, self.choice = app, current
        self.setWindowTitle("Voix du doublage")
        self.resize(460, 480)
        lay = QVBoxLayout(self)
        self.engine = QComboBox()
        has_key = bool(secrets.get_secret("elevenlabs_api_key"))
        self.engine.addItem("ElevenLabs" + ("" if has_key else " (clé à saisir : icône réglages)"), "elevenlabs")
        self.engine.addItem("ElevenLabs · doublage à partir du son de la vidéo"
                            + ("" if has_key else " (clé à saisir)"), "eleven_dub")
        llm = app.settings.get("llm", None) or {}
        if llm.get("kind") == "openai" and secrets.get_secret("llm_api_key"):
            self.engine.addItem("OpenAI (même clé que le modèle de langage)", "openai")
        try:
            tts.PiperTTS(app.paths.models)
            self.engine.addItem("Piper (hors ligne, gratuit : une voix par langue)", "piper")
        except tts.TTSError:
            pass
        if current and current.get("engine"):
            self.engine.setCurrentIndex(max(0, self.engine.findData(current["engine"])))
        self.engine.currentIndexChanged.connect(self._load)
        lay.addLayout(_row(label("Moteur", "muted"), self.engine, "stretch"))
        self.list = QListWidget()
        self.list.itemDoubleClicked.connect(lambda _it: self.accept())
        self.preview = VoicePreview(self)
        lay.addWidget(self.list, 1)
        self.status = label("", "faint", wrap=True, hint=False)
        lay.addWidget(self.status)
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.button(QDialogButtonBox.Ok).setText("Choisir")
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)
        QTimer.singleShot(0, self._load)

    def _load(self) -> None:
        from ..ai import tts
        from ..services import secrets
        self.preview.stop()
        self.list.clear()
        none = voice_item(self.list, "Aucune voix : garder la voix d'origine, sous-titres traduits", None)
        self.list.setCurrentItem(none)
        if self.engine.currentData() == "eleven_dub":
            it = voice_item(self.list, "Doublage ElevenLabs : la voix de la vidéo reproduite dans chaque langue",
                            {"engine": "eleven_dub", "id": "auto", "name": "Doublage ElevenLabs"})
            self.list.setCurrentItem(it)
            self.status.setText("ElevenLabs reçoit le son de la vidéo, le transcrit, le traduit et refait la voix "
                                "(voix d'origine reproduite, fond sonore gardé) : la transcription du logiciel n'est "
                                "pas utilisée pour la voix. Les sous-titres restent ceux de la traduction du logiciel. "
                                "Décompté du crédit ElevenLabs.")
            return
        QGuiApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            voices = tts.make_engine(self.engine.currentData(), secrets.get_secret, self.app.paths.models,
                                     self.app.settings.get("llm", None) or {}).voices()
        except tts.TTSError as exc:
            self.status.setText(str(exc))
            return
        finally:
            QGuiApplication.restoreOverrideCursor()
        from ..ai.translate import LANG_NAMES
        for v in voices:
            lang = f" · {LANG_NAMES.get(v.language, v.language)}" if v.language else ""
            it = voice_item(self.list, v.name + lang + (f" · {v.category}" if v.category else ""),
                            {"engine": self.engine.currentData(), "id": v.id, "name": v.name}, v.preview,
                            self.preview.play)
            if self.choice and self.choice.get("id") == v.id:
                self.list.setCurrentItem(it)
        note = {"elevenlabs": "Le texte traduit est envoyé à ElevenLabs et consomme tes crédits ; la voix parle "
                              "toutes les langues.",
                "openai": "Le texte traduit est envoyé à OpenAI (payant à l'usage) ; la voix parle toutes les "
                          "langues.",
                "piper": "Hors ligne : pour chaque langue, une voix de cette langue est prise (à télécharger dans "
                         "Réglages si elle manque)."}.get(self.engine.currentData(), "")
        self.status.setText(f"{len(voices)} voix disponibles. {note}")

    def accept(self) -> None:
        it = self.list.currentItem()
        self.choice = it.data(Qt.UserRole) if it else None
        super().accept()

    def done(self, r: int) -> None:
        self.preview.stop()                               # l'extrait s'arrête avec la fenêtre
        super().done(r)


FLAG_DIR = Path(__file__).parent / "flags"
# langue → pays du drapeau (affiché au survol)
COUNTRIES = {"en": "Royaume-Uni", "de": "Allemagne", "es": "Espagne", "pt": "Brésil", "it": "Italie", "ja": "Japon",
             "ru": "Russie", "ko": "Corée du Sud", "ar": "Arabie saoudite", "tr": "Turquie", "pl": "Pologne",
             "nl": "Pays-Bas", "id": "Indonésie", "hi": "Inde", "fr": "France", "zh": "Chine"}
# langue → code du pays (nom du fichier du drapeau)
FLAG_OF = {"en": "gb", "pt": "br", "ja": "jp", "ko": "kr", "ar": "sa", "hi": "in", "zh": "cn"}


def flag_icon(code: str) -> QIcon:
    """Drapeau d'une langue, emoji en image (Noto 3D, `ui/flags/<pays>.png`) : Windows n'affiche pas les
    drapeaux emoji (il écrit « FR »)."""
    p = FLAG_DIR / f"{FLAG_OF.get(code, code)}.png"
    return QIcon(str(p)) if p.exists() else QIcon()


class ToolRow(QWidget):
    """Un outil de la traduction : nom, interrupteur, moteur utilisé, coche verte (opérationnel) ou rouge, roue
    crantée (alternatives, réglages, autres modèles)."""

    def __init__(self, name: str, question: str, on_engine=None, on_settings=None, settings_tip: str = "",
                 parent=None):
        super().__init__(parent)
        h = QHBoxLayout(self)
        h.setContentsMargins(0, 2, 0, 2)
        h.setSpacing(10)
        self.name = label(name, wrap=True, hint=False)     # un nom long passe à la ligne, l'interrupteur reste
        self.name.setStyleSheet("font-weight: 700;")
        self.state = QLabel()                               # prêt ou non : juste à droite du nom
        self.state.setFixedSize(18, 18)
        head = QFrame()
        head.setProperty("role", "dim")                     # sans fond propre, sur la carte
        head.setFixedWidth(136)                             # les interrupteurs restent alignés
        hh = QHBoxLayout(head)
        hh.setContentsMargins(0, 0, 0, 0)
        hh.setSpacing(6)
        hh.addWidget(self.name)
        hh.addWidget(self.state)
        hh.addStretch(1)
        h.addWidget(head)
        self.switch = ToggleSwitch(question)
        h.addWidget(self.switch)
        h.addSpacing(6)
        self.engine = QPushButton()
        self.engine.setProperty("kind", "link")
        self.engine.setCursor(Qt.PointingHandCursor)
        self.engine.setMaximumWidth(260)
        if on_engine:
            self.engine.clicked.connect(on_engine)
        h.addWidget(self.engine)
        h.addStretch(1)
        self.gear = tool("settings", settings_tip, on_settings) if on_settings else None
        if self.gear:
            h.addWidget(self.gear)

    def set_engine(self, text: str, tip: str = "") -> None:
        fm = self.engine.fontMetrics()
        self.engine.setText(fm.elidedText(text, Qt.ElideRight, 240))
        self.engine.setToolTip(tip or text)

    def set_ok(self, ok: bool, why: str = "") -> None:
        """Coche verte : l'outil est prêt ; rouge : il ne peut pas marcher (clé, voix manquante…), raison au survol."""
        col = theme.color("ok") if ok else theme.color("danger")
        self.state.setPixmap(theme.icon("circle-check" if ok else "circle-x", col, 18).pixmap(18, 18))
        self.state.setToolTip(why or ("Opérationnel" if ok else "Pas opérationnel"))
        self.ok = ok

    def set_active(self, on: bool) -> None:
        for w in (self.engine, self.state):
            w.setEnabled(on)


class TranslationView(_View):
    """Traduction en un écran : langues (drapeaux), outils (voix, sous-titres) chacun avec son interrupteur, son
    moteur et son état, puis « Préparer » : une version par langue, rangée sous le projet."""

    def __init__(self, app: AppContext, host, export_dir: Path, parent=None):
        super().__init__(app, host, parent)
        self.export_dir = export_dir
        self.job_id: int | None = None
        self._loading = False
        self._preview_timer = QTimer(self, singleShot=True, interval=250)
        self._preview_timer.timeout.connect(self._preview)
        root = QVBoxLayout(self)
        root.setContentsMargins(10, 8, 0, 10)
        root.setSpacing(14)
        body = QHBoxLayout()
        body.setSpacing(28)
        # à gauche : l'aperçu (les trois quarts de la hauteur), puis le nom du fichier importé
        lcol = QVBoxLayout()
        lcol.setSpacing(8)
        self.left = QStackedWidget()
        dz = DropZone("Glisse ta vidéo ici\nou importe-la en haut")
        dz.dropped.connect(self.dropped)
        self.left.addWidget(dz)
        self.player = MontagePlayer()
        self.player.note.setMaximumHeight(0)
        self.player.track_resolver = lambda aid: self.host._tracks_for(aid, self.player.tl)   # voix doublée
        self.left.addWidget(self.player)
        self.left.setMinimumWidth(320)
        lcol.addWidget(self.left, 3)
        self.video_lbl = label("", "muted", wrap=True, hint=False)     # plus affiché sous l'aperçu
        self.video_lbl.hide()
        lcol.addStretch(1)
        body.addLayout(lcol, 2)

        card = QFrame()
        card.setProperty("role", "card")
        cl = QVBoxLayout(card)
        cl.setContentsMargins(24, 20, 24, 18)
        cl.setSpacing(10)
        grid = QGridLayout()
        grid.setHorizontalSpacing(6)
        grid.setVerticalSpacing(6)
        self.chips: dict[str, QToolButton] = {}
        for i, (code, nm) in enumerate(LANGS):
            b = QToolButton()
            b.setProperty("role", "flag")
            b.setCheckable(True)
            b.setIcon(flag_icon(code))
            b.setIconSize(QSize(20, 20))
            b.setCursor(Qt.PointingHandCursor)
            b.setToolTip(f"{COUNTRIES.get(code, nm)} · {nm.lower()}")
            b.toggled.connect(self._changed)
            grid.addWidget(b, i // 8, i % 8)
            self.chips[code] = b
        cl.addLayout(_row(grid, "stretch"))
        cl.addSpacing(8)
        self.voice: dict | None = None
        self.voice_row = ToolRow("Voix", "Voulez-vous traduire la voix ? (doublage dans chaque langue)",
                                 on_engine=lambda: self._pick_voice(), on_settings=self._voices_dialog,
                                 settings_tip="Voix : moteurs, clés et voix hors ligne")
        self.do_voice = self.voice_row.switch
        self.do_voice.toggled.connect(self._voice_toggled)
        self.subs_row = ToolRow("Sous-titres", "Voulez-vous des sous-titres traduits ?",
                                on_engine=self._engines_dialog, on_settings=self._engines_dialog,
                                settings_tip="Traduction : moteurs, clés et modèle de langage")
        self.do_subs = self.subs_row.switch
        self.do_subs.toggled.connect(self._changed)
        self.translate_btn = button("Traduire", "mini", on_click=self._translate_now,
                                    tooltip="Traduire le texte maintenant dans les langues cochées, pour le relire "
                                    "et le corriger avant de préparer la voix")
        sl = self.subs_row.layout()
        sl.insertWidget(sl.indexOf(self.subs_row.engine) + 1, self.translate_btn)   # juste après le moteur
        self.subs_hist = tool("history", "Historique des traductions", lambda: self._history("translation"))
        sl.insertWidget(sl.indexOf(self.subs_row.gear), self.subs_hist)
        vl = self.voice_row.layout()
        self.voice_hist = tool("history", "Historique des voix générées", lambda: self._history("dub"))
        vl.insertWidget(vl.indexOf(self.voice_row.gear), self.voice_hist)
        cl.addWidget(self.subs_row)
        self.tr_jobs: set[int] = set()
        self._tr_note = ""                                # où en est « Traduire » (gardé après un rafraîchissement)
        self.presets = QButtonGroup(self)
        self.presets.setExclusive(True)
        prow = []
        from .montage_panel import preset_pixmap
        for key, (nm, sty) in trn.SUB_PRESETS.items():
            b = QToolButton()                             # comme l'onglet Textes : le « A » tel qu'à l'écran
            pm = preset_pixmap({**sty, "bold": True, "size": 72}, "A", 26)
            b.setIcon(QIcon(pm))
            b.setIconSize(QSize(30, 26))
            b.setFixedSize(38, 32)
            b.setProperty("role", "flag")
            b.setCursor(Qt.PointingHandCursor)
            b.setToolTip(f"Sous-titres : {nm.lower()}")
            b.setCheckable(True)
            b.setProperty("preset", key)
            self.presets.addButton(b)
            prow.append(b)
        self.presets.buttonToggled.connect(lambda _b, on: on and self._changed())
        pr = _row(*prow, "stretch", spacing=5)
        pr.setContentsMargins(146, 0, 0, 0)
        prw = QWidget()                                   # styles de sous-titres : le premier, toujours
        prw.setLayout(pr)                                 # (pour le changer : l'onglet Textes)
        prw.hide()
        cl.addWidget(prw)
        # textes traduits : un bouton par langue, pour relire et corriger la traduction
        self.tr_edit = QHBoxLayout()
        self.tr_edit.setContentsMargins(146, 0, 0, 0)
        self.tr_edit.setSpacing(5)
        cl.addLayout(self.tr_edit)
        cl.addWidget(self.voice_row)
        self.music_row = ToolRow("Musique", "Avec une voix : la musique et l'ambiance d'origine sont séparées de "
                                 "la voix et gardées sous le doublage.")
        self.keep_music = self.music_row.switch
        self.music_row.engine.hide()
        self.music_row.state.hide()                       # pas d'état : toute la place au nom
        self.keep_music.toggled.connect(self._changed)
        cl.addWidget(self.music_row)
        self.erase_row = ToolRow("Effacer les textes d'origine", "Effacer les textes incrustés d'origine (jamais "
                                 "parfait : vérifie la vidéo).")
        self.erase = self.erase_row.switch
        self.erase_row.engine.hide()
        self.erase_row.state.hide()                       # pas d'état : toute la place au nom
        self.erase.toggled.connect(self._changed)
        cl.addWidget(self.erase_row)
        cl.addSpacing(6)
        self.go = button("Préparer", "primary", on_click=self._go)
        self.go.setMinimumHeight(40)
        cl.addWidget(self.go)
        self.progress = QProgressBar()
        self.progress.setRange(0, 100)
        self.progress.hide()
        cl.addWidget(self.progress)
        # ce que fait le logiciel (en cours), puis le résultat de chaque outil
        self.task = label("", hint=False, wrap=True)
        self.task.setStyleSheet("font-weight: 600;")
        cl.addWidget(self.task)
        self.task_detail = label("", "muted", wrap=True, hint=False)
        cl.addWidget(self.task_detail)
        self.status = QLabel()
        self.status.setTextFormat(Qt.RichText)
        self.status.setWordWrap(True)
        self.status.setTextInteractionFlags(Qt.TextSelectableByMouse)
        cl.addWidget(self.status)
        holder = QWidget()                                # la carte garde sa hauteur, sans vide sous le bouton
        hl = QVBoxLayout(holder)
        hl.setContentsMargins(0, 0, 0, 0)
        hl.addWidget(card)
        hl.addStretch(1)
        sc = QScrollArea()
        sc.setWidgetResizable(True)
        sc.setFrameShape(QFrame.NoFrame)
        sc.setWidget(holder)
        body.addWidget(sc, 3)
        root.addLayout(body, 1)

    # -- état enregistré dans le projet ---------------------------------------------------
    def _state(self) -> dict:
        st = {"langs": ["en"], "voice": None, "preset": "white", "keep_music": True, "erase": False,
              "do_voice": None, "do_subs": True}
        st.update(self.app.settings.get("translate_defaults", None) or {})
        if self.pid:
            st.update(self.app.projects.load_data(self.pid).get("translate_ui") or {})
        return st

    def _changed(self, *_a) -> None:
        if self._loading or not self.pid:
            return
        st = {"langs": [c for c, b in self.chips.items() if b.isChecked()], "voice": self.voice,
              "do_voice": self.do_voice.isChecked(), "do_subs": self.do_subs.isChecked(),
              "preset": next(iter(trn.SUB_PRESETS)),     # le style par défaut ; il se règle dans Textes
              "keep_music": self.keep_music.isChecked(), "erase": self.erase.isChecked()}
        data = self.app.projects.load_data(self.pid)
        data["translate_ui"] = st
        self.app.projects.save_data(self.pid, data)
        self.app.settings.set("translate_defaults", {k: st[k] for k in ("langs", "voice", "preset", "keep_music",
                                                                         "erase", "do_voice", "do_subs")})
        self._enable_tools()
        self._update_go()
        self._preview_timer.start()

    def _enable_tools(self) -> None:
        """Outils éteints : leur moteur et leur état sont grisés (et ne s'appliquent pas)."""
        self.voice_row.set_active(self.do_voice.isChecked())
        self.subs_row.set_active(self.do_subs.isChecked())
        self.music_row.setEnabled(self.do_voice.isChecked())
        for b in self.presets.buttons():
            b.setEnabled(self.do_subs.isChecked())
        self._tool_states()

    def _engine(self) -> str:
        return self.app.settings.get("translate_engine", None) or "auto"

    def _tool_states(self) -> None:
        """Moteur utilisé par chaque outil, et s'il peut marcher (coche verte ou rouge)."""
        from ..ai import translate as trm
        from .engines import translation_choices
        choices = dict(translation_choices(self.app))
        eng = self._engine()
        name = choices.get(eng) or trm.ENGINES.get(eng, (eng,))[0]
        ready = eng in choices and (eng != "auto" or len(choices) > 1)
        if self.voice and self.voice.get("engine") == "eleven_dub" and self.do_voice.isChecked():
            # doublage ElevenLabs : ses sous-titres (sa traduction) servent ; l'autre moteur seulement en secours
            name, ready = "ElevenLabs (avec le doublage)", True
        self.subs_row.set_engine(name.replace(" (recommandé)", ""), "Moteur de traduction (aussi pour la voix) : "
                                 "clic pour en choisir un autre. Avec le doublage ElevenLabs, les sous-titres sont "
                                 "ceux d'ElevenLabs.")
        self.subs_row.set_ok(ready, "Prêt" if ready else "Ce moteur n'est pas réglé : roue crantée")
        ok, why = self._voice_ready()
        self.voice_row.set_engine(self.voice["name"] if self.voice else "Choisir une voix…",
                                  "Clic pour choisir une autre voix")
        self.voice_row.set_ok(ok, why)

    def _voice_ready(self) -> tuple[bool, str]:
        """La voix choisie peut-elle doubler toutes les langues cochées ?"""
        from ..ai import piper
        from ..ai.translate import LANG_NAMES
        from ..services import secrets
        v = self.voice
        if not v:
            return False, "Aucune voix choisie : clic sur « Choisir une voix… »"
        eng = v.get("engine")
        if eng in ("elevenlabs", "eleven_dub"):
            key = secrets.get_secret("elevenlabs_api_key") or ""
            if not key.startswith("sk_"):
                return False, "Clé ElevenLabs manquante ou à corriger : roue crantée"
        elif eng == "openai":
            llm = self.app.settings.get("llm", None) or {}
            if not (llm.get("kind") == "openai" and secrets.get_secret("llm_api_key")):
                return False, "Clé OpenAI manquante : roue crantée"
        elif eng == "piper":
            have = {x.split("_")[0] for x in piper.installed_voices(self.app.paths.models)}
            if piper.exe_path(self.app.paths.models) is None:
                return False, "Moteur de voix hors ligne non installé : roue crantée"
            miss = [lg for lg in self._langs() if lg not in have]
            if miss:
                return False, ("Pas de voix hors ligne en " + ", ".join(LANG_NAMES.get(m, m) for m in miss)
                               + " : à télécharger (roue crantée)")
        return True, "Prêt"

    def _voice_toggled(self, on: bool) -> None:
        """Voix allumée sans voix choisie : la liste des voix s'ouvre ; rien de choisi, l'interrupteur revient."""
        if on and not self._loading and not self.voice:
            self._pick_voice()
            if not self.voice:
                self.do_voice.setChecked(False)
                return
        self._changed()

    def _engines_dialog(self) -> None:
        from .engines import TranslationEnginesDialog
        dlg = TranslationEnginesDialog(self.app, self)
        dlg.changed.connect(self.schedule_refresh)
        dlg.exec()
        self.refresh()

    def _voices_dialog(self) -> None:
        from .engines import VoiceEnginesDialog
        langs = self._langs()
        dlg = VoiceEnginesDialog(self.app, self, (self.voice or {}).get("engine"), langs[0] if langs else "fr")
        dlg.exec()
        self.refresh()
        if dlg.chosen and dlg.chosen != (self.voice or {}).get("engine"):
            self._pick_voice({"engine": dlg.chosen})          # autre moteur : choisir sa voix

    def refresh(self) -> None:
        if not self.pid:
            return
        self._loading = True
        try:
            st = self._state()
            for c, b in self.chips.items():
                b.setChecked(c in st["langs"])
            self.voice = st["voice"]
            self.do_voice.setChecked(bool(self.voice) if st["do_voice"] is None else bool(st["do_voice"]))
            self.do_subs.setChecked(bool(st["do_subs"]))
            for b in self.presets.buttons():
                b.setChecked(b.property("preset") == next(iter(trn.SUB_PRESETS)))
            self.keep_music.setChecked(bool(st["keep_music"]))
            self.erase.setChecked(bool(st["erase"]))
            v = self.video()
            self.left.setCurrentIndex(1 if v else 0)
            src = self.app.transcripts.get(v["id"]) if v and self.app.transcripts.has(v["id"]) else None
            for c, b in self.chips.items():
                same = bool(src and src.language == c)
                b.setEnabled(not same)
                if same:
                    b.setToolTip(f"{COUNTRIES.get(c, c)} · langue d'origine de la vidéo")
            self._update_video_line(v, src)
            self._fill_tr_edit(v)
            last = _last_job(self.app, "translate_video", "project_id", self.pid)
            running = _active_job(self.app, "translate_video", "project_id", self.pid)
            self.job_id = running["id"] if running else None
            self.progress.setVisible(running is not None)
            ready = (self.app.projects.load_data(self.pid).get("translations") or {}).get("ready") or []
            if running:
                self._show_task(running["message"] or "", running["progress"] or 0)
                self.status.setText("")
            else:
                self.task.setText("")
                self.task_detail.setText("")
                self.task.setVisible(False)
                self.task_detail.setVisible(False)
                if self._tr_note:
                    self.note(self._tr_note)
                self.status.setText(self._report(last, ready) if (last or ready) else
                                    ("" if v else "Ajoute une vidéo pour commencer."))
            self._ensure_versions(ready)
        finally:
            self._loading = False
        self._enable_tools()
        self._update_go()
        self._preview()

    def _show_task(self, message: str, progress: float) -> None:
        """Ce que fait le logiciel : l'étape en clair (langue, outil), puis le détail."""
        from ..ai.translate import LANG_NAMES
        names = {c.upper(): LANG_NAMES.get(c, c).capitalize() for c, _n in LANGS}
        parts = [p.strip() for p in (message or "").split("·") if p.strip()]
        step = {"voix": "doublage de la voix", "traduction du texte": "traduction des sous-titres",
                "Transcription": "transcription", "Séparation voix / musique": "séparation voix / musique",
                "Textes d'origine": "repérage des textes d'origine", "export": "export"}
        lang = names[parts.pop(0)] if parts and parts[0] in names else None
        what = step[parts.pop(0)] if parts and parts[0] in step else None
        title = " — ".join(x for x in (lang, what) if x) or "Préparation"
        self.task.setText(f"{title[0].upper()}{title[1:]} · {int(progress * 100)} %")
        self.task_detail.setText(" · ".join(parts))
        self.task.setVisible(True)
        self.task_detail.setVisible(bool(self.task_detail.text()))
        self.progress.setValue(int(progress * 100))

    def _report(self, last, ready: list[str]) -> str:
        """Résultat, outil par outil : ce qui a marché (vert), ce qui a échoué (rouge) et pourquoi."""
        from html import escape
        from ..ai.translate import LANG_NAMES
        ok_c, ko_c, mute = theme.color("ok"), theme.color("danger"), theme.color("muted")

        def nm(c: str) -> str:
            return LANG_NAMES.get(c, c).capitalize()
        if last and last["status"] == "failed":
            why = ((last["error"] or "").strip().splitlines() or ["échec"])[-1][:300]
            return f"<span style='color:{ko_c}'>✗ Rien n'a été préparé :</span> {escape(why)}"
        if last and last["status"] == "cancelled":
            return f"<span style='color:{mute}'>Préparation annulée.</span>"
        res = (last or {}).get("result") or {}
        status = res.get("status")
        if not status:                                      # préparé par une version plus ancienne
            status = {lg: {"subtitles": "ok", "voice": "ok" if res.get("dubbed") else "off"} for lg in ready}
        out = []
        for tool_key, title in (("subtitles", "Sous-titres traduits"), ("voice", "Voix traduite")):
            good = [lg for lg, s in status.items() if s.get(tool_key) == "ok"]
            bad = [(lg, s[tool_key]) for lg, s in status.items() if s.get(tool_key) not in ("ok", "off", None)]
            if not good and not bad:
                out.append(f"<b>{title}</b> <span style='color:{mute}'>· non demandé</span>")
                continue
            lines = [f"<b>{title}</b>"]
            if good:
                miss = [f"{nm(lg)} : {status[lg]['voice_missing']} phrase(s) sans voix" for lg in good
                        if tool_key == "voice" and status[lg].get("voice_missing")]
                lines.append(f"<span style='color:{ok_c}'>✓ Réussi :</span> " + ", ".join(nm(lg) for lg in good)
                             + "".join(f"<br><span style='color:{mute}'>⚠ {escape(m)}</span>" for m in miss))
            for lg, why in bad:
                lines.append(f"<span style='color:{ko_c}'>✗ Échec · {nm(lg)} :</span> {escape(str(why))}")
            out.append("<br>".join(lines))
        if res.get("engines"):
            out.append(f"<span style='color:{mute}'>Traduit avec {escape(', '.join(res['engines']))}.</span>")
        for n in (res.get("notes") or [])[:2]:
            out.append(f"<span style='color:{mute}'>Écarté : {escape(n)}</span>")
        return "<br><br>".join(out)

    def _ensure_versions(self, ready: list[str]) -> None:
        """Langues préparées avant les versions rangées sous le projet : leur version est créée une fois."""
        if not ready or self.app.projects.parent_of(self.pid):
            return
        have = {r["lang"] for r in self.app.projects.versions(self.pid)}
        v = self.video()
        t = self.app.projects.load_data(self.pid).get("translations") or {}
        made = False
        for lang in ready:
            tr = self.app.transcripts.translation(v["id"], lang) if v else None
            if lang in have or tr is None:
                continue
            tl = trn.translated_timeline(self.host.tl, v["id"], float(v["duration_s"] or 0), lang, tr,
                                         t.get("preset") or "white", top_text=(t.get("top_texts") or {}).get(lang, ""),
                                         masks=t.get("masks"), dubbed=bool(t.get("dubbed")),
                                         keep_music=bool(t.get("keep_music", True)),
                                         text_style=self.app.montage.text_style_for(self.pid))
            self.app.projects.save_version(self.pid, lang, tl.to_dict(), {"project_video": v["id"]})
            made = True
        if made:
            self.host.sidebar.refresh()

    def _update_video_line(self, v, src) -> None:
        if not v:
            self.video_lbl.setText("")
            return
        names = dict(LANGS)
        if src is not None:
            lang = names.get(src.language, src.language.upper() if src.language else "?")
            info = f"transcrite ({lang} détecté, {len(src.segments)} phrases)"
        elif _active_job(self.app, "transcribe", "asset_id", v["id"]):
            info = "transcription en cours…"
        elif not v.get("audio_tracks"):
            info = "pas de son : rien à traduire"
        else:
            last = _last_job(self.app, "transcribe", "asset_id", v["id"])
            info = ("aucune parole exploitable détectée" if last and last["status"] == "succeeded"
                    and last["result"].get("segments") == 0 else "transcription automatique au lancement")
        self.video_lbl.setText(f"{v['file_name']} · {fmt_timecode(v['duration_s'] or 0)} · {info}")

    def _langs(self) -> list[str]:
        return [c for c, b in self.chips.items() if b.isChecked() and b.isEnabled()]

    def _update_go(self) -> None:
        if self.job_id is not None:
            self.go.setText("Annuler")
            self.go.setEnabled(True)
            return
        n = len(self._langs())
        self.go.setText(f"Préparer  ·  {n} langue{'s' if n > 1 else ''}" if n else "Préparer")
        self.go.setEnabled(bool(self.video() and n and (self.do_subs.isChecked() or self.do_voice.isChecked())))
        self.go.setToolTip("Chaque langue devient une version de la vidéo, rangée sous elle (barre de gauche).")

    def _pick_voice(self, current: dict | None = None) -> None:
        dlg = VoiceDialog(self.app, current or self.voice or
                          ({"engine": self.app.settings.get("voice_engine", None)}
                           if self.app.settings.get("voice_engine", None) else None), self)
        if dlg.exec():
            if dlg.choice is None and current:            # aucune voix choisie : la précédente reste
                return
            self.voice = dlg.choice
            if self.voice and not self.do_voice.isChecked():
                self._loading = True
                self.do_voice.setChecked(True)
                self._loading = False
            self._changed()

    def _preview(self) -> None:
        """Aperçu : le projet ouvert tel qu'il est. Le projet d'origine montre la vidéo originale (pour d'autres
        traductions) ; une version (« · V-EN », en retrait dans la barre) montre sa langue, voix doublée comprise."""
        if self.video():
            self.player.set_timeline(self.host.tl, self.host.assets)

    def _fill_tr_edit(self, v) -> None:
        """Un bouton par langue déjà traduite (parmi celles proposées) : relire et corriger sa traduction."""
        from ..ai.translate import LANG_NAMES
        while self.tr_edit.count():
            w = self.tr_edit.takeAt(0).widget()
            if w:
                w.hide()
                w.setParent(None)
                w.deleteLater()
        have = [lg for lg in (self.app.transcripts.languages(v["id"]) if v else []) if lg in self.chips]
        if not have:
            return
        self.tr_edit.addWidget(label("Corriger :", "faint", hint=False))
        for lg in have:
            b = QToolButton()
            b.setIcon(flag_icon(lg))
            b.setIconSize(QSize(20, 20))
            b.setProperty("role", "flag")
            b.setCursor(Qt.PointingHandCursor)
            b.setToolTip(LANG_NAMES.get(lg, lg).capitalize())
            b.clicked.connect(lambda _c=False, lg=lg: self._edit_translation(lg))
            b.setProperty("lang", lg)
            self.tr_edit.addWidget(b)
        self.tr_edit.addStretch(1)

    def _history(self, kind: str) -> None:
        """Historique des traductions (ou des voix) de la vidéo, langue par langue : un clic revient à une version,
        qui s'applique partout (sous-titres, versions de la langue)."""
        from ..ai.translate import LANG_NAMES
        from .transcript_dialog import history_entries
        v = self.video()
        if not v:
            return
        langs = [r["language"] for r in self.app.db.query(
            "SELECT DISTINCT language FROM version_history WHERE source_asset_id = ? AND kind = ? ORDER BY language",
            (v["id"], kind))]
        entries: list = []
        for lg in langs:
            rows = history_entries(self.app, v["id"], kind, lg)
            if rows:
                entries.append(LANG_NAMES.get(lg, lg).capitalize())
                entries += rows
        btn = self.voice_hist if kind == "dub" else self.subs_hist
        self.hist_pop = HistoryPopover("Historique des voix" if kind == "dub" else "Historique des traductions",
                                       entries, self, empty="Rien de généré pour l'instant.")
        self.hist_pop.picked.connect(lambda hid: self._apply_version(kind, hid))
        self.hist_pop.show_at(btn)

    def _apply_version(self, kind: str, hid: int) -> None:
        if kind == "dub":
            if self.app.dubs.apply_version(hid) is not None:
                self.host.save_lbl.setText("Voix choisie : elle remplace la précédente dans la version de la langue.")
        else:
            lang = self.app.transcripts.apply_version(hid)
            if lang is not None:
                self.host.transcript_saved(lang)
        self.host.refresh()
        self.schedule_refresh()

    def _edit_translation(self, lang: str) -> None:
        from .transcript_dialog import TranscriptDialog
        dlg = TranscriptDialog(self.app, self.video(), lang, self)
        dlg.saved.connect(self.host.transcript_saved)
        dlg.exec()

    def _translate_now(self) -> None:
        """Traduire le texte (sans voix ni version) dans les langues cochées, pour le relire avant « Préparer »."""
        v = self.video()
        langs = self._langs()
        if not v or not langs:
            QMessageBox.information(self, "Traduire", "Coche d'abord une ou plusieurs langues (drapeaux).")
            return
        if not self.app.transcripts.has(v["id"]):
            QMessageBox.information(self, "Traduire", "La vidéo n'est pas encore transcrite : attends la fin de la "
                                    "transcription (liste des tâches) ou lance-la avec le bouton « Transcription », "
                                    "en haut.")
            return
        todo = langs                                      # une nouvelle traduction ; les précédentes : historique
        p = self.app.projects.get(self.pid)
        for lg in todo:
            self.tr_jobs.add(self.app.jobs.submit(
                "translate", f"Traduction du texte · {v['file_name']} · {lg.upper()}",
                {"asset_id": v["id"], "language": lg, "engine": self._engine(),
                 "glossary": [p["concept_name"]] if p and p["concept_name"] else []}))
        self._tr_note = "Traduction du texte en cours…"
        self.note(self._tr_note)

    def _go(self) -> None:
        if self.job_id is not None:
            self.app.jobs.cancel(self.job_id)
            return
        v = self.video()
        langs = self._langs()
        if not v or not langs:
            return
        voice = self.voice if self.do_voice.isChecked() else None
        if voice and not self.app.settings.get("dub_consent", False):
            from .dub_dialog import CONSENT
            if not ask(self, "Voix", CONSENT + " ?"):
                return
            self.app.settings.set("dub_consent", True)
        self.host.flush()
        p = self.app.projects.get(self.pid)
        st = self._state()
        root = self.app.projects.root_of(self.pid)                # versions rangées sous le projet d'origine
        template = self.host.tl if root == self.pid else self.app.montage.load(root)
        self.job_id = self.app.jobs.submit(
            "translate_video", f"Traduction · {v['file_name']} · {', '.join(c.upper() for c in langs)}",
            {"asset_id": v["id"], "project_id": self.pid, "root_id": root, "languages": langs,
             "engine": self._engine(), "voice": voice, "top_text": "", "preset": next(iter(trn.SUB_PRESETS)),
             "keep_music": st["keep_music"], "erase_texts": st["erase"], "subtitles": self.do_subs.isChecked(),
             "timeline": template.to_dict(), "folder": str(self.export_dir),
             "export": False, "glossary": [p["concept_name"]] if p["concept_name"] else []})
        self.status.setText("")
        self._show_task("Préparation", 0.0)
        self.progress.show()
        self._update_go()

    def note(self, text: str) -> None:
        self.task.setText(text)
        self.task.setVisible(bool(text))

    def on_job(self, job) -> None:
        pid = job.params.get("project_id")
        if job.kind == "translate_video" and job.status == "succeeded" and pid and self.app.projects.get(pid) \
                and not job.params.get("export", True):
            # langues prêtes : gardées dans le projet, l'onglet Export en fait la vidéo
            r = job.result or {}
            data = self.app.projects.load_data(pid)
            old = data.get("translations") or {}
            data["translations"] = {
                "ready": sorted(set(old.get("ready") or []) | set(r.get("ready") or [])),
                "top_texts": {**(old.get("top_texts") or {}), **(r.get("top_texts") or {})},
                "masks": r.get("masks") or [], "dubbed": bool(r.get("dubbed")),
                "keep_music": bool(r.get("keep_music", True)), "preset": job.params.get("preset") or "white"}
            self.app.projects.save_data(pid, data)
            versions = r.get("versions") or {}
            first = next((versions[lg] for lg in (r.get("languages") or []) if lg in versions), None)
            if first and pid == self.pid and self.host.stack.currentWidget() is self:   # la première version s'ouvre d'elle-même
                from PySide6.QtCore import QTimer
                QTimer.singleShot(0, lambda: self.host.open_from_list(first, "translation"))
        if not self.pid:
            return
        if job.kind == "translate_video" and job.status in ("pending", "running") and job.id == self.job_id:
            self._show_task(job.message or "", job.progress or 0)
            return
        if job.kind == "translate" and job.id in self.tr_jobs and job.status not in ("pending", "running"):
            self.tr_jobs.discard(job.id)
            if job.status == "failed":
                self._tr_note = "Traduction en échec : " + ((job.error or "").strip().splitlines() or ["?"])[-1][:200]
            elif not self.tr_jobs:
                self._tr_note = ""
            self.schedule_refresh()
            return
        if job.kind in ("translate_video", "import_file", "import_url", "transcribe") and \
                job.status not in ("pending", "running"):
            self.schedule_refresh()
        elif job.kind == "transcribe" and job.status == "running" and not self.video_lbl.text().endswith("…"):
            self.schedule_refresh()
