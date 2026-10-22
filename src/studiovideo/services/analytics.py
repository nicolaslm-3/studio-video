"""Analytiques (V4, cahier des charges §20).

Chaque relevé est horodaté (historique conservé). Une clé absente = indicateur non fourni :
jamais remplacé par zéro. La source (« api » ou « manuel ») est toujours affichée.
"""
from __future__ import annotations

import json
import re

from ..db.database import Database

METRICS = ("views", "likes", "comments", "shares")
DETAILS = ("watch_minutes", "avg_view_s", "avg_view_pct")      # YouTube Analytics (chaîne connectée)
LABELS = {"views": "Vues", "likes": "J'aime", "comments": "Commentaires", "shares": "Partages",
          "watch_minutes": "Minutes vues", "avg_view_s": "Durée moyenne vue", "avg_view_pct": "% moyen vu"}


class AnalyticsService:
    def __init__(self, db: Database):
        self.db = db

    def record(self, publication_id: int, metrics: dict, source: str = "api") -> None:
        clean: dict = {k: int(v) for k, v in metrics.items() if k in METRICS and v is not None and int(v) >= 0}
        clean.update({k: round(float(v), 2) for k, v in metrics.items() if k in DETAILS and v is not None})
        if metrics.get("retention"):
            clean["retention"] = [[round(float(a), 3), round(float(b), 4)] for a, b in metrics["retention"]]
        clean["_source"] = source
        with self.db.transaction() as c:
            c.execute("INSERT INTO analytics_snapshot(publication_id, metrics_json) VALUES (?, ?)",
                      (publication_id, json.dumps(clean)))

    def history(self, publication_id: int) -> list[tuple[str, dict]]:
        return [(r["captured_at"], json.loads(r["metrics_json"])) for r in self.db.query(
            "SELECT captured_at, metrics_json FROM analytics_snapshot WHERE publication_id = ? ORDER BY id",
            (publication_id,))]

    def latest(self, concept_id: int | None = None, platform: str | None = None) -> list[dict]:
        """Dernier relevé de chaque publication publiée (ou programmée sur la plateforme)."""
        sql = ("SELECT p.id, p.status, p.public_url, p.remote_id, p.last_attempt_at, a.platform, a.name account, "
               "a.id account_id, c.id concept_id, c.name concept, c.color, COALESCE(p.title, m.title) title, "
               "(SELECT metrics_json FROM analytics_snapshot s WHERE s.publication_id = p.id ORDER BY s.id DESC "
               "LIMIT 1) metrics, (SELECT captured_at FROM analytics_snapshot s WHERE s.publication_id = p.id "
               "ORDER BY s.id DESC LIMIT 1) captured_at "
               "FROM publication p JOIN master_publication m ON m.id = p.master_id "
               "JOIN concept c ON c.id = m.concept_id JOIN social_account a ON a.id = p.social_account_id "
               "WHERE p.status IN ('published', 'scheduled_platform', 'manual_action') AND p.remote_id != ''")
        params: list = []
        if concept_id:
            sql += " AND c.id = ?"
            params.append(concept_id)
        if platform:
            sql += " AND a.platform = ?"
            params.append(platform)
        rows = self.db.query(sql + " ORDER BY p.last_attempt_at DESC", tuple(params))
        out = []
        for r in rows:
            d = dict(r)
            d["metrics"] = json.loads(r["metrics"]) if r["metrics"] else {}
            out.append(d)
        return out

    @staticmethod
    def aggregate(rows: list[dict], key: str) -> list[dict]:
        """Totaux par concept / plateforme / compte. Un total n'additionne que les valeurs fournies ;
        `n_<metric>` indique combien de publications l'ont fournie."""
        groups: dict = {}
        for r in rows:
            g = groups.setdefault(r[key], {"key": r[key], "publications": 0, "color": r.get("color")})
            g["publications"] += 1
            for m in METRICS:
                if m in r["metrics"]:
                    g[m] = g.get(m, 0) + r["metrics"][m]
                    g["n_" + m] = g.get("n_" + m, 0) + 1
        out = list(groups.values())
        for g in out:
            if g.get("views"):
                inter = sum(g.get(m, 0) for m in ("likes", "comments", "shares"))
                g["engagement"] = inter / g["views"]
        return sorted(out, key=lambda g: -g.get("views", 0))

    def tiktok_ids(self) -> dict[int, list[tuple[int, str]]]:
        """{compte: [(publication, id de la vidéo TikTok)]} pour les vidéos publiées (API ou lien confirmé)."""
        return _tiktok_rows(self.db)

    def youtube_ids(self) -> list[tuple[int, str]]:
        """[(publication, id vidéo)] pour les publications YouTube : envoyées par l'API ou confirmées par lien."""
        out = []
        for r in self.db.query("SELECT p.id, p.remote_id, p.public_url FROM publication p "
                               "JOIN social_account a ON a.id = p.social_account_id WHERE a.platform = 'youtube' "
                               "AND p.status IN ('published','scheduled_platform','manual_action') "
                               "AND p.remote_id != ''"):
            vid = youtube_id(r["remote_id"]) or youtube_id(r["public_url"] or "")
            if vid:
                out.append((r["id"], vid))
        return out


def _tiktok_rows(db) -> dict:
    out: dict = {}
    for r in db.query("SELECT p.id, p.remote_id, p.public_url, p.social_account_id FROM publication p "
                      "JOIN social_account a ON a.id = p.social_account_id WHERE a.platform = 'tiktok' "
                      "AND p.status = 'published'"):
        m = re.search(r"/video/(\d+)", r["public_url"] or "") or re.fullmatch(r"(\d{10,})", r["remote_id"] or "")
        if m:
            out.setdefault(r["social_account_id"], []).append((r["id"], m.group(1)))
    return out


_YT = re.compile(r"(?:youtu\.be/|[?&]v=|/shorts/|/live/|/embed/)([A-Za-z0-9_-]{6,20})")


def youtube_id(s: str) -> str | None:
    """Identifiant de vidéo à partir d'un identifiant brut ou d'un lien YouTube."""
    s = (s or "").strip()
    if re.fullmatch(r"[A-Za-z0-9_-]{6,20}", s):
        return s
    m = _YT.search(s)
    return m.group(1) if m else None
