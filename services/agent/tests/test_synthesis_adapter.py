from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any

import pytest

from deepresearch_agent.application.synthesis import AdkSynthesizer
from deepresearch_agent.domain.models import Evidence, ResearchInput, SourceKind


class FakeRunner:
    def __init__(self) -> None:
        self.prompt = ""
        self.session_service = FakeSessionService()

    async def run_async(self, **kwargs: Any) -> Any:
        self.prompt = kwargs["new_message"].parts[0].text
        draft = {
            "answer_markdown": (
                "> 創薬仮説探索用であり、臨床判断や患者個別助言には使用できません。"
                "\n\nSynthetic grounded claim [E1]"
            ),
            "claims": [
                {
                    "text": "Synthetic grounded claim",
                    "evidence_ids": ["E1"],
                    "support_level": "background",
                }
            ],
            "limitations": ["Synthetic adapter test."],
        }
        yield SimpleNamespace(
            content=SimpleNamespace(
                parts=[SimpleNamespace(text=json.dumps(draft, ensure_ascii=False))]
            )
        )


class FakeSessionService:
    def __init__(self) -> None:
        self.created: list[dict[str, str]] = []

    async def create_session(self, **kwargs: str) -> None:
        self.created.append(kwargs)


@pytest.mark.asyncio
async def test_adk_synthesis_adapter_with_mock_runner_uses_structured_evidence() -> None:
    runner = FakeRunner()
    synthesizer = AdkSynthesizer.__new__(AdkSynthesizer)
    synthesizer._runner = runner
    evidence = [
        Evidence(
            id="E1",
            document_id="public-1",
            source_kind=SourceKind.PUBLIC,
            title="Synthetic publication",
            excerpt="Synthetic evidence, not a tool response.",
        )
    ]

    result = await synthesizer.synthesize(
        research_input=ResearchInput(
            target_molecule="NLRP3",
            disease="ischemic stroke",
            research_question="Assess target validity.",
        ),
        evidence=evidence,
        safe_trace_metadata={"app.turn_id": "turn-synthetic"},
    )

    prompt = json.loads(runner.prompt)
    assert prompt["research_input"]["disease"] == "ischemic stroke"
    assert prompt["evidence"][0]["id"] == "E1"
    assert result.claims[0].evidence_ids == ["E1"]
    assert runner.session_service.created[0]["user_id"] == "ephemeral-synthesis"
