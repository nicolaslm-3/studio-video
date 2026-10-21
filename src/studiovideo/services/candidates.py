"""Propositions d'extraits (§7.3) : stockage, décisions, conversion en clips."""
from __future__ import annotations

import json
import sqlite3

from ..ai.highlights import Candidate, Request
from ..db.database import Database


class CandidateService:
    def __init__(self, db: Database):
        self.db = db

    def replace_proposals(self, project_id: int, asset_id: int, cands: list[Candidate], req: Request) -> list[int]:
        """Remplace les propositions non traitées ; les extraits acceptés ou favoris sont conservés."""
        ids = []
        with self.db.transaction() as c:
            c.execute("DELETE FROM clip_candidate WHERE project_id = ? AND source_asset_id = ? AND state = 'proposed'"
                      " AND favorite = 0", (project_id, asset_id))
            for k in cands:
                ids.append(c.execute(
                    "INSERT INTO clip_candidate(project_id, source_asset_id, start_s, end_s, subject, category, score,"
                    " rationale, hook, context_flags, speakers, engine, request_json, created_at)"
                    " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?, strftime('%Y-%m-%dT%H:%M:%SZ','now'))",
                    (project_id, asset_id, k.start, k.end, k.subject, k.category, k.score, k.rationale, k.hook,
                     json.dumps(k.context_flags, ensure_ascii=False), ", ".join(k.speakers), k.engine,
                     json.dumps(req.__dict__, ensure_ascii=False))).lastrowid)
        return ids

    def list(self, project_id: int, asset_id: int | None = None, states: tuple[str, ...] | None = None
             ) -> list[sqlite3.Row]:
        sql = ("SELECT k.*, a.file_name FROM clip_candidate k JOIN source_asset a ON a.id = k.source_asset_id "
               "WHERE k.project_id = ?")
        params: list = [project_id]
        if asset_id:
            sql += " AND k.source_asset_id = ?"
            params.append(asset_id)
        if states:
            sql += f" AND k.state IN ({','.join('?' * len(states))})"
            params += list(states)
        return self.db.query(sql + " ORDER BY k.score DESC, k.start_s", tuple(params))

    def get(self, cid: int) -> sqlite3.Row | None:
        return self.db.query_one("SELECT * FROM clip_candidate WHERE id = ?", (cid,))

    def set_state(self, cid: int, state: str) -> None:
        if state not in ("proposed", "accepted", "rejected"):
            raise ValueError(state)
        self.db.connect().execute("UPDATE clip_candidate SET state = ? WHERE id = ?", (state, cid))

    def toggle_favorite(self, cid: int) -> None:
        self.db.connect().execute("UPDATE clip_candidate SET favorite = 1 - favorite WHERE id = ?", (cid,))

    def set_times(self, cid: int, start: float, end: float, flags: list[str]) -> None:
        if end - start < 0.5:
            raise ValueError("L'extrait doit durer au moins 0,5 s.")
        self.db.connect().execute("UPDATE clip_candidate SET start_s = ?, end_s = ?, context_flags = ? WHERE id = ?",
                                  (start, end, json.dumps(flags, ensure_ascii=False), cid))

    def to_clips(self, cids: list[int]) -> list[int]:
        """Crée un clip par proposition (nommé d'après le sujet) ; renvoie les ids de clips."""
        out = []
        with self.db.transaction() as c:
            for cid in cids:
                k = c.execute("SELECT * FROM clip_candidate WHERE id = ?", (cid,)).fetchone()
                if not k:
                    continue
                n = c.execute("SELECT COUNT(*) n FROM clip WHERE project_id = ? AND name != 'Montage'",
                              (k["project_id"],)).fetchone()["n"]
                name = (k["subject"].split(",")[0].strip().capitalize() or f"Extrait {n + 1}")[:60]
                out.append(c.execute(
                    "INSERT INTO clip(project_id, source_asset_id, candidate_id, name, start_s, end_s, edit_json)"
                    " VALUES (?,?,?,?,?,?,?)",
                    (k["project_id"], k["source_asset_id"], cid, name, k["start_s"], k["end_s"],
                     json.dumps({"hook": k["hook"]}, ensure_ascii=False))).lastrowid)
                c.execute("UPDATE clip_candidate SET state = 'accepted' WHERE id = ?", (cid,))
        return out
