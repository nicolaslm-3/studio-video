"""Création originale (§4 mode 3, V4) : une vidéo à partir d'une idée, d'un texte ou d'un PDF.

1. Script en scènes (narration, texte à l'écran, visuel, durée) : modèle de langage si configuré,
   sinon découpage du texte en phrases. Le script reste entièrement modifiable.
2. Voix facultative : chaque narration est lue par le moteur de voix choisi ; la scène dure le temps
   de la voix (+ une respiration).
3. Une vraie vidéo est fabriquée par FFmpeg : chaque scène sur une image (léger mouvement), une vidéo
   de la bibliothèque, ou un fond de couleur ; narration en piste son.
4. Elle est importée dans le projet avec sa « transcription » exacte (le script, calé sur la voix), ce
   qui donne des sous-titres justes, et les textes à l'écran sont posés sur la timeline.
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
import wave
import zipfile
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Callable

from ..media import ffmpeg
from ..transcribe.model import TSegment, Transcript, Word

_NOWIN = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
WORDS_PER_S = 2.6          # lecture confortable des sous-titres sans voix
COLORS = ["#1F2A44", "#3A2F5B", "#14403B", "#4A2B2B", "#2B3B4A", "#40371F"]


class OriginalError(RuntimeError):
    pass


@dataclass
class Scene:
    narration: str = ""
    text: str = ""                 # texte affiché (titre, chiffre clé…)
    visual: str = ""               # "asset:<id>" | "#RRGGBB" | "" (couleur automatique)
    duration: float = 0.0          # 0 = automatique (voix ou vitesse de lecture)

    def to_dict(self) -> dict:
        return asdict(self)


# -- lecture des sources -----------------------------------------------------------------------
def read_source(path: Path) -> str:
    """Texte d'un fichier .txt, .md, .pdf ou .docx."""
    path = Path(path)
    ext = path.suffix.lower()
    if ext in (".txt", ".md", ".srt"):
        return path.read_text(encoding="utf-8", errors="replace")
    if ext == ".pdf":
        try:
            from pypdf import PdfReader
        except ImportError as exc:
            raise OriginalError("Lecture des PDF indisponible dans cette version.") from exc
        try:
            return "\n".join((p.extract_text() or "") for p in PdfReader(str(path)).pages)
        except Exception as exc:
            raise OriginalError(f"PDF illisible : {exc}") from exc
    if ext == ".docx":
        try:
            with zipfile.ZipFile(path) as z:
                xml = z.read("word/document.xml").decode("utf-8", "replace")
        except (KeyError, zipfile.BadZipFile) as exc:
            raise OriginalError("Fichier Word illisible.") from exc
        xml = re.sub(r"</w:p>", "\n", xml)
        return re.sub(r"<[^>]+>", "", xml)
    raise OriginalError(f"Format non pris en charge : {ext} (texte, Markdown, PDF ou Word).")


def clean_text(text: str) -> str:
    text = re.sub(r"-\n(\w)", r"\1", text or "")            # coupures de mots en fin de ligne (PDF)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{2,}", "\n\n", text)
    return text.strip()


def sentences(text: str) -> list[str]:
    parts = re.split(r"(?<=[.!?…])\s+|\n{2,}", clean_text(text))
    return [p.replace("\n", " ").strip() for p in parts if p and p.strip()]


def script_from_text(text: str, max_words: int = 28, max_scenes: int = 40) -> list[Scene]:
    """Découpage déterministe : phrases regroupées en scènes de ~max_words mots."""
    scenes, cur = [], []
    for s in sentences(text):
        n = len(s.split())
        if cur and sum(len(x.split()) for x in cur) + n > max_words:
            scenes.append(Scene(" ".join(cur)))
            cur = []
        cur.append(s)
        if len(scenes) >= max_scenes:
            break
    if cur and len(scenes) < max_scenes:
        scenes.append(Scene(" ".join(cur)))
    if scenes:
        first = sentences(scenes[0].narration)[0]
        scenes[0].text = first if len(first) <= 60 else ""
    return scenes


def script_from_llm(engine, idea: str, language: str = "fr", seconds: int = 45, source: str = "") -> list[Scene]:
    """engine : objet avec _ask(prompt) -> str (LLMEngine)."""
    n = max(3, min(12, round(seconds / 6)))
    prompt = (f"Écris le script d'une vidéo courte verticale (réseaux sociaux) d'environ {seconds} secondes, en "
              f"langue « {language} », en {n} scènes. Accroche forte dans la première scène, une idée par scène, "
              "phrases parlées naturelles, aucun fait inventé"
              + (" : appuie-toi uniquement sur le document fourni" if source else "") + ". "
              'Réponds UNIQUEMENT en JSON : {"scenes": [{"narration": "texte lu", "text": "texte affiché court '
              '(5 mots max, ou vide)"}]}\n\n'
              f"Sujet : {idea.strip() or '(voir document)'}"
              + (f"\n\nDocument :\n{source[:12000]}" if source else ""))
    content = engine._ask(prompt)
    try:
        data = json.loads(content[content.find("{"): content.rfind("}") + 1])
        raw = data["scenes"]
    except (ValueError, KeyError, TypeError) as exc:
        raise OriginalError(f"Le modèle n'a pas répondu au format attendu : {content[:200]}") from exc
    out = [Scene(str(s.get("narration", "")).strip(), str(s.get("text", "")).strip()[:80])
           for s in raw if isinstance(s, dict) and str(s.get("narration", "")).strip()]
    if not out:
        raise OriginalError("Le modèle n'a proposé aucune scène.")
    return out


# -- fabrication ---------------------------------------------------------------------------------
def reading_time(text: str) -> float:
    return max(2.5, len(text.split()) / WORDS_PER_S + 0.6)


def _wav_duration(p: Path) -> float:
    with wave.open(str(p), "rb") as w:
        return w.getnframes() / float(w.getframerate())


def _run(cmd: list[str]) -> None:
    p = subprocess.run(cmd, capture_output=True, text=True, creationflags=_NOWIN)
    if p.returncode != 0:
        raise OriginalError(f"FFmpeg : {p.stderr[-600:]}")


def _scene_clip(i: int, sc: Scene, dur: float, W: int, H: int, fps: float, voice: Path | None,
                visual: dict | None, color: str, work: Path) -> Path:
    out = work / f"scene_{i:03d}.mp4"
    cmd = [ffmpeg.ffmpeg_bin(), "-hide_banner", "-loglevel", "error", "-nostdin", "-y"]
    cover = f"scale={W}:{H}:force_original_aspect_ratio=increase:flags=lanczos"
    if visual and visual.get("kind") == "image":
        cmd += ["-loop", "1", "-framerate", f"{fps:g}", "-t", f"{dur:.3f}", "-i", visual["path"]]
        # léger travelling (Ken Burns) : image agrandie de 10 %, cadre qui glisse
        vf = (f"scale={int(W * 1.1)}:{int(H * 1.1)}:force_original_aspect_ratio=increase:flags=lanczos,"
              f"crop={W}:{H}:x='(iw-{W})*t/{dur:.3f}':y='(ih-{H})/2'")
    elif visual and visual.get("kind") == "video":
        cmd += ["-stream_loop", "-1", "-ss", f"{visual.get('start', 0):.3f}", "-t", f"{dur:.3f}",
                "-i", visual["path"]]
        vf = f"{cover},crop={W}:{H}"
    else:
        cmd += ["-f", "lavfi", "-t", f"{dur:.3f}", "-i", f"color=c={color}:s={W}x{H}:r={fps:g}"]
        vf = "vignette=PI/5"
    if voice:
        cmd += ["-i", str(voice)]
        af = f"adelay=250|250,apad,atrim=0:{dur:.3f}"
    else:
        cmd += ["-f", "lavfi", "-t", f"{dur:.3f}", "-i", "anullsrc=r=48000:cl=stereo"]
        af = f"atrim=0:{dur:.3f}"
    cmd += ["-map", "0:v:0", "-map", "1:a:0", "-vf", f"{vf},fps={fps:g},setsar=1,format=yuv420p",
            "-af", f"aresample=48000,aformat=channel_layouts=stereo,{af}", "-t", f"{dur:.3f}",
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "18", "-c:a", "aac", "-b:a", "192k", str(out)]
    _run(cmd)
    return out


def _words(text: str, start: float, end: float) -> list[Word]:
    toks = text.split()
    if not toks:
        return []
    total = sum(len(t) + 1 for t in toks)
    out, t = [], start
    for tok in toks:
        d = (end - start) * (len(tok) + 1) / total
        out.append(Word(round(t, 3), round(t + d, 3), " " + tok))
        t += d
    return out


def build(scenes: list[Scene], W: int, H: int, fps: float, dest: Path, work: Path,
          tts=None, voice: str = "", language: str = "fr", visuals: Callable[[str], dict | None] = lambda v: None,
          progress: Callable[[float, str], None] = lambda f, m: None,
          is_cancelled: Callable[[], bool] = lambda: False) -> dict:
    """Fabrique la vidéo. Retourne {"path", "duration", "transcript": Transcript, "texts": [(début, fin, texte)]}."""
    if not scenes:
        raise OriginalError("Le script est vide.")
    work.mkdir(parents=True, exist_ok=True)
    clips, segs, texts, t = [], [], [], 0.0
    for i, sc in enumerate(scenes):
        if is_cancelled():
            raise OriginalError("Annulé.")
        voice_wav, speech = None, 0.0
        if tts is not None and sc.narration.strip():
            progress(0.8 * i / len(scenes), f"Voix : scène {i + 1}/{len(scenes)}")
            voice_wav = work / f"voice_{i:03d}.wav"
            tts.synth(sc.narration, voice, language, voice_wav)
            speech = _wav_duration(voice_wav)
        auto = (speech + 0.6) if voice_wav else reading_time(sc.narration or sc.text)
        dur = round(max(1.5, sc.duration or auto, speech + 0.4 if voice_wav else 0), 3)
        progress(0.8 * (i + 0.5) / len(scenes), f"Image : scène {i + 1}/{len(scenes)}")
        color = sc.visual if re.fullmatch(r"#[0-9A-Fa-f]{6}", sc.visual or "") else COLORS[i % len(COLORS)]
        clips.append(_scene_clip(i, sc, dur, W, H, fps, voice_wav, visuals(sc.visual), color, work))
        if sc.narration.strip():
            a = t + (0.25 if voice_wav else 0.2)
            b = t + (0.25 + speech if voice_wav else dur - 0.2)
            segs.append(TSegment(round(a, 3), round(b, 3), sc.narration.strip(), _words(sc.narration, a, b)))
        if sc.text.strip():
            texts.append((round(t, 3), round(t + dur, 3), sc.text.strip()))
        t += dur
    progress(0.9, "Assemblage")
    lst = work / "scenes.txt"
    lst.write_text("".join(f"file '{c.as_posix()}'\n" for c in clips), encoding="utf-8")
    dest.parent.mkdir(parents=True, exist_ok=True)
    _run([ffmpeg.ffmpeg_bin(), "-hide_banner", "-loglevel", "error", "-nostdin", "-y", "-f", "concat", "-safe", "0",
          "-i", str(lst), "-c", "copy", "-movflags", "+faststart", str(dest)])
    tr = Transcript(segs, language, "script")
    return {"path": str(dest), "duration": round(t, 3), "transcript": tr, "texts": texts}
