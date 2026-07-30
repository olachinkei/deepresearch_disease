from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Any

from opentelemetry import trace

from deepresearch_agent import __version__
from deepresearch_agent.application.budget import BudgetExceeded, ResearchBudget, ToolKind
from deepresearch_agent.application.citations import verify_citations
from deepresearch_agent.application.dedupe import deduplicate_evidence
from deepresearch_agent.application.normalization import (
    build_search_queries,
    normalize_research_input,
)
from deepresearch_agent.application.synthesis import (
    AdkSynthesizer,
    DeterministicSynthesizer,
    SynthesisDraft,
    Synthesizer,
)
from deepresearch_agent.domain.models import (
    Evidence,
    ResearchResult,
    RunManifest,
    SourceKind,
    SourceReference,
    WorkflowEvent,
)
from deepresearch_agent.infrastructure.corpus import CorpusRepository
from deepresearch_agent.infrastructure.embeddings import EmbeddingProvider
from deepresearch_agent.infrastructure.exa import ExaSearchClient
from deepresearch_agent.infrastructure.sessions import AdkSessionStateStore
from deepresearch_agent.observability.otel import (
    TraceMetadata,
    classify_trace_input,
    classify_trace_output,
    pseudonymize_user,
    set_safe_span_attributes,
    trace_content_attributes,
    trace_input_fingerprint,
)
from deepresearch_agent.settings import Settings


class ResearchWorkflow:
    def __init__(
        self,
        *,
        settings: Settings,
        corpus: CorpusRepository,
        embeddings: EmbeddingProvider,
        sessions: AdkSessionStateStore,
        exa: ExaSearchClient | None = None,
        deterministic_synthesizer: Synthesizer | None = None,
    ) -> None:
        self._settings = settings
        self._corpus = corpus
        self._embeddings = embeddings
        self._sessions = sessions
        self._exa = exa
        self._deterministic_synthesizer = (
            deterministic_synthesizer or DeterministicSynthesizer()
        )
        self._adk_synthesizer: Synthesizer | None = None

    async def close(self) -> None:
        if self._exa:
            await self._exa.close()

    @property
    def corpus_document_count(self) -> int:
        return self._corpus.count_documents()

    async def run(
        self,
        *,
        user_id: str,
        conversation_id: str,
        turn_id: str,
        question: str,
        target_molecule: str | None,
        mechanism: str | None,
        disease: str | None,
        research_question: str | None,
        cancel_event: asyncio.Event,
        session_state: dict[str, Any] | None = None,
    ) -> AsyncIterator[WorkflowEvent]:
        state = session_state or await self._sessions.merge(
            user_id=user_id,
            session_id=conversation_id,
            values={
                "target_molecule": target_molecule,
                "mechanism": mechanism,
                "disease": disease,
                "last_research_question": research_question or question,
                "last_turn_id": turn_id,
                "recent_question": question,
            },
        )
        effective_question = (
            research_question or question
            if int(state.get("turn_count", 1)) == 1
            else question
        )
        normalized = normalize_research_input(
            target_molecule=target_molecule or state.get("target_molecule"),
            mechanism=mechanism or state.get("mechanism"),
            disease=disease or state.get("disease"),
            research_question=effective_question,
        )
        input_classification = classify_trace_input(
            fingerprint=trace_input_fingerprint(
                question=question,
                target_molecule=normalized.target_molecule,
                mechanism=(
                    normalized.mechanism.value if normalized.mechanism else None
                ),
                disease=normalized.disease,
                research_question=normalized.research_question,
            ),
            public_fingerprints=self._settings.trace_public_input_fingerprints,
            synthetic_fingerprints=(
                self._settings.trace_synthetic_input_fingerprints
            ),
        )
        trace_metadata = TraceMetadata(
            user_hash=pseudonymize_user(
                user_id, self._settings.hmac_secret.get_secret_value()
            ),
            turn_id=turn_id,
            conversation_id=conversation_id,
            agent_version=__version__,
            prompt_version=self._settings.prompt_version,
            corpus_version=self._settings.corpus_version,
        )
        tracer = trace.get_tracer(__name__)
        with tracer.start_as_current_span("invoke_agent deepresearch_agent"):
            set_safe_span_attributes(trace_metadata.attributes())
            yield WorkflowEvent(
                kind="research_started",
                turn_id=turn_id,
                message="調査条件を正規化しました。",
                details={
                    "disease": normalized.disease,
                    "target_present": bool(normalized.target_molecule),
                },
            )
            self._raise_if_cancelled(cancel_event)
            budget = ResearchBudget()
            evidence = await self._retrieve(
                normalized=normalized,
                budget=budget,
                turn_id=turn_id,
                cancel_event=cancel_event,
            )
            yield WorkflowEvent(
                kind="search_progress",
                turn_id=turn_id,
                message=f"{len(evidence)}件の根拠候補を取得しました。",
                details={"source_count": len({item.document_id for item in evidence})},
            )
            self._raise_if_cancelled(cancel_event)

            packed = self._pack_evidence(evidence, budget)
            approximate_tokens = sum(max(1, len(item.excerpt) // 4) for item in packed)
            context_ratio = approximate_tokens / budget.max_evidence_tokens
            budget.record_context_ratio(context_ratio)
            safe_metadata: dict[str, str | int | float | list[str]] = {
                **trace_metadata.attributes(),
                "app.tool_count": budget.total_calls,
                "app.duplicate_query_count": budget.duplicate_query_count,
                "app.context_ratio": context_ratio,
                "app.flags_csv": ",".join(sorted(budget.flags)),
            }
            synthesizer = self._select_synthesizer(packed, normalized)
            draft = await synthesizer.synthesize(
                research_input=normalized,
                evidence=packed,
                safe_trace_metadata=safe_metadata,
            )
            sources = self._source_references(
                packed,
                evidence_ids={
                    evidence_id
                    for claim in draft.claims
                    for evidence_id in claim.evidence_ids
                },
            )
            check = verify_citations(
                answer_markdown=draft.answer_markdown,
                claims=draft.claims,
                evidence=packed,
                sources=sources,
            )
            if not check.valid:
                budget.flags.add("citation_repair")
                draft = await self._deterministic_synthesizer.synthesize(
                    research_input=normalized,
                    evidence=[item for item in packed if not item.retracted],
                    safe_trace_metadata=safe_metadata,
                )
                sources = self._source_references(
                    packed,
                    evidence_ids={
                        evidence_id
                        for claim in draft.claims
                        for evidence_id in claim.evidence_ids
                    },
                )
                check = verify_citations(
                    answer_markdown=draft.answer_markdown,
                    claims=draft.claims,
                    evidence=packed,
                    sources=sources,
                )
            if not check.valid:
                budget.flags.add("citation_verification_failed")
                draft = SynthesisDraft(
                    answer_markdown=(
                        "> 創薬仮説探索用であり、臨床判断や患者個別助言には使用できません。"
                        "\n\n引用検証を通過した主張を生成できませんでした。"
                    ),
                    claims=[],
                    limitations=[*draft.limitations, "Citation verification failed."],
                )
                sources = []

            manifest = RunManifest(
                turn_id=turn_id,
                conversation_id=conversation_id,
                agent_version=__version__,
                prompt_version=self._settings.prompt_version,
                corpus_version=self._settings.corpus_version,
                runtime_mode=self._settings.runtime_mode,
                tool_counts={kind.value: count for kind, count in budget.counts.items()},
                flags=sorted(budget.flags),
                citation_count=sum(len(claim.evidence_ids) for claim in draft.claims),
                source_count=len(sources),
                completed_at=datetime.now(UTC),
            )
            result = ResearchResult(
                answer_markdown=draft.answer_markdown,
                claims=draft.claims,
                sources=sources,
                limitations=draft.limitations,
                manifest=manifest,
            )
            output_classification = classify_trace_output(
                input_classification=input_classification,
                has_internal_evidence=any(
                    item.source_kind == SourceKind.INTERNAL for item in packed
                ),
            )
            final_attributes: dict[str, Any] = {
                "app.tool_count": budget.total_calls,
                "app.duplicate_query_count": budget.duplicate_query_count,
                "app.context_ratio": context_ratio,
                "app.finish_reason": "stop",
                "app.citation_count": manifest.citation_count,
                "app.source_count": manifest.source_count,
                "app.flags_csv": ",".join(manifest.flags),
                "app.input_data_classification": input_classification.value,
                "app.output_data_classification": output_classification.value,
                **trace_content_attributes(
                    input_enabled=self._settings.trace_input_content_enabled,
                    output_enabled=self._settings.trace_output_content_enabled,
                    input_classification=input_classification,
                    output_classification=output_classification,
                    question=question,
                    answer=result.answer_markdown,
                ),
            }
            set_safe_span_attributes(final_attributes)
            for delta in _chunk_text(result.answer_markdown):
                self._raise_if_cancelled(cancel_event)
                yield WorkflowEvent(kind="answer_delta", turn_id=turn_id, delta=delta)
                await asyncio.sleep(0)
            yield WorkflowEvent(kind="completed", turn_id=turn_id, result=result)

    async def _retrieve(
        self,
        *,
        normalized: Any,
        budget: ResearchBudget,
        turn_id: str,
        cancel_event: asyncio.Event,
    ) -> list[Evidence]:
        all_evidence: list[Evidence] = []
        known_documents: set[str] = set()
        for query in build_search_queries(normalized):
            self._raise_if_cancelled(cancel_event)
            tasks: list[asyncio.Task[list[Evidence]]] = []
            budget.consume(ToolKind.INTERNAL_SEARCH, {"query": query, "limit": 10})
            tasks.append(
                asyncio.create_task(self._search_internal(query=query, limit=10))
            )
            if self._exa and self._settings.live_exa_enabled:
                budget.consume(ToolKind.EXA_SEARCH, {"query": query, "num_results": 10})
                tasks.append(
                    asyncio.create_task(self._search_exa(query=query, num_results=10))
                )
            round_evidence: list[Evidence] = []
            for result in await asyncio.gather(*tasks):
                round_evidence.extend(result)
            if normalized.target_molecule:
                round_evidence = [
                    item
                    for item in round_evidence
                    if _contains_term(
                        f"{item.title}\n{item.excerpt}",
                        normalized.target_molecule,
                    )
                ]
            new_documents = {item.document_id for item in round_evidence} - known_documents
            known_documents.update(new_documents)
            all_evidence.extend(round_evidence)
            try:
                budget.record_progress(len(new_documents))
            except BudgetExceeded:
                break
        deduped = deduplicate_evidence(all_evidence)
        return [
            item.model_copy(update={"id": f"E{index}"})
            for index, item in enumerate(deduped, 1)
        ]

    async def _search_internal(self, *, query: str, limit: int) -> list[Evidence]:
        tracer = trace.get_tracer(__name__)
        with tracer.start_as_current_span("execute_tool internal_search"):
            set_safe_span_attributes(
                {
                    "gen_ai.operation.name": "execute_tool",
                    "gen_ai.agent.name": "deepresearch_agent",
                }
            )
            query_embedding = (await self._embeddings.embed([query]))[0]
            return await asyncio.to_thread(
                self._corpus.search,
                query=query,
                query_embedding=query_embedding,
                limit=limit,
            )

    async def _search_exa(self, *, query: str, num_results: int) -> list[Evidence]:
        if self._exa is None:
            return []
        tracer = trace.get_tracer(__name__)
        with tracer.start_as_current_span("execute_tool exa_search"):
            set_safe_span_attributes(
                {
                    "gen_ai.operation.name": "execute_tool",
                    "gen_ai.agent.name": "deepresearch_agent",
                }
            )
            return await self._exa.search_publications(query, num_results=num_results)

    @staticmethod
    def _pack_evidence(evidence: list[Evidence], budget: ResearchBudget) -> list[Evidence]:
        result: list[Evidence] = []
        per_document: dict[str, int] = {}
        token_count = 0
        for item in evidence:
            if len(result) >= budget.max_evidence:
                break
            if per_document.get(item.document_id, 0) >= budget.max_excerpts_per_document:
                continue
            clipped = item.excerpt[: budget.max_excerpt_chars]
            excerpt_tokens = max(1, len(clipped) // 4)
            if token_count + excerpt_tokens > budget.max_evidence_tokens:
                break
            result.append(item.model_copy(update={"excerpt": clipped}))
            per_document[item.document_id] = per_document.get(item.document_id, 0) + 1
            token_count += excerpt_tokens
        return result

    def _select_synthesizer(
        self, evidence: list[Evidence], research_input: Any
    ) -> Synthesizer:
        if not self._settings.live_gemini_enabled:
            return self._deterministic_synthesizer
        has_internal = any(item.source_kind == SourceKind.INTERNAL for item in evidence)
        has_public = any(item.source_kind == SourceKind.PUBLIC for item in evidence)
        if has_internal and not self._settings.allow_internal_content_to_gemini:
            return self._deterministic_synthesizer
        if has_public and not self._settings.allow_public_content_to_gemini:
            return self._deterministic_synthesizer
        if (
            (research_input.target_molecule or research_input.research_question)
            and not self._settings.allow_research_hypothesis_to_gemini
        ):
            return self._deterministic_synthesizer
        if self._adk_synthesizer is None:
            self._adk_synthesizer = AdkSynthesizer(model=self._settings.model)
        return self._adk_synthesizer

    @staticmethod
    def _source_references(
        evidence: list[Evidence],
        *,
        evidence_ids: set[str],
    ) -> list[SourceReference]:
        return [
            SourceReference(
                evidence_id=item.id,
                document_id=item.document_id,
                title=item.title,
                source_kind=item.source_kind,
                url=item.canonical_url,
                doi=item.doi,
                pmid=item.pmid,
            )
            for item in evidence
            if item.id in evidence_ids
        ]

    @staticmethod
    def _raise_if_cancelled(cancel_event: asyncio.Event) -> None:
        if cancel_event.is_set():
            raise asyncio.CancelledError


def _chunk_text(text: str, size: int = 240) -> list[str]:
    return [text[index : index + size] for index in range(0, len(text), size)]


def _contains_term(text: str, term: str) -> bool:
    return " ".join(term.casefold().split()) in " ".join(text.casefold().split())
