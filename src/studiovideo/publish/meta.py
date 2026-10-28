"""Mise en ligne Instagram (Reels) et Facebook (Reels de Page) — API Graph de Meta, §18 (V3).

- Connexion : « Facebook Login for Business » avec redirection locale fixe (Meta exige une adresse exacte :
  http://localhost:8767/callback/), jeton utilisateur de 60 jours, puis jetons de Page (sans expiration
  tant que l'accès n'est pas retiré) rangés dans le coffre Windows.
- Instagram : compte professionnel relié à une Page. Conteneur « REELS » en envoi reprenable
  (rupload.facebook.com) → suivi → publication → lien permanent.
- Facebook : Reel de Page (video_reels : start → envoi → finish).
"""
from __future__ import annotations

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

VERSION = "v21.0"
PORT = 8767
SCOPES = ("pages_show_list,pages_read_engagement,pages_manage_posts,business_management,"
          "instagram_basic,instagram_content_publish")


class MetaError(RuntimeError):
    pass


@dataclass
class Endpoints:
    dialog: str = f"https://www.facebook.com/{VERSION}/dialog/oauth"
    graph: str = f"https://graph.facebook.com/{VERSION}"
    rupload: str = "https://rupload.facebook.com"

    @classmethod
    def from_env(cls) -> "Endpoints":
        base = os.environ.get("STUDIOVIDEO_META_TEST_URL")
        return cls(base + "/dialog", base + "/graph", base + "/rupload") if base else cls()


def _call(method: str, url: str, params: dict | None = None, headers: dict | None = None, data: bytes | None = None,
          timeout: float = 120) -> dict:
    if params:
        url += ("&" if "?" in url else "?") + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, data=data if data is not None else (b"" if method == "POST" else None),
                                 headers=headers or {}, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode() or "{}")
    except urllib.error.HTTPError as exc:
        try:
            err = json.loads(exc.read().decode()).get("error", {})
        except ValueError:
            raise MetaError(f"Meta a répondu {exc.code}.") from exc
        code, msg = err.get("code"), err.get("message", "")
        if code == 190:
            raise MetaError("Autorisation Meta expirée ou révoquée : reconnectez le compte.") from exc
        if code in (10, 200) or "permission" in msg.lower():
            raise MetaError(f"Autorisation insuffisante chez Meta : {msg}") from exc
        if code in (4, 17, 32, 613) or "limit" in msg.lower():
            raise MetaError("Limite de publication Meta atteinte : réessayez plus tard.") from exc
        raise MetaError(f"Meta : {msg or exc.code}") from exc
    except (urllib.error.URLError, OSError) as exc:
        raise MetaError(f"Meta injoignable : {exc}") from exc


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
                          ("Connexion Meta réussie. Vous pouvez fermer cet onglet." if ok else
                           f"Connexion refusée ({q.get('error_description') or q.get('error', '')}).") +
                          "</p></body></html>").encode())


def redirect_uri(port: int = PORT) -> str:
    return f"http://localhost:{port}/callback/"


def connect(app_id: str, app_secret: str, open_browser: Callable[[str], None], timeout: float = 300,
            ep: Endpoints | None = None, port: int = PORT, is_cancelled: Callable[[], bool] = lambda: False) -> dict:
    """Retourne {"user_token", "expires_in", "pages": [{"id", "name", "token", "ig_id", "ig_username"}]}."""
    if not app_id or not app_secret:
        raise MetaError("Identifiant et clé secrète de l'application Meta absents (Paramètres → Publication).")
    ep = ep or Endpoints.from_env()
    state = _secrets.token_urlsafe(16)
    handler = type("Catcher", (_Catcher,), {"result": {}})
    try:
        srv = http.server.HTTPServer(("127.0.0.1", port), handler)
    except OSError as exc:
        raise MetaError(f"Le port local {port} est occupé : fermez l'application qui l'utilise et réessayez.") from exc
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    redirect = redirect_uri(port)
    try:
        open_browser(ep.dialog + "?" + urllib.parse.urlencode({
            "client_id": app_id, "redirect_uri": redirect, "state": state, "response_type": "code",
            "scope": SCOPES}))
        end = time.time() + timeout
        while not handler.result and time.time() < end:
            if is_cancelled():
                raise MetaError("Connexion annulée.")
            time.sleep(0.2)
        res = dict(handler.result)
    finally:
        srv.shutdown()
        srv.server_close()
    if not res:
        raise MetaError("Pas de réponse de Meta dans le temps imparti.")
    if res.get("state") != state:
        raise MetaError("Réponse de Meta invalide (state).")
    if "error" in res:
        raise MetaError(f"Connexion refusée : {res.get('error_description') or res['error']}")
    short = _call("GET", ep.graph + "/oauth/access_token", {"client_id": app_id, "client_secret": app_secret,
                                                            "redirect_uri": redirect, "code": res["code"]})
    long = _call("GET", ep.graph + "/oauth/access_token", {"grant_type": "fb_exchange_token", "client_id": app_id,
                                                           "client_secret": app_secret,
                                                           "fb_exchange_token": short["access_token"]})
    return {"user_token": long["access_token"], "expires_in": long.get("expires_in"),
            "pages": pages(long["access_token"], ep)}


def pages(user_token: str, ep: Endpoints | None = None) -> list[dict]:
    ep = ep or Endpoints.from_env()
    data = _call("GET", ep.graph + "/me/accounts", {
        "fields": "id,name,access_token,instagram_business_account{id,username}", "access_token": user_token})
    out = []
    for p in data.get("data") or []:
        ig = p.get("instagram_business_account") or {}
        out.append({"id": p["id"], "name": p.get("name", ""), "token": p.get("access_token", ""),
                    "ig_id": ig.get("id", ""), "ig_username": ig.get("username", "")})
    if not out:
        raise MetaError("Aucune Page Facebook accessible avec ce compte (une Page est nécessaire, y compris pour "
                        "Instagram).")
    return out


def pick(pgs: list[dict], platform: str, name: str) -> dict:
    """Page (Facebook) ou compte Instagram correspondant au nom du compte dans Studio Vidéo."""
    n = name.strip().lower().lstrip("@")
    if platform == "instagram":
        cand = [p for p in pgs if p["ig_id"]]
        if not cand:
            raise MetaError("Aucun compte Instagram professionnel relié à vos Pages Facebook.")
        return next((p for p in cand if p["ig_username"].lower() == n), None) or \
            next((p for p in cand if n and n in p["ig_username"].lower()), cand[0])
    return next((p for p in pgs if p["name"].lower() == n), None) or \
        next((p for p in pgs if n and n in p["name"].lower()), pgs[0])


# -- Instagram -----------------------------------------------------------------------------------
def instagram_reel(path: Path, caption: str, ig_id: str, token: str, progress=lambda f, m: None,
                   is_cancelled=lambda: False, ep: Endpoints | None = None, poll_s: float = 5.0,
                   max_wait: float = 900, cover_ms: int | None = None) -> dict:
    ep = ep or Endpoints.from_env()
    path = Path(path)
    if not path.exists():
        raise MetaError(f"Vidéo introuvable : {path}")
    size = path.stat().st_size
    params = {"media_type": "REELS", "upload_type": "resumable", "caption": caption[:2200], "share_to_feed": "true",
              "access_token": token}
    if cover_ms:
        params["thumb_offset"] = str(int(cover_ms))
    c = _call("POST", ep.graph + f"/{ig_id}/media", params)
    cid = c.get("id")
    if not cid:
        raise MetaError("Instagram n'a pas créé de conteneur.")
    up = c.get("uri") or f"{ep.rupload}/ig-api-upload/{VERSION}/{cid}"
    progress(0.1, "Envoi vers Instagram")
    _rupload(up, path, size, token)
    progress(0.6, "Traitement par Instagram…")
    end = time.time() + max_wait
    while True:
        st = _call("GET", ep.graph + f"/{cid}", {"fields": "status_code,status", "access_token": token})
        code = st.get("status_code")
        if code == "FINISHED":
            break
        if code in ("ERROR", "EXPIRED"):
            raise MetaError(f"Instagram a refusé la vidéo : {st.get('status', code)}")
        if time.time() > end:
            raise MetaError("Instagram traite encore la vidéo : réessayez la publication plus tard.")
        if is_cancelled():
            raise MetaError("Annulé.")
        time.sleep(poll_s)
    pub = _call("POST", ep.graph + f"/{ig_id}/media_publish", {"creation_id": cid, "access_token": token})
    mid = pub.get("id")
    if not mid:
        raise MetaError("Instagram n'a pas confirmé la publication.")
    link = _call("GET", ep.graph + f"/{mid}", {"fields": "permalink", "access_token": token}).get("permalink", "")
    return {"id": mid, "url": link}


def _rupload(url: str, path: Path, size: int, token: str) -> None:
    for attempt in range(4):
        try:
            with open(path, "rb") as f:
                res = _call("POST", url, headers={"Authorization": f"OAuth {token}", "offset": "0",
                                                  "file_size": str(size)}, data=f.read(), timeout=600)
            if res.get("success") is False:
                raise MetaError(f"Envoi refusé : {res}")
            return
        except MetaError as exc:
            if "injoignable" in str(exc) and attempt < 3:
                time.sleep(2 ** attempt)
                continue
            raise


def instagram_stats(media_ids: list[str], token: str, ep: Endpoints | None = None) -> dict[str, dict]:
    ep = ep or Endpoints.from_env()
    out = {}
    for mid in media_ids:
        d = _call("GET", ep.graph + f"/{mid}", {"fields": "like_count,comments_count", "access_token": token})
        m = {}
        if d.get("like_count") is not None:
            m["likes"] = int(d["like_count"])
        if d.get("comments_count") is not None:
            m["comments"] = int(d["comments_count"])
        try:
            ins = _call("GET", ep.graph + f"/{mid}/insights", {"metric": "views", "access_token": token})
            for row in ins.get("data") or []:
                if row.get("name") == "views" and row.get("values"):
                    m["views"] = int(row["values"][0].get("value", 0))
        except MetaError:
            pass                                        # vues non fournies : on n'invente rien
        out[mid] = m
    return out


# -- Facebook ------------------------------------------------------------------------------------
def facebook_reel(path: Path, description: str, page_id: str, token: str, progress=lambda f, m: None,
                  ep: Endpoints | None = None, draft: bool = False) -> dict:
    """Reel de la Page ; draft : laissé en brouillon (à publier depuis Meta Business Suite)."""
    ep = ep or Endpoints.from_env()
    path = Path(path)
    if not path.exists():
        raise MetaError(f"Vidéo introuvable : {path}")
    start = _call("POST", ep.graph + f"/{page_id}/video_reels", {"upload_phase": "start", "access_token": token})
    vid, url = start.get("video_id"), start.get("upload_url")
    if not vid or not url:
        raise MetaError("Facebook n'a pas fourni d'adresse d'envoi.")
    progress(0.1, "Envoi vers Facebook")
    _rupload(url, path, path.stat().st_size, token)
    progress(0.8, "Brouillon sur la Page" if draft else "Publication sur la Page")
    fin = _call("POST", ep.graph + f"/{page_id}/video_reels", {
        "upload_phase": "finish", "video_id": vid, "video_state": "DRAFT" if draft else "PUBLISHED",
        "description": description[:5000], "access_token": token})
    if not fin.get("success", True):
        raise MetaError("Facebook n'a pas confirmé la publication.")
    return {"id": vid, "url": f"https://www.facebook.com/reel/{vid}"}
