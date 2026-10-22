"""Textes de publication proposés : sans modèle (mots-clés) et avec modèle (JSON), job sur une vraie exportation."""
import time

import pytest

from studiovideo.publish import copywriter

TEXT = ("Aujourd'hui je teste la nouvelle boisson énergisante. La boisson a un goût de citron. "
        "Franchement, cette boisson citronnée me surprend. Le citron domine vraiment tout le reste !")


def test_heuristic():
    out = copywriter.heuristic(TEXT, "Poussey Pass")
    assert out["title"].startswith("Aujourd'hui je teste") and len(out["title"]) <= 95
    tags = out["hashtags"].split()
    assert tags[0] == "#PousseyPass" and "#Boisson" in tags and "#Citron" in tags
    assert all(t.startswith("#") for t in tags)


def test_with_llm():
    class Fake:
        def _ask(self, prompt):
            assert "N'invente aucun fait" in prompt and "TikTok" in prompt
            return '{"title": "« Le test du citron »", "description": "Je goûte.", "hashtags": ["citron", "#test"]}'
    out = copywriter.with_llm(Fake(), TEXT, "PP", "fr", ["TikTok"])
    assert out == {"title": "Le test du citron", "description": "Je goûte.", "hashtags": "#citron #test"}

    class Bad:
        def _ask(self, prompt):
            return "non"
    with pytest.raises(ValueError):
        copywriter.with_llm(Bad(), TEXT)


def test_write_copy_job(tmp_path):
    from studiovideo.core import build_context
    from studiovideo.edit.model import Timeline
    from studiovideo.transcribe.model import TSegment, Transcript
    app = build_context(tmp_path / "d")
    try:
        c = app.concepts.create_concept("Poussey Pass")
        pid = app.projects.create(c.id, "P")
        con = app.db.connect()
        con.execute("INSERT INTO source_asset(workspace_id, project_id, kind, path, file_name, fingerprint, "
                    "audio_tracks, duration_s) VALUES (1,?,'video','x','x.mp4','fw',1,60)", (pid,))
        aid = con.execute("SELECT last_insert_rowid()").fetchone()[0]
        app.transcripts.store_original(aid, Transcript([TSegment(0, 5, "Hors de l'extrait."),
                                                        TSegment(10, 20, TEXT)], "fr"))
        tl = Timeline()
        tl.add_segment(aid, 8, 25)
        vid = app.montage.record_export(pid, tl, {"path": str(tmp_path / "o.mp4"), "duration": 17}, None)
        jid = app.jobs.submit("write_copy", "Texte", {"variant_id": vid})
        end = time.time() + 20
        while app.jobs.get(jid).status in ("pending", "running") and time.time() < end:
            time.sleep(0.05)
        j = app.jobs.get(jid)
        assert j.status == "succeeded", j.error
        assert j.result["engine"] == "local" and "Hors de" not in j.result["description"]
        assert "#Citron" in j.result["hashtags"]
    finally:
        app.close()
