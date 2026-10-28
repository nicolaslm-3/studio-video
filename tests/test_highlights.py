"""Détection des moments forts : analyse locale, protection du contexte, moteur LLM (serveur simulé)."""
import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from studiovideo.ai.highlights import (LLMEngine, LLMError, LocalEngine, Request, check_llm, context_flags,
                                       ranges_from_sentences)
from studiovideo.transcribe.model import TSegment, Transcript

LINES = [
    ("Alice", "Bonjour à tous et bienvenue dans ce débat sur la réforme des retraites."),
    ("Alice", "Monsieur le ministre, pourquoi repousser l'âge de départ ?"),
    ("Bob", "Parce que le système est en déficit et qu'il faut le sauver, c'est-à-dire équilibrer le budget."),
    ("Alice", "Non mais attendez, c'est faux, les chiffres du rapport disent l'inverse !"),
    ("Bob", "Je ne suis pas d'accord, laissez-moi finir s'il vous plaît."),
    ("Alice", "Je vous arrête, 60 pour cent des Français sont contre."),
    ("Bob", "Mais ce n'est pas une raison pour ne rien faire."),
    ("Alice", "Bon, parlons d'autre chose, la météo de demain."),
    ("Bob", "Il fera beau, enfin je crois."),
    ("Alice", "Haha, vous êtes drôle, c'est une blague ?"),
    ("Bob", "Mdr, non, c'est absurde mais c'est vrai !"),
    ("Alice", "Par exemple, comment expliquer simplement la retraite par répartition ?"),
    ("Bob", "En fait, les actifs paient pour les retraités, c'est pour ça qu'il faut un équilibre."),
    ("Bob", "Retenez une chose : sans cotisations, pas de pensions."),
]


def debate(repeat=4, sentence=6.0, gap=0.4):
    segs, t = [], 0.0
    for r in range(repeat):
        for spk, txt in LINES:
            segs.append(TSegment(t, t + sentence, txt, [], spk))
            t += sentence + gap
    return Transcript(segs, "fr", "test")


def test_local_engine_respects_request():
    tr = debate()
    req = Request(count=6, min_duration=15, max_duration=40, target_duration=25)
    cands = LocalEngine().propose(tr, req)
    assert 1 <= len(cands) <= 6
    for c in cands:
        assert 15 <= c.duration <= 40
        assert 5 <= c.score <= 100 and c.rationale and c.hook and c.category
        assert c.start == tr.segments[c.first].start and c.end == tr.segments[c.last].end  # bornes de phrases
    srt = sorted(cands, key=lambda c: c.start)
    assert all(srt[i].end + req.min_gap <= srt[i + 1].start for i in range(len(srt) - 1))   # sans chevauchement
    assert max(c.score for c in cands) == 100


def test_categories_and_keywords_steer_results():
    tr = debate()
    pol = LocalEngine().propose(tr, Request(count=3, min_duration=10, max_duration=30, categories=["politique"]))
    assert all(c.category == "politique" for c in pol)
    assert any("ministre" in c.rationale or "réforme" in c.rationale for c in pol)
    assert all(3 <= len(c.subject.replace("·", " ").split()) <= 8 for c in pol)      # titre de 3 à 8 mots
    hum = LocalEngine().propose(tr, Request(count=2, min_duration=10, max_duration=25, categories=["rire"]))
    assert any("drôle" in c.rationale or "blague" in c.rationale or "mdr" in c.rationale.lower() for c in hum)
    deb = LocalEngine().propose(tr, Request(count=2, min_duration=10, max_duration=30, categories=["polarisant"]))
    assert any("contradiction" in c.rationale for c in deb)
    kw = LocalEngine().propose(tr, Request(count=5, min_duration=10, max_duration=30, keywords=["météo"]))
    assert kw and all("météo" in " ".join(s.text for s in tr.segments[c.first:c.last + 1]) for c in kw)


def test_speaker_filters():
    tr = debate()
    only = LocalEngine().propose(tr, Request(count=5, min_duration=5, max_duration=7, speakers_include=["Bob"]))
    assert only and all("Bob" in c.speakers for c in only)


def test_context_protection_flags():
    tr = debate(repeat=1)
    # extrait qui s'arrête juste avant « Mais ce n'est pas une raison » (réserve)
    flags = context_flags(tr, 4, 5)
    assert any("réserve" in f for f in flags)
    assert any("chiffres" in f for f in flags)               # « 60 pour cent »
    # réponse de Bob isolée de la question d'Alice
    flags2 = context_flags(tr, 2, 2)
    assert any("question" in f for f in flags2)
    # timecodes modifiés au milieu d'une phrase
    flags3 = context_flags(tr, 7, 8, start=tr.segments[7].start + 2.0)
    assert any("milieu" in f for f in flags3)


def test_energy_profile_and_intensity(tmp_path):
    import subprocess

    from studiovideo.ai.highlights import energy_profile
    from studiovideo.media import ffmpeg
    if not ffmpeg.available():
        pytest.skip("FFmpeg absent")
    wav = tmp_path / "e.wav"
    subprocess.run([ffmpeg.ffmpeg_bin(), "-loglevel", "error", "-y", "-f", "lavfi", "-i",
                    "sine=f=200:d=4,volume='if(lt(t,2),0.05,0.8)':eval=frame", "-ac", "1", "-ar", "16000",
                    "-c:a", "pcm_s16le", str(wav)], check=True)
    prof = energy_profile(wav, 0.5)
    assert len(prof) == 8 and prof[6] - prof[1] > 15


def test_text_based_ranges():
    tr = debate(repeat=1)
    keep = [0, 1, 2, 5, 6, 9]
    r = ranges_from_sentences(tr, keep)
    assert len(r) == 3                       # 0-2 fusionnées, 5-6 fusionnées, 9 seule
    assert r[0][0] == pytest.approx(0.0) and r[0][1] == pytest.approx(tr.segments[2].end + 0.08)


# -- moteur LLM avec un faux serveur Ollama ----------------------------------------------------
class FakeOllama(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(json.dumps({"models": [{"name": "llama3.1:8b"}]}).encode())

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        prompt = body["messages"][0]["content"]
        assert "Transcription" in prompt and body["model"] == "llama3.1:8b"
        clips = [{"start": 13.0, "end": 39.5, "subject": "Retraites", "category": "polarisant", "score": 92,
                  "rationale": "Échange vif sur les chiffres", "hook": "C'est faux, les chiffres disent l'inverse !"},
                 {"start": 2000, "end": 2030, "subject": "hors bornes"},          # hors transcription
                 {"start": 1.0, "end": 3.0, "subject": "trop court"}]
        self.send_response(200)
        self.end_headers()
        self.wfile.write(json.dumps({"message": {"content": json.dumps({"clips": clips})}}).encode())


@pytest.fixture
def ollama():
    srv = HTTPServer(("127.0.0.1", 0), FakeOllama)
    th = threading.Thread(target=srv.serve_forever, daemon=True)
    th.start()
    yield f"http://127.0.0.1:{srv.server_port}"
    srv.shutdown()


def test_llm_engine_snaps_and_validates(ollama):
    tr = debate(repeat=1)
    assert check_llm("ollama", ollama) == ["llama3.1:8b"]
    eng = LLMEngine("ollama", "llama3.1:8b", ollama)
    cands = eng.propose(tr, Request(count=5, min_duration=15, max_duration=45))
    assert len(cands) == 1
    c = cands[0]
    assert c.category == "polarisant" and c.hook.startswith("C'est faux")
    assert c.start == tr.segments[c.first].start and c.end == tr.segments[c.last].end
    assert c.context_flags  # contrôles appliqués aussi aux propositions du modèle


def test_llm_unreachable():
    with pytest.raises(LLMError):
        check_llm("ollama", "http://127.0.0.1:9")


def test_laughter_and_shouting_from_sound():
    """Rire et Émotion s'appuient aussi sur le son : éclats hors parole, cris, micro qui sature."""
    from studiovideo.ai.highlights import AudioProfile, summary
    segs, t = [], 0.0
    for k in range(12):
        segs.append(TSegment(t, t + 4.0, f"On parle du sujet numéro {k} tranquillement ici.", [], "A"))
        t += 6.0                                     # 2 s sans parole entre chaque phrase
    tr = Transcript(segs, "fr", "test")
    n = int(t / 0.5) + 2
    rms, clip, bright, burst = [-30.0] * n, [0.0] * n, [1000.0] * n, [1.0] * n
    for k in range(int(36 / 0.5), int(48 / 0.5)):   # 36-48 s : éclats saccadés et saturés entre les phrases
        if k * 0.5 % 6 >= 4:
            rms[k], clip[k], burst[k] = -12.0, 0.01, 4.0
    for k in range(int(6 / 0.5), int(18 / 0.5)):    # 6-18 s : la voix monte et crie
        rms[k], bright[k] = -14.0, 2200.0
    eng = LocalEngine(audio=AudioProfile(0.5, rms, clip, bright, burst))
    laugh = eng.propose(tr, Request(count=1, min_duration=8, max_duration=14, categories=["rire"]))[0]
    assert 30 <= laugh.start <= 42 and "hors parole" in laugh.rationale
    shout = eng.propose(tr, Request(count=1, min_duration=8, max_duration=14, categories=["emotion"]))[0]
    assert shout.start <= 12 and "monte" in shout.rationale
    assert summary(["Bah honnêtement je pense que le rap français est mort, c'est ridicule !"]).startswith(
        "honnêtement je pense que le rap")
