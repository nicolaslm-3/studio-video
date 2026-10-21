"""Mises à jour automatiques (cahier des charges §28).

Fonctionnement sous Windows (WinSparkle) :
1. Au démarrage, WinSparkle connaît la version (__version__) et l'URL du flux
   appcast (canal stable ou bêta) ; il vérifie périodiquement (24 h).
2. S'il existe une version plus récente, il affiche les notes de version avec
   « Installer », « Plus tard », « Ignorer cette version ».
3. Il télécharge l'installateur et vérifie sa signature EdDSA (ed25519) avec la
   clé publique intégrée au programme. Signature absente ou invalide = refus.
4. Il demande s'il peut fermer l'application (`can_shutdown`) : refusé tant
   qu'un export ou une publication (tâche critique) est en cours.
5. `shutdown_request` : sauvegarde de la base, journalisation, fermeture propre.
6. L'installateur Inno Setup conserve une copie de la version précédente,
   remplace les fichiers du programme (jamais les données dans %LOCALAPPDATA%)
   puis relance l'application.

Hors Windows ou sans WinSparkle.dll, le module se déclare indisponible :
aucune mise à jour n'est simulée.
"""
from __future__ import annotations

import ctypes
import json
import logging
import sys
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from .. import __app_name__, __publisher__, __version__
from ..paths import bundle_dir, install_dir
from . import config

log = logging.getLogger(__name__)


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class UpdateJournal:
    """Journal des mises à jour + état de la dernière mise à jour (dans le dossier de données)."""

    def __init__(self, root: Path):
        self.log_file = root / "logs" / "updates.log"
        self.state_file = root / "update_state.json"
        self.log_file.parent.mkdir(parents=True, exist_ok=True)

    def write(self, msg: str) -> None:
        with self.log_file.open("a", encoding="utf-8") as f:
            f.write(f"{_now()} {msg}\n")
        log.info("[update] %s", msg)

    def read_state(self) -> dict:
        try:
            return json.loads(self.state_file.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}

    def write_state(self, **state) -> None:
        self.state_file.write_text(json.dumps(state, indent=2), encoding="utf-8")

    def on_startup(self) -> str | None:
        """À appeler au lancement. Retourne un message si une mise à jour vient d'avoir lieu."""
        st = self.read_state()
        if st.get("pending"):
            prev = st.get("from_version")
            if prev and prev != __version__:
                self.write(f"Mise à jour réussie : {prev} -> {__version__}")
                self.write_state(pending=False, from_version=prev, to_version=__version__, at=_now(), ok=True)
                return f"Studio Vidéo a été mis à jour de la version {prev} vers {__version__}."
            self.write(f"Mise à jour non appliquée (version toujours {__version__}).")
            self.write_state(pending=False, from_version=prev, at=_now(), ok=False)
            return "La mise à jour n'a pas été installée. Vous pouvez réessayer depuis Paramètres → À propos."
        return None


class BaseUpdater:
    available = False
    reason = "Les mises à jour automatiques ne sont disponibles que dans la version Windows installée."

    def start(self) -> None: ...
    def check_now(self) -> None: ...
    def stop(self) -> None: ...


class WinSparkleUpdater(BaseUpdater):
    available = True
    reason = ""

    def __init__(self, dll_path: Path, appcast_url: str, public_key: str,
                 can_shutdown: Callable[[], bool], request_shutdown: Callable[[], None]):
        self._dll = ctypes.WinDLL(str(dll_path))
        self.appcast_url = appcast_url
        self.public_key = public_key
        # Les callbacks sont appelés depuis un thread WinSparkle : ils doivent rester légers
        # et se contenter de relayer vers le thread graphique.
        self._can_cb = ctypes.WINFUNCTYPE(ctypes.c_int)(lambda: 1 if can_shutdown() else 0)
        self._shutdown_cb = ctypes.WINFUNCTYPE(None)(request_shutdown)
        self._started = False

    def start(self) -> None:
        d = self._dll
        d.win_sparkle_set_appcast_url.argtypes = [ctypes.c_char_p]
        d.win_sparkle_set_app_details.argtypes = [ctypes.c_wchar_p] * 3
        d.win_sparkle_set_eddsa_public_key.argtypes = [ctypes.c_char_p]
        d.win_sparkle_set_eddsa_public_key.restype = ctypes.c_int
        d.win_sparkle_set_automatic_check_for_updates.argtypes = [ctypes.c_int]
        d.win_sparkle_set_update_check_interval.argtypes = [ctypes.c_int]

        d.win_sparkle_set_appcast_url(self.appcast_url.encode("utf-8"))
        d.win_sparkle_set_app_details(__publisher__, "Studio Vidéo", __version__)
        if not d.win_sparkle_set_eddsa_public_key(self.public_key.encode("ascii")):
            raise RuntimeError("Clé publique de vérification des mises à jour invalide.")
        d.win_sparkle_set_automatic_check_for_updates(1)
        d.win_sparkle_set_update_check_interval(24 * 3600)
        d.win_sparkle_set_can_shutdown_callback(self._can_cb)
        d.win_sparkle_set_shutdown_request_callback(self._shutdown_cb)
        d.win_sparkle_init()
        self._started = True

    def check_now(self) -> None:
        if self._started:
            self._dll.win_sparkle_check_update_with_ui()

    def stop(self) -> None:
        if self._started:
            self._dll.win_sparkle_cleanup()
            self._started = False


def create_updater(channel: str, can_shutdown: Callable[[], bool],
                   request_shutdown: Callable[[], None]) -> BaseUpdater:
    if sys.platform != "win32" or not getattr(sys, "frozen", False):
        return BaseUpdater()
    dll = next((d / "WinSparkle.dll" for d in (bundle_dir(), install_dir()) if (d / "WinSparkle.dll").exists()),
               None)
    if dll is None:
        u = BaseUpdater()
        u.reason = "WinSparkle.dll est absent de l'installation."
        return u
    if not config.PUBLIC_KEY:
        u = BaseUpdater()
        u.reason = "Cette version n'a pas été compilée avec une clé de signature des mises à jour."
        return u
    try:
        return WinSparkleUpdater(dll, config.appcast_url(channel), config.PUBLIC_KEY,
                                 can_shutdown, request_shutdown)
    except OSError as exc:
        u = BaseUpdater()
        u.reason = f"Chargement de WinSparkle impossible : {exc}"
        return u


class ShutdownCoordinator:
    """Logique de fermeture sûre avant installation, indépendante de Qt (testable)."""

    def __init__(self, app_ctx, journal: UpdateJournal):
        self.app = app_ctx
        self.journal = journal
        self._lock = threading.Lock()

    def can_shutdown(self) -> bool:
        critical = self.app.jobs.critical_running()
        if critical:
            self.journal.write("Mise à jour reportée : tâche critique en cours ("
                               + ", ".join(j.label for j in critical) + ")")
            return False
        return True

    def prepare(self) -> None:
        """Sauvegarde et arrêt propre. Appelé dans le thread graphique."""
        with self._lock:
            self.app.jobs.cancel_non_critical()
            self.app.jobs.wait_idle(timeout=30, critical_only=True)
            backup = self.app.db.backup(reason=f"avant-mise-a-jour-{__version__}")
            self.journal.write_state(pending=True, from_version=__version__, at=_now(), backup=str(backup))
            self.journal.write(f"Fermeture pour mise à jour depuis {__version__} (sauvegarde : {backup.name})")
