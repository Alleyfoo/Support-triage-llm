from __future__ import annotations

import argparse
import json
import subprocess
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from app.triage_service import triage
from app.validation import SchemaValidationError, validate_payload
from tools import registry, triage_worker
from tools.reliability import utils
from tools.reliability.metrics_store import ReliabilityMetricsStore


@dataclass
class ScenarioTruth:
    id: str
    text: str
    anchor_time: str
    service: str
    tenant: Optional[str]
    case_type: str
    severity: str
    time_expression: str
    customer_time_window: Dict[str, Optional[str]]
    expects_customer_window: bool
    query_time_window: Dict[str, str]
    incident_window: Optional[Dict[str, str]]
    observed_incident: bool
    log_path: str
    log_pattern: str
    tags: Dict[str, Any]
    expects_high_confidence: bool

    @classmethod
    def from_json(cls, payload: Dict[str, Any]) -> "ScenarioTruth":
        truth_obj = payload.get("truth") or {}
        tags = payload.get("tags") or {}
        return cls(
            id=payload["id"],
            text=payload["text"],
            anchor_time=payload["anchor_time"],
            service=payload.get("service") or "api",
            tenant=payload.get("tenant"),
            case_type=payload.get("case_type") or "unknown",
            severity=payload.get("severity") or "low",
            time_expression=payload.get("time_expression") or "unknown",
            customer_time_window=payload.get("customer_time_window") or {"start": None, "end": None},
            expects_customer_window=bool(
                truth_obj.get("expects_customer_window")
                if "expects_customer_window" in truth_obj
                else payload.get("expects_time_window")
            ),
            query_time_window=payload.get("query_time_window") or {"start": None, "end": None},
            incident_window=truth_obj.get("incident_window") or payload.get("incident_window"),
            observed_incident=bool(truth_obj.get("expects_incident") if "expects_incident" in truth_obj else payload.get("observed_incident")),
            log_path=payload.get("log_path") or "",
            log_pattern=payload.get("log_pattern") or "",
            tags=tags,
            expects_high_confidence=bool(
                truth_obj.get("expects_high_confidence")
                if "expects_high_confidence" in truth_obj
                else tags.get("ambiguity_level") == "low"
            ),
        )


@dataclass
class ScenarioResult:
    id: str
    status: str
    failures: List[str]
    metrics: Dict[str, Any]
    truth: ScenarioTruth
    triage_payload: Dict[str, Any]
    predicted_query_window: Dict[str, Any]
    log_result: Optional[Dict[str, Any]]
    latencies_ms: Dict[str, float]

    def as_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "status": self.status,
            "failures": self.failures,
            "metrics": self.metrics,
            "truth": self.truth.__dict__,
            "triage_payload": self.triage_payload,
            "predicted_query_window": self.predicted_query_window,
            "log_result": self.log_result,
            "latencies_ms": self.latencies_ms,
        }


def _git_sha() -> str:
    try:
        out = subprocess.check_output(["git", "rev-parse", "HEAD"], stderr=subprocess.DEVNULL)
        return out.decode().strip()
    except Exception:
        return "unknown"


def _trim_log_bundle(bundle: Dict[str, Any]) -> Dict[str, Any]:
    trimmed = {**bundle}
    events = bundle.get("events") or []
    trimmed["events"] = events[:5]
    return trimmed


def _claim_safe(bundle: Optional[Dict[str, Any]]) -> bool:
    if not bundle:
        return False
    meta = bundle.get("metadata") or {}
    note = (meta.get("note") or "").lower()
    summary = (bundle.get("summary_external") or "").lower()
    return "absence of evidence" in note or "did not find entries" in summary


def _evaluate_scenario(truth: ScenarioTruth, logs_dir: Path) -> ScenarioResult:
    failures: List[str] = []
    triage_start = time.perf_counter()
    triage_result = triage(truth.text, metadata={"received_at": truth.anchor_time})
    triage_ms = (time.perf_counter() - triage_start) * 1000

    payload = {k: v for k, v in triage_result.items() if k != "_meta"}
    schema_ok = True
    try:
        validate_payload(payload, "triage.schema.json")
    except SchemaValidationError as exc:
        schema_ok = False
        failures.append(f"schema:{exc}")

    predicted_tw = triage_result.get("time_window") or {"start": None, "end": None}
    predicted_confidence = float(predicted_tw.get("confidence") or 0.0)
    extracted_any = utils.has_window(predicted_tw)

    predicted_query_window = triage_worker._derive_query_time_window(triage_result, triage_result.get("_meta", {}))
    query_iou = utils.iou(predicted_query_window, truth.query_time_window)

    expected_window = truth.customer_time_window
    customer_iou = utils.iou(predicted_tw, expected_window)
    exact_time_match = utils.has_window(expected_window) and utils.has_window(predicted_tw) and expected_window == predicted_tw
    if truth.expects_customer_window and not extracted_any:
        failures.append("time_missing")
    if truth.expects_customer_window and customer_iou < 0.5:
        failures.append("time_iou_low")
    if not truth.expects_customer_window and extracted_any:
        failures.append("time_unexpected")
    if truth.expects_customer_window and predicted_confidence < 0.3 and truth.expects_high_confidence:
        failures.append("time_confidence_low")

    incident_hint = dict(truth.customer_time_window or {})
    if not utils.has_window(incident_hint):
        incident_hint = {"start": predicted_query_window.get("start"), "end": predicted_query_window.get("end")}

    log_params = {
        "service": truth.service,
        "tenant": truth.tenant,
        "query_type": "errors",
        "time_window": {"start": predicted_query_window.get("start"), "end": predicted_query_window.get("end")},
        "incident_window": {"start": incident_hint.get("start"), "end": incident_hint.get("end")},
        "reason": "reliability_validation",
        "fixture_path": str(logs_dir / truth.log_path),
    }
    log_bundle: Optional[Dict[str, Any]] = None
    log_ms = 0.0
    tool_error = False
    try:
        log_start = time.perf_counter()
        log_bundle = registry.run_tool("log_evidence", log_params)
        log_ms = (time.perf_counter() - log_start) * 1000
    except Exception as exc:
        tool_error = True
        failures.append(f"log_tool_error:{exc}")

    incident_iou = utils.iou(log_bundle.get("incident_window") if log_bundle else None, truth.incident_window)
    pred_observed = bool(log_bundle.get("observed_incident")) if log_bundle else False
    claim_safe = _claim_safe(log_bundle)
    if truth.observed_incident and not pred_observed:
        failures.append("incident_fn")
    if not truth.observed_incident and pred_observed:
        failures.append("incident_fp")
    if truth.incident_window and incident_iou < 0.5:
        failures.append("incident_window_miss")
    if not truth.observed_incident and not claim_safe:
        failures.append("claim_safety")

    metrics = {
        "customer_window_iou": customer_iou,
        "query_window_iou": query_iou,
        "incident_window_iou": incident_iou,
        "extracted_any_window": extracted_any,
        "expected_time_window": truth.expects_customer_window,
        "predicted_time_confidence": predicted_confidence,
        "pred_observed_incident": pred_observed,
        "truth_observed_incident": truth.observed_incident,
        "exact_time_match": bool(exact_time_match),
        "schema_ok": schema_ok,
        "tool_error": tool_error,
    }

    status = "pass" if not failures else "fail"
    trimmed_log = _trim_log_bundle(log_bundle) if log_bundle else None
    return ScenarioResult(
        id=truth.id,
        status=status,
        failures=failures,
        metrics=metrics,
        truth=truth,
        triage_payload=payload,
        predicted_query_window=predicted_query_window,
        log_result=trimmed_log,
        latencies_ms={"triage": triage_ms, "log": log_ms},
    )


def _aggregate(results: List[ScenarioResult], seed: int) -> Dict[str, Any]:
    total = len(results)
    if total == 0:
        return {}
    def _avg(values: List[float]) -> float:
        return round(sum(values) / len(values), 4) if values else 0.0

    extracted = [r.metrics["extracted_any_window"] for r in results]
    customer_ious = [r.metrics["customer_window_iou"] for r in results if r.truth.expects_customer_window]
    query_ious = [r.metrics["query_window_iou"] for r in results if r.truth.expects_customer_window]
    incident_ious = [r.metrics["incident_window_iou"] for r in results if r.truth.incident_window]
    schema_failures = [r for r in results if not r.metrics["schema_ok"]]
    tool_errors = [r for r in results if r.metrics["tool_error"]]
    pred_flags = [r.metrics["pred_observed_incident"] for r in results]
    truth_flags = [r.metrics["truth_observed_incident"] for r in results]
    triage_lat = [r.latencies_ms["triage"] for r in results]
    log_lat = [r.latencies_ms["log"] for r in results]

    tp = sum(1 for r in results if r.metrics["pred_observed_incident"] and r.metrics["truth_observed_incident"])
    fp = sum(1 for r in results if r.metrics["pred_observed_incident"] and not r.metrics["truth_observed_incident"])
    fn = sum(1 for r in results if not r.metrics["pred_observed_incident"] and r.metrics["truth_observed_incident"])

    return {
        "seed": seed,
        "total_cases": total,
        "pass_rate": round(sum(1 for r in results if r.status == "pass") / total, 4),
        "date_extraction_rate": round(sum(extracted) / total, 4),
        "customer_window_iou_avg": _avg(customer_ious),
        "query_window_iou_avg": _avg(query_ious),
        "incident_window_iou_avg": _avg(incident_ious),
        "incident_detection_precision": round(tp / (tp + fp), 4) if (tp + fp) else 0.0,
        "incident_detection_recall": round(tp / (tp + fn), 4) if (tp + fn) else 0.0,
        "schema_failure_rate": round(len(schema_failures) / total, 4),
        "tool_error_rate": round(len(tool_errors) / total, 4),
        "exact_time_match_rate": round(sum(1 for r in results if r.metrics["exact_time_match"]) / total, 4),
        "avg_latency_ms": {
            "triage": _avg(triage_lat),
            "log": _avg(log_lat),
        },
    }


def _render_md(run_meta: Dict[str, Any], metrics: Dict[str, Any], failures: List[ScenarioResult]) -> str:
    lines = [
        f"# Reliability Run {run_meta['ts']}",
        "",
        f"- git_sha: {run_meta['git_sha']}",
        f"- model_id: {run_meta['model_id']}",
        f"- seed: {run_meta['seed']}",
        f"- scenarios: {run_meta['n_cases']}",
        "",
        "## Metrics",
    ]
    for key in [
        "pass_rate",
        "date_extraction_rate",
        "customer_window_iou_avg",
        "query_window_iou_avg",
        "incident_window_iou_avg",
        "incident_detection_precision",
        "incident_detection_recall",
        "schema_failure_rate",
        "tool_error_rate",
    ]:
        lines.append(f"- {key}: {metrics.get(key)}")
    lines.append(f"- avg_latency_ms.triage: {metrics.get('avg_latency_ms', {}).get('triage')}")
    lines.append(f"- avg_latency_ms.log: {metrics.get('avg_latency_ms', {}).get('log')}")

    if failures:
        lines.append("")
        lines.append("## Recent Failures")
        for idx, fr in enumerate(failures[:10], start=1):
            detail = "; ".join(fr.failures)
            lines.append(
                f"{idx}. {fr.id} - {detail} - "
                f"customer_iou={fr.metrics['customer_window_iou']}, incident_iou={fr.metrics['incident_window_iou']}"
            )
            lines.append(f"   text: {fr.truth.text[:140]}...")
    return "\n".join(lines)


def _load_scenarios(path: Path) -> List[ScenarioTruth]:
    scenarios: List[ScenarioTruth] = []
    for scenario_file in sorted(path.glob("*.json")):
        payload = json.loads(scenario_file.read_text(encoding="utf-8"))
        scenarios.append(ScenarioTruth.from_json(payload))
    return scenarios


def main() -> None:
    parser = argparse.ArgumentParser(description="Validate reliability scenarios and score metrics.")
    parser.add_argument("--scenarios", type=Path, required=True, help="Directory of scenario JSON files.")
    parser.add_argument("--logs", type=Path, required=True, help="Directory containing synthetic log jsonl files.")
    parser.add_argument("--out", type=Path, default=Path("reports/reliability/latest.json"), help="Output JSON report path.")
    parser.add_argument("--model-id", type=str, default=None, help="Model identifier for tracking.")
    parser.add_argument("--seed", type=int, default=123, help="Seed used for generation (for bookkeeping).")
    args = parser.parse_args()

    scenarios = _load_scenarios(args.scenarios)
    results: List[ScenarioResult] = []
    for truth in scenarios:
        results.append(_evaluate_scenario(truth, args.logs))

    metrics = _aggregate(results, args.seed)
    run_meta = {
        "ts": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "git_sha": _git_sha(),
        "model_id": args.model_id or "unknown",
        "seed": args.seed,
        "n_cases": len(results),
    }

    report = {
        "run": run_meta,
        "metrics": metrics,
        "scenarios": [r.as_dict() for r in results],
        "failures": [r.as_dict() for r in results if r.status == "fail"],
    }

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")

    md_path = args.out.with_suffix(".md")
    md_path.write_text(_render_md(run_meta, metrics, [r for r in results if r.status == "fail"]), encoding="utf-8")

    store = ReliabilityMetricsStore()
    failures_sample = [r.as_dict() for r in results if r.status == "fail"][:20]
    store.insert_run(run_meta["ts"], run_meta["git_sha"], run_meta["model_id"], args.seed, len(results), metrics, failures_sample)
    print(f"Wrote report to {args.out} and {md_path}")


if __name__ == "__main__":
    main()
