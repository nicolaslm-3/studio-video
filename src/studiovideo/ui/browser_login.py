"""Pages des réseaux : elles s'ouvrent dans un nouvel onglet du navigateur par défaut, déjà ouvert, avec les
comptes où l'utilisateur est connecté (`open_page`) — comme toute autre application.

`BrowserLogin` (profil de navigateur séparé par compte, `publish/browser.py`) n'est plus proposé dans l'interface ;
il reste pour un usage futur (lecture automatisée)."""
from __future__ import annotations

from datetime import datetime, timezone

from PySide6.QtCore import QObject, QTimer, Signal

from ..core import AppContext
from ..publish import browser


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def profile(app: AppContext, account_id: int):
    return browser.profile_dir(app.paths.root, account_id, browser.find_browser())


def check_session(app: AppContext, account_id: int) -> bool | None:
    """Relit la session du profil (si le navigateur est fermé) et la garde dans les réglages du compte."""
    acc = app.concepts.get_account(account_id)
    found = browser.session_found(profile(app, account_id), acc.platform.value)
    if found is not None:
        was = bool((app.concepts.account_settings(account_id).get("browser") or {}).get("was")) or found
        app.concepts.update_account_settings(account_id, browser={"session": found, "checked_at": _now(),
                                                                  "was": was})
    return found


def session_state(app: AppContext, account_id: int) -> bool | None:
    """Dernier état connu : True connecté, False pas de session, None jamais vérifié."""
    return (app.concepts.account_settings(account_id).get("browser") or {}).get("session")


def state_text(app: AppContext, account_id: int) -> str:
    acc = app.concepts.get_account(account_id)
    if acc.connection_status == "connected":
        return "Connecté (API)"
    return "Sans API (ton navigateur)"


def open_page(app: AppContext, account_id: int, url: str, fallback=None) -> str:
    """Ouvre `url` comme le font les autres applications : un nouvel onglet du navigateur par défaut, déjà ouvert,
    avec les comptes où l'utilisateur est connecté (`fallback` : ouverture par Windows)."""
    if fallback is None:
        from PySide6.QtCore import QUrl
        from PySide6.QtGui import QDesktopServices
        fallback = lambda u: QDesktopServices.openUrl(QUrl(u))      # noqa: E731
    fallback(url)
    return ""


class BrowserLogin(QObject):
    """Ouvre la page de connexion et attend la fermeture du navigateur. `finished(état)` : True connecté, False
    aucune session trouvée, None impossible à vérifier."""
    finished = Signal(object)

    def __init__(self, app: AppContext, account_id: int, parent=None):
        super().__init__(parent)
        self.app, self.account_id = app, account_id
        self.profile = profile(app, account_id)
        self._seen_open = False
        self._waited = 0
        self.timer = QTimer(self, interval=1500)
        self.timer.timeout.connect(self._poll)

    def start(self) -> str:
        """Lance le navigateur ; renvoie un message d'erreur, ou "" si la fenêtre s'ouvre."""
        exe = browser.find_browser()
        if exe is None:
            return "Aucun navigateur capable d'un profil séparé (Brave, Chrome, Edge, Firefox…) sur ce PC : la " \
                   "connexion dans un navigateur dédié est impossible."
        acc = self.app.concepts.get_account(self.account_id)
        browser.launch(exe, self.profile, browser.LOGIN_URLS.get(acc.platform.value, "about:blank"))
        self.timer.start()
        return ""

    def _poll(self) -> None:
        if browser.is_open(self.profile):
            self._seen_open = True
            return
        self._waited += 1
        if not self._seen_open and self._waited < 20:      # le navigateur met quelques secondes à démarrer
            return
        self.timer.stop()
        self.finished.emit(check_session(self.app, self.account_id))
