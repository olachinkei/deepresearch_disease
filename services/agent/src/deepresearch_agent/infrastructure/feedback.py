from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal, Protocol

from pydantic import BaseModel, Field


class FeedbackRecord(BaseModel):
    feedback_id: str = Field(min_length=1, max_length=128)
    turn_id: str = Field(min_length=1, max_length=128)
    rating: Literal["up", "down"]
    reason: Literal[
        "irrelevant_sources",
        "unsupported_claim",
        "incomplete",
        "citation_error",
        "too_slow",
        "other",
    ] | None = None
    comment: str | None = Field(default=None, max_length=1024)


@dataclass(frozen=True, slots=True)
class FeedbackSyncResult:
    status: Literal["synced", "pending"]
    feedback_id: str | None = None
    trace_id: str | None = None


class FeedbackBackend(Protocol):
    def has_feedback(self, feedback_id: str) -> bool: ...

    def find_turn_trace_id(self, turn_id: str) -> str | None: ...

    def add_feedback(
        self, trace_id: str, feedback: FeedbackRecord, *, include_comment: bool
    ) -> str: ...


class FeedbackSynchronizer:
    def __init__(
        self,
        backend: FeedbackBackend | None,
        *,
        include_comment: bool = False,
    ) -> None:
        self._backend = backend
        self._include_comment = include_comment

    def sync(self, feedback: FeedbackRecord) -> FeedbackSyncResult:
        if self._backend is None:
            return FeedbackSyncResult(status="pending")
        if self._backend.has_feedback(feedback.feedback_id):
            return FeedbackSyncResult(
                status="synced",
                feedback_id=feedback.feedback_id,
            )
        trace_id = self._backend.find_turn_trace_id(feedback.turn_id)
        if trace_id is None:
            return FeedbackSyncResult(status="pending")
        feedback_id = self._backend.add_feedback(
            trace_id,
            feedback,
            include_comment=self._include_comment,
        )
        return FeedbackSyncResult(
            status="synced",
            feedback_id=feedback_id,
            trace_id=trace_id,
        )


class WeaveFeedbackBackend:
    """Lazy Weave SDK adapter. It never enables Weave runtime tracing/autopatching."""

    def __init__(self, project_path: str) -> None:
        self._project_path = project_path
        self._client: Any | None = None

    def _get_client(self) -> Any:
        if self._client is None:
            import weave

            self._client = weave.init(
                self._project_path,
                settings={
                    "implicitly_patch_integrations": False,
                    "capture_code": False,
                    "capture_system_info": False,
                    "print_call_link": False,
                },
            )
        return self._client

    def find_turn_trace_id(self, turn_id: str) -> str | None:
        from weave.trace_server.agents.types import (
            AgentSortBy,
            AgentSpansQueryReq,
            AgentSpanValueRef,
        )
        from weave.trace_server.interface.query import Query

        client = self._get_client()
        result = client.server.agent_spans_query(
            AgentSpansQueryReq(
                project_id=self._project_path,
                query=Query(
                    **{  # type: ignore[arg-type]
                        "$expr": {
                            "$eq": [
                                {"$getField": "custom_attrs_string.app.turn_id"},
                                {"$literal": turn_id},
                            ]
                        }
                    }
                ),
                custom_attr_columns=[
                    AgentSpanValueRef(
                        source="custom_attrs_string",
                        key="app.turn_id",
                    )
                ],
                include_details=False,
                include_costs=False,
                sort_by=[AgentSortBy(field="started_at", direction="desc")],
                limit=10,
            )
        )
        for span in result.spans:
            if span.operation_name == "invoke_agent":
                return str(span.trace_id)
        return str(result.spans[0].trace_id) if result.spans else None

    def has_feedback(self, feedback_id: str) -> bool:
        from weave.trace_server.interface.query import Query

        client = self._get_client()
        matches = client.get_feedback(
            Query(
                **{  # type: ignore[arg-type]
                    "$expr": {
                        "$eq": [
                            {"$getField": "id"},
                            {"$literal": feedback_id},
                        ]
                    }
                }
            ),
            limit=1,
        )
        return bool(list(matches))

    def add_feedback(
        self, trace_id: str, feedback: FeedbackRecord, *, include_comment: bool
    ) -> str:
        from weave.trace.refs import AgentTurnRef
        from weave.trace_server.trace_server_interface import FeedbackCreateReq

        client = self._get_client()
        reaction = "👍" if feedback.rating == "up" else "👎"
        payload: dict[str, Any] = {
            "feedback_id": feedback.feedback_id,
            "rating": feedback.rating,
            "reaction": reaction,
            "reason": feedback.reason,
        }
        if include_comment and feedback.comment:
            payload["comment"] = feedback.comment[:800]
        response = client.server.feedback_create(
            FeedbackCreateReq(
                id=feedback.feedback_id,
                project_id=self._project_path,
                weave_ref=AgentTurnRef(
                    entity=client.entity,
                    project=client.project,
                    trace_id=trace_id,
                ).uri(),
                feedback_type="wandb.agent_user_feedback",
                scorer_tags=[reaction],
                scorer_tag_reasons={
                    reaction: feedback.reason or "User feedback",
                },
                scorer_tag_confidences={reaction: 1.0},
                payload=payload,
                wb_user_id=None,
            )
        )
        return str(response.id)
