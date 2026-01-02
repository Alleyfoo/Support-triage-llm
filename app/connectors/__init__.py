"""Connector interfaces for ingesting intakes into the queue."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Protocol


@dataclass
class InboundItem:
    text: str
    tenant: str | None = None
    received_at: datetime | None = None
    attachments: List[Path] = field(default_factory=list)
    source_meta: Dict[str, Any] = field(default_factory=dict)


class SourceConnector(Protocol):
    """Minimal interface for pluggable intake sources."""

    def pull(self) -> Iterable[InboundItem]:
        ...


__all__ = ["InboundItem", "SourceConnector"]
