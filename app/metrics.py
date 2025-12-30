"""Simple in-memory metrics scaffold (placeholder for D-phase)."""

from __future__ import annotations

import time
from collections import defaultdict
from typing import Dict

_COUNTERS: Dict[str, int] = defaultdict(int)
_TIMINGS: Dict[str, list] = defaultdict(list)


def incr(name: str, amount: int = 1) -> None:
    _COUNTERS[name] += amount


def timing(name: str, duration_seconds: float) -> None:
    _TIMINGS[name].append(duration_seconds)


def snapshot() -> Dict[str, object]:
    return {
        "counters": dict(_COUNTERS),
        "timings": {k: {"count": len(v), "p50_ms": _percentile_ms(v, 50), "p95_ms": _percentile_ms(v, 95)} for k, v in _TIMINGS.items()},
    }


def _percentile_ms(samples, pct: float) -> float:
    if not samples:
        return 0.0
    ordered = sorted(samples)
    idx = int(len(ordered) * (pct / 100))
    idx = min(max(idx, 0), len(ordered) - 1)
    return ordered[idx] * 1000
