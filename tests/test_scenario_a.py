"""Scénario A du cahier des charges (§26), de bout en bout, avec de vrais exports.

Vidéo longue → transcription (simulée : le moteur Whisper est testé à part dans l'intégration continue)
→ analyse → 10 propositions → 5 sélectionnées → timecodes modifiés → sous-titres → traitement audio
→ export des 5 clips.
"""
import subprocess
import time

import pytest

from studiovideo.core import build_context
from studiovideo.edit.model import Timeline
from studiovideo.edit.render import export
from studiovideo.edit.subtitles import SubtitleTrack, generate
from studiovideo.media import ffmpeg
from studiovideo.transcribe.model import TSegment, Transcript, Word

pytestmark = pytest.mark.skipif(not ffmpeg.available(), reason="FFmpeg absent")

SENTENCES = [
    ("Alice", "Bienvenue dans ce débat sur la réforme des retraites, merci d'être là."),
    ("Bob", "Merci, c'est un sujet qui concerne tous les Français."),
    ("Alice", "Pourquoi le gouvernement veut-il repousser l'âge de départ ?"),
    ("Bob", "Parce que le système est en déficit, c'est-à-dire qu'il faut équilibrer le budget."),
    ("Alice", "Non mais attendez, c'est faux, les chiffres disent l'inverse !"),
    ("Bob", "Je ne suis pas d'accord, laissez-moi finir s'il vous plaît."),
    ("Alice", "Haha, vous êtes drôle, c'est une blague ?"),
    ("Bob", "Par exemple, la répartition fonctionne ainsi : les actifs paient pour les retraités."),
    ("Alice", "Personne ne vous dit la vérité sur ce sujet, et c'est le problème."),
    ("Bob", "Retenez une chose : sans cotisations, il n'y a pas de pensions."),
]


def wait(app, jid, timeout=120):
    end = time.time() + timeout
    while time.time() < end:
        j = app.jobs.get(jid)
        if j.status in ("succeeded", "failed", "cancelled"):
            return j
        time.sleep(0.05)
    raise TimeoutError


def test_scenario_a(tmp_path):
    long_dur = 400.0      # « vidéo longue » réduite pour la durée du test (même chaîne que pour 2 h)
    src = tmp_path / "debat.mp4"
    subprocess.run([ffmpeg.ffmpeg_bin(), "-loglevel", "error", "-y", "-f", "lavfi", "-i",
                    f"testsrc2=size=320x180:rate=10:duration={long_dur}", "-f", "lavfi", "-i",
                    f"sine=f=220:d={long_dur}", "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
                    "-c:a", "aac", "-shortest", str(src)], check=True)
    app = build_context(tmp_path / "data")
    try:
        c = app.concepts.create_concept("Poussey Pass")
        pid = app.projects.create(c.id, "Débat", "smart_clipping")
        aid = app.importer().import_file(src, project_id=pid).asset_id

        # 1. transcription (mots horodatés)
        segs, t, k = [], 0.5, 0
        while t < long_dur - 8:
            spk, txt = SENTENCES[k % len(SENTENCES)]
            words, wt = [], t
            for w in txt.split():
                words.append(Word(wt, wt + 0.32, " " + w))
                wt += 0.36
            segs.append(TSegment(t, wt, txt, words, spk))
            t = wt + 0.6
            k += 1
        app.transcripts.store_original(aid, Transcript(segs, "fr", "test"))

        # 2. analyse → 10 propositions
        jid = app.jobs.submit("find_highlights", "Moments forts", {
            "asset_id": aid, "project_id": pid, "engine": {"kind": "local"},
            "request": {"count": 10, "min_duration": 15, "max_duration": 35, "target_duration": 25}})
        j = wait(app, jid)
        assert j.status == "succeeded", j.error
        props = app.candidates.list(pid, aid)
        assert len(props) == 10
        assert all(15 <= p["end_s"] - p["start_s"] <= 35 for p in props)

        # 3. sélection de 5 clips, 4. modification de leurs débuts et fins
        chosen = props[:5]
        for p in chosen:
            app.candidates.set_state(p["id"], "accepted")
        app.candidates.set_times(chosen[0]["id"], chosen[0]["start_s"] + 1.0, chosen[0]["end_s"] - 1.0, [])
        clip_ids = app.candidates.to_clips([p["id"] for p in chosen])
        assert len(clip_ids) == 5
        clips = [app.db.query_one("SELECT * FROM clip WHERE id = ?", (i,)) for i in clip_ids]
        assert clips[0]["start_s"] == pytest.approx(chosen[0]["start_s"] + 1.0)

        # 5-7. sous-titres, traitement audio, export des 5 clips
        tr = app.transcripts.get(aid)
        outputs = []
        for cl in clips:
            tl = Timeline()
            tl.set_aspect("9:16", 360)
            tl.format.fps = 25
            tl.export.preset = "ultrafast"
            tl.add_segment(aid, cl["start_s"], cl["end_s"])
            tl.audio.highpass = True
            tl.audio.normalize = True
            tl.subtitles = SubtitleTrack(animation="highlight")
            tl.subtitles.cues = generate(tl, {aid: tr})
            assert tl.subtitles.cues
            sources, music = app.montage.sources_for(tl)

            def run(cmd, on_line):
                p = subprocess.run(cmd, capture_output=True, text=True)
                for line in p.stderr.splitlines(keepends=True):
                    on_line(line)
                if p.returncode != 0:
                    raise RuntimeError(p.stderr[-800:])
            rep = export(tl, sources, music, tmp_path / f"clip_{cl['id']}.mp4", run)
            assert abs(rep["duration"] - tl.duration) < 0.3
            assert abs(rep["lufs"] - tl.audio.target_lufs) < 1.5 and rep["true_peak"] <= -0.4
            outputs.append(rep["path"])
        assert len(set(outputs)) == 5
    finally:
        app.close()
