from __future__ import annotations

from datetime import datetime, timezone
from typing import Dict, Optional


def isoformat(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def parse_iso(ts: str) -> datetime:
    return datetime.fromisoformat(ts.replace("Z", "+00:00")).astimezone(timezone.utc)


def has_window(window: Optional[Dict[str, Optional[str]]]) -> bool:
    if not window:
        return False
    return bool(window.get("start") and window.get("end"))


def iou(window_a: Optional[Dict[str, Optional[str]]], window_b: Optional[Dict[str, Optional[str]]]) -> float:
    if not (has_window(window_a) and has_window(window_b)):
        return 0.0
    start_a = parse_iso(window_a["start"])
    end_a = parse_iso(window_a["end"])
    start_b = parse_iso(window_b["start"])
    end_b = parse_iso(window_b["end"])
    latest_start = max(start_a, start_b)
    earliest_end = min(end_a, end_b)
    overlap = (earliest_end - latest_start).total_seconds()
    if overlap <= 0:
        return 0.0
    union = (max(end_a, end_b) - min(start_a, start_b)).total_seconds()
    if union <= 0:
        return 0.0
    return round(overlap / union, 4)


def boundary_delta_seconds(
    predicted: Optional[Dict[str, Optional[str]]], expected: Optional[Dict[str, Optional[str]]]
) -> Dict[str, Optional[float]]:
    if not (has_window(predicted) and has_window(expected)):
        return {"start_delta_sec": None, "end_delta_sec": None}
    pred_start = parse_iso(predicted["start"])
    pred_end = parse_iso(predicted["end"])
    exp_start = parse_iso(expected["start"])
    exp_end = parse_iso(expected["end"])
    return {
        "start_delta_sec": (pred_start - exp_start).total_seconds(),
        "end_delta_sec": (pred_end - exp_end).total_seconds(),
    }
