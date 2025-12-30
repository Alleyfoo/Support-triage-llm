triage_schema = {
    "type": "object",
    "additionalProperties": False,
    "required": ["case_type", "severity", "time_window", "scope", "symptoms", "examples", "missing_info_questions", "suggested_tools", "draft_customer_reply"],
    "properties": {
        "case_type": {"type": "string", "enum": ["email_delivery", "integration", "ui_bug", "data_import", "access_permissions", "unknown"]},
        "severity": {"type": "string", "enum": ["critical", "high", "medium", "low"]},
        "time_window": {
            "type": "object",
            "additionalProperties": False,
            "required": ["start", "end", "confidence"],
            "properties": {
                "start": {"type": ["string", "null"], "format": "date-time"},
                "end": {"type": ["string", "null"], "format": "date-time"},
                "confidence": {"type": "number", "minimum": 0.0},
            },
        },
        "scope": {
            "type": "object",
            "additionalProperties": False,
            "required": ["affected_tenants", "affected_users", "affected_recipients", "recipient_domains", "is_all_users", "notes"],
            "properties": {
                "affected_tenants": {"type": "array", "items": {"type": "string"}},
                "affected_users": {"type": "array", "items": {"type": "string"}},
                "affected_recipients": {"type": "array", "items": {"type": "string"}},
                "recipient_domains": {"type": "array", "items": {"type": "string"}},
                "is_all_users": {"type": "boolean"},
                "notes": {"type": "string"},
            },
        },
        "symptoms": {"type": "array", "items": {"type": "string"}},
        "examples": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["recipient", "timestamp", "description"],
                "properties": {
                    "recipient": {"type": ["string", "null"]},
                    "timestamp": {"type": ["string", "null"], "format": "date-time"},
                    "description": {"type": "string"},
                },
            },
        },
        "missing_info_questions": {"type": "array", "items": {"type": "string"}},
        "suggested_tools": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["tool_name", "reason", "params"],
                "properties": {
                    "tool_name": {"type": "string"},
                    "reason": {"type": "string"},
                    "params": {"type": "object"},
                },
            },
        },
        "draft_customer_reply": {
            "type": "object",
            "additionalProperties": False,
            "required": ["subject", "body"],
            "properties": {"subject": {"type": "string"}, "body": {"type": "string"}},
        },
    },
}

evidence_bundle_schema = {
    "type": "object",
    "additionalProperties": False,
    "required": ["source", "time_window", "tenant", "summary_counts", "events"],
    "properties": {
        "source": {"type": "string", "enum": ["email_events", "app_events", "integration_events", "dns_checks"]},
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
        "summary_counts": {
            "type": "object",
            "additionalProperties": False,
            "required": ["sent", "bounced", "deferred", "delivered"],
            "properties": {
                "sent": {"type": "integer", "minimum": 0},
                "bounced": {"type": "integer", "minimum": 0},
                "deferred": {"type": "integer", "minimum": 0},
                "delivered": {"type": "integer", "minimum": 0},
            },
        },
        "events": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["ts", "type", "id", "message_id", "detail"],
                "properties": {
                    "ts": {"type": "string", "format": "date-time"},
                    "type": {"type": "string"},
                    "id": {"type": "string"},
                    "message_id": {"type": ["string", "null"]},
                    "detail": {"type": "string"},
                },
            },
        },
    },
}

final_report_schema = {
    "type": "object",
    "additionalProperties": False,
    "required": ["classification", "timeline_summary", "customer_update", "engineering_escalation", "kb_suggestions"],
    "properties": {
        "classification": {
            "type": "object",
            "additionalProperties": False,
            "required": ["failure_stage", "confidence", "top_reasons"],
            "properties": {
                "failure_stage": {"type": "string", "enum": ["trigger", "queue", "provider", "recipient", "configuration", "unknown"]},
                "confidence": {"type": "number", "minimum": 0.0},
                "top_reasons": {"type": "array", "items": {"type": "string"}},
            },
        },
        "timeline_summary": {"type": "string"},
        "customer_update": {
            "type": "object",
            "additionalProperties": False,
            "required": ["subject", "body", "requested_info"],
            "properties": {
                "subject": {"type": "string"},
                "body": {"type": "string"},
                "requested_info": {"type": "array", "items": {"type": "string"}},
            },
        },
        "engineering_escalation": {
            "type": "object",
            "additionalProperties": False,
            "required": ["title", "body", "evidence_refs", "severity", "repro_steps"],
            "properties": {
                "title": {"type": "string"},
                "body": {"type": "string"},
                "evidence_refs": {"type": "array", "items": {"type": "string"}},
                "severity": {"type": "string", "enum": ["S1", "S2", "S3"]},
                "repro_steps": {"type": "array", "items": {"type": "string"}},
            },
        },
        "kb_suggestions": {"type": "array", "items": {"type": "string"}},
    },
}
