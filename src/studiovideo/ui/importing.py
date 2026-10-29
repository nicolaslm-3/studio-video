"""Actions d'import partagées (fichiers, glisser-déposer, URL)."""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QFileDialog, QFrame, QInputDialog, QMessageBox, QVBoxLayout, QWidget

from ..core import AppContext
from ..media import downloader, ffmpeg
from ..media.importer import fingerprint, guess_kind
from .widgets import label
from .widgets import ask

FILE_FILTER = ("Médias et documents (*.mp4 *.mov *.mkv *.webm *.avi *.m4v *.mts *.wmv *.mp3 *.wav *.m4a "
               "*.aac *.flac *.ogg *.opus *.png *.jpg *.jpeg *.webp *.pdf *.txt *.docx *.pptx *.srt *.vtt);;"
               "Tous les fichiers (*)")


def import_paths(app: AppContext, parent: QWidget, paths: list[str], project_id: int | None = None,
                 extra: dict | None = None) -> int:
    """Soumet une tâche d'import par fichier. Demande confirmation pour les doublons.

    extra : paramètres ajoutés à chaque tâche (ex. {"auto_clips": True} : extraits cherchés d'office)."""
    if not ffmpeg.available():
        QMessageBox.critical(parent, "Import", "FFmpeg est introuvable : l'analyse des médias est impossible.")
        return 0
    importer = app.importer()
    n = 0
    for p in paths:
        path = Path(p)
        if path.is_dir():
            n += import_paths(app, parent, [str(x) for x in sorted(path.iterdir()) if x.is_file()], project_id,
                              extra)
            continue
        if not path.is_file():
            continue
        allow = False
        try:
            dup = importer.find_duplicate(fingerprint(path))
        except OSError as exc:
            QMessageBox.warning(parent, "Import", f"Lecture impossible : {path.name}\n{exc}")
            continue
        if dup:
            if not ask(parent, "Média déjà importé",
                       f"« {path.name} » est déjà dans Médias.\nL'importer une seconde fois ?"):
                continue
            allow = True
        app.jobs.submit("import_file", f"Import · {path.name}",
                        {"path": str(path), "project_id": project_id, "allow_duplicate": allow, **(extra or {})})
        n += 1
    return n


def pick_and_import(app: AppContext, parent: QWidget, project_id: int | None = None, extra: dict | None = None,
                    file_filter: str = FILE_FILTER) -> int:
    files, _ = QFileDialog.getOpenFileNames(parent, "Importer des médias", "", file_filter)
    return import_paths(app, parent, files, project_id, extra) if files else 0


def import_url(app: AppContext, parent: QWidget, project_id: int | None = None, url: str | None = None,
               extra: dict | None = None) -> bool:
    """Téléchargement par lien ; sans `url`, l'adresse est demandée."""
    if not downloader.available():
        QMessageBox.information(parent, "Import par URL",
                                "Le module de téléchargement (yt-dlp) n'est pas installé dans cette version.")
        return False
    ok = True
    if url is None:
        url, ok = QInputDialog.getText(parent, "Importer depuis une URL",
                                       "Adresse de la vidéo (YouTube, TikTok, Instagram, Facebook, X, lien direct…)\n"
                                       "Le téléchargement n'est pas garanti pour toutes les adresses.")
    url = url.strip()
    if not ok or not url:
        return False
    if not url.lower().startswith(("http://", "https://")):
        QMessageBox.warning(parent, "Import par URL", "L'adresse doit commencer par http:// ou https://")
        return False
    app.jobs.submit("import_url", f"Téléchargement · {url[:60]}",
                    {"url": url, "project_id": project_id, **(extra or {})})
    return True


class DropZone(QFrame):
    """Zone de glisser-déposer."""
    dropped = Signal(list)

    def __init__(self, text: str = "Glissez vos fichiers ici", parent=None):
        super().__init__(parent)
        self.setProperty("role", "dropzone")
        self.setAcceptDrops(True)
        self.setMinimumHeight(90)
        lay = QVBoxLayout(self)
        lay.setAlignment(Qt.AlignCenter)
        t = label(text, "muted")
        t.setAlignment(Qt.AlignCenter)
        lay.addWidget(t)
        s = label("Vidéos, audios, images et documents · plusieurs fichiers possibles", "faint")
        s.setAlignment(Qt.AlignCenter)
        lay.addWidget(s)

    def _set_active(self, on: bool) -> None:
        self.setProperty("active", "true" if on else "false")
        self.style().unpolish(self)
        self.style().polish(self)

    def dragEnterEvent(self, e):
        if e.mimeData().hasUrls():
            e.acceptProposedAction()
            self._set_active(True)

    def dragLeaveEvent(self, e):
        self._set_active(False)

    def dropEvent(self, e):
        self._set_active(False)
        paths = [u.toLocalFile() for u in e.mimeData().urls() if u.isLocalFile()]
        if paths:
            self.dropped.emit(paths)


KIND_LABELS = {"video": "Vidéo", "audio": "Audio", "image": "Image", "document": "Document", "other": "Autre"}
__all__ = ["import_paths", "pick_and_import", "import_url", "DropZone", "KIND_LABELS", "guess_kind"]
