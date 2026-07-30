from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Callable
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
    VerificationStatus,
    WorkflowEvent,
)
from deepresearch_agent.infrastructure.corpus import CorpusRepository
from deepresearch_agent.infrastructure.embeddings import EmbeddingProvider
from deepresearch_agent.infrastructure.exa import ExaAdapterError, ExaSearchClient
from deepresearch_agent.infrastructure.publication_metadata import (
    MetadataVerificationError,
    PublicationMetadataVerifier,
)
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
        metadata_verifier: PublicationMetadataVerifier | None = None,
        deterministic_synthesizer: Synthesizer | None = None,
    ) -> None:
        self._settings = settings
        self._corpus = corpus
        self._embeddings = embeddings
        self._sessions = sessions
        self._exa = exa
        self._metadata_verifier = metadata_verifier
        self._deterministic_synthesizer = (
            deterministic_synthesizer or DeterministicSynthesizer()
        )
        self._adk_synthesizer: Synthesizer | None = None

    async def close(self) -> None:
        if self._exa:
            await self._exa.close()
        if self._metadata_verifier:
            await self._metadata_verifier.close()

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
        _evaluation_capture: (
            Callable[
                [tuple[Evidence, ...], tuple[Evidence, ...], ResearchResult],
                None,
            ]
            | None
        ) = None,
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

            draft = self._apply_retrieval_limitations(draft, budget)
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
                context_ratio=context_ratio,
                completed_at=datetime.now(UTC),
            )
            result = ResearchResult(
                answer_markdown=draft.answer_markdown,
                claims=draft.claims,
                sources=sources,
                limitations=draft.limitations,
                manifest=manifest,
            )
            if _evaluation_capture is not None:
                _evaluation_capture(tuple(evidence), tuple(packed), result)
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
            budget.consume(ToolKind.INTERNAL_SEARCH, {"query": query, "limit": 10})
            async with asyncio.TaskGroup() as task_group:
                internal_task = task_group.create_task(
                    self._search_internal(query=query, limit=10)
                )
                exa_task: asyncio.Task[list[Evidence]] | None = None
                if self._exa and self._settings.live_exa_enabled:
                    exa_task = task_group.create_task(
                        self._search_exa_with_retry(
                            query=query,
                            num_results=10,
                            budget=budget,
                            cancel_event=cancel_event,
                        )
                    )
            round_evidence = await internal_task
            if exa_task:
                round_evidence.extend(await exa_task)
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
        deduped = await self._verify_publication_metadata(
            evidence=deduped,
            budget=budget,
        )
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

    async def _search_exa_with_retry(
        self,
        *,
        query: str,
        num_results: int,
        budget: ResearchBudget,
        cancel_event: asyncio.Event,
    ) -> list[Evidence]:
        for attempt in range(2):
            self._raise_if_cancelled(cancel_event)
            try:
                budget.consume(
                    ToolKind.EXA_SEARCH,
                    {"query": query, "num_results": num_results},
                )
            except BudgetExceeded:
                budget.flags.add("exa_budget_exhausted")
                return []
            try:
                return await self._search_exa(
                    query=query,
                    num_results=num_results,
                )
            except ExaAdapterError as exc:
                budget.flags.add("exa_partial_failure")
                budget.flags.add(f"exa_{exc.kind.value}")
                if not exc.retryable or attempt == 1:
                    return []
                await asyncio.sleep(
                    self._settings.exa_retry_backoff_seconds * (2**attempt)
                )
            except Exception:
                budget.flags.add("exa_partial_failure")
                budget.flags.add("exa_unexpected")
                return []
        return []

    async def _verify_publication_metadata(
        self,
        *,
        evidence: list[Evidence],
        budget: ResearchBudget,
    ) -> list[Evidence]:
        exa_evidence = [item for item in evidence if "exa:search" in item.provenance]
        verifiable = [item for item in exa_evidence if item.doi or item.pmid]
        if not exa_evidence:
            return evidence
        if not verifiable:
            budget.flags.add("metadata_unverified")
            return evidence
        if self._metadata_verifier is None:
            budget.flags.add("metadata_unverified")
            return evidence
        try:
            budget.consume(
                ToolKind.METADATA,
                {
                    "evidence_count": len(verifiable),
                    "provider": "europe_pmc",
                },
            )
            verified_public = await self._metadata_verifier.verify(exa_evidence)
        except (BudgetExceeded, MetadataVerificationError):
            budget.flags.add("metadata_verification_failed")
            return [
                item.model_copy(
                    update={"verification_status": VerificationStatus.FAILED}
                )
                if "exa:search" in item.provenance and (item.doi or item.pmid)
                else item
                for item in evidence
            ]
        verified_by_id = {item.id: item for item in verified_public}
        return [verified_by_id.get(item.id, item) for item in evidence]

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
                evidence_stage=item.evidence_stage,
                verification_status=item.verification_status,
                publication_status=item.publication_status,
            )
            for item in evidence
            if item.id in evidence_ids
        ]

    @staticmethod
    def _apply_retrieval_limitations(
        draft: SynthesisDraft,
        budget: ResearchBudget,
    ) -> SynthesisDraft:
        messages: list[str] = []
        limitations = list(draft.limitations)
        if "exa_partial_failure" in budget.flags:
            messages.append(
                "外部文献検索は一部失敗したため、取得済みの根拠のみで回答しました。"
            )
            limitations.append("External publication search partially failed.")
        if "metadata_verification_failed" in budget.flags:
            messages.append(
                "公開文献の書誌・撤回情報を検証できず、該当根拠を未検証として扱いました。"
            )
            limitations.append("Publication metadata verification failed.")
        elif "metadata_unverified" in budget.flags:
            messages.append(
                "識別子または検証サービスがない公開文献は未検証として表示しています。"
            )
            limitations.append("Some publication metadata remains unverified.")
        if not messages:
            return draft
        return draft.model_copy(
            update={
                "answer_markdown": (
                    f"{draft.answer_markdown}\n\n## 検索・検証上の追加制約\n\n"
                    + "\n".join(f"- {message}" for message in messages)
                ),
                "limitations": limitations,
            }
        )

    @staticmethod
    def _raise_if_cancelled(cancel_event: asyncio.Event) -> None:
        if cancel_event.is_set():
            raise asyncio.CancelledError


def _chunk_text(text: str, size: int = 240) -> list[str]:
    return [text[index : index + size] for index in range(0, len(text), size)]


def _contains_term(text: str, term: str) -> bool:
    return " ".join(term.casefold().split()) in " ".join(text.casefold().split())
