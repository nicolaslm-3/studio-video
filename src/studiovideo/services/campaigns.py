"""Campagnes : brief enregistré dans le projet, rapport de conformité de chaque export (§4 mode 5)."""
from __future__ import annotations

import json
from pathlib import Path

from ..db.database import Database
from ..edit import campaign as cp


class CampaignService:
    def __init__(self, db: Database, projects, transcripts):
        self.db, self.projects, self.transcripts = db, projects, transcripts

    def brief(self, project_id: int) -> cp.Brief:
        return cp.Brief.from_dict(self.projects.load_data(project_id).get("brief"))

    def save_brief(self, project_id: int, brief: cp.Brief) -> None:
        data = self.projects.load_data(project_id)
        data["brief"] = brief.to_dict()
        self.projects.save_data(project_id, data)
        self.db.audit("campaign.brief", f"project={project_id}")

    def brief_for_variant(self, variant_id: int | None) -> cp.Brief | None:
        if not variant_id:
            return None
        r = self.db.query_one("SELECT p.id, p.mode FROM video_variant v JOIN clip c ON c.id = v.clip_id "
                              "JOIN project p ON p.id = c.project_id WHERE v.id = ?", (variant_id,))
        if not r or r["mode"] != "campaign":
            return None
        b = self.brief(r["id"])
        return b if b.defined else None

    def report(self, project_id: int) -> list[dict]:
        """Une ligne par export du projet : chemin, existence, contrôles."""
        b = self.brief(project_id)
        rows = self.db.query("SELECT v.id, v.output_path, v.aspect, e.settings_json FROM video_variant v "
                             "JOIN clip c ON c.id = v.clip_id LEFT JOIN export_job e ON e.variant_id = v.id "
                             "WHERE c.project_id = ? ORDER BY v.id", (project_id,))
        out = []
        for r in rows:
            st = json.loads(r["settings_json"] or "{}")
            tl = st.get("timeline")
            rep = st.get("report") or {}
            if tl:
                ids = {s["asset_id"] for s in tl.get("segments", [])}
                trs = {i: self.transcripts.get(i) for i in ids}
                info = cp.video_info(tl, rep, {i: t for i, t in trs.items() if t is not None})
            else:   # export antérieur à la 0.16 : seuls le format et la durée sont connus
                info = {"aspect": r["aspect"], "duration": rep.get("duration"), "subtitles": None,
                        "spoken": "", "shown": "", "transcribed": False}
            checks = cp.check_video(b, info)
            if not Path(r["output_path"]).exists():
                checks.insert(0, cp.Check("Fichier", False, "introuvable (déplacé ou supprimé)"))
            out.append({"variant_id": r["id"], "path": r["output_path"], "checks": checks,
                        "summary": cp.summary(checks)})
        return out
