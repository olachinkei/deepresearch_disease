from __future__ import annotations

import asyncio
import json
from collections.abc import Callable
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import httpx
import pytest
from google.genai import types
from pydantic import ValidationError

from deepresearch_agent.api.adk_runtime import AdkRunRegistry, DeepResearchAdkAgent
from deepresearch_agent.api.app import create_adk_app
from deepresearch_agent.application.synthesis import SynthesisDraft
from deepresearch_agent.application.workflow import ResearchWorkflow
from deepresearch_agent.domain.models import Evidence, SourceKind, WorkflowEvent
from deepresearch_agent.infrastructure.sessions import AdkSessionStateStore
from deepresearch_agent.settings import Settings


def _payload(*, turn_id: str, question: str) -> dict[str, object]:
    return {
        "app_name": "deepresearch_agent",
        "user_id": "synthetic-user",
        "session_id": "synthetic-conversation",
        "new_message": {
            "role": "user",
            "parts": [{"text": question}],
        },
        "streaming": True,
        "custom_metadata": {
            "turn_id": turn_id,
            "conversation_id": "synthetic-conversation",
            "target_molecule": "NLRP3" if turn_id == "turn-1" else None,
            "mechanism": "inhibition" if turn_id == "turn-1" else None,
            "disease": "ischemic stroke",
        },
    }


def _sse_events(response: httpx.Response) -> list[dict[str, Any]]:
    return [
        json.loads(line.removeprefix("data: "))
        for line in response.text.splitlines()
        if line.startswith("data: ") and line != "data: [DONE]"
    ]


def test_turn_deadline_cannot_exceed_product_limit() -> None:
    with pytest.raises(ValidationError):
        Settings(turn_deadline_seconds=180.01)


@pytest.mark.asyncio
async def test_google_adk_api_server_openapi_sse_and_session_state(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.delenv("OTEL_EXPORTER_OTLP_TRACES_ENDPOINT", raising=False)
    app = create_adk_app(
        settings=Settings(
            database_path=tmp_path / "corpus.sqlite",
            session_database_path=tmp_path / "sessions.sqlite",
        )
    )
    schema = app.openapi()
    assert schema["paths"]["/run_sse"]["post"]["requestBody"]["content"][
        "application/json"
    ]["schema"] == {"$ref": "#/components/schemas/RunAgentRequest"}
    assert "/apps/{app_name}/users/{user_id}/sessions/{session_id}" in schema["paths"]

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://agent.test",
    ) as client:
        first = await client.post(
            "/run_sse",
            json=_payload(turn_id="turn-1", question="Assess NLRP3 inhibition."),
        )
        second = await client.post(
            "/run_sse",
            json=_payload(turn_id="turn-2", question="What contradictory evidence exists?"),
        )
        session = await client.get(
            "/apps/deepresearch_agent/users/synthetic-user/"
            "sessions/synthetic-conversation"
        )

    assert first.status_code == 200
    assert second.status_code == 200
    events = [
        json.loads(line.removeprefix("data: "))
        for line in first.text.splitlines()
        if line.startswith("data: ")
    ]
    assert [event["customMetadata"]["kind"] for event in events] == [
        "research_started",
        "search_progress",
        "answer_delta",
        "answer_delta",
        "completed",
    ]
    state = session.json()["state"]
    assert state["turn_count"] == 2
    assert state["target_molecule"] == "NLRP3"
    assert state["mechanism"] == "inhibition"
    assert state["disease"] == "ischemic stroke"


class FailingWorkflow:
    async def run(self, **kwargs: Any) -> Any:
        del kwargs
        if False:
            yield None
        raise RuntimeError("provider-secret-must-not-leak")


@pytest.mark.asyncio
async def test_adk_agent_sanitizes_external_failures() -> None:
    agent = DeepResearchAdkAgent.model_construct(
        name="deepresearch_agent",
        description="test",
        workflow=FailingWorkflow(),
        registry=AdkRunRegistry(),
    )
    context = SimpleNamespace(
        run_config=SimpleNamespace(
            custom_metadata={
                "turn_id": "turn-failure",
                "conversation_id": "conversation-failure",
                "disease": "ischemic stroke",
            }
        ),
        user_content=types.Content(
            role="user",
            parts=[types.Part(text="Synthetic failure test")],
        ),
        session=SimpleNamespace(
            user_id="synthetic-user",
            state={},
        ),
    )

    events = [event async for event in agent._run_async_impl(context)]

    assert events[-1].custom_metadata["kind"] == "error"
    serialized = events[-1].model_dump_json()
    assert "provider-secret-must-not-leak" not in serialized
    assert events[-1].custom_metadata["manifest"]["finish_reason"] == "error"


class SyntheticCorpus:
    def __init__(self, evidence: list[Evidence] | None = None) -> None:
        self._evidence = evidence or []

    def count_documents(self) -> int:
        return len(self._evidence)

    def search(self, **_: Any) -> list[Evidence]:
        return list(self._evidence)


class SyntheticEmbeddings:
    async def embed(self, texts: list[str]) -> list[list[float]]:
        return [[0.0] for _ in texts]


class HangingExa:
    def __init__(self) -> None:
        self.started = asyncio.Event()
        self.cleaned = asyncio.Event()

    async def search_publications(
        self, query: str, *, num_results: int = 10
    ) -> list[Evidence]:
        del query, num_results
        self.started.set()
        try:
            await asyncio.Event().wait()
        finally:
            self.cleaned.set()
        return []

    async def close(self) -> None:
        return None


class HangingSynthesizer:
    def __init__(self) -> None:
        self.started = asyncio.Event()
        self.cleaned = asyncio.Event()

    async def synthesize(self, **_: Any) -> SynthesisDraft:
        self.started.set()
        try:
            await asyncio.Event().wait()
        finally:
            self.cleaned.set()
        raise AssertionError("cancelled synthesis must not return")


def _workflow(
    *,
    settings: Settings,
    corpus: SyntheticCorpus,
    tmp_path: Any,
    exa: HangingExa | None = None,
    synthesizer: HangingSynthesizer | None = None,
) -> ResearchWorkflow:
    return ResearchWorkflow(
        settings=settings,
        corpus=corpus,  # type: ignore[arg-type]
        embeddings=SyntheticEmbeddings(),  # type: ignore[arg-type]
        sessions=AdkSessionStateStore(tmp_path / "unused-sessions.sqlite"),
        exa=exa,  # type: ignore[arg-type]
        deterministic_synthesizer=synthesizer,
    )


@pytest.mark.asyncio
async def test_production_adk_deadline_cancels_hanging_exa_once(
    tmp_path: Any,
    monkeypatch: pytest.MonkeyPatch,
    approval_registry_factory: Callable[..., Path],
) -> None:
    safe_attributes: list[dict[str, Any]] = []
    monkeypatch.setattr(
        "deepresearch_agent.api.adk_runtime.set_safe_span_attributes",
        lambda attributes: safe_attributes.append(attributes),
    )
    settings = Settings(
        runtime_mode="live",
        allow_target_to_exa=True,
        EXA_API_KEY="synthetic-key",
        turn_deadline_seconds=0.05,
        exa_retry_backoff_seconds=0,
        database_path=tmp_path / "corpus.sqlite",
        session_database_path=tmp_path / "sessions.sqlite",
        sensitive_approval_registry_path=approval_registry_factory(),
    )
    exa = HangingExa()
    workflow = _workflow(
        settings=settings,
        corpus=SyntheticCorpus(),
        tmp_path=tmp_path,
        exa=exa,
    )
    app = create_adk_app(settings=settings, workflow=workflow)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://agent.test",
    ) as client:
        started_at = asyncio.get_running_loop().time()
        response = await client.post(
            "/run_sse",
            json=_payload(turn_id="turn-timeout", question="Synthetic slow Exa"),
        )
        elapsed = asyncio.get_running_loop().time() - started_at
        assert (await client.post("/runs/turn-timeout/cancel")).status_code == 404

    events = _sse_events(response)
    terminal = [
        event
        for event in events
        if event["customMetadata"]["kind"] in {"completed", "cancelled", "error"}
    ]
    assert response.status_code == 200
    assert elapsed < 1
    assert exa.started.is_set()
    assert exa.cleaned.is_set()
    assert len(terminal) == 1
    assert terminal[0]["customMetadata"]["kind"] == "error"
    assert terminal[0]["customMetadata"]["error_code"] == "turn_deadline_exceeded"
    manifest = terminal[0]["customMetadata"]["manifest"]
    assert manifest["finish_reason"] == "timeout"
    assert manifest["flags"] == ["timeout"]
    assert any(
        attributes.get("app.finish_reason") == "timeout"
        and attributes.get("app.flags_csv") == "timeout"
        for attributes in safe_attributes
    )
    assert "synthetic-key" not in response.text
    assert "toolResponse" not in response.text
    assert not any(
        event["customMetadata"]["kind"] in {"answer_delta", "completed"}
        for event in events
    )


@pytest.mark.asyncio
async def test_production_adk_cancel_interrupts_synthesis_and_emits_once(
    tmp_path: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    safe_attributes: list[dict[str, Any]] = []
    monkeypatch.setattr(
        "deepresearch_agent.api.adk_runtime.set_safe_span_attributes",
        lambda attributes: safe_attributes.append(attributes),
    )
    settings = Settings(
        turn_deadline_seconds=5,
        database_path=tmp_path / "corpus.sqlite",
        session_database_path=tmp_path / "sessions.sqlite",
    )
    evidence = Evidence(
        id="synthetic-evidence",
        document_id="synthetic-document",
        source_kind=SourceKind.INTERNAL,
        title="Synthetic ischemic stroke evidence",
        excerpt="Synthetic evidence for cancellation testing.",
    )
    synthesizer = HangingSynthesizer()
    workflow = _workflow(
        settings=settings,
        corpus=SyntheticCorpus([evidence]),
        tmp_path=tmp_path,
        synthesizer=synthesizer,
    )
    app = create_adk_app(settings=settings, workflow=workflow)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://agent.test",
    ) as client:
        run_task = asyncio.create_task(
            client.post(
                "/run_sse",
                json=_payload(turn_id="turn-cancel", question="Synthetic slow Gemini"),
            )
        )
        await asyncio.wait_for(synthesizer.started.wait(), timeout=1)
        cancel_started_at = asyncio.get_running_loop().time()
        cancel_response = await client.post("/runs/turn-cancel/cancel")
        response = await asyncio.wait_for(run_task, timeout=1)
        cancel_elapsed = asyncio.get_running_loop().time() - cancel_started_at
        assert (await client.post("/runs/turn-cancel/cancel")).status_code == 404

    events = _sse_events(response)
    terminal = [
        event
        for event in events
        if event["customMetadata"]["kind"] in {"completed", "cancelled", "error"}
    ]
    assert cancel_response.status_code == 204
    assert cancel_elapsed < 1
    assert synthesizer.cleaned.is_set()
    assert len(terminal) == 1
    assert terminal[0]["customMetadata"]["kind"] == "cancelled"
    assert terminal[0]["customMetadata"]["manifest"]["finish_reason"] == "cancelled"
    assert any(
        attributes.get("app.finish_reason") == "cancelled"
        and attributes.get("app.flags_csv") == "cancelled"
        for attributes in safe_attributes
    )
    assert "Synthetic evidence for cancellation testing." not in response.text
    assert "toolResponse" not in response.text
    assert not any(
        event["customMetadata"]["kind"] in {"answer_delta", "completed"}
        for event in events
    )


class ImmediateCompletionWorkflow:
    async def run(self, **kwargs: Any) -> Any:
        yield WorkflowEvent(
            kind="completed",
            turn_id=kwargs["turn_id"],
        )


@pytest.mark.asyncio
async def test_completed_turn_wins_cancel_race_without_second_terminal() -> None:
    registry = AdkRunRegistry()
    agent = DeepResearchAdkAgent.model_construct(
        name="deepresearch_agent",
        description="test",
        workflow=ImmediateCompletionWorkflow(),
        registry=registry,
    )
    context = SimpleNamespace(
        run_config=SimpleNamespace(
            custom_metadata={
                "turn_id": "turn-complete-race",
                "conversation_id": "conversation-race",
                "disease": "ischemic stroke",
            }
        ),
        user_content=types.Content(
            role="user",
            parts=[types.Part(text="Synthetic completion race")],
        ),
        session=SimpleNamespace(
            user_id="synthetic-user",
            state={},
        ),
    )
    stream = agent._run_async_impl(context)

    completed = await anext(stream)
    assert completed.custom_metadata["kind"] == "completed"
    assert not await registry.cancel("turn-complete-race")
    with pytest.raises(StopAsyncIteration):
        await anext(stream)
