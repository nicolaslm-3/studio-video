"""Recadrage intelligent (§8.3) : le cadre suit les visages quand on passe du 16:9 au 9:16 (ou 1:1, 4:5).

- Détection : UltraFace RFB-320 (licence MIT, 1,2 Mo, ONNX), téléchargé une fois, exécuté sur le PC.
- Qui parle : à plusieurs à l'image, le cadre suit la personne dont la bouche bouge le plus (image recalée
  d'abord, pour qu'un mouvement de caméra ou de tête ne compte pas), avec un temps de maintien pour ne pas
  sauter d'un visage à l'autre à chaque syllabe ; après un changement de plan, le cadre se replace aussitôt
  sur celui qui parle dans les secondes qui suivent. Limite : quelqu'un qui écoute en bougeant la main
  devant la bouche peut être pris pour celui qui parle.
- Suivi : un visage par instant (celui qui parle, sinon le plus grand et le plus net, en privilégiant la
  continuité), zone morte (le cadre ne bouge pas pour de petits déplacements), vitesse limitée, et changement de plan
  franc quand le visage suivi change brusquement de place pendant plus d'une demi-seconde.
- Résultat : des points clés (temps source, centre horizontal 0..1), simplifiés, rendus par FFmpeg en
  interpolation linéaire et rejoués dans l'aperçu.
"""
from __future__ import annotations

import hashlib
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Callable

import numpy as np

from ..media import ffmpeg

_NOWIN = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
MODEL_URL = ("https://raw.githubusercontent.com/Linzaer/Ultra-Light-Fast-Generic-Face-Detector-1MB/master/"
             "models/onnx/version-RFB-320.onnx")
MODEL_SHA = "34cd7e60aeff28744c657de7a3dc64e872d506741de66987f3426f2b79f88017"
IW, IH = 320, 240


class ReframeError(RuntimeError):
    pass


def model_path(models_dir: Path) -> Path:
    return Path(models_dir) / "faces" / "version-RFB-320.onnx"


def ensure_model(models_dir: Path) -> Path:
    p = model_path(models_dir)
    if p.exists():
        return p
    p.parent.mkdir(parents=True, exist_ok=True)
    try:
        with urllib.request.urlopen(MODEL_URL, timeout=60) as r:
            data = r.read()
    except (OSError, urllib.error.URLError) as exc:
        raise ReframeError(f"Téléchargement du détecteur de visages impossible : {exc}") from exc
    if hashlib.sha256(data).hexdigest() != MODEL_SHA:
        raise ReframeError("Détecteur de visages corrompu (empreinte différente).")
    tmp = p.with_suffix(".partial")
    tmp.write_bytes(data)
    tmp.replace(p)
    return p


class FaceDetector:
    def __init__(self, models_dir: Path):
        try:
            import onnxruntime as ort
        except Exception as exc:
            raise ReframeError("onnxruntime n'est pas installé dans cette version.") from exc
        so = ort.SessionOptions()
        so.log_severity_level = 3
        self.sess = ort.InferenceSession(str(ensure_model(models_dir)), so, providers=["CPUExecutionProvider"])

    def detect(self, rgb: np.ndarray, threshold: float = 0.7) -> list[tuple[float, float, float, float, float]]:
        """rgb : (240, 320, 3) uint8 → [(x1, y1, x2, y2, score)] normalisés 0..1."""
        x = ((rgb.astype(np.float32) - 127.0) / 128.0).transpose(2, 0, 1)[None]
        scores, boxes = self.sess.run(None, {"input": x})
        sc, bx = scores[0, :, 1], boxes[0]
        keep = sc > threshold
        return nms(np.clip(bx[keep], 0, 1), sc[keep])


def nms(boxes: np.ndarray, scores: np.ndarray, iou: float = 0.3) -> list[tuple]:
    order = np.argsort(-scores)
    out = []
    while order.size:
        i = order[0]
        out.append((*map(float, boxes[i]), float(scores[i])))
        if order.size == 1:
            break
        b = boxes[order[1:]]
        xx1, yy1 = np.maximum(boxes[i, 0], b[:, 0]), np.maximum(boxes[i, 1], b[:, 1])
        xx2, yy2 = np.minimum(boxes[i, 2], b[:, 2]), np.minimum(boxes[i, 3], b[:, 3])
        inter = np.clip(xx2 - xx1, 0, None) * np.clip(yy2 - yy1, 0, None)
        a = (boxes[i, 2] - boxes[i, 0]) * (boxes[i, 3] - boxes[i, 1])
        ab = (b[:, 2] - b[:, 0]) * (b[:, 3] - b[:, 1])
        order = order[1:][inter / (a + ab - inter + 1e-9) < iou]
    return out


def mouth_activity(prev: np.ndarray | None, cur: np.ndarray, box: tuple, shift: int = 4) -> float:
    """Mouvement du bas du visage entre deux images (niveaux de gris), une fois l'image précédente recalée
    (décalage de ±`shift` pixels qui la fait le mieux coïncider) : un mouvement de caméra, un zoom ou une tête
    qui se déplace sont ainsi compensés, il reste la bouche qui s'ouvre et se ferme. Mesure relative, à
    comparer entre les visages d'un même instant."""
    if prev is None:
        return 0.0
    h, w = cur.shape
    x1, y1, x2, y2 = box[:4]
    bw, bh = x2 - x1, y2 - y1
    xa, xb = max(shift, int((x1 + 0.2 * bw) * w)), min(w - shift, int((x1 + 0.8 * bw) * w))
    ya, yb = max(shift, int((y1 + 0.6 * bh) * h)), min(h - shift, int((y1 + 1.0 * bh) * h))
    if xb - xa < 2 or yb - ya < 2:
        return 0.0
    c = cur[ya:yb, xa:xb]
    return min(float(np.abs(c - prev[ya + dy:yb + dy, xa + dx:xb + dx]).mean())
               for dy in range(-shift, shift + 1, 2) for dx in range(-shift, shift + 1, 2))


def speaker_centers(times: list[float], detections: list[list[tuple]], activity: list[list[float]],
                    window: float = 1.2, hold: float = 0.8, ratio: float = 1.35,
                    min_level: float = 3.0) -> list[float | None]:
    """Centre horizontal de la personne qui parle, image par image (None : aucun visage).

    Les visages sont suivis d'une image à l'autre par proximité. Pour chacun, le mouvement de la bouche est
    moyenné sur `window` secondes ; on passe à un autre visage s'il parle nettement plus (`ratio`) pendant
    au moins `hold` secondes. Sans parole visible, on reste sur le même visage ; s'il quitte l'image
    (changement de plan), on passe aussitôt à celui qui parle à l'image, sinon au plus grand."""
    n = len(times)
    if n == 0:
        return []
    step = (times[-1] - times[0]) / max(1, n - 1) or 0.25
    tracks: list[dict] = []                         # {"x": dernier centre, "last": dernière image, "act": {i: a}}
    ids: list[list[int]] = []
    for i, dets in enumerate(detections):
        row: list[int] = []
        for k, d in enumerate(dets):
            c = (d[0] + d[2]) / 2
            near = [t for t in range(len(tracks)) if t not in row and i - tracks[t]["last"] <= 3 / step
                    and abs(tracks[t]["x"] - c) < 0.12]
            if near:
                tid = min(near, key=lambda t: abs(tracks[t]["x"] - c))
            else:
                tid = len(tracks)
                tracks.append({"x": c, "last": i, "act": {}})
            tracks[tid].update(x=c, last=i)
            tracks[tid]["act"][i] = activity[i][k] if i < len(activity) and k < len(activity[i]) else 0.0
            row.append(tid)
        ids.append(row)
    half = max(1, int(round(window / step / 2)))
    smooth = np.zeros((len(tracks), n))
    for tid, tr in enumerate(tracks):
        raw = np.zeros(n)
        for i, a in tr["act"].items():
            raw[i] = a
        smooth[tid] = np.convolve(np.pad(raw, (half, half)), np.ones(2 * half + 1) / (2 * half + 1), mode="valid")
    need = max(1, int(round(hold / step)))
    look = max(1, int(round(2.0 / step)))
    out: list[float | None] = []
    cur, challenger, streak, miss = None, None, 0, 0
    pos: dict[int, float] = {}
    for i in range(n):
        here = ids[i]
        for k, tid in enumerate(here):
            d = detections[i][k]
            pos[tid] = (d[0] + d[2]) / 2
        if not here:
            out.append(pos.get(cur) if cur is not None else None)
            continue
        best = max(here, key=lambda t: smooth[t, i])
        miss = miss + 1 if cur is not None and cur not in here else 0
        if cur is None or miss >= 2:                # premier visage, ou visage suivi sorti de l'image
            # nouveau plan : celui qui parle le plus dans les secondes qui suivent (un seul instant trompe :
            # une main portée au visage bouge autant qu'une bouche)
            def ahead_level(t: int) -> float:
                vals = [a for j, a in tracks[t]["act"].items() if i <= j <= i + look]
                return sum(vals) / len(vals) if vals else 0.0
            lead = max(here, key=ahead_level)
            if ahead_level(lead) >= min_level or len(here) == 1:
                cur = lead
            else:                                   # personne ne parle encore : le plus grand visage
                cur = max(here, key=lambda t: _area(detections[i][here.index(t)]))
            challenger, streak, miss = None, 0, 0
        elif best != cur and smooth[best, i] >= min_level and smooth[best, i] > ratio * smooth[cur, i]:
            streak = streak + 1 if challenger == best else 1
            challenger = best
            if streak >= need:
                cur, challenger, streak = best, None, 0
        else:
            challenger, streak = None, 0
        out.append(pos[cur])
    return out


def _area(d: tuple) -> float:
    return (d[2] - d[0]) * (d[3] - d[1])


def track(times: list[float], detections: list[list[tuple]], dead_zone: float = 0.06,
          max_speed: float = 0.35, jump: float = 0.2,
          centers: list[float | None] | None = None) -> list[tuple[float, float]]:
    """Trajectoire du cadre (temps source, centre horizontal 0..1), calculée sur tout l'extrait.

    - plans séparés aux sauts brusques du visage suivi (changement de plan) : coupe franche du cadre ;
    - dans un plan où le visage bouge peu : cadre fixe (comme un cadreur) ;
    - sinon : trajectoire lissée (≈ 1,25 s), vitesse limitée à max_speed largeur/seconde.
    """
    if not times:
        return []
    raw: list[float | None] = []
    prev = None
    for k, dets in enumerate(detections):
        if centers is not None:                     # visage choisi en amont (la personne qui parle)
            raw.append(centers[k] if dets else None)
            continue
        if not dets:
            raw.append(None)
            continue

        def score(d):
            area = (d[2] - d[0]) * (d[3] - d[1])
            c = (d[0] + d[2]) / 2
            near = 1.0 if prev is None else max(0.2, 1 - abs(c - prev) * 2)
            return area * d[4] * near
        best = max(dets, key=score)
        prev = (best[0] + best[2]) / 2
        raw.append(prev)
    known = [c for c in raw if c is not None]
    if not known:
        return [(times[0], 0.5), (times[-1], 0.5)]
    filled, last = [], known[0]
    for c in raw:                                   # sans visage : on garde la dernière position
        last = c if c is not None else last
        filled.append(last)
    v = np.array(filled, dtype=float)
    t = np.array(times, dtype=float)
    cuts = [0] + [i for i in range(1, len(v)) if abs(v[i] - v[i - 1]) > jump] + [len(v)]
    out: list[tuple[float, float]] = []
    for a, b in zip(cuts, cuts[1:]):
        seg, ts = v[a:b], t[a:b]
        if seg.max() - seg.min() <= 2 * dead_zone:
            path = np.full(len(seg), float(np.median(seg)))
        else:
            k = min(5, len(seg))
            pad = np.pad(seg, (k // 2, k - 1 - k // 2), mode="edge")
            path = np.convolve(pad, np.ones(k) / k, mode="valid")
            for i in range(1, len(path)):           # vitesse limitée
                step = max_speed * max(ts[i] - ts[i - 1], 1e-3)
                path[i] = path[i - 1] + max(-step, min(step, path[i] - path[i - 1]))
        if out and a > 0:                           # coupe franche juste avant le nouveau plan
            out.append((float(ts[0]) - 0.001, out[-1][1]))
        out += [(float(x), float(y)) for x, y in zip(ts, path)]
    return simplify(out)


def simplify(points: list[tuple[float, float]], tol: float = 0.01) -> list[tuple[float, float]]:
    """Ramer-Douglas-Peucker : garde les points utiles (moins de points clés à rendre)."""
    if len(points) <= 2:
        return [(round(t, 3), round(c, 4)) for t, c in points]
    p = np.array(points)

    def rdp(a: int, b: int) -> list[int]:
        if b <= a + 1:
            return [a]
        t0, c0 = p[a]
        t1, c1 = p[b]
        seg = p[a + 1:b]
        lin = c0 + (c1 - c0) * (seg[:, 0] - t0) / max(1e-9, t1 - t0)
        err = np.abs(seg[:, 1] - lin)
        k = int(np.argmax(err))
        if err[k] <= tol:
            return [a]
        m = a + 1 + k
        return rdp(a, m) + rdp(m, b)
    keep = rdp(0, len(p) - 1) + [len(p) - 1]
    return [(round(float(p[i, 0]), 3), round(float(p[i, 1]), 4)) for i in keep]


def stream_frames(path: Path, start: float, end: float, fps: float = 8.0, w: int = 2 * IW, h: int = 2 * IH):
    """Images (temps source, rgb h×w) lues une à une : la mémoire reste faible même sur un long extrait."""
    dur = max(0.1, end - start)
    p = subprocess.Popen([ffmpeg.ffmpeg_bin(), "-v", "error", "-nostdin", "-ss", f"{start:.3f}", "-t", f"{dur:.3f}",
                          "-i", str(path), "-vf", f"fps={fps:g},scale={w}:{h}:flags=area,format=rgb24",
                          "-f", "rawvideo", "-"], stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                         creationflags=_NOWIN)
    size, i = w * h * 3, 0
    try:
        while True:
            buf = p.stdout.read(size)
            if len(buf) < size:
                break
            yield start + i / fps, np.frombuffer(buf, np.uint8).reshape(h, w, 3)
            i += 1
    finally:
        p.stdout.close()
        err = p.stderr.read()
        p.stderr.close()
        p.wait()
    if p.returncode != 0 and i == 0:
        raise ReframeError(f"Lecture des images impossible : {err[-300:].decode('utf-8', 'replace')}")


def analyze(path: Path, start: float, end: float, models_dir: Path,
            progress: Callable[[float, str], None] = lambda f, m: None,
            is_cancelled: Callable[[], bool] = lambda: False, fps: float = 8.0) -> dict:
    det = FaceDetector(models_dir)
    progress(0.1, "Lecture des images")
    total = max(1, int((end - start) * fps))
    times: list[float] = []
    found: list[list[tuple]] = []
    acts: list[list[float]] = []
    prev = None
    for i, (t, f) in enumerate(stream_frames(path, start, end, fps)):
        if is_cancelled():
            raise ReframeError("Annulé.")
        dets = det.detect(np.ascontiguousarray(f[::2, ::2]))
        gray = f.mean(axis=2, dtype=np.float32)
        if prev is not None and float(np.abs(gray[::8, ::8] - prev[::8, ::8]).mean()) > 40:
            prev = None                             # changement de plan : pas de mouvement à mesurer
        times.append(t)
        found.append(dets)
        acts.append([mouth_activity(prev, gray, d) for d in dets])
        prev = gray
        if i % 16 == 0:
            progress(0.15 + 0.8 * min(1.0, i / total), f"Visages : image {i + 1}/{total}")
    with_face = sum(bool(d) for d in found)
    centers = speaker_centers(times, found, acts)
    return {"keys": track(times, found, centers=centers), "frames": len(times), "with_face": with_face}


def cx_at(keys: list, t: float) -> float:
    """Centre interpolé au temps source t."""
    if not keys:
        return 0.5
    if t <= keys[0][0]:
        return keys[0][1]
    for (t0, c0), (t1, c1) in zip(keys, keys[1:]):
        if t <= t1:
            return c0 + (c1 - c0) * (t - t0) / max(1e-9, t1 - t0)
    return keys[-1][1]


def crop_x_expr(keys: list, src_in: float, src_out: float, scaled_w: int, out_w: int) -> str:
    """Expression FFmpeg de x(t) pour crop (t = temps depuis le début de l'extrait)."""
    room = max(0, scaled_w - out_w)

    def x_of(c: float) -> float:
        return max(0.0, min(room, c * scaled_w - out_w / 2))
    pts = [(max(0.0, t - src_in), x_of(c)) for t, c in keys if src_in - 1 <= t <= src_out + 1]
    if not pts:
        return str(int(x_of(cx_at(keys, src_in))))
    pts.sort()
    if pts[0][0] > 0:
        pts.insert(0, (0.0, x_of(cx_at(keys, src_in))))
    expr = f"{pts[-1][1]:.1f}"
    for (t0, x0), (t1, x1) in reversed(list(zip(pts, pts[1:]))):
        if t1 - t0 < 1e-6:
            continue
        expr = f"if(lt(t\\,{t1:.3f})\\,{x0:.1f}+(t-{t0:.3f})*{(x1 - x0) / (t1 - t0):.4f}\\,{expr})"
    return f"trunc({expr}/2)*2"
