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
from .services.campaigns import CampaignService
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
    campaigns: CampaignService | None = None

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
    ctx.campaigns = CampaignService(db, ctx.projects, ctx.transcripts)
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
        energy = []
        if a["audio_tracks"]:
            ctx.progress(0.02, "Analyse de l'énergie de la voix")
            with tempfile.TemporaryDirectory(dir=str(app.paths.temp)) as d:
                wav = engines.extract_audio(Path(a["path"]), Path(d) / "a.wav")
                ctx.check()
                energy = hl.energy_profile(wav, 0.5)
        eng_cfg = params.get("engine") or {"kind": "local"}
        if eng_cfg.get("kind") in ("ollama", "openai"):
            from .services import secrets
            key = secrets.get_secret("llm_api_key") or "" if eng_cfg["kind"] == "openai" else ""
            engine = hl.LLMEngine(eng_cfg["kind"], eng_cfg.get("model", ""), eng_cfg.get("base_url", ""), key)
        else:
            engine = hl.LocalEngine(energy, 0.5)
        cands = engine.propose(tr, req, ctx.progress)
        ctx.check()
        ids = app.candidates.replace_proposals(pid, aid, cands, req)
        return {"count": len(ids), "asset_id": aid, "engine": getattr(engine, "name", "local")}

    def optimize_audio(ctx: JobContext, params: dict) -> dict:
        from .edit import audiofix
        from .edit.model import Timeline
        tl = Timeline.from_dict(params["timeline"])
        sources, music = app.montage.sources_for(tl)
        p = audiofix.optimize(tl, sources, music, ctx.progress)
        return {"audio": p.audio, "music": p.music, "seg_volumes": p.seg_volumes, "report": p.report,
                "warnings": p.warnings}

    def translate(ctx: JobContext, params: dict) -> dict:
        from .ai import translate as tr_mod
        from .services import secrets
        aid, dst = params["asset_id"], params["language"]
        src_tr = app.transcripts.get(aid)
        if src_tr is None:
            raise ValueError("Transcrivez d'abord ce média.")
        cfg = dict(params.get("engine") or {})
        cfg["llm"] = app.settings.get("llm", None) or {}
        translator = tr_mod.make_translator(cfg, secrets.get_secret)
        out = tr_mod.translate_transcript(src_tr, translator, dst, params.get("glossary") or [], ctx.progress,
                                          is_cancelled=lambda: ctx.cancelled)
        ctx.check()
        app.transcripts.store_translation(aid, out)
        return {"asset_id": aid, "language": dst, "segments": len(out.segments)}

    def _tts(kind: str):
        from .ai import tts
        from .services import secrets
        return tts.make_engine(kind, secrets.get_secret, app.paths.models)

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
        work = app.dubs.directory(aid, lang)
        tmp = work.with_name(work.name + ".partial")
        import shutil
        shutil.rmtree(tmp, ignore_errors=True)
        man = dubbing.build(tr, engine, params.get("voices") or {}, params["default_voice"], aid,
                            float(a["duration_s"] or tr.segments[-1].end), tmp, ctx.progress,
                            is_cancelled=lambda: ctx.cancelled, speed=float(params.get("speed") or 1.0))
        ctx.check()
        shutil.rmtree(work, ignore_errors=True)
        tmp.rename(work)
        app.dubs.store(man, work)
        errors = [ln.index for ln in man.lines if ln.error]
        fast = [ln.index for ln in man.lines if ln.tempo > 1.2]
        return {"asset_id": aid, "language": lang, "lines": len(man.lines), "errors": errors, "accelerated": fast}

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
        app.dubs.store(man, work)
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

    def connect_account(ctx: JobContext, params: dict) -> dict:
        """Connexion d'un compte à sa plateforme (V3). YouTube uniquement pour l'instant."""
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
            return {"account_id": acc.id, "channel": "@" + user if user else (info.get("creator_nickname") or "TikTok"),
                    "channel_id": tok.get("open_id", "")}
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
        return {"account_id": acc.id, "channel": ch["title"], "channel_id": ch["id"]}

    def publish_upload(ctx: JobContext, params: dict) -> dict:
        """Mise en ligne automatique d'une publication (V3, YouTube)."""
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
        if r["platform"] not in ("youtube", "tiktok"):
            fail("Mise en ligne automatique disponible pour YouTube et TikTok uniquement.")
        acc = app.concepts.get_account(r["account_id"])
        if r["platform"] == "tiktok":
            return _publish_tiktok(ctx, r, acc, params, fail)
        refresh = secrets.get_secret(f"youtube_refresh_{acc.id}")
        if acc.connection_status != "connected" or not refresh:
            fail("Compte YouTube non connecté (Paramètres → Comptes).")
        if not r["output_path"] or not Path(r["output_path"]).exists():
            fail("La vidéo de cette publication est introuvable.")
        try:
            ps.set_status(r["id"], PublicationStatus.UPLOADING)
            token = youtube.access_token(app.settings.get("youtube_client_id", "") or "",
                                         secrets.get_secret("youtube_client_secret") or "", refresh)
            when = r["at"] if r["at"] and r["at"] > datetime.now().strftime("%Y-%m-%dT%H:%M") else None
            privacy = (app.settings.get("youtube_privacy", "public") or "public")
            meta = youtube.metadata(r["title"] or "", r["description"] or "", r["hashtags"] or "", privacy, when)
            video = youtube.upload(Path(r["output_path"]), meta, token, ctx.progress, lambda: ctx.cancelled)
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
        if when:
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
        return {"publication_id": r["id"], "video_id": vid, "url": url, "state": state}

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
            res = tiktok.upload(Path(r["output_path"]), text, desired, tok["access_token"], ctx.progress,
                                lambda: ctx.cancelled)
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

    def _tiktok_stats() -> tuple[int, list[str]]:
        """Statistiques des vidéos TikTok publiques, compte par compte (chaque compte ne voit que les siennes)."""
        from .publish import tiktok
        from .services import secrets
        done, errors = 0, []
        for acc_id, items in app.analytics.tiktok_ids().items():
            acc = app.concepts.get_account(acc_id)
            refresh_tok = secrets.get_secret(f"tiktok_refresh_{acc_id}")
            if acc.connection_status != "connected" or not refresh_tok:
                errors.append(f"{acc.display} : compte non connecté (chiffres à saisir à la main)")
                continue
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

    def refresh_stats(ctx: JobContext, params: dict) -> dict:
        """Relevé des statistiques publiques des vidéos YouTube (V4), via un compte YouTube connecté."""
        from .publish import youtube
        from .services import secrets
        tk_done, tk_errors = _tiktok_stats()
        items = app.analytics.youtube_ids()
        if not items:
            if tk_done or tk_errors:
                return {"updated": tk_done, "errors": tk_errors}
            return {"updated": 0, "errors": [], "note": "Aucune vidéo YouTube ou TikTok publiée à relever."}
        token, errors = None, list(tk_errors)
        for acc in app.concepts.list_accounts():
            refresh = secrets.get_secret(f"youtube_refresh_{acc.id}")
            if acc.platform.value == "youtube" and acc.connection_status == "connected" and refresh:
                try:
                    token = youtube.access_token(app.settings.get("youtube_client_id", "") or "",
                                                 secrets.get_secret("youtube_client_secret") or "", refresh)
                    break
                except youtube.YouTubeError as exc:
                    errors.append(f"{acc.display} : {exc}")
        if token is None:
            if tk_done:
                return {"updated": tk_done, "errors": errors + ["YouTube : aucun compte connecté."]}
            raise ValueError("Aucun compte YouTube connecté : connectez-en un (Paramètres → Comptes) pour relever "
                             "les statistiques." + (" " + "; ".join(errors) if errors else ""))
        ctx.progress(0.3, "Lecture des statistiques YouTube")
        stats = youtube.video_stats(token, list(dict.fromkeys(v for _, v in items)))
        from datetime import date, timedelta
        done, detailed, scope_note = 0, 0, ""
        end = date.today().isoformat()
        for k, (pid, vid) in enumerate(items):
            ctx.check()
            if vid not in stats:
                errors.append(f"Vidéo {vid} introuvable (supprimée ou privée).")
                continue
            m = dict(stats[vid])
            if not scope_note:
                pub = app.db.query_one("SELECT last_attempt_at FROM publication WHERE id = ?", (pid,))
                start = ((pub["last_attempt_at"] or "")[:10] if pub else "") or (date.today() - timedelta(days=90)).isoformat()
                try:
                    start = (date.fromisoformat(start) - timedelta(days=1)).isoformat()
                    det = youtube.video_analytics(token, vid, start, end)
                    m.update(det)
                    detailed += bool(det)
                except youtube.ScopeMissing as exc:
                    scope_note = str(exc)
                except youtube.YouTubeError as exc:
                    if "403" not in str(exc):          # vidéo d'une autre chaîne : pas de détail, c'est normal
                        errors.append(f"Détail {vid} : {exc}")
            app.analytics.record(pid, m, "api")
            done += 1
            ctx.progress(0.3 + 0.7 * (k + 1) / len(items), f"{done} vidéo(s) relevée(s)")
        res = {"updated": done + tk_done, "detailed": detailed, "errors": errors}
        if scope_note:
            res["note"] = scope_note
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
    app.jobs.register("refresh_stats", refresh_stats)
    app.jobs.register("connect_account", connect_account)
    app.jobs.register("publish_upload", publish_upload)
    app.jobs.register("diarize", diarize_job)
    app.jobs.register("separate", separate_job)
    app.jobs.register("dub", dub)
    app.jobs.register("dub_line", dub_line)
    app.jobs.register("translate", translate)
    app.jobs.register("optimize_audio", optimize_audio)
    app.jobs.register("find_highlights", find_highlights)
    app.jobs.register("transcribe", transcribe)
    app.jobs.register("download_model", download_model)
    app.jobs.register("import_file", import_file)
    app.jobs.register("import_url", import_url)
    app.jobs.register("export_timeline", export_timeline)
    app.jobs.register("preview_frame", preview)
    app.jobs.register("analyze_audio", analyze)
