"""Chat service scaffolding for the queue-driven chatbot migration."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Dict, Iterable, List, Literal, Optional
from uuid import uuid4

from .knowledge import load_knowledge
from .pipeline import run_pipeline


Role = Literal["user", "assistant", "system"]
Source = Literal["knowledge", "pipeline", "fallback"]
Decision = Literal["answer", "clarify", "handoff"]


@dataclass
class ChatMessage:
    """Represents a single conversational turn."""

    role: Role
    content: str
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    metadata: Dict[str, str] = field(default_factory=dict)


@dataclass
class ChatTurnResult:
    """Response payload returned by the chat service."""

    response: ChatMessage
    matched_fact: Optional[str]
    source: Source
    decision: Decision
    evaluation: Dict[str, object] = field(default_factory=dict)


class ChatService:
    """High-level orchestrator that adapts the email cleaner into a chat assistant."""

    _FACT_PATTERNS: Dict[str, Iterable[str]] = {
        "company_name": ("company", "aurora gadgets"),
        "founded_year": ("founded", "when did", "established"),
        "headquarters": ("headquarters", "located", "where are you"),
        "support_hours": ("support hours", "opening hours", "when are you open"),
        "warranty_policy": ("warranty", "guarantee"),
        "return_policy": ("return", "refund"),
        "shipping_time": ("shipping", "delivery"),
        "loyalty_program": ("loyalty", "rewards"),
        "support_email": ("contact", "email", "reach support"),
        "premium_support": ("premium support", "enterprise", "sla"),
        "key_code_AG-445": ("ag-445", "ag445"),
    }
    _HANDOFF_KEYWORDS = ("human", "agent", "representative", "supervisor", "manager")
    _CLARIFY_TRIGGERS = ("hi", "hello", "hey", "thanks", "thank you", "good morning", "good evening")

    def __init__(self, *, knowledge: Optional[Dict[str, str]] = None) -> None:
        self._knowledge = knowledge or load_knowledge()

    def respond(
        self,
        conversation: List[ChatMessage],
        user_message: ChatMessage,
        *,
        conversation_id: Optional[str] = None,
        channel: str = "web_chat",
    ) -> ChatTurnResult:
        """Return a chatbot reply using heuristics and the existing pipeline."""

        lowered = user_message.content.lower().strip()

        if self._needs_handoff(lowered):
            response = ChatMessage(
                role="assistant",
                content="I'll bring in a human teammate to continue this conversation right away.",
                metadata={
                    "conversation_id": conversation_id or "",
                    "channel": channel,
                    "source": "fallback",
                },
            )
            return ChatTurnResult(
                response=response,
                matched_fact=None,
                source="fallback",
                decision="handoff",
            )

        matched_fact = self._match_fact(user_message.content)
        if matched_fact and matched_fact in self._knowledge:
            content = self._format_fact_reply(matched_fact)
            response = ChatMessage(
                role="assistant",
                content=content,
                metadata={
                    "conversation_id": conversation_id or "",
                    "channel": channel,
                    "source": "knowledge",
                    "matched_fact": matched_fact,
                },
            )
            return ChatTurnResult(
                response=response,
                matched_fact=matched_fact,
                source="knowledge",
                decision="answer",
            )

        if self._needs_clarification(lowered, conversation):
            response = ChatMessage(
                role="assistant",
                content="Happy to help! Could you share a bit more detail about what you need?",
                metadata={
                    "conversation_id": conversation_id or "",
                    "channel": channel,
                    "source": "knowledge",
                    "clarify": "prompt",
                },
            )
            return ChatTurnResult(
                response=response,
                matched_fact=None,
                source="knowledge",
                decision="clarify",
            )

        expected_keys = [matched_fact] if matched_fact else None
        context_snapshot = self._serialise_history(conversation)
        metadata: Dict[str, object] = {}
        if expected_keys:
            metadata["expected_keys"] = expected_keys
        if conversation_id:
            metadata["conversation_id"] = conversation_id
        if channel:
            metadata["channel"] = channel
        if context_snapshot:
            metadata["conversation_context"] = context_snapshot

        try:
            result = run_pipeline(user_message.content, metadata=metadata or None)
            reply_text = result.get("reply") or result.get("response") or ""
            evaluation = result.get("evaluation") or {}
            if result.get("human_review"):
                decision: Decision = "handoff"
            elif not reply_text.strip():
                decision = "clarify"
            else:
                decision = "answer"
            response = ChatMessage(
                role="assistant",
                content=reply_text or "I am still composing a response based on our policies.",
                metadata={
                    "conversation_id": conversation_id or "",
                    "channel": channel,
                    "source": "pipeline",
                },
            )
            if decision == "clarify":
                response.content = "I want to make sure I give the right info. Could you clarify your request a little?"
            if decision == "handoff":
                response.content = "I'll bring in a human teammate to continue this conversation."
            return ChatTurnResult(
                response=response,
                matched_fact=matched_fact,
                source="pipeline",
                decision=decision,
                evaluation=evaluation,
            )
        except Exception as exc:  # pragma: no cover - defensive guard
            fallback = ChatMessage(
                role="assistant",
                content=(
                    "I am unable to reach the response pipeline right now, but a human agent "
                    "will follow up momentarily."
                ),
                metadata={
                    "conversation_id": conversation_id or "",
                    "channel": channel,
                    "source": "fallback",
                    "error": type(exc).__name__,
                },
            )
            return ChatTurnResult(
                response=fallback,
                matched_fact=matched_fact,
                source="fallback",
                decision="handoff",
            )

    def build_queue_record(
        self,
        user_message: ChatMessage,
        turn_result: ChatTurnResult,
        *,
        conversation_id: str,
        end_user_handle: str,
        channel: str,
    ) -> Dict[str, object]:
        """Map a processed turn to the upcoming chat queue schema."""

        finished_at = datetime.now(timezone.utc)
        if turn_result.decision == "handoff":
            status = "handoff"
            delivery_status = "blocked"
        else:
            status = "responded"
            delivery_status = "pending"
        response_payload = {
            "type": "text",
            "content": turn_result.response.content,
            "decision": turn_result.decision,
        }
        if turn_result.matched_fact:
            response_payload["matched_fact"] = turn_result.matched_fact

        evaluation = turn_result.evaluation.copy()
        if evaluation and "decision" not in evaluation:
            evaluation["decision"] = turn_result.decision

        return {
            "message_id": str(uuid4()),
            "conversation_id": conversation_id,
            "end_user_handle": end_user_handle,
            "channel": channel,
            "message_direction": "inbound",
            "message_type": "text",
            "payload": user_message.content,
            "raw_payload": user_message.metadata.get("raw", ""),
            "language": user_message.metadata.get("language", ""),
            "language_source": user_message.metadata.get("language_source", ""),
            "language_confidence": user_message.metadata.get("language_confidence"),
            "conversation_tags": user_message.metadata.get("conversation_tags", ""),
            "status": status,
            "processor_id": user_message.metadata.get("processor_id", "chat-service"),
            "started_at": user_message.timestamp.isoformat(),
            "finished_at": finished_at.isoformat(),
            "latency_seconds": (finished_at - user_message.timestamp).total_seconds(),
            "quality_score": evaluation.get("score") if evaluation else None,
            "matched": evaluation.get("matched") if evaluation else None,
            "missing": evaluation.get("missing") if evaluation else None,
            "response_payload": response_payload,
            "response_metadata": evaluation,
            "delivery_route": turn_result.response.metadata.get("delivery_route", ""),
            "delivery_status": delivery_status,
            "ingest_signature": user_message.metadata.get("ingest_signature", ""),
        }

    def _needs_handoff(self, lowered_text: str) -> bool:
        if not lowered_text:
            return False
        return any(keyword in lowered_text for keyword in self._HANDOFF_KEYWORDS)

    def _needs_clarification(self, lowered_text: str, conversation: List[ChatMessage]) -> bool:
        if not lowered_text:
            return True
        if any(lowered_text == trigger for trigger in self._CLARIFY_TRIGGERS):
            return True
        tokens = lowered_text.split()
        if len(tokens) <= 3 and not lowered_text.endswith("?"):
            return True
        if lowered_text in {"thanks", "thank you", "ok", "okay"}:
            return True
        return False

    def _match_fact(self, text: str) -> Optional[str]:
        lowered = text.lower()
        for fact_key, patterns in self._FACT_PATTERNS.items():
            if any(pattern in lowered for pattern in patterns):
                return fact_key
        return None

    def _format_fact_reply(self, fact_key: str) -> str:
        value = self._knowledge.get(fact_key)
        if not value:
            return "I could not find the requested information right now."
        if fact_key == "company_name":
            return f"We are {value}, and we are happy to help."
        if fact_key == "founded_year":
            return f"Aurora Gadgets was founded in {value}."
        if fact_key == "headquarters":
            return f"Our headquarters is located in {value}."
        if fact_key == "support_hours":
            return f"Our support hours are {value}."
        if fact_key == "support_email":
            return f"You can reach us via email at {value}."
        return value

    def _serialise_history(self, conversation: List[ChatMessage], *, limit: int = 6) -> List[Dict[str, str]]:
        if not conversation:
            return []
        tail = conversation[-limit:]
        serialised: List[Dict[str, str]] = []
        for message in tail:
            serialised.append(
                {
                    "role": message.role,
                    "content": message.content,
                    "timestamp": message.timestamp.isoformat(),
                }
            )
        return serialised


__all__ = ["ChatMessage", "ChatTurnResult", "ChatService"]

