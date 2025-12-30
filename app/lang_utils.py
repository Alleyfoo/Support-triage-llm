import re
from typing import List, Dict

try:  # pragma: no cover - optional dependency
    import langid  # type: ignore
except Exception:  # pragma: no cover
    langid = None


def segment_sentences(text: str) -> List[Dict]:
    pattern = re.compile(r'[^.!?]+[.!?]*', re.MULTILINE)
    segments: List[Dict] = []
    for match in pattern.finditer(text):
        segments.append({'start': match.start(), 'end': match.end(), 'text': text[match.start():match.end()]})
    return segments


def detect_lang(text: str) -> str:
    if langid:
        lang, _ = langid.classify(text)
        return lang
    if re.search(r'[åäöÅÄÖ]', text):
        return 'fi'
    fi_words = {'on', 'ja', 'tämä', 'hyvä', 'takki', 'kaupungilla', 'suosittu', 'malli', 'klassikko'}
    tokens = re.findall(r'\w+', text.lower())
    if any(t in fi_words for t in tokens):
        return 'fi'
    return 'en'


def lang_spans(text: str) -> List[Dict]:
    spans: List[Dict] = []
    for match in re.finditer(r'\b\w+\b', text, flags=re.UNICODE):
        token = match.group(0)
        lang = detect_lang(token)
        spans.append({'start': match.start(), 'end': match.end(), 'lang': lang, 'text': token})
    return spans


def mask_terms(text: str, terms: List[str]) -> str:
    if not terms:
        return text
    for term in terms:
        text = re.sub(re.escape(term), lambda m: f"<TERM>{m.group(0)}</TERM>", text)
    return text
