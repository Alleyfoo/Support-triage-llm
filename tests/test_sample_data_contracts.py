import json
from datetime import datetime
from pathlib import Path

import jsonschema

from tests.schema_definitions import evidence_bundle_schema, final_report_schema, triage_schema


SAMPLES_DIR = Path(__file__).parent / "data_samples"


def _parse_iso(ts: str) -> datetime:
    # Accept trailing Z by normalizing to UTC offset.
    return datetime.fromisoformat(ts.replace("Z", "+00:00"))


def test_sample_email_events_match_schema_and_window():
    path = SAMPLES_DIR / "email_events.jsonl"
    with path.open() as f:
        for line in f:
            payload = json.loads(line)
            jsonschema.validate(payload, evidence_bundle_schema)
            start = _parse_iso(payload["time_window"]["start"])
            end = _parse_iso(payload["time_window"]["end"])
            for event in payload["events"]:
                ts = _parse_iso(event["ts"])
                assert start <= ts <= end, f"{event['id']} outside declared window"


def test_sample_app_events_match_schema_and_window():
    path = SAMPLES_DIR / "app_events.jsonl"
    with path.open() as f:
        for line in f:
            payload = json.loads(line)
            jsonschema.validate(payload, evidence_bundle_schema)
            start = _parse_iso(payload["time_window"]["start"])
            end = _parse_iso(payload["time_window"]["end"])
            for event in payload["events"]:
                ts = _parse_iso(event["ts"])
                assert start <= ts <= end, f"{event['id']} outside declared window"


def test_fake_emails_are_well_formed():
    path = SAMPLES_DIR / "fake_emails.jsonl"
    with path.open() as f:
        for line in f:
            payload = json.loads(line)
            assert {"id", "tenant", "subject", "body", "received_at"} <= set(payload.keys())
            _parse_iso(payload["received_at"])
            assert payload["body"], "email body should not be empty"


def test_triage_and_final_report_contracts_examples():
    triage_example = {
        "case_type": "email_delivery",
        "severity": "high",
        "time_window": {"start": "2025-12-29T08:50:00Z", "end": "2025-12-29T09:30:00Z", "confidence": 0.7},
        "scope": {
            "affected_tenants": ["acme"],
            "affected_users": [],
            "affected_recipients": ["ops@contoso.com", "invoices@contoso.com"],
            "recipient_domains": ["contoso.com"],
            "is_all_users": False,
            "notes": "",
        },
        "symptoms": ["bounces to contoso.com"],
        "examples": [
            {"recipient": "ops@contoso.com", "timestamp": "2025-12-29T08:55:12Z", "description": "550 5.1.1"},
            {"recipient": "invoices@contoso.com", "timestamp": "2025-12-29T08:56:44Z", "description": "550 5.1.1"},
        ],
        "missing_info_questions": ["Are other domains impacted?", "Any recent DNS or provider changes?"],
        "suggested_tools": [
            {"tool_name": "fetch_email_events", "reason": "Confirm bounce patterns", "params": {"recipient_domain": "contoso.com"}},
            {"tool_name": "dns_email_auth_check", "reason": "Check SPF/DKIM/DMARC presence", "params": {"domain": "contoso.com"}},
        ],
        "draft_customer_reply": {
            "subject": "Quick check on bounces to contoso.com",
            "body": "We see bounces to contoso.com around 08:50-09:10 UTC. Can you confirm if other domains are affected and whether DNS/email settings changed recently?",
        },
    }

    final_report_example = {
        "classification": {"failure_stage": "recipient", "confidence": 0.64, "top_reasons": ["Recipient address rejected"]},
        "timeline_summary": "Two bounces to contoso.com between 08:55-08:57 UTC; later delivery to accounting@contoso.com succeeded.",
        "customer_update": {
            "subject": "Update on bounces to contoso.com",
            "body": "We observed 550 5.1.1 bounces to ops@contoso.com and invoices@contoso.com. Later deliveries to accounting@contoso.com succeeded. Please confirm if other recipients are impacted.",
            "requested_info": ["List of affected recipients", "Any provider/DNS changes"],
        },
        "engineering_escalation": {
            "title": "Bounces to contoso.com for tenant acme",
            "body": "Bounce errors 550 5.1.1 for ops@contoso.com and invoices@contoso.com between 08:55-08:57 UTC. Delivery to accounting@contoso.com succeeded at 09:05 UTC.",
            "evidence_refs": ["evt-201", "evt-202", "evt-203"],
            "severity": "S2",
            "repro_steps": ["Send to ops@contoso.com", "Observe 550 5.1.1 response"],
        },
        "kb_suggestions": ["Email delivery troubleshooting", "Recipient validation checklist"],
    }

    jsonschema.validate(triage_example, triage_schema)
    jsonschema.validate(final_report_example, final_report_schema)
