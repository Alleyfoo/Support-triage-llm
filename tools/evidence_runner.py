from __future__ import annotations

import json
import re
from typing import Any, Dict, Tuple

from app import queue_db
from tools import registry
from app.sanitizer import sanitize_public_text


INTERNAL_HOST_RE = re.compile(r"\b[\w.-]+\.(internal|corp|svc|local)\b", re.IGNORECASE)


def _redact(text: str) -> str:
    text = text or ""
    text = re.sub(r"Authorization:\s*\S+", "[REDACTED]", text)
    text = re.sub(r"Bearer\s+\S+", "[REDACTED]", text)
    text = re.sub(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}", "[REDACTED]", text)
    text = INTERNAL_HOST_RE.sub("[REDACTED]", text)
    return text


def _summarize_log_evidence(result: Dict[str, Any]) -> str:
    query_type = (result.get("metadata") or {}).get("query_type") or "errors"
    observed = result.get("observed_incident")
    decision = result.get("decision")
    window = result.get("incident_window") or result.get("time_window") or {}
    start = window.get("start") or ""
    end = window.get("end") or ""
    counts = result.get("summary_counts") or {}
    decision_phrase = {
        "corroborated": "observed error patterns",
        "inconclusive": "saw some anomalies",
        "not_observed": "did not observe anomalies",
    }.get(decision, "checked logs")
    if observed:
        msg = f"{decision_phrase} between {start} and {end} (errors={counts.get('errors',0)}, timeouts={counts.get('timeouts',0)}, availability_gaps={counts.get('availability_gaps',0)})"
    else:
        msg = f"{decision_phrase} in the checked window {start}–{end} (errors={counts.get('errors',0)}, timeouts={counts.get('timeouts',0)})"
    return sanitize_public_text(_redact(msg))


def _summarize_service_status(result: Dict[str, Any]) -> str:
    metadata = result.get("metadata") or {}
    service_id = metadata.get("service_id") or "unknown"
    status = metadata.get("status") or "unknown"
    http_status = metadata.get("http_status")
    latency = metadata.get("latency_ms")
    parts = [f"{service_id} status={status}"]
    if http_status is not None:
        parts.append(f"http={http_status}")
    if latency is not None:
        parts.append(f"latency_ms={latency}")
    notes = metadata.get("notes") or []
    if notes:
        parts.append(f"notes={'/'.join(notes)}")
    return _redact("; ".join(parts))


def _summary_for_tool(tool_name: str, result: Dict[str, Any]) -> str:
    if tool_name == "log_evidence":
        return _summarize_log_evidence(result)
    if tool_name == "service_status":
        return _summarize_service_status(result)
    return ""


def run_tool_with_evidence(intake_id: str, tool_name: str, params: Dict[str, Any], *, redaction_level: str = "internal", replays_evidence_id: str | None = None, allow_cache: bool = True) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    result: Dict[str, Any] = {}
    summary_external = ""
    status = "ok"
    error_message = None

    cache_hit = False
    checked_at = None
    try:
        result = registry.run_tool(tool_name, params)
        summary_external = _summary_for_tool(tool_name, result)
    except Exception as exc:
        status = "error"
        error_message = str(exc)
        summary_external = ""
        result = {}

    evidence_record = queue_db.record_evidence_run(
        intake_id=intake_id,
        tool_name=tool_name,
        params=params,
        result=result,
        summary_external=summary_external,
        summary_internal=None,
        status=status,
        redaction_level=redaction_level,
        ttl_seconds=None,
        error_message=error_message,
        replays_evidence_id=replays_evidence_id,
        cache_bucketed=allow_cache,
    )

    cache_hit = bool(evidence_record.get("cache_hit"))
    checked_at = evidence_record.get("checked_at")

    if result.get("metadata") is None:
        result["metadata"] = {}
    result["metadata"]["evidence_id"] = evidence_record.get("evidence_id")
    result["metadata"]["summary_external"] = summary_external
    result["metadata"]["cache_hit"] = cache_hit
    result["metadata"]["checked_at"] = checked_at or evidence_record.get("ran_at")
    return evidence_record, result


def replay_evidence(evidence_id: str, force: bool = False) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    existing = queue_db.get_evidence_by_id(evidence_id)
    if not existing:
        raise ValueError("Evidence not found")
    params = json.loads(existing.get("params_json") or "{}")
    tool_name = existing.get("tool_name")
    intake_id = existing.get("intake_id") or ""
    record, result = run_tool_with_evidence(intake_id, tool_name, params, redaction_level=existing.get("redaction_level") or "internal", replays_evidence_id=evidence_id, allow_cache=not force)
    if record.get("cache_hit"):
        # If cache hit reused same record, fabricate a new replay entry to preserve lineage
        record = queue_db.record_evidence_run(
            intake_id=intake_id,
            tool_name=tool_name,
            params=params,
            result=result,
            summary_external=result.get("metadata", {}).get("summary_external") or "",
            summary_internal=None,
            status="ok",
            redaction_level=existing.get("redaction_level") or "internal",
            ttl_seconds=None,
            error_message=None,
            replays_evidence_id=evidence_id,
            cache_bucketed=False,
        )
        record["cache_hit"] = False
        result["metadata"]["cache_hit"] = False
    record["replays_evidence_id"] = evidence_id
    if result.get("metadata") is None:
        result["metadata"] = {}
    result["metadata"]["replays_evidence_id"] = evidence_id
    prev_meta = json.loads(existing.get("result_json_internal") or "{}")
    diff = {
        "previous_checked_at": existing.get("ran_at"),
        "new_checked_at": result.get("metadata", {}).get("checked_at"),
        "hash_changed": record.get("result_hash") != existing.get("result_hash"),
    }
    # simple structured comparisons
    prev_status = prev_meta.get("metadata", {}).get("status") if isinstance(prev_meta, dict) else None
    new_status = result.get("metadata", {}).get("status")
    if prev_status != new_status:
        diff["status_change"] = {"previous": prev_status, "new": new_status}
    record["diff"] = diff
    result["metadata"]["diff"] = diff
    return record, result
