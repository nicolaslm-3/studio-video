"""Chapitres YouTube : placement sur le temps monté, règles (0:00, ≥ 10 s, ≥ 3), découpe locale, modèle."""
import json
from types import SimpleNamespace as NS

from studiovideo.edit.model import Timeline
from studiovideo.publish import copywriter as cw


def _tr(segs):
    return NS(segments=[NS(start=a, end=b, text=t) for a, b, t in segs], language="fr")


def test_sentences_follow_the_edit():
    tl = Timeline()
    tl.add_segment(1, 10, 20)
    tl.add_segment(2, 0, 5)
    trs = {1: _tr([(5, 9, "coupée"), (11, 14, "un"), (18, 25, "deux")]), 2: _tr([(1, 3, "trois")])}
    s = cw.sentences_on_output(tl, trs.get)
    assert s == [(1.0, 4.0, "un"), (8.0, 10.0, "deux"), (11.0, 13.0, "trois")]


def test_valid_chapters_rules():
    assert cw.valid_chapters([(5, "a"), (8, "b"), (40, "c")], 100) == []           # trop peu après filtrage
    assert cw.valid_chapters([(3, "a"), (30, "b"), (35, "x"), (60, "c"), (95, "d")], 100) == \
        [(0.0, "a"), (30.0, "b"), (60.0, "c")]
    assert cw.chapters_text([(0, "Intro"), (75, "Suite"), (3700, "Fin")]) == "00:00 Intro\n01:15 Suite\n1:01:40 Fin"


def _talk(total=400):
    topics = ["moteur électrique batterie", "recette gâteau chocolat", "voyage montagne randonnée",
              "football match stade"]
    sents, t, k = [], 0.0, 0
    while t < total - 5:
        topic = topics[min(3, int(t / (total / 4)))]
        sents.append((t, t + 4, f"Nous parlons de {topic} aujourd'hui."))
        nxt = t + 5
        if int(nxt / (total / 4)) != int(t / (total / 4)):
            nxt += 2.5                                   # vraie pause au changement de sujet
        t, k = nxt, k + 1
    return sents


def test_heuristic_cuts_at_pauses_with_topic_titles():
    sents = _talk()
    chs = cw.chapters_heuristic(sents, 400)
    assert chs[0][0] == 0 and len(chs) == 4
    pauses = [sents[i + 1][0] for i in range(len(sents) - 1) if sents[i + 1][0] - sents[i][1] > 1.5]
    assert [c[0] for c in chs[1:]] == pauses                        # sur les pauses, pas au milieu d'une phrase
    assert "moteur" in chs[0][1].lower() and "football" in chs[3][1].lower()
    assert cw.chapters_heuristic(sents[:10], 60) == []             # vidéo courte : pas de chapitres


def test_llm_chapters_validated():
    eng = NS(_ask=lambda p: "Voici : " + json.dumps({"chapters": [
        {"start": 4, "title": "Intro"}, {"start": 100, "title": "Batterie"}, {"start": 105, "title": "Trop près"},
        {"start": 300, "title": "Fin"}]}))
    assert cw.chapters_llm(eng, _talk(), 400) == [(0.0, "Intro"), (100.0, "Batterie"), (300.0, "Fin")]
