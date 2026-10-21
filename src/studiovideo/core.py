"""Contexte applicatif : assemble base, services, tâches (indépendant de l'interface)."""
from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

from .db.database import Database, open_database
from .jobs.manager import JobContext, JobManager
from .media import downloader
from .media.importer import MediaImporter
from .paths import AppPaths, app_paths
from .services.concepts import ConceptService
from .services.library import LibraryService
from .services.montage import MontageService
from .services.projects import ProjectService
from .services.publications import PublicationService
from .services.settings import SettingsService
from .services.transcripts import TranscriptService

log = logging.getLogger(__name__)


@dataclass
class AppContext:
    paths: AppPaths
    db: Database
    jobs: JobManager
    concepts: ConceptService
    projects: ProjectService
    publications: PublicationService
    library: LibraryService
    settings: SettingsService
    montage: MontageService
    transcripts: TranscriptService
    workspace_id: int

    def importer(self) -> MediaImporter:
        return MediaImporter(self.db, self.paths, self.workspace_id)

    def close(self) -> None:
        self.jobs.shutdown(wait=True)
        self.db.close()


def build_context(root: Path | None = None, max_workers: int | None = None) -> AppContext:
    paths = app_paths(root)
    db = open_database(paths.database, paths.backups)
    concepts = ConceptService(db)
    ws = concepts.default_workspace()
    settings = SettingsService(db)
    jobs = JobManager(db, max_workers=max_workers or int(settings.get("max_workers", 2)))
    ctx = AppContext(paths=paths, db=db, jobs=jobs, concepts=concepts, projects=ProjectService(db),
                     publications=PublicationService(db), library=LibraryService(db), settings=settings,
                     montage=MontageService(db), transcripts=TranscriptService(db), workspace_id=ws)
    _register_handlers(ctx)
    return ctx


def _register_handlers(app: AppContext) -> None:
    def import_file(ctx: JobContext, params: dict) -> dict:
        res = app.importer().import_file(
            params["path"], project_id=params.get("project_id"),
            allow_duplicate=params.get("allow_duplicate", False), progress=ctx.progress)
        return {"asset_id": res.asset_id, "kind": res.kind, "warnings": res.warnings}

    def import_url(ctx: JobContext, params: dict) -> dict:
        dest = app.paths.root / "downloads"
        path, meta = downloader.download(params["url"], dest, progress=ctx.progress,
                                         is_cancelled=lambda: ctx.cancelled)
        ctx.progress(0.92, "Analyse du média")
        res = app.importer().import_file(path, project_id=params.get("project_id"),
                                         origin_url=params["url"],
                                         provenance=f"{meta.get('extractor')}:{meta.get('uploader') or ''}")
        return {"asset_id": res.asset_id, "kind": res.kind, "warnings": res.warnings, "meta": meta}

    def export_timeline(ctx: JobContext, params: dict) -> dict:
        from .edit import render
        from .edit.model import Timeline
        tl = Timeline.from_dict(params["timeline"])
        sources, music = app.montage.sources_for(tl)
        dest = Path(params["dest"])

        def run(cmd, on_line):
            return ctx.run_process(cmd, on_line)
        report = render.export(tl, sources, music, dest, run, ctx.progress)
        report["variant_id"] = app.montage.record_export(params["project_id"], tl, report, ctx.job_id)
        return report

    def preview(ctx: JobContext, params: dict) -> dict:
        from .edit import render
        from .edit.model import Timeline
        tl = Timeline.from_dict(params["timeline"])
        sources, _ = app.montage.sources_for(tl)
        dest = app.paths.cache / "preview" / f"p{params['project_id']}_{ctx.job_id}.png"
        render.preview_frame(tl, sources, float(params["t"]), dest)
        return {"path": str(dest), "t": params["t"]}

    def analyze(ctx: JobContext, params: dict) -> dict:
        from .edit import render
        from .edit.model import Timeline
        tl = Timeline.from_dict(params["timeline"])
        sources, music = app.montage.sources_for(tl)
        ctx.progress(0.1, "Analyse du son")
        rep = render.analyze_audio(tl, sources, music)
        rep["silences"] = rep.get("silences", [])[:20]
        return rep

    def transcribe(ctx: JobContext, params: dict) -> dict:
        from .transcribe import engines
        a = app.library.get(params["asset_id"])
        if a is None:
            raise ValueError("Média introuvable dans la bibliothèque.")
        if a["audio_tracks"] < 1:
            raise ValueError("Ce média ne contient pas de son : rien à transcrire.")
        eng = engines.FasterWhisperEngine(app.paths.models, params.get("model") or "small")
        try:
            tr = engines.transcribe_file(eng, Path(a["path"]), params.get("language") or None, ctx.progress,
                                         lambda: ctx.cancelled, app.paths.temp)
        except engines.Cancelled:
            ctx.check()
            raise
        if not tr.segments:
            raise RuntimeError("Aucune parole détectée dans ce média.")
        app.transcripts.store_original(a["id"], tr)
        return {"asset_id": a["id"], "segments": len(tr.segments), "language": tr.language,
                "words": len(tr.all_words())}

    def download_model(ctx: JobContext, params: dict) -> dict:
        from .transcribe import engines
        try:
            p = engines.download_model(app.paths.models, params["model"], ctx.progress, lambda: ctx.cancelled)
        except engines.Cancelled:
            ctx.check()
            raise
        return {"model": params["model"], "path": str(p)}

    app.jobs.register("transcribe", transcribe)
    app.jobs.register("download_model", download_model)
    app.jobs.register("import_file", import_file)
    app.jobs.register("import_url", import_url)
    app.jobs.register("export_timeline", export_timeline)
    app.jobs.register("preview_frame", preview)
    app.jobs.register("analyze_audio", analyze)
