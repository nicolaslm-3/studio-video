"""« Se connecter à ce compte » : le navigateur dédié du compte (Edge, sinon Chrome) s'ouvre normalement sur la page
de connexion du réseau ; l'utilisateur se connecte lui-même puis ferme la fenêtre. Le logiciel vérifie ensuite que
le profil contient bien une session (`publish/browser.py`). Il ne voit jamais le mot de passe."""
from __future__ import annotations

from datetime import datetime, timezone

from PySide6.QtCore import QObject, QTimer, Signal

from ..core import AppContext
from ..publish import browser


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def profile(app: AppContext, account_id: int):
    return browser.profile_dir(app.paths.root, account_id)


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
    st = app.concepts.account_settings(account_id).get("browser") or {}
    if st.get("session"):
        return f"Connecté dans {browser.browser_name(browser.find_browser())}"
    return "Session expirée" if st.get("was") else "Non connecté"


def open_page(app: AppContext, account_id: int, url: str, fallback=None) -> str:
    """Ouvre `url` dans le profil du compte. Sans Edge ni Chrome : navigateur par défaut (`fallback`), et le dit."""
    exe = browser.find_browser()
    if exe is None:
        if fallback:
            fallback(url)
        return "Edge et Chrome introuvables : page ouverte dans le navigateur par défaut (pas dans le profil du compte)."
    browser.launch(exe, profile(app, account_id), url)
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
            return "Ni Edge ni Chrome n'ont été trouvés sur ce PC : la connexion dans un navigateur dédié est " \
                   "impossible."
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
