"""Export des clips (espace Clipping intelligent) : chaque clip coché, dans chaque format coché, avec les
réglages du montage."""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (QCheckBox, QHBoxLayout, QListWidget, QListWidgetItem, QMessageBox,
                               QVBoxLayout, QWidget)

from ..core import AppContext
from ..edit.batch import BatchOptions, plan_batch
from ..edit.model import ASPECTS, Timeline
from ..edit.render import output_path
from ..media.ffmpeg import fmt_timecode
from .widgets import button, label

def launch_exports(app: AppContext, project_id: int, template: Timeline, clips: list[dict], opts: BatchOptions,
                   export_dir: Path) -> tuple[list[int], list[str], Path, dict[int, dict]]:
    """Met en file l'export de chaque clip (× formats × langues) avec les réglages du modèle.

    Renvoie (tâches, clips non exportables, dossier, {tâche: clip})."""
    trs = {}
    if opts.subtitles:
        for c in clips:
            for lang in opts.languages:
                trs[(c["asset_id"], lang)] = app.transcripts.for_language(c["asset_id"], lang)
    style = app.montage.text_style_for(project_id)
    proj = app.projects.get(project_id)
    folder = export_dir / f"{proj['name']} - clips {datetime.now():%Y-%m-%d %H%M}"
    missing, job_ids, jobs = [], [], {}
    for c, asp, tl in plan_batch(template, clips, opts, trs, style):
        problems = tl.problems(app.montage.asset_row)
        if problems:
            missing.append(f"{c['name']} ({asp}) : {problems[0]}")
            continue
        tl.export.directory = str(folder)
        tl.export.file_pattern = "{project}_{aspect}" + (f"_{tl.subtitles.language}" if tl.subtitles.language else "")
        dest = output_path(tl, c["name"], folder)
        params = {"timeline": tl.to_dict(), "dest": str(dest), "project_id": project_id}
        if c.get("caption"):
            params["caption"] = c["caption"]
        jid = app.jobs.submit("export_timeline", f"Clip · {c['name']} · {asp}", params, critical=True)
        job_ids.append(jid)
        jobs[jid] = c
    return job_ids, missing, folder, jobs


ASPECT_NAMES = {"9:16": "Vertical", "16:9": "Horizontal", "1:1": "Carré", "4:5": "Portrait"}


class ClipExportPanel(QWidget):
    """Clips cochés × formats cochés × langues cochées → une file d'exports."""
    launched = Signal()

    def __init__(self, app: AppContext, export_dir: Path, parent=None):
        super().__init__(parent)
        self.app, self.export_dir = app, export_dir
        self.project_id: int | None = None
        self.template = Timeline()
        self.template_fn = None               # modèle courant fourni par le Studio (annuler remplace la timeline)
        self.job_ids: list[int] = []
        self.folder: Path | None = None
        self._rows = []
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(6)
        top = QHBoxLayout()
        self.title = label("Clips", "h2")
        top.addWidget(self.title)
        top.addStretch(1)
        top.addWidget(button("Tout", "ghost", "Tout cocher", lambda: self._all(True)))
        top.addWidget(button("Aucun", "ghost", "Tout décocher", lambda: self._all(False)))
        lay.addLayout(top)
        self.list = QListWidget()
        self.list.setToolTip("Chaque clip coché est exporté dans chaque format coché, avec les réglages du montage : "
                             "cadrage, image, son, musique, style des textes et des sous-titres.")
        lay.addWidget(self.list, 1)
        lay.addWidget(label("Formats", "h2"))
        fr = QHBoxLayout()
        self.aspects: dict[str, QCheckBox] = {}
        for a in ASPECTS:
            cb = QCheckBox(ASPECT_NAMES.get(a, a))
            cb.setToolTip(a)
            self.aspects[a] = cb
            fr.addWidget(cb)
            cb.toggled.connect(lambda _v: self._update())
        fr.addStretch(1)
        lay.addLayout(fr)
        self.langs_title = label("Langues des sous-titres", "h2")
        lay.addWidget(self.langs_title)
        self.langs_row = QHBoxLayout()
        lay.addLayout(self.langs_row)
        self.langs: dict[str, QCheckBox] = {}
        self.o_subs = QCheckBox("Sous-titres automatiques")
        self.o_subs.setToolTip("Pour les médias transcrits")
        self.o_hook = QCheckBox("Accroche en titre (3 premières secondes)")
        self.o_music = QCheckBox("Garder la musique du montage")
        self.o_reframe = QCheckBox("Suivre les visages (vertical et carré)")
        self.o_dub = QCheckBox("Voix doublée pour les variantes traduites")
        for w in (self.o_subs, self.o_hook, self.o_reframe):
            w.setChecked(True)
        for w in (self.o_subs, self.o_hook, self.o_music, self.o_reframe, self.o_dub):
            lay.addWidget(w)
        self.summary = label("", "h2")
        lay.addWidget(self.summary)
        self.go = button("Lancer les exports", "primary", on_click=self._launch, icon="share-2")
        lay.addWidget(self.go)
        self.list.itemChanged.connect(lambda _i: self._update())

    def load(self, project_id: int, template: Timeline) -> None:
        """(Re)charge les clips du projet ; les choix déjà faits sont conservés pour le même projet."""
        same = project_id == self.project_id
        unchecked = {self.list.item(i).data(Qt.UserRole)["id"] for i in range(self.list.count())
                     if self.list.item(i).checkState() != Qt.Checked} if same else set()
        prev_langs = {c for c, cb in self.langs.items() if cb.isChecked()} if same else {""}
        self.project_id, self.template = project_id, template
        rows = self.app.db.query("SELECT c.*, a.file_name, k.hook FROM clip c JOIN source_asset a ON a.id = "
                                 "c.source_asset_id LEFT JOIN clip_candidate k ON k.id = c.candidate_id "
                                 "WHERE c.project_id = ? AND c.name != 'Montage' ORDER BY c.id", (project_id,))
        self._rows = rows
        self.list.blockSignals(True)
        self.list.clear()
        for r in rows:
            hook = r["hook"] or json.loads(r["edit_json"] or "{}").get("hook") or ""
            it = QListWidgetItem(f"{r['name']}   {fmt_timecode(r['start_s'])} → {fmt_timecode(r['end_s'])}"
                                 f"   ({(r['end_s'] or 0) - r['start_s']:.0f} s)")
            it.setFlags(it.flags() | Qt.ItemIsUserCheckable)
            it.setCheckState(Qt.Unchecked if r["id"] in unchecked else Qt.Checked)
            it.setData(Qt.UserRole, {"id": r["id"], "asset_id": r["source_asset_id"], "start": r["start_s"],
                                     "end": r["end_s"], "name": r["name"], "hook": hook})
            it.setToolTip(f"{r['file_name']}\n{hook}".strip())
            self.list.addItem(it)
        self.list.blockSignals(False)
        if not same:
            for a, cb in self.aspects.items():
                cb.blockSignals(True)
                cb.setChecked(a == template.format.aspect)
                cb.blockSignals(False)
        self._load_langs(rows, prev_langs)
        self.o_music.setChecked(bool(template.music.asset_id))
        self.o_music.setEnabled(bool(template.music.asset_id))
        self.title.setText(f"Clips ({len(rows)})")
        self._update()

    def _load_langs(self, rows, checked: set[str]) -> None:
        while self.langs_row.count():
            w = self.langs_row.takeAt(0).widget()
            if w:
                w.hide()                  # sinon son affichage différé en fait une fenêtre
                w.setParent(None)
                w.deleteLater()
        self.langs = {}
        langs = set()
        for r in rows:
            langs.update(self.app.transcripts.languages(r["source_asset_id"]))
        self.langs_title.setVisible(bool(langs))
        self.o_dub.setVisible(bool(langs))
        self.o_dub.setChecked(True)
        if not langs:
            return
        from .dialogs import LANGUAGES
        names = dict(LANGUAGES)
        for c in [""] + sorted(langs):
            cb = QCheckBox(names.get(c, c) if c else "Texte d'origine")
            cb.setChecked(c in checked)
            cb.toggled.connect(lambda _v: self._update())
            self.langs[c] = cb
            self.langs_row.addWidget(cb)
        self.langs_row.addStretch(1)

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

    def count(self) -> int:
        return len(self._clips()) * len(self._formats()) * max(1, len(self._languages()))

    def _update(self) -> None:
        n = self.count()
        if self.list.count():
            self.summary.setText(f"{n} vidéo(s) à exporter")
        else:
            self.summary.setText("Aucun clip pour l'instant")
            self.summary.setToolTip("Créez des clips depuis la Source (I, O puis C), la transcription ou les "
                                    "Extraits.")
        self.go.setEnabled(n > 0)

    def _launch(self) -> None:
        if not self.project_id:
            return
        if self.template_fn is not None:
            self.template = self.template_fn()
        opts = BatchOptions(aspects=self._formats(), short_side=min(self.template.format.width,
                                                                    self.template.format.height),
                            subtitles=self.o_subs.isChecked(), hook_title=self.o_hook.isChecked(),
                            keep_music=self.o_music.isChecked(), languages=self._languages() or [""],
                            reframe=self.o_reframe.isChecked())
        clips = self._clips()
        if self.o_dub.isChecked() and self.app.dubs:
            opts.dubbed = {(c["asset_id"], lg) for c in clips for lg in self.app.dubs.languages(c["asset_id"])}
        self.job_ids, missing, folder, _jobs = launch_exports(self.app, self.project_id, self.template, clips, opts,
                                                              self.export_dir)
        if missing:
            QMessageBox.warning(self, "Export des clips", "Non exportés :\n" + "\n".join(missing[:15]))
        self.folder = folder
        self.launched.emit()

