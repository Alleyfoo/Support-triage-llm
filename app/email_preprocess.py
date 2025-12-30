"""Utilities for normalising inbound customer emails before queueing."""

from __future__ import annotations

import html
import re
from html.parser import HTMLParser
from typing import Iterable, List


class _HTMLStripper(HTMLParser):
    """Simple HTML -> text converter preserving paragraphs."""

    def __init__(self) -> None:
        super().__init__()
        self._chunks: List[str] = []
        self._pending_newline = False

    def handle_starttag(self, tag: str, attrs: Iterable[tuple[str, str | None]]) -> None:
        if tag in {"br", "p", "div", "li"}:
            self._newline()

    def handle_endtag(self, tag: str) -> None:
        if tag in {"p", "div", "li"}:
            self._newline()

    def handle_data(self, data: str) -> None:
        text = data.strip()
        if not text:
            return
        if self._pending_newline and self._chunks:
            self._chunks.append("\n")
        elif self._chunks and self._chunks[-1] != "\n":
            self._chunks.append(" ")
        self._chunks.append(text)
        self._pending_newline = False

    def handle_entityref(self, name: str) -> None:
        self.handle_data(html.unescape(f"&{name};"))

    def handle_charref(self, name: str) -> None:
        if name.startswith("x"):
            value = int(name[1:], 16)
        else:
            value = int(name)
        self.handle_data(chr(value))

    def _newline(self) -> None:
        if self._chunks and self._chunks[-1] != "\n":
            self._chunks.append("\n")
        self._pending_newline = False

    def get_text(self) -> str:
        text = "".join(self._chunks)
        return re.sub(r"\n{3,}", "\n\n", text)


def html_to_text(content: str) -> str:
    """Convert HTML content to plain text using a lightweight parser."""

    stripper = _HTMLStripper()
    try:
        stripper.feed(content)
        stripper.close()
    except Exception:
        return re.sub(r"<[^>]+>", " ", content)
    text = stripper.get_text()
    return text.strip()


_SIGNATURE_MARKERS = (
    "--",
    "thanks",
    "thank you",
    "regards",
    "cheers",
    "sent from my",
)


def strip_signatures(text: str) -> str:
    """Remove simple email signatures from the tail of the message."""

    lines = text.splitlines()
    if not lines:
        return text.strip()
    cutoff = len(lines)
    for idx in range(len(lines) - 1, max(-1, len(lines) - 12), -1):
        candidate = lines[idx].strip().lower()
        if not candidate:
            continue
        if any(candidate.startswith(marker) for marker in _SIGNATURE_MARKERS):
            cutoff = idx
            break
    return "\n".join(lines[:cutoff]).strip()


_QUOTE_PATTERNS = [
    re.compile(r"^>+"),
    re.compile(r"^on .+ wrote:$", re.IGNORECASE),
    re.compile(r"^from:\s", re.IGNORECASE),
    re.compile(r"^sent:\s", re.IGNORECASE),
    re.compile(r"^subject:\s", re.IGNORECASE),
    re.compile(r"^to:\s", re.IGNORECASE),
]


def strip_quoted_replies(text: str) -> str:
    """Remove quoted previous messages and forwarding headers."""

    lines = text.splitlines()
    cleaned: List[str] = []
    skip_block = False
    for line in lines:
        stripped = line.strip()
        if not stripped and not cleaned:
            continue
        if any(pattern.match(stripped) for pattern in _QUOTE_PATTERNS):
            skip_block = True
        if skip_block:
            continue
        cleaned.append(line)
    result = "\n".join(cleaned)
    # Remove any trailing empty lines
    return re.sub(r"\n{3,}", "\n\n", result).strip()


def normalise_whitespace(text: str) -> str:
    """Collapse excessive blank lines and spaces."""

    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\s*\n\s*", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def clean_email(body: str, *, is_html: bool | None = None) -> str:
    """Normalise email body for ingestion."""

    if body is None:
        return ""
    content = body
    detects_html = is_html if is_html is not None else ("<" in body and ">" in body)
    if detects_html:
        content = html_to_text(content)
    content = html.unescape(content)
    content = strip_signatures(content)
    content = strip_quoted_replies(content)
    content = normalise_whitespace(content)
    return content

