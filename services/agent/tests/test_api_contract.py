from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Any

import httpx
import pytest

from deepresearch_agent.api.app import create_app
from deepresearch_agent.domain.models import (
    ResearchResult,
    RunManifest,
    WorkflowEvent,
)
from deepresearch_agent.infrastructure.feedback import FeedbackSynchronizer
from deepresearch_agent.settings import Settings


class FakeWorkflow:
    corpus_document_count = 220

    async def close(self) -> None:
        return None

    async def run(self, **kwargs: Any) -> AsyncIterator[WorkflowEvent]:
        turn_id = str(kwargs["turn_id"])
        yield WorkflowEvent(kind="research_started", turn_id=turn_id, message="started")
        yield WorkflowEvent(kind="search_progress", turn_id=turn_id, message="searched")
        yield WorkflowEvent(kind="answer_delta", turn_id=turn_id, delta="synthetic answer")
        yield WorkflowEvent(
            kind="completed",
            turn_id=turn_id,
            result=ResearchResult(
                answer_markdown="synthetic answer",
                claims=[],
                sources=[],
                limitations=[],
                manifest=RunManifest(
                    turn_id=turn_id,
                    conversation_id=str(kwargs["conversation_id"]),
                    agent_version="test",
                    model_id="gemini-3.6-flash",
                    prompt_version="test",
                    prompt_sha256="0" * 64,
                    corpus_version="test",
                    runtime_mode="mock",
                    tool_counts={"internal_search": 1},
                    flags=[],
                    citation_count=0,
                    source_count=0,
                    completed_at=datetime.now(UTC),
                ),
            ),
        )


def _payload() -> dict[str, object]:
    return {
        "app_name": "deepresearch_agent",
        "user_id": "local-user-id",
        "session_id": "conversation-1",
        "new_message": {
            "role": "user",
            "parts": [{"text": "Assess NLRP3 in ischemic stroke"}],
        },
        "streaming": True,
        "custom_metadata": {
            "turn_id": "turn-1",
            "conversation_id": "conversation-1",
            "target_molecule": "NLRP3",
            "mechanism": "inhibition",
            "disease": "ischemic stroke",
        },
    }


@pytest.mark.asyncio
async def test_openapi_and_sse_public_contract(tmp_path, monkeypatch) -> None:
    monkeypatch.delenv("OTEL_EXPORTER_OTLP_TRACES_ENDPOINT", raising=False)
    settings = Settings(
        database_path=tmp_path / "corpus.sqlite",
        session_database_path=tmp_path / "sessions.sqlite",
    )
    app = create_app(
        settings=settings,
        workflow=FakeWorkflow(),  # type: ignore[arg-type]
        feedback_synchronizer=FeedbackSynchronizer(None),
    )
    schema = app.openapi()
    assert {"/healthz", "/run", "/run_sse", "/runs/{turn_id}/cancel"} <= set(
        schema["paths"]
    )

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://agent.test",
    ) as client:
        response = await client.post("/run_sse", json=_payload())

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    data_lines = [
        line.removeprefix("data: ")
        for line in response.text.splitlines()
        if line.startswith("data: ")
    ]
    assert data_lines[-1] == "[DONE]"
    events = [json.loads(line) for line in data_lines[:-1]]
    kinds = [event["customMetadata"]["kind"] for event in events]
    assert kinds == [
        "research_started",
        "search_progress",
        "answer_delta",
        "completed",
    ]
    assert [event["customMetadata"]["event_sequence"] for event in events] == [
        0,
        1,
        2,
        3,
    ]
    assert all(
        event["customMetadata"]["conversation_id"] == "conversation-1"
        for event in events
    )
    assert len({event["id"] for event in events}) == len(events)
    source_summary = events[-1]["customMetadata"]["source_summary"]
    assert all("verificationStatus" in source for source in source_summary)
    assert not any("tool_response" in line or "internal excerpt" in line for line in data_lines)


@pytest.mark.asyncio
async def test_cancel_unknown_turn_and_conversation_mismatch(tmp_path, monkeypatch) -> None:
    monkeypatch.delenv("OTEL_EXPORTER_OTLP_TRACES_ENDPOINT", raising=False)
    app = create_app(
        settings=Settings(
            database_path=tmp_path / "corpus.sqlite",
            session_database_path=tmp_path / "sessions.sqlite",
        ),
        workflow=FakeWorkflow(),  # type: ignore[arg-type]
        feedback_synchronizer=FeedbackSynchronizer(None),
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://agent.test",
    ) as client:
        assert (await client.post("/runs/missing/cancel")).status_code == 404
        payload = _payload()
        metadata = dict(payload["custom_metadata"])  # type: ignore[arg-type]
        metadata["conversation_id"] = "other"
        payload["custom_metadata"] = metadata
        assert (await client.post("/run_sse", json=payload)).status_code == 422


@pytest.mark.asyncio
async def test_client_cannot_set_trace_data_classification(tmp_path, monkeypatch) -> None:
    monkeypatch.delenv("OTEL_EXPORTER_OTLP_TRACES_ENDPOINT", raising=False)
    app = create_app(
        settings=Settings(
            database_path=tmp_path / "corpus.sqlite",
            session_database_path=tmp_path / "sessions.sqlite",
        ),
        workflow=FakeWorkflow(),  # type: ignore[arg-type]
        feedback_synchronizer=FeedbackSynchronizer(None),
    )
    payload = _payload()
    metadata = dict(payload["custom_metadata"])  # type: ignore[arg-type]
    metadata["data_classification"] = "public"
    payload["custom_metadata"] = metadata

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://agent.test",
    ) as client:
        response = await client.post("/run_sse", json=payload)

    assert response.status_code == 422


@pytest.mark.asyncio
async def test_cancel_registry_contract() -> None:
    from deepresearch_agent.api.app import RunRegistry

    registry = RunRegistry()
    event = await registry.begin("turn-1")
    assert await registry.cancel("turn-1")
    assert await registry.cancel("turn-1")
    assert event.is_set()
    await registry.finish("turn-1")
    assert not await registry.cancel("turn-1")
    await asyncio.sleep(0)
