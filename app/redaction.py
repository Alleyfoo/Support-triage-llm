"""Minimal PII redaction utilities."""

from __future__ import annotations

import re
from typing import Dict

EMAIL_PATTERN = re.compile(r"\b[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}\b")
PHONE_PATTERN = re.compile(
    r"(?<!\d)(?:\+?\d{1,3}[\s-]?)?(?:\(?\d{2,4}\)?[\s-]?)?\d{3}[\s-]?\d{3,4}(?!\d)"
)


def redact(text: str) -> Dict[str, object]:
    """Return redacted text plus basic stats."""
    if not text:
        return {"redacted_text": "", "redaction_applied": False}
    redacted = EMAIL_PATTERN.sub("[REDACTED_EMAIL]", text)
    redacted = PHONE_PATTERN.sub("[REDACTED_PHONE]", redacted)
    return {
        "redacted_text": redacted,
        "redaction_applied": redacted != text,
    }
