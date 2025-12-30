#!/usr/bin/env python3
"""Email ingestion to Excel-backed queue (demo-friendly).

Two modes:
  1) IMAP polling of an inbox for UNSEEN messages
  2) Local folder watcher for .eml files

Writes rows into the existing Excel queue used by tools/process_queue.py.

Environment (IMAP):
  IMAP_HOST, IMAP_PORT (optional), IMAP_SSL ("1"/"true"),
  IMAP_USERNAME, IMAP_PASSWORD, IMAP_FOLDER (default: INBOX)
"""

from __future__ import annotations

import argparse
import email
import hashlib
import imaplib
import json
import os
import shutil
import time
from email.header import decode_header, make_header
from email.message import Message
from email.utils import parseaddr
from pathlib import Path
from typing import Dict, List, Optional, Tuple, Set

import pandas as pd

import sys
sys.path.append(str(Path(__file__).resolve().parents[1]))

import langid

from app.email_preprocess import clean_email
from app.knowledge import load_knowledge
from app.pipeline import detect_expected_keys
from tools.process_queue import (
    load_queue,
    save_queue,
    QUEUE_COLUMNS,
)


LANG_SUFFIX_MAP = {
    ".fi": "fi",
    ".se": "sv",
    ".sv": "sv",
}
LANG_CONFIDENCE_THRESHOLD = 0.85
MIN_TEXT_LEN_FOR_CONFIDENCE = 20

langid.set_languages(["en", "fi", "sv"])


def _decode(s: Optional[bytes | str]) -> str:
    if s is None:
        return ""
    if isinstance(s, bytes):
        try:
            return s.decode("utf-8", errors="replace")
        except Exception:
            return s.decode(errors="replace")
    return str(s)


def _decode_header(value: Optional[str]) -> str:
    if not value:
        return ""
    try:
        dh = decode_header(value)
        return str(make_header(dh))
    except Exception:
        return value


def _domain_language_hint(sender: str) -> Tuple[Optional[str], Optional[str]]:
    address = parseaddr(sender or "")[1].lower()
    if "@" not in address:
        return None, None
    domain = address.split("@", 1)[1]
    for suffix, lang in LANG_SUFFIX_MAP.items():
        if domain.endswith(suffix):
            return lang, domain
    # also consider top-level domain if domain like "example.fi"
    parts = domain.rsplit(".", 1)
    if len(parts) == 2:
        tld = "." + parts[1]
        if tld in LANG_SUFFIX_MAP:
            return LANG_SUFFIX_MAP[tld], domain
    return None, domain


def _detect_language(body: str, subject: str) -> Tuple[Optional[str], float]:
    text = " ".join(part for part in [subject or "", body or ""] if part)
    text = text.strip()
    if not text:
        return None, 0.0
    lang, confidence = langid.classify(text)
    if len(text) < MIN_TEXT_LEN_FOR_CONFIDENCE:
        return lang, 0.0
    return lang, float(confidence)


def _infer_language(sender: str, subject: str, body: str) -> Tuple[str, str, float, Optional[str], Optional[str]]:
    domain_lang, domain = _domain_language_hint(sender)
    detected_lang, confidence = _detect_language(body, subject)

    final_lang = ""
    source = ""
    effective_conf = confidence if confidence >= 0 else 0.0

    if domain_lang and detected_lang and detected_lang == domain_lang and effective_conf >= LANG_CONFIDENCE_THRESHOLD:
        final_lang = domain_lang
        source = "domain+detector"
    elif domain_lang and (not detected_lang or effective_conf < LANG_CONFIDENCE_THRESHOLD):
        final_lang = domain_lang
        source = "domain"
    elif detected_lang and effective_conf >= LANG_CONFIDENCE_THRESHOLD:
        final_lang = detected_lang
        source = "detector"
    elif domain_lang:
        final_lang = domain_lang
        source = "domain"
    elif detected_lang:
        final_lang = detected_lang
        source = "detector_low"

    return final_lang, source, effective_conf, detected_lang, domain_lang


def _extract_body(msg: Message) -> Tuple[str, bool]:
    if msg.is_multipart():
        # Prefer text/plain
        for part in msg.walk():
            ctype = part.get_content_type()
            disp = (part.get("Content-Disposition") or "").lower()
            if "attachment" in disp:
                continue
            if ctype == "text/plain":
                try:
                    payload = part.get_payload(decode=True)
                except Exception:
                    payload = None
                if payload:
                    charset = part.get_content_charset() or "utf-8"
                    try:
                        return payload.decode(charset, errors="replace"), False
                    except Exception:
                        return payload.decode(errors="replace"), False
        # Fallback: first non-attachment part
        for part in msg.walk():
            disp = (part.get("Content-Disposition") or "").lower()
            if "attachment" in disp:
                continue
            try:
                payload = part.get_payload(decode=True)
            except Exception:
                payload = None
            if payload:
                charset = part.get_content_charset() or "utf-8"
                try:
                    return payload.decode(charset, errors="replace"), part.get_content_type() == "text/html"
                except Exception:
                    return payload.decode(errors="replace"), part.get_content_type() == "text/html"
        return "", False
    # Single part
    try:
        payload = msg.get_payload(decode=True)
    except Exception:
        payload = None
    if payload:
        charset = msg.get_content_charset() or "utf-8"
        try:
            return payload.decode(charset, errors="replace"), msg.get_content_type() == "text/html"
        except Exception:
            return payload.decode(errors="replace"), msg.get_content_type() == "text/html"
    return _decode(msg.get_payload()), False


def _ensure_queue(queue_path: Path) -> None:
    if not queue_path.exists():
        # Create an empty frame with the required columns
        empty = pd.DataFrame([], columns=QUEUE_COLUMNS)
        save_queue(queue_path, empty)


def _append_rows(queue_path: Path, rows: List[Dict[str, object]]) -> int:
    if not rows:
        return 0
    df = load_queue(queue_path)
    # Assign IDs if missing
    next_id = 1
    if "id" in df.columns and not df.empty:
        try:
            next_id = int(pd.to_numeric(df["id"], errors="coerce").max()) + 1
        except Exception:
            next_id = len(df) + 1
    for r in rows:
        if r.get("id") in (None, ""):
            r["id"] = next_id
            next_id += 1
    incoming = pd.DataFrame(rows, columns=QUEUE_COLUMNS)
    combined = pd.concat([df, incoming], ignore_index=True)
    save_queue(queue_path, combined)
    return len(rows)


def ingest_eml_folder(
    folder: Path,
    queue_path: Path,
    *,
    clean: bool,
    retain_raw: bool,
    detect_keys: bool,
    knowledge: Optional[Dict[str, str]],
    known_signatures: Set[str],
    archive_folder: Optional[Path],
    delete_after: bool,
) -> Tuple[int, List[str]]:
    rows: List[Dict[str, object]] = []
    details: List[str] = []
    archive_path: Optional[Path] = None
    if archive_folder:
        archive_path = archive_folder
        archive_path.mkdir(parents=True, exist_ok=True)

    for eml in sorted(folder.glob("*.eml")):
        try:
            raw = eml.read_bytes()
            msg = email.message_from_bytes(raw)
        except Exception:
            continue
        subject = _decode_header(msg.get("Subject"))
        sender = _decode_header(msg.get("From"))
        body, is_html = _extract_body(msg)
        raw_body = body
        if clean:
            body = clean_email(body, is_html=is_html)

        language, language_source, lang_conf, _detected_lang, _domain_lang = _infer_language(sender, subject, body)

        signature_source = (subject or "") + "\n" + (raw_body or "")
        signature = hashlib.sha256(signature_source.encode("utf-8", errors="ignore")).hexdigest()

        if signature in known_signatures:
            details.append(
                (
                    f"Skipped duplicate '{subject or '(no subject)'}' from {sender or 'unknown'} "
                    f"(signature match)"
                )
            )
            if archive_path:
                shutil.move(str(eml), str(archive_path / eml.name))
            elif delete_after:
                try:
                    eml.unlink()
                except Exception:
                    pass
            continue

        if detect_keys:
            detected = detect_expected_keys(body, knowledge=knowledge)
        else:
            detected = []

        rows.append(
            {
                "id": None,
                "customer": sender,
                "subject": subject,
                "body": body,
                "raw_body": raw_body if retain_raw else body,
                "language": language,
                "language_source": language_source,
                "language_confidence": lang_conf if lang_conf else None,
                "expected_keys": json.dumps(detected, ensure_ascii=False),
                "ingest_signature": signature,
                "status": "queued",
                "agent": "",
                "started_at": "",
                "finished_at": "",
                "latency_seconds": None,
                "score": None,
                "matched": "",
                "missing": "",
                "reply": "",
                "answers": "",
            }
        )
        known_signatures.add(signature)
        details.append(
            (
                f"Queued '{subject or '(no subject)'}' from {sender or 'unknown'} "
                f"(lang: {language or 'unknown'}, keys: {', '.join(detected) if detected else 'none'})"
            )
        )

        if archive_path:
            shutil.move(str(eml), str(archive_path / eml.name))
        elif delete_after:
            try:
                eml.unlink()
            except Exception:
                pass

    return _append_rows(queue_path, rows), details


def ingest_imap(
    queue_path: Path,
    *,
    clean: bool,
    retain_raw: bool,
    detect_keys: bool,
    knowledge: Optional[Dict[str, str]],
    known_signatures: Set[str],
) -> Tuple[int, List[str]]:
    host = os.environ.get("IMAP_HOST")
    if not host:
        raise SystemExit("Set IMAP_HOST, IMAP_USERNAME, IMAP_PASSWORD in environment.")
    user = os.environ.get("IMAP_USERNAME")
    password = os.environ.get("IMAP_PASSWORD")
    folder = os.environ.get("IMAP_FOLDER", "INBOX")
    port = int(os.environ.get("IMAP_PORT") or 0) or None
    use_ssl = str(os.environ.get("IMAP_SSL", "1")).lower() in {"1", "true", "yes"}

    if use_ssl:
        conn = imaplib.IMAP4_SSL(host, port=port) if port else imaplib.IMAP4_SSL(host)
    else:
        conn = imaplib.IMAP4(host, port=port) if port else imaplib.IMAP4(host)

    try:
        conn.login(user, password)
        typ, _ = conn.select(folder)
        if typ != "OK":
            raise SystemExit(f"Unable to select folder {folder}")
        typ, data = conn.search(None, "UNSEEN")
        if typ != "OK":
            return 0
        ids = _decode(data[0]).split()
        rows: List[Dict[str, object]] = []
        details: List[str] = []
        for mid in ids:
            typ, resp = conn.fetch(mid, "(RFC822)")
            if typ != "OK" or not resp or not isinstance(resp[0], tuple):
                continue
            raw = resp[0][1]
            try:
                msg = email.message_from_bytes(raw)
            except Exception:
                continue
            subject = _decode_header(msg.get("Subject"))
            sender = _decode_header(msg.get("From"))
            body, is_html = _extract_body(msg)
            raw_body = body
            if clean:
                body = clean_email(body, is_html=is_html)
            language, language_source, lang_conf, _detected_lang, _domain_lang = _infer_language(sender, subject, body)
            if detect_keys:
                detected = detect_expected_keys(body, knowledge=knowledge)
            else:
                detected = []

            signature_source = (subject or "") + "\n" + (raw_body or "")
            signature = hashlib.sha256(signature_source.encode("utf-8", errors="ignore")).hexdigest()
            if signature in known_signatures:
                details.append(
                    (
                        f"Skipped duplicate '{subject or '(no subject)'}' from {sender or 'unknown'} "
                        f"(signature match)"
                    )
                )
                try:
                    conn.store(mid, "+FLAGS", "(\\Seen)")
                except Exception:
                    pass
                continue

            rows.append(
                {
                    "id": None,
                    "customer": sender,
                    "subject": subject,
                    "body": body,
                    "raw_body": raw_body if retain_raw else body,
                    "language": language,
                    "language_source": language_source,
                    "language_confidence": lang_conf if lang_conf else None,
                    "ingest_signature": signature,
                    "expected_keys": json.dumps(detected, ensure_ascii=False),
                    "status": "queued",
                    "agent": "",
                    "started_at": "",
                    "finished_at": "",
                    "latency_seconds": None,
                    "score": None,
                    "matched": "",
                    "missing": "",
                    "reply": "",
                    "answers": "",
                }
            )
            known_signatures.add(signature)
            try:
                conn.store(mid, "+FLAGS", "(\\Seen)")
            except Exception:
                pass
            details.append(
                (
                    f"Queued '{subject or '(no subject)'}' from {sender or 'unknown'} "
                    f"(lang: {language or 'unknown'}, keys: {', '.join(detected) if detected else 'none'})"
                )
            )
        return _append_rows(queue_path, rows), details
    finally:
        try:
            conn.logout()
        except Exception:
            pass


def main() -> None:
    ap = argparse.ArgumentParser(description="Ingest emails into the Excel-backed queue")
    ap.add_argument("--queue", default="data/email_queue.xlsx", help="Queue workbook path")
    mode = ap.add_mutually_exclusive_group(required=True)
    mode.add_argument("--imap", action="store_true", help="Poll an IMAP inbox for UNSEEN messages")
    mode.add_argument("--folder", help="Read .eml files from this folder")
    ap.add_argument("--watch", action="store_true", help="Keep polling (for IMAP) or re-scan folder periodically")
    ap.add_argument("--poll-interval", type=float, default=15.0, help="Seconds between polls when --watch is set")
    ap.add_argument("--no-clean", action="store_true", help="Do not clean email bodies before queueing")
    ap.add_argument("--retain-raw", action="store_true", help="Store raw bodies alongside cleaned text")
    ap.add_argument("--no-detect", action="store_true", help="Do not pre-fill expected_keys during ingestion")
    ap.add_argument("--archive-folder", help="When reading from a folder, move processed .eml files here")
    ap.add_argument("--delete-processed", action="store_true", help="Delete processed .eml files (folder mode)")
    ap.add_argument("--verbose", action="store_true", help="Print details for each ingested email")
    args = ap.parse_args()

    queue_path = Path(args.queue)
    _ensure_queue(queue_path)

    archive_folder = Path(args.archive_folder) if args.archive_folder else None
    delete_after = bool(args.delete_processed)
    if archive_folder and delete_after:
        print("Archive folder specified; ignoring --delete-processed")
        delete_after = False

    def run_once() -> Tuple[int, List[str]]:
        knowledge: Optional[Dict[str, str]] = None
        detect_keys = not args.no_detect
        if detect_keys:
            knowledge = load_knowledge()
        existing_df = load_queue(queue_path)
        existing_sigs = set(
            str(sig)
            for sig in existing_df.get("ingest_signature", [])
            if isinstance(sig, str) and sig
        )
        if args.imap:
            return ingest_imap(
                queue_path,
                clean=not args.no_clean,
                retain_raw=args.retain_raw or not args.no_clean,
                detect_keys=detect_keys,
                knowledge=knowledge,
                known_signatures=existing_sigs,
            )
        else:
            return ingest_eml_folder(
                Path(args.folder),
                queue_path,
                clean=not args.no_clean,
                retain_raw=args.retain_raw or not args.no_clean,
                detect_keys=detect_keys,
                knowledge=knowledge,
                known_signatures=existing_sigs,
                archive_folder=archive_folder,
                delete_after=delete_after,
            )

    count, details = run_once()
    if args.verbose and details:
        for line in details:
            print(line)
    if count:
        print(f"Enqueued {count} email(s) -> {queue_path}")
    else:
        print("No new emails found.")

    if not args.watch:
        return

    while True:
        time.sleep(max(args.poll_interval, 1.0))
        try:
            count, details = run_once()
            if args.verbose and details:
                for line in details:
                    print(line)
            if count:
                print(f"Enqueued {count} email(s) -> {queue_path}")
        except KeyboardInterrupt:
            break


if __name__ == "__main__":
    main()
