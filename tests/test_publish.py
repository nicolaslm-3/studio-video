"""V3 : règles de texte par plateforme, calendrier, rappels, publication assistée confirmée par lien."""
import pytest

from studiovideo.domain.models import Platform, PublicationStatus
from studiovideo.publish import rules
from studiovideo.services.concepts import ConceptService
from studiovideo.services.publications import PublicationError, PublicationService


def test_hashtags_and_compose():
    assert rules.normalize_hashtags("humour, sketch #fr #FR  #") == "#humour #sketch #fr"
    yt = rules.compose("youtube", "Le titre", "Texte", "humour")
    assert yt == {"title": "Le titre", "text": "Texte\n\n#humour"}
    tk = rules.compose("tiktok", "Le titre", "Texte", "humour")
    assert tk["title"] == "" and tk["text"] == "Le titre\n\nTexte\n\n#humour"


def test_checks():
    assert rules.check("youtube", "", "x", "") == ["YouTube : titre obligatoire."]
    assert "trop long" in rules.check("youtube", "a" * 101, "", "")[0]
    assert "maximum 30" in " ".join(rules.check("instagram", "t", "", " ".join(f"t{i}" for i in range(31))))
    # X : un lien compte pour 23 caractères
    assert rules.check("x", "", "a" * 250 + " https://exemple.fr/" + "b" * 200, "") == []
    assert rules.check("x", "", "a" * 281, "")
    warn = rules.check("youtube", "t", "", "", {"duration": 200, "aspect": "9:16", "exists": True})
    assert "Short" in warn[0]
    assert rules.check("tiktok", "t", "", "", {"aspect": "16:9"})
    assert rules.check("tiktok", "t", "", "", {"exists": False}) == ["Le fichier vidéo est introuvable."]


def test_url_matches():
    assert rules.url_matches("youtube", "https://youtu.be/abc")
    assert rules.url_matches("tiktok", "https://www.tiktok.com/@moi/video/1")
    assert not rules.url_matches("tiktok", "https://www.youtube.com/watch?v=1")
    assert not rules.url_matches("x", "javascript:alert(1)")


@pytest.fixture
def pubs(db):
    cs, ps = ConceptService(db), PublicationService(db)
    c = cs.create_concept("Poussey Pass")
    yt, tk = cs.create_account(Platform.YOUTUBE, "PP"), cs.create_account(Platform.TIKTOK, "PP")
    cs.link(c.id, yt.id)
    cs.link(c.id, tk.id)
    m = ps.create_master(c.id, title="Sketch", description="Desc", hashtags="#humour")
    ids = ps.set_destinations(m, [yt.id, tk.id])
    return ps, c, m, ids


def test_calendar_schedule_due_and_confirm(pubs):
    ps, c, m, (p_yt, p_tk) = pubs
    assert {r["id"] for r in ps.undated()} == {p_yt, p_tk}
    ps.schedule(p_yt, "2026-11-03T18:00")
    ps.schedule(p_tk, "2026-11-04T12:30")
    ps.override(p_tk, title="Version TikTok")
    rows = ps.calendar("2026-11-01T00:00", "2026-12-01T00:00", c.id)
    assert [r["id"] for r in rows] == [p_yt, p_tk]
    assert rows[1]["title"] == "Version TikTok" and rows[0]["title"] == "Sketch"
    assert rows[0]["status"] == "scheduled_local"
    assert [r["id"] for r in ps.due("2026-11-03T18:00")] == [p_yt]
    ps.mark_manual(p_yt, "Rappel")
    assert ps.get(p_yt)["status"] == "manual_action" and ps.due("2026-11-05T00:00")[0]["id"] == p_tk
    with pytest.raises(PublicationError, match="plateforme"):
        ps.confirm_published(p_yt, "https://www.tiktok.com/@pp/video/1")
    ps.confirm_published(p_yt, "https://youtu.be/xyz")
    r = ps.get(p_yt)
    assert r["status"] == PublicationStatus.PUBLISHED.value and r["public_url"] == "https://youtu.be/xyz"
    ps.schedule(p_yt, "2027-01-01T00:00")                 # une publication faite ne se reprogramme pas
    assert ps.get(p_yt)["status"] == "published"
    with pytest.raises(PublicationError):
        ps.delete_master(m)
    ps.schedule(p_tk, None)
    assert ps.get(p_tk)["status"] == "draft"
