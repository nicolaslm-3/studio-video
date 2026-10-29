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
from .services.candidates import CandidateService
from .services.concepts import ConceptService
from .services.library import LibraryService
from .services.montage import MontageService
from .services.projects import ProjectService
from .services.publications import PublicationService
from .services.settings import SettingsService
from .services.transcripts import TranscriptService
from .services.analytics import AnalyticsService
from .services.dubs import DubService, StemService

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
    candidates: CandidateService
    workspace_id: int
    dubs: DubService | None = None
    stems: StemService | None = None
    analytics: AnalyticsService | None = None

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
                     montage=MontageService(db, paths.root / "stems"), transcripts=TranscriptService(db),
                     candidates=CandidateService(db), workspace_id=ws,
                     dubs=DubService(db, paths.dubs), stems=StemService(paths.root / "stems"),
                     analytics=AnalyticsService(db))
    _register_handlers(ctx)
    return ctx


def asr_params(app: AppContext) -> dict | str:
    """Moteur de transcription choisi (roue crantée de « Transcription ») : paramètres de la tâche, ou la
    raison pour laquelle il ne peut pas marcher."""
    from .services import secrets
    from .transcribe import engines
    if (app.settings.get("transcribe_engine", None) or "local") == "elevenlabs":
        if (secrets.get_secret("elevenlabs_api_key") or "").startswith("sk_"):
            return {"engine": "elevenlabs"}
        return "clé ElevenLabs absente (roue crantée de « Transcription », en haut)."
    have = engines.installed_models(app.paths.models) if engines.available() else []
    if not have:
        return ("aucun modèle de transcription installé (roue crantée de « Transcription », en haut, ou "
                "Paramètres → Matériel et moteurs).")
    pref = app.settings.get("whisper_model", None)
    model = pref if pref in have else next((m for m in ("small", "base", "medium", "large-v3-turbo", "tiny")
                                            if m in have), have[0])
    return {"engine": "local", "model": model}


def _utc_now() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _register_handlers(app: AppContext) -> None:
    def import_file(ctx: JobContext, params: dict) -> dict:
        res = app.importer().import_file(
            params["path"], project_id=params.get("project_id"),
            allow_duplicate=params.get("allow_duplicate", False), progress=ctx.progress)
        _maybe_proxy(res.asset_id)
        out = {"asset_id": res.asset_id, "kind": res.kind, "warnings": res.warnings}
        if params.get("auto_clips") and res.kind == "video" and params.get("project_id"):
            app.jobs.submit("auto_clips", f"Extraits automatiques · {Path(params['path']).name}",
                            {"asset_id": res.asset_id, "project_id": params["project_id"]})
            out["auto_clips"] = True       # cette tâche transcrit elle-même avant de chercher les moments
        elif (w := _auto_transcribe(res.asset_id)):
            out["warning"] = w
        return out

    def _auto_transcribe(asset_id: int) -> str | None:
        """Chaque vidéo ou son importé est transcrit d'office, en arrière-plan (langue détectée).
        Renvoie un avertissement à afficher quand c'est impossible, sinon None."""
        a = app.library.get(asset_id)
        if (a is None or a["kind"] not in ("video", "audio") or not a["audio_tracks"]
                or not app.settings.get("auto_transcribe", True) or app.transcripts.has(asset_id)):
            return None
        busy = app.db.query_one("SELECT COUNT(*) n FROM job WHERE kind='transcribe' AND status IN "
                                "('pending','running') AND json_extract(params_json, '$.asset_id') = ?",
                                (asset_id,))["n"]
        if busy:
            return None
        how = _asr()
        if isinstance(how, str):
            return "transcription automatique impossible : " + how
        app.jobs.submit("transcribe", f"Transcription · {a['file_name']}", {"asset_id": asset_id, **how, "auto": True})
        return None

    def _asr() -> dict | str:
        return asr_params(app)

    def _maybe_proxy(asset_id: int) -> None:
        from .media import proxy
        row = app.library.get(asset_id)
        if row and proxy.needed(row) and not proxy.path_for(app.paths.proxies, asset_id).exists() \
                and app.settings.get("proxies", True):
            app.jobs.submit("make_proxy", f"Aperçu léger · {row['file_name']}", {"asset_id": asset_id})

    def make_proxy(ctx: JobContext, params: dict) -> dict:
        from .media import proxy
        row = app.library.get(params["asset_id"])
        if row is None or not Path(row["path"]).exists():
            raise ValueError("Média introuvable.")
        dest = proxy.path_for(app.paths.proxies, row["id"])
        proxy.make(Path(row["path"]), dest, row["duration_s"], ctx.run_process, ctx.progress)
        return {"asset_id": row["id"], "path": str(dest)}

    app.jobs.register("make_proxy", make_proxy)

    def waveform_job(ctx: JobContext, params: dict) -> dict:
        from .media import waveform
        row = app.library.get(params["asset_id"])
        if row is None or not Path(row["path"]).exists():
            raise ValueError("Média introuvable.")
        if not row["audio_tracks"]:
            return {"asset_id": row["id"], "peaks": 0}
        peaks = waveform.ensure(app.paths.root, row["fingerprint"], Path(row["path"]), lambda: ctx.cancelled)
        return {"asset_id": row["id"], "peaks": int(peaks.size)}

    app.jobs.register("waveform", waveform_job)

    def import_url(ctx: JobContext, params: dict) -> dict:
        dest = app.paths.root / "downloads"
        path, meta = downloader.download(params["url"], dest, progress=ctx.progress,
                                         is_cancelled=lambda: ctx.cancelled)
        ctx.progress(0.92, "Analyse du média")
        title = " ".join(str(meta.get("title") or "").split())[:120]          # nom affiché : le titre de la vidéo
        res = app.importer().import_file(path, project_id=params.get("project_id"),
                                         origin_url=params["url"],
                                         provenance=f"{meta.get('extractor')}:{meta.get('uploader') or ''}",
                                         display_name=(title + Path(path).suffix) if title else "")
        out = {"asset_id": res.asset_id, "kind": res.kind, "warnings": res.warnings, "meta": meta}
        if params.get("auto_clips") and res.kind == "video" and params.get("project_id"):
            app.jobs.submit("auto_clips", f"Extraits automatiques · {Path(path).name}",
                            {"asset_id": res.asset_id, "project_id": params["project_id"]})
            out["auto_clips"] = True
        elif (w := _auto_transcribe(res.asset_id)):
            out["warning"] = w
        return out

    def export_timeline(ctx: JobContext, params: dict) -> dict:
        from .edit import render
        from .edit.model import Timeline
        tl = Timeline.from_dict(params["timeline"])
        sources, music = app.montage.sources_for(tl)
        dest = Path(params["dest"])
        if tl.video.auto_reframe and tl.format.fit == "fill":
            from .edit import reframe
            todo = [sg for sg in tl.segments if not sg.reframe and (sources[sg.asset_id].width or 0) >
                    (sources[sg.asset_id].height or 0)]
            for k, sg in enumerate(todo):
                res = reframe.analyze(Path(sources[sg.asset_id].path), sg.src_in, sg.src_out, app.paths.models,
                                      lambda f, m: ctx.progress(0.1 * (k + f) / len(todo), m), lambda: ctx.cancelled)
                sg.reframe = [list(x) for x in res["keys"]]

        def run(cmd, on_line):
            return ctx.run_process(cmd, on_line)
        report = render.export(tl, sources, music, dest, run, ctx.progress)
        report["variant_id"] = app.montage.record_export(params["project_id"], tl, report, ctx.job_id)
        if (params.get("caption") or "").strip():          # légende de publication, à côté de la vidéo
            dest.with_suffix(".txt").write_text(params["caption"].strip(), encoding="utf-8")
        return report

    def preview(ctx: JobContext, params: dict) -> dict:
        from .edit import render
        from .edit.model import Timeline
        tl = Timeline.from_dict(params["timeline"])
        sources, _ = app.montage.sources_for(tl)
        dest = app.paths.cache / "preview" / f"p{params['project_id']}_{ctx.job_id}.png"
        render.preview_frame(tl, sources, float(params["t"]), dest)
        return {"path": str(dest), "t": params["t"]}

    def export_still(ctx: JobContext, params: dict) -> dict:
        """Image fixe en pleine définition (miniature, affiche, visuel de réseau social)."""
        from .edit import render
        from .edit.model import Timeline
        tl = Timeline.from_dict(params["timeline"])
        sources, _ = app.montage.sources_for(tl)
        dest = Path(params["dest"])
        dest.parent.mkdir(parents=True, exist_ok=True)
        render.preview_frame(tl, sources, float(params["t"]), dest, max_height=tl.format.height)
        return {"path": str(dest)}

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
        if params.get("engine") == "elevenlabs":
            from .services import secrets
            eng = engines.ScribeEngine(secrets.get_secret("elevenlabs_api_key") or "")
        else:
            eng = engines.FasterWhisperEngine(app.paths.models, params.get("model") or "small")
        try:
            tr = engines.transcribe_file(eng, Path(a["path"]), params.get("language") or None, ctx.progress,
                                         lambda: ctx.cancelled, app.paths.temp)
        except engines.Cancelled:
            ctx.check()
            raise
        if not tr.segments:
            if params.get("auto"):          # import : pas un échec, et surtout aucun texte inventé
                return {"asset_id": a["id"], "segments": 0, "language": tr.language, "words": 0,
                        "warning": "aucune parole exploitable n'a été détectée."}
            raise RuntimeError("Aucune parole détectée dans ce média.")
        app.transcripts.store_original(a["id"], tr)
        return {"asset_id": a["id"], "segments": len(tr.segments), "language": tr.language,
                "words": len(tr.all_words())}

    def download_model(ctx: JobContext, params: dict) -> dict:
        from .transcribe import engines
        if params["model"].startswith("piper:"):
            from .ai import piper
            what = params["model"][6:]
            try:
                if what == "bin":
                    p = piper.install_engine(app.paths.models, ctx.progress, lambda: ctx.cancelled)
                elif what == "catalog":
                    return {"model": params["model"], "voices": len(piper.catalog(app.paths.models, refresh=True))}
                else:
                    info = next((v for v in piper.catalog(app.paths.models) if v.key == what), None)
                    if info is None:
                        raise ValueError(f"Voix inconnue : {what}")
                    p = piper.download_voice(app.paths.models, info, ctx.progress, lambda: ctx.cancelled)
            except piper.PiperError:
                ctx.check()
                raise
            return {"model": params["model"], "path": str(p)}
        if params["model"].startswith("spk:"):
            from .ai import diarize
            try:
                p = diarize.download_model(app.paths.models, params["model"][4:], ctx.progress,
                                           lambda: ctx.cancelled)
            except diarize.DiarizationError:
                ctx.check()
                raise
            return {"model": params["model"], "path": str(p)}
        if params["model"].startswith("sep:"):
            from .edit import separate
            try:
                p = separate.download_model(app.paths.models, params["model"][4:], ctx.progress,
                                            lambda: ctx.cancelled)
            except separate.SeparationError:
                ctx.check()
                raise
            return {"model": params["model"], "path": str(p)}
        try:
            p = engines.download_model(app.paths.models, params["model"], ctx.progress, lambda: ctx.cancelled)
        except engines.Cancelled:
            ctx.check()
            raise
        return {"model": params["model"], "path": str(p)}

    def find_highlights(ctx: JobContext, params: dict) -> dict:
        import tempfile

        from .ai import highlights as hl
        from .transcribe import engines
        aid, pid = params["asset_id"], params["project_id"]
        a = app.library.get(aid)
        tr = app.transcripts.get(aid)
        if a is None or tr is None:
            raise ValueError("Transcrivez d'abord ce média : l'analyse s'appuie sur la transcription.")
        req = hl.Request(**{k: v for k, v in params["request"].items() if k in hl.Request.__dataclass_fields__})
        audio = None
        if a["audio_tracks"]:
            ctx.progress(0.02, "Analyse du son (volume, cris, rires)")
            with tempfile.TemporaryDirectory(dir=str(app.paths.temp)) as d:
                wav = engines.extract_audio(Path(a["path"]), Path(d) / "a.wav")
                ctx.check()
                audio = hl.audio_profile(wav, 0.5)
        eng_cfg = params.get("engine") or {"kind": "local"}
        if eng_cfg.get("kind") in ("ollama", "openai"):
            from .services import secrets
            key = secrets.get_secret("llm_api_key") or "" if eng_cfg["kind"] == "openai" else ""
            engine = hl.LLMEngine(eng_cfg["kind"], eng_cfg.get("model", ""), eng_cfg.get("base_url", ""), key)
        else:
            engine = hl.LocalEngine(step=0.5, audio=audio)
        cands = engine.propose(tr, req, ctx.progress)
        ctx.check()
        ids = app.candidates.replace_proposals(pid, aid, cands, req)
        return {"count": len(ids), "asset_id": aid, "engine": getattr(engine, "name", "local")}

    def auto_clips(ctx: JobContext, params: dict) -> dict:
        """Vidéo importée en Clipping : transcription (si un modèle est installé) puis extraits, d'office."""
        aid, pid = params["asset_id"], params["project_id"]
        a = app.library.get(aid)
        if a is None:
            raise ValueError("Média introuvable.")
        transcribed = False
        if not app.transcripts.has(aid):
            how = _asr()
            if isinstance(how, str):
                return {"asset_id": aid, "project_id": pid, "count": 0,
                        "skipped": f"Transcription impossible : {how} Puis lancez la recherche d'extraits."}
            ctx.progress(0.01, "Transcription")
            transcribe(ctx, {"asset_id": aid, **how})
            transcribed = True
        # critères choisis pendant la transcription (écran Clipping) ; aucun coché : politique
        from .ai.highlights import criteria_request
        crit = app.projects.load_data(pid).get("clip_criteria") if app.projects.get(pid) else None
        req = criteria_request(crit, float(a["duration_s"] or 0)).__dict__
        llm = app.settings.get("llm", None) or {}
        eng = ({"kind": llm["kind"], "model": llm.get("model", ""), "base_url": llm.get("base_url", "")}
               if llm.get("kind") in ("ollama", "openai") and llm.get("model") else {"kind": "local"})
        ctx.progress(0.5, "Recherche des extraits")
        res = find_highlights(ctx, {"asset_id": aid, "project_id": pid, "request": req, "engine": eng})
        return {**res, "project_id": pid, "transcribed": transcribed}

    class _Sub:
        """Contexte d'une étape : la progression de 0 à 1 de l'étape occupe [a, b] de la tâche entière."""

        def __init__(self, ctx: JobContext, a: float, b: float, label: str = ""):
            self._ctx, self.a, self.b, self.label = ctx, a, b, label

        def progress(self, fraction: float, message: str = "") -> None:
            f = max(0.0, min(1.0, fraction))
            self._ctx.progress(self.a + (self.b - self.a) * f, f"{self.label}{message}")

        def __getattr__(self, name):
            return getattr(self._ctx, name)

    def _ensure_transcript(ctx: JobContext, aid: int) -> None:
        """Transcription disponible : attend celle lancée à l'import si elle tourne, sinon la fait ici."""
        import time

        while not app.transcripts.has(aid):
            rows = app.db.query("SELECT id, status FROM job WHERE kind='transcribe' AND status IN "
                                "('pending','running') AND json_extract(params_json, '$.asset_id') = ?", (aid,))
            running = [r for r in rows if r["status"] == "running"]
            if not running:
                for r in rows:                  # en file derrière cette tâche : on la fait ici, sans attendre
                    app.jobs.cancel(r["id"])
                break
            ctx.check()
            ctx.progress(0.02, "Transcription en cours (lancée à l'import)…")
            time.sleep(0.5)
        if app.transcripts.has(aid):
            return
        how = _asr()
        if isinstance(how, str):
            raise ValueError("Transcription impossible : " + how)
        transcribe(_Sub(ctx, 0.0, 0.2, "Transcription · "), {"asset_id": aid, **how})

    def translate_video(ctx: JobContext, params: dict) -> dict:
        """Espace Traduction, en un clic : pour chaque langue, traduction, doublage facultatif, sous-titres
        dans le style choisi, texte du haut et légende traduits, puis export MP4 vérifié.

        export=False : prépare seulement (traduction, voix, texte du haut traduit, zones à masquer) ; la vidéo
        de la langue choisie est ensuite exportée depuis l'onglet Export, sur le montage du projet."""
        import re

        from .ai import translate as tr_mod
        from .edit import translation
        from .edit.model import Timeline
        from .services import secrets
        aid, pid = params["asset_id"], params["project_id"]
        a = app.library.get(aid)
        if a is None:
            raise ValueError("Média introuvable dans la bibliothèque.")
        if not a["audio_tracks"]:
            raise ValueError("Cette vidéo n'a pas de son : rien à traduire.")
        _ensure_transcript(ctx, aid)
        src = app.transcripts.get(aid)
        if src is None or not src.segments:
            raise ValueError("Aucune parole exploitable n'a été détectée : rien à traduire.")
        langs = [lg for lg in params.get("languages") or [] if lg != src.language]
        skipped = [lg for lg in params.get("languages") or [] if lg == src.language]
        if not langs:
            raise ValueError("La vidéo est déjà dans la langue choisie.")
        voice = params.get("voice") or None
        keep_music = bool(params.get("keep_music", True))
        names = tr_mod.LANG_NAMES
        masks = []
        if params.get("erase_texts") and a["kind"] == "video":
            masks = detect_text(_Sub(ctx, 0.2, 0.25, "Textes d'origine · "), {"asset_id": aid})["zones"]
        eleven_dub = bool(voice and voice.get("engine") == "eleven_dub")
        if eleven_dub:
            keep_music = False                       # la piste d'ElevenLabs contient déjà le fond sonore
        if voice and keep_music and not app.stems.has(aid):
            separate_job(_Sub(ctx, 0.25, 0.4, "Séparation voix / musique · "), {"asset_id": aid})
        eng = _tr_cfg(params.get("engine"))
        translator = tr_mod.make_translator(eng, secrets.get_secret)
        used: list[str] = []
        notes: list[str] = []
        template = Timeline.from_dict(params["timeline"])
        folder = Path(params["folder"])
        stem = re.sub(r'[\\/:*?"<>|]+', " ", Path(a["file_name"]).stem).strip()[:60] or "video"
        files, errors = [], []
        do_export = params.get("export", True)
        top_texts: dict[str, str] = {}
        versions: dict[str, int] = {}
        # résultat de chaque outil, langue par langue : "ok", "off" (outil non choisi) ou la raison de l'échec
        status: dict[str, dict] = {}
        subs_on = params.get("subtitles", True)
        span = 0.6 / len(langs)
        for k, lang in enumerate(langs):
            a0 = 0.4 + k * span
            tag = f"{lang.upper()} · "
            st = status[lang] = {"subtitles": "ok" if subs_on else "off", "voice": "off"}
            dubbed = False
            try:
                if eleven_dub:
                    # doublage ElevenLabs : sa traduction (ses sous-titres) sert aux sous-titres de la langue
                    try:
                        rd = eleven_dub_job(_Sub(ctx, a0, a0 + span * 0.6, tag + "voix · "),
                                            {"asset_id": aid, "language": lang, "source": src.language})
                        st["voice"] = "ok"
                        dubbed = True
                        if rd.get("subtitles"):
                            used += [e for e in ["elevenlabs"] if e not in used]
                        elif rd.get("subtitles_error"):
                            notes.append(f"{lang.upper()} : sous-titres d'ElevenLabs indisponibles "
                                         f"({rd['subtitles_error']}), traduction par un autre moteur")
                    except Exception as exc:
                        ctx.check()
                        st["voice"] = (str(exc).splitlines() or [repr(exc)])[0][:300]
                if app.transcripts.translation(aid, lang) is None:
                    r = translate(_Sub(ctx, a0, a0 + span * 0.3, tag + "traduction du texte · "),
                                  {"asset_id": aid, "language": lang, "engine": eng,
                                   "glossary": params.get("glossary") or []})
                    used += [e for e in r.get("engines", []) if e not in used]
                    notes += [n for n in r.get("failures", []) if n not in notes]
                if voice and not eleven_dub:
                    # la voix peut échouer seule : la langue garde alors ses sous-titres traduits
                    try:
                        vid = _voice_for(voice, lang)
                        from .ai.tts import ENGINES as TTS
                        ek = voice.get("engine") or "elevenlabs"
                        rd = dub(_Sub(ctx, a0 + span * 0.3, a0 + span * 0.6, tag + "voix · "),
                                 {"asset_id": aid, "language": lang, "engine": ek, "voices": {},
                                  "default_voice": vid,
                                  "label": f"{TTS.get(ek, (ek,))[0]} · {voice.get('name') or vid}"})
                        st["voice"] = "ok"
                        dubbed = True
                        if rd.get("errors"):
                            st["voice_missing"] = len(rd["errors"])
                    except Exception as exc:
                        ctx.check()
                        st["voice"] = (str(exc).splitlines() or [repr(exc)])[0][:300]
                top = (params.get("top_text") or "").strip()
                if top:
                    top = translator.translate([top], src.language, lang)[0]
                top_texts[lang] = top
                if not do_export:
                    # version traduite : un projet rangé sous le projet d'origine, avec ses sous-titres et sa voix
                    tl = translation.translated_timeline(
                        template, aid, float(a["duration_s"] or src.segments[-1].end), lang,
                        app.transcripts.translation(aid, lang), params.get("preset") or "white", top_text=top,
                        masks=masks, dubbed=dubbed, keep_music=keep_music,
                        text_style=app.montage.text_style_for(pid), subtitles=subs_on)
                    versions[lang] = app.projects.save_version(params.get("root_id") or pid, lang, tl.to_dict(),
                                                               {"project_video": aid})
                    files.append(lang)
                    continue
                tl = translation.translated_timeline(
                    template, aid, float(a["duration_s"] or src.segments[-1].end), lang,
                    app.transcripts.translation(aid, lang), params.get("preset") or "white",
                    aspect=params.get("aspect"), top_text=top,
                    masks=masks, dubbed=dubbed, keep_music=keep_music,
                    text_style=app.montage.text_style_for(pid))
                dest = folder / f"{stem}_{lang}.mp4"
                export_timeline(_Sub(ctx, a0 + span * 0.6, a0 + span, tag + "export · "),
                                {"timeline": tl.to_dict(), "dest": str(dest), "project_id": pid})
                caption = (params.get("caption") or "").strip()
                if caption:
                    dest.with_suffix(".txt").write_text(translator.translate([caption], src.language, lang)[0],
                                                        encoding="utf-8")
                files.append(str(dest))
            except Exception as exc:                          # une langue en échec n'arrête pas les autres
                ctx.check()
                why = (str(exc).splitlines() or [repr(exc)])[0][:400]
                errors.append(f"{lang.upper()} : {why}")
                st["subtitles"] = why if subs_on else "off"
                if voice and st["voice"] in ("off", "ok"):
                    st["voice"] = why
        if not files:
            raise RuntimeError(("Aucune vidéo produite.\n" if do_export else "Aucune langue préparée.\n")
                               + "\n".join(errors))
        ready = files if not do_export else [lg for lg in langs if not any(e.startswith(lg.upper() + " ")
                                                                            for e in errors)]
        out = {"files": files if do_export else [], "folder": str(folder), "languages": langs, "errors": errors,
               "skipped": skipped, "asset_id": aid, "project_id": pid, "ready": ready,
               "top_texts": top_texts, "masks": masks, "dubbed": bool(voice), "keep_music": keep_music,
               "versions": versions, "status": status,
               "engines": [tr_mod.ENGINES.get(e.split(":")[0], (e,))[0] for e in used], "notes": notes}
        voice_fails = [lg for lg, s in status.items() if s["voice"] not in ("ok", "off")]
        if voice_fails and not errors:
            out["warning"] = "Voix en échec : " + ", ".join(names.get(lg, lg) for lg in voice_fails)
        if errors:
            out["warning"] = f"{len(errors)} langue(s) en échec : " + " ; ".join(errors)
        return out

    def optimize_audio(ctx: JobContext, params: dict) -> dict:
        from .edit import audiofix
        from .edit.model import Timeline
        tl = Timeline.from_dict(params["timeline"])
        sources, music = app.montage.sources_for(tl)
        p = audiofix.optimize(tl, sources, music, ctx.progress)
        return {"audio": p.audio, "music": p.music, "seg_volumes": p.seg_volumes, "report": p.report,
                "warnings": p.warnings}

    def _tr_cfg(engine) -> dict:
        """Réglages d'un moteur de traduction : choix (« auto » par défaut) et réglages enregistrés."""
        from .ai.translate import settings_cfg
        return settings_cfg(app.settings.get, engine)

    def _voice_for(voice: dict, lang: str) -> str:
        """Voix de la langue : ElevenLabs et OpenAI parlent toutes les langues avec la même voix ; Piper a une
        voix par langue (la voix choisie si elle est de cette langue, sinon une voix installée de la langue)."""
        if voice.get("engine") != "piper":
            return voice["id"]
        from .ai import piper
        from .ai.translate import LANG_NAMES
        have = piper.installed_voices(app.paths.models)
        if voice["id"] in have and voice["id"].split("_")[0] == lang:
            return voice["id"]
        same = [v for v in have if v.split("_")[0] == lang]
        if not same:
            raise ValueError(f"aucune voix hors ligne en {LANG_NAMES.get(lang, lang)} : téléchargez-en une "
                             "(roue crantée de la voix, onglet Traduction) ou choisissez une voix ElevenLabs ou "
                             "OpenAI, qui parlent toutes les langues.")
        return same[0]

    def translate(ctx: JobContext, params: dict) -> dict:
        from .ai import translate as tr_mod
        from .services import secrets
        aid, dst = params["asset_id"], params["language"]
        src_tr = app.transcripts.get(aid)
        if src_tr is None:
            raise ValueError("Transcrivez d'abord ce média.")
        cfg = _tr_cfg(params.get("engine"))
        if cfg["engine"] == "elevenlabs":                 # ElevenLabs traduit à partir du son de la vidéo
            r = eleven_dub_job(ctx, {"asset_id": aid, "language": dst, "source": src_tr.language,
                                     "keep_voice": False})
            return {"asset_id": aid, "language": dst, "segments": r["subtitles"], "engines": ["elevenlabs"],
                    "failures": []}
        translator = tr_mod.make_translator(cfg, secrets.get_secret)
        out = tr_mod.translate_transcript(src_tr, translator, dst, params.get("glossary") or [], ctx.progress,
                                          is_cancelled=lambda: ctx.cancelled)
        ctx.check()
        used = getattr(translator, "used", None) or [translator.name]
        app.transcripts.store_translation(aid, out, label=", ".join(
            dict.fromkeys(tr_mod.ENGINES.get(e.split(":")[0], (e,))[0] for e in used)))
        return {"asset_id": aid, "language": dst, "segments": len(out.segments), "engines": used,
                "failures": getattr(translator, "failures", [])}

    def _tts(kind: str):
        from .ai import tts
        from .services import secrets
        return tts.make_engine(kind, secrets.get_secret, app.paths.models, app.settings.get("llm", None) or {})

    def dub(ctx: JobContext, params: dict) -> dict:
        """Doublage complet d'une traduction (§10.2, §11)."""
        from .edit import dubbing
        aid, lang = params["asset_id"], params["language"]
        tr = app.transcripts.translation(aid, lang)
        if tr is None:
            raise ValueError(f"Traduisez d'abord la transcription en {lang.upper()}.")
        tr.language = lang
        a = app.library.get(aid)
        if a is None:
            raise ValueError("Média introuvable dans la bibliothèque.")
        engine = _tts(params.get("engine") or "elevenlabs")
        work = app.dubs.new_directory(aid, lang)          # chaque voix générée garde son dossier (historique)
        tmp = work.with_name(work.name + ".partial")
        import shutil
        shutil.rmtree(tmp, ignore_errors=True)
        man = dubbing.build(tr, engine, params.get("voices") or {}, params["default_voice"], aid,
                            float(a["duration_s"] or tr.segments[-1].end), tmp, ctx.progress,
                            is_cancelled=lambda: ctx.cancelled, speed=float(params.get("speed") or 1.0))
        ctx.check()
        tmp.rename(work)
        app.dubs.store(man, work, label=params.get("label") or "")
        errors = [ln.index for ln in man.lines if ln.error]
        fast = [ln.index for ln in man.lines if ln.tempo > 1.2]
        return {"asset_id": aid, "language": lang, "lines": len(man.lines), "errors": errors, "accelerated": fast}

    def eleven_dub_job(ctx: JobContext, params: dict) -> dict:
        """Doublage par ElevenLabs à partir du son de la vidéo (sa transcription, sa traduction, voix d'origine
        reproduite) : la piste rendue devient la voix doublée de la langue, à la place du son d'origine. Ses
        sous-titres (le texte traduit par ElevenLabs, minuté) deviennent la traduction de la vidéo dans cette langue.
        keep_voice=False : seulement la traduction (moteur de traduction « ElevenLabs »), la voix n'est pas gardée."""
        import shutil
        import subprocess
        import tempfile

        from .ai import elevenlabs
        from .media import ffmpeg
        from .edit import dubbing
        from .services import secrets
        from .transcribe import engines
        aid, lang = params["asset_id"], params["language"]
        a = app.library.get(aid)
        if a is None:
            raise ValueError("Média introuvable dans la bibliothèque.")

        def ff(*args: str) -> None:
            p = subprocess.run([ffmpeg.ffmpeg_bin(), "-hide_banner", "-loglevel", "error", "-nostdin", "-y", *args],
                               capture_output=True, text=True, creationflags=engines._NOWIN)
            if p.returncode != 0:
                raise RuntimeError(f"Conversion du son impossible : {p.stderr[-300:]}")
        work = app.dubs.new_directory(aid, lang)
        tmp = work.with_name(work.name + ".partial")
        shutil.rmtree(tmp, ignore_errors=True)
        tmp.mkdir(parents=True)
        with tempfile.TemporaryDirectory(prefix="sv-dub-", dir=str(app.paths.temp)) as d:
            wav = engines.extract_audio(Path(a["path"]), Path(d) / "son.wav")
            mp3 = Path(d) / "son.mp3"
            ff("-i", str(wav), "-b:a", "96k", str(mp3))
            key = secrets.get_secret("elevenlabs_api_key") or ""
            info: dict = {}
            try:
                data = elevenlabs.dub(key, mp3, lang, params.get("source") or None, ctx.progress,
                                      lambda: ctx.cancelled, info=info)
            except InterruptedError:
                ctx.check()
                raise
            got = Path(d) / "double.mp3"
            got.write_bytes(data)
            ff("-i", str(got), "-vn", "-ac", "1", "-ar", "48000", "-c:a", "pcm_s16le", str(tmp / "dub.wav"))
        subs, subs_error = 0, ""
        try:
            ctx.progress(0.96, "sous-titres du doublage")
            cues = elevenlabs.parse_srt(elevenlabs.dub_subtitles(key, info["dubbing_id"], lang))
            if cues:
                from .ai.translate import _spread
                from .transcribe.model import TSegment, Transcript
                segs = [TSegment(s, e, t, _spread(t.split(), s, e)) for s, e, t in cues]
                app.transcripts.store_translation(aid, Transcript(segs, lang, "traduction:elevenlabs"),
                                                  label="ElevenLabs")
                subs = len(segs)
            else:
                subs_error = "ElevenLabs n'a pas renvoyé de sous-titres"
        except (elevenlabs.ElevenLabsError, KeyError, ValueError) as exc:
            subs_error = str(exc).splitlines()[0][:200] if str(exc) else "sous-titres indisponibles"
        if not params.get("keep_voice", True):
            shutil.rmtree(tmp, ignore_errors=True)
            if not subs:
                raise ValueError("Traduction par ElevenLabs impossible : " + (subs_error or "pas de sous-titres"))
            return {"asset_id": aid, "language": lang, "subtitles": subs, "engines": ["elevenlabs"]}
        man = dubbing.DubManifest(aid, lang, "elevenlabs-dubbing", {}, float(a["duration_s"] or 0), [], "dub.wav")
        tmp.rename(work)
        app.dubs.store(man, work, label="ElevenLabs · doublage de la vidéo")
        return {"asset_id": aid, "language": lang, "lines": 0, "errors": [], "accelerated": [], "subtitles": subs,
                "subtitles_error": subs_error}

    def dub_line(ctx: JobContext, params: dict) -> dict:
        from .edit import dubbing
        aid, lang = params["asset_id"], params["language"]
        got = app.dubs.get(aid, lang)
        if got is None:
            raise ValueError("Doublage introuvable.")
        man, work = got
        engine = _tts(man.engine)
        ctx.progress(0.2, "Nouvelle prise de la phrase")
        dubbing.regenerate(man, int(params["index"]), params.get("text"), params.get("voice"), engine, work)
        app.dubs.store(man, work, history=False)          # même voix, une phrase reprise
        return {"asset_id": aid, "language": lang, "index": params["index"]}

    def separate_job(ctx: JobContext, params: dict) -> dict:
        """Séparation voix / musique d'un média, sur le PC (§9, §10.2 mode 3)."""
        import shutil

        from .edit import separate
        aid = params["asset_id"]
        a = app.library.get(aid)
        if a is None:
            raise ValueError("Média introuvable dans la bibliothèque.")
        if not a["audio_tracks"]:
            raise ValueError("Ce média ne contient pas de son.")
        work = app.stems.directory(aid)
        tmp = work.with_name(work.name + ".partial")
        shutil.rmtree(tmp, ignore_errors=True)
        key = params.get("model") or "kim_vocal_2"
        res = separate.separate_file(Path(a["path"]), tmp, app.paths.models, key, ctx.progress,
                                     lambda: ctx.cancelled)
        ctx.check()
        shutil.rmtree(work, ignore_errors=True)
        tmp.rename(work)
        app.stems.mark_done(aid, key)
        return {"asset_id": aid, "model": key, "duration": res["duration"]}

    def diarize_job(ctx: JobContext, params: dict) -> dict:
        """Qui parle ? Intervenants détectés à partir des voix (sur le PC)."""
        import tempfile

        from .ai import diarize
        from .transcribe import engines
        aid = params["asset_id"]
        a = app.library.get(aid)
        tr = app.transcripts.get(aid)
        if a is None or tr is None or not tr.segments:
            raise ValueError("Transcrivez d'abord ce média : les intervenants sont attribués phrase par phrase.")
        ctx.progress(0.02, "Extraction du son")
        with tempfile.TemporaryDirectory(dir=app.paths.temp) as td:
            wav = engines.extract_audio(Path(a["path"]), Path(td) / "a.wav")
            audio = engines.load_wav(wav)
        labels = diarize.diarize(audio, [(s.start, s.end) for s in tr.segments], app.paths.models,
                                 params.get("n_speakers") or None, float(params.get("threshold") or 0.5),
                                 ctx.progress, lambda: ctx.cancelled)
        ctx.check()
        diarize.apply_labels(tr, labels)
        app.transcripts.save_edit(aid, tr)
        synced = []
        for lang in app.transcripts.languages(aid):
            t2 = app.transcripts.translation(aid, lang)
            if t2 and diarize.copy_speakers(tr, t2):
                app.transcripts.save_translation_edit(aid, t2)
                synced.append(lang)
        n = len(set(labels))
        return {"asset_id": aid, "speakers": n, "segments": len(labels), "translations": synced}

    def _save_avatar(account_id: int, url: str) -> None:
        """Photo de profil du compte (si le réseau la donne) : copiée dans les données, pour les menus."""
        if not url:
            return
        import urllib.request
        dest = app.paths.root / "avatars" / f"account_{account_id}.jpg"
        try:
            dest.parent.mkdir(parents=True, exist_ok=True)
            with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "StudioVideo"}),
                                        timeout=20) as r:
                data = r.read(5 * 2**20)
            if len(data) < 100:
                return
            dest.write_bytes(data)
        except OSError:
            return                                # sans photo : le menu affiche l'initiale du compte
        with app.db.transaction() as c:
            c.execute("UPDATE social_account SET avatar_path = ? WHERE id = ?", (str(dest), account_id))

    def connect_account(ctx: JobContext, params: dict) -> dict:
        """Connexion d'un compte à sa plateforme (V3)."""
        import webbrowser

        from .publish import youtube
        from .services import secrets
        acc = app.concepts.get_account(params["account_id"])
        if acc.platform.value == "tiktok":
            from .publish import tiktok
            ck = app.settings.get("tiktok_client_key", "") or ""
            cs = secrets.get_secret("tiktok_client_secret") or ""
            ctx.progress(0.1, "Autorisation dans le navigateur…")
            tok = tiktok.connect(ck, cs, webbrowser.open, is_cancelled=lambda: ctx.cancelled)
            secrets.set_secret(f"tiktok_refresh_{acc.id}", tok["refresh_token"])
            info = tiktok.creator_info(tok["access_token"])
            user = info.get("creator_username") or ""
            with app.db.transaction() as c:
                c.execute("UPDATE social_account SET connection_status = 'connected', remote_id = ?, scopes = ?, "
                          "public_url = CASE WHEN public_url = '' AND ? != '' THEN ? ELSE public_url END WHERE id = ?",
                          (tok.get("open_id", ""), tok.get("scope", tiktok.SCOPES), user,
                           f"https://www.tiktok.com/@{user}", acc.id))
            app.db.audit("account.connect", f"account={acc.id} tiktok={tok.get('open_id', '')}")
            _save_avatar(acc.id, info.get("creator_avatar_url") or "")
            return {"account_id": acc.id, "channel": "@" + user if user else (info.get("creator_nickname") or "TikTok"),
                    "channel_id": tok.get("open_id", "")}
        if acc.platform.value in ("instagram", "facebook"):
            from .publish import meta
            aid_, sec = app.settings.get("meta_app_id", "") or "", secrets.get_secret("meta_app_secret") or ""
            ctx.progress(0.1, "Autorisation dans le navigateur…")
            res = meta.connect(aid_, sec, webbrowser.open, is_cancelled=lambda: ctx.cancelled)
            pg = meta.pick(res["pages"], acc.platform.value, acc.name)
            secrets.set_secret(f"meta_token_{acc.id}", pg["token"])
            remote = pg["ig_id"] if acc.platform.value == "instagram" else pg["id"]
            url = (f"https://www.instagram.com/{pg['ig_username']}/" if acc.platform.value == "instagram"
                   else f"https://www.facebook.com/{pg['id']}")
            with app.db.transaction() as c:
                c.execute("UPDATE social_account SET connection_status = 'connected', remote_id = ?, scopes = ?, "
                          "public_url = CASE WHEN public_url = '' THEN ? ELSE public_url END WHERE id = ?",
                          (remote, meta.SCOPES, url, acc.id))
            app.db.audit("account.connect", f"account={acc.id} {acc.platform.value}={remote}")
            who = ("@" + pg["ig_username"]) if acc.platform.value == "instagram" else pg["name"]
            return {"account_id": acc.id, "channel": who, "channel_id": remote}
        if acc.platform.value == "x":
            from .publish import xapi
            cid_, sec = app.settings.get("x_client_id", "") or "", secrets.get_secret("x_client_secret") or ""
            ctx.progress(0.1, "Autorisation dans le navigateur…")
            tok = xapi.connect(cid_, sec, webbrowser.open, is_cancelled=lambda: ctx.cancelled)
            secrets.set_secret(f"x_refresh_{acc.id}", tok["refresh_token"])
            user = xapi.me(tok["access_token"])
            with app.db.transaction() as c:
                c.execute("UPDATE social_account SET connection_status = 'connected', remote_id = ?, scopes = ?, "
                          "public_url = CASE WHEN public_url = '' THEN ? ELSE public_url END WHERE id = ?",
                          (user.get("id", ""), xapi.SCOPES, f"https://x.com/{user.get('username', '')}", acc.id))
            app.db.audit("account.connect", f"account={acc.id} x={user.get('id', '')}")
            return {"account_id": acc.id, "channel": "@" + user.get("username", ""), "channel_id": user.get("id", "")}
        if acc.platform.value != "youtube":
            raise ValueError(f"La connexion automatique {acc.platform.label} n'est pas encore disponible.")
        cid = app.settings.get("youtube_client_id", "") or ""
        csec = secrets.get_secret("youtube_client_secret") or ""
        ctx.progress(0.1, "Autorisation dans le navigateur…")
        tok = youtube.connect(cid, csec, webbrowser.open, is_cancelled=lambda: ctx.cancelled)
        ch = youtube.channel(tok["access_token"])
        secrets.set_secret(f"youtube_refresh_{acc.id}", tok["refresh_token"])
        with app.db.transaction() as c:
            c.execute("UPDATE social_account SET connection_status = 'connected', remote_id = ?, scopes = ? "
                      "WHERE id = ?", (ch["id"], tok.get("scope", youtube.SCOPES), acc.id))
        app.db.audit("account.connect", f"account={acc.id} youtube={ch['id']}")
        _save_avatar(acc.id, ch.get("thumbnail") or "")
        return {"account_id": acc.id, "channel": ch["title"], "channel_id": ch["id"]}

    def publish_upload(ctx: JobContext, params: dict) -> dict:
        """Mise en ligne automatique d'une publication. draft : envoyée sans être publiée (YouTube en privé,
        boîte de réception TikTok, brouillon de la Page Facebook ; Instagram n'en propose pas)."""
        from datetime import datetime

        from .domain.models import PublicationStatus
        from .publish import youtube
        from .services import secrets
        ps = app.publications
        r = ps.get(params["publication_id"])
        if r is None:
            raise ValueError("Publication introuvable.")
        if r["status"] in ("published", "cancelled", "scheduled_platform"):
            raise ValueError("Cette publication est déjà traitée (pas de doublon).")
        def fail(msg: str):
            ps.set_status(r["id"], PublicationStatus.FAILED, error=msg)
            raise ValueError(msg)
        if r["platform"] not in ("youtube", "tiktok", "instagram", "facebook", "x"):
            fail("Mise en ligne automatique non disponible pour cette plateforme.")
        acc = app.concepts.get_account(r["account_id"])
        draft = bool(params.get("draft"))
        if draft and r["platform"] not in ("youtube", "tiktok", "facebook"):
            fail(f"{acc.platform.label} ne permet pas d'envoyer un brouillon : publication à faire à la main.")
        if r["platform"] == "tiktok":
            return _publish_tiktok(ctx, r, acc, params, fail)
        if r["platform"] in ("instagram", "facebook"):
            return _publish_meta(ctx, r, acc, params, fail)
        if r["platform"] == "x":
            return _publish_x(ctx, r, acc, params, fail)
        refresh = secrets.get_secret(f"youtube_refresh_{acc.id}")
        if acc.connection_status != "connected" or not refresh:
            fail("Compte YouTube non connecté (Paramètres → Comptes).")
        if not r["output_path"] or not Path(r["output_path"]).exists():
            fail("La vidéo de cette publication est introuvable.")
        try:
            ps.set_status(r["id"], PublicationStatus.UPLOADING)
            token = youtube.access_token(app.settings.get("youtube_client_id", "") or "",
                                         secrets.get_secret("youtube_client_secret") or "", refresh)
            when = r["at"] if r["at"] and r["at"] > datetime.now().strftime("%Y-%m-%dT%H:%M") and not draft else None
            privacy = "private" if draft else (app.settings.get("youtube_privacy", "public") or "public")
            meta = youtube.metadata(r["title"] or "", r["description"] or "", r["hashtags"] or "", privacy, when)
            key = f"yt_session_{r['id']}"
            video = youtube.upload(Path(r["output_path"]), meta, token, ctx.progress, lambda: ctx.cancelled,
                                   session=app.settings.get(key, None),
                                   on_session=lambda url: app.settings.set(key, {"url": url}))
            app.settings.set(key, None)
        except Exception as exc:
            if not isinstance(exc, youtube.YouTubeError):
                ps.set_status(r["id"], PublicationStatus.FAILED, error=str(exc)[:300])
                raise
            if "invalid_grant" in str(exc) or "révoquée" in str(exc):
                with app.db.transaction() as c:
                    c.execute("UPDATE social_account SET connection_status = 'expired' WHERE id = ?", (acc.id,))
            ps.set_status(r["id"], PublicationStatus.FAILED, error=str(exc))
            raise
        vid = video["id"]
        url = f"https://youtu.be/{vid}"
        got = (video.get("status") or {}).get("privacyStatus", "")
        thumb_note = ""
        if r["thumbnail"] and Path(r["thumbnail"]).exists():
            try:
                youtube.set_thumbnail(token, vid, Path(r["thumbnail"]))
            except youtube.YouTubeError as exc:
                thumb_note = str(exc)
        if draft:
            ps.set_status(r["id"], PublicationStatus.MANUAL_ACTION, remote_id=vid, public_url=url,
                          error="Brouillon (vidéo privée) sur YouTube : rendez-la publique dans YouTube Studio.")
            state = "envoyée en brouillon (privée)"
        elif when:
            ps.set_status(r["id"], PublicationStatus.SCHEDULED_PLATFORM, remote_id=vid, public_url=url)
            state = "programmée sur YouTube"
        elif got and got != privacy:
            ps.set_status(r["id"], PublicationStatus.MANUAL_ACTION, remote_id=vid, public_url=url,
                          error=f"Envoyée mais restée « {got} » sur YouTube : vérifiez-la dans YouTube Studio "
                                "et rendez-la publique.")
            state = f"envoyée ({got})"
        else:
            ps.set_status(r["id"], PublicationStatus.PUBLISHED, remote_id=vid, public_url=url)
            state = "publiée"
        return {"publication_id": r["id"], "video_id": vid, "url": url, "state": state, "warning": thumb_note}

    def _publish_meta(ctx: JobContext, r, acc, params: dict, fail) -> dict:
        from datetime import datetime

        from .domain.models import PublicationStatus
        from .publish import meta, rules
        from .services import secrets
        ps = app.publications
        token = secrets.get_secret(f"meta_token_{acc.id}")
        if acc.connection_status != "connected" or not token or not acc_remote(acc.id):
            fail(f"Compte {acc.platform.label} non connecté (Paramètres → Comptes).")
        if r["at"] and r["at"] > datetime.now().strftime("%Y-%m-%dT%H:%M") and not params.get("now"):
            raise ValueError(f"{acc.platform.label} : la vidéo partira automatiquement à l'heure prévue si "
                             "Studio Vidéo est ouvert.")
        if not r["output_path"] or not Path(r["output_path"]).exists():
            fail("La vidéo de cette publication est introuvable.")
        text = rules.compose(r["platform"], r["title"] or "", r["description"] or "", r["hashtags"] or "")["text"]
        try:
            ps.set_status(r["id"], PublicationStatus.UPLOADING)
            if r["platform"] == "instagram":
                res = meta.instagram_reel(Path(r["output_path"]), text, acc_remote(acc.id), token, ctx.progress,
                                          lambda: ctx.cancelled, cover_ms=int((r["cover_s"] or 0) * 1000) or None)
            else:
                res = meta.facebook_reel(Path(r["output_path"]), text, acc_remote(acc.id), token, ctx.progress,
                                         draft=bool(params.get("draft")))
        except Exception as exc:
            if "révoquée" in str(exc):
                with app.db.transaction() as c:
                    c.execute("UPDATE social_account SET connection_status = 'expired' WHERE id = ?", (acc.id,))
            ps.set_status(r["id"], PublicationStatus.FAILED, error=str(exc)[:300])
            raise
        if params.get("draft"):
            ps.set_status(r["id"], PublicationStatus.MANUAL_ACTION, remote_id=res["id"],
                          error="Brouillon sur la Page Facebook : à publier depuis Meta Business Suite.")
            return {"publication_id": r["id"], "video_id": res["id"], "url": "", "state": "envoyée en brouillon"}
        ps.set_status(r["id"], PublicationStatus.PUBLISHED, remote_id=res["id"], public_url=res["url"])
        return {"publication_id": r["id"], "video_id": res["id"], "url": res["url"], "state": "publiée"}

    def _x_token(acc) -> str:
        from .publish import xapi
        from .services import secrets
        refresh_tok = secrets.get_secret(f"x_refresh_{acc.id}")
        if not refresh_tok:
            raise xapi.XError("Compte X non connecté (Paramètres → Comptes).")
        tok = xapi.refresh(app.settings.get("x_client_id", "") or "", secrets.get_secret("x_client_secret") or "",
                           refresh_tok)
        if tok.get("refresh_token") and tok["refresh_token"] != refresh_tok:
            secrets.set_secret(f"x_refresh_{acc.id}", tok["refresh_token"])
        return tok["access_token"]

    def _publish_x(ctx: JobContext, r, acc, params: dict, fail) -> dict:
        from datetime import datetime

        from .domain.models import PublicationStatus
        from .publish import rules, xapi
        from .services import secrets
        ps = app.publications
        if acc.connection_status != "connected" or not secrets.get_secret(f"x_refresh_{acc.id}"):
            fail("Compte X non connecté (Paramètres → Comptes).")
        if r["at"] and r["at"] > datetime.now().strftime("%Y-%m-%dT%H:%M") and not params.get("now"):
            raise ValueError("X : la vidéo partira automatiquement à l'heure prévue si Studio Vidéo est ouvert.")
        if not r["output_path"] or not Path(r["output_path"]).exists():
            fail("La vidéo de cette publication est introuvable.")
        text = rules.compose("x", r["title"] or "", r["description"] or "", r["hashtags"] or "")["text"]
        problems = rules.check("x", r["title"] or "", r["description"] or "", r["hashtags"] or "")
        if problems:
            fail(" ".join(problems))
        try:
            ps.set_status(r["id"], PublicationStatus.UPLOADING)
            res = xapi.post_video(Path(r["output_path"]), text, _x_token(acc), ctx.progress, lambda: ctx.cancelled)
        except Exception as exc:
            if "révoquée" in str(exc):
                with app.db.transaction() as c:
                    c.execute("UPDATE social_account SET connection_status = 'expired' WHERE id = ?", (acc.id,))
            ps.set_status(r["id"], PublicationStatus.FAILED, error=str(exc)[:300])
            raise
        url = f"{(acc.public_url or 'https://x.com/i').rstrip('/')}/status/{res['id']}"
        ps.set_status(r["id"], PublicationStatus.PUBLISHED, remote_id=res["id"], public_url=url)
        return {"publication_id": r["id"], "video_id": res["id"], "url": url, "state": "publiée"}

    def _x_stats() -> tuple[int, list[str]]:
        from .publish import xapi
        done, errors = 0, []
        rows = app.db.query("SELECT p.id, p.remote_id, p.public_url, p.social_account_id FROM publication p "
                            "JOIN social_account a ON a.id = p.social_account_id WHERE a.platform = 'x' "
                            "AND p.status = 'published'")
        import re as _re
        by_acc: dict = {}
        for r in rows:
            m = _re.search(r"/status/(\d+)", r["public_url"] or "") or _re.fullmatch(r"(\d{5,})", r["remote_id"] or "")
            if m:
                by_acc.setdefault(r["social_account_id"], []).append((r["id"], m.group(1)))
        for acc_id, items in by_acc.items():
            acc = app.concepts.get_account(acc_id)
            if acc.connection_status != "connected":
                continue
            try:
                stats = xapi.post_stats(_x_token(acc), [v for _, v in items])
            except xapi.XError as exc:
                errors.append(f"X : {exc}")
                continue
            for pid, tid in items:
                if stats.get(tid):
                    app.analytics.record(pid, stats[tid], "api")
                    done += 1
        return done, errors

    def acc_remote(acc_id: int) -> str:
        row = app.db.query_one("SELECT remote_id FROM social_account WHERE id = ?", (acc_id,))
        return (row["remote_id"] if row else "") or ""

    def _publish_tiktok(ctx: JobContext, r, acc, params: dict, fail) -> dict:
        from datetime import datetime

        from .domain.models import PublicationStatus
        from .publish import rules, tiktok
        from .services import secrets
        ps = app.publications
        refresh_tok = secrets.get_secret(f"tiktok_refresh_{acc.id}")
        if acc.connection_status != "connected" or not refresh_tok:
            fail("Compte TikTok non connecté (Paramètres → Comptes).")
        if r["at"] and r["at"] > datetime.now().strftime("%Y-%m-%dT%H:%M") and not params.get("now"):
            raise ValueError("TikTok ne permet pas la programmation par son API : la vidéo partira "
                             "automatiquement à l'heure prévue si Studio Vidéo est ouvert.")
        if not r["output_path"] or not Path(r["output_path"]).exists():
            fail("La vidéo de cette publication est introuvable.")
        desired = app.settings.get("tiktok_privacy", "PUBLIC_TO_EVERYONE") or "PUBLIC_TO_EVERYONE"
        try:
            ps.set_status(r["id"], PublicationStatus.UPLOADING)
            tok = tiktok.refresh(app.settings.get("tiktok_client_key", "") or "",
                                 secrets.get_secret("tiktok_client_secret") or "", refresh_tok)
            if tok.get("refresh_token") and tok["refresh_token"] != refresh_tok:
                secrets.set_secret(f"tiktok_refresh_{acc.id}", tok["refresh_token"])
            text = rules.compose("tiktok", r["title"] or "", r["description"] or "", r["hashtags"] or "")["text"]
            if params.get("draft"):
                res = tiktok.upload_draft(Path(r["output_path"]), tok["access_token"], ctx.progress,
                                          lambda: ctx.cancelled)
                ps.set_status(r["id"], PublicationStatus.MANUAL_ACTION, remote_id=res["publish_id"],
                              error="Envoyée dans la boîte de réception TikTok : ouvrez l'application pour ajouter "
                                    "la description et la publier.")
                return {"publication_id": r["id"], "video_id": res["publish_id"], "url": "",
                        "state": "envoyée en brouillon"}
            res = tiktok.upload(Path(r["output_path"]), text, desired, tok["access_token"], ctx.progress,
                                lambda: ctx.cancelled, cover_ms=int((r["cover_s"] or 0) * 1000) or None)
        except Exception as exc:
            if "révoquée" in str(exc):
                with app.db.transaction() as c:
                    c.execute("UPDATE social_account SET connection_status = 'expired' WHERE id = ?", (acc.id,))
            ps.set_status(r["id"], PublicationStatus.FAILED, error=str(exc)[:300])
            raise
        base = (acc.public_url or "https://www.tiktok.com").rstrip("/")
        if res["post_ids"] and res["privacy"] == desired:
            url = f"{base}/video/{res['post_ids'][0]}"
            ps.set_status(r["id"], PublicationStatus.PUBLISHED, remote_id=res["post_ids"][0], public_url=url)
            state = "publiée"
        else:
            why = ("publiée en privé : l'application TikTok n'est pas encore auditée" if res["privacy"] != desired
                   else "en cours de vérification par TikTok")
            ps.set_status(r["id"], PublicationStatus.MANUAL_ACTION, remote_id=res["publish_id"],
                          error=f"Envoyée sur TikTok, {why}. Vérifiez dans l'application TikTok.")
            url, state = "", f"envoyée ({res['privacy']})"
        return {"publication_id": r["id"], "video_id": res["publish_id"], "url": url, "state": state}

    def _instagram_stats() -> tuple[int, list[str]]:
        from .publish import meta
        from .services import secrets
        done, errors = 0, []
        rows = app.db.query("SELECT p.id, p.remote_id, p.social_account_id FROM publication p JOIN social_account a "
                            "ON a.id = p.social_account_id WHERE a.platform = 'instagram' AND p.status = 'published' "
                            "AND p.remote_id != '' AND p.remote_id NOT LIKE 'http%'")
        by_acc: dict = {}
        for r in rows:
            by_acc.setdefault(r["social_account_id"], []).append((r["id"], r["remote_id"]))
        for acc_id, items in by_acc.items():
            token = secrets.get_secret(f"meta_token_{acc_id}")
            if not token:
                continue
            try:
                stats = meta.instagram_stats([m for _, m in items], token)
            except meta.MetaError as exc:
                errors.append(f"Instagram : {exc}")
                continue
            for pid, mid in items:
                if stats.get(mid):
                    app.analytics.record(pid, stats[mid], "api")
                    done += 1
        return done, errors

    def _tiktok_stats() -> tuple[int, list[str]]:
        """Statistiques des vidéos TikTok publiques, compte par compte (chaque compte ne voit que les siennes)."""
        from .publish import tiktok
        from .services import secrets
        done, errors = 0, []
        for acc_id, items in app.analytics.tiktok_ids().items():
            acc = app.concepts.get_account(acc_id)
            refresh_tok = secrets.get_secret(f"tiktok_refresh_{acc_id}")
            if acc.connection_status != "connected" or not refresh_tok:
                continue                                 # sans API : page publique de la vidéo
            try:
                tok = tiktok.refresh(app.settings.get("tiktok_client_key", "") or "",
                                     secrets.get_secret("tiktok_client_secret") or "", refresh_tok)
                if tok.get("refresh_token") and tok["refresh_token"] != refresh_tok:
                    secrets.set_secret(f"tiktok_refresh_{acc_id}", tok["refresh_token"])
                stats = tiktok.video_stats(tok["access_token"], [v for _, v in items])
            except tiktok.TikTokError as exc:
                errors.append(f"{acc.display} : {exc}")
                continue
            for pid, vid in items:
                if vid in stats:
                    app.analytics.record(pid, stats[vid], "api")
                    done += 1
        return done, errors

    def _youtube_api_stats(ctx: JobContext) -> tuple[int, int, list[str], str]:
        """Comptes YouTube connectés par API (facultatif) : chiffres et rétention de YouTube Analytics."""
        from datetime import date, timedelta

        from .publish import youtube
        from .services import secrets
        items = app.analytics.youtube_ids()
        token, errors = None, []
        for acc in app.concepts.list_accounts():
            refresh = secrets.get_secret(f"youtube_refresh_{acc.id}")
            if acc.platform.value == "youtube" and acc.connection_status == "connected" and refresh:
                try:
                    token = youtube.access_token(app.settings.get("youtube_client_id", "") or "",
                                                 secrets.get_secret("youtube_client_secret") or "", refresh)
                    break
                except youtube.YouTubeError as exc:
                    errors.append(f"{acc.display} : {exc}")
        if not items or token is None:
            return 0, 0, errors, ""
        stats = youtube.video_stats(token, list(dict.fromkeys(v for _, v in items)))
        done, detailed, scope_note = 0, 0, ""
        end = date.today().isoformat()
        for pid, vid in items:
            ctx.check()
            if vid not in stats:
                continue                                 # vidéo d'une autre chaîne : lue sur sa page publique
            m = dict(stats[vid])
            if not scope_note:
                pub = app.db.query_one("SELECT last_attempt_at FROM publication WHERE id = ?", (pid,))
                start = ((pub["last_attempt_at"] or "")[:10] if pub else "") or \
                    (date.today() - timedelta(days=90)).isoformat()
                try:
                    start = (date.fromisoformat(start) - timedelta(days=1)).isoformat()
                    det = youtube.video_analytics(token, vid, start, end)
                    m.update(det)
                    detailed += bool(det)
                except youtube.ScopeMissing as exc:
                    scope_note = str(exc)
                except youtube.YouTubeError as exc:
                    if "403" not in str(exc):
                        errors.append(f"Détail {vid} : {exc}")
            app.analytics.record(pid, m, "api")
            done += 1
        return done, detailed, errors, scope_note

    def refresh_stats(ctx: JobContext, params: dict) -> dict:
        """Relevé des statistiques. Sans API : la page publique de chaque vidéo publiée est lue (yt-dlp), aux
        étapes du calendrier (`public_stats`) ; « all » (bouton Relever) : toute vidéo non relevée depuis une
        heure. Au plus 20 pages par passage, une pause de 5 à 20 s entre deux. Les comptes connectés par API
        (facultatif) passent d'abord par l'API de leur réseau."""
        import random
        from datetime import datetime as _dt, timedelta, timezone

        from .publish import browser, public_stats
        auto = not params.get("all")
        delay = params.get("delay", public_stats.DELAY)
        errors: list[str] = []
        api_done = 0
        ctx.progress(0.02, "Comptes connectés par API")    # facultatif : seulement les comptes connectés
        for fn in (_tiktok_stats, _instagram_stats, _x_stats):
            d, e = fn()
            api_done += d
            errors += e
        d, detailed, e, note = _youtube_api_stats(ctx)
        api_done += d
        errors += e
        now = _dt.now(timezone.utc).replace(tzinfo=None)
        recent = (now - timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
        todo = [t for t in app.analytics.public_targets()
                if (public_stats.due(t["published_at"], t["last_at"], now) if auto
                    else (t["last_at"] or "") < recent)]
        later = max(0, len(todo) - public_stats.LIMIT)
        todo = todo[:public_stats.LIMIT]
        exe = browser.find_browser()
        done = 0
        for k, t in enumerate(todo):
            ctx.check()
            if k and delay:
                ctx.cancel_event.wait(random.uniform(*delay))      # espacé : jamais en rafale
                ctx.check()
            ctx.progress(0.1 + 0.9 * k / len(todo), f"Page publique {k + 1}/{len(todo)}")
            cookies = public_stats.cookies_from(exe) if t["platform"] in public_stats.NEEDS_SESSION else None
            try:
                m = public_stats.read(t["url"], cookies)
            except Exception as exc:
                why = (str(exc).splitlines() or ["illisible"])[0][:160]
                if cookies and "cookie" in why.lower():
                    why = (f"ferme {browser.browser_name(exe)} pour relever les chiffres {t['platform'].capitalize()} "
                           "(ses cookies sont verrouillés tant qu'il est ouvert)")
                errors.append(f"{t['url']} : {why}")
                continue
            if m:
                app.analytics.record(t["id"], m, "public")
                done += 1
            else:
                errors.append(f"{t['url']} : aucun chiffre visible sur la page")
        res = {"updated": api_done + done, "public": done, "detailed": detailed, "errors": errors}
        if later:
            res["note"] = f"{later} vidéo(s) relevée(s) au prochain passage (au plus {public_stats.LIMIT} à la fois)."
        if note:
            res["note"] = (res.get("note", "") + " " + note).strip()
        if not todo and not api_done:
            res["note"] = res.get("note") or ("Rien à relever : aucune vidéo publiée avec son lien, ou toutes "
                                              "relevées il y a moins d'une heure.")
        return res

    def detect_text(ctx: JobContext, params: dict) -> dict:
        """Zones de texte incrusté / logo proposées pour un média (à vérifier par l'utilisateur)."""
        from .edit import textdetect
        a = app.library.get(params["asset_id"])
        if a is None or not Path(a["path"]).exists():
            raise ValueError("Média introuvable.")
        if a["kind"] != "video":
            raise ValueError("Disponible pour les vidéos uniquement.")
        ctx.progress(0.2, "Analyse des images")
        end = params.get("end") or a["duration_s"] or 10
        zones = textdetect.detect_file(Path(a["path"]), float(params.get("start") or 0), float(end),
                                       a["width"] or 16, a["height"] or 9)
        return {"asset_id": a["id"], "zones": zones}

    def write_script(ctx: JobContext, params: dict) -> dict:
        """Script en scènes par le modèle de langage configuré (Création originale)."""
        from .ai.highlights import LLMEngine
        from .edit import original
        from .services import secrets
        llm = app.settings.get("llm", None) or {}
        if llm.get("kind") not in ("ollama", "openai") or not llm.get("model"):
            raise ValueError("Aucun modèle de langage configuré (Paramètres → Matériel et moteurs).")
        eng = LLMEngine(llm["kind"], llm["model"], llm.get("base_url", ""),
                        (secrets.get_secret("llm_api_key") or "") if llm["kind"] == "openai" else "")
        ctx.progress(0.2, "Écriture du script")
        scenes = original.script_from_llm(eng, params.get("idea", ""), params.get("language", "fr"),
                                          int(params.get("seconds") or 45), params.get("source", ""))
        return {"scenes": [s.to_dict() for s in scenes]}

    def create_original(ctx: JobContext, params: dict) -> dict:
        """Fabrique la vidéo d'une création originale et l'importe dans le projet."""
        import shutil

        from .edit import original
        pid = params["project_id"]
        scenes = [original.Scene(**{k: v for k, v in s.items() if k in original.Scene.__dataclass_fields__})
                  for s in params["scenes"]]
        tts = None
        if params.get("tts"):
            tts = _tts(params["tts"])

        def visuals(v: str):
            if v and v.startswith("asset:"):
                a = app.library.get(int(v[6:]))
                if a and Path(a["path"]).exists() and a["kind"] in ("image", "video"):
                    return {"kind": a["kind"], "path": a["path"]}
            return None
        out_dir = app.paths.projects / f"p{pid}"
        n = len(list(out_dir.glob("creation_*.mp4"))) + 1 if out_dir.exists() else 1
        dest = out_dir / f"creation_{n:02d}.mp4"
        work = app.paths.temp / f"original_{ctx.job_id}"
        try:
            res = original.build(scenes, int(params["width"]), int(params["height"]), float(params.get("fps") or 30),
                                 dest, work, tts, params.get("voice", ""), params.get("language", "fr"), visuals,
                                 ctx.progress, lambda: ctx.cancelled)
        finally:
            shutil.rmtree(work, ignore_errors=True)
        ctx.progress(0.95, "Import dans le projet")
        imp = app.importer().import_file(dest, project_id=pid, allow_duplicate=True, provenance="création originale")
        if res["transcript"].segments:
            app.transcripts.store_original(imp.asset_id, res["transcript"])
        data = app.projects.load_data(pid)
        data["original_script"] = params["scenes"]
        data["original_asset"] = imp.asset_id
        app.projects.save_data(pid, data)
        return {"asset_id": imp.asset_id, "duration": res["duration"], "texts": res["texts"], "path": res["path"]}

    def write_copy(ctx: JobContext, params: dict) -> dict:
        """Titre, description et hashtags proposés à partir de la transcription de la vidéo publiée."""
        import json as _json

        from .edit.model import Timeline
        from .publish import copywriter
        from .services import secrets
        row = app.db.query_one("SELECT e.settings_json, p.concept_id, c.name concept FROM video_variant v "
                               "JOIN clip cl ON cl.id = v.clip_id JOIN project p ON p.id = cl.project_id "
                               "JOIN concept c ON c.id = p.concept_id LEFT JOIN export_job e ON e.variant_id = v.id "
                               "WHERE v.id = ?", (params["variant_id"],))
        if row is None:
            raise ValueError("Vidéo introuvable.")
        st = _json.loads(row["settings_json"] or "{}")
        texts, lang = [], params.get("language") or "fr"
        tl = Timeline.from_dict(st["timeline"]) if st.get("timeline") else None
        if tl:
            for sg in tl.segments:
                tr = app.transcripts.get(sg.asset_id)
                if tr:
                    lang = tr.language or lang
                    texts += [s.text for s in tr.segments if s.end > sg.src_in and s.start < sg.src_out]
        text = " ".join(texts).strip()
        if not text:
            raise ValueError("Pas de transcription pour cette vidéo : transcrivez le média dans le Studio d'abord.")
        sents = copywriter.sentences_on_output(tl, app.transcripts.get) if tl else []
        total = tl.duration if tl else 0.0
        llm = app.settings.get("llm", None) or {}
        if llm.get("kind") in ("ollama", "openai") and llm.get("model") and params.get("use_llm", True):
            from .ai.highlights import LLMEngine
            eng = LLMEngine(llm["kind"], llm["model"], llm.get("base_url", ""),
                            (secrets.get_secret("llm_api_key") or "") if llm["kind"] == "openai" else "")
            ctx.progress(0.3, "Rédaction par le modèle de langage")
            out = copywriter.with_llm(eng, text, row["concept"], lang, params.get("platforms"))
            out["engine"] = "llm"
            try:
                ctx.progress(0.7, "Chapitres")
                chs = copywriter.chapters_llm(eng, sents, total, lang)
            except ValueError:
                chs = copywriter.chapters_heuristic(sents, total)
        else:
            out = copywriter.heuristic(text, row["concept"])
            out["engine"] = "local"
            chs = copywriter.chapters_heuristic(sents, total)
        out["chapters"] = copywriter.chapters_text(chs)
        return out

    def export_project_job(ctx: JobContext, params: dict) -> dict:
        from .services import archive
        return archive.export_project(app, params["project_id"], Path(params["dest"]), params.get("media", True),
                                      ctx.progress)

    def import_project_job(ctx: JobContext, params: dict) -> dict:
        from .services import archive
        return archive.import_project(app, Path(params["path"]), Path(params["media_dir"]), None, ctx.progress)

    def find_silences(ctx: JobContext, params: dict) -> dict:
        """Blancs de chaque extrait : d'après les mots transcrits si possible, sinon d'après le son."""
        from .edit import silence
        min_len = float(params.get("min_len") or 0.6)
        out, used = {}, {"transcription": 0, "son": 0}
        segs = params["segments"]
        for k, sg in enumerate(segs):
            ctx.check()
            a = app.library.get(sg["asset_id"])
            if a is None or not a["audio_tracks"]:
                continue
            tr = app.transcripts.get(sg["asset_id"])
            if tr is not None and any(s.words for s in tr.segments):
                out[sg["id"]] = silence.from_transcript(tr, sg["src_in"], sg["src_out"], min_len)
                used["transcription"] += 1
            elif Path(a["path"]).exists():
                out[sg["id"]] = silence.detect(Path(a["path"]), sg["src_in"], sg["src_out"], -35.0, min_len)
                used["son"] += 1
            ctx.progress((k + 1) / len(segs), f"Extrait {k + 1}/{len(segs)}")
        return {"silences": out, "used": used}

    app.jobs.register("find_silences", find_silences)
    app.jobs.register("export_project", export_project_job)
    app.jobs.register("import_project", import_project_job)
    app.jobs.register("write_copy", write_copy)
    app.jobs.register("write_script", write_script)
    app.jobs.register("create_original", create_original)
    def reframe_job(ctx: JobContext, params: dict) -> dict:
        """Recadrage intelligent : trajectoire du cadre qui suit les visages d'un extrait."""
        from .edit import reframe
        a = app.library.get(params["asset_id"])
        if a is None or not Path(a["path"]).exists():
            raise ValueError("Média introuvable.")
        if a["kind"] != "video":
            raise ValueError("Disponible pour les vidéos uniquement.")
        ctx.progress(0.05, "Détecteur de visages")
        res = reframe.analyze(Path(a["path"]), float(params["src_in"]), float(params["src_out"]), app.paths.models,
                              ctx.progress, lambda: ctx.cancelled)
        return {"segment_id": params.get("segment_id"), **res}

    app.jobs.register("reframe", reframe_job)
    app.jobs.register("detect_text", detect_text)
    def match_published(ctx: JobContext, params: dict) -> dict:
        """Atelier de publication, niveau 3 : retrouve sur la page publique de chaque compte le lien des vidéos
        publiées à la main. Pages lues avec yt-dlp, peu souvent, avec des pauses au hasard entre deux pages."""
        import random
        from datetime import datetime, timezone

        from .media import ffmpeg
        from .publish import reconcile, rules
        delay = params.get("delay", (5.0, 20.0))

        def pause() -> None:
            if delay:
                ctx.cancel_event.wait(random.uniform(*delay))
                ctx.check()

        by_account: dict[int, list] = {}
        for r in app.publications.awaiting_link(params.get("account_id")):
            by_account.setdefault(r["account_id"], []).append(r)
        confirmed, proposed, errors, skipped = [], 0, [], []
        first = True
        for k, (aid, rows) in enumerate(by_account.items()):
            acc = app.concepts.get_account(aid)
            urls = reconcile.listing_urls(acc.platform.value, acc.public_url)
            if not urls:
                why = ("adresse publique du compte manquante" if acc.platform.value in reconcile.SUPPORTED
                       else "liste publique non lisible sans session")
                skipped.append(f"{acc.display} : {why}")
                app.concepts.update_account_settings(aid, reconcile={"at": _utc_now(), "error": why})
                continue
            ctx.progress(k / max(1, len(by_account)), f"Recherche des liens · {acc.display}")
            entries, error = [], ""
            try:
                for u in urls:
                    if not first:
                        pause()
                    first = False
                    entries += reconcile.list_recent(u)
                # YouTube ne donne pas toujours la légende, la durée et la date dans la liste
                for i, e in enumerate([e for e in entries if e["duration"] is None or not e["description"]][:5]):
                    pause()
                    try:
                        e.update({k2: v for k2, v in reconcile.details(e["url"]).items()
                                  if v and k2 not in ("url", "id")})
                    except Exception as exc:          # une vidéo illisible n'arrête pas les autres
                        log.info("Détails illisibles %s : %s", e["url"], exc)
            except Exception as exc:
                error = str(exc).strip().splitlines()[-1][:300] if str(exc).strip() else type(exc).__name__
                errors.append(f"{acc.display} : {error}")
            pubs = []
            for r in rows:
                dur = None
                if r["output_path"] and Path(r["output_path"]).exists():
                    try:
                        dur = ffmpeg.probe(r["output_path"]).duration_s
                    except Exception:
                        dur = None
                since = None
                if r["since"]:
                    since = datetime.strptime(r["since"], "%Y-%m-%dT%H:%M:%SZ").replace(
                        tzinfo=timezone.utc).timestamp()
                c = rules.compose(r["platform"], r["title"] or "", r["description"] or "", r["hashtags"] or "")
                pubs.append({"id": r["id"], "title": c["title"], "text": c["text"], "duration": dur, "since": since})
            for m in reconcile.match(pubs, entries, app.publications.linked_urls(aid)) if entries else []:
                if m["sure"]:
                    try:
                        app.publications.confirm_published(m["publication_id"], m["url"])
                        confirmed.append({"publication_id": m["publication_id"], "url": m["url"],
                                          "account": acc.display})
                    except Exception as exc:
                        errors.append(f"{acc.display} : {exc}")
                elif app.publications.propose_match(m["publication_id"], m["url"], m["title"], m["score"]):
                    proposed += 1
            app.concepts.update_account_settings(aid, reconcile={"at": _utc_now(), "error": error,
                                                                 "videos": len(entries)})
        return {"confirmed": confirmed, "proposed": proposed, "errors": errors, "skipped": skipped}

    app.jobs.register("refresh_stats", refresh_stats)
    app.jobs.register("match_published", match_published)
    app.jobs.register("connect_account", connect_account)
    app.jobs.register("publish_upload", publish_upload)
    app.jobs.register("diarize", diarize_job)
    app.jobs.register("separate", separate_job)
    app.jobs.register("dub", dub)
    app.jobs.register("eleven_dub", eleven_dub_job)
    app.jobs.register("dub_line", dub_line)
    app.jobs.register("translate", translate)
    app.jobs.register("optimize_audio", optimize_audio)
    app.jobs.register("find_highlights", find_highlights)
    app.jobs.register("auto_clips", auto_clips)
    app.jobs.register("translate_video", translate_video)
    app.jobs.register("transcribe", transcribe)
    app.jobs.register("download_model", download_model)
    app.jobs.register("import_file", import_file)
    app.jobs.register("import_url", import_url)
    app.jobs.register("export_timeline", export_timeline)
    app.jobs.register("preview_frame", preview)
    app.jobs.register("export_still", export_still)
    app.jobs.register("analyze_audio", analyze)
