from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from typing import Dict, Optional

ISO_PATTERN = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}")
DATE_PATTERN = re.compile(r"\b(?P<year>\d{4})-(?P<month>\d{2})-(?P<day>\d{2})\b")
MONTH_DAY_PATTERN = re.compile(
    r"\b(?P<month>jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|jun(?:e)?|jul(?:y)?|aug(?:ust)?|sep(?:t|tember)|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)\s+"
    r"(?P<day>\d{1,2})(?:st|nd|rd|th)?(?:,?\s+(?P<year>\d{4}))?\b",
    re.IGNORECASE,
)
CLOCK_PATTERN = re.compile(r"\b(?P<hour>\d{1,2}):(?P<minute>\d{2})(?:\s*(?P<ampm>am|pm))?\s*(?P<tz>utc|z)?", re.IGNORECASE)
RANGE_PATTERN = re.compile(
    r"\b(?:between\s+)?(?P<start>\d{1,2}:\d{2})\s*(?:-|to|–|—)\s*(?P<end>\d{1,2}:\d{2})\s*(?P<tz>utc|z)?",
    re.IGNORECASE,
)

MONTH_LOOKUP = {
    "jan": 1,
    "feb": 2,
    "mar": 3,
    "apr": 4,
    "may": 5,
    "jun": 6,
    "jul": 7,
    "aug": 8,
    "sep": 9,
    "sept": 9,
    "oct": 10,
    "nov": 11,
    "dec": 12,
}


def _combine_date_time(base: datetime, clock_match: Optional[re.Match[str]]) -> datetime:
    """Attach a clock time (and am/pm) to a date; defaults to same date."""
    if not clock_match:
        return base
    hour = int(clock_match.group("hour"))
    minute = int(clock_match.group("minute"))
    ampm = clock_match.group("ampm")
    if ampm:
        ampm = ampm.lower()
        if ampm == "pm" and hour < 12:
            hour += 12
        if ampm == "am" and hour == 12:
            hour = 0
    return base.replace(hour=hour % 24, minute=minute)


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def parse_time_window(text: str, now: Optional[datetime] = None) -> Dict[str, object]:
    lower = text.lower()
    now = now or datetime.now(timezone.utc)
    start_dt: Optional[datetime] = None
    end_dt: Optional[datetime] = None
    confidence = 0.1

    iso = ISO_PATTERN.search(text)
    clock = CLOCK_PATTERN.search(text)
    clock_range = RANGE_PATTERN.search(text)
    reason = "parsed_from_text"

    if iso:
        try:
            dt = datetime.fromisoformat(iso.group(0))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            start_dt = dt.astimezone(timezone.utc)
            end_dt = start_dt + timedelta(hours=2)
            confidence = 0.8
        except Exception:
            pass
    elif clock_range:
        try:
            sh, sm = clock_range.group("start").split(":")
            eh, em = clock_range.group("end").split(":")
            start_dt = datetime(now.year, now.month, now.day, int(sh), int(sm), tzinfo=timezone.utc)
            end_dt = datetime(now.year, now.month, now.day, int(eh), int(em), tzinfo=timezone.utc)
            confidence = 0.7
        except Exception:
            pass
    else:
        date_only = DATE_PATTERN.search(text)
        month_day = MONTH_DAY_PATTERN.search(text)
        if date_only:
            year = int(date_only.group("year"))
            month = int(date_only.group("month"))
            day = int(date_only.group("day"))
            start_dt = datetime(year, month, day, tzinfo=timezone.utc)
            confidence = 0.6
        elif month_day:
            month_name = month_day.group("month").lower()
            month = MONTH_LOOKUP.get(month_name[:3], now.month)
            day = int(month_day.group("day"))
            year = int(month_day.group("year") or now.year)
            start_dt = datetime(year, month, day, tzinfo=timezone.utc)
            confidence = 0.55

        if "yesterday" in lower or "last night" in lower:
            confidence = 0.35
            if clock and not start_dt:
                try:
                    base = datetime(now.year, now.month, now.day, tzinfo=timezone.utc) - timedelta(days=1)
                    start_dt = _combine_date_time(base, clock)
                except Exception:
                    start_dt = None
        elif any(token in lower for token in ["today", "this morning", "this afternoon", "this evening"]):
            confidence = 0.35
            if clock and not start_dt:
                try:
                    base = datetime(now.year, now.month, now.day, tzinfo=timezone.utc)
                    start_dt = _combine_date_time(base, clock)
                except Exception:
                    start_dt = None

    if start_dt and clock:
        start_dt = _combine_date_time(start_dt, clock)
        end_dt = start_dt + timedelta(hours=2)
    elif clock and not start_dt:
        try:
            start_dt = _combine_date_time(datetime(now.year, now.month, now.day, tzinfo=timezone.utc), clock)
            end_dt = start_dt + timedelta(hours=2)
        except Exception:
            start_dt = None

    if start_dt and not end_dt:
        end_dt = start_dt + timedelta(hours=36)

    start = _iso(start_dt) if start_dt else None
    end = _iso(end_dt) if end_dt else None

    if not start:
        reason = "no_time_found"

    return {"start": start, "end": end, "confidence": confidence, "reason": reason}
