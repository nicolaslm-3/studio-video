"""Vidéo traduite (espace Traduction) : la vidéo du projet, dans une autre langue.

Même montage que le projet (un extrait choisi en Clipping reste cet extrait, le format reste le même) ;
vidéo entière si le projet n'a pas encore de montage. Sous-titres traduits dans le style choisi, voix doublée si
elle existe (musique d'origine conservée si demandé), texte du haut traduit, zones de texte d'origine masquées.
"""
from __future__ import annotations

import copy

from .model import ASPECTS, Timeline
from .subtitles import generate

# styles de sous-titres proposés d'un clic (clé : (nom, réglages du style))
SUB_PRESETS = {
    "white": ("Blanc contouré", {"color": "#FFFFFF", "highlight_color": "#FFD54A", "outline_color": "#000000",
                                 "outline": 7, "box": False, "shadow": True}),
    "yellow": ("Jaune contouré", {"color": "#FFD54A", "highlight_color": "#FFFFFF", "outline_color": "#000000",
                                  "outline": 7, "box": False, "shadow": True}),
    "band": ("Bandeau noir", {"color": "#FFFFFF", "highlight_color": "#FFD54A", "outline": 0, "box": True,
                              "box_color": "#000000", "box_opacity": 0.78, "shadow": False}),
    "card": ("Carte blanche", {"color": "#111111", "highlight_color": "#E5484D", "outline": 0, "box": True,
                               "box_color": "#FFFFFF", "box_opacity": 1.0, "shadow": False}),
}


def source_aspect(width: int | None, height: int | None) -> str:
    """Format qui respecte l'orientation de la vidéo d'origine (une vidéo courte reste verticale)."""
    if not width or not height:
        return "9:16"
    r = width / height
    return "9:16" if r < 0.7 else "4:5" if r < 0.9 else "1:1" if r < 1.15 else "16:9"


def apply_preset(tl: Timeline, key: str) -> None:
    for k, v in SUB_PRESETS.get(key, SUB_PRESETS["white"])[1].items():
        setattr(tl.subtitles.style, k, v)


def translated_timeline(template: Timeline, asset_id: int, duration: float, lang: str, transcript,
                        preset: str = "white", aspect: str | None = None, top_text: str = "",
                        masks: list | None = None, dubbed: bool = False, keep_music: bool = True,
                        text_style: dict | None = None) -> Timeline:
    tl = Timeline.from_dict(copy.deepcopy(template.to_dict()))
    if not tl.segments:                         # pas encore de montage : la vidéo entière
        tl.add_segment(asset_id, 0.0, duration)
    if top_text.strip():                        # le texte du haut traduit remplace celui d'origine
        tl.texts = [t for t in tl.texts if t.position != "top"]
    if aspect in ASPECTS and aspect != tl.format.aspect:
        tl.set_aspect(aspect, min(tl.format.width, tl.format.height))
    apply_preset(tl, preset)
    tl.subtitles.language = lang
    tl.subtitles.cues = generate(tl, {asset_id: transcript}) if transcript is not None else []
    tl.subtitles.enabled = tl.subtitles.burn = bool(tl.subtitles.cues)
    if dubbed:
        tl.voice.mode, tl.voice.language = "dub", lang
        # musique gardée : fond séparé (sans la voix d'origine) ; sinon la voix doublée seule
        tl.voice.background = "ambience" if keep_music else "original"
        if not keep_music:
            tl.voice.original_level_db = -60.0
    else:
        tl.voice.mode, tl.voice.language = "original", ""
    if masks:
        tl.video.masks = [dict(m) for m in masks]
    if top_text.strip():
        style = {k: v for k, v in (text_style or {}).items() if k not in ("text", "start", "end", "id")}
        style.update(position="top", box=style.get("box", True))
        tl.add_text(top_text.strip(), 0.0, tl.duration, **style)
    return tl
