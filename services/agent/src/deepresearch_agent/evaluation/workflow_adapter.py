from __future__ import annotations

import asyncio
from collections.abc import Sequence
from dataclasses import dataclass
from time import perf_counter

from pydantic import BaseModel, ConfigDict

from deepresearch_agent.application.workflow import ResearchWorkflow
from deepresearch_agent.domain.models import Evidence, ResearchResult
from deepresearch_agent.infrastructure.sessions import AdkSessionStateStore


class EvaluationTurn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: str
    target_molecule: str | None = None
    mechanism: str | None = None
    disease: str = "ischemic stroke"
    research_question: str | None = None


@dataclass(frozen=True, slots=True)
class WorkflowCaseObservation:
    case_id: str
    result: ResearchResult
    retrieved_evidence: tuple[Evidence, ...]
    packed_evidence: tuple[Evidence, ...]
    final_state: dict[str, object]
    latency_ms: float


class WorkflowEvaluationAdapter:
    """Runs the real workflow while keeping full Evidence in the offline process only."""

    def __init__(
        self,
        *,
        workflow: ResearchWorkflow,
        sessions: AdkSessionStateStore,
    ) -> None:
        self._workflow = workflow
        self._sessions = sessions

    async def run_case(
        self,
        *,
        case_id: str,
        turns: Sequence[EvaluationTurn],
    ) -> WorkflowCaseObservation:
        if not turns:
            raise ValueError("evaluation case must contain at least one turn")
        user_id = f"synthetic-eval-user-{case_id}"
        conversation_id = f"synthetic-eval-conversation-{case_id}"
        latest_result: ResearchResult | None = None
        latest_retrieved: tuple[Evidence, ...] = ()
        latest_packed: tuple[Evidence, ...] = ()

        def capture(
            retrieved: tuple[Evidence, ...],
            packed: tuple[Evidence, ...],
            result: ResearchResult,
        ) -> None:
            nonlocal latest_result, latest_retrieved, latest_packed
            latest_result = result
            latest_retrieved = retrieved
            latest_packed = packed

        started_at = perf_counter()
        for index, turn in enumerate(turns, start=1):
            completed = False
            async for event in self._workflow.run(
                user_id=user_id,
                conversation_id=conversation_id,
                turn_id=f"{case_id}-turn-{index}",
                question=turn.text,
                target_molecule=turn.target_molecule,
                mechanism=turn.mechanism,
                disease=turn.disease,
                research_question=turn.research_question,
                cancel_event=asyncio.Event(),
                _evaluation_capture=capture,
            ):
                completed = completed or event.kind == "completed"
            if not completed:
                raise RuntimeError("workflow evaluation turn did not complete")
        latency_ms = (perf_counter() - started_at) * 1000
        if latest_result is None:
            raise RuntimeError("workflow evaluation produced no result")
        final_state = await self._sessions.get_state(
            user_id=user_id,
            session_id=conversation_id,
        )
        return WorkflowCaseObservation(
            case_id=case_id,
            result=latest_result,
            retrieved_evidence=latest_retrieved,
            packed_evidence=latest_packed,
            final_state=final_state,
            latency_ms=latency_ms,
        )
