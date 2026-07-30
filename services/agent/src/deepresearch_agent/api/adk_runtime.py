from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncGenerator
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Literal

from google.adk.agents import BaseAgent
from google.adk.agents.invocation_context import InvocationContext
from google.adk.apps import App
from google.adk.cli.utils.base_agent_loader import BaseAgentLoader
from google.adk.events import Event, EventActions
from google.genai import types

from deepresearch_agent import __version__
from deepresearch_agent.api.schemas import RunCustomMetadata
from deepresearch_agent.application.workflow import ResearchWorkflow
from deepresearch_agent.domain.models import RunManifest, WorkflowEvent
from deepresearch_agent.infrastructure.sessions import merge_research_state
from deepresearch_agent.model_contract import (
    GENERATION_MODEL_ID,
    SYNTHESIS_PROMPT_SHA256,
    SYNTHESIS_PROMPT_VERSION,
)
from deepresearch_agent.observability.otel import set_safe_span_attributes

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class ActiveRun:
    cancel_event: asyncio.Event
    task: asyncio.Task[Any]
    cancel_requested: bool = False
    terminal: bool = False


class AdkRunRegistry:
    def __init__(self) -> None:
        self._runs: dict[str, ActiveRun] = {}
        self._lock = asyncio.Lock()

    async def begin(self, turn_id: str) -> asyncio.Event:
        task = asyncio.current_task()
        if task is None:
            raise RuntimeError("run registry requires an active asyncio task")
        async with self._lock:
            if turn_id in self._runs:
                raise ValueError("turn_id is already running")
            event = asyncio.Event()
            self._runs[turn_id] = ActiveRun(cancel_event=event, task=task)
            return event

    async def cancel(self, turn_id: str) -> bool:
        async with self._lock:
            active = self._runs.get(turn_id)
            if active is None or active.terminal:
                return False
            if active.cancel_requested:
                return True
            active.cancel_requested = True
            active.cancel_event.set()
            task = active.task
        if task is not asyncio.current_task() and not task.done():
            task.cancel()
        return True

    async def mark_terminal(self, turn_id: str) -> bool:
        async with self._lock:
            active = self._runs.get(turn_id)
            if active is None or active.terminal:
                return False
            active.terminal = True
            return True

    async def finish(self, turn_id: str) -> None:
        async with self._lock:
            self._runs.pop(turn_id, None)


class DeepResearchAdkAgent(BaseAgent):
    workflow: ResearchWorkflow
    registry: AdkRunRegistry
    deadline_seconds: float = 180.0
    runtime_mode: str = "mock"
    model_id: str = GENERATION_MODEL_ID
    prompt_version: str = SYNTHESIS_PROMPT_VERSION
    prompt_sha256: str = SYNTHESIS_PROMPT_SHA256
    corpus_version: str = "unknown"

    async def _run_async_impl(
        self, ctx: InvocationContext
    ) -> AsyncGenerator[Event]:
        raw_metadata = ctx.run_config.custom_metadata if ctx.run_config else None
        metadata = RunCustomMetadata.model_validate(raw_metadata or {})
        cancel_event = await self.registry.begin(metadata.turn_id)
        first_event = True
        terminal_emitted = False
        started_at = datetime.now(UTC)
        try:
            async with asyncio.timeout(self.deadline_seconds):
                question = _content_text(ctx.user_content)
                state, state_delta = merge_research_state(
                    ctx.session.state,
                    {
                        "target_molecule": metadata.target_molecule,
                        "mechanism": metadata.mechanism,
                        "disease": metadata.disease,
                        "last_research_question": metadata.research_question or question,
                        "last_turn_id": metadata.turn_id,
                        "recent_question": question,
                    },
                )
                async for workflow_event in self.workflow.run(
                    user_id=ctx.session.user_id,
                    conversation_id=metadata.conversation_id,
                    turn_id=metadata.turn_id,
                    question=question,
                    target_molecule=metadata.target_molecule,
                    mechanism=metadata.mechanism,
                    disease=metadata.disease,
                    research_question=metadata.research_question,
                    cancel_event=cancel_event,
                    session_state=state,
                ):
                    event = _to_adk_event(workflow_event, author=self.name)
                    if event.turn_complete:
                        terminal_emitted = await self.registry.mark_terminal(
                            metadata.turn_id
                        )
                        if not terminal_emitted:
                            break
                    if first_event:
                        event.actions = EventActions(state_delta=state_delta)
                        first_event = False
                    yield event
        except asyncio.CancelledError:
            if not terminal_emitted and await self.registry.mark_terminal(
                metadata.turn_id
            ):
                terminal_emitted = True
                yield self._terminal_event(
                    metadata=metadata,
                    kind="cancelled",
                    message="調査を中止しました。",
                    finish_reason="cancelled",
                    flags=["cancelled"],
                    started_at=started_at,
                )
        except TimeoutError:
            cancel_event.set()
            if not terminal_emitted and await self.registry.mark_terminal(
                metadata.turn_id
            ):
                terminal_emitted = True
                yield self._terminal_event(
                    metadata=metadata,
                    kind="error",
                    message="調査が実行時間の上限を超えました。",
                    finish_reason="timeout",
                    flags=["timeout"],
                    error_code="turn_deadline_exceeded",
                    started_at=started_at,
                )
        except Exception as exc:
            logger.warning(
                "Agent execution failed turn_id=%s error_type=%s",
                metadata.turn_id,
                type(exc).__name__,
            )
            if not terminal_emitted and await self.registry.mark_terminal(
                metadata.turn_id
            ):
                terminal_emitted = True
                yield self._terminal_event(
                    metadata=metadata,
                    kind="error",
                    message=(
                        "調査を完了できませんでした。"
                        "設定または外部サービスを確認してください。"
                    ),
                    finish_reason="error",
                    flags=["agent_execution_failed"],
                    error_code="agent_execution_failed",
                    started_at=started_at,
                )
        finally:
            await self.registry.finish(metadata.turn_id)

    def _terminal_event(
        self,
        *,
        metadata: RunCustomMetadata,
        kind: str,
        message: str,
        finish_reason: Literal["cancelled", "timeout", "error"],
        flags: list[str],
        started_at: datetime,
        error_code: str | None = None,
    ) -> Event:
        set_safe_span_attributes(
            {
                "app.finish_reason": finish_reason,
                "app.flags_csv": ",".join(flags),
            }
        )
        manifest = RunManifest(
            turn_id=metadata.turn_id,
            conversation_id=metadata.conversation_id,
            agent_version=__version__,
            model_id=self.model_id,
            prompt_version=self.prompt_version,
            prompt_sha256=self.prompt_sha256,
            corpus_version=self.corpus_version,
            runtime_mode=self.runtime_mode,
            tool_counts={},
            flags=flags,
            citation_count=0,
            source_count=0,
            finish_reason=finish_reason,
            started_at=started_at,
            completed_at=datetime.now(UTC),
        )
        terminal_metadata: dict[str, Any] = {
            "kind": kind,
            "turn_id": metadata.turn_id,
            "manifest": manifest.model_dump(mode="json"),
        }
        if error_code:
            terminal_metadata["error_code"] = error_code
        return Event(
            author=self.name,
            content=types.Content(
                role="model",
                parts=[types.Part(text=message)],
            ),
            turn_complete=True,
            custom_metadata=terminal_metadata,
        )


class StaticAgentLoader(BaseAgentLoader):
    def __init__(self, app: App) -> None:
        self._app = app

    def load_agent(self, agent_name: str) -> App:
        if agent_name != self._app.name:
            raise ValueError(f"Unknown agent: {agent_name}")
        return self._app

    def list_agents(self) -> list[str]:
        return [self._app.name]


def build_agent_loader(
    workflow: ResearchWorkflow,
    registry: AdkRunRegistry,
    *,
    deadline_seconds: float = 180.0,
    runtime_mode: str = "mock",
    model_id: str = GENERATION_MODEL_ID,
    prompt_version: str = SYNTHESIS_PROMPT_VERSION,
    prompt_sha256: str = SYNTHESIS_PROMPT_SHA256,
    corpus_version: str = "unknown",
) -> StaticAgentLoader:
    agent = DeepResearchAdkAgent(
        name="deepresearch_agent",
        description="Ischemic-stroke drug-discovery evidence research workflow.",
        workflow=workflow,
        registry=registry,
        deadline_seconds=deadline_seconds,
        runtime_mode=runtime_mode,
        model_id=model_id,
        prompt_version=prompt_version,
        prompt_sha256=prompt_sha256,
        corpus_version=corpus_version,
    )
    return StaticAgentLoader(
        App(
            name="deepresearch_agent",
            root_agent=agent,
        )
    )


def _content_text(content: types.Content | None) -> str:
    if content is None:
        return ""
    return "\n".join(part.text for part in content.parts or [] if part.text)


def _to_adk_event(event: WorkflowEvent, *, author: str) -> Event:
    text = event.delta or event.message
    if event.kind == "completed" and event.result:
        text = event.result.answer_markdown
    metadata: dict[str, Any] = {
        "kind": event.kind,
        "turn_id": event.turn_id,
        **event.details,
    }
    if event.result:
        metadata["manifest"] = event.result.manifest.model_dump(mode="json")
        metadata["source_count"] = len(event.result.sources)
        source_summary = [
            {
                "id": source.evidence_id,
                "title": source.title,
                "url": str(source.url) if source.url else None,
                "sourceType": (
                    "internal" if source.source_kind.value == "internal" else "web"
                ),
            }
            for source in event.result.sources
        ]
        metadata["source_summary"] = source_summary
        metadata["sources"] = source_summary
    return Event(
        author=author,
        content=(
            types.Content(role="model", parts=[types.Part(text=text)])
            if text
            else None
        ),
        partial=event.kind == "answer_delta",
        turn_complete=event.kind in {"completed", "cancelled", "error"},
        custom_metadata=metadata,
    )
