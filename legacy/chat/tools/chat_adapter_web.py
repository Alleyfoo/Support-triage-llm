"""Web demo adapter that records dispatched messages for the chat prototype."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict

import pandas as pd


class WebDemoAdapter:
    """Append chat responses to a JSONL transcript for the static demo."""

    def __init__(self, log_path: Path | str | None = None) -> None:
        self.log_path = Path(log_path or "data/chat_web_transcript.jsonl")
        self.log_path.parent.mkdir(parents=True, exist_ok=True)

    def deliver(self, row: pd.Series) -> None:
        entry = self._build_entry(row)
        with self.log_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(entry, ensure_ascii=False) + "\n")

    def _build_entry(self, row: pd.Series) -> Dict[str, Any]:
        response_payload = row.get("response_payload")
        payload_obj: Dict[str, Any] | None = None
        if isinstance(response_payload, str) and response_payload.strip():
            try:
                payload_obj = json.loads(response_payload)
            except json.JSONDecodeError:
                payload_obj = {"type": "text", "content": response_payload}
        elif isinstance(response_payload, dict):
            payload_obj = response_payload

        conversation_id = str(row.get("conversation_id") or "")
        message_id = str(row.get("message_id") or "")
        event = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "conversation_id": conversation_id,
            "message_id": message_id,
            "channel": str(row.get("channel") or "web_chat"),
            "delivery_route": row.get("delivery_route") or "web-demo",
            "response": payload_obj or {"type": "text", "content": ""},
        }
        if row.get("end_user_handle"):
            event["end_user_handle"] = str(row.get("end_user_handle"))
        return event


__all__ = ["WebDemoAdapter"]
