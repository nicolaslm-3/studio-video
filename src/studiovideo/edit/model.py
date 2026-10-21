"""Modèle de montage (cahier des charges §8, §12, §13, §17).

Le montage est une DESCRIPTION : les fichiers sources ne sont jamais modifiés.
Il est enregistré en JSON dans le projet et rendu par FFmpeg à l'export.

Temps :
- « temps source » : position dans un fichier source ;
- « temps montage » : position sur la timeline (somme des segments).
"""
from __future__ import annotations

import copy
import uuid
from dataclasses import asdict, dataclass, field, fields
from typing import Any

SCHEMA = 1
MIN_SEGMENT = 0.1  # secondes

ASPECTS = {
    "9:16": (1080, 1920),
    "16:9": (1920, 1080),
    "1:1": (1080, 1080),
    "4:5": (1080, 1350),
}


def _id() -> str:
    return uuid.uuid4().hex[:10]


def _from_dict(cls, data: dict | None):
    data = data or {}
    names = {f.name for f in fields(cls)}
    return cls(**{k: v for k, v in data.items() if k in names})


@dataclass
class Segment:
    asset_id: int
    src_in: float
    src_out: float
    id: str = field(default_factory=_id)
    volume_db: float = 0.0
    muted: bool = False

    @property
    def duration(self) -> float:
        return max(0.0, self.src_out - self.src_in)


@dataclass
class TextItem:
    text: str
    start: float = 0.0          # temps montage
    end: float = 3.0
    id: str = field(default_factory=_id)
    position: str = "top"       # top | center | bottom | custom
    x: float = 0.5              # 0..1 (centre du texte), si position = custom
    y: float = 0.12
    font: str = "Segoe UI"
    size: int = 72              # en pixels pour une image de 1920 de haut
    bold: bool = True
    italic: bool = False
    color: str = "#FFFFFF"
    outline_color: str = "#000000"
    outline: int = 6
    box: bool = False
    box_color: str = "#000000"
    box_opacity: float = 0.6
    align: str = "center"       # left | center | right
    max_width: float = 0.86     # fraction de la largeur
    fade: float = 0.0           # fondu d'apparition/disparition (s)


@dataclass
class Music:
    asset_id: int | None = None
    volume_db: float = -14.0
    fade_in: float = 1.0
    fade_out: float = 2.0
    start: float = 0.0          # temps montage où démarre la musique
    src_offset: float = 0.0     # départ dans le fichier musique
    loop: bool = True
    duck: bool = True           # atténuation automatique quand on parle
    duck_amount: float = 8.0    # rapport de compression (2..20)


@dataclass
class FormatSettings:
    aspect: str = "9:16"
    width: int = 1080
    height: int = 1920
    fps: float = 30.0
    fit: str = "fill"           # fill (recadré) | fit (bandes noires) | blur (fond flou)


@dataclass
class VideoSettings:
    zoom: float = 1.0           # >= 1 en mode « fill »
    offset_x: float = 0.0       # -1..1 : décalage du cadre dans la zone disponible
    offset_y: float = 0.0
    mirror: bool = False        # miroir horizontal
    flip: bool = False          # retournement vertical
    rotation: int = 0           # 0 | 90 | 180 | 270
    brightness: float = 0.0     # -1..1
    contrast: float = 1.0       # 0..2
    saturation: float = 1.0     # 0..3
    temperature: int = 6500     # kelvins, 6500 = neutre
    sharpen: float = 0.0        # 0..1


@dataclass
class AudioSettings:
    gain_db: float = 0.0
    highpass: bool = True       # coupe les graves parasites (< 80 Hz)
    denoise: bool = False       # réduction de bruit légère
    denoise_amount: float = 12.0  # dB
    normalize: bool = True
    target_lufs: float = -14.0
    true_peak: float = -1.0
    fade_in: float = 0.0
    fade_out: float = 0.0


@dataclass
class ExportSettings:
    codec: str = "h264"         # h264 | h265
    crf: int = 20
    preset: str = "medium"
    audio_bitrate: int = 192    # kb/s
    burn_texts: bool = True
    directory: str = ""
    file_pattern: str = "{project}_{aspect}_{date}"


@dataclass
class Timeline:
    segments: list[Segment] = field(default_factory=list)
    texts: list[TextItem] = field(default_factory=list)
    music: Music = field(default_factory=Music)
    format: FormatSettings = field(default_factory=FormatSettings)
    video: VideoSettings = field(default_factory=VideoSettings)
    audio: AudioSettings = field(default_factory=AudioSettings)
    export: ExportSettings = field(default_factory=ExportSettings)
    schema: int = SCHEMA

    # -- sérialisation -----------------------------------------------------
    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict | None) -> "Timeline":
        d = d or {}
        return cls(
            segments=[_from_dict(Segment, s) for s in d.get("segments", [])],
            texts=[_from_dict(TextItem, t) for t in d.get("texts", [])],
            music=_from_dict(Music, d.get("music")),
            format=_from_dict(FormatSettings, d.get("format")),
            video=_from_dict(VideoSettings, d.get("video")),
            audio=_from_dict(AudioSettings, d.get("audio")),
            export=_from_dict(ExportSettings, d.get("export")),
        )

    def copy(self) -> "Timeline":
        return copy.deepcopy(self)

    # -- durée / positions -------------------------------------------------
    @property
    def duration(self) -> float:
        return sum(s.duration for s in self.segments)

    def segment_start(self, index: int) -> float:
        return sum(s.duration for s in self.segments[:index])

    def locate(self, t: float) -> tuple[int, float] | None:
        """Temps montage -> (index du segment, temps source)."""
        if not self.segments:
            return None
        t = max(0.0, t)
        acc = 0.0
        for i, s in enumerate(self.segments):
            if t < acc + s.duration or i == len(self.segments) - 1:
                return i, min(s.src_out, s.src_in + (t - acc))
            acc += s.duration
        return None

    def index_of(self, seg_id: str) -> int:
        for i, s in enumerate(self.segments):
            if s.id == seg_id:
                return i
        raise KeyError(seg_id)

    # -- opérations --------------------------------------------------------
    def add_segment(self, asset_id: int, src_in: float, src_out: float, index: int | None = None) -> Segment:
        if src_out - src_in < MIN_SEGMENT:
            raise ValueError("Le segment est trop court.")
        seg = Segment(asset_id=asset_id, src_in=float(src_in), src_out=float(src_out))
        if index is None:
            self.segments.append(seg)
        else:
            self.segments.insert(index, seg)
        return seg

    def remove_segment(self, seg_id: str) -> None:
        self.segments.pop(self.index_of(seg_id))

    def move_segment(self, seg_id: str, delta: int) -> int:
        i = self.index_of(seg_id)
        j = max(0, min(len(self.segments) - 1, i + delta))
        if i != j:
            self.segments.insert(j, self.segments.pop(i))
        return j

    def split_at(self, t: float) -> tuple[Segment, Segment] | None:
        """Coupe le segment situé au temps montage t."""
        loc = self.locate(t)
        if loc is None:
            return None
        i, src_t = loc
        s = self.segments[i]
        if src_t - s.src_in < MIN_SEGMENT or s.src_out - src_t < MIN_SEGMENT:
            return None
        right = Segment(asset_id=s.asset_id, src_in=src_t, src_out=s.src_out,
                        volume_db=s.volume_db, muted=s.muted)
        s.src_out = src_t
        self.segments.insert(i + 1, right)
        return s, right

    def trim(self, seg_id: str, src_in: float | None = None, src_out: float | None = None,
             source_duration: float | None = None) -> None:
        s = self.segments[self.index_of(seg_id)]
        new_in = s.src_in if src_in is None else max(0.0, float(src_in))
        new_out = s.src_out if src_out is None else float(src_out)
        if source_duration is not None:
            new_out = min(new_out, source_duration)
        if new_out - new_in < MIN_SEGMENT:
            raise ValueError("Le segment serait trop court.")
        s.src_in, s.src_out = new_in, new_out

    def add_text(self, text: str, start: float = 0.0, end: float | None = None, **style) -> TextItem:
        end = end if end is not None else min(self.duration or 3.0, start + 3.0)
        item = _from_dict(TextItem, {"text": text, "start": start, "end": max(start + 0.2, end), **style})
        self.texts.append(item)
        return item

    def text(self, text_id: str) -> TextItem:
        for t in self.texts:
            if t.id == text_id:
                return t
        raise KeyError(text_id)

    def remove_text(self, text_id: str) -> None:
        self.texts = [t for t in self.texts if t.id != text_id]

    def set_aspect(self, aspect: str, short_side: int = 1080) -> None:
        if aspect in ASPECTS:
            w, h = ASPECTS[aspect]
            scale = short_side / min(w, h)
            self.format.width, self.format.height = _even(w * scale), _even(h * scale)
        self.format.aspect = aspect

    def problems(self, asset_lookup) -> list[str]:
        """Contrôles avant export. asset_lookup(asset_id) -> ligne source_asset ou None."""
        out = []
        if not self.segments:
            out.append("La timeline est vide : ajoutez au moins un extrait.")
        for i, s in enumerate(self.segments, 1):
            a = asset_lookup(s.asset_id)
            if a is None:
                out.append(f"Segment {i} : média absent de la bibliothèque.")
            elif a["missing"]:
                out.append(f"Segment {i} : fichier source introuvable ({a['file_name']}).")
            elif a["kind"] != "video":
                out.append(f"Segment {i} : « {a['file_name']} » n'est pas une vidéo.")
            elif a["duration_s"] and s.src_out > a["duration_s"] + 0.05:
                out.append(f"Segment {i} : la fin dépasse la durée du média.")
        if self.music.asset_id:
            m = asset_lookup(self.music.asset_id)
            if m is None or m["missing"]:
                out.append("La musique est introuvable.")
        if self.format.width % 2 or self.format.height % 2:
            out.append("La largeur et la hauteur doivent être paires.")
        for t in self.texts:
            if not t.text.strip():
                out.append("Un texte est vide.")
            if t.end <= t.start:
                out.append(f"Le texte « {t.text[:20]} » se termine avant de commencer.")
        return out


def _even(v: float) -> int:
    n = int(round(v))
    return n - (n % 2)


# -- réglages par concept (§16.2) ----------------------------------------------
CONCEPT_KEYS = ("format", "video", "audio", "export", "music_defaults", "text_style")


def concept_defaults_from(tl: Timeline) -> dict[str, Any]:
    """Ce qui est mémorisé pour un concept : tout sauf le contenu (segments, textes, médias)."""
    style = asdict(tl.texts[0]) if tl.texts else {}
    for k in ("id", "text", "start", "end"):
        style.pop(k, None)
    music = asdict(tl.music)
    music.pop("asset_id", None)
    video = asdict(tl.video)
    for k in ("offset_x", "offset_y"):
        video.pop(k, None)
    return {"format": asdict(tl.format), "video": video, "audio": asdict(tl.audio),
            "export": asdict(tl.export), "music_defaults": music, "text_style": style}


def apply_concept_defaults(tl: Timeline, defaults: dict | None) -> Timeline:
    if not defaults:
        return tl
    d = tl.to_dict()
    for k in ("format", "video", "audio", "export"):
        if k in defaults:
            d[k] = {**d[k], **defaults[k]}
    if "music_defaults" in defaults:
        d["music"] = {**d["music"], **defaults["music_defaults"]}
    out = Timeline.from_dict(d)
    out.segments, out.texts = tl.segments, tl.texts
    return out


def text_style(defaults: dict | None) -> dict:
    return dict((defaults or {}).get("text_style") or {})
