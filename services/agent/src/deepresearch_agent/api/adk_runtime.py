from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncGenerator
from typing import Any

from google.adk.agents import BaseAgent
from google.adk.agents.invocation_context import InvocationContext
from google.adk.apps import App
from google.adk.cli.utils.base_agent_loader import BaseAgentLoader
from google.adk.events import Event, EventActions
from google.genai import types

from deepresearch_agent.api.schemas import RunCustomMetadata
from deepresearch_agent.application.workflow import ResearchWorkflow
from deepresearch_agent.domain.models import WorkflowEvent
from deepresearch_agent.infrastructure.sessions import merge_research_state

logger = logging.getLogger(__name__)


class AdkRunRegistry:
    def __init__(self) -> None:
        self._events: dict[str, asyncio.Event] = {}
        self._lock = asyncio.Lock()

    async def begin(self, turn_id: str) -> asyncio.Event:
        async with self._lock:
            if turn_id in self._events:
                raise ValueError("turn_id is already running")
            event = asyncio.Event()
            self._events[turn_id] = event
            return event

    async def cancel(self, turn_id: str) -> bool:
        async with self._lock:
            event = self._events.get(turn_id)
            if event is None:
                return False
            event.set()
            return True

    async def finish(self, turn_id: str) -> None:
        async with self._lock:
            self._events.pop(turn_id, None)


class DeepResearchAdkAgent(BaseAgent):
    workflow: ResearchWorkflow
    registry: AdkRunRegistry

    async def _run_async_impl(
        self, ctx: InvocationContext
    ) -> AsyncGenerator[Event]:
        raw_metadata = ctx.run_config.custom_metadata if ctx.run_config else None
        metadata = RunCustomMetadata.model_validate(raw_metadata or {})
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
        cancel_event = await self.registry.begin(metadata.turn_id)
        first_event = True
        try:
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
                if first_event:
                    event.actions = EventActions(state_delta=state_delta)
                    first_event = False
                yield event
        except asyncio.CancelledError:
            yield Event(
                author=self.name,
                content=types.Content(
                    role="model",
                    parts=[types.Part(text="調査を中止しました。")],
                ),
                turn_complete=True,
                custom_metadata={
                    "kind": "cancelled",
                    "turn_id": metadata.turn_id,
                },
            )
        except Exception as exc:
            logger.warning(
                "Agent execution failed turn_id=%s error_type=%s",
                metadata.turn_id,
                type(exc).__name__,
            )
            yield Event(
                author=self.name,
                content=types.Content(
                    role="model",
                    parts=[
                        types.Part(
                            text=(
                                "調査を完了できませんでした。"
                                "設定または外部サービスを確認してください。"
                            )
                        )
                    ],
                ),
                turn_complete=True,
                custom_metadata={
                    "kind": "error",
                    "turn_id": metadata.turn_id,
                    "error_code": "agent_execution_failed",
                },
            )
        finally:
            await self.registry.finish(metadata.turn_id)


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
) -> StaticAgentLoader:
    agent = DeepResearchAdkAgent(
        name="deepresearch_agent",
        description="Ischemic-stroke drug-discovery evidence research workflow.",
        workflow=workflow,
        registry=registry,
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
