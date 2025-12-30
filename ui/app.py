import os
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict

import pandas as pd
import streamlit as st

from app import queue_db

EXPORT_DIR = Path("data/exports")

st.set_page_config(page_title="Support Triage Copilot", layout="wide")
st.title("Support Triage Copilot — Review Console")
st.caption("Queue → worker → triage JSON + draft. Approve or send back for rewrite. No auto-send.")


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


def _update_status(row_id: int, status: str, subject: str | None = None, body: str | None = None) -> None:
    queue_db.update_row_status(
        row_id,
        status=status,
        draft_customer_reply_subject=subject or "",
        draft_customer_reply_body=body or "",
        finished_at=None,
    )


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


if not _auth_gate():
    st.stop()

cases_df = _load_cases()

with st.sidebar:
    st.subheader("Queue snapshot")
    if cases_df.empty:
        st.write("Queue is empty. Use the API or CLI to enqueue cases.")
    else:
        counts = cases_df["status"].value_counts() if "status" in cases_df else {}
        for status, count in counts.items():
            st.write(f"{status}: {count}")
        statuses = sorted(counts.index.tolist())
        status_filter = st.multiselect("Filter by status", statuses, default=statuses)
        tenant_filter = st.text_input("Filter by tenant/end user")
        email_only = st.checkbox("Email delivery cases only", value=False)
        st.write("Run worker: `python tools/triage_worker.py --watch`")

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

st.markdown(f"**Case ID:** {row_id} | **Status:** {row.get('status', 'unknown')} | **Conversation:** {row.get('conversation_id','')} | **Processor:** {row.get('processor_id','')}")

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

missing_questions = row.get("missing_info_questions") or []
if isinstance(missing_questions, str):
    missing_questions = _json_load(missing_questions) or []

st.subheader("Missing info questions")
if missing_questions:
    st.markdown("\n".join(f"- {q}" for q in missing_questions))
else:
    st.write("None captured.")

col1, col2, col3 = st.columns(3)
with col1:
    if st.button("Approve draft", use_container_width=True):
        _update_status(row_id, "approved", subject, body)
        export_path = _export_case(row, triage_json, subject, body, "approved")
        st.success(f"Case marked approved. Exported to {export_path}")
with col2:
    if st.button("Needs rewrite", use_container_width=True):
        _update_status(row_id, "rewrite", subject, body)
        st.warning("Case flagged for rewrite.")
with col3:
    if st.button("Escalate", use_container_width=True):
        _update_status(row_id, "escalate_pending", subject, body)
        export_path = _export_case(row, triage_json, subject, body, "escalate_pending")
        st.info(f"Case marked for escalation. Exported to {export_path}")

with st.expander("Raw record"):
    st.json(row)

st.subheader("Evidence and Report")
tab1, tab2 = st.tabs(["Evidence", "Final report"])
with tab1:
    evidence = row.get("evidence_json") or []
    if isinstance(evidence, str):
        evidence = _json_load(evidence) or []
    if not evidence:
        st.write("No evidence bundles recorded.")
    else:
        for bundle in evidence:
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
        "row": row,
        "triage": triage_json,
        "evidence": row.get("evidence_json"),
        "report": row.get("final_report_json"),
    }
    EXPORT_DIR.mkdir(parents=True, exist_ok=True)
    path = EXPORT_DIR / f"case_{row_id}_package_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}.json"
    path.write_text(_pretty_json(export), encoding="utf-8")
    st.success(f"Exported to {path}")
