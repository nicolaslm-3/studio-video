"""Production en série : exporter plusieurs clips dans plusieurs formats avec les réglages du montage."""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QCheckBox, QDialog, QDialogButtonBox, QHBoxLayout, QListWidget, QListWidgetItem,
                               QMessageBox, QVBoxLayout)

from ..core import AppContext
from ..edit.batch import BatchOptions, plan_batch
from ..edit.model import ASPECTS, Timeline
from ..edit.render import output_path
from ..media.ffmpeg import fmt_timecode
from .widgets import button, label


class BatchDialog(QDialog):
    def __init__(self, app: AppContext, project_id: int, template: Timeline, export_dir: Path, parent=None):
        super().__init__(parent)
        self.app, self.project_id, self.template, self.export_dir = app, project_id, template, export_dir
        self.job_ids: list[int] = []
        self.setWindowTitle("Production en série")
        self.resize(640, 620)
        lay = QVBoxLayout(self)
        lay.addWidget(label("Chaque clip coché est exporté dans chaque format coché, avec les réglages du montage "
                            "actuel : cadrage, image, son, musique, style des textes et des sous-titres.", "muted",
                            wrap=True))
        top = QHBoxLayout()
        top.addWidget(label("Clips", "h2"))
        top.addStretch(1)
        top.addWidget(button("Tout cocher", "ghost", on_click=lambda: self._all(True)))
        top.addWidget(button("Tout décocher", "ghost", on_click=lambda: self._all(False)))
        lay.addLayout(top)
        self.list = QListWidget()
        rows = app.db.query("SELECT c.*, a.file_name, k.hook FROM clip c JOIN source_asset a ON a.id = c.source_asset_id "
                            "LEFT JOIN clip_candidate k ON k.id = c.candidate_id "
                            "WHERE c.project_id = ? AND c.name != 'Montage' ORDER BY c.id", (project_id,))
        for r in rows:
            hook = r["hook"] or json.loads(r["edit_json"] or "{}").get("hook") or ""
            it = QListWidgetItem(f"{r['name']}   {fmt_timecode(r['start_s'])} → {fmt_timecode(r['end_s'])}"
                                 f"   ({(r['end_s'] or 0) - r['start_s']:.0f} s) · {r['file_name']}")
            it.setFlags(it.flags() | Qt.ItemIsUserCheckable)
            it.setCheckState(Qt.Checked)
            it.setData(Qt.UserRole, {"id": r["id"], "asset_id": r["source_asset_id"], "start": r["start_s"],
                                     "end": r["end_s"], "name": r["name"], "hook": hook})
            it.setToolTip(hook)
            self.list.addItem(it)
        lay.addWidget(self.list, 1)
        lay.addWidget(label("Formats", "h2"))
        fr = QHBoxLayout()
        self.aspects: dict[str, QCheckBox] = {}
        for a in ASPECTS:
            cb = QCheckBox(a)
            cb.setChecked(a == template.format.aspect)
            self.aspects[a] = cb
            fr.addWidget(cb)
        fr.addStretch(1)
        lay.addLayout(fr)
        langs = set()
        for r in rows:
            langs.update(app.transcripts.languages(r["source_asset_id"]))
        self.langs: dict[str, QCheckBox] = {}
        if langs:
            from .dialogs import LANGUAGES
            names = dict(LANGUAGES)
            lay.addWidget(label("Langues des sous-titres", "h2"))
            lr = QHBoxLayout()
            orig = QCheckBox("Texte d'origine")
            orig.setChecked(True)
            self.langs[""] = orig
            lr.addWidget(orig)
            for c in sorted(langs):
                cb = QCheckBox(names.get(c, c))
                self.langs[c] = cb
                lr.addWidget(cb)
                cb.toggled.connect(lambda _v: self._update())
            orig.toggled.connect(lambda _v: self._update())
            lr.addStretch(1)
            lay.addLayout(lr)
        self.o_subs = QCheckBox("Sous-titres automatiques (médias transcrits)")
        self.o_subs.setChecked(True)
        self.o_hook = QCheckBox("Accroche en titre pendant les 3 premières secondes (si disponible)")
        self.o_hook.setChecked(True)
        self.o_music = QCheckBox("Garder la musique du montage")
        self.o_music.setChecked(bool(template.music.asset_id))
        self.o_music.setEnabled(bool(template.music.asset_id))
        self.o_reframe = QCheckBox("Formats vertical et carré : suivre les visages (recadrage automatique)")
        self.o_reframe.setChecked(True)
        self.o_dub = QCheckBox("Variantes traduites : utiliser la voix doublée quand un doublage existe")
        self.o_dub.setChecked(True)
        self.o_dub.setVisible(bool(self.langs))
        for w in (self.o_subs, self.o_hook, self.o_music, self.o_reframe, self.o_dub):
            lay.addWidget(w)
        self.summary = label("", "h2")
        lay.addWidget(self.summary)
        self.list.itemChanged.connect(lambda _i: self._update())
        for cb in self.aspects.values():
            cb.toggled.connect(lambda _v: self._update())
        bb = QDialogButtonBox()
        self.go = bb.addButton("Lancer les exports", QDialogButtonBox.AcceptRole)
        self.go.setProperty("kind", "primary")
        bb.addButton("Annuler", QDialogButtonBox.RejectRole)
        self.go.clicked.connect(self._launch)
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)
        if not rows:
            self.summary.setText("Aucun clip dans ce projet : créez-en depuis la Source, la transcription ou "
                                 "les Moments forts.")
        self._update()

    def _all(self, on: bool) -> None:
        for i in range(self.list.count()):
            self.list.item(i).setCheckState(Qt.Checked if on else Qt.Unchecked)

    def _clips(self) -> list[dict]:
        return [self.list.item(i).data(Qt.UserRole) for i in range(self.list.count())
                if self.list.item(i).checkState() == Qt.Checked]

    def _formats(self) -> list[str]:
        return [a for a, cb in self.aspects.items() if cb.isChecked()]

    def _languages(self) -> list[str]:
        return [c for c, cb in self.langs.items() if cb.isChecked()] if self.langs else [""]

    def _update(self) -> None:
        n = len(self._clips()) * len(self._formats()) * max(1, len(self._languages()))
        if self.list.count():
            self.summary.setText(f"{n} vidéo(s) à exporter")
        self.go.setEnabled(n > 0)

    def _launch(self) -> None:
        opts = BatchOptions(aspects=self._formats(), short_side=min(self.template.format.width,
                                                                    self.template.format.height),
                            subtitles=self.o_subs.isChecked(), hook_title=self.o_hook.isChecked(),
                            keep_music=self.o_music.isChecked(), languages=self._languages() or [""],
                            reframe=self.o_reframe.isChecked())
        clips = self._clips()
        if self.o_dub.isChecked() and self.app.dubs:
            opts.dubbed = {(c["asset_id"], lg) for c in clips for lg in self.app.dubs.languages(c["asset_id"])}
        trs = {}
        if opts.subtitles:
            for c in clips:
                for lang in opts.languages:
                    trs[(c["asset_id"], lang)] = self.app.transcripts.for_language(c["asset_id"], lang)
        style = self.app.montage.text_style_for(self.project_id)
        proj = self.app.projects.get(self.project_id)
        folder = self.export_dir / f"{proj['name']} - série {datetime.now():%Y-%m-%d %H%M}"
        missing = []
        for c, asp, tl in plan_batch(self.template, clips, opts, trs, style):
            problems = tl.problems(self.app.montage.asset_row)
            if problems:
                missing.append(f"{c['name']} ({asp}) : {problems[0]}")
                continue
            tl.export.directory = str(folder)
            tl.export.file_pattern = "{project}_{aspect}" + (f"_{tl.subtitles.language}" if tl.subtitles.language else "")
            dest = output_path(tl, c["name"], folder)
            self.job_ids.append(self.app.jobs.submit(
                "export_timeline", f"Série · {c['name']} · {asp}",
                {"timeline": tl.to_dict(), "dest": str(dest), "project_id": self.project_id}, critical=True))
        if missing:
            QMessageBox.warning(self, "Production en série", "Non exportés :\n" + "\n".join(missing[:15]))
        self.folder = folder
        self.accept()
