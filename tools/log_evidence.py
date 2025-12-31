from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List

MAX_EVENTS = 50
MAX_DETAIL_LEN = 200
INCIDENT_THRESHOLD = 3
FIXTURE_PATH = Path(__file__).resolve().parents[1] / "tests" / "fixtures" / "logs" / "fake_logs.jsonl"


@dataclass
class LogEntry:
    ts: datetime
    service: str
    level: str
    event_type: str
    status_code: int
    message: str


def _parse_ts(ts: str) -> datetime:
    return datetime.fromisoformat(ts.replace("Z", "+00:00")).astimezone(timezone.utc)


def _truncate(detail: str) -> str:
    clean = re.sub(r"Authorization:\s*\S+", "[REDACTED]", detail)
    clean = re.sub(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}", "[REDACTED]", clean)
    return clean[:MAX_DETAIL_LEN]


def _load_fixture(path: Path) -> List[LogEntry]:
    entries: List[LogEntry] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        record = json.loads(line)
        entries.append(
            LogEntry(
                ts=_parse_ts(record["ts"]),
                service=record["service"],
                level=record["level"],
                event_type=record["event_type"],
                status_code=int(record["status_code"]),
                message=record["message"],
            )
        )
    return entries


class FakeLogSource:
    def __init__(self, path: Path = FIXTURE_PATH) -> None:
        self.entries = _load_fixture(path)

    def query(self, service: str, start: datetime, end: datetime) -> List[LogEntry]:
        return [e for e in self.entries if e.service == service and start <= e.ts <= end]


def _count_events(entries: List[LogEntry]) -> Dict[str, int]:
    errors = sum(1 for e in entries if e.status_code >= 500)
    timeouts = sum(1 for e in entries if e.status_code == 504 or "timeout" in e.event_type.lower())
    availability = sum(
        1
        for e in entries
        if e.status_code >= 500
        or "unavailable" in e.message.lower()
        or "service_down" in e.event_type.lower()
    )
    return {
        "errors": errors,
        "timeouts": timeouts,
        "availability_gaps": availability,
        "total_events": len(entries),
    }


def _incident_window(entries: List[LogEntry]) -> Dict[str, str] | None:
    if not entries:
        return None
    start = min(e.ts for e in entries)
    end = max(e.ts for e in entries)
    return {"start": start.isoformat().replace("+00:00", "Z"), "end": end.isoformat().replace("+00:00", "Z")}


def _bad_entries(entries: List[LogEntry], query_type: str) -> List[LogEntry]:
    if query_type == "errors":
        return [e for e in entries if e.status_code >= 500]
    if query_type == "timeouts":
        return [e for e in entries if e.status_code == 504 or "timeout" in e.event_type.lower()]
    return [
        e
        for e in entries
        if e.status_code >= 500 or "unavailable" in e.message.lower() or "service_down" in e.event_type.lower()
    ]


def _should_flag_incident(counts: Dict[str, int], query_type: str) -> bool:
    metric = counts.get({"errors": "errors", "timeouts": "timeouts", "availability": "availability_gaps"}[query_type], 0)
    return metric >= INCIDENT_THRESHOLD


def _summarize_events(
    service: str,
    query_type: str,
    counts: Dict[str, int],
    window: Dict[str, str] | None,
    observed: bool,
    metric_count: int,
) -> List[Dict[str, Any]]:
    events: List[Dict[str, Any]] = []
    detail_parts = []
    if observed and window:
        detail_parts.append(
            f"{query_type} signals for {service} between {window['start']} and {window['end']} ({metric_count} events)"
        )
    elif observed:
        detail_parts.append(f"{query_type} signals for {service} detected ({metric_count} events)")
    else:
        detail_parts.append(f"No {query_type} anomalies observed for {service} in window")
    detail_parts.append(
        f"errors={counts.get('errors',0)}, timeouts={counts.get('timeouts',0)}, availability_gaps={counts.get('availability_gaps',0)}, total={counts.get('total_events',0)}"
    )
    detail = _truncate("; ".join(detail_parts))
    events.append(
        {
            "ts": (window["start"] if window else datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")),
            "type": f"{query_type}_summary",
            "id": "log-1",
            "message_id": None,
            "detail": detail,
        }
    )
    return events[:MAX_EVENTS]


def run_log_evidence(params: Dict[str, Any]) -> Dict[str, Any]:
    """
    log_evidence: Inspect log signals for downtime indicators.
    """
    service = params.get("service") or params.get("tenant") or "api"
    time_window = params["time_window"]
    query_type = params["query_type"]
    start = _parse_ts(time_window["start"])
    end = _parse_ts(time_window["end"])

    source = FakeLogSource()
    entries = source.query(service, start, end)
    counts = _count_events(entries)
    observed_incident = _should_flag_incident(counts, query_type)
    incident_entries = _bad_entries(entries, query_type) if observed_incident else []
    incident_win = _incident_window(incident_entries)
    confidence = 0.2
    metric_key = {"errors": "errors", "timeouts": "timeouts", "availability": "availability_gaps"}[query_type]
    metric_count = counts.get(metric_key, 0)
    if observed_incident:
        confidence = min(1.0, 0.5 + metric_count / 10)
    events = _summarize_events(service, query_type, counts, incident_win, observed_incident, metric_count)

    return {
        "source": "logs",
        "evidence_type": "logs",
        "time_window": time_window,
        "incident_window": incident_win or {"start": time_window["start"], "end": time_window["end"]},
        "tenant": params.get("tenant"),
        "observed_incident": observed_incident,
        "confidence": confidence,
        "summary_counts": {
            "sent": 0,
            "bounced": 0,
            "deferred": 0,
            "delivered": 0,
            "errors": counts["errors"],
            "timeouts": counts["timeouts"],
            "availability_gaps": counts["availability_gaps"],
            "total_events": counts["total_events"],
        },
        "metadata": {"query_type": query_type, "log_entry_count": len(entries)},
        "events": events,
    }
