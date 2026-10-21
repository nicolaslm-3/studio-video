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

    def get(self, project_id: int) -> sqlite3.Row | None:
        return self.db.query_one(
            "SELECT p.*, c.name concept_name, c.color concept_color FROM project p "
            "JOIN concept c ON c.id = p.concept_id WHERE p.id = ?", (project_id,))

    def recent(self, limit: int = 10) -> list[sqlite3.Row]:
        return self.db.query(
            "SELECT p.*, c.name concept_name, c.color concept_color FROM project p "
            "JOIN concept c ON c.id = p.concept_id ORDER BY p.updated_at DESC, p.id DESC LIMIT ?", (limit,))

    def save_data(self, project_id: int, data: dict) -> None:
        """Sauvegarde automatique de l'état du projet."""
        with self.db.transaction() as c:
            c.execute("UPDATE project SET data_json = ?, updated_at = strftime('%Y-%m-%dT%H:%M:%SZ','now')"
                      " WHERE id = ?", (json.dumps(data, ensure_ascii=False), project_id))

    def load_data(self, project_id: int) -> dict:
        row = self.db.query_one("SELECT data_json FROM project WHERE id = ?", (project_id,))
        return json.loads(row["data_json"]) if row else {}
