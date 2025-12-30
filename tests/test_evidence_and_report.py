from app import report_service
from tools import registry
from app.validation import validate_payload


def _sample_bundle_with_bounce():
    return registry.run_tool("fetch_email_events_sample", {"tenant": "acme", "recipient_domain": "contoso.com"})


def test_report_includes_evidence_refs_and_schema_valid():
    bundle = _sample_bundle_with_bounce()
    triage = {
        "case_type": "email_delivery",
        "severity": "high",
        "time_window": {"start": None, "end": None, "confidence": 0.1},
        "scope": {
            "affected_tenants": ["acme"],
            "affected_users": [],
            "affected_recipients": [],
            "recipient_domains": ["contoso.com"],
            "is_all_users": False,
            "notes": "",
        },
        "symptoms": ["bounces"],
        "examples": [],
        "missing_info_questions": [],
        "suggested_tools": [],
        "draft_customer_reply": {"subject": "subj", "body": "body"},
    }
    report = report_service.generate_report(triage, [bundle])
    payload = dict(report)
    payload.pop("_meta", None)
    validate_payload(payload, "final_report.schema.json")
    refs = payload["engineering_escalation"]["evidence_refs"]
    assert any(ref.startswith("evt-") for ref in refs)
    assert "bounce" in payload["timeline_summary"].lower()


def test_receipt_discipline_no_bounce_claim_without_evidence():
    bundle = _sample_bundle_with_bounce()
    bundle["summary_counts"]["bounced"] = 0
    bundle["events"] = []
    report = report_service.generate_report({}, [bundle])
    assert "bounce" not in report["timeline_summary"].lower()


def test_no_events_message_when_empty():
    bundle = _sample_bundle_with_bounce()
    bundle["events"] = []
    bundle["summary_counts"] = {"sent": 0, "bounced": 0, "deferred": 0, "delivered": 0}
    report = report_service.generate_report({}, [bundle])
    assert "no events" in report["timeline_summary"].lower()
