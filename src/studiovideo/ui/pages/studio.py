"""Studio (§2.1 B) : médias du projet, lecteur source, montage, timeline, inspecteur, export."""
from __future__ import annotations

import json
from pathlib import Path

from PySide6.QtCore import QSize, QStandardPaths, Qt, QTimer, Signal
from PySide6.QtGui import QKeySequence, QPixmap, QShortcut
from PySide6.QtWidgets import (QButtonGroup, QFileDialog, QComboBox, QLabel, QLineEdit, QMenu, QToolButton, QHBoxLayout,
                               QInputDialog, QListWidget, QListWidgetItem, QMessageBox, QSplitter, QStackedWidget,
                               QTabWidget, QVBoxLayout, QWidget)

from ...core import AppContext
from ...edit.model import Timeline, apply_concept_defaults
from ...edit.render import output_path
from ...media.ffmpeg import fmt_timecode
from ..clip_export import ClipExportPanel
from ..importing import DropZone, import_paths, import_url, pick_and_import
from ..montage_panel import MontagePanel
from ..montage_player import MontagePlayer
from ..player import PlayerWidget
from ..timeline_view import TimelineView
from ..highlights_panel import HighlightsPanel
from ..transcript_panel import TranscriptPanel
from .. import theme
from ..widgets import Card, EmptyState, button, label, tool

HISTORY_MAX = 100

# Espace de travail de chaque parcours : ce qui est affiché, le reste est masqué (« Tous les outils » le montre).
# Clipping et Traduction ont leur propre écran (ui/simple_views.py) ; « Tous les outils » ouvre le montage complet
# depuis Traduction et Création originale. Textes : uniquement les textes, les sous-titres et l'export, sans
# « Tous les outils » ni annuler / rétablir.
# left : onglets de gauche (0 Médias, 1 Texte, 2 Extraits) · viewer : 0 Montage, 1 Source ·
# panel : onglets de l'inspecteur (0 Extrait, 1 Cadrage, 2 Textes, 3 Sous-titres, 4 Audio, 5 Export) ·
# timeline : piste de montage · export : panneau d'export des clips à droite.
FULL = {"left": (0, 1, 2), "viewer": (0, 1), "panel": (0, 1, 2, 3, 4, 5), "timeline": True, "export": False}
LAYOUTS = {
    "free_edit": FULL,
    "original": {"left": (0,), "viewer": (0,), "panel": (2, 3, 4, 5), "timeline": False, "export": False},
    "texts": {"left": (0,), "viewer": (0,), "panel": (2, 3, 5), "timeline": False, "export": False},
}
SIMPLE = ("smart_clipping", "translation")
NO_FULL = ("smart_clipping", "texts")            # pas de « Tous les outils » : l'onglet fait sa seule tâche

# Formats proposés partout (en haut du Studio) : format, icône, nom
FORMATS = (("16:9", "rectangle-horizontal", "Horizontal 16:9"), ("1:1", "square", "Carré 1:1"),
           ("9:16", "rectangle-vertical", "Vertical 9:16"))


class FormatPicker(QWidget):
    """Format de la vidéo en trois icônes : horizontal, carré, vertical."""
    changed = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        h = QHBoxLayout(self)
        h.setContentsMargins(0, 0, 0, 0)
        h.setSpacing(2)
        self.group = QButtonGroup(self)
        self.group.setExclusive(True)
        self.buttons: dict[str, QToolButton] = {}
        for asp, ic, name in FORMATS:
            b = tool(ic, f"Format {name}", checkable=True)
            b.setProperty("aspect", asp)
            self.group.addButton(b)
            self.buttons[asp] = b
            h.addWidget(b)
        self.group.buttonClicked.connect(lambda b: self.changed.emit(b.property("aspect")))

    def value(self) -> str | None:
        b = self.group.checkedButton()
        return b.property("aspect") if b else None

    def set_value(self, aspect: str) -> None:
        """Coche le format du montage (aucun si c'est un autre format, 4:5 d'un ancien projet par exemple)."""
        self.group.setExclusive(False)
        for asp, b in self.buttons.items():
            b.setChecked(asp == aspect)
        self.group.setExclusive(True)

    def restyle(self) -> None:
        for asp, b in self.buttons.items():
            b.setIcon(theme.icon(b.icon_name, theme.color("muted"), 18))


def default_export_dir() -> Path:
    base = QStandardPaths.writableLocation(QStandardPaths.MoviesLocation) or str(Path.home() / "Videos")
    return Path(base) / "Studio Vidéo"


class StudioPage(QWidget):
    new_project = Signal(object)
    new_blank = Signal(str)                   # parcours : projet vierge, supprimé s'il reste intact
    view_changed = Signal()                   # écran affiché changé (onglet Export, retour…) : dock à jour
    goto_tab = Signal(str)                    # « Suivant » : le projet ouvert passe dans un autre onglet
    open_models = Signal()
    plan_publication = Signal(int)            # variant_id exporté
    open_ai_settings = Signal()

    def __init__(self, app: AppContext, parent=None):
        super().__init__(parent)
        self.app = app
        self.project_id: int | None = None
        self.concept_id: int | None = None
        self.asset_id: int | None = None
        self.tl = Timeline()
        self.assets: dict[int, dict] = {}
        self._history: list[str] = []
        self._future: list[str] = []
        self._jobs: dict[int, str] = {}
        self._save_timer = QTimer(self, singleShot=True, interval=600)
        self._save_timer.timeout.connect(self._autosave)
        self._snap_timer = QTimer(self, singleShot=True, interval=450)
        self._snap_timer.timeout.connect(self._snapshot)

        root = QVBoxLayout(self)
        root.setContentsMargins(18, 14, 18, 4)
        root.setSpacing(10)
        top = QHBoxLayout()
        top.setSpacing(8)
        self.mode_icon = QLabel()
        self.mode_icon.setFixedSize(28, 28)
        top.addWidget(self.mode_icon)
        self.project_combo = QComboBox()
        self.project_combo.setMinimumWidth(240)
        self.project_combo.setMaximumWidth(380)
        self.project_combo.setToolTip("Projet ouvert")
        self.project_combo.currentIndexChanged.connect(self._on_project_combo)
        top.addWidget(self.project_combo)
        top.addWidget(tool("plus", "Nouveau projet", lambda: self.new_blank.emit(self._mode() or "free_edit")))
        self.concept_lbl = label("", "badge")
        top.addWidget(self.concept_lbl)
        top.addSpacing(6)
        self.format_pick = FormatPicker()
        self.format_pick.changed.connect(self._on_format)
        top.addWidget(self.format_pick)
        top.addStretch(1)
        self.save_lbl = label("", "faint")               # messages brefs (import lancé, image enregistrée…)
        top.addWidget(self.save_lbl)
        # importer : même endroit, même forme dans tous les onglets ; la vidéo devient celle du projet
        self.import_btn = tool("upload", "Importer une vidéo de l'ordinateur (Ctrl+I)", self._import_files,
                               color=theme.color("accent"))
        self.import_btn.setProperty("kind", "soft")
        self.link_edit = QLineEdit()
        self.link_icon = self.link_edit.addAction(theme.icon("link", theme.color("muted"), 16),
                                                  QLineEdit.LeadingPosition)
        self.link_edit.setToolTip("Coller le lien d'une vidéo (YouTube, TikTok, Twitch…) puis Entrée")
        self.link_edit.setFixedWidth(150)
        self.link_edit.returnPressed.connect(self._import_link)
        self.media_btn = button("Médias", "soft", tooltip="Une vidéo déjà importée", icon="film",
                                on_click=self._attach_from_library)
        for w in (self.import_btn, self.link_edit, self.media_btn):
            w.setFixedHeight(34)
            top.addWidget(w)
        self.undo_btn = tool("undo-2", "Annuler (Ctrl+Z)", self.undo)
        self.redo_btn = tool("redo-2", "Rétablir (Ctrl+Y)", self.redo)
        top.addWidget(self.undo_btn)
        top.addWidget(self.redo_btn)
        self.full_btn = tool("panel-left", "Tous les outils (montage complet)", self._toggle_full, checkable=True)
        top.addWidget(self.full_btn)
        from ..shell import JobsButton
        self.jobs_btn = JobsButton()                     # la fenêtre principale ouvre la liste des tâches
        top.addWidget(self.jobs_btn)
        self.export_quick = tool("share-2", "Exporter (Ctrl+E)", self.show_export, size=38, icon_px=19,
                                 color="#FFFFFF")
        self.export_quick.setProperty("kind", "export")
        top.addWidget(self.export_quick)
        self.topbar = QWidget()
        self.topbar.setLayout(top)
        top.setContentsMargins(0, 0, 0, 0)
        root.addWidget(self.topbar)
        from ..guide import GuideBar
        self.guide = GuideBar()
        self.guide.hide()
        root.addWidget(self.guide)

        self.stack = QStackedWidget()
        root.addWidget(self.stack, 1)
        self.stack.addWidget(EmptyState("Choisissez un parcours en bas",
                                        "Ou glissez une vidéo n'importe où dans la fenêtre : un projet brouillon "
                                        "est créé et ses extraits sont recherchés automatiquement."))

        vsplit = QSplitter(Qt.Vertical)
        vsplit.setHandleWidth(8)
        hsplit = QSplitter(Qt.Horizontal)
        hsplit.setHandleWidth(10)
        hsplit.setChildrenCollapsible(False)
        vsplit.setChildrenCollapsible(False)

        # -- colonne médias et clips
        left = QWidget()
        ll = QVBoxLayout(left)
        ll.setContentsMargins(0, 0, 0, 0)
        mh = QHBoxLayout()
        mh.addStretch(1)
        mh.addWidget(tool("upload", "Importer des fichiers", lambda: pick_and_import(self.app, self, self.project_id)))
        mh.addWidget(tool("link", "Importer depuis une URL", lambda: import_url(self.app, self, self.project_id)))
        mh.addWidget(tool("film", "Ajouter depuis Médias", self._attach_from_library))
        ll.addLayout(mh)
        self.media_list = QListWidget()
        self.media_list.setIconSize(QSize(64, 36))
        self.media_list.currentItemChanged.connect(self._on_media)
        self.media_list.itemDoubleClicked.connect(lambda _it: self._add_media_to_timeline())
        ll.addWidget(self.media_list, 2)
        dz = DropZone("Déposez des fichiers ici")
        dz.setMinimumHeight(52)
        dz.setMaximumHeight(64)
        dz.dropped.connect(lambda p: import_paths(self.app, self, p, self.project_id))
        ll.addWidget(dz)
        ch = QHBoxLayout()
        ch.addWidget(label("Clips", "h2"))
        ch.addStretch(1)
        ch.addWidget(tool("plus", "Ajouter le clip à la timeline",
                          lambda: self._clip_to_timeline(self.clips.currentItem())))
        ch.addWidget(tool("pencil", "Renommer le clip", self._rename_clip))
        ch.addWidget(tool("trash-2", "Supprimer le clip", self._delete_clip))
        ll.addLayout(ch)
        self.clips = QListWidget()
        self.clips.itemDoubleClicked.connect(self._clip_to_timeline)
        ll.addWidget(self.clips, 1)
        self.left_tabs = QTabWidget()
        self.left_tabs.setDocumentMode(True)
        self.left_tabs.setIconSize(QSize(18, 18))
        self.left_tabs.addTab(left, theme.icon("folder-open", theme.color("muted"), 18), "Médias")
        self.transcript = TranscriptPanel(app)
        self.transcript.seek.connect(self._seek_source)
        self.transcript.make_clip.connect(self._create_clip)
        self.transcript.add_to_timeline.connect(self._add_range_to_timeline)
        self.transcript.open_models.connect(self.open_models.emit)
        self.transcript.text_edit.connect(self._text_edit_ranges)
        self.left_tabs.addTab(self.transcript, theme.icon("captions", theme.color("muted"), 18), "Texte")
        self.left_tabs.setTabToolTip(1, "Transcription")
        self.highlights = HighlightsPanel(app)
        self.highlights.play_range.connect(self._play_source_range)
        self.highlights.clips_created.connect(self._load_clips)
        self.highlights.add_to_timeline.connect(self._add_ranges)
        self.highlights.open_settings.connect(self.open_ai_settings.emit)
        self.left_tabs.addTab(self.highlights, theme.icon("sparkles", theme.color("muted"), 18), "Extraits")
        self.left_tabs.setTabToolTip(2, "Extraits")
        self.left_tabs.tabBar().setStyleSheet("QTabBar::tab { padding: 6px 9px; margin: 2px 1px 6px 0; }")
        self.left_tabs.currentChanged.connect(lambda i: self._keep_visible_tab(self.left_tabs, i))
        hsplit.addWidget(self.left_tabs)

        # -- lecteurs
        self.viewer_tabs = QTabWidget()
        mc = Card(padding=10)
        self.mplayer = MontagePlayer()
        self.mplayer.track_resolver = self._tracks_for
        self.mplayer.time_changed.connect(self._on_time)
        mc.lay.addWidget(self.mplayer)
        self.viewer_tabs.addTab(mc, "Montage")
        sc = Card(padding=10)
        self.player = PlayerWidget()
        self.player.create_clip.connect(self._create_clip)
        self.player.in_out_changed.connect(lambda *_: self._save_timer.start())
        self.player.position_changed.connect(lambda t: self.transcript.highlight_time(t))
        sc.lay.addWidget(self.player)
        self.player.actions_row.addWidget(button("Ajouter", "primary",
                                                 tooltip="Ajoute la sélection (ou tout le média) à la fin de la "
                                                 "timeline (A)", on_click=self._add_media_to_timeline, icon="plus"))
        self.viewer_tabs.addTab(sc, "Source")
        self.viewer_tabs.currentChanged.connect(self._viewer_changed)
        self.viewer_tabs.currentChanged.connect(lambda i: self._keep_visible_tab(self.viewer_tabs, i))
        hsplit.addWidget(self.viewer_tabs)

        # -- inspecteur
        self.panel = MontagePanel(app)
        self.panel.changed.connect(self._on_panel_change)
        self.mplayer.layout_edited.connect(self._on_layout_edited)
        self.panel.split_requested.connect(self.split)
        self.panel.analyze_requested.connect(self._analyze)
        self.panel.export_requested.connect(self.show_export)
        self.panel.save_concept_requested.connect(self._save_concept)
        self.panel.apply_concept_requested.connect(self._apply_concept)
        self.panel.generate_subs_requested.connect(self._generate_subs)
        self.panel.optimize_requested.connect(self._optimize_audio)
        self.panel.separate_requested.connect(self._separate)
        self.panel.detect_text_requested.connect(self._detect_text)
        self.panel.reframe_requested.connect(self._reframe)
        self.panel.cue_selected.connect(lambda t: self.mplayer.seek(t))
        self.panel.currentChanged.connect(lambda i: self._keep_visible_tab(self.panel, i))
        hsplit.addWidget(self.panel)

        # -- export des clips (espace Clipping intelligent)
        self.export_card = Card(padding=12)
        self.export_panel = ClipExportPanel(app, default_export_dir())
        self.export_panel.launched.connect(self._on_clips_launched)
        self.export_panel.template_fn = lambda: self.tl
        self.export_card.lay.addWidget(self.export_panel)
        self.export_card.setMinimumWidth(320)
        self.export_card.hide()
        hsplit.addWidget(self.export_card)
        hsplit.setStretchFactor(0, 1)
        hsplit.setStretchFactor(1, 3)
        hsplit.setStretchFactor(2, 1)
        hsplit.setStretchFactor(3, 1)
        hsplit.setSizes([250, 720, 380, 360])
        self.hsplit = hsplit
        self._full = False
        self.left_tabs.setMinimumWidth(230)
        self.viewer_tabs.setMinimumWidth(330)
        vsplit.addWidget(hsplit)

        # -- timeline
        tw = QWidget()
        tl_lay = QVBoxLayout(tw)
        tl_lay.setContentsMargins(0, 4, 0, 0)
        bar = QHBoxLayout()
        bar.setSpacing(4)
        bar.addWidget(tool("scissors", "Couper à la tête de lecture (S)", self.split))
        bar.addWidget(tool("trash-2", "Supprimer l'élément sélectionné (Suppr)", self._delete_selected))
        bar.addWidget(tool("maximize-2", "Afficher toute la timeline", lambda: self.timeline.fit()))
        bar.addSpacing(6)
        bar.addWidget(button("Silences", "ghost", tooltip="Retirer les blancs de parole (annulable avec Ctrl+Z)",
                             on_click=self._remove_silences, icon="audio-waveform"))
        bar.addStretch(1)
        tl_lay.addLayout(bar)
        self.timeline = TimelineView()
        bar.addWidget(self.timeline.info)                 # infos et zoom sur la même ligne que les outils
        bar.addSpacing(10)
        bar.addWidget(self.timeline.zoom)
        self.timeline.zoom.setFixedWidth(120)
        c = self.timeline.canvas
        c.seek.connect(lambda t: self.mplayer.seek(t))
        c.edited.connect(self._on_timeline_drag)
        c.segment_selected.connect(self._select_segment)
        c.text_selected.connect(self._select_text)
        c.music_selected.connect(lambda: self.panel.setCurrentIndex(4))
        c.cue_selected.connect(self._select_cue)
        tl_lay.addWidget(self.timeline)
        vsplit.addWidget(tw)
        self.timeline_box = tw
        vsplit.setStretchFactor(0, 4)
        vsplit.setStretchFactor(1, 1)
        vsplit.setSizes([520, 200])
        self.stack.addWidget(vsplit)
        self.editor = vsplit

        # -- espaces simplifiés : Clipping et Traduction s'ouvrent sur un seul écran
        from ..simple_views import ClippingView, TranslationView
        self.clip_view = ClippingView(app, self, default_export_dir())
        self.tr_view = TranslationView(app, self, default_export_dir())
        from ..export_view import ExportView
        self.export_view = ExportView(app, self, default_export_dir())
        self._export_mode = False
        for v in (self.clip_view, self.tr_view, self.export_view):
            v.open_editor.connect(lambda: self._toggle_full(True))
            v.open_settings.connect(self.open_ai_settings.emit)
            self.stack.addWidget(v)

        for key, fn in (("Ctrl+Z", self.undo), ("Ctrl+Y", self.redo), ("Ctrl+Shift+Z", self.redo),
                        ("S", self.split), ("Delete", self._delete_selected), ("A", self._add_media_to_timeline),
                        ("Ctrl+E", self.show_export), ("Ctrl+Shift+S", self._export_still)):
            sc_ = QShortcut(QKeySequence(key), self)
            sc_.setContext(Qt.WidgetWithChildrenShortcut)
            sc_.activated.connect(fn)
        self.refresh()

    # -- projet -------------------------------------------------------------
    def refresh(self) -> None:
        self.project_combo.blockSignals(True)
        self.project_combo.clear()
        for r in self.app.projects.recent(200):
            self.project_combo.addItem(r["name"], r["id"])
        if self.project_id:
            if self.project_combo.findData(self.project_id) < 0:          # projet archivé ouvert
                cur = self.app.projects.get(self.project_id)
                if cur is None:                                            # projet supprimé
                    self.project_id = None
                else:
                    self.project_combo.addItem(f"{cur['name']}  ·  archivé", self.project_id)
        if self.project_id:
            self.project_combo.setCurrentIndex(max(0, self.project_combo.findData(self.project_id)))
        self.project_combo.setVisible(self.project_combo.count() > 0)
        self.project_combo.blockSignals(False)
        self.stack.setCurrentIndex(1 if self.project_id else 0)
        if self.project_id:
            self._load_media()
            if self.asset_id is None and self.media_list.count():
                self.media_list.setCurrentRow(0)          # espaces sans liste de médias : la vidéo est choisie
            self._load_clips()
            self._sync_project_video()
            self._push_views(panel=True)
        self._apply_layout()                      # affiche aussi la barre d'étapes du parcours

    # -- la vidéo du projet (la même dans tous les onglets) -----------------------------------
    def project_video(self) -> dict | None:
        """La vidéo du projet : celle du montage, sinon la dernière importée."""
        for sg in self.tl.segments:
            a = self.assets.get(sg.asset_id)
            if a and a.get("kind") == "video":
                return a
        vids = self._project_videos()
        return max(vids, key=lambda a: a["id"]) if vids else None

    def _sync_project_video(self) -> None:
        """Vidéo importée : placée d'office dans le montage (timeline vide) ; dans Clipping et Traduction, une
        nouvelle vidéo remplace la précédente (un projet = une vidéo). Dans Montage libre, une vidéo ajoutée
        ensuite reste dans la liste des médias, à placer où l'on veut."""
        vids = self._project_videos()
        if not self.project_id or not vids:
            return
        latest = max(vids, key=lambda a: a["id"])
        data = self.app.projects.load_data(self.project_id)
        known = data.get("project_video")
        if latest["id"] == known and self.tl.segments:
            return
        replace = known is not None and latest["id"] != known and self._mode() in SIMPLE
        if not self.tl.segments or replace:
            if self._mode() == "translation" and not self.tl.segments:
                # Traduction : le format suit la vidéo d'origine (une vidéo courte reste verticale)
                from ...edit.translation import source_aspect
                asp = source_aspect(latest.get("width"), latest.get("height"))
                if asp != self.tl.format.aspect:
                    self.tl.set_aspect(asp, min(self.tl.format.width, self.tl.format.height))
            self.tl.segments = []
            self.tl.add_segment(latest["id"], 0.0, float(latest["duration_s"] or 0))
            if replace:
                data.pop("clip_of", None)
            data["project_video"] = latest["id"]
            self.app.projects.save_data(self.project_id, data)
            self._changed(panel=True)
            self.flush()
            self.viewer_tabs.setCurrentIndex(0)           # on voit le montage, avec la vidéo dedans
        elif known is None:
            data["project_video"] = latest["id"]
            self.app.projects.save_data(self.project_id, data)

    def replace_timeline(self, tl: Timeline) -> None:
        """Nouveau montage du projet (extrait choisi en Clipping…) : une étape d'annulation, enregistré."""
        self.tl = tl
        self._changed(panel=True)
        self.flush()

    def show_export(self) -> None:
        """Onglet Export : la vidéo du projet, ses textes et sa publication."""
        if not self.project_id:
            return
        self.flush()
        self._export_mode = True
        self._full = False
        name, ic, col, _d = theme.EXPORT_TAB
        self.mode_icon.setPixmap(theme.icon(ic, col, 24).pixmap(24, 24))
        self.mode_icon.setToolTip(name)
        self._apply_layout()
        self.view_changed.emit()

    def _import_extra(self) -> dict | None:
        return self.clip_view.import_extra() if self._mode() == "smart_clipping" and not self._export_mode else None

    def _import_link(self) -> None:
        u = self.link_edit.text().strip()
        if u and self.project_id and import_url(self.app, self, self.project_id, url=u, extra=self._import_extra()):
            self.link_edit.clear()
            self.save_lbl.setText("Téléchargement de la vidéo en cours…")

    # -- parcours guidés (§4) -------------------------------------------------------
    def _project_videos(self) -> list[dict]:
        return [a for a in self.assets.values() if a.get("kind") == "video" and a.get("project_id") == self.project_id]

    def _guide_asset(self) -> int | None:
        vids = self._project_videos()
        if self.asset_id and any(v["id"] == self.asset_id for v in vids):
            return self.asset_id
        return vids[0]["id"] if vids else None

    def _select_asset(self, aid: int) -> None:
        for i in range(self.media_list.count()):
            if self.media_list.item(i).data(Qt.UserRole) == aid:
                self.media_list.setCurrentRow(i)
                return

    def target_language(self) -> str:
        data = self.app.projects.load_data(self.project_id) if self.project_id else {}
        aid = self._guide_asset()
        langs = self.app.transcripts.languages(aid) if aid else []
        t = data.get("target_lang")
        return t if t in langs else (langs[0] if langs else "")

    def _simple_view(self):
        """Écran simplifié affiché (Clipping, Traduction, Export), sauf avec « Tous les outils »."""
        if self._full or not self.project_id:
            return None
        if self._export_mode:
            return self.export_view
        return {SIMPLE[0]: self.clip_view, SIMPLE[1]: self.tr_view}.get(self._mode())

    def _refresh_guide(self) -> None:
        if self._simple_view() is not None:
            self.guide.hide()
            return
        p = self.app.projects.get(self.project_id) if self.project_id else None
        mode = p["mode"] if p else None
        steps = None
        extras = []
        if mode == "translation":
            steps, title = self._translation_steps(), "Traduction"
        elif mode == "original":
            steps, title = self._original_steps(), "Création originale"
            if self.app.projects.load_data(self.project_id).get("original_script"):
                extras = [("Script…", self._original_dialog)]
        elif mode == "smart_clipping":
            steps, title = self._clipping_steps(), "Clipping"
        if not steps:
            self.guide.hide()
            return
        self.guide.set_steps(title, steps, extras)
        self.guide.show()

    # -- espaces de travail -----------------------------------------------------------
    def _mode(self) -> str | None:
        p = self.app.projects.get(self.project_id) if self.project_id else None
        return p["mode"] if p else None

    def _apply_layout(self) -> None:
        """Affiche les outils du parcours du projet ouvert ; « Tous les outils » montre le montage complet."""
        mode = self._mode()
        simple = self._simple_view()
        if self.project_id:
            self.stack.setCurrentWidget(simple or self.editor)
        self.topbar.setVisible(True)              # en-tête commun : projet, format, import, Exporter
        for w in (self.export_quick, self.import_btn, self.link_edit, self.media_btn):
            w.setVisible(not self._export_mode)   # onglet Export : ni import ni bouton Exporter
        if simple is not None:
            self.guide.hide()
            self.player.player.pause() if hasattr(self.player.player, "pause") else None
            self.mplayer.pause()
            simple.refresh()
        else:
            self._refresh_guide()
        lay = FULL if (self._full or mode not in LAYOUTS) else LAYOUTS[mode]
        self.full_btn.setVisible(((mode in LAYOUTS and LAYOUTS[mode] is not FULL) or mode in SIMPLE)
                                 and mode not in NO_FULL and not self._export_mode)
        self.full_btn.setToolTip("Revenir à l'écran simplifié" if mode in SIMPLE and self._full else
                                 "Tous les outils (montage complet)")
        self.full_btn.blockSignals(True)
        self.full_btn.setChecked(self._full)
        self.full_btn.blockSignals(False)
        for tabs, keep in ((self.left_tabs, lay["left"]), (self.viewer_tabs, lay["viewer"]),
                           (self.panel, lay["panel"])):
            for i in range(tabs.count()):
                tabs.setTabVisible(i, i in keep)
            tabs.setVisible(bool(keep))
            tabs.tabBar().setVisible(len(keep) > 1 or tabs is self.panel)
            if keep and tabs.currentIndex() not in keep:
                tabs.setCurrentIndex(keep[0])
        self.timeline_box.setVisible(lay["timeline"])
        self.export_card.setVisible(lay["export"])
        if lay["export"] and self.project_id:
            self.export_panel.load(self.project_id, self.tl)
        editing = (lay["timeline"] or bool(lay["panel"])) and simple is None and mode != "texts"
        for w in (self.undo_btn, self.redo_btn):
            w.setVisible(editing)
        self.hsplit.setSizes([(250 if lay is FULL else 320) if lay["left"] else 0, 720,
                              380 if lay["panel"] else 0, 380 if lay["export"] else 0])

    def restyle_accent(self) -> None:
        """Couleur de l'onglet changée : icônes teintées de l'en-tête à jour."""
        self.import_btn.setIcon(theme.icon("upload", theme.color("accent"), 18))
        self.media_btn.setIcon(theme.icon("film", theme.color("accent"), 16))

    def _keep_visible_tab(self, tabs: QTabWidget, i: int) -> None:
        """Un onglet masqué par l'espace de travail ne devient pas l'onglet affiché."""
        if 0 <= i < tabs.count() and not tabs.isTabVisible(i):
            vis = [k for k in range(tabs.count()) if tabs.isTabVisible(k)]
            if vis:
                tabs.setCurrentIndex(vis[0])

    def _toggle_full(self, on: bool) -> None:
        self._full = bool(on)
        self._apply_layout()

    def leave_project(self) -> None:
        """Quitte le projet ouvert : un projet vierge resté intact n'est pas conservé."""
        if self.project_id:
            self.flush()
            if self.app.projects.discard_if_untouched(self.project_id):
                self.project_id = None

    def _on_format(self, asp: str) -> None:
        if not self.project_id or not asp or asp == self.tl.format.aspect:
            return
        self.tl.set_aspect(asp, min(self.tl.format.width, self.tl.format.height))
        self._changed(panel=True)
        if self._simple_view() is not None:
            self._simple_view().schedule_refresh()

    def _on_clips_launched(self) -> None:
        bp = self.export_panel
        if bp.job_ids:
            self.save_lbl.setText(f"{len(bp.job_ids)} export(s) en file · dossier « {bp.folder.name} »")
        self._refresh_guide()

    def _export_clips(self) -> None:
        """Export des clips cochés : panneau de droite (affiché au besoin avec « Tous les outils » désactivé)."""
        if not self.export_card.isVisibleTo(self):
            self._toggle_full(False)
        self.flush()
        if self.export_panel.count():
            self.export_panel._launch()

    def _exported(self) -> bool:
        return bool(self.project_id and self.app.montage.exports(self.project_id, limit=1))

    def _n_clips(self) -> int:
        return self.app.db.query_one("SELECT COUNT(*) n FROM clip WHERE project_id = ? AND name != 'Montage'",
                                     (self.project_id,))["n"]

    def _clipping_steps(self):
        from ..guide import Step
        aid = self._guide_asset()
        transcribed = bool(aid and self.app.transcripts.has(aid))
        found = bool(self.app.candidates.list(self.project_id))
        n = self._n_clips()

        def transcribe():
            self._select_asset(aid)
            self.left_tabs.setCurrentIndex(1)
            self.transcript._transcribe()

        def find():
            self._select_asset(aid)
            self.left_tabs.setCurrentIndex(2)
            if self.highlights.go.isEnabled() and self.highlights.job_id is None:
                self.highlights._find()
        return [
            Step("import", "Importer", bool(aid), lambda: pick_and_import(self.app, self, self.project_id),
                 "Une vidéo longue : podcast, débat, interview…"),
            Step("transcribe", "Transcrire", transcribed, transcribe if aid else None,
                 "Reconnaissance vocale locale"),
            Step("find", "Extraits", found, find if transcribed else None,
                 "Les meilleurs passages, notés et justifiés"),
            Step("choose", f"Choisir ({n})", n > 0, (lambda: self.left_tabs.setCurrentIndex(2)) if found else None,
                 "Acceptez les passages retenus puis « Créer les clips »"),
            Step("export", "Exporter", self._exported(), self._export_clips if n else None,
                 "Chaque clip dans les formats choisis, sous-titré, avec son accroche"),
        ]

    def _original_steps(self):
        from ..guide import Step
        data = self.app.projects.load_data(self.project_id)
        aid = data.get("original_asset")
        made = bool(aid and self.app.library.get(aid))
        on_tl = bool(made and any(sg.asset_id == aid for sg in self.tl.segments))
        exported = bool(self.app.montage.exports(self.project_id, limit=1))
        return [
            Step("script", "Script", bool(data.get("original_script")), self._original_dialog,
                 "Idée, texte ou PDF → scènes (avec l'IA si configurée)"),
            Step("make", "Créer la vidéo", made, self._original_dialog, "Voix off facultative, images ou couleurs"),
            Step("build", "Monter", on_tl, (lambda: self.apply_original(aid, [])) if made else None,
                 "Vidéo, textes à l'écran et sous-titres sur la timeline"),
            Step("export", "Exporter", exported, self.show_export if on_tl else None, "Titre, réseaux, publication"),
        ]

    def _original_dialog(self, load_document: bool = False) -> None:
        from ..original_dialog import OriginalDialog
        self._orig_dlg = OriginalDialog(self.app, self.project_id, self.tl.format.width, self.tl.format.height, self)
        if load_document:
            QTimer.singleShot(0, self._orig_dlg._load_file)
        try:
            ok = self._orig_dlg.exec()
            params = self._orig_dlg.params
        finally:
            self._orig_dlg = None
        if ok and params:
            jid = self.app.jobs.submit("create_original", "Création de la vidéo", params)
            self._jobs[jid] = "create_original"
            self.save_lbl.setText("Création de la vidéo en cours…")
        self._refresh_guide()

    def apply_original(self, aid: int, texts: list) -> None:
        """Pose la vidéo créée sur la timeline (remplace le montage), avec textes et sous-titres."""
        from ...edit.subtitles import generate
        a = self.app.library.get(aid)
        if a is None:
            return
        self._snapshot()
        self.tl.segments = []
        self.tl.texts = []
        self.tl.add_segment(aid, 0.0, float(a["duration_s"] or 0))
        style = {k: v for k, v in self.app.montage.text_style_for(self.project_id).items()
                 if k not in ("text", "start", "end", "id")}
        style.setdefault("position", "top")
        for start, end, text in texts:
            self.tl.add_text(text, start, end, **style)
        tr = self.app.transcripts.get(aid)
        self.tl.subtitles.language = ""
        self.tl.subtitles.cues = generate(self.tl, {aid: tr}) if tr else []
        self.tl.subtitles.enabled = bool(self.tl.subtitles.cues)
        self._load_media()
        self._changed(panel=True)
        self.viewer_tabs.setCurrentIndex(0)
        self._refresh_guide()

    def _translation_steps(self):
        from ..guide import Step
        aid = self._guide_asset()
        tr = self.app.transcripts
        langs = tr.languages(aid) if aid else []
        lang = self.target_language()
        dubbed = bool(aid and lang and self.app.dubs and self.app.dubs.get(aid, lang))
        on_tl = bool(aid and any(sg.asset_id == aid for sg in self.tl.segments))
        built = on_tl and self.tl.subtitles.language == lang and bool(self.tl.subtitles.cues)
        exported = bool(self.project_id and self.app.montage.exports(self.project_id, limit=1))

        def transcribe():
            self._select_asset(aid)
            self.left_tabs.setCurrentIndex(1)
            self.transcript._transcribe()

        def translate():
            self._select_asset(aid)
            self.left_tabs.setCurrentIndex(1)
            self.transcript._translate()

        def dub():
            self._select_asset(aid)
            self.left_tabs.setCurrentIndex(1)
            self.transcript.lang = lang
            self.transcript.set_asset(aid)
            self.transcript._dub()
        return [
            Step("import", "Importer la vidéo", bool(aid),
                 lambda: pick_and_import(self.app, self, self.project_id), "Vidéo d'origine à traduire"),
            Step("transcribe", "Transcrire", bool(aid and tr.has(aid)), transcribe if aid else None,
                 "Reconnaissance vocale locale, corrigeable"),
            Step("translate", "Traduire", bool(langs), translate if aid and tr.has(aid) else None,
                 "Une ou plusieurs langues, avec glossaire"),
            Step("dub", f"Doubler ({lang.upper()})" if lang else "Doubler", dubbed, dub if lang else None,
                 "Voix de synthèse par intervenant (ElevenLabs)", optional=True),
            Step("build", "Monter", built, (lambda: self._build_translation(aid)) if lang else None,
                 "Vidéo entière, sous-titres dans la langue choisie, voix doublée si disponible"),
            Step("export", "Exporter", exported, self.show_export if built else None, "Titre, réseaux, publication"),
        ]

    def _build_translation(self, aid: int) -> None:
        from ..dialogs import LANGUAGES
        langs = self.app.transcripts.languages(aid)
        lang = self.target_language()
        if len(langs) > 1:
            names = dict(LANGUAGES)
            items = [names.get(c, c) for c in langs]
            choice, ok = QInputDialog.getItem(self, "Monter la version traduite", "Langue :", items,
                                              max(0, langs.index(lang)) if lang in langs else 0, False)
            if not ok:
                return
            lang = langs[items.index(choice)]
        self.setup_translation(aid, lang)

    def setup_translation(self, aid: int, lang: str) -> None:
        """Prépare le montage traduit : vidéo entière, sous-titres traduits, voix doublée si elle existe."""
        from ...edit.subtitles import generate
        a = self.assets.get(aid) or dict(self.app.library.get(aid))
        self._snapshot()
        if not any(sg.asset_id == aid for sg in self.tl.segments):
            self.tl.add_segment(aid, 0.0, float(a["duration_s"] or 0))
        self.tl.subtitles.language = lang
        trs = {i: self.app.transcripts.for_language(i, lang) for i in {sg.asset_id for sg in self.tl.segments}}
        self.tl.subtitles.cues = generate(self.tl, {i: t for i, t in trs.items() if t is not None})
        self.tl.subtitles.enabled = bool(self.tl.subtitles.cues)
        if self.app.dubs and self.app.dubs.get(aid, lang):
            self.tl.voice.mode, self.tl.voice.language = "dub", lang
        data = self.app.projects.load_data(self.project_id)
        data["target_lang"] = lang
        self.app.projects.save_data(self.project_id, data)
        self._changed(panel=True)
        self.viewer_tabs.setCurrentIndex(0)
        self._refresh_guide()

    def _mode_icon(self, mode: str | None) -> None:
        if mode in theme.PARCOURS:
            name, ic, col, _d = theme.PARCOURS[mode]
            self.mode_icon.setPixmap(theme.icon(ic, col, 24).pixmap(24, 24))
            self.mode_icon.setToolTip(name)

    def open_project(self, project_id: int, mode: str | None = None) -> None:
        """Ouvre le projet ; `mode` : dans cette interface (sinon celle où il a été ouvert en dernier)."""
        p = self.app.projects.get(project_id)
        if mode and p and p["mode"] != mode:
            self.app.projects.set_mode(project_id, mode)
        self._export_mode = False
        self._mode_icon(mode or (p["mode"] if p else None))
        if self.project_id == project_id and mode:
            # même projet, autre interface : rien à recharger, la vidéo et le montage suivent
            self.flush()
            self._full = False
            self._mode_icon(mode)
            self._apply_layout()
            return
        if self.project_id != project_id:
            self.leave_project()
        self.flush()
        self.player.unload()
        self.mplayer.stop()
        self.project_id = project_id
        self.asset_id = None
        self._full = False
        p = self.app.projects.get(project_id)
        self.concept_id = p["concept_id"] if p else None
        self.concept_lbl.setText(p["concept_name"] if p else "")
        self.concept_lbl.setToolTip("Concept (à changer dans Concepts et projets)")
        self._mode_icon(p["mode"] if p else None)
        self.transcript.glossary_hint = [p["concept_name"]] if p else []
        self.tl = self.app.montage.load(project_id)
        self._history = [json.dumps(self.tl.to_dict(), sort_keys=True)]
        self._future = []
        self.panel.sel_segment = self.panel.sel_text = None
        self.refresh()
        data = self.app.projects.load_data(project_id)
        last = data.get("last_asset_id")
        row = 0
        for i in range(self.media_list.count()):
            if self.media_list.item(i).data(Qt.UserRole) == last:
                row = i
        if self.media_list.count():
            self.media_list.setCurrentRow(row)
        self.viewer_tabs.setCurrentIndex(0 if self.tl.segments else 1)
        if p and p["mode"] == "smart_clipping":
            self.left_tabs.setCurrentIndex(2 if self.asset_id and self.app.transcripts.has(self.asset_id) else 1)
        self._apply_layout()
        QTimer.singleShot(50, self.timeline.fit)

    def _on_project_combo(self) -> None:
        pid = self.project_combo.currentData()
        if pid and pid != self.project_id:
            self.open_project(pid, self._mode())

    def project_menu(self, pid: int, mode: str, parent: QWidget, pos) -> None:
        """Clic droit sur un projet (listes des écrans Clipping et Traduction) : renommer, supprimer."""
        p = self.app.projects.get(pid)
        if p is None:
            return
        m = QMenu(parent)
        ren = m.addAction(theme.icon("pencil", theme.color("muted"), 16), "Renommer…")
        dele = m.addAction(theme.icon("trash-2", theme.color("muted"), 16), "Supprimer…")
        a = m.exec(pos)
        if a is ren:
            name, ok = QInputDialog.getText(parent, "Renommer", "Nom du projet :", text=p["name"])
            if ok and name.strip():
                try:
                    self.app.projects.rename(pid, name.strip())
                except ValueError as exc:
                    QMessageBox.information(parent, "Renommer", str(exc))
                self.refresh()
        elif a is dele:
            self.delete_project(pid, mode, parent)

    def delete_project(self, pid: int, mode: str, parent: QWidget | None = None) -> bool:
        """Supprime un projet (après confirmation) ; s'il était ouvert, le suivant prend sa place."""
        p = self.app.projects.get(pid)
        if p is None:
            return False
        if QMessageBox.question(parent or self, "Supprimer", f"Supprimer le projet « {p['name']} » ?\n\nSon montage, "
                                "ses extraits et l'historique de ses exports sont retirés. Les fichiers vidéo "
                                "(sources et exports) restent sur le disque.") != QMessageBox.Yes:
            return False
        current = pid == self.project_id
        if current:
            self.flush()
            self.player.unload()
            self.mplayer.stop()
        try:
            self.app.projects.delete(pid)
        except ValueError as exc:
            QMessageBox.information(parent or self, "Supprimer", str(exc))
            return False
        if not current:
            self.refresh()
            return True
        self.project_id = None
        nxt = self.app.projects.recent(1)
        if nxt:
            self.open_project(nxt[0]["id"], mode)
        else:
            self.new_blank.emit(mode)
        return True

    def _load_media(self) -> None:
        cur = self.asset_id
        self.media_list.blockSignals(True)
        self.media_list.clear()
        self.assets = {}
        ids = {s.asset_id for s in self.tl.segments} | ({self.tl.music.asset_id} if self.tl.music.asset_id else set())
        rows = self.app.db.query("SELECT * FROM source_asset WHERE project_id = ? ORDER BY id", (self.project_id,))
        extra = [self.app.montage.asset_row(i) for i in ids - {r["id"] for r in rows}]
        from ...media import proxy
        for a in list(rows) + [e for e in extra if e]:
            self.assets[a["id"]] = dict(a)
            self.assets[a["id"]]["preview_path"] = proxy.preview_path(self.app.paths.proxies, a)
            if a["project_id"] != self.project_id and a["id"] not in {s.asset_id for s in self.tl.segments}:
                continue                                  # musique d'un autre projet : pas dans la liste
            it = QListWidgetItem(f"{a['file_name']}\n{fmt_timecode(a['duration_s']) if a['duration_s'] else a['kind']}"
                                 + ("  · introuvable" if a["missing"] else ""))
            it.setData(Qt.UserRole, a["id"])
            if a["thumbnail_path"] and Path(a["thumbnail_path"]).exists():
                it.setIcon(QPixmap(a["thumbnail_path"]))
            self.media_list.addItem(it)
            if a["id"] == cur:
                self.media_list.setCurrentItem(it)
        self.media_list.blockSignals(False)

    def _on_media(self, item, _prev=None) -> None:
        if not item:
            return
        self._autosave()
        aid = item.data(Qt.UserRole)
        a = self.app.library.get(aid)
        if not a or a["kind"] not in ("video", "audio"):
            return
        if a["missing"] or not Path(a["path"]).exists():
            # message non bloquant : la transcription et les propositions restent consultables
            self.save_lbl.setText(f"⚠ Fichier introuvable : {a['file_name']}")
            self.save_lbl.setToolTip(a["path"])
            self.asset_id = aid
            self.transcript.set_asset(aid)
            self.highlights.set_context(self.project_id, aid)
            self.player.unload()
            return
        self.asset_id = aid
        self.transcript.set_asset(aid)
        self.highlights.set_context(self.project_id, aid)
        io = self.app.projects.load_data(self.project_id).get("in_out", {}).get(str(aid), [None, None])
        from ...media import proxy
        self.player.load(proxy.preview_path(self.app.paths.proxies, a), a["fps"], *io)

    def _viewer_changed(self, i: int) -> None:
        if i == 0:
            self.player.player.pause() if hasattr(self.player.player, "pause") else None
        else:
            self.mplayer.pause()

    def _import_files(self) -> None:
        n = pick_and_import(self.app, self, self.project_id, self._import_extra())
        if n:
            self.save_lbl.setText(f"Import de {n} fichier(s) en cours…")

    def _attach_from_library(self) -> None:
        rows = self.app.db.query("SELECT id, file_name FROM source_asset WHERE (project_id IS NULL OR project_id != ?) "
                                 "AND kind IN ('video','audio') AND missing = 0 ORDER BY imported_at DESC LIMIT 300",
                                 (self.project_id,))
        if not rows:
            QMessageBox.information(self, "Médias", "Aucun autre média dans Médias.")
            return
        names = [f"{r['file_name']}  [#{r['id']}]" for r in rows]
        choice, ok = QInputDialog.getItem(self, "Vidéo déjà importée", "Média :", names, 0, False)
        if ok:
            self.attach_asset(rows[names.index(choice)]["id"])

    def attach_asset(self, aid: int) -> None:
        """Rattache au projet un média déjà importé (sa transcription et ses analyses le suivent)."""
        if not self.project_id:
            return
        self.app.db.connect().execute("UPDATE source_asset SET project_id = ? WHERE id = ?", (self.project_id, aid))
        self._load_media()
        self._sync_project_video()
        self._select_asset(aid)
        self._refresh_guide()
        if self._simple_view() is not None:
            self._simple_view().refresh()

    # -- timeline : modifications ------------------------------------------------------
    def _changed(self, panel: bool = False) -> None:
        self._push_views(panel=panel)
        self._save_timer.start()
        if panel:
            # modification de structure (ajout, coupe, suppression…) : une étape d'annulation chacune
            self._snap_timer.stop()
            self._snapshot()
        else:
            # réglages continus (curseurs, saisie) : regroupés en une étape
            self._snap_timer.start()

    def _push_views(self, panel: bool = False) -> None:
        names = {k: v["file_name"] for k, v in self.assets.items()}
        music = self.assets.get(self.tl.music.asset_id) if self.tl.music.asset_id else None
        if self.tl.music.asset_id and not music:
            row = self.app.montage.asset_row(self.tl.music.asset_id)
            music = dict(row) if row else None
            if music:
                self.assets[music["id"]] = music
        self.timeline.canvas.durations = {k: v.get("duration_s") or 0 for k, v in self.assets.items()}
        self.timeline.set_timeline(self.tl, names, music["file_name"] if music else "")
        self._ensure_waves()
        self.mplayer.set_timeline(self.tl, self.assets)
        self.export_quick.setEnabled(bool(self.tl.segments))
        self.format_pick.set_value(self.tl.format.aspect)
        self.undo_btn.setEnabled(len(self._history) > 1)
        self.redo_btn.setEnabled(bool(self._future))
        if panel:
            durations = {k: v.get("duration_s") or 0 for k, v in self.assets.items()}
            name = self.app.concepts.get_concept(self.concept_id).name if self.concept_id else ""
            self.panel.set_timeline(self.tl, self.project_id, name, durations)

    def _on_panel_change(self, what: str) -> None:
        self.timeline.canvas.sel_text = self.panel.sel_text
        self.timeline.canvas.sel_segment = self.panel.sel_segment
        if what == "subtitles":
            self.timeline.canvas.update()
        if what == "text-select":
            self.timeline.canvas.update()
            t = self.panel.current_text()
            if t:
                self.mplayer.seek(t.start)
            return
        self._changed(panel=what in ("format", "structure"))

    def _on_layout_edited(self) -> None:
        """Vue déplacée ou zoomée directement dans l'aperçu : réglages du panneau et sauvegarde à jour."""
        self.panel._reload_layout()
        self._changed()

    def _on_time(self, t: float) -> None:
        self.timeline.canvas.set_playhead(t)
        self.panel.playhead = t

    def _select_segment(self, seg_id) -> None:
        self.panel.select_segment(seg_id)
        self.timeline.canvas.sel_segment = seg_id
        self.timeline.canvas.sel_text = None

    def _select_text(self, text_id) -> None:
        self.panel.select_text(text_id)

    def _add_media_to_timeline(self) -> None:
        if not self.project_id or not self.asset_id:
            return
        a = self.assets.get(self.asset_id) or dict(self.app.library.get(self.asset_id))
        if a["kind"] != "video":
            QMessageBox.information(self, "Timeline", "Seules les vidéos vont sur la piste vidéo. Pour un son, "
                                    "choisissez-le comme musique dans l'onglet Audio.")
            return
        start = self.player.in_s if self.player.in_s is not None else 0.0
        end = self.player.out_s if self.player.out_s is not None else (a["duration_s"] or 0)
        try:
            seg = self.tl.add_segment(a["id"], start, end)
        except ValueError as exc:
            QMessageBox.warning(self, "Timeline", str(exc))
            return
        self.panel.sel_segment = seg.id
        self.timeline.canvas.sel_segment = seg.id
        self._changed(panel=True)
        self.viewer_tabs.setCurrentIndex(0)
        self.mplayer.seek(self.tl.segment_start(len(self.tl.segments) - 1))
        QTimer.singleShot(30, self.timeline.fit)

    def _clip_to_timeline(self, item) -> None:
        if not item or not item.data(Qt.UserRole):
            return
        r = self.app.db.query_one("SELECT * FROM clip WHERE id = ?", (item.data(Qt.UserRole),))
        if not r or not r["source_asset_id"]:
            return
        try:
            self.tl.add_segment(r["source_asset_id"], r["start_s"], r["end_s"])
        except ValueError as exc:
            QMessageBox.warning(self, "Timeline", str(exc))
            return
        self._load_media()
        self._changed(panel=True)
        self.viewer_tabs.setCurrentIndex(0)
        QTimer.singleShot(30, self.timeline.fit)

    def _seek_source(self, t: float) -> None:
        self.viewer_tabs.setCurrentIndex(1)
        self.player.seek(t)

    def _add_range_to_timeline(self, a: float, b: float) -> None:
        if not self.asset_id:
            return
        row = self.app.library.get(self.asset_id)
        if row is None or row["kind"] != "video":
            QMessageBox.information(self, "Timeline", "Seules les vidéos vont sur la piste vidéo.")
            return
        try:
            seg = self.tl.add_segment(self.asset_id, a, min(b, row["duration_s"] or b))
        except ValueError as exc:
            QMessageBox.warning(self, "Timeline", str(exc))
            return
        self.panel.sel_segment = seg.id
        self._changed(panel=True)
        self.viewer_tabs.setCurrentIndex(0)
        QTimer.singleShot(30, self.timeline.fit)

    def _play_source_range(self, a: float, b: float) -> None:
        self.viewer_tabs.setCurrentIndex(1)
        self.player.set_range(a, b)
        self.player.preview_range()

    def _add_ranges(self, ranges: list) -> None:
        if not self.asset_id:
            return
        row = self.app.library.get(self.asset_id)
        if row is None or row["kind"] != "video":
            QMessageBox.information(self, "Timeline", "Seules les vidéos vont sur la piste vidéo.")
            return
        n = 0
        for a, b in ranges:
            try:
                self.tl.add_segment(self.asset_id, max(0.0, a), min(b, row["duration_s"] or b))
                n += 1
            except ValueError:
                pass
        self._changed(panel=True)
        self.viewer_tabs.setCurrentIndex(0)
        self.save_lbl.setText(f"{n} extrait(s) ajouté(s) à la timeline")
        QTimer.singleShot(30, self.timeline.fit)

    def _text_edit_ranges(self, ranges: list, replace: bool) -> None:
        if replace and self.tl.segments:
            if QMessageBox.question(self, "Montage par le texte", "Remplacer le contenu actuel de la timeline ? "
                                    "(Annulable avec Ctrl+Z)") != QMessageBox.Yes:
                return
            self.tl.segments = []
            self.tl.subtitles.cues = []
        self._add_ranges(ranges)

    def _generate_subs(self) -> None:
        from ...edit.subtitles import generate
        if not self.tl.segments:
            QMessageBox.information(self, "Sous-titres", "La timeline est vide.")
            return
        ids = {s.asset_id for s in self.tl.segments}
        lang = self.tl.subtitles.language
        trs = {i: self.app.transcripts.for_language(i, lang) for i in ids}
        missing = [self.assets.get(i, {}).get("file_name", str(i)) for i, t in trs.items() if t is None]
        if len(missing) == len(ids) and lang:
            QMessageBox.information(self, "Sous-titres", f"Aucun extrait n'est traduit en « {lang} ».\n\n"
                                    "Onglet « Transcription » → Plus → Traduire…")
            self.left_tabs.setCurrentIndex(1)
            return
        if len(missing) == len(ids):
            QMessageBox.information(self, "Sous-titres", "Aucun extrait de la timeline n'est transcrit.\n\n"
                                    "Sélectionnez le média, ouvrez l'onglet « Transcription » à gauche et "
                                    "cliquez sur « Transcrire ».")
            self.left_tabs.setCurrentIndex(1)
            return
        if self.tl.subtitles.cues and QMessageBox.question(
                self, "Sous-titres", "Remplacer les sous-titres actuels (et vos corrections) ?") != QMessageBox.Yes:
            return
        self.tl.subtitles.cues = generate(self.tl, {i: t for i, t in trs.items() if t is not None})
        self.tl.subtitles.enabled = True
        self._changed(panel=True)
        msg = f"{len(self.tl.subtitles.cues)} sous-titres générés."
        if missing:
            msg += "\n\nNon transcrits (pas de sous-titres sur ces extraits) : " + ", ".join(sorted(set(missing)))
            QMessageBox.information(self, "Sous-titres", msg)
        self.save_lbl.setText(msg.split("\n")[0])

    def _select_cue(self, cue_id) -> None:
        self.panel.setCurrentIndex(3)
        for i in range(self.panel.cue_list.count()):
            if self.panel.cue_list.item(i).data(Qt.UserRole) == cue_id:
                self.panel.cue_list.setCurrentRow(i)

    def split(self) -> None:
        if self.tl.split_at(self.mplayer.t) is None:
            self.save_lbl.setText("Impossible de couper si près d'une coupe")
            return
        self._changed(panel=True)

    def _delete_selected(self) -> None:
        if self.panel.sel_segment:
            self.panel._seg_del()
        elif self.panel.sel_text:
            self.panel._del_text()

    # -- annuler / rétablir ------------------------------------------------------------
    def _snapshot(self) -> None:
        s = json.dumps(self.tl.to_dict(), sort_keys=True)
        if not self._history or self._history[-1] != s:
            self._history.append(s)
            self._history = self._history[-HISTORY_MAX:]
            self._future.clear()
        self.undo_btn.setEnabled(len(self._history) > 1)
        self.redo_btn.setEnabled(bool(self._future))

    def _restore(self, s: str) -> None:
        self.tl = Timeline.from_dict(json.loads(s))
        self._push_views(panel=True)
        self._save_timer.start()

    def undo(self) -> None:
        self._snapshot()
        if len(self._history) > 1:
            self._future.append(self._history.pop())
            self._restore(self._history[-1])

    def redo(self) -> None:
        if self._future:
            s = self._future.pop()
            self._history.append(s)
            self._restore(s)

    # -- sauvegarde automatique ----------------------------------------------------------
    def flush(self) -> None:
        if self._save_timer.isActive():
            self._save_timer.stop()
        self._autosave()

    def _autosave(self) -> None:
        if not self.project_id:
            return
        data = self.app.projects.load_data(self.project_id)
        if self.asset_id:
            data.setdefault("in_out", {})[str(self.asset_id)] = [self.player.in_s, self.player.out_s]
            data["last_asset_id"] = self.asset_id
        data["timeline"] = self.tl.to_dict()
        self.app.projects.save_data(self.project_id, data)       # enregistrement automatique, sans message

    # -- réglages du concept ----------------------------------------------------------
    def _save_concept(self) -> None:
        if not self.concept_id:
            return
        self.app.montage.save_concept_defaults(self.concept_id, self.tl)
        self.save_lbl.setText("Réglages du concept mémorisés")

    def _apply_concept(self) -> None:
        d = self.app.montage.concept_defaults(self.concept_id) if self.concept_id else None
        if not d:
            QMessageBox.information(self, "Réglages du concept", "Aucun réglage n'a encore été mémorisé pour ce "
                                    "concept. Réglez un montage puis cliquez sur « Mémoriser ces réglages ».")
            return
        self.tl = apply_concept_defaults(self.tl, d)
        self._changed(panel=True)

    # -- tâches : aperçu, analyse, export ---------------------------------------------------
    def _export_still(self) -> None:
        if not self.tl.segments:
            QMessageBox.information(self, "Image", "La timeline est vide.")
            return
        t = min(self.mplayer.t, max(0.0, self.tl.duration - 0.01))
        name = (self.app.projects.get(self.project_id)["name"] if self.project_id else "image")
        path, _ = QFileDialog.getSaveFileName(self, "Enregistrer l'image",
                                              str(default_export_dir() / f"{name} - {int(t // 60):02d}m{int(t % 60):02d}s.png"),
                                              "Image PNG (*.png);;Image JPEG (*.jpg)")
        if not path:
            return
        jid = self.app.jobs.submit("export_still", "Image fixe", {"timeline": self.tl.to_dict(), "t": t, "dest": path})
        self._jobs[jid] = "still"
        self.save_lbl.setText("Enregistrement de l'image…")

    def _optimize_audio(self) -> None:
        if not self.tl.segments:
            return
        self.panel.opt_btn.setEnabled(False)
        self.panel.opt_btn.setText("Analyse en cours…")
        jid = self.app.jobs.submit("optimize_audio", "Optimisation de l'audio", {"timeline": self.tl.to_dict()})
        self._jobs[jid] = "optimize"

    def _tracks_for(self, asset_id: int) -> dict:
        lang = self.tl.voice.language if self.tl.voice.mode == "dub" else ""
        dub = self.app.dubs.track(asset_id, lang) if (lang and self.app.dubs) else None
        st = self.app.stems
        return {"dub": dub, "voice": st.voice(asset_id) if st else None, "music": st.music(asset_id) if st else None}

    def _remove_silences(self) -> None:
        if not self.tl.segments:
            QMessageBox.information(self, "Silences", "La timeline est vide.")
            return
        from PySide6.QtWidgets import QCheckBox, QComboBox, QDialog, QDialogButtonBox
        dlg = QDialog(self)
        dlg.setWindowTitle("Retirer les silences")
        dl = QVBoxLayout(dlg)
        dl.addWidget(label("Que retirer ?"))
        combo = QComboBox()
        combo.addItems(["Blancs longs (1 s et plus)", "Blancs moyens (0,6 s et plus) — conseillé",
                        "Rythme serré (0,35 s et plus)"])
        combo.setCurrentIndex(1)
        dl.addWidget(combo)
        punch = QCheckBox("Alterner plan large et plan resserré à chaque coupe (zoom 112 %)")
        punch.setToolTip("Rend les coupes dans une même prise moins visibles et plus dynamiques ; "
                         "réglable ensuite extrait par extrait (onglet Extrait → Zoom de l'extrait)")
        dl.addWidget(punch)
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.accepted.connect(dlg.accept)
        bb.rejected.connect(dlg.reject)
        dl.addWidget(bb)
        if not dlg.exec():
            return
        self._punch_in = punch.isChecked()
        min_len = {0: 1.0, 1: 0.6, 2: 0.35}[combo.currentIndex()]
        segs = [{"id": sg.id, "asset_id": sg.asset_id, "src_in": sg.src_in, "src_out": sg.src_out}
                for sg in self.tl.segments]
        jid = self.app.jobs.submit("find_silences", "Recherche des silences", {"segments": segs, "min_len": min_len})
        self._jobs[jid] = "silences"
        self.save_lbl.setText("Recherche des silences…")

    def apply_silences(self, silences: dict, punch: bool = False) -> dict:
        from ...edit import silence
        self._snapshot()
        res = silence.apply(self.tl, {k: [tuple(x) for x in v] for k, v in silences.items()})
        res["zoomed"] = silence.punch_in(self.tl) if punch else 0
        self._resync_subs()                         # sous-titres recalés sur le nouveau montage
        self._changed(panel=True)
        self._snapshot()
        return res

    def _export_project(self) -> None:
        if not self.project_id:
            return
        self.flush()
        p = self.app.projects.get(self.project_id)
        path, _ = QFileDialog.getSaveFileName(self, "Exporter le projet",
                                              str(default_export_dir() / f"{p['name']}.studioproj"),
                                              "Projet Studio Vidéo (*.studioproj)")
        if not path:
            return
        media = QMessageBox.question(self, "Exporter le projet", "Inclure les fichiers vidéo et audio dans l'archive ?"
                                     "\n\nOui : archive complète, utilisable sur un autre ordinateur.\nNon : archive "
                                     "légère, les médias devront être présents sur l'autre ordinateur.") == QMessageBox.Yes
        jid = self.app.jobs.submit("export_project", f"Archive · {p['name']}", {
            "project_id": self.project_id, "dest": path, "media": media})
        self._jobs[jid] = "archive"
        self.save_lbl.setText("Création de l'archive…")

    def _separate(self) -> None:
        from ...edit import separate
        ids = sorted({sg.asset_id for sg in self.tl.segments})
        todo = [a for a in ids if not self.app.stems.has(a) and (self.app.library.get(a) or {})["audio_tracks"]]
        if not ids:
            return
        if not todo:
            QMessageBox.information(self, "Séparer voix et musique", "Tous les médias du montage sont déjà séparés.")
            return
        if not separate.available():
            QMessageBox.warning(self, "Séparer voix et musique",
                                "Le moteur de séparation n'est pas installé dans cette version.")
            return
        if not separate.installed(self.app.paths.models):
            if QMessageBox.question(self, "Séparer voix et musique",
                                    "Le modèle de séparation (64 Mo) n'est pas encore installé. "
                                    "Ouvrir la page des modèles ?") == QMessageBox.Yes:
                self.open_models.emit()
            return
        for aid in todo:
            a = self.app.library.get(aid)
            jid = self.app.jobs.submit("separate", f"Séparation voix / musique · {a['file_name']}",
                                       {"asset_id": aid, "model": separate.installed(self.app.paths.models)[0]})
            self._jobs[jid] = "separate"
        self.panel.stems_note.setText(f"Séparation lancée pour {len(todo)} média(s). Durée : environ celle du "
                                      "média (calcul sur le processeur).")

    def apply_stems(self, asset_id: int) -> bool:
        """Après séparation : voix seule sur la piste vidéo, musique et ambiance sur la piste Musique (continue)."""
        from ...edit.model import Music
        from ...media.importer import DuplicateAsset
        mus = self.app.stems.music(asset_id)
        if mus is None or not mus.exists():
            return False
        try:
            mid = self.app.importer().import_file(mus, project_id=self.project_id, provenance="separation").asset_id
        except DuplicateAsset as exc:
            mid = exc.existing_id
        src = self.app.library.get(asset_id)
        if src:
            self.app.db.connect().execute("UPDATE source_asset SET file_name = ? WHERE id = ?",
                                          (f"Musique · {Path(src['file_name']).stem}.wav", mid))
        segs = [sg for sg in self.tl.segments if sg.asset_id == asset_id]
        if not segs:
            return False
        self._snapshot()
        start = self.tl.segment_start(self.tl.segments.index(segs[0]))
        self.tl.music = Music(asset_id=mid, volume_db=-8.0, fade_in=0.0, fade_out=1.0, start=round(start, 3),
                              src_offset=round(segs[0].src_in, 3), loop=False, duck=False)
        self.tl.voice.isolate = True
        row = self.app.library.get(mid)
        if row:
            self.assets[mid] = dict(row)
        self._changed(panel=True)
        self.save_lbl.setText("Voix isolée · musique et ambiance sur la piste Musique (−8 dB). Ctrl+Z pour annuler.")
        return True

    def _reframe(self, on: bool) -> None:
        if not self.tl.segments:
            QMessageBox.information(self, "Recadrage", "Ajoutez d'abord un extrait vidéo au montage.")
            return
        if not on:
            self._snapshot()
            for sg in self.tl.segments:
                sg.reframe = []
            self._changed(panel=True)
            self.panel.reload_reframe()
            return
        if self.tl.format.fit != "fill":
            self._snapshot()
            self.tl.format.fit = "fill"
        sel = getattr(self.panel, "sel_segment", None)
        segs = [sg for sg in self.tl.segments if sg.id == sel] or list(self.tl.segments)
        for sg in segs:
            jid = self.app.jobs.submit("reframe", "Recadrage automatique", {
                "asset_id": sg.asset_id, "src_in": sg.src_in, "src_out": sg.src_out, "segment_id": sg.id})
            self._jobs[jid] = "reframe"
        self.panel.reframe_note.setText(f"Analyse des visages : {len(segs)} extrait(s)…")

    def _detect_text(self) -> None:
        if not self.tl.segments:
            QMessageBox.information(self, "Détection", "Ajoutez d'abord un extrait vidéo au montage.")
            return
        seg = next((s for s in self.tl.segments if s.id == getattr(self.panel, "sel_segment", None)),
                   self.tl.segments[0])
        jid = self.app.jobs.submit("detect_text", "Détection du texte incrusté",
                                   {"asset_id": seg.asset_id, "start": seg.src_in, "end": seg.src_out})
        self._jobs[jid] = "detect_text"
        self.panel.detect_btn.setEnabled(False)
        self.panel.detect_btn.setText("Analyse en cours…")

    def _analyze(self) -> None:
        if not self.tl.segments:
            return
        self.panel.analysis.setText("Analyse en cours…")
        jid = self.app.jobs.submit("analyze_audio", "Analyse du son", {"timeline": self.tl.to_dict()})
        self._jobs[jid] = "analyze"

    def _export(self) -> None:
        if not self.project_id:
            return
        self.flush()
        problems = self.tl.problems(self.app.montage.asset_row)
        if problems:
            QMessageBox.warning(self, "Export impossible", "\n".join(f"• {p}" for p in problems))
            return
        proj = self.app.projects.get(self.project_id)
        dest = output_path(self.tl, proj["name"], default_export_dir())
        jid = self.app.jobs.submit("export_timeline", f"Export · {dest.name}",
                                   {"timeline": self.tl.to_dict(), "dest": str(dest), "project_id": self.project_id},
                                   critical=True)
        self._jobs[jid] = "export"
        self.save_lbl.setText(f"Export lancé : {dest.name}")

    def _resync_subs(self) -> bool:
        """Sous-titres recalculés sur le montage actuel (après une coupe ou un rognage)."""
        if not self.tl.subtitles.cues:
            return False
        from ...edit.subtitles import generate
        lang = self.tl.subtitles.language
        trs = {i: self.app.transcripts.for_language(i, lang) for i in {sg.asset_id for sg in self.tl.segments}}
        self.tl.subtitles.cues = generate(self.tl, {i: t for i, t in trs.items() if t is not None})
        return True

    def _on_timeline_drag(self, kind: str) -> None:
        resynced = kind == "segment" and self._resync_subs()
        self._changed(panel=True)
        self.save_lbl.setText(("Extrait rogné" if kind == "segment" else "Texte déplacé")
                              + (" · sous-titres recalés" if resynced else "") + ". Ctrl+Z pour annuler.")

    def _ensure_waves(self) -> None:
        """Formes d'onde des médias de la timeline : depuis le cache, sinon calculées en tâche de fond."""
        from ...media import waveform
        canvas = self.timeline.canvas
        pending = self.__dict__.setdefault("_wave_pending", {})          # id de tâche -> média
        failed = self.__dict__.setdefault("_wave_failed", set())
        for aid in {sg.asset_id for sg in self.tl.segments} - set(canvas.peaks) - set(pending.values()) - failed:
            a = self.assets.get(aid) or self.app.library.get(aid)
            if not a or a["missing"] or not a["audio_tracks"]:
                continue
            pk = waveform.load(self.app.paths.root, a["fingerprint"])
            if pk is not None:
                canvas.set_peaks(aid, pk)
            elif Path(a["path"]).exists():
                jid = self.app.jobs.submit("waveform", f"Forme d'onde · {a['file_name']}", {"asset_id": aid})
                pending[jid] = aid

    def on_job(self, job) -> None:
        """Appelé par la fenêtre principale à chaque changement d'état d'une tâche."""
        self.clip_view.on_job(job)
        self.tr_view.on_job(job)
        self.export_view.on_job(job)
        if job.kind == "waveform" and job.status in ("succeeded", "failed", "cancelled"):
            aid = getattr(self, "_wave_pending", {}).pop(job.id, None)
            if aid is not None and job.status != "succeeded":
                self._wave_failed.add(aid)                  # pas de nouvel essai pendant la session
            if aid is not None and job.status == "succeeded" and job.result.get("peaks"):
                a = self.app.library.get(aid)
                from ...media import waveform
                pk = waveform.load(self.app.paths.root, a["fingerprint"]) if a else None
                if pk is not None:
                    self.timeline.canvas.set_peaks(aid, pk)
        if getattr(self, "_orig_dlg", None) is not None:
            self._orig_dlg.on_job(job)
        if job.status in ("succeeded", "failed", "cancelled") and not self.guide.isHidden():
            QTimer.singleShot(0, self._refresh_guide)
        if job.kind in ("transcribe", "download_model", "translate", "dub", "dub_line", "diarize"):
            self.transcript.on_job(job)
        if job.kind in ("dub", "dub_line", "separate") and job.status == "succeeded":
            self.panel.reload_voice()
        if job.kind == "transcribe" and job.status == "succeeded":
            self.highlights.refresh()
        if job.kind == "find_highlights":
            self.highlights.on_job(job)
        if job.kind == "auto_clips" and job.status == "succeeded" and \
                (job.result or {}).get("project_id") == self.project_id:
            self._load_media()
            self.transcript.set_asset(self.asset_id) if self.asset_id else None
            self.highlights.refresh()
            if (job.result or {}).get("count"):
                self.left_tabs.setCurrentIndex(2)
        kind = self._jobs.get(job.id)
        if not kind or job.status in ("pending", "running"):
            return
        self._jobs.pop(job.id, None)
        if kind == "optimize":
            self.panel.opt_btn.setEnabled(True)
            self.panel.opt_btn.setText("Optimiser l'audio")
            if job.status == "succeeded":
                from ...edit.audiofix import apply_plan
                self._snapshot()
                apply_plan(self.tl, job.result)
                self._changed(panel=True)
                r = job.result
                msg = "Traitements appliqués :\n\n" + "\n".join(f"• {x}" for x in r["report"])
                if r.get("warnings"):
                    msg += "\n\n" + "\n".join(f"⚠ {w}" for w in r["warnings"])
                msg += "\n\nÉcoutez le résultat ; Ctrl+Z annule l'ensemble."
                QMessageBox.information(self, "Optimiser l'audio", msg)
            elif job.status == "failed":
                QMessageBox.warning(self, "Optimiser l'audio", job.error[:800])
        elif kind == "silences":
            if job.status == "succeeded":
                r = self.apply_silences(job.result["silences"], getattr(self, "_punch_in", False))
                src = " et ".join(k for k, v in job.result["used"].items() if v) or "aucune source"
                self.save_lbl.setText(f"{r['removed']:.1f} s de silence retirées ({r['cuts']} coupe(s), d'après "
                                      f"{src}). Ctrl+Z pour annuler.")
            elif job.status == "failed":
                QMessageBox.warning(self, "Silences", job.error[:600])
        elif kind == "still":
            if job.status == "succeeded":
                self.save_lbl.setText(f"Image enregistrée : {Path(job.result['path']).name}")
            elif job.status == "failed":
                QMessageBox.warning(self, "Image", job.error[:600])
        elif kind == "archive":
            if job.status == "succeeded":
                self.save_lbl.setText(f"Archive créée : {Path(job.result['path']).name}")
            elif job.status == "failed":
                QMessageBox.warning(self, "Archive", job.error[:600])
        elif kind == "create_original":
            if job.status == "succeeded":
                r = job.result
                self.apply_original(r["asset_id"], r.get("texts") or [])
                self.save_lbl.setText(f"Vidéo créée ({r['duration']:.0f} s) et posée sur la timeline.")
            elif job.status == "failed":
                QMessageBox.warning(self, "Création originale", job.error[:800])
                self.save_lbl.setText("")
        elif kind == "reframe":
            if job.status == "succeeded":
                r = job.result
                seg = next((sg for sg in self.tl.segments if sg.id == r.get("segment_id")), None)
                if seg is not None:
                    self._snapshot()
                    seg.reframe = [list(k) for k in r["keys"]]
                    self._changed(panel=True)
                if r.get("frames") and not r.get("with_face"):
                    self.save_lbl.setText("Recadrage : aucun visage trouvé dans un extrait, cadre centré.")
            elif job.status == "failed":
                QMessageBox.warning(self, "Recadrage automatique", job.error[:600])
            self.panel.reload_reframe()
        elif kind == "detect_text":
            self.panel.detect_btn.setEnabled(True)
            self.panel.detect_btn.setText("Détecter le texte incrusté et les logos")
            if job.status == "succeeded":
                zones = job.result.get("zones") or []
                n = self.panel.add_detected(zones)
                names = {"logo": "logo / filigrane", "subtitles": "sous-titres incrustés"}
                found = ", ".join(names.get(z["kind"], z["kind"]) for z in zones)
                QMessageBox.information(self, "Détection", (
                    f"Zone(s) proposée(s) : {found}. {n} ajoutée(s), floutée(s) par défaut.\n\nVérifiez-les avec "
                    "« Aperçu fidèle » et ajustez-les si besoin." if zones else
                    "Rien de détecté dans cet extrait. Vous pouvez placer une zone à la main."))
            elif job.status == "failed":
                QMessageBox.warning(self, "Détection", job.error[:600])
        elif kind == "separate":
            if job.status == "failed":
                QMessageBox.warning(self, "Séparer voix et musique", job.error[:800])
            elif job.status == "succeeded":
                self.apply_stems(job.result["asset_id"])
            self.panel.reload_voice()
        elif kind == "analyze":
            if job.status == "succeeded":
                self.panel.show_analysis(job.result)
            else:
                self.panel.analysis.setText(f"Analyse impossible : {job.error[:300]}")
        elif kind == "export":
            self.panel._load_history()
            if job.status == "succeeded":
                r = job.result
                msg = (f"Vidéo exportée :\n{r['path']}\n\n{r['width']}×{r['height']} · {r['duration']:.1f} s · "
                       f"{r['size_bytes'] / 2**20:.1f} Mo")
                if r.get("lufs") is not None:
                    msg += f"\nVolume : {r['lufs']:.1f} LUFS · crête {r['true_peak']:.1f} dBTP"
                if r.get("warnings"):
                    msg += "\n\n" + "\n".join(f"⚠ {w}" for w in r["warnings"])
                box = QMessageBox(QMessageBox.Information, "Export terminé", msg, parent=self)
                show = box.addButton("Afficher le fichier", QMessageBox.ActionRole)
                plan = box.addButton("Programmer la publication…", QMessageBox.ActionRole)
                box.addButton("Fermer", QMessageBox.RejectRole)
                box.exec()
                if box.clickedButton() is plan and r.get("variant_id"):
                    self.plan_publication.emit(int(r["variant_id"]))
                elif box.clickedButton() is show:
                    from .library import reveal
                    reveal(r["path"])
            elif job.status == "failed":
                QMessageBox.critical(self, "Échec de l'export", job.error[:1500])

    # -- clips (jalon 1) ------------------------------------------------------------
    def _create_clip(self, start: float, end: float) -> None:
        if not self.asset_id:
            QMessageBox.information(self, "Créer un clip", "Sélectionnez d'abord un média vidéo ou audio.")
            return
        n = self.app.db.query_one("SELECT COUNT(*) n FROM clip WHERE project_id=? AND name != 'Montage'",
                                  (self.project_id,))["n"]
        with self.app.db.transaction() as c:
            c.execute("INSERT INTO clip(project_id, source_asset_id, name, start_s, end_s) VALUES (?,?,?,?,?)",
                      (self.project_id, self.asset_id, f"Clip {n + 1}", start, end))
        self._load_clips()

    def _load_clips(self) -> None:
        self.clips.clear()
        rows = self.app.db.query("SELECT c.*, a.file_name FROM clip c LEFT JOIN source_asset a ON a.id = "
                                 "c.source_asset_id WHERE c.project_id = ? AND c.name != 'Montage' ORDER BY c.id",
                                 (self.project_id,))
        if not rows:
            it = QListWidgetItem("Aucun clip")
            it.setToolTip("Dans l'onglet Source : début (I), fin (O), puis « Créer un clip » (C).")
            it.setFlags(Qt.NoItemFlags)
            self.clips.addItem(it)
        for r in rows:
            it = QListWidgetItem(f"{r['name']}   {fmt_timecode(r['start_s'])} → {fmt_timecode(r['end_s'])}"
                                 f"   ({fmt_timecode((r['end_s'] or 0) - r['start_s'], True)})")
            it.setToolTip(r["file_name"] or "")
            it.setData(Qt.UserRole, r["id"])
            self.clips.addItem(it)
        if self.export_card.isVisibleTo(self) and self.project_id:
            self.export_panel.load(self.project_id, self.tl)
        if not self.guide.isHidden():
            self._refresh_guide()

    def _clip_id(self) -> int | None:
        it = self.clips.currentItem()
        return it.data(Qt.UserRole) if it else None

    def _rename_clip(self) -> None:
        cid = self._clip_id()
        if not cid:
            return
        cur = self.app.db.query_one("SELECT name FROM clip WHERE id=?", (cid,))["name"]
        name, ok = QInputDialog.getText(self, "Renommer le clip", "Nom :", text=cur)
        if ok and name.strip() and name.strip() != "Montage":
            self.app.db.connect().execute("UPDATE clip SET name=? WHERE id=?", (name.strip(), cid))
            self._load_clips()

    def _delete_clip(self) -> None:
        cid = self._clip_id()
        if cid and QMessageBox.question(self, "Supprimer", "Supprimer ce clip ? Le média source n'est pas modifié."
                                        ) == QMessageBox.Yes:
            self.app.db.connect().execute("DELETE FROM clip WHERE id=?", (cid,))
            self._load_clips()
