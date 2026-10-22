"""Repérage du texte incrusté et des logos (V2, §9) — propose des zones à masquer.

Méthode (sur ~30 images réparties dans l'extrait, en niveaux de gris 320 px) :
- logo / filigrane : contours présents au même endroit sur presque toutes les images alors que
  l'image change (intensité stable, contours permanents), près d'un bord ;
- sous-titres incrustés : bandes horizontales très riches en contours dans la moitié basse, qui
  apparaissent et disparaissent au fil des images.
Ce n'est qu'une proposition : chaque zone se vérifie dans l'aperçu fidèle avant export.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import numpy as np

from ..media import ffmpeg

_NOWIN = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
W, H = 320, 180


def sample_frames(path: Path, start: float, end: float, n: int = 30, src_w: int = 16, src_h: int = 9) -> np.ndarray:
    """(n, h, W) en niveaux de gris, à la proportion de la source (hauteur arrondie paire)."""
    h = max(2, int(round(W * src_h / max(1, src_w) / 2)) * 2)
    dur = max(0.1, end - start)
    fps = max(0.2, n / dur)
    p = subprocess.run([ffmpeg.ffmpeg_bin(), "-v", "error", "-nostdin", "-ss", f"{start:.3f}", "-t", f"{dur:.3f}",
                        "-i", str(path), "-vf", f"fps={fps:.4f},scale={W}:{h}:flags=area,format=gray",
                        "-frames:v", str(n), "-f", "rawvideo", "-"], capture_output=True, creationflags=_NOWIN)
    if p.returncode != 0:
        raise RuntimeError(f"Lecture des images impossible : {p.stderr[-300:].decode('utf-8', 'replace')}")
    frames = np.frombuffer(p.stdout, np.uint8)
    k = frames.size // (W * h)
    return frames[:k * W * h].reshape(k, h, W).astype(np.float32)


def _edges(f: np.ndarray, t: float = 40.0) -> np.ndarray:
    dx = np.zeros_like(f, dtype=bool)
    dy = np.zeros_like(f, dtype=bool)
    dx[..., :, 1:] = np.abs(np.diff(f, axis=-1)) > t
    dy[..., 1:, :] = np.abs(np.diff(f, axis=-2)) > t
    return dx | dy


def _box_mean(a: np.ndarray, r: int) -> np.ndarray:
    """Moyenne glissante (2r+1)² par sommes cumulées."""
    p = np.pad(a.astype(np.float32), r + 1, mode="edge")
    c = p.cumsum(0).cumsum(1)
    k = 2 * r + 1
    s = c[k:, k:] - c[:-k, k:] - c[k:, :-k] + c[:-k, :-k]
    return s[:a.shape[0], :a.shape[1]] / (k * k)


def _components(mask: np.ndarray) -> list[tuple[int, int, int, int]]:
    """Boîtes (x0, y0, x1, y1) des composantes connexes (4-voisinage)."""
    h, w = mask.shape
    seen = np.zeros_like(mask, dtype=bool)
    boxes = []
    for y0 in range(h):
        for x0 in range(w):
            if not mask[y0, x0] or seen[y0, x0]:
                continue
            stack = [(y0, x0)]
            seen[y0, x0] = True
            ys, xs = [], []
            while stack:
                y, x = stack.pop()
                ys.append(y)
                xs.append(x)
                for ny, nx in ((y + 1, x), (y - 1, x), (y, x + 1), (y, x - 1)):
                    if 0 <= ny < h and 0 <= nx < w and mask[ny, nx] and not seen[ny, nx]:
                        seen[ny, nx] = True
                        stack.append((ny, nx))
            boxes.append((min(xs), min(ys), max(xs) + 1, max(ys) + 1))
    return boxes


def detect(frames: np.ndarray) -> list[dict]:
    """Zones proposées : {"x","y","w","h" (0..1), "mode", "kind": "logo"|"subtitles", "confidence"}."""
    n, h, w = frames.shape
    if n < 4:
        return []
    e = _edges(frames)
    persist = _edges(frames, 28.0).mean(axis=0)               # part des images où le contour est présent
    stable = frames.std(axis=0) < 10                          # intensité qui ne bouge pas
    moving = (frames.std(axis=0) > 3).mean() > 0.25           # l'image change sur une bonne partie du cadre
    zones: list[dict] = []
    # -- logos / filigranes : contours permanents et stables près d'un bord
    if moving:
        logo = (_box_mean((persist > 0.8) & stable, 2) > 0.15)
        for x0, y0, x1, y1 in _components(logo):
            bw, bh = x1 - x0, y1 - y0
            area = bw * bh / (w * h)
            near_edge = min(x0, y0, w - x1, h - y1) < 0.25 * min(w, h) + 4
            if 0.002 < area < 0.12 and near_edge and bw >= 6 and bh >= 4:
                pad = 3
                zones.append({"kind": "logo", "mode": "blur", "confidence": round(float(persist[y0:y1, x0:x1].mean()), 2),
                              "x": max(0, x0 - pad) / w, "y": max(0, y0 - pad) / h,
                              "w": (min(w, x1 + pad) - max(0, x0 - pad)) / w,
                              "h": (min(h, y1 + pad) - max(0, y0 - pad)) / h})
    # -- sous-titres : bandes denses en contours dans la moitié basse, intermittentes
    lo, hi = int(0.10 * w), int(0.90 * w)
    dens = e[:, :, lo:hi].mean(axis=2)                        # (n, h)
    dens = np.stack([np.convolve(d, np.ones(3) / 3, mode="same") for d in dens])
    base = np.median(dens, axis=1, keepdims=True)
    text_rows = dens > np.maximum(0.10, base * 2.5)           # (n, h)
    text_rows[:, : h // 2] = False
    static_rows = (persist[:, lo:hi] > 0.85).mean(axis=1) > 0.05
    text_rows[:, static_rows] = False
    frac = text_rows.mean(axis=0)                             # part des images avec du texte à cette ligne
    rows = np.where((frac > 0.12) & (frac < 0.97))[0]
    if rows.size:
        # plus grand groupe de lignes contiguës (tolérance 3 px)
        groups, cur = [], [rows[0]]
        for r in rows[1:]:
            if r - cur[-1] <= 3:
                cur.append(r)
            else:
                groups.append(cur)
                cur = [r]
        groups.append(cur)
        g = max(groups, key=len)
        y0, y1 = g[0], g[-1] + 1
        if 0.025 * h <= (y1 - y0) <= 0.35 * h:
            active = text_rows[:, y0:y1].any(axis=1)
            cols = e[active][:, y0:y1, :].mean(axis=(0, 1))
            xs = np.where(cols > max(0.02, cols.max() * 0.15))[0]
            x0, x1 = (int(xs[0]), int(xs[-1]) + 1) if xs.size else (lo, hi)
            pad_y, pad_x = max(2, (y1 - y0) // 4), 6
            zones.append({"kind": "subtitles", "mode": "blur", "confidence": round(float(frac[y0:y1].max()), 2),
                          "x": max(0, x0 - pad_x) / w, "y": max(0, y0 - pad_y) / h,
                          "w": (min(w, x1 + pad_x) - max(0, x0 - pad_x)) / w,
                          "h": (min(h, y1 + pad_y) - max(0, y0 - pad_y)) / h})
    for z in zones:
        for k in ("x", "y", "w", "h"):
            z[k] = round(float(z[k]), 4)
    return zones


def detect_file(path: Path, start: float, end: float, width: int, height: int) -> list[dict]:
    return detect(sample_frames(path, start, end, 30, width or 16, height or 9))
