"""Demo connector that yields sample intakes from .txt/.eml files."""

from __future__ import annotations

import email
from datetime import datetime, timezone
from email.parser import BytesParser
from pathlib import Path
from typing import Iterable, List

from . import InboundItem, SourceConnector


class DemoConnector(SourceConnector):
    """Loads demo messages from files for easy replay into the queue."""

    def __init__(self, paths: Iterable[Path]) -> None:
        self.paths = [p for p in paths if p.exists()]

    def pull(self) -> Iterable[InboundItem]:
        for path in sorted(self.paths):
            if path.is_dir():
                yield from DemoConnector(path.glob("**/*")).pull()
                continue
            suffix = path.suffix.lower()
            if suffix == ".eml":
                yield from self._from_eml(path)
            elif suffix == ".txt":
                text = path.read_text(encoding="utf-8", errors="replace").strip()
                if text:
                    yield InboundItem(text=text, source_meta={"source": str(path)})

    def _from_eml(self, path: Path) -> Iterable[InboundItem]:
        try:
            msg = BytesParser().parsebytes(path.read_bytes())
            body = self._extract_body(msg).strip()
            received = msg.get("Date")
            received_at = None
            if received:
                try:
                    received_at = datetime.strptime(received, "%a, %d %b %Y %H:%M:%S %z").astimezone(timezone.utc)
                except Exception:
                    received_at = None
            yield InboundItem(text=body, received_at=received_at, source_meta={"source": str(path), "subject": msg.get("Subject", "")})
        except Exception:
            return

    def _extract_body(self, msg: email.message.Message) -> str:
        if msg.is_multipart():
            for part in msg.walk():
                disp = (part.get("Content-Disposition") or "").lower()
                if "attachment" in disp:
                    continue
                if part.get_content_type() == "text/plain":
                    try:
                        payload = part.get_payload(decode=True) or b""
                        return payload.decode(part.get_content_charset() or "utf-8", errors="replace")
                    except Exception:
                        continue
        payload = msg.get_payload(decode=True) or b""
        try:
            return payload.decode(msg.get_content_charset() or "utf-8", errors="replace")
        except Exception:
            return payload.decode(errors="replace")


def demo_paths(default_dirs: List[Path] | None = None) -> List[Path]:
    """Return default demo paths (scenarios and data/demo)."""
    defaults = default_dirs or [
        Path("tests") / "scenarios",
        Path("tests") / "scenarios_v2",
        Path("data") / "demo",
    ]
    return [p for p in defaults if p.exists()]


__all__ = ["DemoConnector", "demo_paths"]
