"""Archive de projet (.studioproj) : changer d'ordinateur, sauvegarder ou transmettre un projet.

Contenu : project.json (projet, montage, clips, transcriptions corrigées, traductions, doublages) et, si
demandé, les médias et les pistes doublées. À l'import, un média déjà présent dans la bibliothèque est
reconnu par son empreinte (rien n'est dupliqué) ; sinon il est extrait de l'archive ou retrouvé à son
emplacement d'origine ; à défaut il est signalé « introuvable ». Les identifiants sont renumérotés.
"""
from __future__ import annotations

import json
import shutil
import zipfile
from pathlib import Path
from typing import Callable

FORMAT = 1


class ArchiveError(RuntimeError):
    pass


def _safe_name(s: str) -> str:
    return "".join(c if c.isalnum() or c in " ._-" else "_" for c in s).strip() or "media"


def export_project(app, project_id: int, dest: Path, include_media: bool = True,
                   progress: Callable[[float, str], None] = lambda f, m: None) -> dict:
    from .. import __version__
    p = app.projects.get(project_id)
    if p is None:
        raise ArchiveError("Projet introuvable.")
    data = app.projects.load_data(project_id)
    tl = data.get("timeline") or {}
    ids = {r["id"] for r in app.db.query("SELECT id FROM source_asset WHERE project_id = ?", (project_id,))}
    ids |= {s["asset_id"] for s in tl.get("segments", [])}
    if (tl.get("music") or {}).get("asset_id"):
        ids.add(tl["music"]["asset_id"])
    ids |= {r["source_asset_id"] for r in app.db.query(
        "SELECT source_asset_id FROM clip WHERE project_id = ? AND source_asset_id IS NOT NULL", (project_id,))}
    c = app.concepts.get_concept(p["concept_id"])
    doc = {"format": FORMAT, "app_version": __version__,
           "concept": {"name": c.name, "color": c.color, "theme": c.theme, "language": c.language},
           "project": {"name": p["name"], "mode": p["mode"], "data": data},
           "assets": [], "clips": [], "transcripts": [], "translations": [], "dubs": []}
    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(".partial")
    total = len(ids) or 1
    with zipfile.ZipFile(tmp, "w", zipfile.ZIP_STORED) as z:
        for k, aid in enumerate(sorted(ids)):
            a = app.library.get(aid)
            if a is None:
                continue
            entry = {"id": aid, "kind": a["kind"], "file_name": a["file_name"], "fingerprint": a["fingerprint"],
                     "path": a["path"], "archived": None}
            if include_media and Path(a["path"]).exists():
                arc = f"media/{aid}_{_safe_name(a['file_name'])}"
                progress(0.9 * k / total, f"Ajout de {a['file_name']}")
                z.write(a["path"], arc)
                entry["archived"] = arc
            doc["assets"].append(entry)
            t = app.db.query_one("SELECT * FROM transcript WHERE source_asset_id = ? ORDER BY id DESC LIMIT 1", (aid,))
            if t:
                doc["transcripts"].append({"asset_id": aid, "language": t["language"], "engine": t["engine"],
                                           "original_json": t["original_json"], "edited_json": t["edited_json"]})
            for t in app.db.query("SELECT * FROM translation WHERE source_asset_id = ?", (aid,)):
                doc["translations"].append({"asset_id": aid, "language": t["language"], "engine": t["engine"],
                                            "original_json": t["original_json"], "edited_json": t["edited_json"]})
            for d in app.db.query("SELECT * FROM dub WHERE source_asset_id = ?", (aid,)):
                files = []
                if include_media and Path(d["directory"]).exists():
                    for f in Path(d["directory"]).glob("*.wav"):
                        arc = f"dubs/{aid}_{d['language']}/{f.name}"
                        z.write(f, arc)
                        files.append(arc)
                doc["dubs"].append({"asset_id": aid, "language": d["language"], "engine": d["engine"],
                                    "manifest_json": d["manifest_json"], "files": files})
        for r in app.db.query("SELECT * FROM clip WHERE project_id = ? ORDER BY id", (project_id,)):
            doc["clips"].append({k: r[k] for k in ("source_asset_id", "name", "start_s", "end_s", "edit_json")})
        z.writestr("project.json", json.dumps(doc, ensure_ascii=False, indent=1))
    tmp.replace(dest)
    progress(1.0, "Archive créée")
    return {"path": str(dest), "assets": len(doc["assets"]),
            "archived": sum(1 for a in doc["assets"] if a["archived"])}


def _remap_timeline(tl: dict, m: dict) -> dict:
    for s in tl.get("segments", []):
        s["asset_id"] = m.get(s["asset_id"], s["asset_id"])
    if (tl.get("music") or {}).get("asset_id"):
        tl["music"]["asset_id"] = m.get(tl["music"]["asset_id"], tl["music"]["asset_id"])
    return tl


def import_project(app, archive: Path, media_dir: Path, concept_id: int | None = None,
                   progress: Callable[[float, str], None] = lambda f, m: None) -> dict:
    archive = Path(archive)
    try:
        z = zipfile.ZipFile(archive)
    except zipfile.BadZipFile as exc:
        raise ArchiveError("Fichier d'archive invalide.") from exc
    with z:
        names = set(z.namelist())
        if "project.json" not in names:
            raise ArchiveError("Ce n'est pas une archive de projet Studio Vidéo.")
        for n in names:                                  # protection contre les chemins hors du dossier
            if n.startswith("/") or ".." in Path(n).parts:
                raise ArchiveError("Archive invalide (chemin interdit).")
        doc = json.loads(z.read("project.json").decode("utf-8"))
        if doc.get("format", 0) > FORMAT:
            raise ArchiveError("Archive créée par une version plus récente : mettez Studio Vidéo à jour.")
        if concept_id is None:
            cname = doc["concept"]["name"]
            found = next((c for c in app.concepts.list_concepts() if c.name == cname), None)
            concept_id = found.id if found else app.concepts.create_concept(
                cname, theme=doc["concept"].get("theme", ""), color=doc["concept"].get("color", "#8A8F98")).id
        name = doc["project"]["name"]
        existing = {r["name"] for r in app.db.query("SELECT name FROM project")}
        if name in existing:
            name = f"{name} (importé)"
        pid = app.projects.create(concept_id, name, doc["project"]["mode"])
        folder = Path(media_dir) / _safe_name(name)
        mapping, missing, reused, extracted = {}, [], 0, 0
        importer = app.importer()
        for k, a in enumerate(doc["assets"]):
            progress(0.85 * k / max(1, len(doc["assets"])), f"Média {a['file_name']}")
            dup = importer.find_duplicate(a["fingerprint"])
            if dup:
                mapping[a["id"]] = dup
                reused += 1
                continue
            src = None
            if a.get("archived") and a["archived"] in names:
                folder.mkdir(parents=True, exist_ok=True)
                target = folder / a["file_name"]
                with z.open(a["archived"]) as fin, open(target, "wb") as fout:
                    shutil.copyfileobj(fin, fout, 1 << 20)
                src, extracted = target, extracted + 1
            elif a.get("path") and Path(a["path"]).exists():
                src = Path(a["path"])
            if src is not None:
                mapping[a["id"]] = importer.import_file(src, project_id=pid, allow_duplicate=True,
                                                        provenance="archive").asset_id
            elif importer.find_duplicate(a["fingerprint"] + "-absent"):
                mapping[a["id"]] = importer.find_duplicate(a["fingerprint"] + "-absent")
                missing.append(a["file_name"])
            else:
                with app.db.transaction() as c:
                    mapping[a["id"]] = c.execute(
                        "INSERT INTO source_asset(workspace_id, project_id, kind, path, file_name, fingerprint, "
                        "missing, provenance) VALUES (?,?,?,?,?,?,1,'archive')",
                        (app.workspace_id, pid, a["kind"], a.get("path") or "", a["file_name"],
                         a["fingerprint"] + "-absent")).lastrowid
                missing.append(a["file_name"])
        data = doc["project"]["data"]
        if data.get("timeline"):
            data["timeline"] = _remap_timeline(data["timeline"], mapping)
        for key in ("last_asset_id", "original_asset"):
            if data.get(key) in mapping:
                data[key] = mapping[data[key]]
        app.projects.save_data(pid, data)
        with app.db.transaction() as c:
            for cl in doc["clips"]:
                ej = json.loads(cl["edit_json"] or "{}")
                if "segments" in ej:
                    ej = _remap_timeline(ej, mapping)
                c.execute("INSERT INTO clip(project_id, source_asset_id, name, start_s, end_s, edit_json) "
                          "VALUES (?,?,?,?,?,?)", (pid, mapping.get(cl["source_asset_id"]), cl["name"],
                                                    cl["start_s"], cl["end_s"], json.dumps(ej, ensure_ascii=False)))
            for t in doc["transcripts"]:
                aid = mapping.get(t["asset_id"])
                if aid and not c.execute("SELECT 1 FROM transcript WHERE source_asset_id = ?", (aid,)).fetchone():
                    c.execute("INSERT INTO transcript(source_asset_id, language, engine, original_json, edited_json) "
                              "VALUES (?,?,?,?,?)", (aid, t["language"], t["engine"], t["original_json"],
                                                     t["edited_json"]))
            for t in doc["translations"]:
                aid = mapping.get(t["asset_id"])
                if aid:
                    c.execute("INSERT OR IGNORE INTO translation(source_asset_id, language, engine, original_json, "
                              "edited_json) VALUES (?,?,?,?,?)", (aid, t["language"], t["engine"],
                                                                 t["original_json"], t["edited_json"]))
        for d in doc["dubs"]:
            aid = mapping.get(d["asset_id"])
            if not aid or not d["files"] or app.dubs.get(aid, d["language"]):
                continue
            ddir = app.dubs.directory(aid, d["language"])
            ddir.mkdir(parents=True, exist_ok=True)
            for f in d["files"]:
                with z.open(f) as fin, open(ddir / Path(f).name, "wb") as fout:
                    shutil.copyfileobj(fin, fout, 1 << 20)
            from ..edit.dubbing import DubManifest
            man = DubManifest.from_json(d["manifest_json"])
            man.asset_id = aid
            app.dubs.store(man, ddir)
    app.db.audit("project.import", f"{archive.name} -> {pid}")
    progress(1.0, "Projet importé")
    return {"project_id": pid, "name": name, "reused": reused, "extracted": extracted, "missing": missing}
