from typing import Dict, List, Optional

from pydantic import BaseModel, Field, root_validator


class EmailRequest(BaseModel):
    email: str = Field(..., max_length=10000)
    expected_keys: Optional[List[str]] = None
    customer_email: Optional[str] = None
    subject: Optional[str] = Field(None, max_length=512)


class EvaluationResult(BaseModel):
    score: float
    matched: List[str]
    missing: List[str]


class EmailResponse(BaseModel):
    reply: str
    expected_keys: List[str]
    answers: Dict[str, str]
    evaluation: EvaluationResult


class ChatEnqueueRequest(BaseModel):
    text: str = Field(..., min_length=1, max_length=4000, alias="text")
    conversation_id: Optional[str] = Field(None, max_length=120)
    end_user_handle: Optional[str] = Field("api-user", max_length=120)
    channel: str = Field("web_chat", max_length=60)
    message_id: Optional[str] = Field(None, max_length=120)
    raw_payload: Optional[str] = Field(None, max_length=4000)

    @root_validator(pre=True)
    def _coalesce_text(cls, values: Dict[str, object]) -> Dict[str, object]:
        if "text" not in values and "message" in values:
            values["text"] = values["message"]
        return values

    class Config:
        allow_population_by_field_name = True
        anystr_strip_whitespace = True
        extra = "forbid"


