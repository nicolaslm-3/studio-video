"""Publication maître et publications par destination (cahier des charges §18).

Règles :
- une publication = un compte précis ; jamais créée pour un compte non choisi ;
- un compte hors concept exige une confirmation explicite (is_exceptional) ;
- titre/description/hashtags à NULL héritent de la publication maître :
  modifier la maître se propage, une modification locale reste locale ;
- aucun statut « publiée » sans confirmation de la plateforme (remote_id).
"""
from __future__ import annotations

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
        allowed = {"title", "description", "hashtags", "thumbnail", "language", "planned_at", "variant_id"}
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
