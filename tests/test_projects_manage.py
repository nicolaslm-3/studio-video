"""Gestion des projets : renommer (nom unique), dupliquer, archiver, changer de concept, supprimer."""
import pytest

from studiovideo.core import build_context


@pytest.fixture
def app(tmp_path):
    a = build_context(tmp_path / "d")
    yield a
    a.close()


def test_manage_projects(app):
    c1 = app.concepts.create_concept("Principal").id
    c2 = app.concepts.create_concept("Autre").id
    p = app.projects.create(c1, "Reportage")
    q = app.projects.create(c1, "Brouillon")
    app.projects.save_data(p, {"timeline": {"segments": [{"asset_id": 1, "src_in": 0, "src_out": 2}]}})
    with app.db.transaction() as c:
        c.execute("INSERT INTO clip(project_id, name, start_s, end_s) VALUES (?, 'Accroche', 1, 5)", (p,))
    assert app.projects.rename(q, "Reportage") == "Reportage (2)"
    d = app.projects.duplicate(p)
    assert app.projects.get(d)["name"] == "Reportage (copie)"
    assert app.projects.load_data(d) == app.projects.load_data(p)
    assert app.db.query_one("SELECT COUNT(*) n FROM clip WHERE project_id = ?", (d,))["n"] == 1
    app.projects.set_archived(q, True)
    assert q not in [r["id"] for r in app.projects.recent()] and len(app.projects.all()) == 3
    assert q not in [r["id"] for r in app.projects.all(include_archived=False)]
    app.projects.set_concept(d, c2)
    assert app.projects.get(d)["concept_id"] == c2
    app.projects.delete(d)
    assert app.projects.get(d) is None


def test_delete_refused_when_published(app):
    from studiovideo.edit.model import Timeline
    c = app.concepts.create_concept("Principal")
    p = app.projects.create(c.id, "Publié")
    vid = app.montage.record_export(p, Timeline(), {"path": "x.mp4", "duration": 1}, None)
    acc = app.concepts.create_account("youtube", "Chaîne")
    mid = app.publications.create_master(c.id, "Titre", variant_id=vid)
    app.publications.set_destinations(mid, [acc.id], confirmed_external={acc.id})
    assert app.projects.published_count(p) == 1
    with pytest.raises(ValueError, match="archivez"):
        app.projects.delete(p)
    assert app.projects.get(p) is not None
