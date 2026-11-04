"""Rendu FFmpeg du montage : export MP4, aperçu fidèle d'une image, analyse audio.

Chaîne vidéo par segment : rotation → miroir/retournement → mise au format (recadré,
bandes noires ou fond flou) → corrections (luminosité, contraste, saturation,
température, netteté) → cadence et format de pixels → concaténation → textes.

Chaîne audio : segments (volume, silence si absent) → concaténation → passe-haut →
réduction de bruit → gain → musique (atténuation automatique pendant la parole) →
fondus → normalisation EBU R128 en deux passes (mesure puis correction linéaire),
avec plafond de crête vraie.
"""
from __future__ import annotations

import json
import logging
import math
import re
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Callable

from ..media import ffmpeg
from . import compose
from .model import Timeline, TextItem
from .textrender import render_text

log = logging.getLogger(__name__)
_NOWIN = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0

SAMPLE_RATE = 48000


class RenderError(RuntimeError):
    pass


@dataclass
class Source:
    path: str
    width: int | None
    height: int | None
    duration: float | None
    has_audio: bool
    file_name: str = ""
    dub_path: str = ""   # piste de voix doublée (48 kHz mono), alignée sur le média
    voice_stem: str = ""  # voix seule (séparation), alignée sur le média
    music_stem: str = ""  # musique et ambiance seules (séparation)


@dataclass
class Plan:
    """Commande FFmpeg prête à lancer."""
    args: list[str]
    duration: float
    temp_dir: Path | None = None
    notes: list[str] = field(default_factory=list)


# Filtres colorimétriques (bouton robot) : nom affiché, filtre FFmpeg ; l'intensité mêle l'image filtrée à l'image
# d'origine.
LOOKS = {
    "chaud": ("Chaud", "colortemperature=temperature=4200"),
    "froid": ("Froid", "colortemperature=temperature=9500"),
    "vif": ("Vif", "eq=saturation=1.8:contrast=1.12"),
    "doux": ("Doux", "eq=contrast=0.9:saturation=0.85:brightness=0.03"),
    "cinema": ("Cinéma", "colorbalance=rs=-0.08:bs=0.1:rh=0.1:bh=-0.08,eq=contrast=1.08"),
    "vintage": ("Vintage", "curves=preset=vintage"),
    "nb": ("Noir et blanc", "hue=s=0"),
}
PAN_ZOOM = 0.08                 # mouvement subtil : agrandissement qui laisse la place au glissement


def tilt_cover(tl: Timeline) -> float:
    """Agrandissement nécessaire pour qu'une image tournée de `tilt` degrés couvre encore tout le cadre."""
    a = math.radians(abs(float(getattr(tl.video, "tilt", 0.0) or 0.0)))
    if a < 1e-4:
        return 1.0
    W, H = tl.format.width, tl.format.height
    r = max(W / H, H / W)
    return math.cos(a) + math.sin(a) * r


ZOOM_MIN, ZOOM_MAX = 0.2, 2.5   # zoom du bouton robot : sous 1, la vidéo est réduite dans le cadre


BLUR_SIGMA = 30.0               # flou du bouton robot à fond, pour une image de 1080 de large


def blur_sigma(tl: Timeline, width: float | None = None) -> float:
    """Force du flou (écart type, en pixels d'une image de `width` de large, celle de la sortie par défaut)."""
    b = max(0.0, min(1.0, float(getattr(tl.video, "blur", 0.0) or 0.0)))
    f = tl.format
    return round(b * BLUR_SIGMA * (width or min(f.width, f.height)) / 1080, 2)


def video_zoom(tl: Timeline) -> float:
    return max(ZOOM_MIN, min(ZOOM_MAX, float(getattr(tl.video, "zoom", 1.0) or 1.0)))


def pan_zoom(tl: Timeline) -> float:
    return 1.0 + PAN_ZOOM * max(0.0, min(1.0, float(getattr(tl.video, "pan", 0.0) or 0.0)))


def speed_of(tl: Timeline) -> float:
    return max(0.5, min(2.0, float(getattr(tl.video, "speed", 1.0) or 1.0)))


def output_duration(tl: Timeline) -> float:
    """Durée de la vidéo exportée (la vitesse change la durée ; le montage reste en temps d'origine)."""
    return tl.duration / speed_of(tl)


def _even(v: float) -> int:
    n = int(math.ceil(v))
    return n + (n % 2)


def _fmt(v: float) -> str:
    return f"{v:.6f}".rstrip("0").rstrip(".") if v != int(v) else str(int(v))


def _escape_path(p: str) -> str:
    return p.replace("\\", "/")


def _filter_path(p: str) -> str:
    """Chemin utilisable comme option de filtre (« C:/… », apostrophes, virgules) : double échappement FFmpeg."""
    def esc(text: str, chars: str) -> str:
        return "".join("\\" + c if c in chars else c for c in text)
    return esc(esc(_escape_path(p), "\\':"), "\\'[],;")


# -- vidéo -----------------------------------------------------------------------
MASK_MODES = ("blur", "delogo", "box")


def mask_chain(masks: list, sw: int, sh: int, tag: str = "") -> str:
    """Masque des zones de l'image source (avant rotation et recadrage). Chaîne vide si aucune zone."""
    out = []
    for j, m in enumerate(masks or []):
        x = int(max(0.0, min(1.0, float(m.get("x", 0)))) * sw)
        y = int(max(0.0, min(1.0, float(m.get("y", 0)))) * sh)
        w = int(max(0.0, min(1.0, float(m.get("w", 0)))) * sw)
        h = int(max(0.0, min(1.0, float(m.get("h", 0)))) * sh)
        x, y = min(x, sw - 10), min(y, sh - 10)
        w, h = max(8, min(w, sw - x)) // 2 * 2, max(8, min(h, sh - y)) // 2 * 2
        x, y = x // 2 * 2, y // 2 * 2
        mode = m.get("mode", "blur")
        if mode == "box":
            out.append(f"drawbox=x={x}:y={y}:w={w}:h={h}:color={m.get('color', 'black')}@1:t=fill")
        elif mode == "delogo":
            # delogo exige une zone strictement intérieure à l'image
            dx, dy = max(1, x), max(1, y)
            dw, dh = min(w, sw - dx - 1), min(h, sh - dy - 1)
            out.append(f"delogo=x={dx}:y={dy}:w={dw}:h={dh}")
        else:
            r = max(1, min(20, min(w, h) // 4))
            cr = max(1, min(r // 2, min(w, h) // 8))
            t = f"{tag}m{j}"
            out.append(f"split=2[{t}a][{t}b];[{t}b]crop={w}:{h}:{x}:{y},boxblur=luma_radius={r}:luma_power=3:"
                       f"chroma_radius={cr}:chroma_power=3[{t}c];[{t}a][{t}c]overlay={x}:{y}")
    return ",".join(out)


def video_chain(tl: Timeline, src: Source, tag: str = "", seg=None,
                others: dict[int, tuple[str, Source]] | None = None) -> str:
    """Filtres appliqués à un segment pour obtenir une image W×H au bon format.

    others : {vidéo : (étiquette d'entrée FFmpeg, source)} des autres vidéos d'une mise en page (vues empilées,
    image dans l'image)."""
    f, v = tl.format, tl.video
    W, H = f.width, f.height
    parts: list[str] = []
    sw, sh = src.width or W, src.height or H
    mk = mask_chain(getattr(v, "masks", None) or [], sw, sh, f"s{tag}")
    if mk:
        parts.append(mk)
    rot = v.rotation % 360
    if rot == 90:
        parts.append("transpose=1")
        sw, sh = sh, sw
    elif rot == 180:
        parts.append("hflip,vflip")
    elif rot == 270:
        parts.append("transpose=2")
        sw, sh = sh, sw
    flips = (["hflip"] if v.mirror else []) + (["vflip"] if v.flip else [])
    lay = compose.norm(getattr(v, "layout", None))
    if lay["kind"] != "single":
        dur = seg.duration if seg is not None else 0.0
        return _composite_chain(tl, lay, parts, flips, sw, sh, tag, others or {}, dur) + "," + _look_chain(tl, tag)
    parts += flips

    zoom = video_zoom(tl) * max(1.0, float(getattr(seg, "zoom", 1.0) or 1.0))
    ox, oy = max(-1.0, min(1.0, v.offset_x)), max(-1.0, min(1.0, v.offset_y))

    keys = list(getattr(seg, "reframe", None) or []) if seg is not None and rot in (0, 180) else []
    pan = float(getattr(v, "pan", 0.0) or 0.0) > 0 and seg is not None and not keys

    def fill_chain(z: float, follow: bool = True) -> str:
        if follow and pan:
            z *= pan_zoom(tl)
        s = max(W / sw, H / sh) * z
        SW, SH = max(W, _even(sw * s)), max(H, _even(sh * s))
        x = int((SW - W) / 2 * (1 + ox))
        y = int((SH - H) / 2 * (1 + oy))
        if follow and keys and SW > W:
            from .reframe import crop_x_expr
            k = [(t, (1 - c) if v.mirror else c) for t, c in keys]
            return f"scale={SW}:{SH}:flags=lanczos,crop={W}:{H}:x='{crop_x_expr(k, seg.src_in, seg.src_out, SW, W)}':y={y}"
        if follow and pan and SW > W:
            # mouvement subtil : le cadre glisse lentement d'un bord à l'autre pendant l'extrait (sens alterné)
            d = max(0.1, seg.duration)
            p = f"min(1,max(0,t/{_fmt(d)}))"
            back = int(tag) % 2 == 1 if str(tag).isdigit() else False
            return f"scale={SW}:{SH}:flags=lanczos,crop={W}:{H}:x='{SW - W}*{'(1-' + p + ')' if back else p}':y={y}"
        return f"scale={SW}:{SH}:flags=lanczos,crop={W}:{H}:{x}:{y}"

    def fit_chain(z: float, cover: bool = False) -> str:
        s = (max(W / sw, H / sh) if cover else min(W / sw, H / sh)) * z
        SW, SH = _even(sw * s), _even(sh * s)
        chain = f"scale={SW}:{SH}:flags=lanczos"
        if SW > W or SH > H:
            cw, ch = min(SW, W), min(SH, H)
            chain += f",crop={cw}:{ch}:{int((SW - cw) / 2 * (1 + ox))}:{int((SH - ch) / 2 * (1 + oy))}"
            SW, SH = cw, ch
        px = int((W - SW) / 2 * (1 + ox))
        py = int((H - SH) / 2 * (1 + oy))
        return f"{chain},pad={W}:{H}:{px}:{py}:black"

    base = ",".join(parts)
    pre = (base + ",") if base else ""
    if f.fit == "fit":
        geo = pre + fit_chain(zoom)
    elif f.fit == "blur":
        # fond : version recadrée et floutée ; premier plan : image entière centrée
        s = min(W / sw, H / sh) * zoom
        SW, SH = min(W, _even(sw * s)), min(H, _even(sh * s))
        geo = (f"{pre}split=2[bg{tag}][fg{tag}];[bg{tag}]{fill_chain(1.0, False)},boxblur=luma_radius=40:luma_power=2,"
               f"eq=brightness=-0.08[bgb{tag}];[fg{tag}]scale={SW}:{SH}:flags=lanczos[fgs{tag}];"
               f"[bgb{tag}][fgs{tag}]overlay={int((W - SW) / 2 * (1 + ox))}:{int((H - SH) / 2 * (1 + oy))}")
    elif zoom < 1.0:                        # zoom sous 100 % : la vidéo réduite, du noir autour
        geo = pre + fit_chain(zoom, cover=True)
    else:
        geo = pre + fill_chain(zoom)
    tilt =float(getattr(v, "tilt", 0.0) or 0.0)
    if abs(tilt) > 1e-3:                    # légère rotation, sans coins vides (image déjà agrandie)
        c = tilt_cover(tl)
        geo += (f",scale={_even(W * c)}:{_even(H * c)}:flags=lanczos,rotate={_fmt(round(math.radians(tilt), 5))}"
                f":ow=iw:oh=ih:c=black,crop={W}:{H}")
    return geo + "," + _look_chain(tl, tag)


def _composite_chain(tl: Timeline, lay: dict, parts: list[str], flips: list[str], sw: int, sh: int, tag: str,
                     others: dict[int, tuple[str, Source]], dur: float = 0.0) -> str:
    """Vues empilées ou incrustation : chaque vue (vidéo du montage ou autre vidéo) est rognée, recadrée dans sa
    zone, puis les vues sont assemblées. Une même source est dupliquée pour l'image seulement (le son est traité à
    part, une seule fois)."""
    W, H, fps = tl.format.width, tl.format.height, _fmt(tl.format.fps)
    n = compose.count(lay)
    zs = compose.zones(lay, W, H)
    t = f"c{tag}"
    pre = ",".join(parts)
    flip = "".join(f"{x}," for x in flips)
    border = int(lay.get("border") or 0) * W // 1080 if lay["kind"] == "pip" else 0
    oth = compose.others(lay)
    # vidéo absente des sources : la vidéo du montage à sa place
    srcs = [x if x == 0 or oth[x - 1] in others else 0 for x in (compose.view_source(lay, i) for i in range(n))]
    out = []

    def fan(head: str, uses: list[int]) -> None:
        """Une source vers les vues qui la montrent (dupliquée si plusieurs) ; inutilisée : absorbée."""
        if not uses:
            out.append(f"{head}nullsink")
        elif len(uses) == 1:
            out.append(f"{head}null[{t}s{uses[0]}]")
        else:
            out.append(f"{head}split={len(uses)}" + "".join(f"[{t}s{i}]" for i in uses))

    # même origine des temps que les autres vidéos (sinon une image très courte peut ne rien donner)
    fan((pre + "," if pre else "") + "setpts=PTS-STARTPTS,", [i for i in range(n) if srcs[i] == 0])
    dims = {0: (sw, sh)}
    for k, aid in enumerate(oth, start=1):
        label_, s2, *rest = others.get(aid, (None, None))
        if label_ is None:
            continue
        dims[k] = (s2.width or W, s2.height or H)
        hold = f"trim=duration={_fmt(dur)}," if dur > 0 else ""   # vidéo plus courte : dernière image tenue
        wait = float(rest[0]) if rest else 0.0                    # pas encore commencée : première image tenue
        lead = f"start_mode=clone:start_duration={_fmt(round(wait, 3))}:" if wait > 0 else ""
        fan(f"{label_}fps={fps},tpad={lead}stop_mode=clone:stop=-1,{hold}setpts=PTS-STARTPTS,",
            [i for i in range(n) if srcs[i] == k])
    for i in range(n):
        zx, zy, zw, zh = zs[i]
        b = border if i == 1 else 0
        iw, ih = zw - 2 * b, zh - 2 * b
        view = lay["views"][i]
        fw, fh = dims.get(srcs[i], (sw, sh))
        cw, ch, cx, cy = compose.source_crop(fw, fh, view)
        rog = f"crop={cw}:{ch}:{cx}:{cy}," if compose.cropped(view) else ""
        SW, SH, x, y = compose.view_crop(cw, ch, iw, ih, view)
        pad = f",pad={zw}:{zh}:{b}:{b}:color={lay.get('border_color') or '#FFFFFF'}" if b else ""
        own = flip if srcs[i] == 0 else ""
        fit = f"crop={iw}:{ih}:{x}:{y}"
        if x < 0 or y < 0:                  # vidéo réduite dans sa zone : marge noire autour
            kw, kh = min(SW, iw), min(SH, ih)
            fit = f"crop={kw}:{kh}:{max(0, x)}:{max(0, y)},pad={iw}:{ih}:{max(0, -x)}:{max(0, -y)}:black"
        out.append(f"[{t}s{i}]{rog}scale={SW}:{SH}:flags=lanczos,{fit},{own}setsar=1,"
                   f"format=yuv420p{pad}[{t}z{i}]")
    if lay["kind"] == "pip":
        zx, zy = zs[1][0], zs[1][1]
        out.append(f"[{t}z0][{t}z1]overlay={zx}:{zy}:shortest=1")
    elif lay["kind"] == "grid":
        pos = "|".join(f"{zx}_{zy}" for zx, zy, _w, _h in zs)
        out.append("".join(f"[{t}z{i}]" for i in range(n)) + f"xstack=inputs={n}:layout={pos}:shortest=1")
    else:
        out.append("".join(f"[{t}z{i}]" for i in range(n)) + f"vstack=inputs={n}:shortest=1")
    return ";".join(out)


def _look_chain(tl: Timeline, tag: str = "") -> str:
    f, v = tl.format, tl.video
    look = []
    name = getattr(v, "look", "") or ""
    amount = max(0.0, min(1.0, float(getattr(v, "look_amount", 0.7) or 0.0)))
    if name in LOOKS and amount > 0:        # filtre mêlé à l'image d'origine selon son intensité
        t = f"lk{tag}"
        look.append(f"format=yuv420p,split=2[{t}a][{t}b];[{t}b]{LOOKS[name][1]},format=yuv420p[{t}f];"
                    f"[{t}a][{t}f]blend=all_expr='A*{_fmt(1 - amount)}+B*{_fmt(amount)}'")
    lut = getattr(v, "lut", "") or ""
    if lut and Path(lut).is_file():
        look.append(f"lut3d=file={_filter_path(lut)}")
    if v.brightness or v.contrast != 1.0 or v.saturation != 1.0:
        look.append(f"eq=brightness={_fmt(v.brightness)}:contrast={_fmt(v.contrast)}:saturation={_fmt(v.saturation)}")
    if int(v.temperature) != 6500:
        look.append(f"colortemperature=temperature={int(v.temperature)}")
    if v.sharpen > 0:
        look.append(f"unsharp=5:5:{_fmt(round(v.sharpen * 1.5, 3))}:5:5:0")
    sigma = blur_sigma(tl)
    if sigma > 0:
        look.append(f"gblur=sigma={_fmt(sigma)}")
    look += [f"fps={_fmt(f.fps)}", "setsar=1", "format=yuv420p", "setpts=PTS-STARTPTS"]
    return ",".join(look)


# -- construction du graphe -------------------------------------------------------
class GraphBuilder:
    def __init__(self, tl: Timeline, sources: dict[int, Source], music: Source | None,
                 text_images: list[tuple[TextItem, Path]], subs: tuple[list[str], int] | None = None):
        self.tl, self.sources, self.music, self.text_images = tl, sources, music, text_images
        self.subs = subs  # (arguments d'entrée FFmpeg, position verticale de la bande)
        self.inputs: list[list[str]] = []
        self.graph: list[str] = []
        self.t_base = 0.0           # instant du montage où commence le premier segment (aperçu d'une image)

    def _afade(self, i: int, afade: str) -> str:
        if not afade:
            return f"[a{i}]"
        self.graph.append(f"[a{i}]{afade}[af{i}]")
        return f"[af{i}]"

    def _input(self, args: list[str]) -> int:
        self.inputs.append(args)
        return len(self.inputs) - 1

    def build(self, *, video: bool = True, audio: bool = True, normalize: dict | None | bool = None,
              analyze: bool = False) -> tuple[list[str], str | None, str | None]:
        tl = self.tl
        total = tl.duration
        vlabels, alabels = [], []
        lay = compose.norm(getattr(tl.video, "layout", None))
        oth = [aid for aid in compose.others(lay) if aid in self.sources]     # autres vidéos des vues
        t0 = self.t_base
        for i, seg in enumerate(tl.segments):
            src = self.sources[seg.asset_id]
            dur = seg.duration
            k = self._input(["-ss", _fmt(seg.src_in), "-t", _fmt(dur), "-i", src.path])
            ko, wait = {}, {}           # autres vidéos : leur piste (instant du montage), sinon le même instant
            for aid in oth:
                at = compose.other_time(lay, aid, seg.src_in, t0)
                wait[aid] = min(dur, max(0.0, -at))
                ko[aid] = self._input(["-ss", _fmt(round(max(0.0, at), 3)), "-t",
                                       _fmt(round(max(0.05, dur - wait[aid]), 3)), "-i", self.sources[aid].path])
            t0 += dur
            fi, fo = seg.fades() if hasattr(seg, "fades") else (0.0, 0.0)
            vfade = (f",fade=t=in:st=0:d={_fmt(fi)}" if fi else "") + \
                (f",fade=t=out:st={_fmt(dur - fo)}:d={_fmt(fo)}" if fo else "")
            afade = (f"afade=t=in:st=0:d={_fmt(fi)}" if fi else "") + ("," if fi and fo else "") + \
                (f"afade=t=out:st={_fmt(dur - fo)}:d={_fmt(fo)}" if fo else "")
            if video:
                others = {aid: (f"[{kk}:v:0]", self.sources[aid], wait[aid]) for aid, kk in ko.items()}
                self.graph.append(f"[{k}:v:0]{video_chain(tl, src, str(i), seg, others)}{vfade}[v{i}]")
                vlabels.append(f"[v{i}]")
            if audio:
                vs = getattr(tl, "voice", None)
                if vs is not None and vs.mode == "dub" and src.dub_path:
                    d = self._input(["-ss", _fmt(seg.src_in), "-t", _fmt(dur), "-i", src.dub_path])
                    vol = "volume=0" if seg.muted else f"volume={_fmt(seg.volume_db + vs.dub_gain_db)}dB"
                    self.graph.append(
                        f"[{d}:a:0]aresample={SAMPLE_RATE},aformat=sample_fmts=fltp:channel_layouts=stereo,"
                        f"{vol},asetpts=PTS-STARTPTS,apad,atrim=0:{_fmt(dur)}[ad{i}]")
                    if vs.background == "ambience" and src.music_stem and not seg.muted:
                        b = self._input(["-ss", _fmt(seg.src_in), "-t", _fmt(dur), "-i", src.music_stem])
                        self.graph.append(
                            f"[{b}:a:0]aresample={SAMPLE_RATE},aformat=sample_fmts=fltp:channel_layouts=stereo,"
                            f"volume={_fmt(seg.volume_db + vs.ambience_db)}dB,asetpts=PTS-STARTPTS,apad,"
                            f"atrim=0:{_fmt(dur)}[ao{i}]")
                        self.graph.append(f"[ad{i}][ao{i}]amix=inputs=2:duration=first:normalize=0:"
                                          f"dropout_transition=0[a{i}]")
                    elif src.has_audio and vs.original_level_db > -59 and not seg.muted:
                        self.graph.append(
                            f"[{k}:a:0]aresample={SAMPLE_RATE},aformat=sample_fmts=fltp:channel_layouts=stereo,"
                            f"volume={_fmt(seg.volume_db + vs.original_level_db)}dB,asetpts=PTS-STARTPTS,apad,"
                            f"atrim=0:{_fmt(dur)}[ao{i}]")
                        self.graph.append(f"[ad{i}][ao{i}]amix=inputs=2:duration=first:normalize=0:"
                                          f"dropout_transition=0[a{i}]")
                    else:
                        self.graph.append(f"[ad{i}]anull[a{i}]")
                    alabels.append(self._afade(i, afade))
                    continue
                ain = f"[{k}:a:0]"
                if vs is not None and vs.mode != "dub" and vs.isolate and src.voice_stem:
                    ain = f"[{self._input(['-ss', _fmt(seg.src_in), '-t', _fmt(dur), '-i', src.voice_stem])}:a:0]"
                fmt_a = f"aresample={SAMPLE_RATE},aformat=sample_fmts=fltp:channel_layouts=stereo"
                vol = "volume=0" if seg.muted else f"volume={_fmt(seg.volume_db)}dB"
                # son des autres vidéos : la première (« second ») ou toutes (« both »), mêlées au besoin
                heard = [(kk, wait[aid]) for aid, kk in ko.items() if self.sources[aid].has_audio]
                heard = heard[:1] if lay["audio"] == "second" else heard if lay["audio"] == "both" else []
                if heard:
                    ins = [] if lay["audio"] == "second" or not src.has_audio else [(ain, 0.0)]
                    ins += [(f"[{kk}:a:0]", w) for kk, w in heard]
                    for j, (a_, w) in enumerate(ins):
                        late = f"adelay={int(w * 1000)}:all=1," if w > 0 else ""
                        self.graph.append(f"{a_}{fmt_a},asetpts=PTS-STARTPTS,{late}apad,atrim=0:{_fmt(dur)}"
                                          f"[am{i}x{j}]")
                    mix = "".join(f"[am{i}x{j}]" for j in range(len(ins)))
                    self.graph.append(f"{mix}amix=inputs={len(ins)}:duration=first:normalize=0:dropout_transition=0,"
                                      f"{vol}[a{i}]" if len(ins) > 1 else f"{mix}{vol}[a{i}]")
                elif src.has_audio:
                    self.graph.append(
                        f"{ain}{fmt_a},{vol},asetpts=PTS-STARTPTS,apad,atrim=0:{_fmt(dur)}[a{i}]")
                else:
                    z = self._input(["-f", "lavfi", "-t", _fmt(dur), "-i",
                                     f"anullsrc=r={SAMPLE_RATE}:cl=stereo"])
                    self.graph.append(f"[{z}:a]aformat=sample_fmts=fltp:channel_layouts=stereo[a{i}]")
                alabels.append(self._afade(i, afade))
        n = len(tl.segments)
        vout = aout = None
        if video:
            if n > 1:
                self.graph.append("".join(vlabels) + f"concat=n={n}:v=1:a=0[vcat]")
                cur = "[vcat]"
            else:
                cur = vlabels[0]
            if self.subs:
                args_in, sy = self.subs
                k = self._input(args_in)
                self.graph.append(f"[{k}:v]format=rgba[subs]")
                self.graph.append(f"{cur}[subs]overlay=0:{sy}:eof_action=pass:repeatlast=0[vsub]")
                cur = "[vsub]"
            for j, (item, png) in enumerate(self.text_images):
                p = self._input(["-loop", "1", "-framerate", _fmt(tl.format.fps), "-t", _fmt(total), "-i",
                                 str(png)])
                chain = "format=rgba"
                if item.fade > 0:
                    fd = min(item.fade, (item.end - item.start) / 2)
                    chain += (f",fade=t=in:st={_fmt(item.start)}:d={_fmt(fd)}:alpha=1"
                              f",fade=t=out:st={_fmt(item.end - fd)}:d={_fmt(fd)}:alpha=1")
                self.graph.append(f"[{p}:v]{chain}[t{j}]")
                self.graph.append(f"{cur}[t{j}]overlay=0:0:eof_action=pass:"
                                  f"enable='between(t,{_fmt(item.start)},{_fmt(item.end)})'[vt{j}]")
                cur = f"[vt{j}]"
            sp = speed_of(tl)                   # vitesse : toute l'image d'un coup (textes compris, en temps)
            self.graph.append(f"{cur}{'setpts=PTS/' + _fmt(sp) if sp != 1.0 else 'null'}[vout]")
            vout = "[vout]"
        if audio:
            a = tl.audio
            if n > 1:
                self.graph.append("".join(alabels) + f"concat=n={n}:v=0:a=1[acat]")
                cur = "[acat]"
            else:
                cur = alabels[0]
            chain = []
            if a.highpass:
                chain.append("highpass=f=80")
            if a.denoise:
                chain.append(f"afftdn=nr={_fmt(max(1.0, min(40.0, a.denoise_amount)))}:nf=-40")
            if getattr(a, "level_speakers", False):
                # nivellement dynamique (fenêtres ~2 s) : compense les écarts entre intervenants
                chain.append("dynaudnorm=f=250:g=15:p=0.9:m=12:s=8")
            if getattr(a, "presence", False):
                chain.append("equalizer=f=3000:t=q:w=1.2:g=2.5")
            if getattr(a, "deess", False):
                chain.append("deesser=i=0.4:m=0.5:f=0.5")
            if getattr(a, "compress", False):
                chain.append("acompressor=threshold=-20dB:ratio=3:attack=10:release=150:makeup=2")
            if a.gain_db:
                chain.append(f"volume={_fmt(a.gain_db)}dB")
            self.graph.append(f"{cur}{','.join(chain) or 'anull'}[voice]")
            cur = "[voice]"
            m = tl.music
            if self.music and m.asset_id:
                args = (["-stream_loop", "-1"] if m.loop else []) + ["-ss", _fmt(m.src_offset), "-i", self.music.path]
                k = self._input(args)
                mdur = max(0.1, total - m.start)
                mchain = (f"[{k}:a:0]aresample={SAMPLE_RATE},aformat=sample_fmts=fltp:channel_layouts=stereo,"
                          f"atrim=0:{_fmt(mdur)},asetpts=PTS-STARTPTS,volume={_fmt(m.volume_db)}dB")
                if m.fade_in > 0:
                    mchain += f",afade=t=in:st=0:d={_fmt(m.fade_in)}"
                if m.fade_out > 0:
                    mchain += f",afade=t=out:st={_fmt(max(0.0, mdur - m.fade_out))}:d={_fmt(m.fade_out)}"
                if m.start > 0:
                    ms = int(m.start * 1000)
                    mchain += f",adelay={ms}|{ms}"
                self.graph.append(mchain + "[mus]")
                if m.duck:
                    self.graph.append(f"{cur}asplit=2[vmain][vsc]")
                    self.graph.append(f"[mus][vsc]sidechaincompress=threshold=0.02:ratio="
                                      f"{_fmt(max(2.0, min(20.0, m.duck_amount)))}:attack=20:release=500:"
                                      f"makeup=1[musd]")
                    self.graph.append("[vmain][musd]amix=inputs=2:duration=first:normalize=0:dropout_transition=0[mix]")
                else:
                    self.graph.append(f"{cur}[mus]amix=inputs=2:duration=first:normalize=0:dropout_transition=0[mix]")
                cur = "[mix]"
            master = []
            if a.fade_in > 0:
                master.append(f"afade=t=in:st=0:d={_fmt(a.fade_in)}")
            if a.fade_out > 0:
                master.append(f"afade=t=out:st={_fmt(max(0.0, total - a.fade_out))}:d={_fmt(a.fade_out)}")
            if analyze:
                master.append("volumedetect")
                master.append("silencedetect=noise=-45dB:d=2.5")
                master.append("ebur128=peak=true")
            elif a.normalize and normalize is not False:
                ln = f"loudnorm=I={_fmt(a.target_lufs)}:TP={_fmt(a.true_peak)}:LRA=11"
                if isinstance(normalize, dict):
                    ln += (f":measured_I={normalize['input_i']}:measured_TP={normalize['input_tp']}"
                           f":measured_LRA={normalize['input_lra']}:measured_thresh={normalize['input_thresh']}"
                           f":offset={normalize['target_offset']}:linear=true")
                else:
                    ln += ":print_format=json"
                master.append(ln)
                master.append(f"aresample={SAMPLE_RATE}")
            # sécurité : aucune saturation introduite par le logiciel
            if not analyze:
                master.append(f"alimiter=limit={_fmt(round(10 ** (min(a.true_peak, -0.5) / 20), 4))}:"
                              "level=disabled:attack=5:release=50")
            master.append(f"atrim=0:{_fmt(total)}")
            if speed_of(tl) != 1.0 and not analyze:
                master.append(f"atempo={_fmt(speed_of(tl))}")     # même vitesse, voix à la même hauteur
            self.graph.append(f"{cur}{','.join(master)}[aout]")
            aout = "[aout]"
        args = [x for inp in self.inputs for x in inp]
        return args, vout, aout


# -- utilitaires d'exécution ---------------------------------------------------------
def _run(cmd: list[str], timeout: float | None = None) -> subprocess.CompletedProcess:
    log.debug("ffmpeg %s", " ".join(cmd))
    return subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace",
                          timeout=timeout, creationflags=_NOWIN)


def _base() -> list[str]:
    return [ffmpeg.ffmpeg_bin(), "-hide_banner", "-nostdin", "-y"]


_MAJOR: int | None = None


def ffmpeg_major() -> int:
    global _MAJOR
    if _MAJOR is None:
        try:
            out = _run([ffmpeg.ffmpeg_bin(), "-hide_banner", "-version"], timeout=20).stdout
            m = re.search(r"version n?(\d+)\.", out)
            _MAJOR = int(m.group(1)) if m else 7
        except Exception:
            _MAJOR = 7
    return _MAJOR


def _write_graph(tmp: Path, graph: list[str]) -> list[str]:
    """Le graphe est passé par fichier : évite la limite de longueur de ligne de commande sous Windows."""
    script = tmp / "graph.txt"
    script.write_text(";\n".join(graph), encoding="utf-8")
    if ffmpeg_major() >= 7:
        return ["-/filter_complex", str(script)]        # syntaxe FFmpeg 7+
    return ["-filter_complex_script", str(script)]       # FFmpeg 6 et antérieurs


def encoder_available(name: str) -> bool:
    try:
        out = _run([ffmpeg.ffmpeg_bin(), "-hide_banner", "-encoders"], timeout=20).stdout
    except Exception:
        return False
    return re.search(rf"\s{re.escape(name)}\s", out) is not None


def _text_images(tl: Timeline, tmp: Path) -> list[tuple[TextItem, Path]]:
    if not tl.export.burn_texts:
        return []
    out = []
    for i, t in enumerate(tl.texts):
        if t.text.strip() and t.end > t.start:
            out.append((t, render_text(t, tl.format.width, tl.format.height, tmp / f"text_{i}.png")))
    return out


def _parse_loudnorm(stderr: str) -> dict:
    m = list(re.finditer(r"\{[^{}]*\"input_i\"[^{}]*\}", stderr, re.S))
    if not m:
        raise RenderError("Mesure de loudness impossible :\n" + stderr[-600:])
    d = json.loads(m[-1].group(0))
    # valeurs -inf (silence total) : la normalisation n'a pas de sens
    if "inf" in str(d.get("input_i", "")):
        raise RenderError("SILENCE")
    return d


def parse_ebur128(stderr: str) -> dict:
    out: dict = {}
    summary = stderr[stderr.rfind("Summary:"):] if "Summary:" in stderr else stderr
    for key, rx in (("lufs", r"I:\s+(-?[\d.]+|-inf) LUFS"), ("lra", r"LRA:\s+(-?[\d.]+) LU"),
                    ("true_peak", r"Peak:\s+(-?[\d.]+|-inf) dBFS")):
        m = re.search(rx, summary)
        if m:
            out[key] = float(m.group(1)) if "inf" not in m.group(1) else float("-inf")
    for key, rx in (("mean_volume", r"mean_volume:\s+(-?[\d.]+) dB"), ("max_volume", r"max_volume:\s+(-?[\d.]+) dB")):
        m = re.search(rx, stderr)
        if m:
            out[key] = float(m.group(1))
    out["silences"] = [(float(a), float(b)) for a, b in
                       re.findall(r"silence_start: (-?[\d.]+)[\s\S]*?silence_end: (-?[\d.]+)", stderr)]
    return out


# -- API --------------------------------------------------------------------------
def analyze_audio(tl: Timeline, sources: dict[int, Source], music: Source | None) -> dict:
    """Analyse le mixage (avant normalisation) et signale les problèmes (§12.1)."""
    if not tl.segments:
        raise RenderError("Timeline vide.")
    gb = GraphBuilder(tl, sources, music, [])
    with tempfile.TemporaryDirectory(prefix="sv-ana-") as d:
        args, _, aout = gb.build(video=False, audio=True, analyze=True)
        cmd = _base() + ["-loglevel", "info"] + args + _write_graph(Path(d), gb.graph) + \
            ["-map", aout, "-f", "null", "-"]
        proc = _run(cmd, timeout=max(120, tl.duration * 4))
    if proc.returncode != 0:
        raise RenderError("Analyse audio impossible :\n" + proc.stderr[-800:])
    m = parse_ebur128(proc.stderr)
    issues = []
    lufs, tp, mx = m.get("lufs"), m.get("true_peak"), m.get("max_volume")
    if lufs is None or lufs == float("-inf"):
        issues.append("Aucun son détecté.")
    else:
        if lufs < -30:
            issues.append(f"Volume global très faible ({lufs:.1f} LUFS).")
        elif lufs < -20:
            issues.append(f"Volume global faible ({lufs:.1f} LUFS) : la normalisation le corrigera.")
        if lufs > -9:
            issues.append(f"Volume global très fort ({lufs:.1f} LUFS).")
    if (tp is not None and tp > -0.1) or (mx is not None and mx >= -0.1):
        issues.append("Saturation possible : des crêtes atteignent 0 dBFS (écrêtage).")
    long_sil = [s for s in m["silences"] if s[1] - s[0] >= 2.5]
    if long_sil:
        issues.append(f"{len(long_sil)} silence(s) de plus de 2,5 s (ex. à {long_sil[0][0]:.1f} s).")
    m["issues"] = issues
    return m


def preview_frame(tl: Timeline, sources: dict[int, Source], t: float, dest: Path, max_height: int = 960) -> Path:
    """Image fidèle (format, recadrage, couleurs, textes) au temps montage t."""
    loc = tl.locate(t)
    if loc is None:
        raise RenderError("Timeline vide.")
    i, src_t = loc
    seg = tl.segments[i]
    one = tl.copy()
    step = 1.0 / max(1.0, tl.format.fps)
    import dataclasses
    fi, fo = seg.fades() if hasattr(seg, "fades") else (0.0, 0.0)
    offset, fin, fout = 0.0, 0.0, 0.0
    if fi and src_t - seg.src_in < fi:                       # dans un fondu : on rend le fondu puis on prend l'image
        a, b, fin = seg.src_in, min(seg.src_out, seg.src_in + 2 * fi + step), fi
        offset = src_t - a
    elif fo and src_t > seg.src_out - fo:
        a, b, fout = max(seg.src_in, seg.src_out - 2 * fo), seg.src_out, fo
        offset = min(src_t, seg.src_out - step) - a
    else:
        a = max(seg.src_in, min(src_t, seg.src_out - step))
        b = a + step * 2
    one.segments = [dataclasses.replace(seg, src_in=a, src_out=b, fade_in=fin, fade_out=fout)
                    if dataclasses.is_dataclass(seg) else seg]
    one.texts = []
    for item in tl.texts:
        if item.start <= t <= item.end:
            c = type(item)(**{**item.__dict__})
            c.start, c.end, c.fade = 0.0, 10.0, 0.0
            one.texts.append(c)
    one.export.burn_texts = True
    with tempfile.TemporaryDirectory(prefix="sv-prev-") as d:
        tmp = Path(d)
        subs = None
        st = tl.subtitles
        if st.enabled and st.burn and st.cues:
            from .subrender import render_state, strip_geometry
            from .subtitles import states
            cur = next((s_ for a, b, s_ in states(st, tl.duration, tl.format.fps) if a <= t < b), None)
            if cur is not None and cur.cue_index >= 0:
                png = tmp / "sub.png"
                render_state(st, cur, one.format.width, one.format.height).save(str(png), "PNG")
                subs = (["-loop", "1", "-t", "1", "-i", str(png)], strip_geometry(st.style, one.format.width,
                                                                            one.format.height)[0])
        one.subtitles = type(st)(enabled=False)
        gb = GraphBuilder(one, sources, None, _text_images(one, tmp), subs)
        gb.t_base = tl.segment_start(i) + (a - seg.src_in)       # pistes vidéo 2 à 4 : le bon instant
        args, vout, _ = gb.build(video=True, audio=False)
        scale_h = min(max_height, one.format.height)
        pick = f"trim=start={_fmt(offset)},setpts=PTS-STARTPTS," if offset > 0 else ""
        gb.graph.append(f"{vout}{pick}scale=-2:{scale_h}[pv]")
        cmd = _base() + ["-loglevel", "error"] + args + _write_graph(tmp, gb.graph) + \
            ["-map", "[pv]", "-frames:v", "1", str(dest)]
        dest.parent.mkdir(parents=True, exist_ok=True)
        proc = _run(cmd, timeout=120)
    if proc.returncode != 0 or not dest.exists():
        raise RenderError("Aperçu impossible :\n" + proc.stderr[-800:])
    return dest


def output_path(tl: Timeline, project_name: str, default_dir: Path) -> Path:
    d = Path(tl.export.directory) if tl.export.directory else default_dir
    safe = re.sub(r'[<>:"/\\|?*\x00-\x1f]+', "_", project_name).strip() or "video"
    name = tl.export.file_pattern.format(project=safe, aspect=tl.format.aspect.replace(":", "x"),
                                         date=datetime.now().strftime("%Y%m%d-%H%M%S"),
                                         w=tl.format.width, h=tl.format.height)
    p = d / f"{name}.mp4"
    k = 2
    while p.exists():
        p = d / f"{name}-{k}.mp4"
        k += 1
    return p


def export(tl: Timeline, sources: dict[int, Source], music: Source | None, dest: Path,
           run: Callable[[list[str], Callable[[str], None]], int],
           progress: Callable[[float, str], None] = lambda f, m: None) -> dict:
    """Exporte le montage en MP4 et vérifie le résultat. Ne simule jamais un succès.

    `run(cmd, on_line)` exécute FFmpeg (fourni par le gestionnaire de tâches pour
    permettre l'annulation) et lève une exception si le code de retour est non nul.
    """
    total = tl.duration
    if total <= 0:
        raise RenderError("La timeline est vide.")
    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = Path(tempfile.mkdtemp(prefix="sv-export-"))
    partial = dest.with_name(dest.stem + ".partial.mp4")
    try:
        progress(0.01, "Préparation des textes")
        texts = _text_images(tl, tmp)
        subs = None
        if tl.subtitles.enabled and tl.subtitles.burn and tl.subtitles.cues:
            from .subrender import write_overlay
            progress(0.02, "Préparation des sous-titres")
            ov = write_overlay(tl.subtitles, total, tl.format.width, tl.format.height, tl.format.fps, tmp / "subs")
            if ov:
                subs = (["-f", "concat", "-safe", "0", "-i", str(ov[0])], ov[1])
        measured = None
        if tl.audio.normalize:
            progress(0.03, "Mesure du volume (passe 1/2)")
            gb = GraphBuilder(tl, sources, music, [])
            args, _, aout = gb.build(video=False, audio=True, normalize=True)
            p1 = tmp / "pass1"
            p1.mkdir()
            cmd = _base() + ["-loglevel", "info"] + args + _write_graph(p1, gb.graph) + \
                ["-map", aout, "-f", "null", "-"]
            lines: list[str] = []
            run(cmd, lines.append)
            try:
                measured = _parse_loudnorm("".join(lines))
            except RenderError as exc:
                if str(exc) != "SILENCE":
                    raise
                measured = False  # piste silencieuse : pas de normalisation

        progress(0.1, "Rendu vidéo (passe 2/2)")
        gb = GraphBuilder(tl, sources, music, texts, subs)
        args, vout, aout = gb.build(video=True, audio=True,
                                    normalize=(measured if measured is not None else False))
        e = tl.export
        if e.codec == "h265" and encoder_available("libx265"):
            venc = ["-c:v", "libx265", "-crf", str(e.crf), "-preset", e.preset, "-tag:v", "hvc1"]
        elif encoder_available("libx264"):
            venc = ["-c:v", "libx264", "-crf", str(e.crf), "-preset", e.preset, "-profile:v", "high"]
        elif encoder_available("h264_mf"):
            venc = ["-c:v", "h264_mf", "-b:v", f"{max(4, int(tl.format.width * tl.format.height / 200000))}M"]
        else:
            raise RenderError("Aucun encodeur H.264 disponible dans FFmpeg.")
        cmd = _base() + ["-loglevel", "error", "-progress", "pipe:2", "-nostats"] + args + \
            _write_graph(tmp, gb.graph) + ["-map", vout, "-map", aout] + venc + \
            ["-pix_fmt", "yuv420p", "-r", _fmt(tl.format.fps),
             "-c:a", "aac", "-b:a", f"{e.audio_bitrate}k", "-ar", str(SAMPLE_RATE), "-ac", "2",
             "-movflags", "+faststart", "-metadata", "comment=Studio Video",
             "-t", _fmt(output_duration(tl)), str(partial)]

        def on_line(line: str) -> None:
            if line.startswith("out_time_us=") or line.startswith("out_time_ms="):
                try:
                    us = int(line.split("=", 1)[1])
                except ValueError:
                    return
                progress(0.1 + 0.82 * min(1.0, us / 1e6 / output_duration(tl)), "Rendu vidéo")

        run(cmd, on_line)
        progress(0.93, "Vérification du fichier")
        report = verify(partial, tl)
        partial.replace(dest)
        report["path"] = str(dest)
        if tl.subtitles.enabled and tl.subtitles.sidecar_srt and tl.subtitles.cues:
            from .subtitles import to_srt
            srt = dest.with_suffix(".srt")
            srt.write_text(to_srt(tl.subtitles.cues), encoding="utf-8")
            report["srt"] = str(srt)
        progress(1.0, "Export terminé")
        return report
    finally:
        partial.unlink(missing_ok=True)
        shutil.rmtree(tmp, ignore_errors=True)


def verify(path: Path, tl: Timeline) -> dict:
    """Contrôle réel du fichier produit (§18.5 : contrôle audio final)."""
    if not path.exists() or path.stat().st_size == 0:
        raise RenderError("Le fichier exporté est absent ou vide.")
    info = ffmpeg.probe(path)
    problems = []
    if not info.has_video:
        problems.append("aucune piste vidéo")
    if info.audio_tracks < 1:
        problems.append("aucune piste audio")
    if (info.width, info.height) != (tl.format.width, tl.format.height):
        problems.append(f"dimensions {info.width}×{info.height} au lieu de {tl.format.width}×{tl.format.height}")
    expected = output_duration(tl)
    if info.duration_s is None or abs(info.duration_s - expected) > max(0.35, 2 / tl.format.fps):
        problems.append(f"durée {info.duration_s or 0:.2f} s au lieu de {expected:.2f} s")
    if problems:
        raise RenderError("Export invalide : " + ", ".join(problems) + ".")
    proc = _run(_base() + ["-loglevel", "info", "-i", str(path), "-map", "0:a:0",
                           "-af", "ebur128=peak=true", "-f", "null", "-"], timeout=max(60, expected * 2))
    loud = parse_ebur128(proc.stderr) if proc.returncode == 0 else {}
    warnings = []
    tp = loud.get("true_peak")
    if tp is not None and tp > -0.1:
        warnings.append(f"Crête vraie à {tp:.1f} dBFS : risque de saturation.")
    lufs = loud.get("lufs")
    if tl.audio.normalize and lufs not in (None, float("-inf")) and abs(lufs - tl.audio.target_lufs) > 1.5:
        warnings.append(f"Volume final {lufs:.1f} LUFS (cible {tl.audio.target_lufs:.0f}).")
    return {"width": info.width, "height": info.height, "duration": info.duration_s, "fps": info.fps,
            "video_codec": info.video_codec, "audio_codec": info.audio_codec,
            "size_bytes": path.stat().st_size, "lufs": lufs, "true_peak": tp, "warnings": warnings}
