from __future__ import annotations

import re
from typing import Optional

FOOTER_TEMPLATE = "\n\n--\nInternal Ref: {case_id}"
FOOTER_REGEX = re.compile(r"Internal Ref:\s*([a-zA-Z0-9\-_]+)", re.IGNORECASE)
BODY_SIZE_CAP = 100_000


def append_footer(body: str, case_id: str) -> str:
    """Append the internal reference footer to the body (deduped) and cap length."""
    body = body or ""
    body = strip_footer(body)
    capped = body[:BODY_SIZE_CAP]
    return capped + FOOTER_TEMPLATE.format(case_id=case_id)


def extract_case_id(text: str) -> Optional[str]:
    match = FOOTER_REGEX.search(text or "")
    if match:
        return match.group(1).strip()
    return None


def strip_footer(text: str) -> str:
    """Remove the Internal Ref footer line to avoid polluting diff calculations."""
    if not text:
        return ""
    lines = text.splitlines()
    cleaned = [line for line in lines if not FOOTER_REGEX.search(line)]
    trimmed = "\n".join(cleaned).strip()
    return trimmed[:BODY_SIZE_CAP]
