import os
import json
import requests
import streamlit as st
from datetime import datetime


API_BASE = os.environ.get("API_BASE_URL", "http://localhost:8000")
API_KEY = os.environ.get("INGEST_API_KEY", "")
HEADERS = {"X-API-KEY": API_KEY} if API_KEY else {}


def _fmt_ts(ts: str | None) -> str:
    if not ts:
        return ""
    try:
        return datetime.fromisoformat(ts.replace("Z", "+00:00")).strftime("%Y-%m-%d %H:%M:%S")
    except Exception:
        return ts


def fetch_json(path: str, params: dict | None = None) -> dict:
    resp = requests.get(f"{API_BASE}{path}", headers=HEADERS, params=params or {})
    resp.raise_for_status()
    return resp.json()


def post_json(path: str, payload: dict | None = None) -> dict:
    resp = requests.post(f"{API_BASE}{path}", headers=HEADERS, json=payload or {})
    resp.raise_for_status()
    return resp.json()


def _friendly_reason(reason: str | None) -> str:
    if not reason:
        return ""
    mapping = {
        "default_no_date": "last 24h (no date provided)",
        "fallback_last24h": "last 24h (fallback)",
        "triage_time_window": "triage-provided window",
        "triage_time_window_inferred_end": "triage window (inferred end)",
        "triage_time_window_inferred_start": "triage window (inferred start)",
        "parsed_from_text": "parsed from message",
        "sanity_override": "sanity override to fallback",
    }
    return mapping.get(reason, reason)


def main():
    st.set_page_config(page_title="Intake Console", layout="wide")
    st.title("Support Triage Console")

    with st.sidebar:
        st.header("Filters")
        tenant = st.text_input("Tenant", value="")
        confidence = st.selectbox("Identity confidence", ["", "high", "low", "unknown"], index=0)
        query = st.text_input("Search (subject/body)", value="")
        limit = st.slider("Max intakes", 1, 200, 50)
        if st.button("Refresh"):
            st.experimental_rerun()

    params = {"limit": limit}
    if tenant:
        params["tenant"] = tenant
    if confidence:
        params["confidence"] = confidence
    if query:
        params["q"] = query

    intakes_data = fetch_json("/intakes", params=params)
    intakes = intakes_data.get("intakes", [])
    intake_ids = [i["intake_id"] for i in intakes]
    selected = st.selectbox("Select intake", intake_ids, format_func=lambda x: next((i["subject_raw"] or x for i in intakes if i["intake_id"] == x), x))

    if not selected:
        st.stop()

    intake = fetch_json(f"/intakes/{selected}")
    st.subheader("Envelope")
    cols = st.columns(4)
    cols[0].markdown(f"**From:** {intake.get('from_address','')}")
    cols[1].markdown(f"**Tenant:** {intake.get('tenant_id') or 'unknown'}")
    cols[2].markdown(f"**Confidence:** {intake.get('identity_confidence')}")
    cols[3].markdown(f"**Status:** {intake.get('status','new')}")
    status_options = ["new", "investigating", "awaiting_customer", "escalated", "resolved"]
    try:
        status_idx = status_options.index(intake.get("status", "new"))
    except ValueError:
        status_idx = 0
    status = st.selectbox("Update status", status_options, index=status_idx)
    resolution_note = st.text_input("Resolution note (optional)", value=intake.get("resolution_note") or "")
    if st.button("Save status"):
        try:
            post_json(f"/intakes/{selected}/status", {"status": status, "resolution_note": resolution_note})
            st.success("Status updated")
            st.experimental_rerun()
        except Exception as exc:
            st.error(f"Status update failed: {exc}")
    if intake.get("status") == "escalated" and not intake.get("acknowledged_at"):
        if st.button("Acknowledge (T3/Admin)"):
            try:
                post_json(f"/intakes/{selected}/acknowledge")
                st.success("Acknowledged")
                st.experimental_rerun()
            except Exception as exc:
                st.error(f"Acknowledge failed: {exc}")
    st.markdown(f"**Subject:** {intake.get('subject_raw','')}")
    st.text_area("Body (immutable)", intake.get("body_raw", ""), height=160)

    evidence = fetch_json(f"/intakes/{selected}/evidence").get("evidence", [])
    if evidence:
        first_meta = evidence[0].get("metadata") or {}
        cust_tw = first_meta.get("customer_time_window") or {}
        inv_tw = first_meta.get("investigation_time_window") or {}
        cust_reason = _friendly_reason(first_meta.get("customer_time_window_reason"))
        inv_reason = _friendly_reason(first_meta.get("query_time_window_reason"))
        st.subheader("Case overview")
        c1, c2, c3 = st.columns([1, 1, 1])
        c1.markdown("**Customer-reported window**")
        c1.markdown(f"- Start: {_fmt_ts(cust_tw.get('start')) or '—'}")
        c1.markdown(f"- End: {_fmt_ts(cust_tw.get('end')) or '—'}")
        c1.markdown(f"- Reason: {cust_reason or '—'}")
        c2.markdown("**Investigation window**")
        c2.markdown(f"- Start: {_fmt_ts(inv_tw.get('start')) or '—'}")
        c2.markdown(f"- End: {_fmt_ts(inv_tw.get('end')) or '—'}")
        c2.markdown(f"- Reason: {inv_reason or '—'}")

        # Top evidence summary
        log_ev = next((e for e in evidence if e.get("tool_name") == "log_evidence" or e.get("source") == "logs"), None)
        svc_ev = next((e for e in evidence if (e.get("tool_name") == "service_status")), None)
        summary_lines = []
        if log_ev:
            m = log_ev.get("metadata") or {}
            counts = m.get("summary_counts") or {}
            summary_lines.append(f"Logs: {m.get('summary_external') or 'no summary'}")
            if counts:
                summary_lines.append(
                    f"Counts: errors={counts.get('errors',0)}, timeouts={counts.get('timeouts',0)}, availability_gaps={counts.get('availability_gaps',0)}"
                )
        if svc_ev:
            m = svc_ev.get("metadata") or {}
            status = m.get("status") or "unknown"
            notes = " / ".join(m.get("notes") or [])
            suffix = f" ({notes})" if notes else ""
            summary_lines.append(f"Service status: {status}{suffix}")
        if summary_lines:
            c3.markdown("**Evidence summary**")
            for line in summary_lines:
                c3.markdown(f"- {line}")

        draft_text = intake.get("triage_draft_body") or intake.get("draft_customer_reply_body") or ""
        st.text_area("Customer update (copy)", draft_text, height=140)
        # Escalation button gated by role
        ui_role = os.environ.get("UI_ROLE", "").lower()
        if ui_role in {"t3", "admin"}:
            if st.button("Escalate to Tier 3 (status=escalated)"):
                note = f"Escalated via UI ({ui_role})"
                try:
                    post_json(f"/intakes/{selected}/status", {"status": "escalated", "resolution_note": note})
                    st.success("Escalated and status updated")
                    st.experimental_rerun()
                except Exception as exc:
                    st.error(f"Escalate failed: {exc}")
        else:
            st.info("Escalate is available for T3/Admin roles (set UI_ROLE).")
            try:
                post_json(f"/intakes/{selected}/status", {"status": "escalated"})
                st.success("Escalated and status updated")
                st.experimental_rerun()
            except Exception as exc:
                st.error(f"Escalate failed: {exc}")

    st.subheader("Evidence timeline")
    for ev in evidence:
        with st.expander(f"{ev.get('tool_name')} @ {_fmt_ts(ev.get('ran_at'))}"):
            st.markdown(f"- Summary: {ev.get('summary_external') or '(none)'}")
            st.markdown(f"- Status: {ev.get('status')}")
            st.markdown(f"- Hash: {ev.get('result_hash')}")
            st.markdown(f"- Cache bucket: {ev.get('time_bucket')}")
            replay_col, info_col = st.columns([1, 3])
            if replay_col.button(f"Replay {ev['evidence_id']}", key=f"replay-{ev['evidence_id']}"):
                try:
                    res = post_json(f"/evidence/{ev['evidence_id']}/replay")
                    info_col.success(f"Replayed: new evidence_id={res.get('evidence_id')} cache_hit={res.get('cache_hit')}")
                except Exception as exc:  # pragma: no cover - UI feedback
                    info_col.error(f"Replay failed: {exc}")
            if info_col.button(f"Force refresh {ev['evidence_id']}", key=f"force-{ev['evidence_id']}"):
                try:
                    res = post_json(f"/evidence/{ev['evidence_id']}/replay?force=true")
                    msg = f"Refreshed: new evidence_id={res.get('evidence_id')} cache_hit={res.get('cache_hit')}"
                    if res.get("diff"):
                        msg += f" diff={res['diff']}"
                    info_col.success(msg)
                except Exception as exc:
                    info_col.error(f"Force replay failed: {exc}")
            with info_col.expander("Internal details", expanded=False):
                st.code(json.dumps({"params_hash": ev.get("params_hash"), "summary_internal": ev.get("summary_internal"), "diff": ev.get("diff")}, indent=2))

    st.subheader("Draft (external-safe)")
    st.info("Customer drafts only use external-safe summaries and confidence-aware phrasing.")

    handoffs = fetch_json(f"/intakes/{selected}/handoffs").get("handoffs", [])
    if handoffs:
        st.subheader("Handoff packs")
        for ho in handoffs:
            payload = json.loads(ho.get("payload_json") or "{}")
            st.markdown(f"- Handoff ID: {ho['handoff_id']} @ {_fmt_ts(ho.get('created_at'))}")
            st.markdown(f"- Status: {ho.get('status')}")
            with st.expander("Payload", expanded=False):
                st.code(json.dumps(payload, indent=2))
            ev_refs = payload.get("evidence_refs") or []
            if ev_refs and st.button(f"Replay all evidence for {ho['handoff_id']}", key=f"replay-all-{ho['handoff_id']}"):
                msgs = []
                for ref in ev_refs:
                    try:
                        res = post_json(f"/evidence/{ref['evidence_id']}/replay")
                        msgs.append(f"{ref['evidence_id']} -> {res.get('evidence_id')} (cache_hit={res.get('cache_hit')})")
                    except Exception as exc:  # pragma: no cover
                        msgs.append(f"{ref['evidence_id']} failed: {exc}")
                st.success("\n".join(msgs))
    else:
        st.markdown("No handoff packs yet.")

    st.subheader("Export")
    export_mode = st.selectbox("Export mode", ["external", "internal"])
    if st.button("Download export"):
        try:
            data = fetch_json(f"/intakes/{selected}/export", params={"mode": export_mode})
            st.download_button("Save export", data=json.dumps(data, indent=2), file_name=f"{selected}-{export_mode}.json")
        except Exception as exc:
            st.error(f"Export failed: {exc}")


if __name__ == "__main__":
    main()
