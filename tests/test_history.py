"""Historique des transcriptions, traductions et voix : chaque génération est gardée et appliquée, on revient à une
ancienne ; une nouvelle voix ne se bute plus sur la précédente (fichier encore ouvert) ; heures à l'heure du PC ;
tâches décrites en clair."""
import json

from studiovideo.edit.dubbing import DubManifest
from studiovideo.transcribe.model import TSegment, Transcript


def _ctx(tmp_path):
    from studiovideo.core import build_context
    app = build_context(tmp_path / "d")
    con = app.db.connect()
    con.execute("INSERT INTO source_asset(workspace_id, kind, path, file_name, fingerprint, audio_tracks, duration_s) "
                "VALUES (1,'video','x.mp4','x.mp4','f',1,10)")
    return app, con.execute("SELECT last_insert_rowid()").fetchone()[0]


def test_transcript_and_translation_history(tmp_path):
    app, aid = _ctx(tmp_path)
    try:
        t = app.transcripts
        t.store_original(aid, Transcript([TSegment(0, 1, "Premier", [])], "fr", "faster-whisper:small"))
        tr = t.get(aid)
        tr.edit_segment(0, "Premier corrigé")
        t.save_edit(aid, tr)
        t.store_original(aid, Transcript([TSegment(0, 1, "Second", [])], "fr", "elevenlabs:scribe_v2"))
        rows = t.history.list(aid, "transcript")
        assert [json.loads(r["data_json"])["segments"][0]["text"] for r in rows] == ["Second", "Premier corrigé"]
        assert [r["active"] for r in rows] == [1, 0] and t.get(aid).segments[0].text == "Second"
        assert t.apply_version(rows[1]["id"]) == ""                     # retour à l'ancienne, corrections comprises
        assert t.get(aid).segments[0].text == "Premier corrigé" and t.get(aid).engine == "faster-whisper:small"
        assert [r["active"] for r in t.history.list(aid, "transcript")] == [0, 1]
        t.store_translation(aid, Transcript([TSegment(0, 1, "First", [])], "en", "traduction:google_free"), "Google")
        t.store_translation(aid, Transcript([TSegment(0, 1, "Initial", [])], "en", "traduction:deepl"), "DeepL")
        hist = t.history.list(aid, "translation", "en")
        assert [r["label"] for r in hist] == ["DeepL", "Google"] and t.translation(aid, "en").segments[0].text == "Initial"
        assert t.apply_version(hist[1]["id"]) == "en" and t.translation(aid, "en").segments[0].text == "First"
    finally:
        app.close()


def test_dub_history_new_folder_each_time(tmp_path):
    app, aid = _ctx(tmp_path)
    try:
        dirs = []
        for k in range(2):
            d = app.dubs.new_directory(aid, "en")
            assert not d.exists() and d not in dirs
            d.mkdir(parents=True)
            (d / "dub.wav").write_bytes(b"x")
            app.dubs.store(DubManifest(aid, "en", "piper", {}, 10.0, [], "dub.wav"), d, label=f"Piper · v{k}")
            dirs.append(d)
        assert app.dubs.get(aid, "en")[1] == dirs[1] and dirs[0].exists()      # l'ancienne voix est gardée
        hist = app.dubs.history.list(aid, "dub", "en")
        assert [r["label"] for r in hist] == ["Piper · v1", "Piper · v0"]
        assert app.dubs.apply_version(hist[1]["id"]) == "en" and app.dubs.track(aid, "en") == dirs[0] / "dub.wav"
    finally:
        app.close()


def test_local_time_and_job_description(tmp_path):
    from datetime import datetime, timezone

    from studiovideo.jobs.manager import JobInfo
    from studiovideo.ui.jobs_panel import STATUS_EMOJI, describe
    from studiovideo.ui.widgets import local_time
    utc = datetime(2026, 10, 28, 9, 5, tzinfo=timezone.utc)
    assert local_time("2026-10-28T09:05:00Z") == utc.astimezone().strftime("%d/%m/%Y %H:%M")    # heure du PC
    assert STATUS_EMOJI["succeeded"] == "✅" and STATUS_EMOJI["failed"] == "❌"
    app, aid = _ctx(tmp_path)
    try:
        pid = app.projects.quick_create(app.concepts, "translation", "Manif")
        app.db.connect().execute("UPDATE source_asset SET project_id = ? WHERE id = ?", (pid, aid))

        def job(kind, params, status="succeeded", message="", result=None):
            return JobInfo(1, kind, "Étiquette", status, 0.5, message, "", False, "", result or {}, params)
        assert describe(app, job("transcribe", {"asset_id": aid, "engine": "elevenlabs"})) == \
            "Manif · Transcription · ElevenLabs Scribe v2"
        assert describe(app, job("transcribe", {"asset_id": aid, "engine": "local", "model": "small"})) == \
            "Manif · Transcription · Whisper small"
        assert describe(app, job("translate_video", {"project_id": pid, "languages": ["en"]}, "running",
                                 "EN · voix · Synthèse 3/40")) == "Manif · Génération de la voix (anglais)"
        assert describe(app, job("download_model", {"model": "piper:fr_FR-siwis-medium"})) == \
            "Téléchargement de la voix · fr_FR-siwis-medium"
    finally:
        app.close()
