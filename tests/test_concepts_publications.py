"""Scénarios D (Poussey Pass) et E (Pokémon) du cahier des charges, couche données."""
import pytest

from studiovideo.domain.models import Platform, PublicationStatus
from studiovideo.services.concepts import ConceptError, ConceptService
from studiovideo.services.publications import (ExternalAccountNotConfirmed, PublicationError,
                                               PublicationService)


@pytest.fixture
def setup(db):
    cs, ps = ConceptService(db), PublicationService(db)
    pp = cs.create_concept("Poussey Pass", theme="Humour")
    pk = cs.create_concept("Pokémon", theme="Pokémon")
    pp_acc = {p: cs.create_account(p, "Poussey Pass") for p in
              (Platform.YOUTUBE, Platform.TIKTOK, Platform.INSTAGRAM, Platform.FACEBOOK)}
    pk_acc = {p: cs.create_account(p, "Pokémon FR") for p in (Platform.X, Platform.INSTAGRAM)}
    for a in pp_acc.values():
        cs.link(pp.id, a.id)
    for a in pk_acc.values():
        cs.link(pk.id, a.id)
    return cs, ps, pp, pk, pp_acc, pk_acc


def test_scenario_d_poussey_pass(setup):
    cs, ps, pp, _, pp_acc, _ = setup
    dests = cs.proposed_destinations(pp.id)
    assert {d.account.platform for d in dests} == {Platform.YOUTUBE, Platform.TIKTOK,
                                                   Platform.INSTAGRAM, Platform.FACEBOOK}
    assert all(d.default_selected for d in dests)
    m = ps.create_master(pp.id, title="Sketch", description="desc")
    created = ps.set_destinations(m, [pp_acc[Platform.YOUTUBE].id, pp_acc[Platform.FACEBOOK].id])
    assert len(created) == 2
    pubs = ps.list_for_master(m)
    assert {p.account.platform for p in pubs} == {Platform.YOUTUBE, Platform.FACEBOOK}
    assert all(p.status == PublicationStatus.DRAFT for p in pubs)
    ps.set_status(pubs[0].id, PublicationStatus.FAILED, error="quota")
    after = {p.id: p.status for p in ps.list_for_master(m)}
    assert after[pubs[0].id] == PublicationStatus.FAILED
    assert after[pubs[1].id] == PublicationStatus.DRAFT


def test_scenario_e_pokemon(setup):
    cs, ps, _, pk, pp_acc, pk_acc = setup
    dests = cs.proposed_destinations(pk.id)
    assert {d.account.id for d in dests} == {a.id for a in pk_acc.values()}
    assert not any(d.account.id in {a.id for a in pp_acc.values()} for d in dests)
    m = ps.create_master(pk.id, title="Évolution")
    ps.set_destinations(m, [pk_acc[Platform.INSTAGRAM].id])
    pubs = ps.list_for_master(m)
    assert len(pubs) == 1 and pubs[0].account.id == pk_acc[Platform.INSTAGRAM].id


def test_external_account_requires_confirmation(setup):
    cs, ps, _, pk, pp_acc, _ = setup
    m = ps.create_master(pk.id)
    yt = pp_acc[Platform.YOUTUBE].id
    with pytest.raises(ExternalAccountNotConfirmed):
        ps.set_destinations(m, [yt])
    assert ps.list_for_master(m) == []
    ps.set_destinations(m, [yt], confirmed_external={yt})
    (pub,) = ps.list_for_master(m)
    assert pub.is_exceptional
    ext = cs.proposed_destinations(pk.id, include_external=True)
    assert any(d.account.id == yt and not d.in_concept and not d.default_selected for d in ext)


def test_master_propagation_and_local_override(setup):
    cs, ps, pp, _, pp_acc, _ = setup
    m = ps.create_master(pp.id, title="Commun", description="D")
    ps.set_destinations(m, [pp_acc[Platform.YOUTUBE].id, pp_acc[Platform.TIKTOK].id])
    yt, tt = sorted(ps.list_for_master(m), key=lambda p: p.account.platform.value != "youtube")
    ps.override(yt.id, title="Titre YouTube")
    ps.update_master(m, title="Commun v2")
    by = {p.account.platform: p for p in ps.list_for_master(m)}
    assert by[Platform.YOUTUBE].title == "Titre YouTube"
    assert by[Platform.TIKTOK].title == "Commun v2"
    ps.reset_to_master(yt.id, "title")
    assert {p.title for p in ps.list_for_master(m)} == {"Commun v2"}


def test_no_duplicates_and_published_needs_remote_id(setup):
    cs, ps, pp, _, pp_acc, _ = setup
    m = ps.create_master(pp.id)
    a = pp_acc[Platform.YOUTUBE].id
    assert len(ps.set_destinations(m, [a, a])) == 1
    assert ps.set_destinations(m, [a]) == []
    (pub,) = ps.list_for_master(m)
    with pytest.raises(PublicationError):
        ps.set_status(pub.id, PublicationStatus.PUBLISHED)
    ps.set_status(pub.id, PublicationStatus.PUBLISHED, remote_id="abc123")


def test_link_unlink_and_multi_accounts(setup):
    cs, _, pp, _, _, _ = setup
    tt_en = cs.create_account(Platform.TIKTOK, "Poussey Pass EN", language="en")
    cs.link(pp.id, tt_en.id, is_default=False)
    tiktoks = [a for a, _ in cs.concept_accounts(pp.id) if a.platform == Platform.TIKTOK]
    assert len(tiktoks) == 2
    assert not next(d for d in cs.proposed_destinations(pp.id) if d.account.id == tt_en.id).default_selected
    cs.unlink(pp.id, tt_en.id)
    assert tt_en.id not in {a.id for a, _ in cs.concept_accounts(pp.id)}


def test_duplicate_concept_name(setup):
    cs = setup[0]
    with pytest.raises(ConceptError):
        cs.create_concept("Poussey Pass")
