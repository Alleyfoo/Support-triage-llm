"""Allowlisted tool registry with schema validation."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Callable, Dict, Optional

import jsonschema

from app.validation import load_schema, SchemaValidationError
from tools.log_evidence import run_log_evidence
from tools.service_status import run_service_status


@dataclass
class Tool:
    name: str
    params_schema: Dict[str, Any]
    result_schema: Dict[str, Any]
    fn: Callable[[Dict[str, Any]], Dict[str, Any]]


def _validate(instance: Dict[str, Any], schema: Dict[str, Any]) -> None:
    try:
        jsonschema.validate(instance, schema)
    except jsonschema.ValidationError as exc:
        raise SchemaValidationError(str(exc)) from exc


def _load_evidence_schema() -> Dict[str, Any]:
    return load_schema("evidence_bundle.schema.json")


def _email_events_sample(params: Dict[str, Any]) -> Dict[str, Any]:
    tenant = params.get("tenant") or "sample-tenant"
    domain = params.get("recipient_domain") or "contoso.com"
    start = params.get("start") or datetime.utcnow().replace(microsecond=0).isoformat() + "Z"
    end = params.get("end") or (datetime.utcnow().replace(microsecond=0) + timedelta(minutes=20)).isoformat() + "Z"

    events = [
        {"ts": start, "type": "accepted", "id": "evt-accept-001", "message_id": "msg-001", "detail": f"Provider accepted message to ops@{domain}"},
        {"ts": start, "type": "bounce", "id": "evt-bounce-001", "message_id": "msg-002", "detail": f"550 5.1.1 recipient not found invoices@{domain}"},
        {"ts": end, "type": "delivered", "id": "evt-deliv-001", "message_id": "msg-003", "detail": f"Delivered to accounting@{domain}"},
        {"ts": end, "type": "unknown", "id": "evt-unknown-001", "message_id": None, "detail": "Provider returned nonstandard status"},
    ]
    summary = {"sent": 3, "bounced": 1, "deferred": 0, "delivered": 1}
    return {
        "source": "email_events",
        "time_window": {"start": start, "end": end},
        "tenant": tenant,
        "summary_counts": summary,
        "events": events,
    }


_EMAIL_PARAMS_SCHEMA: Dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "tenant": {"type": ["string", "null"]},
        "start": {"type": ["string", "null"], "format": "date-time"},
        "end": {"type": ["string", "null"], "format": "date-time"},
        "recipient_domain": {"type": ["string", "null"]},
    },
}


def _dns_email_auth_check_sample(params: Dict[str, Any]) -> Dict[str, Any]:
    domain = params.get("domain") or "example.com"
    start = datetime.utcnow().replace(microsecond=0).isoformat() + "Z"
    end = (datetime.utcnow().replace(microsecond=0) + timedelta(minutes=5)).isoformat() + "Z"
    metadata = {
        "spf_present": True,
        "dkim_present": True,
        "dmarc_present": True,
        "dmarc_policy": "reject",
        "notes": f"DMARC policy reject for {domain}",
    }
    events = [
        {"ts": start, "type": "dns_check", "id": "dns-spf-1", "message_id": None, "detail": f"SPF present for {domain}"},
        {"ts": start, "type": "dns_check", "id": "dns-dkim-1", "message_id": None, "detail": f"DKIM present for {domain}"},
        {"ts": start, "type": "dns_check", "id": "dns-dmarc-1", "message_id": None, "detail": f"DMARC policy reject for {domain}"},
    ]
    return {
        "source": "dns_checks",
        "time_window": {"start": start, "end": end},
        "tenant": None,
        "summary_counts": {"sent": 0, "bounced": 0, "deferred": 0, "delivered": 0},
        "metadata": metadata,
        "events": events,
    }


def _app_events_sample(params: Dict[str, Any]) -> Dict[str, Any]:
    tenant = params.get("tenant") or "sample-tenant"
    start = params.get("start") or datetime.utcnow().replace(microsecond=0).isoformat() + "Z"
    end = params.get("end") or (datetime.utcnow().replace(microsecond=0) + timedelta(minutes=10)).isoformat() + "Z"
    workflow_id = params.get("workflow_id") or "wf-123"
    events = [
        {"ts": start, "type": "workflow_triggered", "id": "app-001", "message_id": None, "detail": f"Workflow {workflow_id} triggered"},
        {"ts": end, "type": "workflow_disabled", "id": "app-002", "message_id": None, "detail": f"Workflow {workflow_id} disabled by config change"},
        {"ts": end, "type": "deployment_completed", "id": "app-003", "message_id": None, "detail": f"Deployment completed for {workflow_id}"},
    ]
    return {
        "source": "app_events",
        "time_window": {"start": start, "end": end},
        "tenant": tenant,
        "summary_counts": {"sent": 0, "bounced": 0, "deferred": 0, "delivered": 0},
        "events": events,
    }


def _integration_events_sample(params: Dict[str, Any]) -> Dict[str, Any]:
    tenant = params.get("tenant") or "sample-tenant"
    integration_name = params.get("integration_name") or "ats"
    start = params.get("start") or datetime.utcnow().replace(microsecond=0).isoformat() + "Z"
    end = params.get("end") or (datetime.utcnow().replace(microsecond=0) + timedelta(minutes=15)).isoformat() + "Z"
    events = [
        {"ts": start, "type": "auth_failed", "id": "int-001", "message_id": None, "detail": f"Auth failed for {integration_name} token expired"},
        {"ts": start, "type": "rate_limited", "id": "int-002", "message_id": None, "detail": f"{integration_name} returned 429"},
        {"ts": end, "type": "webhook_delivery_failed", "id": "int-003", "message_id": None, "detail": f"{integration_name} webhook failed"},
    ]
    return {
        "source": "integration_events",
        "time_window": {"start": start, "end": end},
        "tenant": tenant,
        "summary_counts": {"sent": 0, "bounced": 0, "deferred": 0, "delivered": 0},
        "events": events,
    }


def _email_provider_events_file(params: Dict[str, Any]) -> Dict[str, Any]:
    path = Path(params.get("file_path"))
    payload = json.loads(path.read_text(encoding="utf-8"))
    events = payload.get("events", [])
    summary = payload.get("summary_counts", {"sent": 0, "bounced": 0, "deferred": 0, "delivered": 0})
    return {
        "source": "email_events",
        "time_window": payload.get("time_window") or {"start": events[0]["ts"], "end": events[-1]["ts"]} if events else {"start": "", "end": ""},
        "tenant": payload.get("tenant"),
        "summary_counts": summary,
        "events": events,
    }


def _app_log_events_file(params: Dict[str, Any]) -> Dict[str, Any]:
    path = Path(params.get("file_path"))
    tenant = params.get("tenant") or "sample-tenant"
    events = []
    for line in path.read_text(encoding="utf-8").splitlines():
        parts = line.split(" ", 2)
        if len(parts) < 3:
            continue
        ts, evt_type, detail = parts
        events.append({"ts": ts, "type": evt_type, "id": f"log-{len(events)+1}", "message_id": None, "detail": detail})
    start = events[0]["ts"] if events else ""
    end = events[-1]["ts"] if events else ""
    return {
        "source": "app_events",
        "time_window": {"start": start, "end": end},
        "tenant": tenant,
        "summary_counts": {"sent": 0, "bounced": 0, "deferred": 0, "delivered": 0},
        "events": events,
    }


_DNS_PARAMS_SCHEMA: Dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {"domain": {"type": "string"}},
    "required": ["domain"],
}


def _email_provider_events_sample(params: Dict[str, Any]) -> Dict[str, Any]:
    tenant = params.get("tenant") or "sample-tenant"
    start = params.get("start") or datetime.utcnow().replace(microsecond=0).isoformat() + "Z"
    end = params.get("end") or (datetime.utcnow().replace(microsecond=0) + timedelta(minutes=15)).isoformat() + "Z"
    events = [
        {"ts": start, "type": "accepted", "id": "prov-001", "message_id": "msg-101", "detail": "Provider accepted message"},
        {"ts": start, "type": "delivered", "id": "prov-002", "message_id": "msg-101", "detail": "Delivered to recipient"},
        {"ts": end, "type": "quarantined", "id": "prov-003", "message_id": "msg-102", "detail": "Recipient domain quarantined message"},
    ]
    return {
        "source": "email_events",
        "time_window": {"start": start, "end": end},
        "tenant": tenant,
        "summary_counts": {"sent": 2, "bounced": 0, "deferred": 0, "delivered": 1},
        "events": events,
    }

_APP_EVENTS_PARAMS_SCHEMA: Dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "tenant": {"type": ["string", "null"]},
        "start": {"type": ["string", "null"], "format": "date-time"},
        "end": {"type": ["string", "null"], "format": "date-time"},
        "workflow_id": {"type": ["string", "null"]},
    },
}

_INTEGRATION_EVENTS_PARAMS_SCHEMA: Dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "tenant": {"type": ["string", "null"]},
        "start": {"type": ["string", "null"], "format": "date-time"},
        "end": {"type": ["string", "null"], "format": "date-time"},
        "integration_name": {"type": ["string", "null"]},
    },
}

_FILE_PARAMS_SCHEMA: Dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {"file_path": {"type": "string"}, "tenant": {"type": ["string", "null"]}},
    "required": ["file_path"],
}

_LOG_EVIDENCE_PARAMS_SCHEMA: Dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "service": {"type": ["string", "null"]},
        "query_type": {"type": "string", "enum": ["errors", "timeouts", "availability"]},
        "incident_window": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "start": {"type": ["string", "null"], "format": "date-time"},
                "end": {"type": ["string", "null"], "format": "date-time"},
            },
        },
        "time_window": {
            "type": "object",
            "additionalProperties": False,
            "required": ["start", "end"],
            "properties": {
                "start": {"type": "string", "format": "date-time"},
                "end": {"type": "string", "format": "date-time"},
            },
        },
        "tenant": {"type": ["string", "null"]},
        "reason": {"type": ["string", "null"]},
    },
    "required": ["time_window", "query_type"],
}

_SERVICE_STATUS_PARAMS_SCHEMA: Dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "service_id": {"type": "string"},
        "tenant_id": {"type": ["string", "null"]},
        "region": {"type": ["string", "null"]},
    },
    "required": ["service_id"],
}


def _build_registry() -> Dict[str, Tool]:
    evidence_schema = _load_evidence_schema()
    return {
        "fetch_email_events_sample": Tool(
            name="fetch_email_events_sample",
            params_schema=_EMAIL_PARAMS_SCHEMA,
            result_schema=evidence_schema,
            fn=_email_events_sample,
        ),
        "fetch_email_provider_events_sample": Tool(
            name="fetch_email_provider_events_sample",
            params_schema=_EMAIL_PARAMS_SCHEMA,
            result_schema=evidence_schema,
            fn=_email_provider_events_sample,
        ),
        "dns_email_auth_check_sample": Tool(
            name="dns_email_auth_check_sample",
            params_schema=_DNS_PARAMS_SCHEMA,
            result_schema=evidence_schema,
            fn=_dns_email_auth_check_sample,
        ),
        "fetch_app_events_sample": Tool(
            name="fetch_app_events_sample",
            params_schema=_APP_EVENTS_PARAMS_SCHEMA,
            result_schema=evidence_schema,
            fn=_app_events_sample,
        ),
        "fetch_integration_events_sample": Tool(
            name="fetch_integration_events_sample",
            params_schema=_INTEGRATION_EVENTS_PARAMS_SCHEMA,
            result_schema=evidence_schema,
            fn=_integration_events_sample,
        ),
        "fetch_email_provider_events_file": Tool(
            name="fetch_email_provider_events_file",
            params_schema=_FILE_PARAMS_SCHEMA,
            result_schema=evidence_schema,
            fn=_email_provider_events_file,
        ),
        "fetch_app_log_events_file": Tool(
            name="fetch_app_log_events_file",
            params_schema=_FILE_PARAMS_SCHEMA,
            result_schema=evidence_schema,
            fn=_app_log_events_file,
        ),
        "log_evidence": Tool(
            name="log_evidence",
            params_schema=_LOG_EVIDENCE_PARAMS_SCHEMA,
            result_schema=evidence_schema,
            fn=run_log_evidence,
        ),
        "service_status": Tool(
            name="service_status",
            params_schema=_SERVICE_STATUS_PARAMS_SCHEMA,
            result_schema=evidence_schema,
            fn=run_service_status,
        ),
    }


REGISTRY: Dict[str, Tool] = _build_registry()


def run_tool(name: str, params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    params = params or {}
    if name not in REGISTRY:
        raise ValueError(f"Tool not allowed: {name}")
    tool = REGISTRY[name]
    _validate(params, tool.params_schema)
    result = tool.fn(params)
    _validate(result, tool.result_schema)
    return result
