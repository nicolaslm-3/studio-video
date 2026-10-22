"""Mise en ligne YouTube (API YouTube Data v3) — cahier des charges §18, V3.

- Connexion : OAuth 2.0 « application de bureau » avec redirection locale (127.0.0.1) et PKCE.
  L'identifiant client vient du projet Google Cloud de l'utilisateur (Paramètres → Comptes).
- Le jeton de renouvellement est rangé dans le coffre d'identifiants Windows, jamais en clair.
- Envoi « reprenable » par blocs : une coupure réseau reprend là où l'envoi s'était arrêté.
- Programmation : si l'heure prévue est future, la vidéo est envoyée en privé avec `publishAt`
  et YouTube la publie lui-même à l'heure dite (le PC peut être éteint).

Quota : réserve « Video Uploads » propre aux envois (100 par jour et par projet, octobre 2026).
Si YouTube laisse une vidéo en privé malgré la visibilité demandée, l'appelant le signale.
"""
from __future__ import annotations

import base64
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
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
API_URL = "https://www.googleapis.com"
SCOPES = ("https://www.googleapis.com/auth/youtube.upload https://www.googleapis.com/auth/youtube.readonly "
          "https://www.googleapis.com/auth/yt-analytics.readonly")
ANALYTICS_URL = "https://youtubeanalytics.googleapis.com"
CHUNK = 8 * 1024 * 1024


class YouTubeError(RuntimeError):
    pass


@dataclass
class Endpoints:
    auth: str = AUTH_URL
    token: str = TOKEN_URL
    api: str = API_URL
    analytics: str = ANALYTICS_URL

    @classmethod
    def from_env(cls) -> "Endpoints":
        base = os.environ.get("STUDIOVIDEO_YOUTUBE_TEST_URL")     # serveur simulé (tests)
        return cls(base + "/auth", base + "/token", base, base) if base else cls()


def _post_form(url: str, data: dict, timeout: float = 30) -> dict:
    req = urllib.request.Request(url, data=urllib.parse.urlencode(data).encode(),
                                 headers={"Content-Type": "application/x-www-form-urlencoded"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode())
    except urllib.error.HTTPError as exc:
        body = exc.read()[:400].decode("utf-8", "replace")
        if "invalid_grant" in body:
            raise YouTubeError("Autorisation expirée ou révoquée : reconnectez le compte.") from exc
        raise YouTubeError(f"Google a répondu {exc.code} : {body}") from exc
    except (urllib.error.URLError, OSError) as exc:
        raise YouTubeError(f"Google injoignable : {exc}") from exc


# -- connexion -----------------------------------------------------------------------------------
class _Catcher(http.server.BaseHTTPRequestHandler):
    result: dict = {}

    def log_message(self, *a):
        pass

    def do_GET(self):
        q = dict(urllib.parse.parse_qsl(urllib.parse.urlparse(self.path).query))
        if "code" in q or "error" in q:
            type(self).result.update(q)
            msg = ("Connexion réussie. Vous pouvez fermer cet onglet et revenir dans Studio Vidéo."
                   if "code" in q else f"Connexion refusée ({q.get('error')}).")
        else:
            msg = "En attente de Google…"
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        self.wfile.write(f"<html><body style='font-family:sans-serif;padding:40px'><h2>Studio Vidéo</h2>"
                         f"<p>{msg}</p></body></html>".encode())


def connect(client_id: str, client_secret: str, open_browser: Callable[[str], None],
            timeout: float = 300, ep: Endpoints | None = None,
            is_cancelled: Callable[[], bool] = lambda: False) -> dict:
    """Ouvre la page de consentement Google et attend le retour sur 127.0.0.1. Retourne les jetons."""
    if not client_id:
        raise YouTubeError("Identifiant client Google absent (Paramètres → Comptes → YouTube).")
    ep = ep or Endpoints.from_env()
    verifier = base64.urlsafe_b64encode(_secrets.token_bytes(48)).rstrip(b"=").decode()
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    state = _secrets.token_urlsafe(16)
    handler = type("Catcher", (_Catcher,), {"result": {}})
    srv = http.server.HTTPServer(("127.0.0.1", 0), handler)
    redirect = f"http://127.0.0.1:{srv.server_port}"
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        url = ep.auth + "?" + urllib.parse.urlencode({
            "client_id": client_id, "redirect_uri": redirect, "response_type": "code", "scope": SCOPES,
            "code_challenge": challenge, "code_challenge_method": "S256", "access_type": "offline",
            "prompt": "consent", "state": state})
        open_browser(url)
        end = time.time() + timeout
        while not handler.result and time.time() < end:
            if is_cancelled():
                raise YouTubeError("Connexion annulée.")
            time.sleep(0.2)
        res = dict(handler.result)
    finally:
        srv.shutdown()
        srv.server_close()
    if not res:
        raise YouTubeError("Pas de réponse de Google dans le temps imparti.")
    if res.get("state") != state:
        raise YouTubeError("Réponse de Google invalide (state).")
    if "error" in res:
        raise YouTubeError(f"Connexion refusée : {res['error']}")
    tok = _post_form(ep.token, {"code": res["code"], "client_id": client_id, "client_secret": client_secret,
                                "redirect_uri": redirect, "grant_type": "authorization_code",
                                "code_verifier": verifier})
    if not tok.get("refresh_token"):
        raise YouTubeError("Google n'a pas fourni de jeton de renouvellement.")
    return tok


def access_token(client_id: str, client_secret: str, refresh_token: str, ep: Endpoints | None = None) -> str:
    ep = ep or Endpoints.from_env()
    tok = _post_form(ep.token, {"client_id": client_id, "client_secret": client_secret,
                                "refresh_token": refresh_token, "grant_type": "refresh_token"})
    return tok["access_token"]


def _api(method: str, url: str, token: str, body: bytes | None = None, headers: dict | None = None,
         timeout: float = 60):
    h = {"Authorization": f"Bearer {token}"}
    h.update(headers or {})
    req = urllib.request.Request(url, data=body, headers=h, method=method)
    return urllib.request.urlopen(req, timeout=timeout)


def channel(token: str, ep: Endpoints | None = None) -> dict:
    ep = ep or Endpoints.from_env()
    try:
        with _api("GET", ep.api + "/youtube/v3/channels?part=snippet&mine=true", token) as r:
            items = json.loads(r.read().decode()).get("items") or []
    except urllib.error.HTTPError as exc:
        raise YouTubeError(f"Lecture de la chaîne impossible ({exc.code}).") from exc
    if not items:
        raise YouTubeError("Ce compte Google n'a pas de chaîne YouTube.")
    return {"id": items[0]["id"], "title": items[0]["snippet"]["title"]}


# -- envoi ---------------------------------------------------------------------------------------
def to_rfc3339(local: str) -> str:
    """« 2026-11-05T18:00 » (heure locale) → « 2026-11-05T17:00:00Z »."""
    dt = datetime.strptime(local, "%Y-%m-%dT%H:%M").astimezone(timezone.utc)
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def metadata(title: str, description: str, hashtags: str, privacy: str = "public",
             publish_at_local: str | None = None, category: str = "22", made_for_kids: bool = False) -> dict:
    from .rules import normalize_hashtags
    tags = [t.lstrip("#") for t in normalize_hashtags(hashtags).split()]
    desc = "\n\n".join(p for p in (description.strip(), normalize_hashtags(hashtags)) if p)
    status = {"privacyStatus": privacy, "selfDeclaredMadeForKids": made_for_kids}
    if publish_at_local:
        status["privacyStatus"] = "private"                 # exigé par YouTube pour une publication programmée
        status["publishAt"] = to_rfc3339(publish_at_local)
    return {"snippet": {"title": title[:100], "description": desc[:5000], "tags": tags[:60],
                        "categoryId": category}, "status": status}


def upload(path: Path, meta: dict, token: str, progress: Callable[[float, str], None] = lambda f, m: None,
           is_cancelled: Callable[[], bool] = lambda: False, ep: Endpoints | None = None,
           chunk: int = CHUNK, retries: int = 5, session: dict | None = None,
           on_session: Callable[[str], None] = lambda url: None) -> dict:
    """Envoi reprenable. Retourne la ressource vidéo créée (id, status…).

    session : {"url"} d'un envoi interrompu (logiciel fermé pendant l'envoi) : on reprend ce même envoi,
    ou l'on récupère la vidéo s'il était déjà terminé — jamais de doublon. on_session mémorise l'adresse."""
    ep = ep or Endpoints.from_env()
    path = Path(path)
    if not path.exists():
        raise YouTubeError(f"Vidéo introuvable : {path}")
    total = path.stat().st_size
    if session and session.get("url"):
        try:
            sent, done = _resume_point(session["url"], token, total)
            if done is not None:
                return done
            return _send(path, session["url"], token, total, sent, progress, is_cancelled, chunk, retries)
        except YouTubeError:
            pass                       # session expirée (> 1 semaine) : nouvel envoi
    body = json.dumps(meta).encode()
    try:
        with _api("POST", ep.api + "/upload/youtube/v3/videos?uploadType=resumable&part=snippet,status", token, body,
                  {"Content-Type": "application/json; charset=UTF-8", "X-Upload-Content-Length": str(total),
                   "X-Upload-Content-Type": "video/mp4"}) as r:
            session_url = r.headers["Location"]
    except urllib.error.HTTPError as exc:
        detail = exc.read()[:400].decode("utf-8", "replace")
        if exc.code == 403 and "quota" in detail.lower():
            raise YouTubeError("Quota quotidien d'envois YouTube atteint (100 par jour) : réessayez demain.") from exc
        raise YouTubeError(f"YouTube a refusé l'envoi ({exc.code}) : {detail}") from exc
    except (urllib.error.URLError, OSError) as exc:
        raise YouTubeError(f"YouTube injoignable : {exc}") from exc
    on_session(session_url)
    return _send(path, session_url, token, total, 0, progress, is_cancelled, chunk, retries)


def _send(path: Path, session: str, token: str, total: int, sent: int, progress, is_cancelled, chunk: int,
          retries: int) -> dict:
    fails = 0
    with open(path, "rb") as f:
        while True:
            if is_cancelled():
                raise YouTubeError("Envoi annulé.")
            f.seek(sent)
            data = f.read(chunk)
            last = sent + len(data) - 1
            try:
                with _api("PUT", session, token, data, {"Content-Length": str(len(data)),
                                                        "Content-Range": f"bytes {sent}-{last}/{total}"},
                          timeout=300) as r:
                    return json.loads(r.read().decode())        # 200/201 : terminé
            except urllib.error.HTTPError as exc:
                if exc.code == 308:                               # bloc reçu, continuer
                    rng = exc.headers.get("Range")
                    sent = int(rng.split("-")[1]) + 1 if rng else 0
                    fails = 0
                    progress(sent / total, f"Envoi {sent >> 20}/{total >> 20} Mo")
                    continue
                if exc.code in (500, 502, 503, 504) and fails < retries:
                    fails += 1
                    time.sleep(min(30, 2 ** fails) * _BACKOFF)
                    sent, done = _resume_point(session, token, total)
                    if done is not None:
                        return done
                    continue
                raise YouTubeError(f"Envoi interrompu ({exc.code}) : "
                                   f"{exc.read()[:300].decode('utf-8', 'replace')}") from exc
            except (urllib.error.URLError, OSError) as exc:
                if fails >= retries:
                    raise YouTubeError(f"Connexion perdue pendant l'envoi : {exc}") from exc
                fails += 1
                time.sleep(min(30, 2 ** fails) * _BACKOFF)
                sent, done = _resume_point(session, token, total)
                if done is not None:
                    return done


_BACKOFF = 1.0   # réduit dans les tests


def _resume_point(session: str, token: str, total: int) -> tuple[int, dict | None]:
    """Demande à YouTube où en est l'envoi : (octets reçus, ressource si l'envoi est déjà complet)."""
    try:
        with _api("PUT", session, token, b"", {"Content-Length": "0", "Content-Range": f"bytes */{total}"}) as r:
            return total, json.loads(r.read().decode())
    except urllib.error.HTTPError as exc:
        if exc.code == 308:
            rng = exc.headers.get("Range")
            return (int(rng.split("-")[1]) + 1 if rng else 0), None
        raise YouTubeError(f"Reprise de l'envoi impossible ({exc.code}).") from exc


def video_stats(token: str, ids: list[str], ep: Endpoints | None = None) -> dict[str, dict]:
    """Statistiques publiques (vues, j'aime, commentaires) par identifiant de vidéo, par lots de 50.

    Une clé absente signifie « non fournie » (ex. : j'aime masqués par la chaîne)."""
    ep = ep or Endpoints.from_env()
    out: dict[str, dict] = {}
    for i in range(0, len(ids), 50):
        part = ",".join(ids[i:i + 50])
        try:
            with _api("GET", ep.api + f"/youtube/v3/videos?part=statistics&id={urllib.parse.quote(part)}",
                      token) as r:
                items = json.loads(r.read().decode()).get("items") or []
        except urllib.error.HTTPError as exc:
            raise YouTubeError(f"Lecture des statistiques impossible ({exc.code}).") from exc
        except (urllib.error.URLError, OSError) as exc:
            raise YouTubeError(f"YouTube injoignable : {exc}") from exc
        for it in items:
            st = it.get("statistics") or {}
            m = {}
            for src, dst in (("viewCount", "views"), ("likeCount", "likes"), ("commentCount", "comments")):
                if src in st:
                    m[dst] = int(st[src])
            out[it["id"]] = m
    return out


class ScopeMissing(YouTubeError):
    pass


def _report(token: str, params: dict, ep: Endpoints) -> dict:
    url = ep.analytics + "/v2/reports?" + urllib.parse.urlencode(params)
    try:
        with _api("GET", url, token) as r:
            return json.loads(r.read().decode())
    except urllib.error.HTTPError as exc:
        body = exc.read()[:400].decode("utf-8", "replace")
        if exc.code == 403 and ("scope" in body.lower() or "insufficient" in body.lower()):
            raise ScopeMissing("Statistiques détaillées non autorisées : reconnectez le compte YouTube "
                               "(Paramètres → Comptes) pour accepter la nouvelle autorisation.") from exc
        raise YouTubeError(f"YouTube Analytics a répondu {exc.code} : {body}") from exc
    except (urllib.error.URLError, OSError) as exc:
        raise YouTubeError(f"YouTube Analytics injoignable : {exc}") from exc


def video_analytics(token: str, video_id: str, start: str, end: str, ep: Endpoints | None = None) -> dict:
    """Durée de visionnage, durée moyenne, % moyen vu, et courbe de rétention d'une vidéo de la chaîne.

    start / end : « AAAA-MM-JJ ». Les données YouTube Analytics arrivent avec 2 à 3 jours de décalage."""
    ep = ep or Endpoints.from_env()
    base = {"ids": "channel==MINE", "startDate": start, "endDate": end, "filters": f"video=={video_id}"}
    out: dict = {}
    rep = _report(token, {**base, "metrics": "estimatedMinutesWatched,averageViewDuration,averageViewPercentage"}, ep)
    rows = rep.get("rows") or []
    if rows:
        names = [h["name"] for h in rep.get("columnHeaders", [])]
        row = dict(zip(names, rows[0]))
        if row.get("estimatedMinutesWatched") is not None:
            out["watch_minutes"] = float(row["estimatedMinutesWatched"])
        if row.get("averageViewDuration") is not None:
            out["avg_view_s"] = float(row["averageViewDuration"])
        if row.get("averageViewPercentage") is not None:
            out["avg_view_pct"] = float(row["averageViewPercentage"])
    rep = _report(token, {**base, "dimensions": "elapsedVideoTimeRatio", "metrics": "audienceWatchRatio"}, ep)
    curve = [(float(r[0]), float(r[1])) for r in (rep.get("rows") or [])]
    if curve:
        out["retention"] = sorted(curve)
    return out


def set_thumbnail(token: str, video_id: str, image: Path, ep: Endpoints | None = None) -> None:
    """Miniature personnalisée (la chaîne doit être validée par téléphone chez YouTube)."""
    ep = ep or Endpoints.from_env()
    data = Path(image).read_bytes()
    url = ep.api + "/upload/youtube/v3/thumbnails/set?" + urllib.parse.urlencode({"videoId": video_id,
                                                                                  "uploadType": "media"})
    try:
        with _api("POST", url, token, data, {"Content-Type": "image/jpeg", "Content-Length": str(len(data))}):
            return
    except urllib.error.HTTPError as exc:
        body = exc.read()[:300].decode("utf-8", "replace")
        if exc.code == 403:
            raise YouTubeError("Miniature refusée : la chaîne doit être validée (youtube.com/verify) pour les "
                               "miniatures personnalisées.") from exc
        raise YouTubeError(f"Miniature refusée ({exc.code}) : {body}") from exc
    except (urllib.error.URLError, OSError) as exc:
        raise YouTubeError(f"YouTube injoignable : {exc}") from exc
