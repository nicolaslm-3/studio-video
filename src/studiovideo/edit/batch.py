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


def clip_timeline(template: Timeline, asset_id: int, start: float, end: float, aspect: str, opts: BatchOptions,
                  transcript=None, hook: str = "", title_style: dict | None = None) -> Timeline:
    tl = Timeline.from_dict(copy.deepcopy(template.to_dict()))
    tl.segments = []
    tl.add_segment(asset_id, start, end)
    if aspect in ASPECTS:
        tl.set_aspect(aspect, opts.short_side)
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
    """clips : dicts avec asset_id, start, end, name, hook. Retourne (clip, format, timeline)."""
    out = []
    for c in clips:
        for asp in opts.aspects:
            tl = clip_timeline(template, c["asset_id"], c["start"], c["end"], asp, opts,
                               transcripts.get(c["asset_id"]), c.get("hook") or "", title_style)
            out.append((c, asp, tl))
    return out
