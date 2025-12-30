#!/usr/bin/env python3
"""
Compute learning metrics from the SQLite queue and export summary artifacts.

Outputs (under data/learning/):
- learning_metrics.json
- learning_report.md
- learning_rows.csv
"""

from __future__ import annotations

import argparse
import csv
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List
import difflib

from app import queue_db
from tools.triage_worker import EXPECTED_TOOLS_BY_CASE

LEARNING_DIR = Path("data/learning")
EMAIL_RE = re.compile(r"([A-Za-z0-9._%+-]+)@([A-Za-z0-9.-]+\\.[A-Za-z]{2,})", re.IGNORECASE)
TIME_QUESTION_RE = re.compile(r"\\b(time|when)\\b", re.IGNORECASE)
DOMAIN_QUESTION_RE = re.compile(r"domain|recipient", re.IGNORECASE)
TIME_EXPR_RE = re.compile(r"(\\b\\d{1,2}:\\d{2}\\b|since\\s+\\w+|yesterday|today|this\\s+morning|UTC)", re.IGNORECASE)
SEVERITY_RE = re.compile(r"severity\\s+(?:noted\\s+as|is)\\s+(\\w+)", re.IGNORECASE)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _parse_json(value: Any) -> Any:
    if isinstance(value, str) and value.strip():
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return value
    return value


def _duration_seconds(start: str | None, end: str | None) -> float | None:
    if not start or not end:
        return None
    try:
        a = datetime.fromisoformat(start.replace("Z", "+00:00"))
        b = datetime.fromisoformat(end.replace("Z", "+00:00"))
        return max((b - a).total_seconds(), 0.0)
    except Exception:
        return None


def _edit_changed(a: str, b: str) -> int:
    a = (a or "").strip()
    b = (b or "").strip()
    return 0 if a == b else 1


def _edit_ratio(a: str | None, b: str | None) -> float:
    a = a or ""
    b = b or ""
    if not a and not b:
        return 0.0
    sim = difflib.SequenceMatcher(None, a, b).ratio()
    return round(1.0 - sim, 4)


def _has_time_expr(text: str) -> bool:
    return bool(TIME_EXPR_RE.search(text or ""))


def _redundant_questions(questions: List[str], triage: Dict[str, Any], inbound_text: str) -> Dict[str, int]:
    redundant_time = 0
    redundant_domain = 0
    if not questions:
        return {"time": 0, "domain": 0}

    reported_tw = triage.get("reported_time_window") or {}
    actionable_tw = triage.get("time_window") or {}
    has_time = bool(reported_tw.get("raw_text")) or bool(actionable_tw.get("start") or actionable_tw.get("end")) or _has_time_expr(inbound_text)
    if has_time and any(TIME_QUESTION_RE.search(q or "") for q in questions):
        redundant_time = 1

    scope = triage.get("scope") or {}
    domains = scope.get("recipient_domains") or []
    has_domains = bool(domains)
    if has_domains and any(DOMAIN_QUESTION_RE.search(q or "") for q in questions):
        redundant_domain = 1

    return {"time": redundant_time, "domain": redundant_domain}


def _routing_accuracy(case_type: str, executed: List[str]) -> Dict[str, Any]:
    expected = EXPECTED_TOOLS_BY_CASE.get(case_type, set())
    executed_set = {e.split(":")[0] for e in executed if e}
    if not expected:
        return {"expected": [], "executed": list(executed_set), "ok": True}
    ok = expected.issubset(executed_set)
    return {"expected": sorted(expected), "executed": sorted(executed_set), "ok": ok}


def _median(vals: List[float]) -> float | None:
    if not vals:
        return None
    vals = sorted(vals)
    mid = len(vals) // 2
    if len(vals) % 2 == 0:
        return (vals[mid - 1] + vals[mid]) / 2
    return vals[mid]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Compute learning metrics from the queue DB.")
    parser.add_argument("--db-path", default=str(queue_db.DB_PATH), help="Path to SQLite DB")
    parser.add_argument("--out-dir", default=str(LEARNING_DIR), help="Directory for learning outputs")
    args = parser.parse_args(argv)

    # Point queue_db at the requested DB before fetch.
    queue_db.DB_PATH = Path(args.db_path)
    queue_db.init_db()

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    rows = queue_db.fetch_queue(limit=1000)
    if not rows:
        print("No rows found.")
        return 0

    per_case: List[Dict[str, Any]] = []
    metrics: Dict[str, Any] = {
        "generated_at": _now_iso(),
        "total_cases": len(rows),
        "approval_counts": {},
        "status_counts": {},
        "by_case_type": {},
        "claim_warnings_total": 0,
        "time_to_first_draft_median_s": None,
        "time_to_reviewed_median_s": None,
        "redundant_time_questions": 0,
        "redundant_domain_questions": 0,
        "contradiction_count": 0,
        "routing_ok": 0,
        "routing_total": 0,
        "tag_counts": {},
    }

    time_to_draft: List[float] = []
    time_to_review: List[float] = []

    for row in rows:
        triage = _parse_json(row.get("triage_json")) or {}
        report = _parse_json(row.get("final_report_json")) or {}
        resp_meta = _parse_json(row.get("response_metadata")) or {}
        triage_meta = {}
        if isinstance(resp_meta, dict):
            triage_meta = resp_meta.get("triage_meta") or {}

        review_action = row.get("review_action") or ""
        status = row.get("status") or ""
        case_type = triage.get("case_type") or "unknown"
        metrics["approval_counts"][review_action] = metrics["approval_counts"].get(review_action, 0) + 1
        metrics["status_counts"][status] = metrics["status_counts"].get(status, 0) + 1
        metrics["by_case_type"].setdefault(case_type, {"count": 0, "approved": 0, "rewrite": 0, "escalate": 0})
        metrics["by_case_type"][case_type]["count"] += 1
        if review_action in {"approved", "rewrite", "escalate_pending"}:
            metrics["by_case_type"][case_type][review_action if review_action != "escalate_pending" else "escalate"] += 1

        claim_warnings = 0
        if isinstance(report, dict):
            meta = report.get("_meta") or {}
            claim_warnings = len(meta.get("claim_warnings") or [])
        metrics["claim_warnings_total"] += claim_warnings

        inbound_text = row.get("redacted_payload") or row.get("payload") or ""
        questions = triage.get("missing_info_questions") or []
        redundancies = _redundant_questions(questions if isinstance(questions, list) else [], triage if isinstance(triage, dict) else {}, inbound_text)
        metrics["redundant_time_questions"] += redundancies["time"]
        metrics["redundant_domain_questions"] += redundancies["domain"]

        draft_body = row.get("draft_customer_reply_body") or ""
        contradiction = 0
        if triage.get("severity") and isinstance(draft_body, str):
            m = SEVERITY_RE.search(draft_body)
            if m and m.group(1).lower() != str(triage.get("severity")).lower():
                contradiction = 1
        metrics["contradiction_count"] += contradiction

        ttfd = _duration_seconds(row.get("started_at"), row.get("finished_at"))
        if ttfd is not None:
            time_to_draft.append(ttfd)
        ttrev = _duration_seconds(row.get("started_at"), row.get("reviewed_at"))
        if ttrev is not None:
            time_to_review.append(ttrev)

        executed = _parse_json(row.get("evidence_sources_run")) or []
        routing = _routing_accuracy(case_type, executed if isinstance(executed, list) else [])
        metrics["routing_total"] += 1 if routing["expected"] else 0
        metrics["routing_ok"] += 1 if routing["expected"] and routing["ok"] else 0

        error_tags = _parse_json(row.get("error_tags")) or []
        if isinstance(error_tags, str):
            error_tags = [error_tags]
        for tag in error_tags or []:
            metrics["tag_counts"][tag] = metrics["tag_counts"].get(tag, 0) + 1

        diff_subject_ratio = row.get("diff_subject_ratio")
        diff_body_ratio = row.get("diff_body_ratio")
        if diff_subject_ratio is None:
            diff_subject_ratio = _edit_ratio(row.get("triage_draft_subject"), row.get("draft_customer_reply_subject"))
        if diff_body_ratio is None:
            diff_body_ratio = _edit_ratio(row.get("triage_draft_body"), row.get("draft_customer_reply_body"))

        per_case.append(
            {
                "id": row.get("id"),
                "case_id": row.get("case_id"),
                "case_type": case_type,
                "severity": triage.get("severity"),
                "status": status,
                "review_action": review_action,
                "reviewer": row.get("reviewer") or "",
                "triage_mode": triage_meta.get("triage_mode") or "",
                "llm_model": triage_meta.get("llm_model") or row.get("llm_model") or "",
                "missing_info_questions": len(triage.get("missing_info_questions") or []),
                "claim_warnings": claim_warnings,
                "subject_edit_changed": _edit_changed(row.get("triage_draft_subject"), row.get("draft_customer_reply_subject")),
                "body_edit_changed": _edit_changed(row.get("triage_draft_body"), row.get("draft_customer_reply_body")),
                "diff_subject_ratio": diff_subject_ratio,
                "diff_body_ratio": diff_body_ratio,
                "time_to_first_draft_s": ttfd,
                "time_to_reviewed_s": ttrev,
                "redundant_time_question": redundancies["time"],
                "redundant_domain_question": redundancies["domain"],
                "routing_ok": routing["ok"],
                "routing_expected": ",".join(routing["expected"]),
                "routing_executed": ",".join(routing["executed"]),
                "contradiction": contradiction,
                "error_tags": ",".join(error_tags) if error_tags else "",
            }
        )

    metrics["time_to_first_draft_median_s"] = _median(time_to_draft)
    metrics["time_to_reviewed_median_s"] = _median(time_to_review)

    metrics_path = out_dir / "learning_metrics.json"
    metrics_path.write_text(json.dumps(metrics, indent=2, ensure_ascii=False), encoding="utf-8")

    report_lines = [
        "# Learning Report",
        f"- Generated: {_now_iso()}",
        f"- Cases: {metrics['total_cases']}",
        f"- Approval counts: {metrics['approval_counts']}",
        f"- Status counts: {metrics['status_counts']}",
        f"- Claim warnings total: {metrics['claim_warnings_total']}",
    ]
    report_lines.append(f"- Median time to first draft (s): {metrics['time_to_first_draft_median_s']}")
    report_lines.append(f"- Median time to reviewed (s): {metrics['time_to_reviewed_median_s']}")
    report_lines.append(f"- Redundant time questions: {metrics['redundant_time_questions']}")
    report_lines.append(f"- Redundant domain questions: {metrics['redundant_domain_questions']}")
    report_lines.append(f"- Contradictions detected: {metrics['contradiction_count']}")
    if metrics["routing_total"]:
        pct = round((metrics["routing_ok"] / metrics["routing_total"]) * 100, 1)
        report_lines.append(f"- Routing accuracy: {metrics['routing_ok']}/{metrics['routing_total']} ({pct}%)")
    if metrics["tag_counts"]:
        report_lines.append(f"- Error tags: {metrics['tag_counts']}")
    report_lines.append("\\n## By case type")
    for ct, stats in metrics["by_case_type"].items():
        report_lines.append(f"- {ct}: {stats}")
    (out_dir / "learning_report.md").write_text("\\n".join(report_lines) + "\\n", encoding="utf-8")

    csv_path = out_dir / "learning_rows.csv"
    with csv_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=list(per_case[0].keys()),
        )
        writer.writeheader()
        for row in per_case:
            writer.writerow(row)

    print(f"Wrote {metrics_path}, {csv_path}, and learning_report.md")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
