from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from typing import Dict, Optional

ISO_PATTERN = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}")


def parse_time_window(text: str) -> Dict[str, object]:
    lower = text.lower()
    now = datetime.now(timezone.utc)
    start: Optional[str] = None
    end: Optional[str] = None
    confidence = 0.1

    iso = ISO_PATTERN.search(text)
    if iso:
        try:
            dt = datetime.fromisoformat(iso.group(0))
            start = dt.isoformat() + "Z"
            end = None
            confidence = 0.8
        except Exception:
            pass
    elif "yesterday" in lower or "last night" in lower:
        # Relative hints stay null to avoid inventing timestamps; confidence remains low.
        start = None
        end = None
        confidence = 0.3
    elif "this morning" in lower or "today" in lower:
        start = None
        end = None
        confidence = 0.3

    return {"start": start, "end": end, "confidence": confidence}
