from __future__ import annotations

import asyncio
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any
from uuid import uuid4

from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.responses import StreamingResponse

from deepresearch_agent.api.adk_runtime import AdkRunRegistry, build_agent_loader
from deepresearch_agent.api.schemas import (
    AdkContent,
    AdkEvent,
    FeedbackSyncResponse,
    HealthResponse,
    MessagePart,
    RunAgentRequest,
)
from deepresearch_agent.application.normalization import ScopeError
from deepresearch_agent.application.workflow import ResearchWorkflow
from deepresearch_agent.domain.models import WorkflowEvent
from deepresearch_agent.governance.approvals import log_approval_decisions
from deepresearch_agent.infrastructure.corpus import CorpusRepository
from deepresearch_agent.infrastructure.embeddings import (
    GeminiEmbeddingProvider,
    HashEmbeddingProvider,
)
from deepresearch_agent.infrastructure.exa import ExaSearchClient
from deepresearch_agent.infrastructure.feedback import (
    FeedbackRecord,
    FeedbackSynchronizer,
    WeaveFeedbackBackend,
)
from deepresearch_agent.infrastructure.publication_metadata import (
    EuropePmcMetadataVerifier,
)
from deepresearch_agent.infrastructure.sessions import AdkSessionStateStore
from deepresearch_agent.observability.otel import configure_otel
from deepresearch_agent.settings import Settings, get_settings

RunRegistry = AdkRunRegistry


def build_workflow(settings: Settings) -> ResearchWorkflow:
    corpus = CorpusRepository(settings.database_path)
    corpus.initialize()
    embedding_model = (
        settings.embedding_model
        if settings.embedding_provider == "gemini"
        else HashEmbeddingProvider.model_name
    )
    corpus.assert_embedding_contract(
        snapshot_id=settings.corpus_version,
        model_name=embedding_model,
        dimension=settings.embedding_dimension,
    )
    exa = (
        ExaSearchClient(api_key=settings.exa_api_key.get_secret_value())
        if settings.live_exa_enabled and settings.exa_api_key
        else None
    )
    metadata_verifier = EuropePmcMetadataVerifier() if exa else None
    embeddings = (
        GeminiEmbeddingProvider(api_key=settings.google_api_key.get_secret_value())
        if settings.embedding_provider == "gemini" and settings.google_api_key
        else HashEmbeddingProvider()
    )
    return ResearchWorkflow(
        settings=settings,
        corpus=corpus,
        embeddings=embeddings,
        sessions=AdkSessionStateStore(settings.session_database_path),
        exa=exa,
        metadata_verifier=metadata_verifier,
    )


def create_app(
    *,
    settings: Settings | None = None,
    workflow: ResearchWorkflow | None = None,
    feedback_synchronizer: FeedbackSynchronizer | None = None,
) -> FastAPI:
    configured = settings or get_settings()
    log_approval_decisions(configured.sensitive_approval_decisions)
    tracing_enabled = configure_otel()
    owned_workflow = workflow is None
    research_workflow = workflow or build_workflow(configured)
    registry = RunRegistry()
    if feedback_synchronizer is None:
        project_path = (
            f"{configured.wandb_entity}/{configured.wandb_project}"
            if configured.wandb_entity and configured.wandb_project
            else None
        )
        feedback_synchronizer = FeedbackSynchronizer(
            WeaveFeedbackBackend(project_path) if project_path else None,
            include_comment=configured.feedback_comment_to_wandb_enabled,
        )

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        yield
        if owned_workflow:
            await research_workflow.close()

    app = FastAPI(
        title="Deep Research Disease Agent",
        version="0.1.0",
        lifespan=lifespan,
    )

    @app.get("/healthz", response_model=HealthResponse)
    async def health() -> HealthResponse:
        return HealthResponse(
            runtime_mode=configured.runtime_mode,
            corpus_documents=research_workflow.corpus_document_count,
            tracing_export_enabled=tracing_enabled,
        )

    @app.post("/run_sse")
    async def run_sse(payload: RunAgentRequest, request: Request) -> StreamingResponse:
        metadata = payload.custom_metadata
        if metadata.conversation_id != payload.session_id:
            raise HTTPException(
                status_code=422,
                detail="custom_metadata.conversation_id must equal session_id",
            )
        try:
            cancel_event = await registry.begin(metadata.turn_id)
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

        async def stream() -> AsyncIterator[str]:
            event_sequence = 0
            try:
                async with asyncio.timeout(configured.turn_deadline_seconds):
                    async for workflow_event in research_workflow.run(
                        user_id=payload.user_id,
                        conversation_id=metadata.conversation_id,
                        turn_id=metadata.turn_id,
                        question=payload.question,
                        target_molecule=metadata.target_molecule,
                        mechanism=metadata.mechanism,
                        disease=metadata.disease,
                        research_question=metadata.research_question,
                        cancel_event=cancel_event,
                    ):
                        if await request.is_disconnected():
                            cancel_event.set()
                        event = _to_adk_event(
                            workflow_event,
                            conversation_id=metadata.conversation_id,
                            sequence=event_sequence,
                        )
                        event_sequence += 1
                        yield (
                            f"data: {event.model_dump_json(exclude_none=True, by_alias=True)}\n\n"
                        )
                yield "data: [DONE]\n\n"
            except asyncio.CancelledError:
                event = _terminal_event(
                    "cancelled",
                    metadata.turn_id,
                    metadata.conversation_id,
                    event_sequence,
                    "調査を中止しました。",
                )
                yield f"data: {event.model_dump_json(exclude_none=True, by_alias=True)}\n\n"
                yield "data: [DONE]\n\n"
            except (ScopeError, ValueError) as exc:
                event = _terminal_event(
                    "error",
                    metadata.turn_id,
                    metadata.conversation_id,
                    event_sequence,
                    str(exc),
                )
                yield f"data: {event.model_dump_json(exclude_none=True, by_alias=True)}\n\n"
                yield "data: [DONE]\n\n"
            except TimeoutError:
                cancel_event.set()
                event = _terminal_event(
                    "error",
                    metadata.turn_id,
                    metadata.conversation_id,
                    event_sequence,
                    "調査が実行時間の上限を超えました。",
                )
                yield f"data: {event.model_dump_json(exclude_none=True, by_alias=True)}\n\n"
                yield "data: [DONE]\n\n"
            except Exception:
                event = _terminal_event(
                    "error",
                    metadata.turn_id,
                    metadata.conversation_id,
                    event_sequence,
                    "Agent execution failed. See server logs for the correlation turn ID.",
                )
                yield f"data: {event.model_dump_json(exclude_none=True, by_alias=True)}\n\n"
                yield "data: [DONE]\n\n"
            finally:
                await registry.finish(metadata.turn_id)

        return StreamingResponse(
            stream(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    @app.post("/run", response_model=list[AdkEvent])
    async def run(payload: RunAgentRequest) -> list[AdkEvent]:
        metadata = payload.custom_metadata
        if metadata.conversation_id != payload.session_id:
            raise HTTPException(
                status_code=422,
                detail="custom_metadata.conversation_id must equal session_id",
            )
        try:
            cancel_event = await registry.begin(metadata.turn_id)
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        events: list[AdkEvent] = []
        try:
            async for sequence, workflow_event in _enumerate_async(
                research_workflow.run(
                    user_id=payload.user_id,
                    conversation_id=metadata.conversation_id,
                    turn_id=metadata.turn_id,
                    question=payload.question,
                    target_molecule=metadata.target_molecule,
                    mechanism=metadata.mechanism,
                    disease=metadata.disease,
                    research_question=metadata.research_question,
                    cancel_event=cancel_event,
                )
            ):
                events.append(
                    _to_adk_event(
                        workflow_event,
                        conversation_id=metadata.conversation_id,
                        sequence=sequence,
                    )
                )
        finally:
            await registry.finish(metadata.turn_id)
        return events

    @app.post("/runs/{turn_id}/cancel", status_code=204)
    async def cancel(turn_id: str) -> Response:
        if not await registry.cancel(turn_id):
            raise HTTPException(status_code=404, detail="active turn not found")
        return Response(status_code=204)

    @app.post("/feedback/sync", response_model=FeedbackSyncResponse)
    async def sync_feedback(feedback: FeedbackRecord) -> FeedbackSyncResponse:
        result = await asyncio.to_thread(feedback_synchronizer.sync, feedback)
        return FeedbackSyncResponse(
            status=result.status,
            feedback_id=result.feedback_id,
            trace_id=result.trace_id,
        )

    return app


def _to_adk_event(
    event: WorkflowEvent,
    *,
    conversation_id: str,
    sequence: int,
) -> AdkEvent:
    text = event.delta or event.message
    if event.kind == "completed" and event.result:
        text = event.result.answer_markdown
    content = (
        AdkContent(role="model", parts=[MessagePart(text=text)])
        if text
        else None
    )
    metadata: dict[str, Any] = {
        "kind": event.kind,
        "turn_id": event.turn_id,
        "conversation_id": conversation_id,
        "event_sequence": sequence,
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
                "verificationStatus": source.verification_status.value,
            }
            for source in event.result.sources
        ]
        metadata["source_summary"] = source_summary
        metadata["sources"] = source_summary
    return AdkEvent(
        id=uuid4().hex,
        content=content,
        partial=event.kind == "answer_delta",
        turn_complete=event.kind in {"completed", "cancelled", "error"},
        custom_metadata=metadata,
    )


def _terminal_event(
    kind: str,
    turn_id: str,
    conversation_id: str,
    sequence: int,
    message: str,
) -> AdkEvent:
    return AdkEvent(
        id=uuid4().hex,
        content=AdkContent(role="model", parts=[MessagePart(text=message)]),
        turn_complete=True,
        custom_metadata={
            "kind": kind,
            "turn_id": turn_id,
            "conversation_id": conversation_id,
            "event_sequence": sequence,
        },
    )


async def _enumerate_async(
    iterator: AsyncIterator[WorkflowEvent],
) -> AsyncIterator[tuple[int, WorkflowEvent]]:
    sequence = 0
    async for item in iterator:
        yield sequence, item
        sequence += 1


def create_adk_app(
    *,
    settings: Settings | None = None,
    workflow: ResearchWorkflow | None = None,
) -> FastAPI:
    configured = settings or get_settings()
    log_approval_decisions(configured.sensitive_approval_decisions)
    configure_otel()
    owned_workflow = workflow is None
    research_workflow = workflow or build_workflow(configured)
    registry = AdkRunRegistry()
    project_path = (
        f"{configured.wandb_entity}/{configured.wandb_project}"
        if configured.wandb_entity and configured.wandb_project
        else None
    )
    feedback = FeedbackSynchronizer(
        WeaveFeedbackBackend(project_path) if project_path else None,
        include_comment=configured.feedback_comment_to_wandb_enabled,
    )

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        yield
        if owned_workflow:
            await research_workflow.close()

    from google.adk.cli.fast_api import get_fast_api_app

    agents_dir = configured.session_database_path.parent.resolve()
    agents_dir.mkdir(parents=True, exist_ok=True)
    adk_app = get_fast_api_app(
        agents_dir=str(agents_dir),
        agent_loader=build_agent_loader(
            research_workflow,
            registry,
            deadline_seconds=configured.turn_deadline_seconds,
            runtime_mode=configured.runtime_mode,
            model_id=configured.model,
            prompt_version=configured.prompt_version,
            prompt_sha256=configured.prompt_sha256,
            corpus_version=configured.corpus_version,
        ),
        session_service_uri=(
            f"sqlite+aiosqlite:///{configured.session_database_path.resolve().as_posix()}"
        ),
        use_local_storage=False,
        web=False,
        auto_create_session=True,
        lifespan=lifespan,
    )
    adk_app.title = "Deep Research Disease Agent"
    adk_app.version = "0.1.0"

    @adk_app.get("/healthz", response_model=HealthResponse)
    async def health() -> HealthResponse:
        return HealthResponse(
            runtime_mode=configured.runtime_mode,
            corpus_documents=research_workflow.corpus_document_count,
            tracing_export_enabled=bool(os.environ.get("OTEL_EXPORTER_OTLP_TRACES_ENDPOINT")),
        )

    @adk_app.post("/runs/{turn_id}/cancel", status_code=204)
    async def cancel(turn_id: str) -> Response:
        if not await registry.cancel(turn_id):
            raise HTTPException(status_code=404, detail="active turn not found")
        return Response(status_code=204)

    @adk_app.post("/feedback/sync", response_model=FeedbackSyncResponse)
    async def sync_feedback(feedback_record: FeedbackRecord) -> FeedbackSyncResponse:
        result = await asyncio.to_thread(feedback.sync, feedback_record)
        return FeedbackSyncResponse(
            status=result.status,
            feedback_id=result.feedback_id,
            trace_id=result.trace_id,
        )

    return adk_app


app = create_adk_app()
