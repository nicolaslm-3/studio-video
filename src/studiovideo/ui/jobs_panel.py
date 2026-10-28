"""Gestionnaire de tâches visible (§17.3) : une colonne pour le média, une pour l'action en cours, une pour l'outil
(moteur, modèle, alternative) ; chaque tâche active s'arrête à tout instant (bouton carré de sa ligne)."""
from __future__ import annotations

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (QAbstractItemView, QComboBox, QHBoxLayout, QHeaderView, QMessageBox, QProgressBar,
                               QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget)

from ..core import AppContext
from .widgets import STATUS_LABELS, button, label, local_time, tool

FILTERS = [("Toutes", None), ("Actives", ("pending", "running")), ("Échouées", ("failed",)),
           ("Terminées", ("succeeded",)), ("Annulées", ("cancelled",))]
# état d'une tâche : un emoji, le mot au survol
STATUS_EMOJI = {"pending": "⏳", "running": "🔄", "succeeded": "✅", "failed": "❌", "cancelled": "⛔"}
# ce que fait chaque sorte de tâche
STEPS = {"transcribe": "Transcription", "translate": "Traduction", "translate_video": "Traduction",
         "dub": "Génération de la voix", "dub_line": "Nouvelle prise de voix", "eleven_dub": "Doublage de la voix",
         "auto_clips": "Recherche des extraits", "find_highlights": "Recherche des extraits",
         "import_file": "Import", "import_url": "Téléchargement de la vidéo", "export_timeline": "Export",
         "export_still": "Image", "separate": "Séparation voix / musique", "detect_text": "Repérage des textes",
         "publish_upload": "Publication", "match_published": "Recherche des liens publiés",
         "diarize": "Intervenants", "reframe": "Suivi de la tête",
         "make_proxy": "Préparation de l'aperçu", "waveform": "Forme d'onde", "find_silences": "Silences",
         "optimize_audio": "Son optimisé", "analyze_audio": "Analyse du son", "write_copy": "Rédaction",
         "write_script": "Rédaction du script", "create_original": "Création originale",
         "export_project": "Sauvegarde du projet", "import_project": "Ouverture d'un projet",
         "refresh_stats": "Statistiques", "connect_account": "Connexion du compte"}
# étapes de la traduction en un clic (message « EN · voix · … »)
TV_STEPS = {"voix": "génération de la voix", "traduction du texte": "traduction",
            "Transcription": "transcription", "Séparation voix / musique": "séparation voix / musique",
            "Textes d'origine": "repérage des textes", "export": "export"}
COLUMNS = ["Média", "Action", "Outil", "État", "Progression", ""]
STOP_COL = 5


def _project_name(app: AppContext, p: dict) -> str:
    pid = p.get("project_id")
    a = None
    if not pid and p.get("asset_id"):
        a = app.library.get(p["asset_id"])
        pid = a["project_id"] if a else None
    pr = app.projects.get(pid) if pid else None
    if pr:
        return pr["name"]
    return a["file_name"] if a else ""


def describe_parts(app: AppContext, j) -> tuple[str, str, str]:
    """(média, action en cours, outil utilisé) ; une tâche sans action connue : son étiquette comme action."""
    from ..ai import translate as trm
    from ..ai import tts
    from ..ai.translate import LANG_NAMES
    p = j.params or {}
    step = STEPS.get(j.kind, "")
    model = ""
    lang = LANG_NAMES.get(p.get("language") or "", "")
    if j.kind == "transcribe":
        model = "ElevenLabs Scribe v2" if p.get("engine") == "elevenlabs" else f"Whisper {p.get('model') or ''}"
    elif j.kind == "translate":
        used = (j.result or {}).get("engines") or []
        model = ", ".join(trm.ENGINES.get(e.split(":")[0], (e,))[0] for e in used) or \
            trm.ENGINES.get(p.get("engine") or "", ("Automatique",))[0]
    elif j.kind == "translate_video":
        parts = [x.strip() for x in (j.message or "").split("·") if x.strip()]
        if j.status == "running" and parts:
            code = parts[0].lower()
            if code in LANG_NAMES:
                lang = LANG_NAMES[code]
                parts = parts[1:]
            if parts and parts[0] in TV_STEPS:
                step = TV_STEPS[parts[0]].capitalize()
        else:
            lang = ", ".join(LANG_NAMES.get(x, x) for x in p.get("languages") or [])
        v = p.get("voice") or {}
        if v:
            model = f"{tts.ENGINES.get(v.get('engine'), ('ElevenLabs · doublage',))[0]} · {v.get('name', '')}"
    elif j.kind in ("dub", "dub_line"):
        model = p.get("label") or tts.ENGINES.get(p.get("engine") or "", ("",))[0]
    elif j.kind == "eleven_dub":
        model = "ElevenLabs"
    elif j.kind == "download_model":
        m = p.get("model") or ""
        step = ("Téléchargement de la voix" if m.startswith("piper:") and m not in ("piper:bin", "piper:catalog")
                else "Téléchargement du moteur de voix" if m == "piper:bin"
                else "Catalogue des voix" if m == "piper:catalog" else "Téléchargement du modèle")
        model = m.split(":", 1)[1] if ":" in m and m not in ("piper:bin", "piper:catalog") else m
        if m.startswith(("spk:", "sep:")):
            model = m[4:]
    elif j.kind in ("find_highlights", "auto_clips"):
        eng = p.get("engine") or {}
        model = eng.get("model") or ("analyse locale" if eng else "")
    elif j.kind in ("refresh_stats", "match_published"):
        model = "pages publiques (yt-dlp)"
    if not step:
        return "", j.label, ""
    return _project_name(app, p), step + (f" ({lang.lower()})" if lang else ""), model.strip()


def describe(app: AppContext, j) -> str:
    """La tâche en clair : le projet, ce qu'elle fait (l'étape en cours), puis le modèle utilisé."""
    return " · ".join(x for x in describe_parts(app, j) if x)


class JobsPanel(QWidget):
    def __init__(self, app: AppContext, parent=None):
        super().__init__(parent)
        self.app = app
        lay = QVBoxLayout(self)
        lay.setContentsMargins(16, 10, 16, 12)
        # en haut : enregistrement automatique du projet et messages brefs (import en cours…)
        self._saved, self._message = "", ""
        self.note = label("", "muted", wrap=True, hint=False)
        self.note.hide()
        lay.addWidget(self.note)
        head = QHBoxLayout()
        head.addWidget(label("Tâches", "h2"))
        self.filter = QComboBox()
        for name, val in FILTERS:
            self.filter.addItem(name, val)
        self.filter.currentIndexChanged.connect(self.refresh)
        head.addWidget(self.filter)
        head.addStretch(1)
        self.retry_btn = button("Relancer", on_click=self._retry)
        head.addWidget(self.retry_btn)
        lay.addLayout(head)
        self.table = QTableWidget(0, len(COLUMNS))
        self.table.setHorizontalHeaderLabels(COLUMNS)
        self.table.horizontalHeaderItem(3).setTextAlignment(Qt.AlignCenter)
        self.table.verticalHeader().hide()
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setShowGrid(False)
        hh = self.table.horizontalHeader()
        for c in (0, 1, 2):
            hh.setSectionResizeMode(c, QHeaderView.Stretch)
        for c, w in ((3, 48), (4, 80), (STOP_COL, 34)):
            hh.setSectionResizeMode(c, QHeaderView.Fixed)
            hh.resizeSection(c, w)
        self.table.itemSelectionChanged.connect(self._update_buttons)
        lay.addWidget(self.table)
        self._pending_refresh = QTimer(self, singleShot=True, interval=150)
        self._pending_refresh.timeout.connect(self.refresh)
        self.refresh()

    def set_saved(self, when: str) -> None:
        self._saved = when
        self._show_note()

    def set_message(self, text: str) -> None:
        self._message = text
        self._show_note()

    def _show_note(self) -> None:
        parts = ([f"✓ Enregistré à {self._saved}"] if self._saved else []) + ([self._message] if self._message else [])
        self.note.setText("  ·  ".join(parts))
        self.note.setVisible(bool(parts))

    def schedule_refresh(self, *_):
        if not self._pending_refresh.isActive():
            self._pending_refresh.start()

    def refresh(self, *_):
        sel = self._selected()
        jobs = [j for j in self.app.jobs.list(self.filter.currentData(), limit=300) if j.kind != "preview_frame"]
        self.table.setRowCount(len(jobs))
        for i, j in enumerate(jobs):
            media, step, model = describe_parts(self.app, j)
            detail = j.error or j.message or ("; ".join(j.result.get("warnings", [])) if j.result else "")
            tip = f"{describe(self.app, j)}\n{j.label}\nCréée le {local_time(j.created_at)}" + \
                (f"\n\n{detail}" if detail else "")
            for c, text in enumerate((media, step, model)):
                it = QTableWidgetItem(text)
                it.setToolTip(tip)
                if c == 0:
                    it.setData(Qt.UserRole, j.id)
                self.table.setItem(i, c, it)
            st = QTableWidgetItem(STATUS_EMOJI.get(j.status, "•"))
            st.setTextAlignment(Qt.AlignCenter)
            st.setToolTip(STATUS_LABELS[j.status] + (f" : {detail.splitlines()[0]}" if detail else ""))
            self.table.setItem(i, 3, st)
            bar = QProgressBar()
            bar.setRange(0, 100)
            bar.setValue(int(j.progress * 100))
            bar.setFixedHeight(6)
            wrap = QWidget()
            wl = QVBoxLayout(wrap)
            wl.setContentsMargins(6, 0, 6, 0)
            wl.addWidget(bar)
            self.table.setCellWidget(i, 4, wrap)
            if j.status in ("pending", "running"):
                stop = tool("square", "Arrêter cette tâche", lambda _=False, jid=j.id: self.stop(jid), size=26,
                            icon_px=14)
                self.table.setCellWidget(i, STOP_COL, stop)
            else:
                self.table.removeCellWidget(i, STOP_COL)
            if j.id == sel:
                self.table.selectRow(i)
        self._update_buttons()

    def _selected(self) -> int | None:
        r = self.table.currentRow()
        it = self.table.item(r, 0) if r >= 0 else None
        return it.data(Qt.UserRole) if it else None

    def _update_buttons(self) -> None:
        jid = self._selected()
        j = self.app.jobs.get(jid) if jid else None
        self.retry_btn.setEnabled(bool(j and j.status in ("failed", "cancelled")))

    def stop(self, jid: int, ask: bool = True) -> None:
        """Arrête la tâche tout de suite. Un export ou une publication en cours (tâches « critiques ») : après
        confirmation, le fichier ou l'envoi pouvant rester incomplet."""
        j = self.app.jobs.get(jid)
        if j is None or j.status not in ("pending", "running"):
            return
        if ask and j.critical and j.status == "running" and QMessageBox.question(
                self, "Arrêter la tâche", "Cette tâche est un export ou une publication en cours. L'arrêter maintenant "
                "peut laisser un fichier ou un envoi incomplet.\n\nL'arrêter quand même ?") != QMessageBox.Yes:
            return
        self.app.jobs.cancel(jid)

    def _retry(self) -> None:
        jid = self._selected()
        if jid:
            try:
                self.app.jobs.retry(jid)
            except ValueError as exc:
                QMessageBox.information(self, "Relancer", str(exc))
