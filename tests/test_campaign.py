"""Campagne : brief, conformité des vidéos (format, durée, sous-titres, mots) et des textes de publication."""
from studiovideo.edit import campaign as cp
from studiovideo.edit.model import Timeline
from studiovideo.edit.subtitles import Cue
from studiovideo.transcribe.model import TSegment, Transcript


def brief():
    return cp.Brief(name="Lancement", aspects=["9:16"], min_s=10, max_s=60, require_subtitles=True,
                    required_words=["Boisson Éclair"], forbidden_words=["concurrent"], required_hashtags=["eclair"],
                    required_mentions=["@marque"], disclosure="#pub")


def test_contains_ignores_accents_and_case():
    assert cp.contains("La BOISSON eclair est là", "Boisson Éclair")
    assert not cp.contains("Boissons éclairées", "boisson éclair")
    assert cp.contains("merci @Marque !", "@marque") and cp.contains("#Pub #eclair", "#pub")
    assert cp.split_list("a, b;c\nd") == ["a", "b", "c", "d"]


def test_video_checks():
    b = brief()
    good = {"aspect": "9:16", "duration": 30, "subtitles": True, "spoken": "goûtez la boisson éclair",
            "shown": "", "transcribed": True}
    assert all(c.ok for c in cp.check_video(b, good)) and cp.summary(cp.check_video(b, good)) == "✓ Conforme"
    bad = dict(good, aspect="16:9", duration=75, subtitles=False, spoken="le concurrent est moins bon")
    res = {c.rule: c.ok for c in cp.check_video(b, bad)}
    assert res == {"Format": False, "Durée": False, "Sous-titres": False, "Mot requis « Boisson Éclair »": False,
                   "Mot interdit « concurrent »": False}
    unknown = dict(good, spoken="", transcribed=False)
    assert "à vérifier" in cp.summary(cp.check_video(b, unknown))      # jamais « conforme » sans preuve


def test_text_checks():
    b = brief()
    ok = cp.check_text(b, "Nouveau goût ! @marque #eclair #pub")
    assert all(c.ok for c in ok)
    ko = {c.rule: c.ok for c in cp.check_text(b, "Nouveau goût, mieux que le concurrent #eclair")}
    assert ko["Mention @marque"] is False and ko["Mention de partenariat"] is False
    assert ko["Mot interdit « concurrent »"] is False


def test_video_info_from_timeline():
    tl = Timeline()
    tl.add_segment(1, 10, 20)
    tl.subtitles.enabled = True
    tl.subtitles.cues = [Cue(0, 2, "Boisson éclair")]
    tr = Transcript([TSegment(2, 5, "avant l'extrait"), TSegment(12, 15, "dans l'extrait")], "fr")
    info = cp.video_info(tl.to_dict(), {"duration": 10.0}, {1: tr})
    assert info["spoken"] == "dans l'extrait" and info["subtitles"] and info["duration"] == 10.0
    assert "Boisson" in info["shown"] and info["transcribed"]
