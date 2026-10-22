"""Concepts éditoriaux, comptes sociaux et associations (cahier des charges §3)."""
from __future__ import annotations

import json
import sqlite3

from ..db.database import Database
from ..domain.models import Concept, Destination, Platform, SocialAccount


class ConceptError(ValueError):
    pass


def _concept(row: sqlite3.Row) -> Concept:
    return Concept(id=row["id"], workspace_id=row["workspace_id"], name=row["name"],
                   theme=row["theme"], color=row["color"], language=row["language"],
                   archived=bool(row["archived"]))


def _account(row: sqlite3.Row) -> SocialAccount:
    return SocialAccount(id=row["id"], workspace_id=row["workspace_id"],
                         platform=Platform(row["platform"]), name=row["name"],
                         public_url=row["public_url"], language=row["language"],
                         connection_status=row["connection_status"])


class ConceptService:
    def __init__(self, db: Database):
        self.db = db

    # -- espace de travail ----------------------------------------------
    def default_workspace(self) -> int:
        row = self.db.query_one("SELECT id FROM workspace ORDER BY id LIMIT 1")
        if row:
            return row["id"]
        with self.db.transaction() as c:
            return c.execute("INSERT INTO workspace(name) VALUES ('Principal')").lastrowid

    # -- concepts --------------------------------------------------------
    DRAFTS = "Brouillons"

    def drafts_concept(self) -> Concept:
        """Concept par défaut des projets pas encore rangés (créé au besoin)."""
        for c in self.list_concepts(include_archived=True):
            if c.name == self.DRAFTS:
                if c.archived:
                    self.update_concept(c.id, archived=0)
                return self.get_concept(c.id)
        return self.create_concept(self.DRAFTS, theme="Projets pas encore rangés", color="#ADA698")

    def create_concept(self, name: str, theme: str = "", color: str = "#8A8F98",
                       language: str = "fr") -> Concept:
        name = name.strip()
        if not name:
            raise ConceptError("Le nom du concept est obligatoire.")
        ws = self.default_workspace()
        try:
            with self.db.transaction() as c:
                cid = c.execute(
                    "INSERT INTO concept(workspace_id, name, theme, color, language) VALUES (?,?,?,?,?)",
                    (ws, name, theme, color, language)).lastrowid
        except sqlite3.IntegrityError as exc:
            raise ConceptError(f"Un concept nommé « {name} » existe déjà.") from exc
        self.db.audit("concept.create", name)
        return self.get_concept(cid)

    def update_concept(self, concept_id: int, **fields) -> Concept:
        allowed = {"name", "theme", "color", "language", "archived"}
        bad = set(fields) - allowed
        if bad:
            raise ConceptError(f"Champs non modifiables : {bad}")
        if fields:
            sets = ", ".join(f"{k} = ?" for k in fields)
            with self.db.transaction() as c:
                c.execute(f"UPDATE concept SET {sets} WHERE id = ?", (*fields.values(), concept_id))
        return self.get_concept(concept_id)

    def get_concept(self, concept_id: int) -> Concept:
        row = self.db.query_one("SELECT * FROM concept WHERE id = ?", (concept_id,))
        if not row:
            raise ConceptError(f"Concept {concept_id} introuvable.")
        return _concept(row)

    def list_concepts(self, include_archived: bool = False) -> list[Concept]:
        sql = "SELECT * FROM concept" + ("" if include_archived else " WHERE archived = 0") + " ORDER BY name COLLATE NOCASE"
        return [_concept(r) for r in self.db.query(sql)]

    def delete_concept(self, concept_id: int) -> None:
        n = self.db.query_one("SELECT COUNT(*) n FROM project WHERE concept_id = ?", (concept_id,))["n"]
        if n:
            raise ConceptError(f"Ce concept contient {n} projet(s). Archivez-le plutôt que de le supprimer.")
        with self.db.transaction() as c:
            c.execute("DELETE FROM concept WHERE id = ?", (concept_id,))
        self.db.audit("concept.delete", str(concept_id))

    # -- comptes ---------------------------------------------------------
    def create_account(self, platform: Platform | str, name: str, public_url: str = "",
                       language: str = "fr", settings: dict | None = None) -> SocialAccount:
        platform = Platform(platform)
        if not name.strip():
            raise ConceptError("Le nom du compte est obligatoire.")
        ws = self.default_workspace()
        with self.db.transaction() as c:
            aid = c.execute(
                "INSERT INTO social_account(workspace_id, platform, name, public_url, language, settings_json)"
                " VALUES (?,?,?,?,?,?)",
                (ws, platform.value, name.strip(), public_url, language,
                 json.dumps(settings or {}))).lastrowid
        self.db.audit("account.create", f"{platform.value}:{name}")
        return self.get_account(aid)

    def get_account(self, account_id: int) -> SocialAccount:
        row = self.db.query_one("SELECT * FROM social_account WHERE id = ?", (account_id,))
        if not row:
            raise ConceptError(f"Compte {account_id} introuvable.")
        return _account(row)

    def list_accounts(self) -> list[SocialAccount]:
        return [_account(r) for r in self.db.query(
            "SELECT * FROM social_account ORDER BY platform, name COLLATE NOCASE")]

    def delete_account(self, account_id: int) -> None:
        n = self.db.query_one("SELECT COUNT(*) n FROM publication WHERE social_account_id = ?",
                              (account_id,))["n"]
        if n:
            raise ConceptError(f"Ce compte a {n} publication(s) dans l'historique ; il ne peut pas être supprimé.")
        with self.db.transaction() as c:
            c.execute("DELETE FROM social_account WHERE id = ?", (account_id,))
        self.db.audit("account.delete", str(account_id))

    # -- associations ----------------------------------------------------
    def link(self, concept_id: int, account_id: int, is_default: bool = True) -> None:
        self.get_concept(concept_id)
        self.get_account(account_id)
        with self.db.transaction() as c:
            c.execute("INSERT INTO concept_account(concept_id, social_account_id, is_default) VALUES (?,?,?)"
                      " ON CONFLICT(concept_id, social_account_id) DO UPDATE SET is_default = excluded.is_default",
                      (concept_id, account_id, int(is_default)))
        self.db.audit("concept.link", f"{concept_id}->{account_id}")

    def unlink(self, concept_id: int, account_id: int) -> None:
        with self.db.transaction() as c:
            c.execute("DELETE FROM concept_account WHERE concept_id = ? AND social_account_id = ?",
                      (concept_id, account_id))
        self.db.audit("concept.unlink", f"{concept_id}->{account_id}")

    def concept_accounts(self, concept_id: int) -> list[tuple[SocialAccount, bool]]:
        rows = self.db.query(
            "SELECT a.*, ca.is_default FROM social_account a "
            "JOIN concept_account ca ON ca.social_account_id = a.id "
            "WHERE ca.concept_id = ? ORDER BY a.platform, a.name COLLATE NOCASE", (concept_id,))
        return [(_account(r), bool(r["is_default"])) for r in rows]

    def concepts_of_account(self, account_id: int) -> list[Concept]:
        rows = self.db.query("SELECT c.* FROM concept c JOIN concept_account ca ON ca.concept_id = c.id "
                             "WHERE ca.social_account_id = ? ORDER BY c.name", (account_id,))
        return [_concept(r) for r in rows]

    def proposed_destinations(self, concept_id: int, include_external: bool = False) -> list[Destination]:
        """Destinations proposées pour une vidéo du concept.

        Seuls les comptes associés au concept sont proposés (et présélectionnés
        s'ils sont « par défaut »). Les comptes extérieurs ne sont listés que sur
        demande explicite, jamais présélectionnés.
        """
        linked = self.concept_accounts(concept_id)
        dests = [Destination(a, True, d) for a, d in linked]
        if include_external:
            ids = {a.id for a, _ in linked}
            dests += [Destination(a, False, False) for a in self.list_accounts() if a.id not in ids]
        return dests
