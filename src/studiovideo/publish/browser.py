"""Navigateur dédié à chaque compte social, sans API de plateforme.

Chaque compte a son propre profil de navigateur (`%LOCALAPPDATA%\\StudioVideo\\browser\\<navigateur>\\<id du
compte>`) : il y reste connecté, et la page d'envoi s'ouvre toujours dans la bonne session. Le navigateur est celui
choisi par défaut dans Windows (Brave, Chrome, Edge, Firefox, Opera, Vivaldi…) ; s'il ne sait pas ouvrir un profil
séparé, Edge (présent sur Windows) prend le relais. Aucun navigateur n'est téléchargé.

La connexion se fait par l'utilisateur lui-même, dans le navigateur lancé normalement (sans automatisation : Google
et Meta bloquent souvent la connexion dans un navigateur piloté). Le logiciel ne voit jamais le mot de passe. Une
fois la fenêtre fermée, il regarde seulement si le profil contient le cookie de session du réseau (son nom et sa date
d'expiration, jamais sa valeur).
"""
from __future__ import annotations

import os
import re
import shutil
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

# navigateurs qui savent ouvrir un profil séparé : nom du programme → (nom affiché, famille)
KNOWN = {"msedge": ("Edge", "chromium"), "chrome": ("Chrome", "chromium"), "brave": ("Brave", "chromium"),
         "vivaldi": ("Vivaldi", "chromium"), "opera": ("Opera", "chromium"), "launcher": ("Opera", "chromium"),
         "chromium": ("Chromium", "chromium"), "google-chrome": ("Chrome", "chromium"),
         "firefox": ("Firefox", "firefox")}
# secours si le navigateur par défaut ne convient pas : Edge (toujours présent sur Windows), puis Chrome
_FALLBACK = (r"Microsoft\Edge\Application\msedge.exe", r"Google\Chrome\Application\chrome.exe")
_BASES = ("PROGRAMFILES(X86)", "PROGRAMFILES", "LOCALAPPDATA")

# page de connexion de chaque réseau
LOGIN_URLS = {
    "youtube": "https://accounts.google.com/ServiceLogin?service=youtube&continue=https%3A%2F%2Fwww.youtube.com%2F",
    "tiktok": "https://www.tiktok.com/login",
    "instagram": "https://www.instagram.com/accounts/login/",
    "facebook": "https://www.facebook.com/login/",
    "x": "https://x.com/i/flow/login",
}
# page vue une fois connecté (vérification à la main, bouton « Ouvrir »)
HOME_URLS = {"youtube": "https://studio.youtube.com/", "tiktok": "https://www.tiktok.com/tiktokstudio",
             "instagram": "https://www.instagram.com/", "facebook": "https://www.facebook.com/",
             "x": "https://x.com/home"}
# cookies présents seulement quand une session est ouverte : (domaine, nom) ; l'un d'eux suffit
SESSION_COOKIES = {
    "youtube": ((".youtube.com", "LOGIN_INFO"), (".youtube.com", "__Secure-3PSID"), (".youtube.com", "SID")),
    "tiktok": ((".tiktok.com", "sessionid"), (".tiktok.com", "sessionid_ss")),
    "instagram": ((".instagram.com", "sessionid"),),
    "facebook": ((".facebook.com", "c_user"),),
    "x": ((".x.com", "auth_token"), (".twitter.com", "auth_token")),
}
_EPOCH_1601 = 11_644_473_600          # les navigateurs Chromium datent les cookies en µs depuis 1601


def _kind(exe: Path | None) -> str | None:
    return KNOWN.get(exe.stem.lower(), (None, None))[1] if exe else None


def default_browser() -> Path | None:
    """Programme du navigateur choisi par défaut dans Windows (celui qui ouvre les liens https)."""
    if sys.platform != "win32":
        return None
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\Shell\Associations"
                            r"\UrlAssociations\https\UserChoice") as k:
            progid = winreg.QueryValueEx(k, "ProgId")[0]
        with winreg.OpenKey(winreg.HKEY_CLASSES_ROOT, progid + r"\shell\open\command") as k:
            cmd = winreg.QueryValueEx(k, "")[0]
    except OSError:
        return None
    m = re.match(r'\s*"([^"]+)"', cmd) or re.match(r"\s*(\S+\.exe)", cmd, re.I)
    return Path(m.group(1)) if m and Path(m.group(1)).is_file() else None


def find_browser() -> Path | None:
    """Le navigateur par défaut s'il sait ouvrir un profil séparé, sinon Edge, sinon Chrome (STUDIOVIDEO_BROWSER
    force un autre programme)."""
    forced = os.environ.get("STUDIOVIDEO_BROWSER")
    if forced:
        return Path(forced) if Path(forced).is_file() else None
    exe = default_browser()
    if _kind(exe):
        return exe
    for rel in _FALLBACK:
        for base in _BASES:
            root = os.environ.get(base)
            if root and (Path(root) / rel).is_file():
                return Path(root) / rel
    for name in ("msedge", "chrome", "google-chrome", "chromium", "firefox"):
        found = shutil.which(name)
        if found:
            return Path(found)
    return None


def browser_name(exe: Path | None) -> str:
    return KNOWN.get(exe.stem.lower(), ("le navigateur",))[0] if exe else "le navigateur"


def profile_dir(root: Path, account_id: int, exe: Path | None = None) -> Path:
    """Profil du compte, propre à chaque navigateur (les profils de deux navigateurs ne se mélangent pas)."""
    return Path(root) / "browser" / (exe.stem.lower() if exe else "navigateur") / str(int(account_id))


def _locked(path: Path) -> bool:
    if not path.exists():
        return False
    try:
        with open(path, "a"):
            return False
    except OSError:
        return True


def is_open(profile: Path) -> bool:
    """Le navigateur tourne avec ce profil : son fichier de verrou est pris (un fichier resté après un plantage, lui,
    s'ouvre). Chromium : « lockfile » ; Firefox : « parent.lock »."""
    return _locked(Path(profile) / "lockfile") or _locked(Path(profile) / "parent.lock")


def command(exe: Path, profile: Path, url: str) -> list[str]:
    if _kind(exe) == "firefox":
        return [str(exe), "-profile", str(profile), url]
    return [str(exe), f"--user-data-dir={profile}", "--no-first-run", "--no-default-browser-check", url]


def launch(exe: Path, profile: Path, url: str) -> None:
    """Ouvre `url` dans le profil (navigateur lancé normalement, sans automatisation). Si le profil est déjà ouvert,
    la page s'ajoute dans un nouvel onglet de la même fenêtre."""
    Path(profile).mkdir(parents=True, exist_ok=True)
    flags = 0
    if sys.platform == "win32":
        flags = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
    subprocess.Popen(command(exe, profile, url), stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                     stderr=subprocess.DEVNULL, close_fds=True, creationflags=flags)


def _cookie_db(profile: Path) -> tuple[Path, str] | None:
    p = Path(profile)
    for db, kind in ((p / "Default" / "Network" / "Cookies", "chromium"), (p / "Default" / "Cookies", "chromium"),
                     (p / "cookies.sqlite", "firefox")):
        if db.is_file():
            return db, kind
    return None


def session_found(profile: Path, platform: str, now: float | None = None) -> bool | None:
    """Une session du réseau est-elle ouverte dans ce profil ? None : impossible à dire (navigateur encore ouvert,
    fichier illisible). Seuls le nom et l'expiration des cookies sont lus."""
    wanted = SESSION_COOKIES.get(platform)
    if not wanted:
        return None
    found = _cookie_db(profile)
    if found is None:
        return False
    if is_open(profile):
        return None
    db, kind = found
    now = time.time() if now is None else now
    if kind == "firefox":           # secondes depuis 1970 ; une session sans date expire à la fermeture
        sql = "SELECT 1 FROM moz_cookies WHERE host IN (?, ?) AND name = ? AND expiry > ? LIMIT 1"
        limit = int(now)
    else:
        sql = ("SELECT 1 FROM cookies WHERE host_key IN (?, ?) AND name = ? AND (expires_utc = 0 OR expires_utc > ?) "
               "LIMIT 1")
        limit = int((now + _EPOCH_1601) * 1_000_000)
    try:
        con = sqlite3.connect(db.as_uri() + "?mode=ro", uri=True)
        try:
            for host, name in wanted:
                if con.execute(sql, (host, host.lstrip("."), name, limit)).fetchone():
                    return True
        finally:
            con.close()
    except sqlite3.Error:
        return None
    return False
