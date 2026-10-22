"""Studio (§2.1 B) : médias du projet, lecteur source, montage, timeline, inspecteur, export."""
from __future__ import annotations

import json
from pathlib import Path

from PySide6.QtCore import QSize, QStandardPaths, Qt, QTimer, Signal
from PySide6.QtGui import QKeySequence, QPixmap, QShortcut
from PySide6.QtWidgets import (QComboBox, QHBoxLayout, QInputDialog, QListWidget, QListWidgetItem, QMessageBox,
                               QSplitter, QStackedWidget, QTabWidget, QVBoxLayout, QWidget)

from ...core import AppContext
from ...edit.model import Timeline, apply_concept_defaults
from ...edit.render import output_path
from ...media.ffmpeg import fmt_timecode
from ..importing import DropZone, import_paths, import_url, pick_and_import
from ..montage_panel import MontagePanel
from ..montage_player import MontagePlayer
from ..player import PlayerWidget
from ..timeline_view import TimelineView
from ..highlights_panel import HighlightsPanel
from ..transcript_panel import TranscriptPanel
from ..widgets import Card, EmptyState, button, label

HISTORY_MAX = 100


def default_export_dir() -> Path:
    base = QStandardPaths.writableLocation(QStandardPaths.MoviesLocation) or str(Path.home() / "Videos")
    return Path(base) / "Studio Vidéo"


class StudioPage(QWidget):
    new_project = Signal(object)
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
        root.setContentsMargins(16, 12, 16, 12)
        root.setSpacing(10)
        top = QHBoxLayout()
        top.addWidget(label("Studio", "h1"))
        top.addSpacing(16)
        self.project_combo = QComboBox()
        self.project_combo.setMinimumWidth(280)
        self.project_combo.currentIndexChanged.connect(self._on_project_combo)
        top.addWidget(self.project_combo)
        self.concept_lbl = label("", "faint")
        top.addWidget(self.concept_lbl)
        top.addStretch(1)
        self.undo_btn = button("↶", tooltip="Annuler (Ctrl+Z)", on_click=self.undo)
        self.redo_btn = button("↷", tooltip="Rétablir (Ctrl+Y)", on_click=self.redo)
        top.addWidget(self.undo_btn)
        top.addWidget(self.redo_btn)
        self.save_lbl = label("", "faint")
        top.addWidget(self.save_lbl)
        top.addWidget(button("Nouveau projet", on_click=lambda: self.new_project.emit(None)))
        root.addLayout(top)
        from ..guide import GuideBar
        self.guide = GuideBar()
        self.guide.hide()
        root.addWidget(self.guide)

        self.stack = QStackedWidget()
        root.addWidget(self.stack, 1)
        self.stack.addWidget(EmptyState("Aucun projet ouvert",
                                        "Créez un projet ou ouvrez-en un depuis le tableau de bord pour importer "
                                        "des médias et commencer le montage.", "Nouveau projet",
                                        lambda: self.new_project.emit(None)))

        vsplit = QSplitter(Qt.Vertical)
        hsplit = QSplitter(Qt.Horizontal)

        # -- colonne médias et clips
        left = QWidget()
        ll = QVBoxLayout(left)
        ll.setContentsMargins(0, 0, 0, 0)
        ll.addWidget(label("Médias du projet", "h2"))
        self.media_list = QListWidget()
        self.media_list.setIconSize(QSize(64, 36))
        self.media_list.currentItemChanged.connect(self._on_media)
        self.media_list.itemDoubleClicked.connect(lambda _it: self._add_media_to_timeline())
        ll.addWidget(self.media_list, 2)
        dz = DropZone("Déposez des fichiers ici")
        dz.setMinimumHeight(64)
        dz.dropped.connect(lambda p: import_paths(self.app, self, p, self.project_id))
        ll.addWidget(dz)
        hb = QHBoxLayout()
        hb.addWidget(button("Importer…", on_click=lambda: pick_and_import(self.app, self, self.project_id)))
        hb.addWidget(button("URL…", on_click=lambda: import_url(self.app, self, self.project_id)))
        hb.addWidget(button("+ Biblio.", "ghost", tooltip="Ajouter un média de la bibliothèque",
                            on_click=self._attach_from_library))
        ll.addLayout(hb)
        ll.addWidget(label("Clips", "h2"))
        self.clips = QListWidget()
        self.clips.itemDoubleClicked.connect(self._clip_to_timeline)
        ll.addWidget(self.clips, 1)
        ch = QHBoxLayout()
        ch.addWidget(button("→ Timeline", tooltip="Ajouter le clip à la fin de la timeline",
                            on_click=lambda: self._clip_to_timeline(self.clips.currentItem())))
        ch.addWidget(button("Renommer", "ghost", on_click=self._rename_clip))
        ch.addWidget(button("✕", "ghost", tooltip="Supprimer le clip", on_click=self._delete_clip))
        ll.addLayout(ch)
        self.left_tabs = QTabWidget()
        self.left_tabs.setDocumentMode(True)
        self.left_tabs.addTab(left, "Médias")
        self.transcript = TranscriptPanel(app)
        self.transcript.seek.connect(self._seek_source)
        self.transcript.make_clip.connect(self._create_clip)
        self.transcript.add_to_timeline.connect(self._add_range_to_timeline)
        self.transcript.open_models.connect(self.open_models.emit)
        self.transcript.text_edit.connect(self._text_edit_ranges)
        self.left_tabs.addTab(self.transcript, "Transcription")
        self.highlights = HighlightsPanel(app)
        self.highlights.play_range.connect(self._play_source_range)
        self.highlights.clips_created.connect(self._load_clips)
        self.highlights.add_to_timeline.connect(self._add_ranges)
        self.highlights.open_settings.connect(self.open_ai_settings.emit)
        self.left_tabs.addTab(self.highlights, "Moments forts")
        hsplit.addWidget(self.left_tabs)

        # -- lecteurs
        self.viewer_tabs = QTabWidget()
        mc = Card(padding=10)
        self.mplayer = MontagePlayer()
        self.mplayer.track_resolver = self._tracks_for
        self.mplayer.time_changed.connect(self._on_time)
        self.mplayer.render_requested.connect(self._request_preview)
        mc.lay.addWidget(self.mplayer)
        self.viewer_tabs.addTab(mc, "Montage")
        sc = Card(padding=10)
        self.player = PlayerWidget()
        self.player.create_clip.connect(self._create_clip)
        self.player.in_out_changed.connect(lambda *_: self._save_timer.start())
        self.player.position_changed.connect(lambda t: self.transcript.highlight_time(t))
        sc.lay.addWidget(self.player)
        srow = QHBoxLayout()
        srow.addStretch(1)
        srow.addWidget(button("Ajouter à la timeline (A)", "primary",
                              tooltip="Ajoute la sélection (ou tout le média) à la fin de la timeline (A)",
                              on_click=self._add_media_to_timeline))
        sc.lay.addLayout(srow)
        self.viewer_tabs.addTab(sc, "Source")
        self.viewer_tabs.currentChanged.connect(self._viewer_changed)
        hsplit.addWidget(self.viewer_tabs)

        # -- inspecteur
        self.panel = MontagePanel(app)
        self.panel.changed.connect(self._on_panel_change)
        self.panel.split_requested.connect(self.split)
        self.panel.analyze_requested.connect(self._analyze)
        self.panel.export_requested.connect(self._export)
        self.panel.save_concept_requested.connect(self._save_concept)
        self.panel.apply_concept_requested.connect(self._apply_concept)
        self.panel.generate_subs_requested.connect(self._generate_subs)
        self.panel.optimize_requested.connect(self._optimize_audio)
        self.panel.separate_requested.connect(self._separate)
        self.panel.detect_text_requested.connect(self._detect_text)
        self.panel.reframe_requested.connect(self._reframe)
        self.panel.cue_selected.connect(lambda t: self.mplayer.seek(t))
        hsplit.addWidget(self.panel)
        hsplit.setStretchFactor(0, 1)
        hsplit.setStretchFactor(1, 3)
        hsplit.setStretchFactor(2, 1)
        hsplit.setSizes([230, 760, 410])
        vsplit.addWidget(hsplit)

        # -- timeline
        tw = QWidget()
        tl_lay = QVBoxLayout(tw)
        tl_lay.setContentsMargins(0, 4, 0, 0)
        bar = QHBoxLayout()
        bar.addWidget(label("Timeline", "h2"))
        bar.addSpacing(12)
        bar.addWidget(button("Couper (S)", tooltip="Couper l'extrait à la tête de lecture", on_click=self.split))
        bar.addWidget(button("Supprimer l'extrait", "ghost", tooltip="Suppr", on_click=self._delete_selected))
        bar.addWidget(button("Ajuster", "ghost", tooltip="Afficher toute la timeline", on_click=lambda: self.timeline.fit()))
        bar.addStretch(1)
        bar.addWidget(button("Production en série…", tooltip="Exporter plusieurs clips dans plusieurs formats",
                             on_click=self._batch))
        self.export_quick = button("Exporter", "primary", tooltip="Exporter la vidéo (Ctrl+E)", on_click=self._export)
        bar.addWidget(self.export_quick)
        tl_lay.addLayout(bar)
        self.timeline = TimelineView()
        c = self.timeline.canvas
        c.seek.connect(lambda t: self.mplayer.seek(t))
        c.segment_selected.connect(self._select_segment)
        c.text_selected.connect(self._select_text)
        c.music_selected.connect(lambda: self.panel.setCurrentIndex(4))
        c.cue_selected.connect(self._select_cue)
        tl_lay.addWidget(self.timeline)
        vsplit.addWidget(tw)
        vsplit.setStretchFactor(0, 4)
        vsplit.setStretchFactor(1, 1)
        self.stack.addWidget(vsplit)

        for key, fn in (("Ctrl+Z", self.undo), ("Ctrl+Y", self.redo), ("Ctrl+Shift+Z", self.redo),
                        ("S", self.split), ("Delete", self._delete_selected), ("A", self._add_media_to_timeline),
                        ("Ctrl+E", self._export)):
            sc_ = QShortcut(QKeySequence(key), self)
            sc_.setContext(Qt.WidgetWithChildrenShortcut)
            sc_.activated.connect(fn)
        self.refresh()

    # -- projet -------------------------------------------------------------
    def refresh(self) -> None:
        self.project_combo.blockSignals(True)
        self.project_combo.clear()
        for r in self.app.projects.recent(200):
            self.project_combo.addItem(f"{r['name']}  ·  {r['concept_name']}", r["id"])
        if self.project_id:
            self.project_combo.setCurrentIndex(max(0, self.project_combo.findData(self.project_id)))
        self.project_combo.setVisible(self.project_combo.count() > 0)
        self.project_combo.blockSignals(False)
        self.stack.setCurrentIndex(1 if self.project_id else 0)
        if self.project_id:
            self._load_media()
            self._load_clips()
            self._push_views(panel=True)
        self._refresh_guide()

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

    def _refresh_guide(self) -> None:
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
        elif mode == "campaign":
            b = self.app.campaigns.brief(self.project_id)
            steps, title = self._campaign_steps(), f"Campagne · {b.name}" if b.defined else "Campagne"
            if b.defined:
                extras = [("Brief…", self._edit_brief), ("Conformité…", self._compliance)]
        if not steps:
            self.guide.hide()
            return
        self.guide.set_steps(title, steps, extras)
        self.guide.show()

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
            Step("export", "Exporter", exported, self._export if on_tl else None, "Un MP4 prêt à publier"),
        ]

    def _original_dialog(self) -> None:
        from ..original_dialog import OriginalDialog
        self._orig_dlg = OriginalDialog(self.app, self.project_id, self.tl.format.width, self.tl.format.height, self)
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

    def _edit_brief(self) -> None:
        from ..campaign_dialog import BriefDialog
        if BriefDialog(self.app, self.project_id, self).exec():
            self._refresh_guide()

    def _compliance(self) -> None:
        from ..campaign_dialog import ComplianceDialog
        ComplianceDialog(self.app, self.project_id, self).exec()

    def _campaign_steps(self):
        from ..guide import Step
        b = self.app.campaigns.brief(self.project_id)
        vids = self._project_videos()
        n_clips = self.app.db.query_one("SELECT COUNT(*) n FROM clip WHERE project_id = ? AND name != 'Montage'",
                                        (self.project_id,))["n"]
        rep = self.app.campaigns.report(self.project_id) if b.defined else []
        target = max(1, b.target_count)
        conform = [r for r in rep if r["summary"].startswith("✓")]
        variants = [r["variant_id"] for r in rep]
        published = 0
        if variants:
            q = ",".join("?" * len(variants))
            published = self.app.db.query_one(f"SELECT COUNT(DISTINCT variant_id) n FROM master_publication "
                                              f"WHERE variant_id IN ({q})", tuple(variants))["n"]
        nxt_pub = next((r["variant_id"] for r in conform if not self.app.db.query_one(
            "SELECT 1 FROM master_publication WHERE variant_id = ?", (r["variant_id"],))), None)

        def clips():
            aid = self._guide_asset()
            if aid and self.app.transcripts.has(aid):
                self._select_asset(aid)
                self.left_tabs.setCurrentIndex(2)
            else:
                self.left_tabs.setCurrentIndex(1 if aid else 0)
        return [
            Step("brief", "Brief", b.defined, self._edit_brief, "Exigences du client : formats, durées, mots, mentions"),
            Step("import", "Importer", bool(vids), lambda: pick_and_import(self.app, self, self.project_id),
                 "Vidéo(s) source"),
            Step("clips", f"Clips ({n_clips})", n_clips > 0, clips,
                 "Moments forts ou sélection manuelle dans la source"),
            Step("export", f"Exporter ({len(rep)}/{target})", len(rep) >= target, self._batch,
                 "Production en série avec les réglages du montage"),
            Step("check", f"Conformité ({len(conform)}/{len(rep)})", bool(rep) and len(conform) == len(rep),
                 self._compliance, "Contrôle automatique de chaque vidéo d'après le brief"),
            Step("publish", f"Publier ({published}/{len(conform)})", bool(conform) and published >= len(conform),
                 (lambda: self.plan_publication.emit(nxt_pub)) if nxt_pub else None,
                 "Préparer la publication des vidéos conformes"),
        ]

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
            Step("export", "Exporter", exported, self._export if built else None, "Un MP4 prêt à publier"),
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

    def open_project(self, project_id: int) -> None:
        self.flush()
        self.player.unload()
        self.mplayer.stop()
        self.project_id = project_id
        self.asset_id = None
        p = self.app.projects.get(project_id)
        self.concept_id = p["concept_id"] if p else None
        self.concept_lbl.setText(f"Concept : {p['concept_name']}" if p else "")
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
        QTimer.singleShot(50, self.timeline.fit)

    def _on_project_combo(self) -> None:
        pid = self.project_combo.currentData()
        if pid and pid != self.project_id:
            self.open_project(pid)

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
            if a["project_id"] != self.project_id:
                continue
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

    def _attach_from_library(self) -> None:
        rows = self.app.db.query("SELECT id, file_name FROM source_asset WHERE (project_id IS NULL OR project_id != ?) "
                                 "AND kind IN ('video','audio') AND missing = 0 ORDER BY imported_at DESC LIMIT 300",
                                 (self.project_id,))
        if not rows:
            QMessageBox.information(self, "Bibliothèque", "Aucun autre média dans la bibliothèque.")
            return
        names = [f"{r['file_name']}  [#{r['id']}]" for r in rows]
        choice, ok = QInputDialog.getItem(self, "Ajouter depuis la bibliothèque", "Média :", names, 0, False)
        if ok:
            aid = rows[names.index(choice)]["id"]
            self.app.db.connect().execute("UPDATE source_asset SET project_id = ? WHERE id = ?", (self.project_id, aid))
            self._load_media()

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
        self.timeline.set_timeline(self.tl, names, music["file_name"] if music else "")
        self.mplayer.set_timeline(self.tl, self.assets)
        self.export_quick.setEnabled(bool(self.tl.segments))
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
        self.app.projects.save_data(self.project_id, data)
        self.save_lbl.setText("Enregistré")

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
    def _request_preview(self, t: float) -> None:
        if not self.tl.segments:
            return
        for jid, kind in list(self._jobs.items()):
            if kind == "preview":
                self.app.jobs.cancel(jid)
        jid = self.app.jobs.submit("preview_frame", "Aperçu", {"timeline": self.tl.to_dict(), "t": t,
                                                              "project_id": self.project_id})
        self._jobs[jid] = "preview"

    def _batch(self) -> None:
        if not self.project_id:
            return
        from ..batch_dialog import BatchDialog
        self.flush()
        dlg = BatchDialog(self.app, self.project_id, self.tl, default_export_dir(), self)
        if dlg.exec() and dlg.job_ids:
            self.save_lbl.setText(f"{len(dlg.job_ids)} export(s) en file · dossier « {dlg.folder.name} »")

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

    def on_job(self, job) -> None:
        """Appelé par la fenêtre principale à chaque changement d'état d'une tâche."""
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
        kind = self._jobs.get(job.id)
        if not kind or job.status in ("pending", "running"):
            return
        self._jobs.pop(job.id, None)
        if kind == "preview":
            if job.status == "succeeded":
                self.mplayer.show_render(job.result["path"])
            elif job.status == "failed":
                self.mplayer.render_failed(job.error)
        elif kind == "optimize":
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
            it = QListWidgetItem("Aucun clip. Dans l'onglet Source : début (I), fin (O), puis « Créer un clip » (C).")
            it.setFlags(Qt.NoItemFlags)
            self.clips.addItem(it)
        for r in rows:
            it = QListWidgetItem(f"{r['name']}   {fmt_timecode(r['start_s'])} → {fmt_timecode(r['end_s'])}"
                                 f"   ({fmt_timecode((r['end_s'] or 0) - r['start_s'], True)})")
            it.setToolTip(r["file_name"] or "")
            it.setData(Qt.UserRole, r["id"])
            self.clips.addItem(it)

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
