#!/usr/bin/env python3
"""
One-run end-to-end demo:

- Loads .env (if present)
- Optional: checks Ollama and pulls a model
- Runs pytest (optional)
- Seeds the queue DB with fake emails (tests/data_samples/fake_emails.jsonl if present)
- Runs triage_worker until queue is empty
- Exports:
    - data/demo_run/<timestamp>/INBOX_PREVIEW.md
    - data/demo_run/<timestamp>/emails/*.eml
    - data/demo_run/<timestamp>/rows/*.json  (raw queue rows, model in filename)

Usage examples:
  python tools/one_run.py
  python tools/one_run.py --triage-mode llm --ollama-model llama3.2:3b --ensure-ollama-model
  python tools/one_run.py --skip-tests
  python tools/one_run.py --db-path data/demo_queue.sqlite
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import sqlite3
import subprocess
import sys
import textwrap
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
DEFAULT_DB_PATH = REPO_ROOT / "data" / "demo_queue.sqlite"
DEFAULT_OUT_DIR = REPO_ROOT / "data" / "demo_run"


def _load_dotenv(dotenv_path: Path) -> None:
    """Minimal .env loader (no external dependency). Existing env wins."""
    if not dotenv_path.exists():
        return
    for raw in dotenv_path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


def _http_json(method: str, url: str, payload: Optional[dict] = None, timeout: int = 20) -> Any:
    data = None
    headers = {"Content-Type": "application/json"}
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
    req = Request(url=url, data=data, headers=headers, method=method.upper())
    with urlopen(req, timeout=timeout) as resp:  # nosec - local network call
        body = resp.read().decode("utf-8", errors="replace")
        return json.loads(body) if body else None


def _ollama_base_url() -> str:
    return (os.environ.get("OLLAMA_URL") or os.environ.get("OLLAMA_HOST") or "http://127.0.0.1:11434").rstrip("/")


def _ollama_healthcheck() -> Tuple[bool, str]:
    base = _ollama_base_url()
    try:
        version = _http_json("GET", f"{base}/api/version", timeout=5)
        return True, f"Ollama OK ({version})"
    except Exception as exc:
        return False, f"Ollama not reachable at {base}: {exc}"


def _ollama_has_model(model: str) -> bool:
    base = _ollama_base_url()
    tags = _http_json("GET", f"{base}/api/tags", timeout=20) or {}
    for item in tags.get("models") or []:
        if (item.get("name") or "") == model:
            return True
    return False


def _ollama_pull_model(model: str) -> None:
    base = _ollama_base_url()
    req = Request(
        url=f"{base}/api/pull",
        data=json.dumps({"name": model}).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urlopen(req, timeout=600) as resp:  # nosec - local network call
            for raw in resp:
                line = raw.decode("utf-8", errors="replace").strip()
                if not line:
                    continue
                try:
                    msg = json.loads(line)
                    status = msg.get("status") or "pulling"
                    completed = msg.get("completed")
                    total = msg.get("total")
                    if completed is not None and total:
                        pct = (completed / total) * 100.0
                        print(f"  {status}: {pct:5.1f}%")
                    else:
                        print(f"  {status}")
                except json.JSONDecodeError:
                    print(f"  {line}")
    except Exception as exc:
        raise RuntimeError(f"Failed to pull model via {base}/api/pull: {exc}") from exc


def _run_pytest(tests: Optional[List[str]] = None) -> int:
    test_targets = tests or [
        "tests/test_scenarios_v2.py",
        "tests/test_time_window.py",
        "tests/test_tool_selection.py",
        "tests/test_idempotency_and_retries.py",
    ]
    cmd = [sys.executable, "-m", "pytest", "-q", *test_targets]
    print(f"\n[tests] running: {' '.join(cmd)}")
    try:
        return subprocess.call(cmd, cwd=str(REPO_ROOT))
    except FileNotFoundError:
        print("[tests] pytest not available (skipping).")
        return 0


def _read_jsonl(path: Path) -> List[Dict[str, Any]]:
    items: List[Dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        items.append(json.loads(line))
    return items


def _find_fake_emails_file() -> Optional[Path]:
    candidates = [
        REPO_ROOT / "tests" / "data_samples" / "fake_emails.jsonl",
        REPO_ROOT / "tests" / "data_samples" / "fake_emails.json",
        REPO_ROOT / "data" / "fake_emails.jsonl",
    ]
    for p in candidates:
        if p.exists():
            return p
    return None


def _ensure_parent_dir(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)


def _seed_queue_with_fake_emails(db_path: Path) -> int:
    """Seeds the queue using app.queue_db; falls back to minimal SQLite if needed."""
    fake_path = _find_fake_emails_file()
    if not fake_path:
        print("[seed] No fake email file found; skipping seeding.")
        return 0

    print(f"[seed] Loading fake emails from: {fake_path}")
    emails: List[Dict[str, Any]]
    if fake_path.suffix.lower() == ".jsonl":
        emails = _read_jsonl(fake_path)
    else:
        emails = json.loads(fake_path.read_text(encoding="utf-8"))

    os.environ["DB_PATH"] = str(db_path)
    try:
        from app import queue_db  # type: ignore
        queue_db.init_db()

        def _attempt_insert() -> int:
            inserted_inner = 0
            for e in emails:
                payload = {
                    "case_id": e.get("id") or e.get("case_id") or "",
                    "text": e.get("body") or e.get("text") or "",
                    "end_user_handle": e.get("from") or e.get("sender") or e.get("tenant") or "",
                    "channel": e.get("channel") or "email",
                    "message_direction": "inbound",
                    "message_type": "text",
                    "raw_payload": json.dumps(e, ensure_ascii=False),
                    "conversation_id": e.get("thread_id") or e.get("conversation_id") or e.get("case_id") or "",
                    "ingest_signature": "one-run-seed",
                    "subject": e.get("subject") or "Support request",
                }
                queue_db.insert_message(payload)
                inserted_inner += 1
            return inserted_inner

        try:
            inserted = _attempt_insert()
        except Exception as inner_exc:
            if isinstance(inner_exc, sqlite3.OperationalError) and "no such column" in str(inner_exc).lower():
                # Recreate demo DB with the canonical schema then retry once.
                if db_path.exists():
                    db_path.unlink()
                queue_db.init_db()
                inserted = _attempt_insert()
            else:
                raise

        print(f"[seed] Seeded {inserted} emails into DB: {db_path}")
        return inserted
    except Exception as exc:
        print(f"[seed] queue_db seeding failed ({exc}); falling back to raw sqlite insert.")

    # Reset the demo DB to a clean slate for fallback schema.
    try:
        if db_path.exists():
            db_path.unlink()
    except OSError:
        pass

    _ensure_parent_dir(db_path)
    conn = sqlite3.connect(str(db_path))
    cur = conn.cursor()
    cur.executescript(
        """
        CREATE TABLE IF NOT EXISTS queue (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            case_id TEXT,
            message_id TEXT,
            idempotency_key TEXT,
            retry_count INTEGER DEFAULT 0,
            available_at TEXT,
            conversation_id TEXT,
            end_user_handle TEXT,
            channel TEXT DEFAULT 'email',
            message_direction TEXT DEFAULT 'inbound',
            message_type TEXT DEFAULT 'text',
            subject TEXT,
            payload TEXT,
            raw_payload TEXT,
            status TEXT DEFAULT 'queued',
            processor_id TEXT,
            started_at TEXT,
            finished_at TEXT,
            delivery_status TEXT DEFAULT 'pending',
            delivery_route TEXT,
            response_payload TEXT,
            response_metadata TEXT,
            latency_seconds REAL,
            quality_score REAL,
            matched TEXT,
            missing TEXT,
            triage_json TEXT,
            draft_customer_reply_subject TEXT,
            draft_customer_reply_body TEXT,
            missing_info_questions TEXT,
            llm_model TEXT,
            prompt_version TEXT,
            redaction_applied INTEGER,
            triage_mode TEXT,
            llm_latency_ms INTEGER,
            llm_attempts INTEGER,
            schema_valid INTEGER,
            redacted_payload TEXT,
            evidence_json TEXT,
            evidence_sources_run TEXT,
            evidence_created_at TEXT,
            final_report_json TEXT,
            ingest_signature TEXT,
            created_at TEXT DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
        );
        """
    )
    conn.commit()

    inserted = 0
    now = dt.datetime.utcnow().isoformat()
    for e in emails:
        cur.execute(
            """
            INSERT INTO queue (created_at, status, end_user_handle, subject, payload, case_id, conversation_id, channel, message_direction, message_type, raw_payload, ingest_signature)
            VALUES (?, 'queued', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                now,
                e.get("from") or e.get("sender") or "",
                e.get("subject") or "Support request",
                e.get("body") or e.get("text") or "",
                e.get("id") or e.get("case_id"),
                e.get("thread_id") or e.get("conversation_id") or e.get("case_id") or "",
                e.get("channel") or "email",
                "inbound",
                "text",
                json.dumps(e, ensure_ascii=False),
                "one-run-seed",
            ),
        )
        inserted += 1
    conn.commit()
    conn.close()
    print(f"[seed] Seeded {inserted} emails into DB (fallback schema): {db_path}")
    return inserted


def _drain_queue_with_triage_worker(db_path: Path) -> int:
    """Call triage_worker.process_once until queue is empty."""
    os.environ["DB_PATH"] = str(db_path)
    from tools import triage_worker  # type: ignore

    processed = 0
    print("\n[worker] Draining queue...")
    while True:
        did_work = triage_worker.process_once("one-run")
        if not did_work:
            break
        processed += 1
        if processed % 5 == 0:
            print(f"[worker] processed {processed} items...")
    print(f"[worker] Done. processed={processed}")
    return processed


def _fetch_rows(db_path: Path) -> List[Dict[str, Any]]:
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    try:
        cur.execute("SELECT * FROM queue ORDER BY id ASC")
        rows = [dict(r) for r in cur.fetchall()]
    except sqlite3.OperationalError:
        rows = []
    finally:
        conn.close()
    return rows


def _safe_json_load(x: Any) -> Any:
    if x is None:
        return None
    if isinstance(x, (dict, list)):
        return x
    if isinstance(x, str):
        s = x.strip()
        if not s:
            return None
        try:
            return json.loads(s)
        except json.JSONDecodeError:
            return {"_raw": s}
    return {"_raw": x}


def _slug(s: str) -> str:
    s = s.lower().strip()
    s = re.sub(r"[^a-z0-9]+", "-", s)
    return s.strip("-")[:80] or "case"


def _model_slug(rows: List[Dict[str, Any]]) -> str:
    env_model = os.environ.get("OLLAMA_MODEL") or os.environ.get("MODEL_NAME") or ""
    if env_model:
        return _slug(env_model)
    for row in rows:
        meta = _safe_json_load(row.get("response_metadata")) or {}
        triage_meta = meta.get("triage_meta") if isinstance(meta, dict) else {}
        if isinstance(triage_meta, dict) and triage_meta.get("llm_model"):
            return _slug(str(triage_meta["llm_model"]))
    return _slug(os.environ.get("TRIAGE_MODE", "heuristic"))


def _render_inbox_email(row: Dict[str, Any]) -> Tuple[str, str]:
    """Returns (subject, body) using whatever fields exist on the row."""
    tenant = row.get("end_user_handle") or "acme"
    case_id = row.get("case_id") or f"row-{row.get('id')}"
    triage = _safe_json_load(row.get("triage_json")) or {}
    report = _safe_json_load(row.get("final_report_json")) or {}
    evidence = _safe_json_load(row.get("evidence_json")) or {}
    meta = _safe_json_load(row.get("response_metadata")) or {}
    triage_meta = meta.get("triage_meta") if isinstance(meta, dict) else {}
    report_meta = meta.get("report_meta") if isinstance(meta, dict) else {}

    case_type = (triage.get("case_type") or "unknown").strip()
    severity = triage.get("severity") or "unknown"
    confidence = triage.get("confidence")
    llm_model = ""
    if isinstance(triage_meta, dict):
        llm_model = triage_meta.get("llm_model") or ""

    draft_subj = row.get("draft_customer_reply_subject") or triage.get("draft_customer_reply", {}).get("subject") or ""
    draft_body = row.get("draft_customer_reply_body") or triage.get("draft_customer_reply", {}).get("body") or ""

    orig_subject = row.get("subject") or "(no subject)"
    orig_text = row.get("payload") or row.get("text") or ""

    subject = f"[TriageBot/{tenant}] {case_type} ({severity}) — {orig_subject} — {case_id}"

    body = textwrap.dedent(
        f"""\
        TriageBot Inbox Preview
        ========================
        Tenant: {tenant}
        Case ID: {case_id}
        Row ID: {row.get("id")}
        Status: {row.get("status")}
        Created: {row.get("created_at")}
        Triage mode/model: {triage_meta.get("triage_mode") if isinstance(triage_meta, dict) else ''} {llm_model}
        Report model: {report_meta.get("llm_model") if isinstance(report_meta, dict) else ''}

        Original Customer Message
        -------------------------
        Subject: {orig_subject}

        {orig_text}

        Triage Summary
        --------------
        case_type: {case_type}
        severity: {severity}
        confidence: {confidence}

        Recommended Customer Reply
        --------------------------
        Subject: {draft_subj}

        {draft_body}

        Evidence Snapshot (raw)
        -----------------------
        {json.dumps(evidence, indent=2, ensure_ascii=False)[:12000]}

        Final Report (raw)
        ------------------
        {json.dumps(report, indent=2, ensure_ascii=False)[:12000]}
        """
    )
    return subject, body


def _write_eml(path: Path, to_addr: str, from_addr: str, subject: str, body: str) -> None:
    date_str = dt.datetime.now().strftime("%a, %d %b %Y %H:%M:%S %z")
    content = (
        f"From: {from_addr}\n"
        f"To: {to_addr}\n"
        f"Subject: {subject}\n"
        f"Date: {date_str}\n"
        f"MIME-Version: 1.0\n"
        f"Content-Type: text/plain; charset=utf-8\n"
        f"\n"
        f"{body}\n"
    )
    _ensure_parent_dir(path)
    path.write_text(content, encoding="utf-8")


def main() -> int:
    p = argparse.ArgumentParser(description="One-run demo runner (tests + queue + inbox preview).")
    p.add_argument("--db-path", default=str(DEFAULT_DB_PATH), help="SQLite DB path for this demo run.")
    p.add_argument("--out-dir", default=str(DEFAULT_OUT_DIR), help="Output directory for demo artifacts.")
    p.add_argument("--skip-tests", action="store_true", help="Skip running pytest.")
    p.add_argument("--tests", nargs="+", help="Optional explicit pytest targets (default: fast triage subset). Use 'all' to run full suite.")
    p.add_argument("--skip-seed", action="store_true", help="Skip seeding fake emails.")
    p.add_argument("--skip-worker", action="store_true", help="Skip draining queue with triage_worker.")
    p.add_argument("--triage-mode", default=None, help="Override TRIAGE_MODE env (rules|heuristic|llm).")
    p.add_argument("--ollama-model", default=None, help="Override OLLAMA_MODEL env.")
    p.add_argument("--ensure-ollama-model", action="store_true", help="If set, pull Ollama model when missing.")
    p.add_argument("--to", default=os.environ.get("DEMO_EMAIL_TO", "support@local"), help="To: address for .eml output.")
    p.add_argument("--from-addr", default=os.environ.get("DEMO_EMAIL_FROM", "triage-bot@local"), help="From: address for .eml output.")
    args = p.parse_args()

    _load_dotenv(REPO_ROOT / ".env")

    if args.triage_mode:
        os.environ["TRIAGE_MODE"] = args.triage_mode
    if args.ollama_model:
        os.environ["OLLAMA_MODEL"] = args.ollama_model

    db_path = Path(args.db_path)
    out_root = Path(args.out_dir) / dt.datetime.now().strftime("%Y%m%d_%H%M%S")
    out_emails = out_root / "emails"
    out_rows = out_root / "rows"
    out_root.mkdir(parents=True, exist_ok=True)

    triage_mode = (os.environ.get("TRIAGE_MODE") or "").lower()
    wants_llm = triage_mode == "llm" or args.ensure_ollama_model
    if wants_llm:
        ok, msg = _ollama_healthcheck()
        print(f"\n[ollama] {msg}")
        if not ok:
            print("[ollama] If using docker compose, ensure the ollama service is up and OLLAMA_URL/OLLAMA_HOST are set.")
            return 2
        model = os.environ.get("OLLAMA_MODEL") or args.ollama_model
        if model:
            present = _ollama_has_model(model)
            print(f"[ollama] model '{model}': {'present' if present else 'missing'}")
            if (not present) and args.ensure_ollama_model:
                print(f"[ollama] pulling model '{model}' ...")
                _ollama_pull_model(model)
        else:
            print("[ollama] OLLAMA_MODEL not set; skipping model presence check.")

    test_rc = 0
    if not args.skip_tests:
        if args.tests and len(args.tests) == 1 and args.tests[0].lower() == "all":
            test_rc = _run_pytest(tests=["tests"])
        else:
            test_rc = _run_pytest(tests=args.tests)

    os.environ["DB_PATH"] = str(db_path)
    _ensure_parent_dir(db_path)

    if not args.skip_seed:
        _seed_queue_with_fake_emails(db_path)
    if not args.skip_worker:
        _drain_queue_with_triage_worker(db_path)

    rows = _fetch_rows(db_path)
    model_slug = _model_slug(rows)

    inbox_md = ["# TriageBot – Inbox Preview", f"- Generated: {dt.datetime.now().isoformat()}", f"- DB: `{db_path}`", f"- Rows: {len(rows)}", ""]

    for row in rows:
        subject, body = _render_inbox_email(row)
        case_id = str(row.get("case_id") or f"row-{row.get('id')}")

        row_json_path = out_rows / f"{_slug(case_id)}--{model_slug}.json"
        _ensure_parent_dir(row_json_path)
        row_json_path.write_text(json.dumps(row, indent=2, ensure_ascii=False), encoding="utf-8")

        eml_path = out_emails / f"{_slug(case_id)}--{model_slug}.eml"
        _write_eml(eml_path, args.to, args.from_addr, subject, body)

        inbox_md.append("\n---\n")
        inbox_md.append(f"## {subject}\n")
        inbox_md.append(f"- Model: `{model_slug}`")
        inbox_md.append(f"- EML: `{eml_path.relative_to(REPO_ROOT)}`")
        inbox_md.append(f"- Row JSON: `{row_json_path.relative_to(REPO_ROOT)}`\n")
        inbox_md.append("```text")
        inbox_md.append(body.strip())
        inbox_md.append("```")

    preview_path = out_root / f"INBOX_PREVIEW--{model_slug}.md"
    preview_path.write_text("\n".join(inbox_md) + "\n", encoding="utf-8")

    print(f"\n[export] Wrote inbox preview: {preview_path}")
    print(f"[export] Wrote .eml files to: {out_emails}")
    print(f"[export] Wrote row JSON to: {out_rows}")

    if test_rc != 0:
        print(f"\n[tests] FAILED (exit={test_rc}) – demo outputs still generated.")
        return test_rc

    print("\n[done] All good.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
