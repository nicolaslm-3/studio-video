"""Traduction des transcriptions (cahier des charges §10).

Moteurs interchangeables : modèle de langage (Ollama local ou API compatible OpenAI), DeepL,
Google Cloud Translation. Un glossaire protège les termes à conserver (noms, marques) :
ils sont remplacés par des repères avant traduction puis restitués.
Les horodatages des phrases sont conservés ; ceux des mots sont répartis dans la phrase.
"""
from __future__ import annotations

import json
import re
import urllib.parse
from typing import Callable, Protocol

from ..transcribe.model import TSegment, Transcript, _spread
from .highlights import LLMError, _http_json

LANG_NAMES = {"fr": "français", "en": "anglais", "es": "espagnol", "pt": "portugais", "it": "italien",
              "ja": "japonais", "ru": "russe", "ko": "coréen", "zh": "chinois", "ar": "arabe", "tr": "turc",
              "id": "indonésien", "hi": "hindi", "de": "allemand", "nl": "néerlandais", "pl": "polonais"}
DEEPL_CODES = {"en": "EN-US", "pt": "PT-PT", "zh": "ZH"}       # variantes par défaut (modifiables)
Progress = Callable[[float, str], None]


class TranslationError(RuntimeError):
    pass


class Translator(Protocol):
    name: str

    def translate(self, texts: list[str], src: str | None, dst: str) -> list[str]: ...


# -- glossaire -----------------------------------------------------------------------------
def protect(text: str, terms: list[str]) -> tuple[str, dict[str, str]]:
    mapping = {}
    for i, t in enumerate(sorted({t.strip() for t in terms if t.strip()}, key=len, reverse=True)):
        token = f"[[{i}]]"
        new = re.sub(re.escape(t), token, text, flags=re.IGNORECASE)
        if new != text:
            mapping[token] = t
            text = new
    return text, mapping


def restore(text: str, mapping: dict[str, str]) -> str:
    for token, term in mapping.items():
        text = text.replace(token, term)
        text = text.replace(token.replace("[[", "[[ ").replace("]]", " ]]"), term)
    return text


# -- moteurs -----------------------------------------------------------------------------------
class DeepLTranslator:
    name = "deepl"

    def __init__(self, api_key: str, base_url: str = ""):
        if not api_key:
            raise TranslationError("Clé API DeepL absente (Paramètres → Matériel et moteurs).")
        self.key = api_key
        free = api_key.endswith(":fx")
        self.url = (base_url or ("https://api-free.deepl.com" if free else "https://api.deepl.com")).rstrip("/")

    def translate(self, texts, src, dst):
        payload = {"text": texts, "target_lang": DEEPL_CODES.get(dst, dst.upper()),
                   "preserve_formatting": True}
        if src:
            payload["source_lang"] = src.upper()
        try:
            r = _http_json(f"{self.url}/v2/translate", payload, {"Authorization": f"DeepL-Auth-Key {self.key}"}, 120)
        except LLMError as exc:
            raise TranslationError(str(exc)) from exc
        out = [t["text"] for t in r.get("translations", [])]
        if len(out) != len(texts):
            raise TranslationError("Réponse DeepL incomplète.")
        return out


class GoogleTranslator:
    name = "google"

    def __init__(self, api_key: str, base_url: str = ""):
        if not api_key:
            raise TranslationError("Clé API Google Cloud Translation absente.")
        self.key = api_key
        self.url = (base_url or "https://translation.googleapis.com").rstrip("/")

    def translate(self, texts, src, dst):
        payload = {"q": texts, "target": dst, "format": "text"}
        if src:
            payload["source"] = src
        try:
            r = _http_json(f"{self.url}/language/translate/v2?key={urllib.parse.quote(self.key)}", payload, None, 120)
        except LLMError as exc:
            raise TranslationError(str(exc)) from exc
        out = [t["translatedText"] for t in r.get("data", {}).get("translations", [])]
        if len(out) != len(texts):
            raise TranslationError("Réponse Google incomplète.")
        return out


class LLMTranslator:
    """Traduction par le modèle de langage configuré (Ollama : hors ligne, sur le PC)."""

    def __init__(self, kind: str, model: str, base_url: str = "", api_key: str = ""):
        from .highlights import LLMEngine
        self.engine = LLMEngine(kind, model, base_url, api_key)
        self.name = f"llm:{model}"

    def translate(self, texts, src, dst):
        numbered = {str(i + 1): t for i, t in enumerate(texts)}
        prompt = (f"Traduis chaque phrase de {LANG_NAMES.get(src or '', 'la langue d’origine')} vers "
                  f"{LANG_NAMES.get(dst, dst)}, pour des sous-titres : naturel, fidèle, concis, sans rien ajouter. "
                  "Conserve tels quels les repères de la forme [[0]]. Réponds UNIQUEMENT en JSON "
                  '{"1": "...", "2": "..."} avec les mêmes numéros.\n\n' + json.dumps(numbered, ensure_ascii=False))
        content = self.engine._ask(prompt)
        try:
            data = json.loads(content[content.find("{"): content.rfind("}") + 1])
        except ValueError as exc:
            raise TranslationError(f"Le modèle n'a pas répondu en JSON : {content[:200]}") from exc
        out = []
        for i, t in enumerate(texts):
            v = data.get(str(i + 1))
            if not isinstance(v, str) or not v.strip():
                raise TranslationError(f"Phrase {i + 1} non traduite par le modèle.")
            out.append(v.strip())
        return out


def make_translator(cfg: dict, secret: Callable[[str], str | None]) -> Translator:
    kind = cfg.get("engine")
    if kind == "deepl":
        return DeepLTranslator(secret("deepl_api_key") or "", cfg.get("base_url", ""))
    if kind == "google":
        return GoogleTranslator(secret("google_api_key") or "", cfg.get("base_url", ""))
    if kind == "llm":
        llm = cfg.get("llm") or {}
        if llm.get("kind") not in ("ollama", "openai") or not llm.get("model"):
            raise TranslationError("Aucun modèle de langage configuré (Paramètres → Matériel et moteurs).")
        return LLMTranslator(llm["kind"], llm["model"], llm.get("base_url", ""),
                             (secret("llm_api_key") or "") if llm["kind"] == "openai" else "")
    raise TranslationError(f"Moteur de traduction inconnu : {kind}")


# -- transcription ------------------------------------------------------------------------------
def translate_transcript(tr: Transcript, translator: Translator, dst: str, glossary: list[str] | None = None,
                         progress: Progress = lambda f, m: None, batch: int = 25,
                         is_cancelled: Callable[[], bool] = lambda: False) -> Transcript:
    src = tr.language
    if src and src == dst:
        raise TranslationError("La langue cible est la langue d'origine.")
    glossary = glossary or []
    out_segs: list[TSegment] = []
    segs = tr.segments
    for k in range(0, len(segs), batch):
        if is_cancelled():
            raise TranslationError("Annulé.")
        chunk = segs[k:k + batch]
        prot = [protect(s.text, glossary) for s in chunk]
        res = translator.translate([p[0] for p in prot], src, dst)
        for s, (_, mapping), txt in zip(chunk, prot, res):
            txt = restore(txt, mapping).strip()
            out_segs.append(TSegment(s.start, s.end, txt, _spread(txt.split(), s.start, s.end) if txt else [],
                                     s.speaker))
        progress(min(0.99, (k + len(chunk)) / len(segs)), f"Traduction ({k + len(chunk)}/{len(segs)} phrases)")
    return Transcript(out_segs, dst, f"traduction:{translator.name}")
