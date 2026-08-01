from __future__ import annotations

import asyncio
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from deepresearch_agent.application.workflow import ResearchWorkflow
from deepresearch_agent.domain.models import Evidence, SourceKind
from deepresearch_agent.infrastructure.exa import ExaAdapterError, ExaErrorKind
from deepresearch_agent.settings import Settings


class FakeCorpus:
    def count_documents(self) -> int:
        return 1

    def search(self, **_: Any) -> list[Evidence]:
        return [
            Evidence(
                id="internal-original",
                document_id="internal:synthetic",
                source_kind=SourceKind.INTERNAL,
                title="Synthetic internal ischemic stroke evidence",
                excerpt="Synthetic evidence about ischemic stroke mechanisms.",
                score=1.0,
                provenance=["synthetic:test"],
            )
        ]


class FakeEmbeddings:
    async def embed_queries(self, texts: list[str]) -> list[list[float]]:
        return [[0.0] for _ in texts]

    async def close(self) -> None:
        return None


class FakeSessions:
    async def merge(self, **_: Any) -> dict[str, Any]:
        raise AssertionError("explicit session state should be used")


class FailingExa:
    def __init__(self) -> None:
        self.calls = 0

    async def search_publications(
        self, query: str, *, num_results: int = 10
    ) -> list[Evidence]:
        del query, num_results
        self.calls += 1
        raise ExaAdapterError(ExaErrorKind.UPSTREAM, retryable=True)

    async def close(self) -> None:
        return None


@pytest.mark.asyncio
async def test_exa_failure_retries_finitely_and_returns_internal_partial_success(
    tmp_path: Any,
    approval_registry_factory: Callable[..., Path],
) -> None:
    settings = Settings(
        runtime_mode="live",
        deployment_profile="approved_sensitive_pilot",
        allow_target_to_exa=True,
        EXA_API_KEY="test-exa-key",
        exa_retry_backoff_seconds=0,
        hmac_secret="test-secret-with-at-least-24-characters",
        database_path=tmp_path / "corpus.sqlite",
        session_database_path=tmp_path / "sessions.sqlite",
        sensitive_approval_registry_path=approval_registry_factory(),
    )
    exa = FailingExa()
    workflow = ResearchWorkflow(
        settings=settings,
        corpus=FakeCorpus(),  # type: ignore[arg-type]
        embeddings=FakeEmbeddings(),  # type: ignore[arg-type]
        sessions=FakeSessions(),  # type: ignore[arg-type]
        exa=exa,  # type: ignore[arg-type]
    )

    events = [
        event
        async for event in workflow.run(
            user_id="local-user",
            conversation_id="conversation-1",
            turn_id="turn-1",
            question="What evidence is available for ischemic stroke?",
            target_molecule=None,
            mechanism=None,
            disease="ischemic stroke",
            research_question=None,
            cancel_event=asyncio.Event(),
            session_state={"turn_count": 1},
        )
    ]

    completed = events[-1]
    assert completed.kind == "completed"
    assert completed.result is not None
    assert exa.calls == 2
    assert completed.result.manifest.tool_counts["exa_search"] == 2
    assert "exa_partial_failure" in completed.result.manifest.flags
    assert "exa_upstream" in completed.result.manifest.flags
    assert completed.result.sources[0].source_kind == SourceKind.INTERNAL
    assert "外部文献検索は一部失敗" in completed.result.answer_markdown
    serialized = completed.model_dump_json()
    assert "sensitive-query" not in serialized
    assert "raw-provider" not in serialized
