"""Navigateur dédié à chaque compte social, sans API de plateforme.

Chaque compte a son propre profil de navigateur (`%LOCALAPPDATA%\\StudioVideo\\browser\\<id du compte>`) : il y
reste connecté, et la page d'envoi s'ouvre toujours dans la bonne session. Le navigateur est Microsoft Edge, déjà
présent sur Windows (sinon Chrome) ; aucun navigateur n'est téléchargé.

La connexion se fait par l'utilisateur lui-même, dans le navigateur lancé normalement (sans automatisation : Google
et Meta bloquent souvent la connexion dans un navigateur piloté). Le logiciel ne voit jamais le mot de passe. Une
fois la fenêtre fermée, il regarde seulement si le profil contient le cookie de session du réseau (son nom et sa date
d'expiration, jamais sa valeur, qui reste chiffrée par le navigateur).
"""
from __future__ import annotations

import os
import shutil
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

# emplacements habituels, sous Program Files (x86), Program Files puis le dossier de l'utilisateur
_KNOWN = (r"Microsoft\Edge\Application\msedge.exe", r"Google\Chrome\Application\chrome.exe")
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


def find_browser() -> Path | None:
    """Edge installé sur le PC, sinon Chrome (STUDIOVIDEO_BROWSER force un autre exécutable)."""
    forced = os.environ.get("STUDIOVIDEO_BROWSER")
    if forced:
        return Path(forced) if Path(forced).is_file() else None
    for rel in _KNOWN:
        for base in _BASES:
            root = os.environ.get(base)
            if root and (Path(root) / rel).is_file():
                return Path(root) / rel
    for name in ("msedge", "chrome", "google-chrome", "chromium"):
        found = shutil.which(name)
        if found:
            return Path(found)
    return None


def browser_name(exe: Path | None) -> str:
    n = (exe.name if exe else "").lower()
    return "Edge" if "edge" in n else "Chrome" if "chrom" in n else "le navigateur"


def profile_dir(root: Path, account_id: int) -> Path:
    return Path(root) / "browser" / str(int(account_id))


def is_open(profile: Path) -> bool:
    """Le navigateur tourne avec ce profil : son fichier de verrou existe et reste pris (un fichier resté après un
    plantage, lui, s'ouvre)."""
    lock = Path(profile) / "lockfile"
    if not lock.exists():
        return False
    try:
        with open(lock, "a"):
            return False
    except OSError:
        return True


def command(exe: Path, profile: Path, url: str) -> list[str]:
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


def _cookie_db(profile: Path) -> Path | None:
    for p in (Path(profile) / "Default" / "Network" / "Cookies", Path(profile) / "Default" / "Cookies"):
        if p.is_file():
            return p
    return None


def session_found(profile: Path, platform: str, now: float | None = None) -> bool | None:
    """Une session du réseau est-elle ouverte dans ce profil ? None : impossible à dire (navigateur encore ouvert,
    fichier illisible). Seuls le nom et l'expiration des cookies sont lus."""
    wanted = SESSION_COOKIES.get(platform)
    if not wanted:
        return None
    db = _cookie_db(profile)
    if db is None:
        return False
    if is_open(profile):
        return None
    limit = int(((time.time() if now is None else now) + _EPOCH_1601) * 1_000_000)
    try:
        con = sqlite3.connect(db.as_uri() + "?mode=ro", uri=True)
        try:
            for host, name in wanted:
                row = con.execute("SELECT 1 FROM cookies WHERE host_key IN (?, ?) AND name = ? AND "
                                  "(expires_utc = 0 OR expires_utc > ?) LIMIT 1",
                                  (host, host.lstrip("."), name, limit)).fetchone()
                if row:
                    return True
        finally:
            con.close()
    except sqlite3.Error:
        return None
    return False
