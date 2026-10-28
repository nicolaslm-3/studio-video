"""Gestionnaire de tâches visible (§17.3)."""
from __future__ import annotations

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (QAbstractItemView, QComboBox, QHBoxLayout, QHeaderView, QProgressBar, QTableWidget,
                               QTableWidgetItem, QVBoxLayout, QWidget)

from ..core import AppContext
from .widgets import STATUS_LABELS, button, label, local_time

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


def _project_name(app: AppContext, p: dict) -> str:
    pid = p.get("project_id")
    if not pid and p.get("asset_id"):
        a = app.library.get(p["asset_id"])
        pid = a["project_id"] if a else None
    pr = app.projects.get(pid) if pid else None
    return pr["name"] if pr else ""


def describe(app: AppContext, j) -> str:
    """La tâche en clair : le projet, ce qu'elle fait (l'étape en cours), puis le modèle utilisé."""
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
    if not step:
        return j.label
    parts = [_project_name(app, p), step + (f" ({lang.lower()})" if lang else ""), model]
    return " · ".join(x for x in parts if x)


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
        self.cancel_btn = button("Annuler", on_click=self._cancel)
        self.retry_btn = button("Relancer", on_click=self._retry)
        head.addWidget(self.cancel_btn)
        head.addWidget(self.retry_btn)
        lay.addLayout(head)
        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(["Tâche", "État", "Progression", "Détail"])
        self.table.horizontalHeaderItem(1).setTextAlignment(Qt.AlignCenter)
        self.table.verticalHeader().hide()
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setShowGrid(False)
        hh = self.table.horizontalHeader()
        hh.setSectionResizeMode(0, QHeaderView.Stretch)
        hh.setSectionResizeMode(3, QHeaderView.Interactive)       # la tâche d'abord, le détail au survol
        hh.resizeSection(3, 190)
        hh.setSectionResizeMode(1, QHeaderView.Fixed)
        hh.setSectionResizeMode(2, QHeaderView.Fixed)
        hh.resizeSection(1, 48)
        hh.resizeSection(2, 80)
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
            text = describe(self.app, j)
            it = QTableWidgetItem(text)
            it.setData(Qt.UserRole, j.id)
            it.setToolTip(f"{text}\n{j.label}\nCréée le {local_time(j.created_at)}")
            self.table.setItem(i, 0, it)
            st = QTableWidgetItem(STATUS_EMOJI.get(j.status, "•"))
            st.setTextAlignment(Qt.AlignCenter)
            st.setToolTip(STATUS_LABELS[j.status])
            self.table.setItem(i, 1, st)
            bar = QProgressBar()
            bar.setRange(0, 100)
            bar.setValue(int(j.progress * 100))
            bar.setFixedHeight(6)
            wrap = QWidget()
            wl = QVBoxLayout(wrap)
            wl.setContentsMargins(6, 0, 6, 0)
            wl.addWidget(bar)
            self.table.setCellWidget(i, 2, wrap)
            detail = j.error or j.message or ("; ".join(j.result.get("warnings", [])) if j.result else "")
            d = QTableWidgetItem(detail.splitlines()[0] if detail else "")
            d.setToolTip(detail)
            self.table.setItem(i, 3, d)
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
        self.cancel_btn.setEnabled(bool(j and j.status in ("pending", "running")))
        self.retry_btn.setEnabled(bool(j and j.status in ("failed", "cancelled")))

    def _cancel(self) -> None:
        jid = self._selected()
        if jid:
            self.app.jobs.cancel(jid)

    def _retry(self) -> None:
        jid = self._selected()
        if jid:
            try:
                self.app.jobs.retry(jid)
            except ValueError:
                pass
