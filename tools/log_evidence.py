from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from app.sanitizer import sanitize_public_text

MAX_EVENTS = 25
MAX_DETAIL_LEN = 200
INCIDENT_THRESHOLD = 3
FIXTURE_PATH = Path(__file__).resolve().parents[1] / "tests" / "fixtures" / "fake_logs.jsonl"


@dataclass
class LogEntry:
    ts: datetime
    tenant: str
    service: str
    level: str
    event_type: str
    message: str
    status_code: Optional[int] = None
    request_id: Optional[str] = None
    latency_ms: Optional[int] = None


def _parse_ts(ts: str) -> datetime:
    return datetime.fromisoformat(ts.replace("Z", "+00:00")).astimezone(timezone.utc)


def _truncate(detail: str) -> str:
    clean = re.sub(r"Authorization:\s*\S+", "[REDACTED]", detail)
    clean = re.sub(r"Bearer\s+\S+", "[REDACTED]", clean)
    clean = re.sub(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}", "[REDACTED]", clean)
    clean = re.sub(r"req-[A-Za-z0-9-]+", "[REDACTED]", clean)
    return clean[:MAX_DETAIL_LEN]


def _load_fixture(path: Path) -> List[LogEntry]:
    entries: List[LogEntry] = []
    if not path.exists():
        return entries
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        record = json.loads(line)
        entries.append(
            LogEntry(
                ts=_parse_ts(record["ts"]),
                tenant=record.get("tenant") or "",
                service=record.get("service") or "api",
                level=record.get("level") or "INFO",
                event_type=record.get("event_type") or "",
                message=record.get("message") or "",
                status_code=record.get("status_code"),
                request_id=record.get("request_id"),
                latency_ms=record.get("latency_ms"),
            )
        )
    return entries


class FakeLogSource:
    def __init__(self, path: Path = FIXTURE_PATH) -> None:
        self.entries = _load_fixture(path)

    def query(self, *, tenant: Optional[str], service: str, start: datetime, end: datetime) -> List[LogEntry]:
        results: List[LogEntry] = []
        for e in self.entries:
            if tenant and e.tenant != tenant:
                continue
            if e.service != service:
                continue
            if start <= e.ts <= end:
                results.append(e)
        return results


def _count_events(entries: List[LogEntry]) -> Dict[str, Any]:
    errors = [e for e in entries if e.level.upper() == "ERROR" or (e.status_code or 0) >= 500]
    warnings = [e for e in entries if e.level.upper() == "WARN"]
    info = [e for e in entries if e.level.upper() == "INFO"]
    unique_error_types = sorted({e.event_type for e in errors})
    first_ts = min((e.ts for e in entries), default=None)
    last_ts = max((e.ts for e in entries), default=None)
    return {
        "errors": len(errors),
        "warnings": len(warnings),
        "info": len(info),
        "total_events": len(entries),
        "unique_error_types": unique_error_types,
        "first_ts": first_ts.isoformat().replace("+00:00", "Z") if first_ts else None,
        "last_ts": last_ts.isoformat().replace("+00:00", "Z") if last_ts else None,
    }


def _select_incident_entries(entries: List[LogEntry], query_type: str) -> List[LogEntry]:
    if query_type == "errors":
        return [e for e in entries if e.level.upper() == "ERROR" or (e.status_code or 0) >= 500]
    if query_type == "timeouts":
        return [e for e in entries if (e.status_code == 504) or ("timeout" in e.event_type.lower())]
    return [e for e in entries if (e.status_code or 0) >= 500 or "service_down" in e.event_type.lower()]


def _window_from_entries(entries: List[LogEntry]) -> Optional[Dict[str, str]]:
    if not entries:
        return None
    start = min(e.ts for e in entries)
    end = max(e.ts for e in entries)
    return {"start": start.isoformat().replace("+00:00", "Z"), "end": end.isoformat().replace("+00:00", "Z")}


def _observed_incident(incident_entries: List[LogEntry], query_type: str) -> bool:
    metric = len(incident_entries)
    return metric >= INCIDENT_THRESHOLD


def _sample_events(entries: List[LogEntry]) -> List[Dict[str, Any]]:
    sample = entries[:MAX_EVENTS]
    serialized: List[Dict[str, Any]] = []
    for e in sample:
        serialized.append(
            {
                "ts": e.ts.isoformat().replace("+00:00", "Z"),
                "type": e.event_type,
                "id": None,
                "message_id": None,
                "detail": _truncate(f"{e.level} {e.event_type} {e.message}"),
            }
        )
    return serialized


def _summaries(service: str, query_type: str, counts: Dict[str, Any], incident_window: Optional[Dict[str, str]], 
               observed: bool, coverage: str) -> Dict[str, str]:
    summary_external = (
        f"We checked service logs for {service} and "
        f"{'observed' if observed else 'did not observe'} {query_type} patterns in the checked window."
    )
    if not counts.get("total_events"):
        summary_external = "We checked service logs and did not find entries for the requested window."
    summary_internal = (
        f"coverage={coverage}; window={incident_window}; counts="
        f"errors={counts.get('errors',0)}, warnings={counts.get('warnings',0)}, total={counts.get('total_events',0)}; "
        f"unique_errors={counts.get('unique_error_types', [])}"
    )
    return {"external": sanitize_public_text(_truncate(summary_external)), "internal": summary_internal}


def run_log_evidence(params: Dict[str, Any]) -> Dict[str, Any]:
    """
    log_evidence: Inspect log signals for downtime indicators.
    """
    time_window = params["time_window"]
    incident_window_param = params.get("incident_window")
    tenant = params.get("tenant")
    service = params.get("service")
    service_inferred_from = None
    if not service and tenant and tenant.lower() in {"api", "worker", "app", "ui", "backend"}:
        service = tenant
        tenant = None  # treat tenant value as service hint
        service_inferred_from = "tenant_as_service"
    service = service or "api"
    query_type = params.get("query_type") or "errors"
    reason = params.get("reason") or "triage_outage_check"

    start = _parse_ts(time_window["start"])
    end = _parse_ts(time_window["end"])
    incident_start = _parse_ts(incident_window_param["start"]) if incident_window_param and incident_window_param.get("start") else start
    incident_end = _parse_ts(incident_window_param["end"]) if incident_window_param and incident_window_param.get("end") else end

    fixture_param = params.get("fixture_path")
    fixture_path = Path(fixture_param) if fixture_param else None
    source = FakeLogSource(fixture_path) if fixture_path else FakeLogSource()
    entries = source.query(tenant=tenant, service=service, start=start, end=end)
    counts = _count_events(entries)

    incident_entries = [e for e in entries if incident_start <= e.ts <= incident_end]
    incident_filtered = _select_incident_entries(incident_entries, query_type)
    observed = _observed_incident(incident_filtered, query_type)
    signal_entries = [e for e in incident_entries if e.level.upper() != "INFO" or (e.status_code or 0) >= 400]
    incident_window_start_dt = min((e.ts for e in incident_filtered), default=incident_start)
    incident_window_start_dt = incident_window_start_dt if incident_window_start_dt >= incident_start else incident_start
    end_candidates = incident_filtered if incident_filtered else incident_entries
    incident_window_end_dt = max((e.ts for e in end_candidates), default=incident_end)
    incident_window_end_dt = min(incident_window_end_dt, incident_end)
    incident_window = {
        "start": incident_window_start_dt.isoformat().replace("+00:00", "Z"),
        "end": incident_window_end_dt.isoformat().replace("+00:00", "Z"),
    }
    incident_signals: List[str] = []
    if len([e for e in incident_filtered if (e.status_code or 0) >= 500]) >= INCIDENT_THRESHOLD:
        incident_signals.append("5xx_burst")
    if len([e for e in incident_filtered if "timeout" in e.event_type.lower()]) >= INCIDENT_THRESHOLD:
        incident_signals.append("timeouts")
    if len(incident_filtered) >= INCIDENT_THRESHOLD and any("service_down" in e.event_type.lower() for e in incident_filtered):
        incident_signals.append("availability_drop")
    incident_score = min(100, max(0, len(incident_signals) * 30))
    decision = "corroborated" if observed else ("inconclusive" if incident_signals else "not_observed")
    confidence = 0.2 if not observed else min(1.0, 0.5 + len(incident_filtered) / 10)
    summaries = _summaries(service, query_type, counts, incident_window, observed, "fixture")
    events = _sample_events(incident_filtered if observed else entries)

    return {
        "source": "logs",
        "evidence_type": "logs",
        "tenant": tenant,
        "service": service,
        "time_window": {"start": time_window["start"], "end": time_window["end"]},
        "incident_window": incident_window,
        "observed_incident": observed,
        "decision": decision,
        "incident_score": incident_score,
        "incident_signals": incident_signals,
        "confidence": confidence,
        "summary_counts": {
            "errors": counts["errors"],
            "warnings": counts["warnings"],
            "info": counts["info"],
            "unique_error_types": counts["unique_error_types"],
            "total_events": counts["total_events"],
            "first_ts": counts["first_ts"],
            "last_ts": counts["last_ts"],
        },
        "summary_internal": summaries["internal"],
        "summary_external": summaries["external"],
        "metadata": {
            "query_type": query_type,
            "log_entry_count": len(entries),
            "coverage": "fixture",
            "reason": reason,
            "incident_eval_window": incident_window,
            "note": "Logs are sampled; absence of evidence is not proof of absence.",
            "service_inferred_from": service_inferred_from,
            "fixture_path": str(fixture_path) if fixture_path else None,
        },
        "events": events,
    }
