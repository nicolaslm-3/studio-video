"""Publication sur X (API v2) — cahier des charges §18, V3.

- Connexion : OAuth 2.0 avec PKCE, redirection locale fixe (X exige l'adresse exacte :
  http://127.0.0.1:8768/callback), autorisation « offline.access » pour le renouvellement.
- Vidéo : initialize → append (blocs de 4 Mo) → finalize → suivi du traitement → POST /2/tweets.
- L'accès en écriture dépend de l'offre développeur X du compte (éventuellement payante) : un refus
  est affiché tel quel.
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
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

PORT = 8768
SCOPES = "tweet.read tweet.write users.read media.write offline.access"
CHUNK = 4 * 1024 * 1024


class XError(RuntimeError):
    pass


@dataclass
class Endpoints:
    auth: str = "https://x.com/i/oauth2/authorize"
    api: str = "https://api.x.com"

    @classmethod
    def from_env(cls) -> "Endpoints":
        base = os.environ.get("STUDIOVIDEO_X_TEST_URL")
        return cls(base + "/auth", base) if base else cls()


def redirect_uri(port: int = PORT) -> str:
    return f"http://127.0.0.1:{port}/callback"


def _call(method: str, url: str, token: str | None = None, body=None, form: dict | None = None,
          basic: tuple[str, str] | None = None, raw: bytes | None = None, ctype: str | None = None,
          timeout: float = 120) -> dict:
    headers, data = {}, None
    if form is not None:
        data = urllib.parse.urlencode(form).encode()
        headers["Content-Type"] = "application/x-www-form-urlencoded"
    elif body is not None:
        data = json.dumps(body).encode()
        headers["Content-Type"] = "application/json"
    elif raw is not None:
        data, headers["Content-Type"] = raw, ctype or "application/octet-stream"
    if token:
        headers["Authorization"] = f"Bearer {token}"
    if basic:
        headers["Authorization"] = "Basic " + base64.b64encode(f"{basic[0]}:{basic[1]}".encode()).decode()
    req = urllib.request.Request(url, data=data if data is not None else (b"" if method == "POST" else None),
                                 headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            txt = r.read().decode()
            return json.loads(txt) if txt.strip() else {}
    except urllib.error.HTTPError as exc:
        txt = exc.read()[:600].decode("utf-8", "replace")
        if exc.code == 401:
            raise XError("Autorisation X expirée ou révoquée : reconnectez le compte.") from exc
        if exc.code == 403:
            raise XError(f"X refuse cette action (offre développeur ou droits du compte) : {txt[:300]}") from exc
        if exc.code == 429:
            raise XError("Limite de l'API X atteinte : réessayez plus tard.") from exc
        if "invalid_grant" in txt or "invalid_request" in txt and "refresh" in txt:
            raise XError("Autorisation X expirée ou révoquée : reconnectez le compte.") from exc
        raise XError(f"X a répondu {exc.code} : {txt[:300]}") from exc
    except (urllib.error.URLError, OSError) as exc:
        raise XError(f"X injoignable : {exc}") from exc


class _Catcher(http.server.BaseHTTPRequestHandler):
    result: dict = {}

    def log_message(self, *a):
        pass

    def do_GET(self):
        q = dict(urllib.parse.parse_qsl(urllib.parse.urlparse(self.path).query))
        if "code" in q or "error" in q:
            type(self).result.update(q)
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        self.wfile.write(("<html><body style='font-family:sans-serif;padding:40px'><h2>Studio Vidéo</h2><p>" +
                          ("Connexion X réussie. Vous pouvez fermer cet onglet." if "code" in q else
                           "Connexion refusée.") + "</p></body></html>").encode())


def connect(client_id: str, client_secret: str, open_browser: Callable[[str], None], timeout: float = 300,
            ep: Endpoints | None = None, port: int = PORT, is_cancelled: Callable[[], bool] = lambda: False) -> dict:
    if not client_id:
        raise XError("Identifiant client X absent (Paramètres → Publication).")
    ep = ep or Endpoints.from_env()
    verifier = base64.urlsafe_b64encode(_secrets.token_bytes(48)).rstrip(b"=").decode()
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    state = _secrets.token_urlsafe(16)
    handler = type("Catcher", (_Catcher,), {"result": {}})
    try:
        srv = http.server.HTTPServer(("127.0.0.1", port), handler)
    except OSError as exc:
        raise XError(f"Le port local {port} est occupé : fermez l'application qui l'utilise et réessayez.") from exc
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    redirect = redirect_uri(port)
    try:
        open_browser(ep.auth + "?" + urllib.parse.urlencode({
            "response_type": "code", "client_id": client_id, "redirect_uri": redirect, "scope": SCOPES,
            "state": state, "code_challenge": challenge, "code_challenge_method": "S256"}))
        end = time.time() + timeout
        while not handler.result and time.time() < end:
            if is_cancelled():
                raise XError("Connexion annulée.")
            time.sleep(0.2)
        res = dict(handler.result)
    finally:
        srv.shutdown()
        srv.server_close()
    if not res:
        raise XError("Pas de réponse de X dans le temps imparti.")
    if res.get("state") != state:
        raise XError("Réponse de X invalide (state).")
    if "error" in res:
        raise XError(f"Connexion refusée : {res.get('error_description') or res['error']}")
    form = {"code": res["code"], "grant_type": "authorization_code", "redirect_uri": redirect,
            "code_verifier": verifier, "client_id": client_id}
    tok = _call("POST", ep.api + "/2/oauth2/token", form=form,
                basic=(client_id, client_secret) if client_secret else None)
    if not tok.get("refresh_token"):
        raise XError("X n'a pas fourni de jeton de renouvellement (autorisation offline.access).")
    return tok


def refresh(client_id: str, client_secret: str, refresh_token: str, ep: Endpoints | None = None) -> dict:
    ep = ep or Endpoints.from_env()
    return _call("POST", ep.api + "/2/oauth2/token", form={
        "grant_type": "refresh_token", "refresh_token": refresh_token, "client_id": client_id},
        basic=(client_id, client_secret) if client_secret else None)


def me(token: str, ep: Endpoints | None = None) -> dict:
    ep = ep or Endpoints.from_env()
    return _call("GET", ep.api + "/2/users/me", token).get("data") or {}


def _multipart(fields: dict, file_field: str, data: bytes) -> tuple[bytes, str]:
    b = uuid.uuid4().hex
    parts = []
    for k, v in fields.items():
        parts.append(f"--{b}\r\nContent-Disposition: form-data; name=\"{k}\"\r\n\r\n{v}\r\n".encode())
    parts.append(f"--{b}\r\nContent-Disposition: form-data; name=\"{file_field}\"; filename=\"blob\"\r\n"
                 f"Content-Type: application/octet-stream\r\n\r\n".encode() + data + b"\r\n")
    parts.append(f"--{b}--\r\n".encode())
    return b"".join(parts), f"multipart/form-data; boundary={b}"


def post_video(path: Path, text: str, token: str, progress=lambda f, m: None, is_cancelled=lambda: False,
               ep: Endpoints | None = None, max_wait: float = 900) -> dict:
    """Envoie la vidéo puis publie le message. Retourne {"id": id du post}."""
    ep = ep or Endpoints.from_env()
    path = Path(path)
    if not path.exists():
        raise XError(f"Vidéo introuvable : {path}")
    size = path.stat().st_size
    init = _call("POST", ep.api + "/2/media/upload/initialize", token, body={
        "media_type": "video/mp4", "total_bytes": size, "media_category": "tweet_video"}).get("data") or {}
    mid = init.get("id")
    if not mid:
        raise XError("X n'a pas fourni d'identifiant de média.")
    with open(path, "rb") as f:
        i = 0
        while chunk := f.read(CHUNK):
            if is_cancelled():
                raise XError("Envoi annulé.")
            body, ctype = _multipart({"segment_index": str(i)}, "media", chunk)
            _call("POST", ep.api + f"/2/media/upload/{mid}/append", token, raw=body, ctype=ctype)
            i += 1
            progress(0.8 * min(1.0, f.tell() / size), f"Envoi {f.tell() >> 20}/{size >> 20} Mo")
    fin = _call("POST", ep.api + f"/2/media/upload/{mid}/finalize", token).get("data") or {}
    info = fin.get("processing_info")
    end = time.time() + max_wait
    while info and info.get("state") in ("pending", "in_progress"):
        if time.time() > end:
            raise XError("X traite encore la vidéo : réessayez plus tard.")
        if is_cancelled():
            raise XError("Annulé.")
        progress(0.9, "Traitement par X…")
        time.sleep(min(30, max(0.01, float(info.get("check_after_secs") or 2))))
        info = (_call("GET", ep.api + f"/2/media/upload?command=STATUS&media_id={mid}", token).get("data") or {}) \
            .get("processing_info")
    if info and info.get("state") == "failed":
        raise XError(f"X a refusé la vidéo : {(info.get('error') or {}).get('message', 'raison inconnue')}")
    post = _call("POST", ep.api + "/2/tweets", token, body={"text": text[:280], "media": {"media_ids": [mid]}})
    pid = (post.get("data") or {}).get("id")
    if not pid:
        raise XError("X n'a pas confirmé la publication.")
    return {"id": pid}


def post_stats(token: str, ids: list[str], ep: Endpoints | None = None) -> dict[str, dict]:
    ep = ep or Endpoints.from_env()
    out = {}
    for i in range(0, len(ids), 100):
        data = _call("GET", ep.api + "/2/tweets?" + urllib.parse.urlencode(
            {"ids": ",".join(ids[i:i + 100]), "tweet.fields": "public_metrics"}), token).get("data") or []
        for t in data:
            pm = t.get("public_metrics") or {}
            m = {}
            if "impression_count" in pm:
                m["views"] = int(pm["impression_count"])
            if "like_count" in pm:
                m["likes"] = int(pm["like_count"])
            if "reply_count" in pm:
                m["comments"] = int(pm["reply_count"])
            if "retweet_count" in pm or "quote_count" in pm:
                m["shares"] = int(pm.get("retweet_count", 0)) + int(pm.get("quote_count", 0))
            out[str(t["id"])] = m
    return out
