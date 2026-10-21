"""« Optimiser l'audio » (cahier des charges §12.4).

1. Analyse chaque extrait de la timeline (volume intégré, plage de volume, crête, bruit de fond).
2. Choisit des traitements : équilibrage des extraits, nivellement des intervenants, débruitage,
   compression légère, musique abaissée et atténuée sous la voix, contrôle de crête.
3. Rend un rapport lisible. Les réglages modifiés restent visibles et modifiables dans l'onglet
   Audio, et l'opération s'annule avec Ctrl+Z (traitement transparent et réversible).
"""
from __future__ import annotations

import re
import statistics
import subprocess
import sys
from dataclasses import dataclass, field

from ..media import ffmpeg
from .model import Timeline
from .render import Source, parse_ebur128

_NOWIN = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0


@dataclass
class SegmentMeasure:
    seg_id: str
    index: int
    lufs: float | None
    lra: float | None
    true_peak: float | None
    noise_floor: float | None
    has_audio: bool


@dataclass
class Plan:
    audio: dict
    music: dict
    seg_volumes: dict[str, float]
    report: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    measures: list[dict] = field(default_factory=list)


def measure_segment(src: Source, a: float, d: float) -> tuple[float | None, float | None, float | None, float | None]:
    cmd = [ffmpeg.ffmpeg_bin(), "-hide_banner", "-nostdin", "-loglevel", "info", "-ss", f"{a:.3f}", "-t", f"{d:.3f}",
           "-i", src.path, "-map", "0:a:0", "-af", "aresample=48000,ebur128=peak=true,astats=measure_perchannel=none",
           "-f", "null", "-"]
    p = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace",
                       timeout=max(60, d * 3), creationflags=_NOWIN)
    if p.returncode != 0:
        return None, None, None, None
    m = parse_ebur128(p.stderr)
    nf = None
    found = re.findall(r"Noise floor dB:\s*(-?[\d.]+|-inf)", p.stderr)
    if found and "inf" not in found[-1]:
        nf = float(found[-1])
    lufs = m.get("lufs")
    return (None if lufs in (None, float("-inf")) else lufs), m.get("lra"), m.get("true_peak"), nf


def analyze(tl: Timeline, sources: dict[int, Source], progress=lambda f, m: None) -> list[SegmentMeasure]:
    out = []
    for i, s in enumerate(tl.segments):
        progress(0.05 + 0.85 * i / max(1, len(tl.segments)), f"Analyse de l'extrait {i + 1}")
        src = sources[s.asset_id]
        if not src.has_audio or s.muted:
            out.append(SegmentMeasure(s.id, i, None, None, None, None, False))
            continue
        lufs, lra, tp, nf = measure_segment(src, s.src_in, s.duration)
        out.append(SegmentMeasure(s.id, i, lufs, lra, tp, nf, lufs is not None))
    return out


def plan(tl: Timeline, measures: list[SegmentMeasure], has_music: bool) -> Plan:
    a = dict(tl.audio.__dict__)
    m = dict(tl.music.__dict__)
    seg_vol: dict[str, float] = {}
    rep: list[str] = []
    warn: list[str] = []
    voiced = [x for x in measures if x.has_audio and x.lufs is not None and x.lufs > -60]
    if not voiced:
        return Plan(a, m, seg_vol, ["Aucun son exploitable dans les extraits : rien à optimiser."], [],
                    [x.__dict__ for x in measures])

    # 1. équilibrage des extraits entre eux
    med = statistics.median(x.lufs for x in voiced)
    for x in voiced:
        diff = med - x.lufs
        if abs(diff) >= 2.0:
            g = round(max(-12.0, min(12.0, diff)) * 2) / 2
            seg_vol[x.seg_id] = g
            rep.append(f"Extrait {x.index + 1} : {g:+.1f} dB ({'voix plus faible' if g > 0 else 'voix plus forte'} "
                       "que les autres).")
        elif tl.segments[x.index].volume_db:
            seg_vol[x.seg_id] = 0.0
    # 2. écarts à l'intérieur des extraits (intervenants éloignés du micro, interpellations…)
    lras = [x.lra for x in voiced if x.lra is not None]
    max_lra = max(lras) if lras else 0.0
    if max_lra > 9:
        a["level_speakers"] = True
        rep.append(f"Nivellement des intervenants activé (écarts de volume jusqu'à {max_lra:.0f} LU dans un extrait).")
    if max_lra > 7:
        a["compress"] = True
        rep.append("Compression légère de la voix (volume plus régulier).")
    # 3. bruit de fond
    floors = [x.noise_floor for x in voiced if x.noise_floor is not None]
    nf = max(floors) if floors else None
    if nf is not None and -58 < nf <= -35:
        amount = round(max(6.0, min(20.0, (nf + 72) / 2)))
        a["denoise"], a["denoise_amount"] = True, float(amount)
        rep.append(f"Réduction du bruit de fond ({amount} dB, bruit mesuré à {nf:.0f} dBFS).")
    elif nf is not None and nf > -35:
        # un « fond » aussi fort est en général de la musique ou une ambiance voulue, pas un souffle
        warn.append(f"Fond sonore continu détecté ({nf:.0f} dBFS : musique ou ambiance ?). La réduction de bruit "
                    "n'est pas appliquée pour ne pas l'abîmer ; activez-la à la main si c'est bien du bruit.")
    if not a.get("highpass"):
        a["highpass"] = True
        rep.append("Coupure des graves parasites (< 80 Hz).")
    a["presence"] = True
    rep.append("Légère accentuation de l'intelligibilité de la voix (2-4 kHz).")
    # 4. musique
    if has_music:
        changed = []
        if m.get("volume_db", -14) > -16:
            m["volume_db"] = -16.0
            changed.append("abaissée à -16 dB")
        if not m.get("duck"):
            m["duck"] = True
            changed.append("atténuée automatiquement quand on parle")
        m["duck_amount"] = max(8.0, float(m.get("duck_amount", 8.0)))
        rep.append("Musique : " + (", ".join(changed) if changed else "déjà réglée sous la voix") + ".")
    # 5. volume final et crêtes
    a["normalize"] = True
    a["true_peak"] = min(-1.0, float(a.get("true_peak", -1.0)))
    rep.append(f"Volume final normalisé à {a.get('target_lufs', -14):.0f} LUFS, crêtes limitées à "
               f"{a['true_peak']:.1f} dBTP.")
    for x in voiced:
        if x.true_peak is not None and x.true_peak > -0.1:
            warn.append(f"Extrait {x.index + 1} : la source est saturée (écrêtage). Le logiciel n'en ajoute pas, "
                        "mais ne peut pas entièrement réparer un son déjà écrêté.")
    return Plan(a, m, seg_vol, rep, warn, [x.__dict__ for x in measures])


def optimize(tl: Timeline, sources: dict[int, Source], music: Source | None,
             progress=lambda f, m: None) -> Plan:
    measures = analyze(tl, sources, progress)
    progress(0.95, "Choix des traitements")
    return plan(tl, measures, music is not None and bool(tl.music.asset_id))


def apply_plan(tl: Timeline, p: dict | Plan) -> None:
    d = p if isinstance(p, dict) else p.__dict__
    for k, v in d["audio"].items():
        if hasattr(tl.audio, k):
            setattr(tl.audio, k, v)
    for k, v in d["music"].items():
        if hasattr(tl.music, k) and k != "asset_id":
            setattr(tl.music, k, v)
    for s in tl.segments:
        if s.id in d["seg_volumes"]:
            s.volume_db = d["seg_volumes"][s.id]
