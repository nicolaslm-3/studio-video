"""Calendrier et concepts partagés entre deux ordinateurs par un dossier synchronisé (services/shared_sync.py)."""
import json

import pytest

from studiovideo.db.database import open_database
from studiovideo.domain.models import Platform
from studiovideo.paths import app_paths
from studiovideo.services.concepts import ConceptService
from studiovideo.services.projects import ProjectService
from studiovideo.services.publications import PublicationService
from studiovideo.services.shared_sync import SharedSync, SyncError


class PC:
    def __init__(self, root, shared):
        p = app_paths(root)
        self.db = open_database(p.database, p.backups)
        self.concepts, self.pubs = ConceptService(self.db), PublicationService(self.db)
        self.projects = ProjectService(self.db)
        self.shared = shared

    def sync(self) -> dict:
        return SharedSync(self.db, self.concepts, self.pubs, self.shared).run()

    def concept(self, name):
        return next(c for c in self.concepts.list_concepts(include_archived=True) if c.name == name)

    def plans(self):
        return self.pubs.planned("2000-01-01T00:00", "9999-12-31T23:59")


@pytest.fixture
def pcs(tmp_path):
    shared = tmp_path / "OneDrive" / "Studio"
    shared.mkdir(parents=True)
    a, b = PC(tmp_path / "a", shared), PC(tmp_path / "b", shared)
    yield a, b
    a.db.close()
    b.db.close()


def _channel(pc, concept, platform, url):
    acc = pc.concepts.create_account(platform, concept.name, url)
    pc.concepts.link(concept.id, acc.id)
    return acc


def test_concepts_and_channels_travel(pcs):
    a, b = pcs
    c = a.concepts.create_concept("Mas.tair", theme="Humour", color="#FF0000", language="en")
    _channel(a, c, Platform.YOUTUBE, "https://www.youtube.com/@mastair")
    _channel(a, c, Platform.INSTAGRAM, "https://www.instagram.com/mastair")
    assert a.sync()["sent"] == 1
    assert b.sync()["received"] == 1
    got = b.concept("Mas.tair")
    assert (got.theme, got.color, got.language) == ("Humour", "#FF0000", "en")
    assert {(x.platform.value, x.public_url) for x, _d in b.concepts.concept_accounts(got.id)} == {
        ("youtube", "https://www.youtube.com/@mastair"), ("instagram", "https://www.instagram.com/mastair")}
    # changé sur B (thème, chaîne Meta passée sur Facebook) : A suit
    b.concepts.update_concept(got.id, theme="Sketchs")
    ig = next(x for x, _d in b.concepts.concept_accounts(got.id) if x.platform == Platform.INSTAGRAM)
    b.concepts.unlink(got.id, ig.id)
    _channel(b, got, Platform.FACEBOOK, "https://www.facebook.com/mastair")
    assert b.sync()["sent"] == 1
    assert a.sync()["received"] == 1
    assert a.concept("Mas.tair").theme == "Sketchs"
    assert {x.platform.value for x, _d in a.concepts.concept_accounts(c.id)} == {"youtube", "facebook"}
    # plus rien à faire de part et d'autre : pas d'aller-retour sans fin
    assert a.sync() == b.sync() == {"sent": 0, "received": 0, "deleted": 0, "errors": []}


def test_same_name_on_both_pcs_becomes_one_concept(pcs):
    a, b = pcs
    a.concepts.create_concept("Pokémon", theme="A")
    b.concepts.create_concept("pokémon", theme="B")
    a.sync()
    b.sync()                                   # B rejoint : le dossier fait foi, pas de doublon
    a.sync()
    assert [c.name for c in b.concepts.user_concepts()] == ["Pokémon"]
    assert b.concept("Pokémon").theme == "A"
    assert len(list((a.shared / "concepts").glob("*.json"))) == 1


def test_calendar_travels_with_video_name(pcs):
    a, b = pcs
    c = a.concepts.create_concept("Mas.tair")
    pid = a.projects.create(c.id, "Interview du maire")
    mid = a.pubs.plan(c.id, pid, "Titre du jour\nDescription", "2026-11-10T18:00", {"youtube": True})
    a.sync()
    b.sync()
    rows = b.plans()
    assert len(rows) == 1
    r = rows[0]
    assert (r["concept"], r["planned_at"], r["title"]) == ("Mas.tair", "2026-11-10T18:00", "Titre du jour")
    assert r["networks"]["youtube"] and not r["networks"]["tiktok"]
    assert r["video"] == "Interview du maire" and r["project_id"] is None   # la vidéo est restée sur A
    # B déplace la publication et coche TikTok ; A suit
    b.pubs.plan(r["concept_id"], None, r["text"], "2026-11-11T09:30", {"youtube": True, "tiktok": True}, r["id"])
    b.pubs.toggle_network(r["id"], "meta")
    b.sync()
    a.sync()
    back = a.pubs.plan_of(mid)
    assert back["planned_at"] == "2026-11-11T09:30"
    assert back["networks"] == {"youtube": True, "tiktok": True, "meta": True}
    assert back["project_id"] == pid                                      # toujours reliée à la vidéo sur A


def test_deletions(pcs):
    a, b = pcs
    c = a.concepts.create_concept("Éphémère")
    a.pubs.plan(c.id, None, "À supprimer", "2026-12-01T10:00")
    a.sync()
    b.sync()
    assert len(b.plans()) == 1
    b.pubs.delete_master(b.plans()[0]["id"])
    b.concepts.delete_concept(b.concept("Éphémère").id)
    assert b.sync()["errors"] == []
    out = a.sync()
    assert out["deleted"] == 2 and out["errors"] == []
    assert a.plans() == [] and a.concepts.user_concepts() == []
    data = [json.loads(f.read_text(encoding="utf-8")) for f in (a.shared / "calendrier").glob("*.json")]
    assert data and all(d["deleted"] for d in data)
    assert a.sync() == b.sync() == {"sent": 0, "received": 0, "deleted": 0, "errors": []}


def test_both_pcs_find_the_same_published_video(pcs):
    """La recherche des vidéos publiées tourne sur chaque PC : la même vidéo n'apparaît qu'une fois."""
    a, b = pcs
    a.concepts.create_concept("Mas.tair")
    a.sync()
    b.sync()
    url = "https://www.youtube.com/watch?v=abcdefghijk"
    for pc in (a, b):
        cid = pc.concept("Mas.tair").id
        assert pc.pubs.sync_published(cid, "youtube", url, "Vidéo", "desc", "2026-11-01T12:00") == "added"
    a.sync()
    b.sync()
    a.sync()
    assert len(a.plans()) == len(b.plans()) == 1
    assert len(list((a.shared / "calendrier").glob("*.json"))) == 1


def test_conflict_files_and_missing_folder(pcs, tmp_path):
    a, _b = pcs
    a.concepts.create_concept("Mas.tair")
    a.sync()
    f = next((a.shared / "concepts").glob("*.json"))
    (f.parent / f"{f.stem}-PC-BUREAU.json").write_text(f.read_text(encoding="utf-8"), encoding="utf-8")
    (f.parent / "abime.json").write_text("{ pas du json", encoding="utf-8")
    assert a.sync() == {"sent": 0, "received": 0, "deleted": 0, "errors": []}
    with pytest.raises(SyncError):
        SharedSync(a.db, a.concepts, a.pubs, tmp_path / "absent").run()
