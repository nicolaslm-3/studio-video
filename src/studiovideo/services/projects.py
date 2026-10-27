"""Projets de production."""
from __future__ import annotations

import json
import sqlite3

from ..db.database import Database
from ..domain.models import ProjectMode


class ProjectService:
    def __init__(self, db: Database):
        self.db = db

    def create(self, concept_id: int, name: str, mode: ProjectMode | str = ProjectMode.FREE_EDIT) -> int:
        if not name.strip():
            raise ValueError("Le nom du projet est obligatoire.")
        with self.db.transaction() as c:
            pid = c.execute("INSERT INTO project(concept_id, name, mode) VALUES (?,?,?)",
                            (concept_id, name.strip(), ProjectMode(mode).value)).lastrowid
        self.db.audit("project.create", name)
        return pid

    def quick_create(self, concepts, mode: ProjectMode | str, name: str = "", aspect: str = "9:16",
                     concept_id: int | None = None, blank: bool = False) -> int:
        """Projet prêt à monter, sans formalité : concept « Brouillons » si aucun n'est choisi, format donné.

        blank : projet vierge, supprimé par `discard_if_untouched` s'il est quitté sans aucune modification."""
        from ..edit.model import Timeline
        cid = concept_id or concepts.drafts_concept().id
        label_ = ProjectMode(mode).label
        base = name.strip() or f"{label_} {self._next_number(label_)}"
        final = self._unique_name(base)
        pid = self.create(cid, final, mode)
        tl = Timeline()
        tl.set_aspect(aspect)
        data = {"timeline": tl.to_dict()}
        if blank:
            data["blank"] = final
        self.save_data(pid, data)
        return pid

    def is_untouched(self, project_id: int) -> bool:
        """Projet vierge resté intact : nom d'origine, aucun média, clip, proposition, texte ni réglage propre."""
        p = self.get(project_id)
        if p is None:
            return False
        data = self.load_data(project_id)
        if data.get("blank") != p["name"] or set(data) - {"blank", "timeline", "last_asset_id", "in_out"}:
            return False
        tl = data.get("timeline") or {}
        if tl.get("segments") or tl.get("texts") or (tl.get("music") or {}).get("asset_id") \
                or (tl.get("subtitles") or {}).get("cues"):
            return False
        n = self.db.query_one("SELECT (SELECT COUNT(*) FROM source_asset WHERE project_id = :p) + "
                              "(SELECT COUNT(*) FROM clip WHERE project_id = :p) + "
                              "(SELECT COUNT(*) FROM clip_candidate WHERE project_id = :p) n", {"p": project_id})["n"]
        return n == 0

    def discard_if_untouched(self, project_id: int) -> bool:
        """En quittant un projet vierge : supprimé s'il est intact, sinon conservé comme projet ordinaire."""
        if self.is_untouched(project_id):
            self.delete(project_id)
            return True
        data = self.load_data(project_id)
        if "blank" in data:
            data.pop("blank")
            self.save_data(project_id, data)
        return False

    def purge_untouched(self) -> int:
        """Au démarrage : projets vierges restés intacts (logiciel fermé brutalement)."""
        rows = self.db.query("SELECT id FROM project WHERE data_json LIKE '%\"blank\"%'")
        return sum(self.discard_if_untouched(r["id"]) for r in rows)

    def _next_number(self, prefix: str) -> int:
        n = self.db.query_one("SELECT COUNT(*) n FROM project WHERE name LIKE ?", (prefix + " %",))["n"]
        return n + 1

    def by_mode(self, mode: str, limit: int = 6) -> list[sqlite3.Row]:
        return self.db.query(
            "SELECT p.*, c.name concept_name, c.color concept_color FROM project p JOIN concept c ON c.id = p.concept_id "
            "WHERE p.mode = ? AND p.status != 'archived' ORDER BY p.updated_at DESC, p.id DESC LIMIT ?", (mode, limit))

    def get(self, project_id: int) -> sqlite3.Row | None:
        return self.db.query_one(
            "SELECT p.*, c.name concept_name, c.color concept_color FROM project p "
            "JOIN concept c ON c.id = p.concept_id WHERE p.id = ?", (project_id,))

    def recent(self, limit: int = 10) -> list[sqlite3.Row]:
        return self.db.query(
            "SELECT p.*, c.name concept_name, c.color concept_color FROM project p "
            "JOIN concept c ON c.id = p.concept_id WHERE p.status != 'archived' "
            "ORDER BY p.updated_at DESC, p.id DESC LIMIT ?", (limit,))

    def all(self, include_archived: bool = True) -> list[sqlite3.Row]:
        return self.db.query(
            "SELECT p.*, c.name concept_name, c.color concept_color, "
            "(SELECT COUNT(*) FROM source_asset a WHERE a.project_id = p.id) n_media, "
            "(SELECT COUNT(*) FROM video_variant v JOIN clip cl ON cl.id = v.clip_id WHERE cl.project_id = p.id) n_videos "
            "FROM project p JOIN concept c ON c.id = p.concept_id "
            + ("" if include_archived else "WHERE p.status != 'archived' ")
            + "ORDER BY p.status = 'archived', p.updated_at DESC, p.id DESC")

    # -- gestion ----------------------------------------------------------------------
    def _unique_name(self, name: str, exclude: int | None = None) -> str:
        names = {r["name"] for r in self.db.query("SELECT name FROM project WHERE id != ?", (exclude or -1,))}
        if name not in names:
            return name
        k = 2
        while f"{name} ({k})" in names:
            k += 1
        return f"{name} ({k})"

    def rename(self, project_id: int, name: str) -> str:
        name = name.strip()
        if not name:
            raise ValueError("Le nom du projet est obligatoire.")
        name = self._unique_name(name, project_id)
        with self.db.transaction() as c:
            c.execute("UPDATE project SET name = ?, updated_at = strftime('%Y-%m-%dT%H:%M:%SZ','now') WHERE id = ?",
                      (name, project_id))
        self.db.audit("project.rename", f"{project_id}:{name}")
        return name

    def set_mode(self, project_id: int, mode: ProjectMode | str) -> None:
        """Interface dans laquelle le projet a été ouvert en dernier : un même projet passe d'un parcours à
        l'autre (Clipping, Traduction, Création originale, Montage libre) sans être dupliqué."""
        with self.db.transaction() as c:
            c.execute("UPDATE project SET mode = ? WHERE id = ?", (ProjectMode(mode).value, project_id))

    def set_concept(self, project_id: int, concept_id: int) -> None:
        with self.db.transaction() as c:
            c.execute("UPDATE project SET concept_id = ? WHERE id = ?", (concept_id, project_id))

    def set_archived(self, project_id: int, archived: bool) -> None:
        with self.db.transaction() as c:
            c.execute("UPDATE project SET status = ? WHERE id = ?", ("archived" if archived else "active", project_id))
        self.db.audit("project.archive" if archived else "project.unarchive", str(project_id))

    def duplicate(self, project_id: int, name: str | None = None) -> int:
        """Copie du montage et des clips (les médias sont partagés, rien n'est copié sur le disque)."""
        p = self.get(project_id)
        if p is None:
            raise ValueError("Projet introuvable.")
        new_name = self._unique_name((name or f"{p['name']} (copie)").strip())
        with self.db.transaction() as c:
            pid = c.execute("INSERT INTO project(concept_id, name, mode, data_json) VALUES (?,?,?,?)",
                            (p["concept_id"], new_name, p["mode"], p["data_json"])).lastrowid
            c.execute("INSERT INTO clip(project_id, source_asset_id, candidate_id, name, start_s, end_s, edit_json) "
                      "SELECT ?, source_asset_id, NULL, name, start_s, end_s, edit_json FROM clip "
                      "WHERE project_id = ? AND name != 'Montage'", (pid, project_id))
        self.db.audit("project.duplicate", f"{project_id} -> {pid}")
        return pid

    def published_count(self, project_id: int) -> int:
        return self.db.query_one(
            "SELECT COUNT(*) n FROM publication pu JOIN video_variant v ON v.id = pu.variant_id "
            "JOIN clip cl ON cl.id = v.clip_id WHERE cl.project_id = ?", (project_id,))["n"]

    def delete(self, project_id: int) -> None:
        """Supprime le projet (montage, clips, historique d'export). Les fichiers ne sont jamais effacés ;
        les médias restent dans la bibliothèque. Refusé si des publications en dépendent : archivez-le."""
        if self.published_count(project_id):
            raise ValueError("Des publications utilisent les vidéos de ce projet : archivez-le plutôt que de le "
                             "supprimer, pour garder leur historique.")
        with self.db.transaction() as c:
            c.execute("DELETE FROM project WHERE id = ?", (project_id,))
        self.db.audit("project.delete", str(project_id))

    def save_data(self, project_id: int, data: dict) -> None:
        """Sauvegarde automatique de l'état du projet."""
        with self.db.transaction() as c:
            c.execute("UPDATE project SET data_json = ?, updated_at = strftime('%Y-%m-%dT%H:%M:%SZ','now')"
                      " WHERE id = ?", (json.dumps(data, ensure_ascii=False), project_id))

    def load_data(self, project_id: int) -> dict:
        row = self.db.query_one("SELECT data_json FROM project WHERE id = ?", (project_id,))
        return json.loads(row["data_json"]) if row else {}
