import json
from pathlib import Path
from typing import Any, Dict

import pandas as pd
import streamlit as st

from app import queue_db

st.set_page_config(page_title="Support Triage Copilot", layout="wide")
st.title("Support Triage Copilot ? Review Console")
st.caption("Queue ? worker ? triage JSON + draft. Approve or send back for rewrite. No auto-send.")


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


cases_df = _load_cases()

with st.sidebar:
    st.subheader("Queue snapshot")
    if cases_df.empty:
        st.write("Queue is empty. Use the API or CLI to enqueue cases.")
    else:
        counts = cases_df["status"].value_counts() if "status" in cases_df else {}
        for status, count in counts.items():
            st.write(f"{status}: {count}")
        st.write("Use `python tools/triage_worker.py --watch` to process queued items.")

st.subheader("Cases")
if cases_df.empty:
    st.info("No cases available yet.")
    raise SystemExit

cases_df = cases_df.sort_values(by="created_at", ascending=False)
options = {f"#{row.id} - {row.status} - {row.get('payload', '')[:40]}": row.id for row in cases_df.itertuples()}
selected_label = st.selectbox("Select a case", list(options.keys()))
row_id = options[selected_label]
row = cases_df[cases_df["id"] == row_id].iloc[0].to_dict()

st.markdown(f"**Status:** {row.get('status', 'unknown')} | **Conversation:** {row.get('conversation_id','')} | **Processor:** {row.get('processor_id','')}")

with st.expander("Original text"):
    st.write(row.get("payload", ""))

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
    st.markdown("
".join(f"- {q}" for q in missing_questions))
else:
    st.write("None captured.")

col1, col2, col3 = st.columns(3)
with col1:
    if st.button("Approve draft", use_container_width=True):
        _update_status(row_id, "approved", subject, body)
        st.success("Case marked approved.")
with col2:
    if st.button("Needs rewrite", use_container_width=True):
        _update_status(row_id, "rewrite", subject, body)
        st.warning("Case flagged for rewrite.")
with col3:
    if st.button("Escalate", use_container_width=True):
        _update_status(row_id, "escalate_pending", subject, body)
        st.info("Case marked for escalation (manual follow-up required).")

with st.expander("Raw record"):
    st.json(row)
