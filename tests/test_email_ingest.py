from email.message import EmailMessage
from pathlib import Path

from tools import email_ingest
from tools.process_queue import load_queue


def _write_eml(path: Path, *, subject: str, body: str) -> None:
    msg = EmailMessage()
    msg["From"] = "Alice <alice@example.com>"
    msg["To"] = "Support <support@example.com>"
    msg["Subject"] = subject
    msg.set_content(body)
    path.write_bytes(msg.as_bytes())


def test_ingest_populates_expected_keys(tmp_path, monkeypatch):
    inbox = tmp_path / "inbox"
    inbox.mkdir()
    _write_eml(inbox / "email.eml", subject="Shipping question", body="How long does shipping take?")

    # Provide knowledge so detect_expected_keys picks up shipping_time
    queue_path = tmp_path / "queue.xlsx"
    email_ingest._ensure_queue(queue_path)  # type: ignore[attr-defined]

    knowledge = {"shipping_time": "Ships in 2 days"}
    count, details = email_ingest.ingest_eml_folder(
        inbox,
        queue_path,
        clean=True,
        retain_raw=True,
        detect_keys=True,
        knowledge=knowledge,
        known_signatures=set(),
        archive_folder=None,
        delete_after=False,
    )
    assert count == 1
    assert details and "shipping" in details[0].lower()

    df = load_queue(queue_path)
    row = df.iloc[0]
    assert "shipping_time" in row["expected_keys"]
    assert "shipping" in row["body"].lower()
    assert row["raw_body"] != ""
    assert row["language"] == "en"
    assert row["language_source"] in {"detector", "detector_low"}


def test_ingest_skips_duplicates(tmp_path, monkeypatch):
    inbox = tmp_path / "inbox"
    inbox.mkdir()
    _write_eml(inbox / "email1.eml", subject="Support", body="How long is shipping?")

    queue_path = tmp_path / "queue.xlsx"
    email_ingest._ensure_queue(queue_path)  # type: ignore[attr-defined]
    knowledge = {"shipping_time": "Ships in 2 days"}

    known = set()
    count, _ = email_ingest.ingest_eml_folder(
        inbox,
        queue_path,
        clean=True,
        retain_raw=False,
        detect_keys=True,
        knowledge=knowledge,
        known_signatures=known,
        archive_folder=None,
        delete_after=False,
    )
    assert count == 1

    # Re-run with same email file (still present)
    count2, details2 = email_ingest.ingest_eml_folder(
        inbox,
        queue_path,
        clean=True,
        retain_raw=False,
        detect_keys=True,
        knowledge=knowledge,
        known_signatures=known,
        archive_folder=None,
        delete_after=False,
    )
    assert count2 == 0
    assert any("skipped duplicate" in d.lower() for d in details2)
