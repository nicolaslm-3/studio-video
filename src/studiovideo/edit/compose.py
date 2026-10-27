"""Mise en page de l'image : plein écran, double cadrage (deux vues d'une même source empilées), deux sources
différentes empilées, image dans l'image.

La mise en page est une description (`VideoSettings.layout`, un dict) ; ce module en tire la géométrie utilisée à la
fois par le rendu FFmpeg et par l'aperçu en direct, pour que l'aperçu corresponde au fichier exporté.

Chaque vue remplit sa zone : `cx`, `cy` (0..1) désignent le point de la source placé au centre de la zone (borné
pour ne jamais montrer de bord noir), `zoom` (≥ 1) resserre le cadre.
"""
from __future__ import annotations

import copy
import math

KINDS = {
    "single": "Plein écran",
    "split": "Double cadrage (même vidéo)",
    "split2": "Deux vidéos haut / bas",
    "pip": "Image dans l'image",
}
AUDIO = {"main": "Son de la vidéo principale", "second": "Son de la seconde vidéo", "both": "Les deux sons"}

DEFAULT = {
    "kind": "single",
    "ratio": 0.5,             # part de la hauteur occupée par la zone du haut (écrans divisés)
    "swap": False,            # inverser haut et bas
    "views": [{"cx": 0.25, "cy": 0.5, "zoom": 1.3}, {"cx": 0.75, "cy": 0.5, "zoom": 1.3}],
    "asset2": None,           # seconde vidéo (deux vidéos, image dans l'image)
    "offset": 0.0,            # décalage de la seconde vidéo (s) : elle commence à « début de l'extrait + offset »
    "audio": "main",          # son conservé quand il y a deux vidéos ; jamais doublé pour une même source
    "pip": {"x": 0.58, "y": 0.06, "w": 0.38, "h": 0.12},   # fractions de l'image (≈ 16:9 dans un 9:16)
    "border": 0,              # contour de l'incrustation (px pour une image de 1080 de large)
    "border_color": "#FFFFFF",
}
RATIO_MIN, RATIO_MAX = 0.2, 0.8


def _clamp(v: float, a: float, b: float) -> float:
    return max(a, min(b, v))


def norm(layout: dict | None) -> dict:
    """Mise en page complète (valeurs par défaut pour les clés absentes, bornes respectées)."""
    out = copy.deepcopy(DEFAULT)
    for k, v in (layout or {}).items():
        if k in ("views", "pip"):
            continue
        out[k] = v
    for i, v in enumerate((layout or {}).get("views") or []):
        if i < 2 and isinstance(v, dict):
            out["views"][i].update(v)
    out["pip"].update((layout or {}).get("pip") or {})
    if out["kind"] not in KINDS:
        out["kind"] = "single"
    if out["audio"] not in AUDIO:
        out["audio"] = "main"
    out["ratio"] = _clamp(float(out["ratio"]), RATIO_MIN, RATIO_MAX)
    for v in out["views"]:
        v["cx"], v["cy"] = _clamp(float(v["cx"]), 0.0, 1.0), _clamp(float(v["cy"]), 0.0, 1.0)
        v["zoom"] = _clamp(float(v["zoom"]), 1.0, 6.0)
    p = out["pip"]
    p["w"], p["h"] = _clamp(float(p["w"]), 0.1, 1.0), _clamp(float(p["h"]), 0.06, 1.0)
    p["x"], p["y"] = _clamp(float(p["x"]), 0.0, 1.0 - p["w"]), _clamp(float(p["y"]), 0.0, 1.0 - p["h"])
    return out


def active(layout: dict | None) -> bool:
    return norm(layout)["kind"] != "single"


def uses_second(layout: dict | None) -> bool:
    """La seconde vue vient-elle d'une autre vidéo ?"""
    lay = norm(layout)
    return lay["kind"] in ("split2", "pip") and lay["asset2"] is not None


def _even_floor(v: float) -> int:
    n = int(v)
    return n - (n % 2)


def _even_ceil(v: float) -> int:
    n = int(math.ceil(v - 1e-9))
    return n + (n % 2)


def zones(layout: dict | None, W: int, H: int) -> list[tuple[int, int, int, int]]:
    """Zones (x, y, w, h) en pixels de sortie, dans l'ordre des vues (vue 1, vue 2). Dimensions paires."""
    lay = norm(layout)
    if lay["kind"] == "pip":
        p = lay["pip"]
        w, h = max(16, _even_floor(p["w"] * W)), max(16, _even_floor(p["h"] * H))
        x, y = _even_floor(p["x"] * W), _even_floor(p["y"] * H)
        return [(0, 0, W, H), (min(x, W - w), min(y, H - h), w, h)]
    if lay["kind"] in ("split", "split2"):
        top = _clamp(_even_floor(lay["ratio"] * H), 2, H - 2)
        a, b = (0, 0, W, top), (0, top, W, H - top)
        return [b, a] if lay["swap"] else [a, b]
    return [(0, 0, W, H)]


def view_crop(sw: int, sh: int, zw: int, zh: int, view: dict) -> tuple[int, int, int, int]:
    """Source sw×sh mise à l'échelle (SW×SH, paires) puis recadrée à zw×zh depuis (x, y) : (SW, SH, x, y)."""
    s = max(zw / sw, zh / sh) * max(1.0, float(view.get("zoom", 1.0)))
    SW, SH = max(zw, _even_ceil(sw * s)), max(zh, _even_ceil(sh * s))
    x = int(_clamp(float(view.get("cx", 0.5)) * SW - zw / 2, 0, SW - zw))
    y = int(_clamp(float(view.get("cy", 0.5)) * SH - zh / 2, 0, SH - zh))
    return SW, SH, x, y


def center_for(sw: int, sh: int, zw: int, zh: int, view: dict, x: float, y: float) -> tuple[float, float]:
    """Inverse de view_crop : centre (cx, cy) qui place le coin du cadre en (x, y) (déplacement à la souris)."""
    SW, SH, _x, _y = view_crop(sw, sh, zw, zh, view)
    return _clamp((x + zw / 2) / SW, 0.0, 1.0), _clamp((y + zh / 2) / SH, 0.0, 1.0)
