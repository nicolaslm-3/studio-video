"""Mise en page de l'image : plein écran, vues empilées ou côte à côte (de 2 à 4, chacune d'une vidéo du projet ou
de la vidéo du montage), image dans l'image.

La mise en page est une description (`VideoSettings.layout`, un dict) ; ce module en tire la géométrie utilisée à la
fois par le rendu FFmpeg et par l'aperçu en direct, pour que l'aperçu corresponde au fichier exporté.

Chaque vue a sa vidéo (`asset` : None = la vidéo du montage, sinon une autre vidéo du projet), est rognée
(`crop_l`, `crop_r`, `crop_t`, `crop_b` : part de l'image retirée de chaque côté), puis remplit sa zone : `cx`, `cy`
(0..1) désignent le point de la source rognée placé au centre de la zone (borné pour ne jamais montrer de bord noir),
`zoom` resserre le cadre (> 1) ou réduit la vidéo dans sa zone, entourée de noir (< 1).

Pistes vidéo 2 à 4 de la timeline (`tracks`) : une autre vidéo chacune, qui commence à l'instant `start` du montage
(avant : sa première image, tenue, et pas de son). Une autre vidéo sans piste suit l'ancienne règle : calée sur
l'instant de la source du montage, plus `offset`.

Anciennes mises en page (« split » : la même vidéo haut / bas, « split2 » : deux vidéos, `asset2`, `swap`) : lues
comme des vues empilées.
"""
from __future__ import annotations

import copy
import math

KINDS = {
    "single": "Plein écran",
    "stack": "Vues empilées (2 à 4)",
    "grid": "Côte à côte (2 à 4)",
    "pip": "Image dans l'image",
}
AUDIO = {"main": "Son de la vidéo du montage", "second": "Son de l'autre vidéo", "both": "Tous les sons"}
MAX_VIEWS = 4
MAX_TRACKS = MAX_VIEWS - 1      # pistes vidéo 2 à 4 de la timeline
ZOOM_MIN, ZOOM_MAX = 0.2, 6.0
CROP_MAX = 0.45                 # part retirée d'un côté, au plus (il reste toujours au moins 10 % de l'image)

VIEW = {"asset": None, "cx": 0.5, "cy": 0.5, "zoom": 1.0, "crop_l": 0.0, "crop_r": 0.0, "crop_t": 0.0, "crop_b": 0.0}
DEFAULT = {
    "kind": "single",
    "count": 2,               # vues empilées : nombre de vues (2 à 4)
    "ratio": 0.5,             # deux vues : part de la hauteur occupée par celle du haut (au-delà : parts égales)
    "views": [{**VIEW, "cx": 0.25, "zoom": 1.3}, {**VIEW, "cx": 0.75, "zoom": 1.3}, dict(VIEW), dict(VIEW)],
    "offset": 0.0,            # décalage des autres vidéos (s) : elles commencent à « début de l'extrait + offset »
    "audio": "main",          # son conservé quand il y a d'autres vidéos ; jamais doublé pour une même source
    "pip": {"x": 0.58, "y": 0.06, "w": 0.38, "h": 0.12},   # fractions de l'image (≈ 16:9 dans un 9:16)
    "border": 0,              # contour de l'incrustation (px pour une image de 1080 de large)
    "border_color": "#FFFFFF",
    "tracks": [],             # pistes vidéo 2 à 4 : [{"asset", "start" (s, temps du montage)}]
}
RATIO_MIN, RATIO_MAX = 0.2, 0.8


def _clamp(v: float, a: float, b: float) -> float:
    return max(a, min(b, v))


def norm(layout: dict | None) -> dict:
    """Mise en page complète (valeurs par défaut pour les clés absentes, bornes respectées, anciennes formes
    converties)."""
    src = dict(layout or {})
    out = copy.deepcopy(DEFAULT)
    for k, v in src.items():
        if k in ("views", "pip", "asset2", "swap", "tracks"):
            continue
        out[k] = v
    for i, v in enumerate(src.get("views") or []):
        if i < MAX_VIEWS and isinstance(v, dict):
            out["views"][i].update(v)
    out["pip"].update(src.get("pip") or {})
    kind = out["kind"]
    if kind in ("split", "split2"):           # ancienne forme : deux vues haut / bas
        out["kind"], out["count"] = "stack", 2
        if kind == "split2":
            out["views"][1]["asset"] = src.get("asset2")
        if src.get("swap"):
            out["views"][0], out["views"][1] = out["views"][1], out["views"][0]
    elif kind == "pip" and "asset2" in src:
        out["views"][1]["asset"] = src.get("asset2")
    if out["kind"] not in KINDS:
        out["kind"] = "single"
    if out["audio"] not in AUDIO:
        out["audio"] = "main"
    try:
        out["count"] = int(_clamp(int(out["count"]), 2, MAX_VIEWS))
    except (TypeError, ValueError):
        out["count"] = 2
    out["ratio"] = _clamp(float(out["ratio"]), RATIO_MIN, RATIO_MAX)
    for v in out["views"]:
        v["cx"], v["cy"] = _clamp(float(v["cx"]), 0.0, 1.0), _clamp(float(v["cy"]), 0.0, 1.0)
        v["zoom"] = _clamp(float(v["zoom"]), ZOOM_MIN, ZOOM_MAX)
        for a, b in (("crop_l", "crop_r"), ("crop_t", "crop_b")):
            v[a] = _clamp(float(v.get(a) or 0.0), 0.0, CROP_MAX)
            v[b] = _clamp(float(v.get(b) or 0.0), 0.0, CROP_MAX)
        a = v.get("asset")
        v["asset"] = int(a) if isinstance(a, (int, float)) and not isinstance(a, bool) else None
    tracks = []
    for t in (src.get("tracks") or [])[:MAX_TRACKS]:
        a = t.get("asset") if isinstance(t, dict) else None
        if isinstance(a, (int, float)) and not isinstance(a, bool):
            tracks.append({"asset": int(a), "start": max(0.0, float(t.get("start") or 0.0))})
        else:
            tracks.append({"asset": None, "start": 0.0})
    out["tracks"] = tracks
    p = out["pip"]
    p["w"], p["h"] = _clamp(float(p["w"]), 0.1, 1.0), _clamp(float(p["h"]), 0.06, 1.0)
    p["x"], p["y"] = _clamp(float(p["x"]), 0.0, 1.0 - p["w"]), _clamp(float(p["y"]), 0.0, 1.0 - p["h"])
    return out


def active(layout: dict | None) -> bool:
    return norm(layout)["kind"] != "single"


def count(layout: dict | None) -> int:
    """Nombre de vues affichées (1 en plein écran, 2 pour l'image dans l'image)."""
    lay = norm(layout)
    return {"single": 1, "pip": 2}.get(lay["kind"], lay["count"])


def others(layout: dict | None) -> list[int]:
    """Les autres vidéos utilisées par les vues affichées, dans l'ordre, sans doublon."""
    lay = norm(layout)
    out: list[int] = []
    if lay["kind"] == "single":
        return out
    for v in lay["views"][:count(lay)]:
        if v["asset"] is not None and v["asset"] not in out:
            out.append(v["asset"])
    return out


def track_start(layout: dict | None, aid: int) -> float | None:
    """Instant du montage où commence la vidéo `aid` sur sa piste (None : pas sur une piste)."""
    return next((t["start"] for t in norm(layout)["tracks"] if t["asset"] == aid), None)


def other_time(layout: dict | None, aid: int, src_t: float, t_line: float) -> float:
    """Instant de la vidéo `aid` montré quand le montage est à `t_line` (source du montage à `src_t`) ; négatif :
    la vidéo n'a pas encore commencé."""
    start = track_start(layout, aid)
    if start is not None:
        return t_line - start
    return src_t + float(norm(layout)["offset"] or 0.0)


def place_track(layout: dict | None, aid: int, W: int, H: int, start: float = 0.0) -> tuple[dict, int | None]:
    """Vidéo posée sur la première piste vidéo libre (2 à 4), avec sa vue (côte à côte pour un format large,
    empilées sinon) : (mise en page, numéro de piste 1..3) ; None : déjà posée ou plus de piste libre."""
    lay = norm(layout)
    tr = lay["tracks"]
    if any(t["asset"] == aid for t in tr):
        return lay, None
    j = next((k for k, t in enumerate(tr) if t["asset"] is None), len(tr))
    if j >= MAX_TRACKS:
        return lay, None
    while len(tr) <= j:
        tr.append({"asset": None, "start": 0.0})
    tr[j] = {"asset": aid, "start": max(0.0, float(start))}
    on_tracks = {t["asset"] for t in tr if t["asset"] is not None}
    if lay["kind"] == "single":
        lay["kind"], lay["count"] = ("grid" if W > H else "stack"), 2
        lay["views"] = [dict(VIEW) for _ in range(MAX_VIEWS)]
    if lay["kind"] == "pip":
        lay["views"][1]["asset"] = aid
        return lay, j + 1
    free = next((i for i in range(1, MAX_VIEWS) if lay["views"][i]["asset"] not in on_tracks - {aid}
                 and lay["views"][i]["asset"] != aid), None)
    if free is not None:
        lay["views"][free]["asset"] = aid
        lay["count"] = min(MAX_VIEWS, max(lay["count"], free + 1))
    return lay, j + 1


def remove_track(layout: dict | None, j: int) -> dict:
    """Piste vidéo j (1..3) vidée : sa vidéo quitte les vues ; plus aucune autre vidéo : plein écran."""
    lay = norm(layout)
    tr = lay["tracks"]
    if not 1 <= j <= len(tr) or tr[j - 1]["asset"] is None:
        return lay
    aid = tr[j - 1]["asset"]
    tr[j - 1] = {"asset": None, "start": 0.0}
    while tr and tr[-1]["asset"] is None:
        tr.pop()
    keep = [v for v in lay["views"] if v["asset"] != aid]
    lay["views"] = (keep + [dict(VIEW) for _ in range(MAX_VIEWS)])[:MAX_VIEWS]
    left = sum(1 for t in tr if t["asset"] is not None)
    if lay["kind"] in ("stack", "grid"):
        if left:
            lay["count"] = max(2, min(lay["count"], 1 + left))
        else:
            lay["kind"] = "single"
    elif lay["kind"] == "pip" and not left:
        lay["kind"] = "single"
    return lay


def uses_second(layout: dict | None) -> bool:
    """Une vue au moins montre-t-elle une autre vidéo que celle du montage ?"""
    return bool(others(layout))


def view_source(layout: dict | None, i: int) -> int:
    """Image de la vue i : 0 = la vidéo du montage, k ≥ 1 = la k-ième autre vidéo (`others`)."""
    lay = norm(layout)
    a = lay["views"][i]["asset"] if i < MAX_VIEWS else None
    oth = others(lay)
    return oth.index(a) + 1 if a in oth else 0


def uses_main(layout: dict | None) -> bool:
    lay = norm(layout)
    return lay["kind"] == "single" or any(view_source(lay, i) == 0 for i in range(count(lay)))


def _even_floor(v: float) -> int:
    n = int(v)
    return n - (n % 2)


def _even_ceil(v: float) -> int:
    n = int(math.ceil(v - 1e-9))
    return n + (n % 2)


def zones(layout: dict | None, W: int, H: int) -> list[tuple[int, int, int, int]]:
    """Zones (x, y, w, h) en pixels de sortie, dans l'ordre des vues (de haut en bas). Dimensions paires."""
    lay = norm(layout)
    if lay["kind"] == "pip":
        p = lay["pip"]
        w, h = max(16, _even_floor(p["w"] * W)), max(16, _even_floor(p["h"] * H))
        x, y = _even_floor(p["x"] * W), _even_floor(p["y"] * H)
        return [(0, 0, W, H), (min(x, W - w), min(y, H - h), w, h)]
    if lay["kind"] == "grid":                 # côte à côte : 2 colonnes ; 3 : deux en haut, une en bas ; 4 : 2 × 2
        n = lay["count"]
        cw = _even_floor(W / 2)
        if n == 2:
            return [(0, 0, cw, H), (cw, 0, W - cw, H)]
        rh = _even_floor(H / 2)
        out = [(0, 0, cw, rh), (cw, 0, W - cw, rh)]
        if n == 3:
            return out + [(0, rh, W, H - rh)]
        return out + [(0, rh, cw, H - rh), (cw, rh, W - cw, H - rh)]
    if lay["kind"] == "stack":
        n = lay["count"]
        if n == 2:
            top = _clamp(_even_floor(lay["ratio"] * H), 2, H - 2)
            return [(0, 0, W, top), (0, top, W, H - top)]
        h = _even_floor(H / n)
        out = [(0, i * h, W, h) for i in range(n - 1)]
        return out + [(0, (n - 1) * h, W, H - (n - 1) * h)]
    return [(0, 0, W, H)]


def source_crop(sw: int, sh: int, view: dict) -> tuple[int, int, int, int]:
    """Rognage d'une source sw×sh : (largeur, hauteur, x, y) de la partie gardée, paires."""
    l, r = float(view.get("crop_l") or 0.0), float(view.get("crop_r") or 0.0)
    t, b = float(view.get("crop_t") or 0.0), float(view.get("crop_b") or 0.0)
    cw = max(2, _even_floor(sw * (1.0 - l - r)))
    ch = max(2, _even_floor(sh * (1.0 - t - b)))
    x = min(_even_floor(sw * l), max(0, sw - cw))
    y = min(_even_floor(sh * t), max(0, sh - ch))
    return cw, ch, x, y


def cropped(view: dict) -> bool:
    return any(float(view.get(k) or 0.0) > 0 for k in ("crop_l", "crop_r", "crop_t", "crop_b"))


def view_crop(sw: int, sh: int, zw: int, zh: int, view: dict) -> tuple[int, int, int, int]:
    """Source sw×sh (déjà rognée) mise à l'échelle (SW×SH, paires) puis recadrée à zw×zh depuis (x, y) :
    (SW, SH, x, y). Zoom < 1 : l'image peut être plus petite que sa zone, centrée ; x ou y est alors négatif
    (marge noire)."""
    z = _clamp(float(view.get("zoom", 1.0)), ZOOM_MIN, ZOOM_MAX)
    s = max(zw / sw, zh / sh) * z
    SW, SH = _even_ceil(sw * s), _even_ceil(sh * s)
    if z >= 1.0:
        SW, SH = max(zw, SW), max(zh, SH)

    def pos(c: float, size: int, zone: int) -> int:
        if size <= zone:
            return -_even_floor((zone - size) / 2)
        return int(_clamp(c * size - zone / 2, 0, size - zone))
    return SW, SH, pos(float(view.get("cx", 0.5)), SW, zw), pos(float(view.get("cy", 0.5)), SH, zh)


def center_for(sw: int, sh: int, zw: int, zh: int, view: dict, x: float, y: float) -> tuple[float, float]:
    """Inverse de view_crop : centre (cx, cy) qui place le coin du cadre en (x, y) (déplacement à la souris)."""
    SW, SH, _x, _y = view_crop(sw, sh, zw, zh, view)
    return _clamp((x + zw / 2) / SW, 0.0, 1.0), _clamp((y + zh / 2) / SH, 0.0, 1.0)
