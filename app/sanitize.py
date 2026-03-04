"""Ingress sanitization helpers for prompt-injection hardening."""

from __future__ import annotations

import html
import re
import unicodedata
from typing import Tuple

from app import email_preprocess

INVISIBLE_RE = re.compile(r"[\u200b-\u200f\u202a-\u202e\u2060-\u2064\ufeff]")
_HIDDEN_STYLE_RE = re.compile(r"display\s*:\s*none|visibility\s*:\s*hidden|font-size\s*:\s*0(?:px|em|rem|%)?", re.IGNORECASE)


def sanitize_text(text: str) -> Tuple[str, bool]:
    """Strip invisible Unicode controls and normalize text for storage/LLM use."""
    value = text or ""
    had_invisible = bool(INVISIBLE_RE.search(value))
    value = INVISIBLE_RE.sub("", value)
    value = unicodedata.normalize("NFKC", value)
    return value, had_invisible


def _strip_hidden_html_elements(content: str) -> Tuple[str, bool]:
    """Remove commonly hidden HTML nodes before text extraction."""
    had_hidden = False
    html_in = content or ""

    try:
        from bs4 import BeautifulSoup  # type: ignore

        soup = BeautifulSoup(html_in, "html.parser")
        for tag in soup(["script", "style", "noscript"]):
            tag.decompose()
            had_hidden = True

        for tag in soup.find_all(True):
            style = (tag.attrs.get("style") or "") if isinstance(tag.attrs, dict) else ""
            style_lower = style.lower()
            classes = " ".join(tag.get("class", [])) if hasattr(tag, "get") else ""
            hidden_attr = tag.attrs.get("hidden") if isinstance(tag.attrs, dict) else None

            color_hidden = "color:#fff" in style_lower and "background" in style_lower and "#fff" in style_lower
            if hidden_attr is not None or "sr-only" in classes.lower() or _HIDDEN_STYLE_RE.search(style_lower) or color_hidden:
                tag.decompose()
                had_hidden = True

        return str(soup), had_hidden
    except Exception:
        cleaned = re.sub(r"<(script|style|noscript)[^>]*>.*?</\1>", " ", html_in, flags=re.IGNORECASE | re.DOTALL)
        cleaned2 = re.sub(
            r"<([a-zA-Z0-9]+)([^>]*)style\s*=\s*['\"][^'\"]*(display\s*:\s*none|visibility\s*:\s*hidden|font-size\s*:\s*0)[^'\"]*['\"][^>]*>.*?</\1>",
            " ",
            cleaned,
            flags=re.IGNORECASE | re.DOTALL,
        )
        had_hidden = cleaned2 != html_in
        return cleaned2, had_hidden


def sanitize_ingress_text(text: str, *, is_html: bool = False) -> Tuple[str, dict]:
    """Return sanitized plain text plus flags about removed suspicious content."""
    value = text or ""
    had_hidden = False
    if is_html:
        value, had_hidden = _strip_hidden_html_elements(value)
        value = email_preprocess.html_to_text(value)
        value = html.unescape(value)
    value, had_invisible = sanitize_text(value)
    value = email_preprocess.normalise_whitespace(value)
    return value, {"had_invisible": had_invisible, "had_hidden_html": had_hidden}


__all__ = ["sanitize_text", "sanitize_ingress_text"]
