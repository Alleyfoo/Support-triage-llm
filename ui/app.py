import os
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict
from difflib import SequenceMatcher

import pandas as pd
import streamlit as st

from app import queue_db
from app.sanitizer import sanitize_public_text

EXPORT_DIR = Path("data/exports")

st.set_page_config(page_title="Support Triage Copilot", layout="wide")
st.title("Support Triage Copilot - Review Console")
st.caption("Queue -> worker -> triage JSON + draft. Approve or send back for rewrite. No auto-send.")


def _auth_gate() -> bool:
    user = os.environ.get("STREAMLIT_AUTH_USER")
    pwd = os.environ.get("STREAMLIT_AUTH_PASS")
    if not user or not pwd:
        return True
    with st.sidebar:
        st.subheader("Auth required")
        u = st.text_input("User")
        p = st.text_input("Password", type="password")
        if st.button("Sign in"):
            if u == user and p == pwd:
                st.session_state["auth_ok"] = True
            else:
                st.error("Invalid credentials")
    return st.session_state.get("auth_ok", False)


def _load_cases(limit: int = 100) -> pd.DataFrame:
    rows = queue_db.fetch_queue(limit=limit)
    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame(rows)
    for col in ("triage_json", "missing_info_questions"):
        if col in df.columns:
            df[col] = df[col].apply(_json_load)
    return df


def _json_load(value: Any) -> Any:
    if isinstance(value, str) and value.strip():
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return value
    return value


def _pretty_json(payload: Dict[str, Any]) -> str:
    try:
        return json.dumps(payload, indent=2, ensure_ascii=False)
    except Exception:
        return str(payload)


def _export_case(row: Dict[str, Any], triage_json: Dict[str, Any], subject: str, body: str, action: str) -> Path:
    EXPORT_DIR.mkdir(parents=True, exist_ok=True)
    payload = {
        "row_id": row.get("id"),
        "conversation_id": row.get("conversation_id"),
        "tenant": row.get("end_user_handle"),
        "triage_json": triage_json,
        "draft": {"subject": subject, "body": body},
        "action": action,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    filename = f"{row.get('id')}_{action}_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}.json"
    path = EXPORT_DIR / filename
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def _edit_ratio(a: str | None, b: str | None) -> float:
    a = a or ""
    b = b or ""
    if not a and not b:
        return 0.0
    sim = SequenceMatcher(None, a, b).ratio()
    return round(1.0 - sim, 4)


if not _auth_gate():
    st.stop()

cases_df = _load_cases()

with st.sidebar:
    st.subheader("Queue snapshot")
    if cases_df.empty:
        st.write("Queue is empty. Use the API or CLI to enqueue cases.")
    else:
        counts = cases_df["status"].value_counts() if "status" in cases_df else {}
        if counts is not None:
            for status, count in counts.items():
                st.write(f"{status}: {count}")
        statuses = sorted(counts.index.tolist()) if counts is not None else []
        status_filter = st.multiselect("Filter by status", statuses, default=statuses)
        tenant_filter = st.text_input("Filter by tenant/end user")
        email_only = st.checkbox("Email delivery cases only", value=False)
        st.caption("Run worker: `python tools/triage_worker.py --watch`")
        st.caption("Health: `python tools/status.py`")

st.subheader("Cases")
if cases_df.empty:
    st.info("No cases available yet.")
    raise SystemExit

if "status" in cases_df:
    cases_df = cases_df[cases_df["status"].isin(status_filter)]
if "end_user_handle" in cases_df and tenant_filter:
    cases_df = cases_df[cases_df["end_user_handle"].astype(str).str.contains(tenant_filter, case=False, na=False)]
if email_only and "triage_json" in cases_df:
    cases_df = cases_df[cases_df["triage_json"].apply(lambda t: isinstance(t, dict) and t.get("case_type") == "email_delivery")]

if cases_df.empty:
    st.info("No cases match current filters.")
    raise SystemExit

cases_df = cases_df.sort_values(by="created_at", ascending=False)
options = {f"#{row.id} - {row.status} - {row.get('payload', '')[:40]}": row.id for row in cases_df.itertuples()}
selected_label = st.selectbox("Select a case", list(options.keys()))
row_id = options[selected_label]
row = cases_df[cases_df["id"] == row_id].iloc[0].to_dict()
case_id = row.get("case_id") or row_id

st.markdown(f"**Case ID:** {case_id} | **Status:** {row.get('status', 'unknown')} | **Conversation:** {row.get('conversation_id','')} | **Processor:** {row.get('processor_id','')}")

with st.expander("Original text"):
    redacted = row.get("redacted_payload") or row.get("payload", "")
    st.write(redacted)

triage_json = row.get("triage_json") or {}
if isinstance(triage_json, str):
    triage_json = _json_load(triage_json)

col_a, col_b = st.columns(2)
with col_a:
    st.subheader("Triage JSON")
    st.code(_pretty_json(triage_json), language="json")
with col_b:
    st.subheader("Draft customer reply")
    default_subject = triage_json.get("draft_customer_reply", {}).get("subject", "") if isinstance(triage_json, dict) else ""
default_body = triage_json.get("draft_customer_reply", {}).get("body", "") if isinstance(triage_json, dict) else ""
subject = st.text_input("Subject", value=row.get("draft_customer_reply_subject") or default_subject)
body = st.text_area("Body", value=row.get("draft_customer_reply_body") or default_body, height=200)

triage_subject = row.get("triage_draft_subject") or default_subject
triage_body = row.get("triage_draft_body") or default_body
raw_evidence = row.get("evidence_json")
if isinstance(raw_evidence, str):
    evidence_list = _json_load(raw_evidence) or []
elif isinstance(raw_evidence, list):
    evidence_list = raw_evidence
else:
    evidence_list = []
st.subheader("Review deltas")
delta_col1, delta_col2, delta_col3 = st.columns(3)
delta_col1.metric("Subject change", f"{_edit_ratio(triage_subject, subject) * 100:.1f}%")
delta_col2.metric("Body change", f"{_edit_ratio(triage_body, body) * 100:.1f}%")
delta_col3.metric("Evidence count", len(evidence_list))

missing_questions = row.get("missing_info_questions") or []
if isinstance(missing_questions, str):
    missing_questions = _json_load(missing_questions) or []

st.subheader("Missing info questions")
if missing_questions:
    st.markdown("\n".join(f"- {q}" for q in missing_questions))
else:
    st.write("None captured.")

reviewer = st.text_input("Reviewer (optional)", value=os.environ.get("USER") or os.environ.get("USERNAME") or "")
review_notes = st.text_area("Review notes (optional)", height=60)
reviewed_at = datetime.now(timezone.utc).isoformat()
error_tag_options = [
    "wrong_case_type",
    "redundant_questions",
    "tone_cold",
    "missing_time_details",
    "missing_scope_details",
    "hallucinated_claim",
]
error_tags = st.multiselect("Error tags (optional)", error_tag_options)

col1, col2, col3 = st.columns(3)
with col1:
    if st.button("Approve draft", use_container_width=True):
        final_subject = row.get("triage_draft_subject") or subject
        final_body = row.get("triage_draft_body") or body
        queue_db.update_row_status(
            row_id,
            status="approved",
            review_action="approved",
            reviewed_at=reviewed_at,
            reviewer=reviewer,
            review_notes=review_notes,
            review_final_subject=final_subject,
            review_final_body=final_body,
            diff_subject_ratio=0.0,
            diff_body_ratio=0.0,
            error_tags=error_tags,
            draft_customer_reply_subject=final_subject,
            draft_customer_reply_body=final_body,
        )
        export_path = _export_case(row, triage_json, subject, body, "approved")
        st.success(f"Case marked approved. Exported to {export_path}")
with col2:
    if st.button("Needs rewrite", use_container_width=True):
        diff_subj = _edit_ratio(row.get("triage_draft_subject"), subject)
        diff_body = _edit_ratio(row.get("triage_draft_body"), body)
        queue_db.update_row_status(
            row_id,
            status="rewrite",
            review_action="rewrite",
            reviewed_at=reviewed_at,
            reviewer=reviewer,
            review_notes=review_notes,
            review_final_subject=subject,
            review_final_body=body,
            diff_subject_ratio=diff_subj,
            diff_body_ratio=diff_body,
            error_tags=error_tags,
            draft_customer_reply_subject=subject,
            draft_customer_reply_body=body,
        )
        st.warning("Case flagged for rewrite.")
with col3:
    if st.button("Escalate", use_container_width=True):
        diff_subj = _edit_ratio(row.get("triage_draft_subject"), subject)
        diff_body = _edit_ratio(row.get("triage_draft_body"), body)
        queue_db.update_row_status(
            row_id,
            status="escalate_pending",
            review_action="escalate_pending",
            reviewed_at=reviewed_at,
            reviewer=reviewer,
            review_notes=review_notes,
            review_final_subject=subject,
            review_final_body=body,
            diff_subject_ratio=diff_subj,
            diff_body_ratio=diff_body,
            error_tags=error_tags,
            draft_customer_reply_subject=subject,
            draft_customer_reply_body=body,
        )
        export_path = _export_case(row, triage_json, subject, body, "escalate_pending")
        st.info(f"Case marked for escalation. Exported to {export_path}")

with st.expander("Raw record"):
    st.json(row)

st.subheader("Evidence and Report")
tab1, tab2 = st.tabs(["Evidence", "Final report"])
with tab1:
    evidence = evidence_list
    if not evidence:
        st.write("No evidence bundles recorded.")
    else:
        for bundle in evidence:
            tool = (bundle.get("metadata") or {}).get("tool_name") or bundle.get("source", "unknown")
            if tool == "log_evidence":
                st.markdown("**Log check**")
                decision = bundle.get("decision") or "not_observed"
                summary = bundle.get("summary_external") or ""
                counts = bundle.get("summary_counts") or {}
                incident = bundle.get("incident_window") or {}
                st.write(f"{decision}: {sanitize_public_text(summary)}")
                st.write(f"Incident window: {incident.get('start','?')} → {incident.get('end','?')}")
                st.write(f"Counts: errors={counts.get('errors',0)}, warnings={counts.get('warnings',0)}, total={counts.get('total_events',0)}")
                with st.expander("Sample events (internal)"):
                    st.code(_pretty_json(bundle.get("events") or []), language="json")
            else:
                st.markdown(f"**Source:** {bundle.get('source','unknown')} tenant={bundle.get('tenant')}")
                st.code(_pretty_json(bundle), language="json")
with tab2:
    report = row.get("final_report_json") or {}
    if isinstance(report, str):
        report = _json_load(report)
    if not report:
        st.write("No final report generated.")
    else:
        st.markdown(f"**Classification:** {report.get('classification', {})}")
        st.markdown("**Timeline summary**")
        st.code(report.get("timeline_summary", ""), language="text")
        st.markdown("**Customer update**")
        st.code(_pretty_json(report.get("customer_update", {})), language="json")
        st.markdown("**Engineering escalation**")
        st.code(_pretty_json(report.get("engineering_escalation", {})), language="json")
        st.markdown("**KB suggestions**")
        st.code(_pretty_json(report.get("kb_suggestions", [])), language="json")

if st.button("Export report package", use_container_width=True):
    export = {
        "case_id": case_id,
        "row": row,
        "triage": triage_json,
        "evidence": row.get("evidence_json"),
        "report": row.get("final_report_json"),
    }
    EXPORT_DIR.mkdir(parents=True, exist_ok=True)
    path = EXPORT_DIR / f"case_{row_id}_package_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}.json"
    path.write_text(_pretty_json(export), encoding="utf-8")
    st.success(f"Exported to {path}")

with st.expander("KB suggestions (if generated)"):
    kb_path = Path("data/kb_suggestions.jsonl")
    if kb_path.exists():
        lines = kb_path.read_text(encoding="utf-8").splitlines()
        if not lines:
            st.write("No suggestions yet.")
        else:
            for line in lines:
                try:
                    payload = json.loads(line)
                except json.JSONDecodeError:
                    continue
                st.markdown(f"- **{payload.get('title')}** (case {payload.get('case_id')}) refs: {payload.get('evidence_refs','')}")
    else:
        st.write("Run `python tools/kb_suggestions.py` to generate suggestions.")
