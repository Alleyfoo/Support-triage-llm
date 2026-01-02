"""Utility helpers to strip sensitive tokens from customer-facing text."""

from __future__ import annotations

import re

EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
UUID_RE = re.compile(r"\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\b")
HEX32_RE = re.compile(r"\b[0-9a-fA-F]{32,}\b")
REQID_RE = re.compile(r"\b(req|request|trace)[-_]?[A-Za-z0-9]{6,}\b", re.IGNORECASE)
URL_RE = re.compile(r"https?://\S+")
WHITESPACE_RE = re.compile(r"\s+")


def sanitize_public_text(text: str, *, max_len: int = 240) -> str:
    """Aggressively redact tokens that should not appear in customer output."""
    clean = text or ""
    clean = EMAIL_RE.sub("[REDACTED]", clean)
    clean = UUID_RE.sub("[REDACTED]", clean)
    clean = HEX32_RE.sub("[REDACTED]", clean)
    clean = REQID_RE.sub("[REDACTED]", clean)
    clean = URL_RE.sub("[REDACTED]", clean)
    clean = WHITESPACE_RE.sub(" ", clean).strip()
    if len(clean) > max_len:
        clean = clean[: max_len - 3] + "..."
    return clean


__all__ = ["sanitize_public_text"]
