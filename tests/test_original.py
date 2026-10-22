"""Création originale : sources (texte, Word, PDF), script, fabrication réelle avec et sans voix."""
import time
import zipfile

import pytest

from studiovideo.edit import original as o
from studiovideo.media import ffmpeg

pytestmark = pytest.mark.skipif(not ffmpeg.available(), reason="FFmpeg absent")


def _pdf(path, text):
    """PDF minimal écrit à la main (une page, Helvetica)."""
    stream = f"BT /F1 18 Tf 72 720 Td ({text}) Tj ET".encode()
    objs = [b"<< /Type /Catalog /Pages 2 0 R >>", b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R "
            b"/Resources << /Font << /F1 5 0 R >> >> >>",
            b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n" + stream + b"\nendstream",
            b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>"]
    out, offs = b"%PDF-1.4\n", []
    for i, ob in enumerate(objs, 1):
        offs.append(len(out))
        out += f"{i} 0 obj\n".encode() + ob + b"\nendobj\n"
    xref = len(out)
    out += f"xref\n0 {len(objs) + 1}\n0000000000 65535 f \n".encode()
    out += b"".join(f"{x:010d} 00000 n \n".encode() for x in offs)
    out += f"trailer\n<< /Size {len(objs) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF".encode()
    path.write_bytes(out)
    return path


def test_read_sources(tmp_path):
    (tmp_path / "a.txt").write_text("Bonjour le monde.", encoding="utf-8")
    assert o.read_source(tmp_path / "a.txt") == "Bonjour le monde."
    assert "Le cafe vient d'Ethiopie" in o.read_source(_pdf(tmp_path / "a.pdf", "Le cafe vient d'Ethiopie."))
    with zipfile.ZipFile(tmp_path / "a.docx", "w") as z:
        z.writestr("word/document.xml", "<w:document><w:body><w:p><w:r><w:t>Premier</w:t></w:r></w:p>"
                                        "<w:p><w:r><w:t>Second</w:t></w:r></w:p></w:body></w:document>")
    assert o.read_source(tmp_path / "a.docx").split() == ["Premier", "Second"]
    with pytest.raises(o.OriginalError):
        o.read_source(tmp_path / "a.xyz")


def test_script_from_text_and_llm():
    sc = o.script_from_text("Un. Deux trois quatre. " * 6, max_words=8)
    assert len(sc) >= 3 and all(len(s.narration.split()) <= 8 for s in sc)

    class Fake:
        def _ask(self, prompt):
            assert "Sujet : Le café" in prompt
            return 'Voici : {"scenes": [{"narration": "Saviez-vous que…", "text": "Le café"}, {"narration": "Fin."}]}'
    out = o.script_from_llm(Fake(), "Le café", "fr", 30)
    assert [s.text for s in out] == ["Le café", ""]

    class Bad:
        def _ask(self, prompt):
            return "désolé"
    with pytest.raises(o.OriginalError):
        o.script_from_llm(Bad(), "x")


def test_build_with_fake_voice(tmp_path, monkeypatch):
    import threading
    from http.server import HTTPServer

    from studiovideo.ai.tts import ElevenLabsTTS
    from test_dubbing import Fake
    srv = HTTPServer(("127.0.0.1", 0), Fake)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        eng = ElevenLabsTTS("good", base_url=f"http://127.0.0.1:{srv.server_port}")
        scenes = [o.Scene("Première scène courte.", "Titre"), o.Scene("Deuxième scène, un peu plus longue.", "",
                                                                     "#224466")]
        r = o.build(scenes, 360, 640, 25, tmp_path / "out.mp4", tmp_path / "w", eng, "v_alice", "fr")
        info = ffmpeg.probe(tmp_path / "out.mp4")
        assert (info.width, info.height) == (360, 640) and info.audio_tracks == 1
        assert abs(info.duration_s - r["duration"]) < 0.2
        s0, s1 = r["transcript"].segments
        assert s0.start == 0.25 and s1.start > s0.end                  # calé sur les voix successives
        assert r["texts"] == [(0.0, r["texts"][0][1], "Titre")]
        assert s0.words[0].text.strip() == "Première"
    finally:
        srv.shutdown()


def test_create_original_job(tmp_path):
    from studiovideo.core import build_context
    from studiovideo.domain.models import ProjectMode
    app = build_context(tmp_path / "d")
    try:
        c = app.concepts.create_concept("C")
        pid = app.projects.create(c.id, "Création", ProjectMode.ORIGINAL)
        jid = app.jobs.submit("create_original", "Création", {
            "project_id": pid, "width": 360, "height": 640, "language": "fr", "tts": None,
            "scenes": [{"narration": "Le café vient d'Éthiopie.", "text": "Le café", "visual": "", "duration": 0},
                       {"narration": "Deux milliards de tasses par jour.", "text": "", "visual": "",
                        "duration": 3}]})
        end = time.time() + 60
        while app.jobs.get(jid).status in ("pending", "running") and time.time() < end:
            time.sleep(0.05)
        j = app.jobs.get(jid)
        assert j.status == "succeeded", j.error
        aid = j.result["asset_id"]
        a = app.library.get(aid)
        assert a["project_id"] == pid and a["kind"] == "video"
        assert app.transcripts.get(aid).segments[1].text == "Deux milliards de tasses par jour."
        assert app.projects.load_data(pid)["original_asset"] == aid
    finally:
        app.close()
