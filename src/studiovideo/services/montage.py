"""Montage d'un projet : timeline enregistrée, réglages par concept, exports."""
from __future__ import annotations

import json
from pathlib import Path

from ..db.database import Database
from ..edit.model import Timeline, apply_concept_defaults, concept_defaults_from, text_style
from ..edit.render import Source

PRESET_KIND = "montage"


class MontageService:
    def __init__(self, db: Database):
        self.db = db

    # -- timeline ------------------------------------------------------------
    def load(self, project_id: int) -> Timeline:
        row = self.db.query_one("SELECT data_json, concept_id FROM project WHERE id = ?", (project_id,))
        if not row:
            raise KeyError(project_id)
        data = json.loads(row["data_json"] or "{}")
        if "timeline" in data:
            return Timeline.from_dict(data["timeline"])
        return apply_concept_defaults(Timeline(), self.concept_defaults(row["concept_id"]))

    def save(self, project_id: int, tl: Timeline) -> None:
        with self.db.transaction() as c:
            row = c.execute("SELECT data_json FROM project WHERE id = ?", (project_id,)).fetchone()
            data = json.loads(row["data_json"] or "{}")
            data["timeline"] = tl.to_dict()
            c.execute("UPDATE project SET data_json = ?, updated_at = strftime('%Y-%m-%dT%H:%M:%SZ','now')"
                      " WHERE id = ?", (json.dumps(data, ensure_ascii=False), project_id))

    # -- réglages du concept -------------------------------------------------------
    def concept_defaults(self, concept_id: int) -> dict | None:
        row = self.db.query_one("SELECT data_json FROM preset WHERE concept_id = ? AND kind = ? AND name = ?",
                                (concept_id, PRESET_KIND, "défaut"))
        return json.loads(row["data_json"]) if row else None

    def save_concept_defaults(self, concept_id: int, tl: Timeline) -> None:
        with self.db.transaction() as c:
            c.execute("INSERT INTO preset(concept_id, kind, name, data_json) VALUES (?,?,?,?) "
                      "ON CONFLICT(concept_id, kind, name) DO UPDATE SET data_json = excluded.data_json",
                      (concept_id, PRESET_KIND, "défaut", json.dumps(concept_defaults_from(tl), ensure_ascii=False)))
        self.db.audit("preset.save", f"concept={concept_id}")

    def copy_concept_defaults(self, from_concept: int, to_concept: int) -> bool:
        d = self.concept_defaults(from_concept)
        if d is None:
            return False
        with self.db.transaction() as c:
            c.execute("INSERT INTO preset(concept_id, kind, name, data_json) VALUES (?,?,?,?) "
                      "ON CONFLICT(concept_id, kind, name) DO UPDATE SET data_json = excluded.data_json",
                      (to_concept, PRESET_KIND, "défaut", json.dumps(d, ensure_ascii=False)))
        return True

    def text_style_for(self, project_id: int) -> dict:
        row = self.db.query_one("SELECT concept_id FROM project WHERE id = ?", (project_id,))
        return text_style(self.concept_defaults(row["concept_id"])) if row else {}

    # -- sources ------------------------------------------------------------
    def sources_for(self, tl: Timeline) -> tuple[dict[int, Source], Source | None]:
        ids = {s.asset_id for s in tl.segments}
        out = {}
        for aid in ids:
            a = self.db.query_one("SELECT * FROM source_asset WHERE id = ?", (aid,))
            if a is None:
                raise ValueError(f"Média {aid} absent de la bibliothèque.")
            if not Path(a["path"]).exists():
                raise FileNotFoundError(f"Fichier source introuvable : {a['path']}")
            out[aid] = Source(a["path"], a["width"], a["height"], a["duration_s"], a["audio_tracks"] > 0,
                              a["file_name"])
            vs = tl.voice
            if vs.mode == "dub" and vs.language:
                d = self.db.query_one("SELECT directory, manifest_json FROM dub WHERE source_asset_id = ? "
                                      "AND language = ?", (aid, vs.language))
                if d is None:
                    raise ValueError(f"Pas de doublage {vs.language.upper()} pour « {a['file_name']} » : "
                                     "créez-le depuis la transcription traduite.")
                import json as _json
                track = Path(d["directory"]) / (_json.loads(d["manifest_json"]).get("track") or "dub.wav")
                if not track.exists():
                    raise FileNotFoundError(f"Piste de doublage introuvable : {track}")
                out[aid].dub_path = str(track)
        music = None
        if tl.music.asset_id:
            m = self.db.query_one("SELECT * FROM source_asset WHERE id = ?", (tl.music.asset_id,))
            if m is None or not Path(m["path"]).exists():
                raise FileNotFoundError("La musique est introuvable.")
            if m["audio_tracks"] < 1:
                raise ValueError("Le fichier choisi comme musique ne contient pas de son.")
            music = Source(m["path"], None, None, m["duration_s"], True, m["file_name"])
        return out, music

    def asset_row(self, asset_id: int):
        return self.db.query_one("SELECT * FROM source_asset WHERE id = ?", (asset_id,))

    # -- historique des exports -------------------------------------------------------
    def record_export(self, project_id: int, tl: Timeline, report: dict, job_id: int | None) -> int:
        with self.db.transaction() as c:
            clip = c.execute("SELECT id FROM clip WHERE project_id = ? AND name = 'Montage' LIMIT 1",
                             (project_id,)).fetchone()
            cid = clip["id"] if clip else c.execute(
                "INSERT INTO clip(project_id, name, start_s, end_s) VALUES (?, 'Montage', 0, ?)",
                (project_id, tl.duration)).lastrowid
            c.execute("UPDATE clip SET edit_json = ?, end_s = ?, updated_at = strftime('%Y-%m-%dT%H:%M:%SZ','now')"
                      " WHERE id = ?", (json.dumps(tl.to_dict(), ensure_ascii=False), tl.duration, cid))
            vid = c.execute("INSERT INTO video_variant(clip_id, label, aspect, output_path) VALUES (?,?,?,?)",
                            (cid, Path(report["path"]).stem, tl.format.aspect, report["path"])).lastrowid
            c.execute("INSERT INTO export_job(variant_id, job_id, settings_json, output_path) VALUES (?,?,?,?)",
                      (vid, job_id, json.dumps({"report": report, "format": tl.to_dict()["format"],
                                                "export": tl.to_dict()["export"]}, ensure_ascii=False, default=str),
                       report["path"]))
        self.db.audit("export.done", report["path"])
        return vid

    def exports(self, project_id: int | None = None, limit: int = 50):
        sql = ("SELECT v.*, p.name project_name, e.settings_json FROM video_variant v JOIN clip c ON c.id = v.clip_id "
               "JOIN project p ON p.id = c.project_id LEFT JOIN export_job e ON e.variant_id = v.id")
        params: tuple = ()
        if project_id:
            sql += " WHERE p.id = ?"
            params = (project_id,)
        return self.db.query(sql + " ORDER BY v.id DESC LIMIT ?", (*params, limit))
