#!/usr/bin/env python3
"""Send approved customer replies directly via SMTP.

Reads queue + approval tracker, emails approved replies to the original
customer, updates queue status to 'sent', and logs each send.

Approvals file (CSV/XLSX) must include at least: id, decision, comment, decided_at.
"""

from __future__ import annotations

import argparse
import csv
import os
import smtplib
import ssl
from email.message import EmailMessage
from email.utils import parseaddr
from pathlib import Path
from typing import Dict, Set, Tuple

import pandas as pd

import sys
sys.path.append(str(Path(__file__).resolve().parents[1]))

from tools.process_queue import save_queue


def _load_sent_log(path: Path) -> Set[Tuple[str, str]]:
    if not path.exists():
        return set()
    try:
        with path.open("r", encoding="utf-8", newline="") as f:
            reader = csv.DictReader(f)
            return {(row.get("id", ""), row.get("decided_at", "")) for row in reader}
    except Exception:
        return set()


def _append_sent_log(path: Path, row_id: str, decided_at: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    is_new = not path.exists()
    with path.open("a", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=["id", "decided_at"])
        if is_new:
            writer.writeheader()
        writer.writerow({"id": row_id, "decided_at": decided_at})


def _send_message(host: str, port: int, starttls: bool, username: str | None, password: str | None, msg: EmailMessage) -> None:
    if starttls:
        context = ssl.create_default_context()
        with smtplib.SMTP(host, port) as server:
            server.ehlo()
            server.starttls(context=context)
            server.ehlo()
            if username and password:
                server.login(username, password)
            server.send_message(msg)
    else:
        with smtplib.SMTP(host, port) as server:
            if username and password:
                server.login(username, password)
            server.send_message(msg)


def _load_approvals(path: Path) -> pd.DataFrame:
    if not path.exists():
        raise SystemExit(f"Approvals file not found: {path}")
    if path.suffix.lower() in {".xlsx", ".xls"}:
        df = pd.read_excel(path)
    else:
        df = pd.read_csv(path)
    required = {"id", "decision"}
    missing = required - set(df.columns)
    if missing:
        raise SystemExit(f"Approvals file missing columns: {sorted(missing)}")
    return df


def main() -> None:
    ap = argparse.ArgumentParser(description="Send approved replies to customers via SMTP")
    ap.add_argument("--queue", default="data/email_queue.xlsx", help="Queue workbook path")
    ap.add_argument("--approvals", default="data/approvals.csv", help="CSV/XLSX approvals file")
    ap.add_argument("--log", default="data/approved_sent_log.csv", help="CSV log of final sends")
    ap.add_argument("--agent-name", default="send-agent", help="Identifier for this sender")
    args = ap.parse_args()

    queue_path = Path(args.queue)
    if not queue_path.exists():
        raise SystemExit(f"Queue file not found: {queue_path}")
    try:
        df = pd.read_excel(queue_path)
    except Exception as exc:
        raise SystemExit(f"Unable to read queue workbook: {exc}")

    approvals_df = _load_approvals(Path(args.approvals))
    approvals_df["decision"] = approvals_df["decision"].astype(str).str.strip().str.lower()
    approved = approvals_df[approvals_df["decision"].isin(["approved", "approve", "ok"])]
    if approved.empty:
        print("No approved entries found.")
        return

    sent_log = Path(args.log)
    sent_keys = _load_sent_log(sent_log)

    host = os.environ.get("SMTP_HOST") or "localhost"
    port = int(os.environ.get("SMTP_PORT") or 587)
    starttls = str(os.environ.get("SMTP_STARTTLS", "1")).lower() in {"1", "true", "yes"}
    username = os.environ.get("SMTP_USERNAME")
    password = os.environ.get("SMTP_PASSWORD")
    sender = os.environ.get("SMTP_FROM") or "no-reply@local"

    rows = df.copy()
    rows["status"] = rows["status"].astype(str).str.lower()
    # Ensure necessary columns exist
    for col in ("sent_at", "sent_agent", "sent_to", "approval_comment"):
        if col not in rows.columns:
            rows[col] = ""

    sent_count = 0
    for _, approval in approved.iterrows():
        row_id = approval.get("id")
        if pd.isna(row_id):
            continue
        row_str = str(row_id)
        decided_at = str(approval.get("decided_at", ""))
        key = (row_str, decided_at)
        if decided_at and key in sent_keys:
            continue

        queue_rows = rows[rows["id"].astype(str) == row_str]
        if queue_rows.empty:
            continue
        queue_idx = queue_rows.index[0]
        queue_row = queue_rows.iloc[0]
        reply = str(queue_row.get("reply", ""))
        if not reply:
            continue
        original_subject = str(queue_row.get("subject", "")).strip()
        customer = str(queue_row.get("customer", "")).strip()
        _, customer_email = parseaddr(customer)
        if not customer_email:
            continue

        subject = f"Response: {original_subject}" if original_subject else "Response from support"
        body = reply
        msg = EmailMessage()
        msg["From"] = sender
        msg["To"] = customer_email
        msg["Subject"] = subject
        msg.set_content(body)

        _send_message(host, port, starttls, username, password, msg)
        sent_count += 1
        if decided_at:
            _append_sent_log(sent_log, row_str, decided_at)

        rows.at[queue_idx, "status"] = "sent"
        rows.at[queue_idx, "sent_at"] = pd.Timestamp.utcnow().isoformat(timespec="seconds") + "Z"
        rows.at[queue_idx, "sent_agent"] = args.agent_name
        rows.at[queue_idx, "sent_to"] = customer_email
        rows.at[queue_idx, "approval_comment"] = str(approval.get("comment", ""))

    if sent_count:
        save_queue(queue_path, rows)
    print(f"Sent {sent_count} approved email(s)")


if __name__ == "__main__":
    main()
