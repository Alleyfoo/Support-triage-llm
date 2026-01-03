from __future__ import annotations

import argparse
import json
import random
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from tools.reliability import utils
from tools.log_evidence import INCIDENT_THRESHOLD

DEFAULT_OUT = Path("tests/fixtures/generated")
DEFAULT_TAG_OUT = Path("tests/fixtures/tag_tests")
DEFAULT_SEED = 123
DEFAULT_SCENARIOS = 50


def _derive_query_window(customer_tw: Dict[str, Optional[str]], anchor_iso: str) -> Dict[str, str]:
    """Mirror triage_worker fallback when no explicit time was parsed."""
    anchor = utils.parse_iso(anchor_iso)
    start = customer_tw.get("start")
    end = customer_tw.get("end")
    if start and end:
        return {"start": start, "end": end, "source": "customer_hint"}
    if start and not end:
        try:
            end_dt = utils.parse_iso(start) + timedelta(hours=2)
            return {"start": start, "end": utils.isoformat(end_dt), "source": "customer_start_only"}
        except Exception:
            pass
    if end and not start:
        try:
            start_dt = utils.parse_iso(end) - timedelta(hours=2)
            return {"start": utils.isoformat(start_dt), "end": end, "source": "customer_end_only"}
        except Exception:
            pass
    return {
        "start": utils.isoformat(anchor - timedelta(hours=24)),
        "end": utils.isoformat(anchor),
        "source": "anchor_last24h",
    }


@dataclass
class Scenario:
    id: str
    text: str
    anchor_time: str
    service: str
    tenant: str
    case_type: str
    severity: str
    time_expression: str
    customer_time_window: Dict[str, Optional[str]]
    expects_time_window: bool
    query_time_window: Dict[str, str]
    incident_window: Optional[Dict[str, str]]
    observed_incident: bool
    log_path: str
    log_pattern: str
    notes: str
    tags: Dict[str, object]
    truth_flags: Dict[str, object]

    def to_json(self) -> Dict[str, object]:
        return {
            "id": self.id,
            "text": self.text,
            "anchor_time": self.anchor_time,
            "service": self.service,
            "tenant": self.tenant,
            "case_type": self.case_type,
            "severity": self.severity,
            "time_expression": self.time_expression,
            "customer_time_window": self.customer_time_window,
            "expects_time_window": self.expects_time_window,
            "query_time_window": self.query_time_window,
            "incident_window": self.incident_window,
            "observed_incident": self.observed_incident,
            "log_path": self.log_path,
            "log_pattern": self.log_pattern,
            "notes": self.notes,
            "tags": self.tags,
            "truth": {
                "customer_time_window": self.customer_time_window,
                "query_time_window": self.query_time_window,
                "incident_window": self.incident_window,
                "expects_customer_window": self.truth_flags.get("expects_customer_window"),
                "expects_incident": self.truth_flags.get("expects_incident"),
                "expects_high_confidence": self.truth_flags.get("expects_high_confidence"),
            },
        }


def _time_pattern_iso(anchor: datetime, rng: random.Random) -> Tuple[str, Dict[str, Optional[str]], bool]:
    start = anchor.replace(hour=10, minute=30, second=0, microsecond=0) + timedelta(minutes=rng.randint(-25, 25))
    end = start + timedelta(hours=2)
    phrase = f"around {start.strftime('%Y-%m-%dT%H:%M')} UTC"
    return phrase, {"start": utils.isoformat(start), "end": utils.isoformat(end)}, True


def _time_pattern_range(anchor: datetime, rng: random.Random) -> Tuple[str, Dict[str, Optional[str]], bool]:
    start = anchor.replace(hour=9, minute=15, second=0, microsecond=0) + timedelta(minutes=rng.randint(-15, 15))
    end = start + timedelta(minutes=rng.randint(45, 90))
    phrase = f"between {start.strftime('%H:%M')} and {end.strftime('%H:%M')} UTC today"
    return phrase, {"start": utils.isoformat(start), "end": utils.isoformat(end)}, True


def _time_pattern_yesterday(anchor: datetime, rng: random.Random) -> Tuple[str, Dict[str, Optional[str]], bool]:
    base = anchor - timedelta(days=1)
    start = base.replace(hour=16, minute=rng.choice([0, 15, 30, 45]), second=0, microsecond=0)
    phrase = f"yesterday around {start.strftime('%H:%M')} UTC"
    end = start + timedelta(hours=2)
    return phrase, {"start": utils.isoformat(start), "end": utils.isoformat(end)}, True


def _time_pattern_last_night(anchor: datetime, rng: random.Random) -> Tuple[str, Dict[str, Optional[str]], bool]:
    phrase = "last night with no clear timestamp"
    return phrase, {"start": None, "end": None}, False


def _time_pattern_clock_only(anchor: datetime, rng: random.Random) -> Tuple[str, Dict[str, Optional[str]], bool]:
    start = anchor.replace(hour=rng.choice([6, 7, 8]), minute=rng.choice([0, 5, 20, 45]), second=0, microsecond=0)
    phrase = f"at {start.strftime('%H:%M')} UTC"
    end = start + timedelta(hours=2)
    return phrase, {"start": utils.isoformat(start), "end": utils.isoformat(end)}, True


def _time_pattern_timezone(anchor: datetime, rng: random.Random) -> Tuple[str, Dict[str, Optional[str]], bool]:
    local = anchor.replace(hour=17, minute=0, second=0, microsecond=0)
    # Treat PT as PDT (-7) for May dates to better match real-world behavior.
    start = local + timedelta(hours=7)
    phrase = f"around 5pm PT (UTC-7) on {start.strftime('%B %d')}"
    end = start + timedelta(hours=1, minutes=30)
    return phrase, {"start": utils.isoformat(start), "end": utils.isoformat(end)}, True


def _time_pattern_vague(anchor: datetime, rng: random.Random) -> Tuple[str, Dict[str, Optional[str]], bool]:
    phrase = rng.choice(
        [
            "recently (no time given)",
            "earlier today without exact time",
            "over the past few hours with no timestamp",
        ]
    )
    return phrase, {"start": None, "end": None}, False


TIME_PATTERNS = [
    ("iso", _time_pattern_iso),
    ("range", _time_pattern_range),
    ("yesterday", _time_pattern_yesterday),
    ("last_night", _time_pattern_last_night),
    ("clock_only", _time_pattern_clock_only),
    ("timezone_pt", _time_pattern_timezone),
    ("vague", _time_pattern_vague),
]


def _compute_tags(time_key: str, customer_tw: Dict[str, Optional[str]], log_pattern: str) -> Dict[str, object]:
    # time_expr mapping
    time_expr_map = {
        "iso": "explicit_iso",
        "range": "time_only",
        "clock_only": "time_only",
        "timezone_pt": "timezone",
        "yesterday": "relative",
        "last_night": "relative",
        "vague": "relative",
    }
    time_expr = time_expr_map.get(time_key, "none")

    has_explicit_date = time_expr in {"explicit_iso", "timezone"}
    has_time_of_day = time_expr in {"explicit_iso", "time_only", "timezone"} or (
        time_expr == "relative" and bool(customer_tw.get("start"))
    )
    has_timezone_hint = time_expr in {"explicit_iso", "time_only", "timezone"} or (
        isinstance(customer_tw.get("start"), str) and customer_tw.get("start", "").endswith("Z")
    )
    ambiguity_level = {
        "explicit_iso": "low",
        "timezone": "low",
        "time_only": "medium",
        "relative": "high",
        "none": "high",
    }.get(time_expr, "medium")

    log_tag = {"no_logs": "clean"}.get(log_pattern, log_pattern)
    return {
        "time_expr": time_expr,
        "has_explicit_date": has_explicit_date,
        "has_time_of_day": has_time_of_day,
        "has_timezone_hint": has_timezone_hint,
        "ambiguity_level": ambiguity_level,
        "log_pattern": log_tag,
    }


def _pick_incident_window(query_tw: Dict[str, str], rng: random.Random) -> Dict[str, str]:
    start_dt = utils.parse_iso(query_tw["start"])
    end_dt = utils.parse_iso(query_tw["end"])
    span_minutes = int((end_dt - start_dt).total_seconds() // 60)
    if span_minutes < 90:
        incident_start = start_dt + timedelta(minutes=5)
    else:
        incident_start = start_dt + timedelta(minutes=rng.randint(10, min(120, span_minutes - 60)))
    incident_end = incident_start + timedelta(minutes=rng.randint(10, 35))
    incident_end = min(incident_end, end_dt)
    return {"start": utils.isoformat(incident_start), "end": utils.isoformat(incident_end)}


def _synthesize_logs(
    pattern: str,
    query_tw: Dict[str, str],
    incident_window: Optional[Dict[str, str]],
    service: str,
    rng: random.Random,
    tenant: str,
) -> List[Dict[str, object]]:
    start_dt = utils.parse_iso(query_tw["start"])
    end_dt = utils.parse_iso(query_tw["end"])
    entries: List[Dict[str, object]] = []

    def _add(ts: datetime, level: str, event_type: str, message: str, status: Optional[int] = None) -> None:
        entries.append(
            {
                "ts": utils.isoformat(ts),
                "tenant": tenant,
                "service": service,
                "level": level.upper(),
                "event_type": event_type,
                "message": message,
                "status_code": status,
                "request_id": None,
                "latency_ms": rng.randint(80, 400),
            }
        )

    if pattern == "no_logs":
        return entries

    if incident_window:
        inc_start = utils.parse_iso(incident_window["start"])
        inc_end = utils.parse_iso(incident_window["end"])
    else:
        inc_start = start_dt + timedelta(minutes=15)
        inc_end = inc_start + timedelta(minutes=15)

    if pattern in {"burst", "recovery"}:
        # Pre-incident noise
        for minute in range(-20, 0, 10):
            ts = max(start_dt, inc_start + timedelta(minutes=minute))
            _add(ts, "INFO", "health_check", "Routine health check ok", 200)
        # Incident burst
        cursor = inc_start
        while cursor <= inc_end:
            _add(cursor, "ERROR", "http_5xx", "Upstream 503 from provider", 503)
            cursor += timedelta(minutes=rng.randint(2, 6))
        # Recovery info trail
        if pattern == "recovery":
            for minute in range(5, 25, 5):
                ts = min(end_dt, inc_end + timedelta(minutes=minute))
                _add(ts, "INFO", "recovered", "Service recovered", 200)
    elif pattern == "sparse":
        for minute in range(0, 40, 20):
            ts = inc_start + timedelta(minutes=minute)
            _add(ts, "ERROR", "http_500", "Intermittent failure", 500)
        _add(inc_end + timedelta(minutes=20), "INFO", "health_check", "No further anomalies", 200)
    elif pattern == "noisy":
        cursor = start_dt
        while cursor < end_dt:
            level = rng.choice(["INFO", "WARN", "ERROR"])
            status = 500 if level == "ERROR" else (429 if level == "WARN" else 200)
            event_type = "burst_5xx" if level == "ERROR" else ("slowdown" if level == "WARN" else "activity")
            _add(cursor, level, event_type, "Mixed traffic sample", status)
            cursor += timedelta(minutes=rng.randint(8, 16))
    else:
        # default background, keep error count below threshold to avoid false incidents
        error_budget = 2
        cursor = start_dt
        while cursor < end_dt:
            level = rng.choices(["INFO", "WARN", "ERROR"], weights=[0.6, 0.25, 0.15])[0]
            if level == "ERROR" and error_budget <= 0:
                level = "WARN"
            if level == "ERROR":
                error_budget -= 1
            status = 500 if level == "ERROR" else (429 if level == "WARN" else 200)
            event_type = "burst_5xx" if level == "ERROR" else ("slowdown" if level == "WARN" else "activity")
            _add(cursor, level, event_type, "Mixed traffic sample", status)
            cursor += timedelta(minutes=rng.randint(8, 16))

    entries.sort(key=lambda e: e["ts"])
    return entries


def _compose_text(case_type: str, service: str, phrase: str, log_pattern: str, observed: bool) -> str:
    prefix = "Customers are reporting" if observed else "We suspect"
    base = f"{prefix} issues with the {service} service {phrase}."
    if case_type == "integration":
        base += " Webhooks are failing and retries are not clearing."
    elif case_type == "email_delivery":
        base += " Bounces and delays increased."
    else:
        base += " API calls are timing out."
    if log_pattern == "no_logs":
        base += " We could not find logs yet."
    elif log_pattern == "sparse":
        base += " Logs are sparse and only show a few errors."
    return base


def build_scenario(idx: int, rng: random.Random) -> Tuple[Scenario, List[Dict[str, object]]]:
    anchor = datetime(2025, 5, 1, 12, 0, tzinfo=timezone.utc) + timedelta(days=idx // 12, hours=rng.randint(-3, 3))
    time_key, pattern_fn = rng.choice(TIME_PATTERNS)
    phrase, customer_tw, expects_time = pattern_fn(anchor, rng)
    anchor_iso = utils.isoformat(anchor)
    query_tw = _derive_query_window(customer_tw, anchor_iso)

    log_pattern = rng.choice(["burst", "recovery", "sparse", "no_logs", "noisy"])
    incident_window_hint = _pick_incident_window(query_tw, rng) if log_pattern in {"burst", "recovery"} else None
    service = rng.choice(["api", "worker"])
    case_type = rng.choice(["incident", "integration", "unknown"])
    severity = rng.choice(["high", "medium", "low"])
    scenario_id = f"scenario_{idx:04d}"
    tenant = rng.choice(["acme", "globex", "umbrella", "contoso"])
    logs = _synthesize_logs(log_pattern, query_tw, incident_window_hint, service, rng, tenant)
    signal_entries = [e for e in logs if e.get("level") == "ERROR" or (e.get("status_code") or 0) >= 500]
    observed_incident = len(signal_entries) >= INCIDENT_THRESHOLD
    incident_window = None
    if observed_incident:
        incident_window = {
            "start": signal_entries[0]["ts"],
            "end": signal_entries[-1]["ts"],
        }
    text = _compose_text(case_type, service, phrase, log_pattern, observed_incident)
    tags = _compute_tags(time_key, customer_tw, log_pattern)
    truth_flags = {
        "expects_customer_window": expects_time,
        "expects_incident": observed_incident,
        "expects_high_confidence": tags.get("ambiguity_level") == "low",
    }

    scenario = Scenario(
        id=scenario_id,
        text=text,
        anchor_time=anchor_iso,
        service=service,
        tenant=tenant,
        case_type=case_type,
        severity=severity,
        time_expression=time_key,
        customer_time_window=customer_tw,
        expects_time_window=expects_time,
        query_time_window=query_tw,
        incident_window=incident_window,
        observed_incident=observed_incident,
        log_path=f"{scenario_id}.jsonl",
        log_pattern=log_pattern,
        notes="synthetic",
        tags=tags,
        truth_flags=truth_flags,
    )
    return scenario, logs


def _write_json(path: Path, payload: Dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")


def _write_jsonl(path: Path, rows: List[Dict[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [json.dumps(row, ensure_ascii=False) for row in rows]
    path.write_text("\n".join(lines), encoding="utf-8")


def generate(out_dir: Path, seed: int, n: int) -> List[Scenario]:
    rng = random.Random(seed)
    scenarios_dir = out_dir / "scenarios"
    logs_dir = out_dir / "logs"
    scenarios_dir.mkdir(parents=True, exist_ok=True)
    logs_dir.mkdir(parents=True, exist_ok=True)

    scenarios: List[Scenario] = []
    for idx in range(1, n + 1):
        scenario, logs = build_scenario(idx, rng)
        scenarios.append(scenario)
        _write_json(scenarios_dir / f"{scenario.id}.json", scenario.to_json())
        _write_jsonl(logs_dir / scenario.log_path, logs)
    return scenarios


def generate_tag_tests(out_dir: Path) -> List[Scenario]:
    """
    Write a small tagged suite to a separate folder for explicit expectation checks.

    These scenarios carry tags.expected_issue describing the intended failure mode.
    """
    scenarios_dir = out_dir / "scenarios"
    logs_dir = out_dir / "logs"
    scenarios_dir.mkdir(parents=True, exist_ok=True)
    logs_dir.mkdir(parents=True, exist_ok=True)

    curated: List[Scenario] = []

    # 1) Incident miss: truth expects incident, but logs are empty.
    curated.append(
        Scenario(
            id="tag_incident_miss",
            text="Synthetic canary: outage at 12:00 UTC today (should be missed).",
            anchor_time=utils.isoformat(datetime(2025, 5, 10, 15, 0, tzinfo=timezone.utc)),
            service="api",
            tenant="acme",
            case_type="incident",
            severity="high",
            time_expression="clock_only",
            customer_time_window={"start": "2025-05-10T12:00:00Z", "end": "2025-05-10T14:00:00Z"},
            expects_time_window=True,
            query_time_window={"start": "2025-05-10T12:00:00Z", "end": "2025-05-10T14:00:00Z", "source": "tag"},
            incident_window={"start": "2025-05-10T12:05:00Z", "end": "2025-05-10T12:25:00Z"},
            observed_incident=True,
            log_path="tag_incident_miss.jsonl",
            log_pattern="clean",
            notes="tagged_canary",
            tags={"time_expr": "time_only", "log_pattern": "clean", "expected_issue": "incident_miss"},
            truth_flags={"expects_customer_window": True, "expects_incident": True, "expects_high_confidence": True},
        )
    )
    (logs_dir / "tag_incident_miss.jsonl").write_text("", encoding="utf-8")

    # 2) Time window shift: logs exist but outside customer window.
    curated.append(
        Scenario(
            id="tag_time_shift",
            text="Synthetic canary: errors at 18:00 UTC, customer reported 10:00-12:00 UTC.",
            anchor_time=utils.isoformat(datetime(2025, 5, 11, 13, 0, tzinfo=timezone.utc)),
            service="worker",
            tenant="globex",
            case_type="incident",
            severity="medium",
            time_expression="range",
            customer_time_window={"start": "2025-05-11T10:00:00Z", "end": "2025-05-11T12:00:00Z"},
            expects_time_window=True,
            query_time_window={"start": "2025-05-11T10:00:00Z", "end": "2025-05-11T12:00:00Z", "source": "tag"},
            incident_window={"start": "2025-05-11T10:15:00Z", "end": "2025-05-11T10:35:00Z"},
            observed_incident=True,
            log_path="tag_time_shift.jsonl",
            log_pattern="noisy",
            notes="tagged_canary",
            tags={"time_expr": "time_only", "log_pattern": "noisy", "expected_issue": "time_window_miss"},
            truth_flags={"expects_customer_window": True, "expects_incident": True, "expects_high_confidence": True},
        )
    )
    logs_shift = [
        {"ts": "2025-05-11T18:00:00Z", "tenant": "globex", "service": "worker", "level": "ERROR", "event_type": "http_500", "message": "Late error", "status_code": 500},
        {"ts": "2025-05-11T18:10:00Z", "tenant": "globex", "service": "worker", "level": "ERROR", "event_type": "http_500", "message": "Late error", "status_code": 500},
    ]
    _write_jsonl(logs_dir / "tag_time_shift.jsonl", logs_shift)

    # 3) Control pass case.
    curated.append(
        Scenario(
            id="tag_happy_path",
            text="Synthetic control: outage at 09:00 UTC today with clear logs.",
            anchor_time=utils.isoformat(datetime(2025, 5, 12, 12, 0, tzinfo=timezone.utc)),
            service="api",
            tenant="umbrella",
            case_type="incident",
            severity="high",
            time_expression="clock_only",
            customer_time_window={"start": "2025-05-12T09:00:00Z", "end": "2025-05-12T11:00:00Z"},
            expects_time_window=True,
            query_time_window={"start": "2025-05-12T09:00:00Z", "end": "2025-05-12T11:00:00Z", "source": "tag"},
            incident_window={"start": "2025-05-12T09:05:00Z", "end": "2025-05-12T09:25:00Z"},
            observed_incident=True,
            log_path="tag_happy_path.jsonl",
            log_pattern="burst",
            notes="tagged_canary",
            tags={"time_expr": "time_only", "log_pattern": "burst", "expected_issue": "none"},
            truth_flags={"expects_customer_window": True, "expects_incident": True, "expects_high_confidence": True},
        )
    )
    logs_pass = [
        {"ts": "2025-05-12T09:05:00Z", "tenant": "umbrella", "service": "api", "level": "ERROR", "event_type": "http_500", "message": "Error", "status_code": 500},
        {"ts": "2025-05-12T09:15:00Z", "tenant": "umbrella", "service": "api", "level": "ERROR", "event_type": "http_500", "message": "Error", "status_code": 500},
        {"ts": "2025-05-12T09:25:00Z", "tenant": "umbrella", "service": "api", "level": "ERROR", "event_type": "http_500", "message": "Error", "status_code": 500},
    ]
    _write_jsonl(logs_dir / "tag_happy_path.jsonl", logs_pass)

    for scenario in curated:
        _write_json(scenarios_dir / f"{scenario.id}.json", scenario.to_json())
    return curated


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate deterministic reliability scenarios + logs.")
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT, help="Output directory root for scenarios and logs.")
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED, help="Seed for deterministic generation.")
    parser.add_argument("--n", type=int, default=DEFAULT_SCENARIOS, help="Number of scenarios to generate.")
    parser.add_argument("--tag-tests", action="store_true", help="Also generate tagged canary scenarios under tests/fixtures/tag_tests.")
    args = parser.parse_args()

    scenarios = generate(args.out, args.seed, args.n)
    if args.tag_tests:
        generate_tag_tests(DEFAULT_TAG_OUT)
    print(f"Wrote {len(scenarios)} scenarios to {args.out}")


if __name__ == "__main__":
    main()
