from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class MessagePart(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: str = Field(min_length=1, max_length=150_000)


class AdkContent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    role: Literal["user", "model"]
    parts: list[MessagePart] = Field(min_length=1)


class RunCustomMetadata(BaseModel):
    model_config = ConfigDict(extra="forbid")

    turn_id: str = Field(min_length=1, max_length=128)
    conversation_id: str = Field(min_length=1, max_length=128)
    target_molecule: str | None = None
    mechanism: str | None = None
    disease: str = "ischemic stroke"
    research_question: str | None = None


class RunAgentRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    app_name: str = "deepresearch_agent"
    user_id: str = Field(min_length=1, max_length=256)
    session_id: str = Field(min_length=1, max_length=128)
    new_message: AdkContent
    streaming: bool = True
    state_delta: dict[str, Any] | None = None
    custom_metadata: RunCustomMetadata

    @field_validator("app_name")
    @classmethod
    def validate_app_name(cls, value: str) -> str:
        if value != "deepresearch_agent":
            raise ValueError("app_name must be deepresearch_agent")
        return value

    @field_validator("new_message")
    @classmethod
    def validate_user_message(cls, value: AdkContent) -> AdkContent:
        if value.role != "user":
            raise ValueError("new_message.role must be user")
        return value

    @model_validator(mode="after")
    def validate_input_length(self) -> RunAgentRequest:
        if len(self.question) > 10_000:
            raise ValueError("new_message text must contain at most 10000 characters")
        return self

    @property
    def question(self) -> str:
        return "\n".join(part.text for part in self.new_message.parts)


class AdkEvent(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    id: str
    author: str = "deepresearch_agent"
    content: AdkContent | None = None
    partial: bool = False
    turn_complete: bool = Field(default=False, serialization_alias="turnComplete")
    custom_metadata: dict[str, Any] = Field(
        default_factory=dict, serialization_alias="customMetadata"
    )


class HealthResponse(BaseModel):
    status: Literal["ok"] = "ok"
    runtime_mode: str
    corpus_documents: int
    tracing_export_enabled: bool


class FeedbackSyncResponse(BaseModel):
    status: Literal["synced", "pending"]
    feedback_id: str | None = None
    trace_id: str | None = None
