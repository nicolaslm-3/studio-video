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


def _even(v: float) -> int:
    n = int(math.ceil(v))
    return n + (n % 2)


def _fmt(v: float) -> str:
    return f"{v:.6f}".rstrip("0").rstrip(".") if v != int(v) else str(int(v))


def _escape_path(p: str) -> str:
    return p.replace("\\", "/")


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


def video_chain(tl: Timeline, src: Source, tag: str = "") -> str:
    """Filtres appliqués à un segment pour obtenir une image W×H au bon format."""
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
    if v.mirror:
        parts.append("hflip")
    if v.flip:
        parts.append("vflip")

    zoom = max(1.0, float(v.zoom))
    ox, oy = max(-1.0, min(1.0, v.offset_x)), max(-1.0, min(1.0, v.offset_y))

    def fill_chain(z: float) -> str:
        s = max(W / sw, H / sh) * z
        SW, SH = max(W, _even(sw * s)), max(H, _even(sh * s))
        x = int((SW - W) / 2 * (1 + ox))
        y = int((SH - H) / 2 * (1 + oy))
        return f"scale={SW}:{SH}:flags=lanczos,crop={W}:{H}:{x}:{y}"

    def fit_chain(z: float) -> str:
        s = min(W / sw, H / sh) * z
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
        geo = (f"{pre}split=2[bg{tag}][fg{tag}];[bg{tag}]{fill_chain(1.0)},boxblur=luma_radius=40:luma_power=2,"
               f"eq=brightness=-0.08[bgb{tag}];[fg{tag}]scale={SW}:{SH}:flags=lanczos[fgs{tag}];"
               f"[bgb{tag}][fgs{tag}]overlay={int((W - SW) / 2 * (1 + ox))}:{int((H - SH) / 2 * (1 + oy))}")
    else:
        geo = pre + fill_chain(zoom)

    look = []
    if v.brightness or v.contrast != 1.0 or v.saturation != 1.0:
        look.append(f"eq=brightness={_fmt(v.brightness)}:contrast={_fmt(v.contrast)}:saturation={_fmt(v.saturation)}")
    if int(v.temperature) != 6500:
        look.append(f"colortemperature=temperature={int(v.temperature)}")
    if v.sharpen > 0:
        look.append(f"unsharp=5:5:{_fmt(round(v.sharpen * 1.5, 3))}:5:5:0")
    look += [f"fps={_fmt(f.fps)}", "setsar=1", "format=yuv420p", "setpts=PTS-STARTPTS"]
    return geo + "," + ",".join(look)


# -- construction du graphe -------------------------------------------------------
class GraphBuilder:
    def __init__(self, tl: Timeline, sources: dict[int, Source], music: Source | None,
                 text_images: list[tuple[TextItem, Path]], subs: tuple[list[str], int] | None = None):
        self.tl, self.sources, self.music, self.text_images = tl, sources, music, text_images
        self.subs = subs  # (arguments d'entrée FFmpeg, position verticale de la bande)
        self.inputs: list[list[str]] = []
        self.graph: list[str] = []

    def _input(self, args: list[str]) -> int:
        self.inputs.append(args)
        return len(self.inputs) - 1

    def build(self, *, video: bool = True, audio: bool = True, normalize: dict | None | bool = None,
              analyze: bool = False) -> tuple[list[str], str | None, str | None]:
        tl = self.tl
        total = tl.duration
        vlabels, alabels = [], []
        for i, seg in enumerate(tl.segments):
            src = self.sources[seg.asset_id]
            dur = seg.duration
            k = self._input(["-ss", _fmt(seg.src_in), "-t", _fmt(dur), "-i", src.path])
            if video:
                self.graph.append(f"[{k}:v:0]{video_chain(tl, src, str(i))}[v{i}]")
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
                    alabels.append(f"[a{i}]")
                    continue
                ain = f"[{k}:a:0]"
                if vs is not None and vs.mode != "dub" and vs.isolate and src.voice_stem:
                    ain = f"[{self._input(['-ss', _fmt(seg.src_in), '-t', _fmt(dur), '-i', src.voice_stem])}:a:0]"
                if src.has_audio:
                    vol = "volume=0" if seg.muted else f"volume={_fmt(seg.volume_db)}dB"
                    self.graph.append(
                        f"{ain}aresample={SAMPLE_RATE},aformat=sample_fmts=fltp:channel_layouts=stereo,"
                        f"{vol},asetpts=PTS-STARTPTS,apad,atrim=0:{_fmt(dur)}[a{i}]")
                else:
                    z = self._input(["-f", "lavfi", "-t", _fmt(dur), "-i",
                                     f"anullsrc=r={SAMPLE_RATE}:cl=stereo"])
                    self.graph.append(f"[{z}:a]aformat=sample_fmts=fltp:channel_layouts=stereo[a{i}]")
                alabels.append(f"[a{i}]")
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
            self.graph.append(f"{cur}null[vout]")
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
    start = max(seg.src_in, min(src_t, seg.src_out - step))
    one.segments = [type(seg)(asset_id=seg.asset_id, src_in=start, src_out=start + step * 2, id=seg.id)]
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
        args, vout, _ = gb.build(video=True, audio=False)
        scale_h = min(max_height, one.format.height)
        gb.graph.append(f"{vout}scale=-2:{scale_h}[pv]")
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
             "-t", _fmt(total), str(partial)]

        def on_line(line: str) -> None:
            if line.startswith("out_time_us=") or line.startswith("out_time_ms="):
                try:
                    us = int(line.split("=", 1)[1])
                except ValueError:
                    return
                progress(0.1 + 0.82 * min(1.0, us / 1e6 / total), "Rendu vidéo")

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
    expected = tl.duration
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
