"""Publication maître et publications par destination (cahier des charges §18).

Règles :
- une publication = un compte précis ; jamais créée pour un compte non choisi ;
- un compte hors concept exige une confirmation explicite (is_exceptional) ;
- titre/description/hashtags à NULL héritent de la publication maître :
  modifier la maître se propage, une modification locale reste locale ;
- aucun statut « publiée » sans confirmation de la plateforme (remote_id).
"""
from __future__ import annotations

import json
import sqlite3

from ..db.database import Database
from ..domain.models import Platform, Publication, PublicationStatus, SocialAccount

_INHERITED = ("title", "description", "hashtags", "thumbnail")


class PublicationError(ValueError):
    pass


class ExternalAccountNotConfirmed(PublicationError):
    def __init__(self, accounts: list[int]):
        super().__init__("Comptes extérieurs au concept non confirmés : " + ", ".join(map(str, accounts)))
        self.accounts = accounts


class PublicationService:
    def __init__(self, db: Database):
        self.db = db

    def create_master(self, concept_id: int, title: str = "", description: str = "",
                      hashtags: str = "", language: str = "fr", variant_id: int | None = None,
                      planned_at: str | None = None) -> int:
        with self.db.transaction() as c:
            return c.execute(
                "INSERT INTO master_publication(concept_id, variant_id, title, description, hashtags,"
                " language, planned_at) VALUES (?,?,?,?,?,?,?)",
                (concept_id, variant_id, title, description, hashtags, language, planned_at)).lastrowid

    def update_master(self, master_id: int, **fields) -> None:
        allowed = {"title", "description", "hashtags", "thumbnail", "language", "planned_at", "variant_id", "cover_s"}
        if set(fields) - allowed:
            raise PublicationError(f"Champs invalides : {set(fields) - allowed}")
        if fields:
            sets = ", ".join(f"{k} = ?" for k in fields)
            with self.db.transaction() as c:
                c.execute(f"UPDATE master_publication SET {sets} WHERE id = ?", (*fields.values(), master_id))

    def set_destinations(self, master_id: int, account_ids: list[int],
                         confirmed_external: set[int] | None = None) -> list[int]:
        """Crée une publication par compte choisi. Retourne les ids créés.

        Les publications déjà existantes pour un compte sont conservées (pas de doublon).
        """
        confirmed_external = confirmed_external or set()
        master = self.db.query_one("SELECT * FROM master_publication WHERE id = ?", (master_id,))
        if not master:
            raise PublicationError("Publication maître introuvable.")
        linked = {r["social_account_id"] for r in self.db.query(
            "SELECT social_account_id FROM concept_account WHERE concept_id = ?", (master["concept_id"],))}
        external = [a for a in account_ids if a not in linked]
        unconfirmed = [a for a in external if a not in confirmed_external]
        if unconfirmed:
            raise ExternalAccountNotConfirmed(unconfirmed)
        created = []
        with self.db.transaction() as c:
            for aid in dict.fromkeys(account_ids):
                try:
                    pid = c.execute(
                        "INSERT INTO publication(master_id, social_account_id, variant_id, is_exceptional)"
                        " VALUES (?,?,?,?)",
                        (master_id, aid, master["variant_id"], int(aid not in linked))).lastrowid
                    created.append(pid)
                except sqlite3.IntegrityError:
                    pass  # déjà présente
        self.db.audit("publication.destinations", f"master={master_id} accounts={account_ids}")
        return created

    def override(self, publication_id: int, **fields) -> None:
        """Modification locale d'une destination (ne touche pas les autres)."""
        allowed = set(_INHERITED) | {"scheduled_at", "schedule_mode"}
        if set(fields) - allowed:
            raise PublicationError(f"Champs invalides : {set(fields) - allowed}")
        sets = ", ".join(f"{k} = ?" for k in fields)
        with self.db.transaction() as c:
            c.execute(f"UPDATE publication SET {sets} WHERE id = ?", (*fields.values(), publication_id))

    def reset_to_master(self, publication_id: int, field: str) -> None:
        if field not in _INHERITED:
            raise PublicationError(field)
        with self.db.transaction() as c:
            c.execute(f"UPDATE publication SET {field} = NULL WHERE id = ?", (publication_id,))

    def set_status(self, publication_id: int, status: PublicationStatus, *, remote_id: str = "",
                   public_url: str = "", error: str = "") -> None:
        status = PublicationStatus(status)
        if status == PublicationStatus.PUBLISHED and not remote_id:
            raise PublicationError("Statut « Publiée » refusé sans identifiant confirmé par la plateforme.")
        with self.db.transaction() as c:
            c.execute("UPDATE publication SET status = ?, remote_id = COALESCE(NULLIF(?, ''), remote_id),"
                      " public_url = COALESCE(NULLIF(?, ''), public_url), last_error = ?,"
                      " last_attempt_at = strftime('%Y-%m-%dT%H:%M:%SZ','now') WHERE id = ?",
                      (status.value, remote_id, public_url, error, publication_id))
            c.execute("INSERT INTO publication_attempt(publication_id, outcome, detail) VALUES (?,?,?)",
                      (publication_id, status.value, error or remote_id))

    def list_for_master(self, master_id: int) -> list[Publication]:
        rows = self.db.query(
            "SELECT p.*, m.title m_title, m.description m_description, m.hashtags m_hashtags,"
            " a.workspace_id a_ws, a.platform a_platform, a.name a_name, a.public_url a_url,"
            " a.language a_lang, a.connection_status a_conn "
            "FROM publication p JOIN master_publication m ON m.id = p.master_id "
            "JOIN social_account a ON a.id = p.social_account_id "
            "WHERE p.master_id = ? ORDER BY a.platform, a.name", (master_id,))
        out = []
        for r in rows:
            acc = SocialAccount(id=r["social_account_id"], workspace_id=r["a_ws"],
                                platform=Platform(r["a_platform"]), name=r["a_name"],
                                public_url=r["a_url"], language=r["a_lang"], connection_status=r["a_conn"])
            overrides = {f for f in ("title", "description", "hashtags") if r[f] is not None}
            out.append(Publication(
                id=r["id"], master_id=r["master_id"], account=acc, is_exceptional=bool(r["is_exceptional"]),
                title=r["title"] if r["title"] is not None else r["m_title"],
                description=r["description"] if r["description"] is not None else r["m_description"],
                hashtags=r["hashtags"] if r["hashtags"] is not None else r["m_hashtags"],
                scheduled_at=r["scheduled_at"], schedule_mode=r["schedule_mode"],
                status=PublicationStatus(r["status"]), overrides=overrides))
        return out

    def upcoming(self, limit: int = 20) -> list[sqlite3.Row]:
        return self.db.query(
            "SELECT p.id, p.status, p.scheduled_at, a.platform, a.name account, c.name concept, c.color,"
            " COALESCE(p.title, m.title) title FROM publication p "
            "JOIN master_publication m ON m.id = p.master_id JOIN concept c ON c.id = m.concept_id "
            "JOIN social_account a ON a.id = p.social_account_id "
            "WHERE p.status NOT IN ('published','cancelled') ORDER BY p.scheduled_at IS NULL, p.scheduled_at "
            "LIMIT ?", (limit,))


    # -- V3 : calendrier et publication assistée ---------------------------------------
    _CAL = ("SELECT p.id, p.master_id, p.status, p.schedule_mode, p.last_error, p.public_url,"
            " COALESCE(p.scheduled_at, m.planned_at) at, a.platform, a.name account, a.id account_id,"
            " c.id concept_id, c.name concept, c.color, COALESCE(p.title, m.title) title,"
            " COALESCE(p.description, m.description) description, COALESCE(p.hashtags, m.hashtags) hashtags,"
            " m.variant_id, v.output_path, v.aspect, m.thumbnail, m.cover_s "
            "FROM publication p JOIN master_publication m ON m.id = p.master_id "
            "JOIN concept c ON c.id = m.concept_id JOIN social_account a ON a.id = p.social_account_id "
            "LEFT JOIN video_variant v ON v.id = COALESCE(p.variant_id, m.variant_id) ")

    def calendar(self, start: str, end: str, concept_id: int | None = None) -> list[sqlite3.Row]:
        """Publications datées dans [start, end[ (heure locale « AAAA-MM-JJTHH:MM »)."""
        sql = self._CAL + "WHERE COALESCE(p.scheduled_at, m.planned_at) >= ? AND COALESCE(p.scheduled_at, m.planned_at) < ?"
        params: list = [start, end]
        if concept_id:
            sql += " AND c.id = ?"
            params.append(concept_id)
        return self.db.query(sql + " ORDER BY at, a.platform", tuple(params))

    def undated(self, concept_id: int | None = None) -> list[sqlite3.Row]:
        sql = self._CAL + ("WHERE COALESCE(p.scheduled_at, m.planned_at) IS NULL "
                           "AND p.status NOT IN ('published','cancelled')")
        params: tuple = ()
        if concept_id:
            sql += " AND c.id = ?"
            params = (concept_id,)
        return self.db.query(sql + " ORDER BY p.id DESC", params)

    def get(self, publication_id: int) -> sqlite3.Row | None:
        return self.db.query_one(self._CAL + "WHERE p.id = ?", (publication_id,))

    def master(self, master_id: int) -> sqlite3.Row | None:
        return self.db.query_one("SELECT * FROM master_publication WHERE id = ?", (master_id,))

    def schedule(self, publication_id: int, when: str | None) -> None:
        """Programme (heure locale) un rappel de publication ; None retire la date."""
        with self.db.transaction() as c:
            if when:
                c.execute("UPDATE publication SET scheduled_at = ?, schedule_mode = 'local', status = 'scheduled_local'"
                          " WHERE id = ? AND status NOT IN ('published','cancelled')", (when, publication_id))
            else:
                c.execute("UPDATE publication SET scheduled_at = NULL, schedule_mode = 'none', status = 'draft'"
                          " WHERE id = ? AND status = 'scheduled_local'", (publication_id,))

    def due(self, now: str) -> list[sqlite3.Row]:
        """Rappels arrivés à échéance (programmés localement, heure passée)."""
        return self.db.query(self._CAL + "WHERE p.status = 'scheduled_local' AND p.scheduled_at <= ? "
                                         "ORDER BY p.scheduled_at", (now,))

    def mark_manual(self, publication_id: int, reason: str) -> None:
        self.set_status(publication_id, PublicationStatus.MANUAL_ACTION, error=reason)

    def confirm_published(self, publication_id: int, public_url: str) -> None:
        """Publication faite à la main : le lien public fourni sert de confirmation (vérifié par plateforme)."""
        from ..publish.rules import url_matches
        row = self.get(publication_id)
        if row is None:
            raise PublicationError("Publication introuvable.")
        if not url_matches(row["platform"], public_url):
            raise PublicationError("Ce lien n'appartient pas à la plateforme de cette publication.")
        with self.db.transaction() as c:
            c.execute("DELETE FROM publication_match WHERE publication_id = ?", (publication_id,))
        self.set_status(publication_id, PublicationStatus.PUBLISHED, remote_id=public_url.strip(),
                        public_url=public_url.strip())

    # -- Atelier de publication : lien retrouvé sur la page publique du compte ---------------
    def awaiting_link(self, account_id: int | None = None) -> list[sqlite3.Row]:
        """Publications faites à la main dont le lien n'est pas encore connu, avec la date de la première
        préparation (`since`, UTC) : la vidéo publiée ne peut pas être plus ancienne."""
        sql = self._CAL.replace("SELECT ", "SELECT a.public_url account_url, (SELECT MIN(t.at) FROM "
                                "publication_attempt t WHERE t.publication_id = p.id) since, ", 1)
        sql += "WHERE p.status = 'manual_action' AND p.remote_id = ''"
        params: tuple = ()
        if account_id:
            sql += " AND a.id = ?"
            params = (account_id,)
        return self.db.query(sql + " ORDER BY p.id", params)

    def propose_match(self, publication_id: int, url: str, title: str, score: float) -> bool:
        """Lien possible (« Est-ce cette vidéo ? »). Un lien déjà refusé n'est plus proposé."""
        with self.db.transaction() as c:
            return c.execute("INSERT OR IGNORE INTO publication_match(publication_id, url, title, score) "
                             "VALUES (?,?,?,?)", (publication_id, url, title, score)).rowcount > 0

    def matches(self, publication_id: int) -> list[sqlite3.Row]:
        return self.db.query("SELECT * FROM publication_match WHERE publication_id = ? AND status = 'proposed' "
                             "ORDER BY score DESC", (publication_id,))

    def accept_match(self, match_id: int) -> None:
        m = self.db.query_one("SELECT * FROM publication_match WHERE id = ?", (match_id,))
        if m is None:
            raise PublicationError("Proposition introuvable.")
        self.confirm_published(m["publication_id"], m["url"])
        with self.db.transaction() as c:
            c.execute("DELETE FROM publication_match WHERE publication_id = ?", (m["publication_id"],))

    def reject_match(self, match_id: int) -> None:
        with self.db.transaction() as c:
            c.execute("UPDATE publication_match SET status = 'rejected' WHERE id = ?", (match_id,))

    def linked_urls(self, account_id: int) -> set[str]:
        """Liens déjà reliés à une publication de ce compte (jamais attribués à une autre)."""
        return {r["public_url"] for r in self.db.query(
            "SELECT public_url FROM publication WHERE social_account_id = ? AND public_url != ''", (account_id,))}

    def cancel(self, publication_id: int) -> None:
        self.set_status(publication_id, PublicationStatus.CANCELLED)

    # -- calendrier : vidéos programmées (organisation personnelle) ---------------------------
    NETS = ("youtube", "tiktok", "meta")

    def plan(self, concept_id: int, project_id: int | None, text: str, when: str,
             networks: dict | None = None, master_id: int | None = None) -> int:
        """Vidéo programmée au calendrier : concept, vidéo, un seul bloc de texte (titre, description, hashtags),
        date et heure locales (« AAAA-MM-JJTHH:MM »), petits logos cochés. Rien n'est publié par le logiciel."""
        text = (text or "").strip()
        title = (text.splitlines() or [""])[0].strip()[:100]
        old = self.plan_of(master_id) if master_id else None
        nets = json.dumps({**{k: bool((networks or {}).get(k)) for k in self.NETS},
                           "links": (old or {}).get("links", {}), "times": (old or {}).get("times", {})})
        with self.db.transaction() as c:
            if master_id:
                c.execute("UPDATE master_publication SET concept_id = ?, project_id = ?, title = ?, description = ?,"
                          " hashtags = '', planned_at = ?, networks_json = ? WHERE id = ?",
                          (concept_id, project_id, title, text, when, nets, master_id))
                return master_id
            return c.execute("INSERT INTO master_publication(concept_id, project_id, title, description, planned_at,"
                             " networks_json) VALUES (?,?,?,?,?,?)",
                             (concept_id, project_id, title, text, when, nets)).lastrowid

    _PLAN = ("SELECT m.id, m.concept_id, c.name concept, c.color, m.project_id, pr.name video, m.title,"
             " m.description, m.hashtags, m.planned_at, m.networks_json FROM master_publication m"
             " JOIN concept c ON c.id = m.concept_id LEFT JOIN project pr ON pr.id = m.project_id ")

    @staticmethod
    def _plan_row(r) -> dict:
        d = dict(r)
        try:
            nets = json.loads(r["networks_json"] or "{}")
        except ValueError:
            nets = {}
        d["networks"] = {k: bool(nets.get(k)) for k in PublicationService.NETS}
        d["links"] = nets.get("links") or {}             # vidéo retrouvée sur le réseau : son lien
        d["times"] = nets.get("times") or {}             # et l'heure de sa mise en ligne (heure locale)
        d["text"] = "\n\n".join(x for x in (r["description"] or r["title"] or "", r["hashtags"] or "") if x)
        return d

    def planned(self, start: str, end: str, concept_id: int | None = None) -> list[dict]:
        """Vidéos programmées dans [start, end[ (heure locale), d'un concept ou de tous."""
        sql = self._PLAN + "WHERE m.planned_at >= ? AND m.planned_at < ?"
        params: list = [start, end]
        if concept_id:
            sql += " AND m.concept_id = ?"
            params.append(concept_id)
        return [self._plan_row(r) for r in self.db.query(sql + " ORDER BY m.planned_at, m.id", tuple(params))]

    def plan_of(self, master_id: int) -> dict | None:
        r = self.db.query_one(self._PLAN + "WHERE m.id = ?", (master_id,))
        return self._plan_row(r) if r else None

    def toggle_network(self, master_id: int, key: str) -> bool:
        """Petit logo d'une vidéo du calendrier : en couleur ↔ grisé. Renvoie le nouvel état."""
        d = self.plan_of(master_id)
        if d is None or key not in self.NETS:
            raise PublicationError("Publication introuvable.")
        d["networks"][key] = not d["networks"][key]
        self._save_nets(master_id, d)
        return d["networks"][key]

    def _save_nets(self, master_id: int, d: dict) -> None:
        with self.db.transaction() as c:
            c.execute("UPDATE master_publication SET networks_json = ? WHERE id = ?",
                      (json.dumps({**d["networks"], "links": d["links"], "times": d["times"]}), master_id))

    def sync_published(self, concept_id: int, platform: str, url: str, title: str, description: str,
                       when: str) -> str:
        """Une vidéo trouvée sur la page publique d'une chaîne du concept (heure locale `when`) :
        - déjà reliée à une publication du calendrier : « known » ;
        - une publication du même jour qui lui ressemble (texte) : reliée, son logo en couleur, et sa date et son
          heure deviennent celles de la mise en ligne (la plus tôt si les réseaux diffèrent) : « matched » ;
        - sinon ajoutée au calendrier avec son titre et sa description : « added »."""
        from ..publish import reconcile
        net = "meta" if platform in ("facebook", "instagram") else platform
        key = reconcile.video_key(url)
        rows = [self._plan_row(r) for r in self.db.query(self._PLAN + "WHERE m.concept_id = ? AND m.planned_at IS "
                                                                      "NOT NULL", (concept_id,))]
        if any(reconcile.video_key(u) == key for r in rows for u in r["links"].values()):
            return "known"
        found = (title + "\n" + description).strip()
        day = [r for r in rows if (r["planned_at"] or "")[:10] == when[:10] and not r["links"].get(net)]
        best, score = None, 0.0
        for r in day:
            s = max(reconcile.text_score(r["text"], found), reconcile.text_score(r["title"] or "", title))
            if s > score:
                best, score = r, s
        if best is None and len(day) == 1 and not day[0]["text"].strip():
            best = day[0]                                # seule publication du jour, sans texte : c'est elle
        if best is not None and (score >= 0.35 or not best["text"].strip()):
            best["links"][net], best["times"][net] = url, when
            best["networks"][net] = True
            self._save_nets(best["id"], best)
            first = min(best["times"].values())          # réseaux à des heures différentes : la plus tôt
            with self.db.transaction() as c:
                c.execute("UPDATE master_publication SET planned_at = ? WHERE id = ?", (first, best["id"]))
            return "matched"
        mid = self.plan(concept_id, None, "\n".join(x for x in (title.strip(), description.strip()) if x), when,
                        {net: True})
        d = self.plan_of(mid)
        d["links"][net], d["times"][net] = url, when
        self._save_nets(mid, d)
        return "added"

    def delete_master(self, master_id: int) -> None:
        if self.db.query_one("SELECT 1 FROM publication WHERE master_id = ? AND status = 'published'", (master_id,)):
            raise PublicationError("Une destination est déjà publiée : annulez plutôt les autres.")
        with self.db.transaction() as c:
            c.execute("DELETE FROM master_publication WHERE id = ?", (master_id,))
