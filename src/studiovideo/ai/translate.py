"""Traduction des transcriptions (cahier des charges §10).

Moteurs interchangeables : DeepL, modèle de langage (Ollama local ou API compatible OpenAI), Google Cloud
Translation, Microsoft Translator, LibreTranslate, et deux services gratuits sans clé (Google Traduction, MyMemory).
« Automatique » prend le meilleur moteur disponible et passe au suivant si l'un échoue (crédit épuisé, panne).
Un glossaire protège les termes à conserver (noms, marques) : ils sont remplacés par des repères avant
traduction puis restitués. Les horodatages des phrases sont conservés ; ceux des mots sont répartis dans la phrase.
"""
from __future__ import annotations

import json
import re
import urllib.error
import urllib.parse
import urllib.request
from typing import Callable, Protocol

from ..transcribe.model import TSegment, Transcript, _spread
from .highlights import LLMError, _http_json

LANG_NAMES = {"fr": "français", "en": "anglais", "es": "espagnol", "pt": "portugais", "it": "italien",
              "ja": "japonais", "ru": "russe", "ko": "coréen", "zh": "chinois", "ar": "arabe", "tr": "turc",
              "id": "indonésien", "hi": "hindi", "de": "allemand", "nl": "néerlandais", "pl": "polonais"}
DEEPL_CODES = {"en": "EN-US", "pt": "PT-BR", "zh": "ZH"}       # variantes par défaut (modifiables)
Progress = Callable[[float, str], None]
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) StudioVideo"}

# Moteurs proposés (ordre de l'automatique : du meilleur au plus simple).
# clé : (nom, qualité, coût, ce qu'il faut, adresse pour obtenir la clé)
ENGINES = {
    "deepl": ("DeepL", "Excellente, la plus naturelle pour les langues européennes",
              "Gratuit jusqu'à 500 000 caractères par mois (compte DeepL API Free), puis payant",
              "Une clé API DeepL", "https://www.deepl.com/pro-api"),
    "llm": ("Modèle de langage (OpenAI, Ollama…)", "Excellente, tient compte du contexte et du ton",
            "OpenAI : payant à l'usage (crédit à acheter) · Ollama : gratuit, sur ce PC (lent sans carte graphique)",
            "Le modèle réglé dans Réglages → Modèle de langage", "https://platform.openai.com/settings/organization/billing"),
    "google": ("Google Cloud Translation", "Très bonne", "Gratuit jusqu'à 500 000 caractères par mois, puis payant",
               "Une clé API Google Cloud", "https://console.cloud.google.com/apis/library/translate.googleapis.com"),
    "microsoft": ("Microsoft Translator (Azure)", "Très bonne", "Gratuit jusqu'à 2 millions de caractères par mois",
                  "Une clé et la région de la ressource Azure Translator",
                  "https://portal.azure.com/#create/Microsoft.CognitiveServicesTextTranslation"),
    "google_free": ("Google Traduction (gratuit)", "Très bonne", "Gratuit, sans compte",
                    "Rien : service public de Google, sans garantie (peut limiter un usage intensif)", ""),
    "mymemory": ("MyMemory (gratuit)", "Correcte", "Gratuit, sans compte (environ 5 000 caractères par jour)",
                 "Rien", "https://mymemory.translated.net/doc/usagelimits.php"),
    "libre": ("LibreTranslate", "Correcte", "Gratuit si vous l'installez vous-même, sinon selon le serveur",
              "L'adresse d'un serveur LibreTranslate (et sa clé s'il en demande une)", "https://libretranslate.com"),
}
AUTO_ORDER = ("deepl", "llm", "google", "microsoft", "google_free", "mymemory", "libre")


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


class GoogleFreeTranslator:
    """Google Traduction sans clé (service public, celui des extensions de navigateur)."""
    name = "google_free"

    def __init__(self, base_url: str = ""):
        self.url = (base_url or "https://translate.googleapis.com").rstrip("/")

    def translate(self, texts, src, dst):
        out: list[str] = []
        for chunk in _chunks(texts, 1500, 40):          # demande GET (l'envoi en POST est refusé) : adresse courte
            form = [("client", "gtx"), ("sl", src or "auto"), ("tl", dst), ("dt", "t")] + [("q", t) for t in chunk]
            r = _http_get(f"{self.url}/translate_a/t?" + urllib.parse.urlencode(form), 60)
            if len(chunk) == 1 and isinstance(r, list) and r and isinstance(r[0], str):
                r = [r]                                  # une seule phrase : réponse ["texte", "langue"]
            items = r if isinstance(r, list) else [r]
            res = [it[0] if isinstance(it, list) else it for it in items]
            if len(res) != len(chunk) or not all(isinstance(x, str) for x in res):
                raise TranslationError("Réponse de Google Traduction inattendue.")
            out += res
        return out


class MyMemoryTranslator:
    """MyMemory : gratuit, sans clé, une phrase par demande (500 caractères au plus)."""
    name = "mymemory"

    def __init__(self, base_url: str = "", email: str = ""):
        self.url = (base_url or "https://api.mymemory.translated.net").rstrip("/")
        self.email = email

    def translate(self, texts, src, dst):
        out = []
        for t in texts:
            if not t.strip():
                out.append(t)
                continue
            q = {"q": t[:500], "langpair": f"{src or 'autodetect'}|{dst}"}
            if self.email:
                q["de"] = self.email
            r = _http_get(f"{self.url}/get?" + urllib.parse.urlencode(q))
            if r.get("quotaFinished") or str(r.get("responseStatus")) == "429":
                raise TranslationError("Quota quotidien MyMemory atteint : réessayez demain ou changez de moteur.")
            txt = (r.get("responseData") or {}).get("translatedText")
            if str(r.get("responseStatus")) != "200" or not isinstance(txt, str):
                raise TranslationError(f"MyMemory : {r.get('responseDetails') or 'réponse inattendue'}")
            out.append(txt)
        return out


class MicrosoftTranslator:
    """Microsoft Translator (Azure) : clé et région de la ressource."""
    name = "microsoft"

    def __init__(self, api_key: str, region: str = "", base_url: str = ""):
        if not api_key:
            raise TranslationError("Clé Microsoft Translator absente.")
        self.key, self.region = api_key, region
        self.url = (base_url or "https://api.cognitive.microsofttranslator.com").rstrip("/")

    def translate(self, texts, src, dst):
        out = []
        for chunk in _chunks(texts, 9000, 900):
            q = {"api-version": "3.0", "to": dst}
            if src:
                q["from"] = src
            headers = {"Ocp-Apim-Subscription-Key": self.key}
            if self.region:
                headers["Ocp-Apim-Subscription-Region"] = self.region
            try:
                r = _http_json(f"{self.url}/translate?" + urllib.parse.urlencode(q), [{"Text": t} for t in chunk],
                               headers, 120)
            except LLMError as exc:
                raise TranslationError(str(exc)) from exc
            res = [(x.get("translations") or [{}])[0].get("text") for x in r] if isinstance(r, list) else []
            if len(res) != len(chunk) or not all(isinstance(x, str) for x in res):
                raise TranslationError("Réponse Microsoft incomplète.")
            out += res
        return out


class LibreTranslator:
    """LibreTranslate : serveur libre (installé chez soi ou public), clé facultative."""
    name = "libre"

    def __init__(self, base_url: str, api_key: str = ""):
        if not base_url:
            raise TranslationError("Adresse du serveur LibreTranslate absente.")
        self.url, self.key = base_url.rstrip("/"), api_key

    def translate(self, texts, src, dst):
        payload = {"q": texts, "source": src or "auto", "target": dst, "format": "text"}
        if self.key:
            payload["api_key"] = self.key
        try:
            r = _http_json(f"{self.url}/translate", payload, None, 180)
        except LLMError as exc:
            raise TranslationError(str(exc)) from exc
        res = r.get("translatedText")
        res = res if isinstance(res, list) else [res]
        if len(res) != len(texts):
            raise TranslationError("Réponse LibreTranslate incomplète.")
        return res


class FallbackTranslator:
    """Automatique : le premier moteur qui répond ; s'il échoue (crédit épuisé, panne), le suivant reprend là où
    il s'est arrêté. `used` : moteurs effectivement utilisés, `failures` : pourquoi les autres ont été écartés."""

    def __init__(self, engines: list):
        if not engines:
            raise TranslationError("Aucun moteur de traduction disponible.")
        self.engines = list(engines)
        self.used: list[str] = []
        self.failures: list[str] = []

    @property
    def name(self) -> str:
        return "+".join(self.used) or self.engines[0].name

    def translate(self, texts, src, dst):
        while self.engines:
            eng = self.engines[0]
            try:
                out = eng.translate(texts, src, dst)
            except (TranslationError, LLMError) as exc:
                self.failures.append(f"{ENGINES.get(eng.name.split(':')[0], (eng.name,))[0]} : {exc}")
                self.engines.pop(0)              # moteur écarté pour la suite de cette traduction
                continue
            if eng.name not in self.used:
                self.used.append(eng.name)
            return out
        raise TranslationError("Aucun moteur n'a pu traduire.\n" + "\n".join(self.failures[-4:]))


def _chunks(texts: list[str], max_chars: int, max_items: int = 128):
    cur, n = [], 0
    for t in texts:
        if cur and (n + len(t) > max_chars or len(cur) >= max_items):
            yield cur
            cur, n = [], 0
        cur.append(t)
        n += len(t)
    if cur:
        yield cur


def _http_get(url: str, timeout: float = 30):
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=timeout) as r:
            return json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        if exc.code == 429:
            raise TranslationError("Trop de demandes : le service limite pour l'instant.") from exc
        raise TranslationError(f"Le service a répondu {exc.code}.") from exc
    except (urllib.error.URLError, TimeoutError, OSError, ValueError) as exc:
        raise TranslationError(f"Service injoignable : {exc}") from exc


class LLMTranslator:
    """Traduction par le modèle de langage configuré (Ollama : hors ligne, sur le PC)."""

    def __init__(self, kind: str, model: str, base_url: str = "", api_key: str = ""):
        from .highlights import LLMEngine
        self.engine = LLMEngine(kind, model, base_url, api_key)
        self.name = f"llm:{model}"

    def translate(self, texts, src, dst):
        numbered = {str(i + 1): t for i, t in enumerate(texts)}
        prompt = (f"Traduis chaque phrase de {LANG_NAMES.get(src or '', 'la langue d’origine')} vers "
                  f"{LANG_NAMES.get(dst, dst)}, pour des sous-titres et un doublage : naturel, fidèle, sans rien "
                  "ajouter, d'une longueur proche de l'original (la voix doit tenir dans le même temps). "
                  "Conserve tels quels les repères de la forme [[0]]. Réponds UNIQUEMENT en JSON "
                  '{"1": "...", "2": "..."} avec les mêmes numéros.\n\n' + json.dumps(numbered, ensure_ascii=False))
        try:
            content = self.engine._ask(prompt)
        except LLMError as exc:
            raise TranslationError(str(exc)) from exc
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


def settings_cfg(get: Callable, engine=None) -> dict:
    """Réglages d'un moteur à partir des paramètres enregistrés (`get(clé, défaut)`) ; « auto » par défaut."""
    cfg = dict(engine) if isinstance(engine, dict) else {"engine": engine}
    cfg["engine"] = cfg.get("engine") or get("translate_engine", None) or "auto"
    cfg["llm"] = get("llm", None) or {}
    cfg["libre"] = get("libretranslate", None) or {}
    cfg["microsoft"] = {"region": get("azure_translator_region", "") or ""}
    return cfg


def configured(kind: str, cfg: dict, secret: Callable[[str], str | None]) -> bool:
    """Le moteur a ce qu'il lui faut (clé, adresse, modèle) ; les moteurs gratuits sans clé le sont toujours."""
    if kind == "llm":
        llm = cfg.get("llm") or {}
        return llm.get("kind") in ("ollama", "openai") and bool(llm.get("model"))
    if kind == "libre":
        return bool((cfg.get("libre") or {}).get("url"))
    key = {"deepl": "deepl_api_key", "google": "google_api_key", "microsoft": "azure_translator_key"}.get(kind)
    return bool(secret(key)) if key else kind in ENGINES


def make_translator(cfg: dict, secret: Callable[[str], str | None]) -> Translator:
    """cfg : {"engine": "auto" | clé de ENGINES, "llm": réglages du modèle, "libre": {"url"},
    "microsoft": {"region"}}. « auto » (et un moteur indisponible) : le meilleur disponible, puis les suivants."""
    kind = cfg.get("engine") or "auto"
    if kind == "auto":
        return FallbackTranslator([_one(k, cfg, secret) for k in AUTO_ORDER if configured(k, cfg, secret)])
    if kind not in ENGINES:
        raise TranslationError(f"Moteur de traduction inconnu : {kind}")
    return _one(kind, cfg, secret)


def _one(kind: str, cfg: dict, secret: Callable[[str], str | None]) -> Translator:
    if kind == "deepl":
        return DeepLTranslator(secret("deepl_api_key") or "", cfg.get("base_url", ""))
    if kind == "google":
        return GoogleTranslator(secret("google_api_key") or "", cfg.get("base_url", ""))
    if kind == "microsoft":
        return MicrosoftTranslator(secret("azure_translator_key") or "", (cfg.get("microsoft") or {}).get("region", ""))
    if kind == "google_free":
        return GoogleFreeTranslator(cfg.get("base_url", ""))
    if kind == "mymemory":
        return MyMemoryTranslator(cfg.get("base_url", ""))
    if kind == "libre":
        return LibreTranslator((cfg.get("libre") or {}).get("url", ""), secret("libretranslate_api_key") or "")
    llm = cfg.get("llm") or {}
    if llm.get("kind") not in ("ollama", "openai") or not llm.get("model"):
        raise TranslationError("Aucun modèle de langage configuré (Réglages → Modèle de langage).")
    return LLMTranslator(llm["kind"], llm["model"], llm.get("base_url", ""),
                         (secret("llm_api_key") or "") if llm["kind"] == "openai" else "")


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
