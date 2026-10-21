"""Identification des intervenants (cahier des charges §6 : « qui parle ») — sur le PC.

1. une empreinte vocale par phrase de la transcription (modèle WeSpeaker ResNet34, ONNX) ;
2. regroupement hiérarchique (similarité cosinus, liaison moyenne), nombre d'intervenants
   automatique ou imposé ;
3. « Intervenant 1, 2… » dans l'ordre d'apparition, renommables ensuite.
"""
from __future__ import annotations

import hashlib
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

import numpy as np

RATE = 16000
URL = "https://github.com/k2-fsa/sherpa-onnx/releases/download/speaker-recongition-models/"


@dataclass(frozen=True)
class SpkModel:
    key: str
    label: str
    file: str
    size_mb: int
    sha256: str


MODELS = [SpkModel("wespeaker_resnet34", "Empreintes vocales (WeSpeaker ResNet34)",
                   "wespeaker_en_voxceleb_resnet34.onnx", 26,
                   "5ef208a9da1453335308a6b6f4e6dfbd7e183a38b604de0a57664f45d257fe94")]
BY_KEY = {m.key: m for m in MODELS}


class DiarizationError(RuntimeError):
    pass


def model_path(models_dir: Path, m: SpkModel = MODELS[0]) -> Path:
    return models_dir / "speakers" / m.file


def installed(models_dir: Path) -> list[str]:
    return [m.key for m in MODELS if model_path(models_dir, m).exists()]


def download_model(models_dir: Path, key: str, progress: Callable[[float, str], None] = lambda f, m: None,
                   is_cancelled: Callable[[], bool] = lambda: False, base_url: str = URL) -> Path:
    m = BY_KEY[key]
    dest = model_path(models_dir, m)
    dest.parent.mkdir(parents=True, exist_ok=True)
    part = dest.with_suffix(".partial")
    h = hashlib.sha256()
    try:
        with urllib.request.urlopen(base_url + m.file, timeout=60) as r, open(part, "wb") as f:
            total = int(r.headers.get("Content-Length") or m.size_mb << 20)
            done = 0
            while chunk := r.read(1 << 20):
                if is_cancelled():
                    raise DiarizationError("Annulé.")
                f.write(chunk)
                h.update(chunk)
                done += len(chunk)
                progress(min(0.99, done / total), f"Téléchargement {done >> 20}/{total >> 20} Mo")
    except (OSError, urllib.error.URLError) as exc:
        part.unlink(missing_ok=True)
        raise DiarizationError(f"Téléchargement impossible : {exc}") from exc
    except DiarizationError:
        part.unlink(missing_ok=True)
        raise
    if m.sha256 and h.hexdigest() != m.sha256:
        part.unlink(missing_ok=True)
        raise DiarizationError("Fichier du modèle corrompu (empreinte SHA-256 différente).")
    part.replace(dest)
    return dest


# -- caractéristiques « fbank » compatibles Kaldi (WeSpeaker) ------------------------------------
_FL, _FS, _NFFT, _NMEL = 400, 160, 512, 80


def _mel(f):
    return 1127.0 * np.log(1.0 + np.asarray(f) / 700.0)


def _mel_banks() -> np.ndarray:
    width = RATE / _NFFT
    lo, hi = _mel(20.0), _mel(RATE / 2)
    delta = (hi - lo) / (_NMEL + 1)
    mels = _mel(np.arange(_NFFT // 2) * width)
    banks = np.zeros((_NMEL, _NFFT // 2 + 1), dtype=np.float32)
    for b in range(_NMEL):
        left, center, right = lo + b * delta, lo + (b + 1) * delta, lo + (b + 2) * delta
        up = (mels - left) / (center - left)
        down = (right - mels) / (right - center)
        banks[b, :_NFFT // 2] = np.maximum(0.0, np.minimum(up, down))
    return banks


_BANKS = _mel_banks()
_WIN = ((0.5 - 0.5 * np.cos(2 * np.pi * np.arange(_FL) / (_FL - 1))) ** 0.85).astype(np.float32)


def fbank(x: np.ndarray) -> np.ndarray:
    """x : signal 16 kHz (float, −1..1) → (T, 80), moyenne retirée (CMN)."""
    x = x.astype(np.float32) * 32768.0
    if x.size < _FL:
        x = np.pad(x, (0, _FL - x.size))
    n = 1 + (x.size - _FL) // _FS
    idx = np.arange(_FL)[None, :] + _FS * np.arange(n)[:, None]
    fr = x[idx]
    fr = fr - fr.mean(axis=1, keepdims=True)
    fr = np.concatenate([fr[:, :1] - 0.97 * fr[:, :1], fr[:, 1:] - 0.97 * fr[:, :-1]], axis=1)
    fr = fr * _WIN
    spec = np.abs(np.fft.rfft(fr, n=_NFFT, axis=1)) ** 2
    feats = np.log(np.maximum(spec @ _BANKS.T, np.finfo(np.float32).eps))
    return (feats - feats.mean(axis=0, keepdims=True)).astype(np.float32)


class Embedder:
    def __init__(self, models_dir: Path):
        p = model_path(models_dir)
        if not p.exists():
            raise DiarizationError("Modèle d'empreintes vocales non installé (Paramètres → Modèles).")
        try:
            import onnxruntime as ort
        except Exception as exc:
            raise DiarizationError("Le moteur onnxruntime n'est pas installé dans cette version.") from exc
        self.sess = ort.InferenceSession(str(p), providers=["CPUExecutionProvider"])
        self.inp = self.sess.get_inputs()[0].name

    def embed(self, x: np.ndarray) -> np.ndarray:
        e = self.sess.run(None, {self.inp: fbank(x)[None]})[0][0]
        return e / (np.linalg.norm(e) + 1e-9)


def cluster(emb: np.ndarray, n_speakers: int | None = None, threshold: float = 0.5,
            weights: np.ndarray | None = None) -> list[int]:
    """Regroupement hiérarchique, liaison moyenne sur la similarité cosinus.

    Arrêt quand la meilleure fusion passe sous `threshold` (mode automatique) ou au
    nombre d'intervenants demandé. Étiquettes renumérotées dans l'ordre d'apparition.
    """
    n = len(emb)
    if n == 0:
        return []
    if n == 1:
        return [0]
    sim = emb @ emb.T
    np.fill_diagonal(sim, -np.inf)
    size = np.ones(n) if weights is None else np.asarray(weights, dtype=float).copy()
    active = np.ones(n, bool)
    label = np.arange(n)
    k = n
    while k > 1:
        i, j = np.unravel_index(int(np.argmax(sim)), sim.shape)
        best = sim[i, j]
        if n_speakers is None and best < threshold:
            break
        if n_speakers is not None and k <= n_speakers:
            break
        # fusion de j dans i (Lance-Williams, liaison moyenne pondérée)
        w = (size[i] * sim[i] + size[j] * sim[j]) / (size[i] + size[j])
        sim[i], sim[:, i] = w, w
        sim[i, i] = -np.inf
        sim[j], sim[:, j] = -np.inf, -np.inf
        size[i] += size[j]
        active[j] = False
        label[label == j] = i
        k -= 1
    order: dict[int, int] = {}
    return [order.setdefault(int(lb), len(order)) for lb in label]


def diarize(audio: np.ndarray, segments: list[tuple[float, float]], models_dir: Path,
            n_speakers: int | None = None, threshold: float = 0.5,
            progress: Callable[[float, str], None] = lambda f, m: None,
            is_cancelled: Callable[[], bool] = lambda: False) -> list[int]:
    """Retourne l'indice d'intervenant de chaque phrase (les phrases < 0,4 s prennent celui du voisin)."""
    emb_model = Embedder(models_dir)
    vecs, idx, w = [], [], []
    for k, (a, b) in enumerate(segments):
        if is_cancelled():
            raise DiarizationError("Annulé.")
        x = audio[int(a * RATE):int(b * RATE)]
        if x.size >= int(0.4 * RATE):
            vecs.append(emb_model.embed(x[: 20 * RATE]))
            idx.append(k)
            w.append(min(10.0, (b - a)))
        progress(0.9 * (k + 1) / max(1, len(segments)), f"Empreintes vocales {k + 1}/{len(segments)}")
    if not vecs:
        return [0] * len(segments)
    labels = cluster(np.array(vecs), n_speakers, threshold, np.array(w))
    out: list[int | None] = [None] * len(segments)
    for k, lb in zip(idx, labels):
        out[k] = lb
    last = next((x for x in out if x is not None), 0)
    for k in range(len(out)):                    # phrases trop courtes : intervenant de la phrase précédente
        if out[k] is None:
            out[k] = last
        last = out[k]
    # renumérotation dans l'ordre d'apparition
    order: dict[int, int] = {}
    return [order.setdefault(x, len(order)) for x in out]


def apply_labels(tr, labels: list[int], names: list[str] | None = None):
    """Écrit « Intervenant N » sur chaque phrase (ou les noms fournis, dans l'ordre d'apparition)."""
    for seg, lb in zip(tr.segments, labels):
        seg.speaker = names[lb] if names and lb < len(names) and names[lb] else f"Intervenant {lb + 1}"
    return tr


def copy_speakers(src, dst) -> bool:
    """Reporte les intervenants sur une traduction (mêmes phrases, mêmes horodatages)."""
    if len(src.segments) != len(dst.segments):
        return False
    if any(abs(a.start - b.start) > 0.05 for a, b in zip(src.segments, dst.segments)):
        return False
    for a, b in zip(src.segments, dst.segments):
        b.speaker = a.speaker
    return True
