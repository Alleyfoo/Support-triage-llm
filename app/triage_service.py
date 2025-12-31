"""Triage service: redaction -> triage (heuristic or LLM) -> schema validation."""

from __future__ import annotations

import json
import re
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from . import config
from .example_retriever import ExampleRetriever
from .vector_store import get_store as get_vector_store
from tools.registry import REGISTRY
from .redaction import redact
from .validation import SchemaValidationError, validate_payload, validate_with_retry
from .time_window import DATE_PATTERN, MONTH_DAY_PATTERN, ISO_PATTERN, parse_time_window

PROMPT_VERSION_HEURISTIC = "triage-heuristic-v1"
PROMPT_VERSION_LLM = "triage-llm-v1"

DOMAIN_PATTERN = re.compile(r"\b([a-zA-Z0-9.-]+\.[a-zA-Z]{2,})\b")
EMAIL_PATTERN = re.compile(r"\b[a-zA-Z0-9._%+-]+@([a-zA-Z0-9.-]+\.[a-zA-Z]{2,})\b")
SCHEMA_TEXT = (Path(__file__).resolve().parents[1] / "schemas" / "triage.schema.json").read_text(encoding="utf-8")
ALLOWED_TOP_KEYS = {
    "case_type",
    "severity",
    "time_window",
    "reported_time_window",
    "time_ambiguity",
    "scope",
    "symptoms",
    "examples",
    "missing_info_questions",
    "suggested_tools",
    "draft_customer_reply",
}
TIME_RANGE_RE = re.compile(r"\b(\d{1,2}:\d{2})\s*(?:-|to|–)\s*(\d{1,2}:\d{2})\b", re.IGNORECASE)
CLOCK_RE = re.compile(r"\b\d{1,2}:\d{2}\b")
ALLOWED_TOP_KEYS = {
    "case_type",
    "severity",
    "time_window",
    "scope",
    "symptoms",
    "examples",
    "missing_info_questions",
    "suggested_tools",
    "draft_customer_reply",
}

_EXAMPLE_RETRIEVER = ExampleRetriever(Path(config.GOLDEN_DATASET_PATH), max_examples=config.FEW_SHOT_EXAMPLES)

def _infer_case_type(text: str) -> str:
    lower = text.lower()
    if any(token in lower for token in ["outage", "downtime", "service unavailable", "site down", "system down"]):
        return "incident"
    if "webhook" in lower or "integration" in lower or "api" in lower or "sync" in lower or "connector" in lower or "http://" in lower or "https://" in lower:
        return "integration"
    if "bounce" in lower or "deliver" in lower or "email" in lower:
        return "email_delivery"
    if EMAIL_PATTERN.search(text):
        return "email_delivery"
    if any(k in lower for k in ["mfa", "2fa", "otp", "authenticator", "login", "sign-in", "sign in"]):
        return "auth_access"
    if "ui" in lower or "button" in lower or "page" in lower:
        return "ui_bug"
    if "import" in lower:
        return "data_import"
    if "permission" in lower or "access" in lower:
        return "access_permissions"
    return "unknown"


def _infer_severity(text: str) -> str:
    lower = text.lower()
    if "critical" in lower or "urgent" in lower:
        return "critical"
    if any(token in lower for token in ["outage", "down"]):
        return "high"
    if "bounce" in lower or "bounced" in lower or "bouncing" in lower:
        return "high"
    if any(token in lower for token in ["failed", "failing", "error", "500"]):
        return "medium"
    if any(token in lower for token in ["degraded", "intermittent", "slow"]):
        return "medium"
    return "low"


def _parse_anchor(metadata: Dict[str, Any]) -> datetime:
    ts = metadata.get("received_at") or metadata.get("created_at")
    if isinstance(ts, str):
        try:
            return datetime.fromisoformat(ts.replace("Z", "+00:00")).astimezone(timezone.utc)
        except Exception:
            pass
    return datetime.now(timezone.utc)


def _has_explicit_date(text: str) -> bool:
    return bool(ISO_PATTERN.search(text) or DATE_PATTERN.search(text) or MONTH_DAY_PATTERN.search(text))


def _sanitize_time_window(time_window: Dict[str, Any], anchor: datetime, explicit: bool, source: str) -> Tuple[Dict[str, Any], bool]:
    overridden = False
    start = time_window.get("start")
    end = time_window.get("end")
    if start and not explicit:
        try:
            start_dt = datetime.fromisoformat(start.replace("Z", "+00:00")).astimezone(timezone.utc)
            delta_days = abs((start_dt - anchor).days)
            if start_dt.year != anchor.year or delta_days > 30:
                time_window["start"] = None
                time_window["end"] = None
                time_window["confidence"] = min(float(time_window.get("confidence") or 0.1), 0.2)
                overridden = True
        except Exception:
            pass
    return time_window, overridden


def _extract_time_fields(text: str, anchor: datetime) -> Dict[str, Any]:
    lower = text.lower()
    parsed = parse_time_window(text, now=anchor)
    explicit = _has_explicit_date(text)
    time_window = {
        "start": parsed.get("start"),
        "end": parsed.get("end"),
        "confidence": parsed.get("confidence", 0.0),
    }
    reported = {
        "raw_text": None,
        "timezone": None,
        "has_date": False,
        "has_only_clock_time": False,
        "confidence": time_window["confidence"],
    }
    time_ambiguity = "missing_date"

    iso = ISO_PATTERN.search(text)
    if iso:
        raw = iso.group(0)
        reported.update(
            {"raw_text": raw, "timezone": None, "has_date": True, "has_only_clock_time": False, "confidence": max(time_window["confidence"], 0.8)}
        )
        time_ambiguity = "none"
        return {
            "time_window": time_window,
            "reported_time_window": reported,
            "time_ambiguity": time_ambiguity,
            "explicit_date": True,
            "time_window_reason": parsed.get("reason", "parsed_from_text"),
        }

    range_match = TIME_RANGE_RE.search(text)
    if range_match:
        raw = range_match.group(0)
        tz = "UTC" if "utc" in lower else None
        reported.update(
            {"raw_text": raw, "timezone": tz, "has_date": False, "has_only_clock_time": True, "confidence": max(time_window["confidence"], 0.5)}
        )
        time_ambiguity = "missing_date"
        return {
            "time_window": time_window,
            "reported_time_window": reported,
            "time_ambiguity": time_ambiguity,
            "explicit_date": explicit,
            "time_window_reason": parsed.get("reason", "parsed_from_text"),
        }

    if CLOCK_RE.search(text):
        raw = CLOCK_RE.search(text).group(0)
        tz = "UTC" if "utc" in lower else None
        reported.update(
            {"raw_text": raw, "timezone": tz, "has_date": False, "has_only_clock_time": True, "confidence": max(time_window["confidence"], 0.4)}
        )
        time_ambiguity = "missing_date" if tz else "missing_timezone"
        return {
            "time_window": time_window,
            "reported_time_window": reported,
            "time_ambiguity": time_ambiguity,
            "time_window_reason": parsed.get("reason", "parsed_from_text"),
        }

    if any(token in lower for token in ["yesterday", "last night", "since yesterday", "earlier today"]):
        reported.update({"raw_text": None, "timezone": None, "has_date": False, "has_only_clock_time": False, "confidence": max(time_window["confidence"], 0.2)})
        time_ambiguity = "relative_ambiguous"
        return {"time_window": time_window, "reported_time_window": reported, "time_ambiguity": time_ambiguity}

    # No time clues
    return {
        "time_window": time_window,
        "reported_time_window": reported,
        "time_ambiguity": time_ambiguity,
        "explicit_date": explicit,
        "time_window_reason": parsed.get("reason", "parsed_from_text"),
    }


def _detect_domains(text: str) -> List[str]:
    domains = set(match.group(1) for match in EMAIL_PATTERN.finditer(text))
    domains.update(DOMAIN_PATTERN.findall(text))
    return sorted(domains)


def _build_missing_questions(domains: List[str], reported_time_window: Dict[str, Any], time_ambiguity: str) -> List[str]:
    questions: List[str] = []
    if reported_time_window.get("raw_text"):
        questions.append(
            f"Can you confirm the date (YYYY-MM-DD) for {reported_time_window['raw_text']}?"
        )
        if time_ambiguity == "missing_timezone":
            questions.append("What timezone is that time in?")
    else:
        questions.append("What time window is impacted (start/end in UTC)?")

    questions.append("How many users or recipients are affected?")

    if domains:
        questions.append(f"Are all recipients at {', '.join(domains)} affected?")
        questions.append("Can you share example message IDs and timestamps?")
    else:
        questions.append("Which recipient domains are impacted?")

    questions.append("Have there been any recent config or provider changes?")
    return questions[:6]


def _suggest_tools(domains: List[str]) -> List[Dict[str, Any]]:
    tools: List[Dict[str, Any]] = []
    if domains:
        tools.append(
            {"tool_name": "fetch_email_events", "reason": "Confirm bounce or delivery patterns", "params": {"recipient_domain": domains[0]}}
        )
        tools.append(
            {"tool_name": "dns_email_auth_check", "reason": "Check SPF/DKIM/DMARC presence", "params": {"domain": domains[0]}}
        )
    else:
        tools.append({"tool_name": "fetch_email_events", "reason": "Confirm bounce or delivery patterns", "params": {}})
    return tools


def _build_draft_reply(domains: List[str], severity: str) -> Dict[str, str]:
    domain_note = f" to {domains[0]}" if domains else ""
    subject = f"Quick update on your report{domain_note}"
    body_lines = [
        "Thanks for letting us know. We are reviewing the issue now.",
        "To help us investigate, could you share:",
        "- Impacted time window (UTC)",
        "- Affected users/recipients and examples",
        "- Any recent configuration or provider changes",
    ]
    if domains:
        body_lines.append(f"- Are other domains besides {domains[0]} affected?")
    return {"subject": subject, "body": "\n".join(body_lines)}


def _enrich_from_heuristic(text: str, payload: Dict[str, Any], metadata: Dict[str, Any]) -> Dict[str, Any]:
    """
    Use heuristic defaults to backfill LLM outputs when they are sparse or missing critical fields.
    """
    base = _base_triage_payload(text, metadata)
    # Backfill questions
    if not payload.get("missing_info_questions"):
        payload["missing_info_questions"] = base["missing_info_questions"]
    # Backfill draft
    dcr = payload.get("draft_customer_reply") or {}
    if not dcr.get("subject") or not dcr.get("body"):
        payload["draft_customer_reply"] = base["draft_customer_reply"]
    # Backfill scope/domains
    scope = payload.get("scope") or {}
    if not scope.get("recipient_domains"):
        scope["recipient_domains"] = base["scope"]["recipient_domains"]
    if "notes" not in scope:
        scope["notes"] = ""
    payload["scope"] = scope
    # Backfill symptoms/examples
    if not payload.get("symptoms"):
        payload["symptoms"] = base["symptoms"]
    if not payload.get("examples"):
        payload["examples"] = []
    # Backfill case type if unknown
    if payload.get("case_type") == "unknown" and base.get("case_type") != "unknown":
        payload["case_type"] = base["case_type"]
    # Backfill time window if missing
    anchor = _parse_anchor(metadata)
    if not payload.get("time_window") or (
        payload["time_window"].get("start") is None and payload["time_window"].get("end") is None
    ):
        parsed = parse_time_window(text, now=anchor)
        payload["time_window"] = parsed
        payload["time_window_reason"] = parsed.get("reason", "parsed_from_text")
    if not payload.get("reported_time_window"):
        payload["reported_time_window"] = base.get("reported_time_window", {})
    if not payload.get("time_ambiguity"):
        payload["time_ambiguity"] = base.get("time_ambiguity", "missing_date")
    return payload


def _apply_confidence_routing(raw_text: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    """Adjust severity and questions when time confidence or scope is weak."""
    scope = payload.get("scope") or {}
    time_window = payload.get("time_window") or {}
    time_conf = float(time_window.get("confidence") or 0.0)
    missing_scope = not scope.get("affected_tenants") and not scope.get("recipient_domains")
    requires_more_info = time_conf < 0.3 or missing_scope

    if requires_more_info:
        severity = (payload.get("severity") or "medium").lower()
        if severity in {"critical", "high"}:
            payload["severity"] = "medium"
        questions = payload.get("missing_info_questions") or []
        if not questions:
            domains = scope.get("recipient_domains") or _detect_domains(raw_text)
            reported = payload.get("reported_time_window") or {"raw_text": None, "timezone": None, "has_date": False, "has_only_clock_time": False, "confidence": 0.1}
            payload["missing_info_questions"] = _build_missing_questions(domains, reported, payload.get("time_ambiguity") or "missing_date")
    return payload


def _base_triage_payload(text: str, metadata: Dict[str, Any]) -> Dict[str, Any]:
    domains = _detect_domains(text)
    case_type = _infer_case_type(text)
    severity = _infer_severity(text)
    tenant_hint = metadata.get("tenant") or metadata.get("customer") or ""
    anchor = _parse_anchor(metadata)
    time_fields = _extract_time_fields(text, anchor)
    time_window = time_fields["time_window"]
    reported_time_window = time_fields["reported_time_window"]
    time_ambiguity = time_fields["time_ambiguity"]

    return {
        "case_type": case_type,
        "severity": severity,
        "time_window": time_window,
        "reported_time_window": reported_time_window,
        "time_ambiguity": time_ambiguity,
        "scope": {
            "affected_tenants": [tenant_hint] if tenant_hint else [],
            "affected_users": [],
            "affected_recipients": [],
            "recipient_domains": domains,
            "is_all_users": False,
            "notes": "",
        },
        "symptoms": [text[:200]],
        "examples": [],
        "missing_info_questions": _build_missing_questions(domains, reported_time_window, time_ambiguity),
        "suggested_tools": _suggest_tools(domains),
        "draft_customer_reply": _build_draft_reply(domains, severity),
    }


def _call_ollama(prompt: str) -> str:
    if not config.OLLAMA_MODEL:
        raise RuntimeError("OLLAMA_MODEL/MODEL_NAME not set")
    payload = {
        "model": config.OLLAMA_MODEL,
        "messages": [
            {"role": "system", "content": "You are a precise support triage assistant. Return only JSON matching the schema."},
            {"role": "user", "content": prompt},
        ],
        "stream": False,
        "options": {
            "temperature": float(config.TEMP),
            "num_predict": int(config.MAX_TOKENS),
        },
    }
    data = json.dumps(payload).encode("utf-8")
    url = config.OLLAMA_HOST.rstrip("/") + "/api/chat"
    request = Request(url, data=data, headers={"Content-Type": "application/json"})
    try:
        with urlopen(request, timeout=config.OLLAMA_TIMEOUT) as response:  # nosec - local inference endpoint
            body = response.read()
    except HTTPError as exc:
        if exc.code == 404:
            raise RuntimeError(
                f"Ollama 404 for model '{config.OLLAMA_MODEL}' at {config.OLLAMA_HOST}. "
                "Ensure the model name is correct and pulled (e.g., `ollama pull llama3.1:8b`) "
                "or set OLLAMA_URL/OLLAMA_MODEL appropriately."
            ) from exc
        raise
    parsed = json.loads(body)
    message = parsed.get("message", {})
    content = message.get("content")
    if not isinstance(content, str):
        raise RuntimeError("LLM returned empty content")
    return content


def _extract_json_block(text: str) -> Dict[str, Any]:
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end != -1 and end > start:
        snippet = text[start : end + 1]
        return json.loads(snippet)
    raise json.JSONDecodeError("Could not parse JSON", text, 0)


def _triage_heuristic(text: str, metadata: Dict[str, Any]) -> Dict[str, Any]:
    triage_payload = _base_triage_payload(text, metadata)

    def _fix(payload: Dict[str, Any]) -> Dict[str, Any]:
        payload.setdefault("examples", [])
        payload.setdefault("suggested_tools", [])
        payload.setdefault("missing_info_questions", [])
        payload.setdefault("symptoms", [])
        payload.setdefault(
            "reported_time_window",
            {"raw_text": None, "timezone": None, "has_date": False, "has_only_clock_time": False, "confidence": 0.1},
        )
        payload.setdefault("time_ambiguity", "missing_date")
        return payload

    triage_payload = validate_with_retry(triage_payload, "triage.schema.json", fixer=_fix)
    triage_payload["_meta"] = {
        "llm_model": "heuristic",
        "prompt_version": PROMPT_VERSION_HEURISTIC,
        "triage_mode": "heuristic",
        "llm_latency_ms": 0,
        "llm_attempts": 0,
        "schema_valid": True,
    }
    return triage_payload


def _format_examples(examples: List[Dict[str, Any]]) -> str:
    if not examples:
        return ""
    blocks = []
    for idx, ex in enumerate(examples, 1):
        inp = ex.get("input_symptoms") or ex.get("input_redacted") or ""
        triage = ex.get("perfect_triage") or ex.get("triage") or {}
        blocks.append(
            "Example {idx}\nInput: {user}\nOutput: {triage}".format(
                idx=idx,
                user=inp,
                triage=json.dumps(triage, ensure_ascii=False),
            )
        )
    return "You are a support triage assistant. Use the following examples for reference:\n" + "\n\n".join(blocks) + "\n\n"


def _format_tools() -> str:
    entries = []
    for name, tool in REGISTRY.items():
        doc = (tool.__doc__ or "").strip().splitlines()[0] if hasattr(tool, "__doc__") else ""
        entries.append(f"- {name}: {doc}")
    if not entries:
        return ""
    return (
        "You have access to the following tools to gather evidence. "
        "Suggest the most relevant ones in the 'suggested_tools' JSON field:\n"
        + "\n".join(entries)
        + "\n\n"
    )


def _triage_llm(text: str, metadata: Dict[str, Any]) -> Dict[str, Any]:
    store = get_vector_store()
    fewshot = store.retrieve(text, k=config.FEW_SHOT_EXAMPLES)
    prompt_prefix = _format_examples(fewshot) + _format_tools()

    prompt_base = (
        prompt_prefix
        + "Customer message:\n"
        f"{text}\n\n"
        "Return ONLY a JSON object matching this schema (no $schema/title keys, no prose, no schema echoes):\n"
        f"{SCHEMA_TEXT}\n"
    )
    last_error: str = ""
    attempts = 0
    start = time.perf_counter()
    for attempt in range(2):
        attempts = attempt + 1
        prompt = prompt_base
        if last_error:
            prompt += f"\nPrevious attempt failed schema validation: {last_error}\nReturn ONLY valid JSON."
        try:
            raw = _call_ollama(prompt)
            parsed = _extract_json_block(raw)
            for key in list(parsed.keys()):
                if key not in ALLOWED_TOP_KEYS:
                    parsed.pop(key, None)

            def _fix(payload: Dict[str, Any]) -> Dict[str, Any]:
                payload.setdefault("examples", [])
                payload.setdefault("suggested_tools", [])
                payload.setdefault("missing_info_questions", [])
                payload.setdefault("symptoms", [])
                payload["case_type"] = payload.get("case_type") or _infer_case_type(text)
                payload["severity"] = payload.get("severity") or _infer_severity(text)
                payload.setdefault("time_window", {"start": None, "end": None, "confidence": 0.1})
                payload.setdefault(
                    "reported_time_window",
                    {"raw_text": None, "timezone": None, "has_date": False, "has_only_clock_time": False, "confidence": 0.1},
                )
                payload.setdefault("time_ambiguity", "missing_date")
                payload.setdefault("scope", {}).setdefault("notes", "")
                dcr = payload.setdefault("draft_customer_reply", {})
                dcr["subject"] = dcr.get("subject") or ""
                dcr["body"] = dcr.get("body") or ""
                return payload

            parsed = validate_with_retry(parsed, "triage.schema.json", fixer=_fix)
            latency_ms = int((time.perf_counter() - start) * 1000)
            parsed = _enrich_from_heuristic(text, parsed, metadata)
            parsed["_meta"] = {
                "llm_model": config.OLLAMA_MODEL or "ollama",
                "prompt_version": PROMPT_VERSION_LLM,
                "triage_mode": "llm",
                "llm_latency_ms": latency_ms,
                "llm_attempts": attempts,
                "schema_valid": True,
            }
            return parsed
        except (SchemaValidationError, json.JSONDecodeError) as exc:
            last_error = str(exc)
            continue
        except (HTTPError, URLError, TimeoutError, OSError) as exc:
            raise RuntimeError(f"LLM call failed: {exc}") from exc
    raise SchemaValidationError(last_error or "LLM could not produce schema-valid JSON")


def triage(raw_text: str, metadata: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Redact, extract triage fields, validate, and return triage JSON."""
    metadata = metadata or {}
    anchor = _parse_anchor(metadata)
    metadata["received_at"] = anchor.isoformat().replace("+00:00", "Z")
    redaction = redact(raw_text)
    text = redaction["redacted_text"]

    mode = config.TRIAGE_MODE
    if mode == "llm":
        triage_payload = _triage_llm(text, metadata)
        source = "llm"
    else:
        triage_payload = _triage_heuristic(text, metadata)
        source = "heuristic"

    meta = triage_payload.get("_meta", {})
    meta["redaction_applied"] = redaction["redaction_applied"]
    meta["redacted_text"] = redaction["redacted_text"]
    meta["case_id"] = metadata.get("case_id")
    triage_payload["_meta"] = meta
    triage_payload = _apply_confidence_routing(text, triage_payload)

    explicit = _has_explicit_date(text)
    tw, overridden = _sanitize_time_window(triage_payload.get("time_window") or {"start": None, "end": None, "confidence": 0.1}, anchor, explicit, source)
    triage_payload["time_window"] = tw
    triage_payload["_meta"]["time_window_source"] = source
    triage_payload["_meta"]["time_window_anchor"] = anchor.isoformat().replace("+00:00", "Z")
    triage_payload["_meta"]["time_window_sanity_overridden"] = overridden
    reason_hint = triage_payload.get("time_window_reason") or "parsed_none"
    if overridden:
        reason_hint = "sanity_override"
    elif tw.get("start") or tw.get("end"):
        reason_hint = reason_hint or "parsed_from_text"
    else:
        reason_hint = "parsed_none"
    triage_payload["_meta"]["time_window_reason"] = reason_hint

    # Ensure redacted snippets persist in symptoms/draft when PII was removed.
    if meta.get("redaction_applied") and redaction.get("redacted_text"):
        redacted_snippet = redaction["redacted_text"][:200]
        symptoms = triage_payload.get("symptoms") or []
        if isinstance(symptoms, list) and not any(isinstance(s, str) and "[REDACTED" in s for s in symptoms):
            if symptoms:
                symptoms[0] = redacted_snippet
            else:
                symptoms = [redacted_snippet]
            triage_payload["symptoms"] = symptoms

        draft = triage_payload.get("draft_customer_reply") or {}
        if "[REDACTED" in redacted_snippet and not (draft.get("body") and "[REDACTED" in draft.get("body", "")):
            body = (draft.get("body") or "").strip()
            suffix = f"\n\nRedacted excerpt: {redacted_snippet}"
            draft["body"] = (body + suffix).strip()
            triage_payload["draft_customer_reply"] = draft

    return triage_payload
