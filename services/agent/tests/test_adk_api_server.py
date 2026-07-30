from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any

import httpx
import pytest
from google.genai import types

from deepresearch_agent.api.adk_runtime import AdkRunRegistry, DeepResearchAdkAgent
from deepresearch_agent.api.app import create_adk_app
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
