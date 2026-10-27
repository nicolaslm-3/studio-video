"""Production en série (cahier des charges §4 mode 6, §17).

À partir d'un montage « modèle » (format, cadrage, image, audio, style de texte et de
sous-titres, musique), fabrique une timeline par clip et par format demandé.
"""
from __future__ import annotations

import copy
from dataclasses import dataclass, field

from .model import ASPECTS, Timeline
from .subtitles import generate


@dataclass
class BatchOptions:
    aspects: list[str] = field(default_factory=lambda: ["9:16"])
    short_side: int = 1080
    subtitles: bool = True          # générer les sous-titres si le média est transcrit
    hook_title: bool = True         # accroche (ou nom du clip) en titre au début
    hook_duration: float = 3.0
    keep_music: bool = True
    keep_texts: bool = False        # recopier les textes du modèle (à éviter : ils sont propres au montage)
    languages: list[str] = field(default_factory=lambda: [""])   # "" = langue d'origine
    dubbed: set = field(default_factory=set)
    reframe: bool = False           # suivre les visages (vidéo horizontale → vertical / carré)   # {(asset_id, langue)} doublés : la variante prend la voix doublée


def clip_timeline(template: Timeline, asset_id: int, start: float, end: float, aspect: str, opts: BatchOptions,
                  transcript=None, hook: str = "", title_style: dict | None = None) -> Timeline:
    tl = Timeline.from_dict(copy.deepcopy(template.to_dict()))
    tl.segments = []
    tl.add_segment(asset_id, start, end)
    if aspect in ASPECTS:
        tl.set_aspect(aspect, opts.short_side)
    tl.video.auto_reframe = bool(opts.reframe and aspect != "16:9")
    if tl.video.auto_reframe:
        tl.format.fit = "fill"
    if not opts.keep_texts:
        tl.texts = []
    if not opts.keep_music:
        tl.music.asset_id = None
    tl.subtitles.cues = []
    if opts.subtitles and transcript is not None:
        tl.subtitles.cues = generate(tl, {asset_id: transcript})
        tl.subtitles.enabled = bool(tl.subtitles.cues)
    else:
        tl.subtitles.enabled = False
    if opts.hook_title and hook.strip():
        style = {k: v for k, v in (title_style or {}).items() if k not in ("text", "start", "end", "id")}
        style.setdefault("position", "top")
        style.setdefault("box", True)
        tl.add_text(hook.strip(), 0.0, min(tl.duration, opts.hook_duration), **style)
    return tl


def plan_batch(template: Timeline, clips: list[dict], opts: BatchOptions, transcripts: dict,
               title_style: dict | None = None) -> list[tuple[dict, str, Timeline]]:
    """clips : dicts avec asset_id, start, end, name, hook.

    transcripts : {asset_id: Transcript} (langue d'origine) ou {(asset_id, langue): Transcript}.
    Retourne (clip, format, timeline) ; pour une langue traduite, le format vaut « 9:16 · en ».
    """
    out = []
    for c in clips:
        for lang in (opts.languages or [""]):
            tr = transcripts.get((c["asset_id"], lang)) if lang else transcripts.get(
                (c["asset_id"], ""), transcripts.get(c["asset_id"]))
            if lang and tr is None:
                continue                      # pas de traduction pour ce média
            for asp in opts.aspects:
                tl = clip_timeline(template, c["asset_id"], c["start"], c["end"], asp, opts, tr,
                                   c.get("hook") or "" if not lang else "", title_style)
                tl.subtitles.language = lang
                if c.get("reframe") and tl.video.auto_reframe:  # cadre déjà calculé (aperçu) : réutilisé
                    tl.segments[0].reframe = [list(k) for k in c["reframe"]]
                if lang and (c["asset_id"], lang) in opts.dubbed:
                    tl.voice.mode, tl.voice.language = "dub", lang
                else:
                    tl.voice.mode, tl.voice.language = "original", ""
                out.append((c, f"{asp} · {lang}" if lang else asp, tl))
    return out
