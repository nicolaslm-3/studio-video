"""Mise en ligne TikTok (Content Posting API, « Direct Post ») — cahier des charges §18, V3.

- Connexion : Login Kit pour ordinateur, redirection locale (127.0.0.1, port libre) et PKCE
  (empreinte SHA-256 en hexadécimal, exigence TikTok). Clé d'application fournie par l'utilisateur.
- Jetons : accès 24 h, renouvellement 365 j ; le jeton de renouvellement peut changer à chaque
  renouvellement (il est alors réenregistré dans le coffre Windows).
- Envoi : init → blocs (5 à 64 Mo, envoi d'un seul tenant jusqu'à 64 Mo) → suivi du statut.
- Limite TikTok : une application non auditée ne publie qu'en privé (SELF_ONLY) ; c'est signalé.
"""
from __future__ import annotations

import hashlib
import http.server
import json
import os
import secrets as _secrets
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

AUTH = "https://www.tiktok.com/v2/auth/authorize/"
API = "https://open.tiktokapis.com"
SCOPES = "user.info.basic,video.publish,video.list"
MB = 1024 * 1024


class TikTokError(RuntimeError):
    pass


@dataclass
class Endpoints:
    auth: str = AUTH
    api: str = API

    @classmethod
    def from_env(cls) -> "Endpoints":
        base = os.environ.get("STUDIOVIDEO_TIKTOK_TEST_URL")
        return cls(base + "/auth", base) if base else cls()


def _call(method: str, url: str, token: str | None = None, body=None, form: dict | None = None,
          timeout: float = 60) -> dict:
    headers = {}
    data = None
    if form is not None:
        data = urllib.parse.urlencode(form).encode()
        headers["Content-Type"] = "application/x-www-form-urlencoded"
    elif body is not None:
        data = json.dumps(body).encode()
        headers["Content-Type"] = "application/json; charset=UTF-8"
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            out = json.loads(r.read().decode() or "{}")
    except urllib.error.HTTPError as exc:
        try:
            out = json.loads(exc.read().decode() or "{}")
        except ValueError:
            raise TikTokError(f"TikTok a répondu {exc.code}.") from exc
    except (urllib.error.URLError, OSError) as exc:
        raise TikTokError(f"TikTok injoignable : {exc}") from exc
    err = out.get("error")
    if isinstance(err, dict) and err.get("code") not in (None, "", "ok"):
        code = err.get("code")
        msg = {"access_token_invalid": "Autorisation expirée ou révoquée : reconnectez le compte.",
               "scope_not_authorized": "Autorisation insuffisante : reconnectez le compte.",
               "spam_risk_too_many_posts": "TikTok limite le nombre de publications : réessayez plus tard.",
               "rate_limit_exceeded": "Trop de requêtes vers TikTok : réessayez dans une minute.",
               "unaudited_client_can_only_post_to_private_accounts":
                   "Application TikTok non auditée : le compte doit être privé pour publier."}.get(code)
        raise TikTokError(msg or f"TikTok : {code} — {err.get('message', '')}")
    if "error" in out and isinstance(out["error"], str):        # point d'accès OAuth
        if out["error"] == "invalid_grant":
            raise TikTokError("Autorisation expirée ou révoquée : reconnectez le compte.")
        raise TikTokError(f"TikTok : {out['error']} — {out.get('error_description', '')}")
    return out


# -- connexion -----------------------------------------------------------------------------------
class _Catcher(http.server.BaseHTTPRequestHandler):
    result: dict = {}

    def log_message(self, *a):
        pass

    def do_GET(self):
        q = dict(urllib.parse.parse_qsl(urllib.parse.urlparse(self.path).query))
        if "code" in q or "error" in q:
            type(self).result.update(q)
        ok = "code" in q
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        self.wfile.write(("<html><body style='font-family:sans-serif;padding:40px'><h2>Studio Vidéo</h2><p>" +
                          ("Connexion TikTok réussie. Vous pouvez fermer cet onglet." if ok else
                           f"Connexion refusée ({q.get('error_description') or q.get('error', '')}).") +
                          "</p></body></html>").encode())


def connect(client_key: str, client_secret: str, open_browser: Callable[[str], None], timeout: float = 300,
            ep: Endpoints | None = None, is_cancelled: Callable[[], bool] = lambda: False) -> dict:
    if not client_key or not client_secret:
        raise TikTokError("Clé et code secret de l'application TikTok absents (Paramètres → Comptes → TikTok).")
    ep = ep or Endpoints.from_env()
    alphabet = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-._~"
    verifier = "".join(_secrets.choice(alphabet) for _ in range(64))
    challenge = hashlib.sha256(verifier.encode()).hexdigest()           # hexadécimal : exigence TikTok
    state = _secrets.token_urlsafe(16)
    handler = type("Catcher", (_Catcher,), {"result": {}})
    srv = http.server.HTTPServer(("127.0.0.1", 0), handler)
    redirect = f"http://127.0.0.1:{srv.server_port}/callback/"
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        open_browser(ep.auth + "?" + urllib.parse.urlencode({
            "client_key": client_key, "scope": SCOPES, "redirect_uri": redirect, "state": state,
            "response_type": "code", "code_challenge": challenge, "code_challenge_method": "S256"}))
        end = time.time() + timeout
        while not handler.result and time.time() < end:
            if is_cancelled():
                raise TikTokError("Connexion annulée.")
            time.sleep(0.2)
        res = dict(handler.result)
    finally:
        srv.shutdown()
        srv.server_close()
    if not res:
        raise TikTokError("Pas de réponse de TikTok dans le temps imparti.")
    if res.get("state") != state:
        raise TikTokError("Réponse de TikTok invalide (state).")
    if "error" in res:
        raise TikTokError(f"Connexion refusée : {res.get('error_description') or res['error']}")
    tok = _call("POST", ep.api + "/v2/oauth/token/", form={
        "client_key": client_key, "client_secret": client_secret, "code": res["code"],
        "grant_type": "authorization_code", "redirect_uri": redirect, "code_verifier": verifier})
    if not tok.get("refresh_token"):
        raise TikTokError("TikTok n'a pas fourni de jeton de renouvellement.")
    return tok


def refresh(client_key: str, client_secret: str, refresh_token: str, ep: Endpoints | None = None) -> dict:
    """Retourne {"access_token", "refresh_token" (éventuellement nouveau), …}."""
    ep = ep or Endpoints.from_env()
    return _call("POST", ep.api + "/v2/oauth/token/", form={
        "client_key": client_key, "client_secret": client_secret, "grant_type": "refresh_token",
        "refresh_token": refresh_token})


def user_info(token: str, ep: Endpoints | None = None) -> dict:
    ep = ep or Endpoints.from_env()
    out = _call("GET", ep.api + "/v2/user/info/?fields=open_id,display_name", token)
    return (out.get("data") or {}).get("user") or {}


def creator_info(token: str, ep: Endpoints | None = None) -> dict:
    ep = ep or Endpoints.from_env()
    return _call("POST", ep.api + "/v2/post/publish/creator_info/query/", token, body={}).get("data") or {}


# -- envoi ---------------------------------------------------------------------------------------
def chunk_plan(size: int) -> tuple[int, int]:
    """(taille de bloc, nombre de blocs) selon les règles TikTok."""
    if size <= 0:
        raise TikTokError("Fichier vidéo vide.")
    if size <= 64 * MB:
        return size, 1
    chunk = 10 * MB
    return chunk, size // chunk            # le dernier bloc absorbe le reste (< 20 Mo)


def upload(path: Path, title: str, privacy: str, token: str,
           progress: Callable[[float, str], None] = lambda f, m: None,
           is_cancelled: Callable[[], bool] = lambda: False, ep: Endpoints | None = None,
           poll_s: float = 5.0, max_wait: float = 900) -> dict:
    """Publie la vidéo. Retourne {"publish_id", "status", "post_ids", "privacy"}."""
    ep = ep or Endpoints.from_env()
    path = Path(path)
    if not path.exists():
        raise TikTokError(f"Vidéo introuvable : {path}")
    info = creator_info(token, ep)
    options = info.get("privacy_level_options") or []
    used = privacy if privacy in options else ("SELF_ONLY" if "SELF_ONLY" in options or not options else options[0])
    max_dur = info.get("max_video_post_duration_sec")
    size = path.stat().st_size
    chunk, count = chunk_plan(size)
    init = _call("POST", ep.api + "/v2/post/publish/video/init/", token, body={
        "post_info": {"title": title[:2200], "privacy_level": used, "disable_comment": False,
                      "disable_duet": False, "disable_stitch": False},
        "source_info": {"source": "FILE_UPLOAD", "video_size": size, "chunk_size": chunk,
                        "total_chunk_count": count}}).get("data") or {}
    pid, url = init.get("publish_id"), init.get("upload_url")
    if not pid or not url:
        raise TikTokError("TikTok n'a pas fourni d'adresse d'envoi.")
    with open(path, "rb") as f:
        for i in range(count):
            if is_cancelled():
                raise TikTokError("Envoi annulé.")
            start = i * chunk
            length = (size - start) if i == count - 1 else chunk
            f.seek(start)
            data = f.read(length)
            req = urllib.request.Request(url, data=data, method="PUT", headers={
                "Content-Type": "video/mp4", "Content-Length": str(len(data)),
                "Content-Range": f"bytes {start}-{start + len(data) - 1}/{size}"})
            for attempt in range(4):
                try:
                    with urllib.request.urlopen(req, timeout=300) as r:
                        if r.status not in (200, 201, 206):
                            raise TikTokError(f"Envoi refusé ({r.status}).")
                    break
                except urllib.error.HTTPError as exc:
                    if exc.code in (500, 502, 503, 504) and attempt < 3:
                        time.sleep(2 ** attempt)
                        continue
                    raise TikTokError(f"Envoi interrompu ({exc.code}).") from exc
                except (urllib.error.URLError, OSError) as exc:
                    if attempt < 3:
                        time.sleep(2 ** attempt)
                        continue
                    raise TikTokError(f"Connexion perdue pendant l'envoi : {exc}") from exc
            progress(0.8 * (i + 1) / count, f"Envoi {start + len(data) >> 20}/{size >> 20} Mo")
    end = time.time() + max_wait
    while True:
        st = _call("POST", ep.api + "/v2/post/publish/status/fetch/", token, body={"publish_id": pid}).get("data") or {}
        status = st.get("status", "")
        if status == "PUBLISH_COMPLETE":
            return {"publish_id": pid, "status": status, "privacy": used,
                    "post_ids": [str(x) for x in (st.get("publicaly_available_post_id") or [])],
                    "max_duration": max_dur}
        if status == "FAILED":
            raise TikTokError(f"TikTok a refusé la vidéo : {st.get('fail_reason', 'raison inconnue')}")
        if time.time() > end:
            raise TikTokError("TikTok traite encore la vidéo : vérifiez dans l'application TikTok.")
        if is_cancelled():
            raise TikTokError("Suivi annulé (la vidéo peut quand même être publiée par TikTok).")
        progress(0.9, "Traitement par TikTok…")
        time.sleep(poll_s)


def video_stats(token: str, ids: list[str], ep: Endpoints | None = None) -> dict[str, dict]:
    """Statistiques des vidéos publiques du compte (autorisation video.list)."""
    ep = ep or Endpoints.from_env()
    out: dict[str, dict] = {}
    for i in range(0, len(ids), 20):
        data = _call("POST", ep.api + "/v2/video/query/?fields=id,view_count,like_count,comment_count,share_count",
                     token, body={"filters": {"video_ids": ids[i:i + 20]}}).get("data") or {}
        for v in data.get("videos") or []:
            m = {}
            for src, dst in (("view_count", "views"), ("like_count", "likes"), ("comment_count", "comments"),
                             ("share_count", "shares")):
                if v.get(src) is not None:
                    m[dst] = int(v[src])
            out[str(v["id"])] = m
    return out
