from __future__ import annotations

import argparse
import asyncio
import json
from collections import defaultdict
from collections.abc import Sequence
from pathlib import Path
from statistics import fmean
from tempfile import TemporaryDirectory
from typing import Any

from pydantic import HttpUrl

from deepresearch_agent import __version__
from deepresearch_agent.application.workflow import ResearchWorkflow
from deepresearch_agent.domain.models import Chunk, Document, SourceKind
from deepresearch_agent.evaluation.gates import (
    EvaluationGateSummary,
    EvaluationVersions,
    GateStatus,
    build_gate_summary,
    nearest_rank_percentile,
)
from deepresearch_agent.evaluation.scorers import (
    citation_coverage_score,
    citation_registry_integrity_score,
    citation_resolvability_score,
    claim_evidence_entailment_score,
    contradiction_handling_score,
    disease_scope_score,
    evidence_stage_calibration_score,
    frustration_metrics,
    multi_turn_retention_score,
    ndcg_at_k,
    recall_at_k,
    retrieved_before_cited_score,
    schema_score,
    tool_policy_score,
    truncation_score,
)
from deepresearch_agent.evaluation.workflow_adapter import (
    EvaluationTurn,
    WorkflowCaseObservation,
    WorkflowEvaluationAdapter,
)
from deepresearch_agent.infrastructure.corpus import CorpusRepository
from deepresearch_agent.infrastructure.embeddings import HashEmbeddingProvider
from deepresearch_agent.infrastructure.sessions import AdkSessionStateStore
from deepresearch_agent.settings import Settings

FIXTURE_DIRECTORY = Path(__file__).with_name("fixtures") / "v1"
SCORER_VERSION = "v2"


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def _load_fixture_bundle(
    fixtures: Path,
) -> tuple[dict[str, Any], dict[str, list[dict[str, Any]]]]:
    manifest = json.loads((fixtures / "manifest.json").read_text(encoding="utf-8"))
    if manifest.get("scientific_gold") and not manifest.get("sme_reviewed"):
        raise ValueError("Scientific-gold fixtures require SME review")
    if manifest.get("release_gate_eligible") and not (
        manifest.get("scientific_gold") and manifest.get("sme_reviewed")
    ):
        raise ValueError(
            "Release-gate eligibility requires scientific gold and SME review"
        )
    datasets = {
        name: load_jsonl(fixtures / filename)
        for name, filename in {
            "retrieval": "retrieval.jsonl",
            "synthesis": "synthesis.jsonl",
            "multi_turn_behavior": "multi_turn_behavior.jsonl",
            "frustration": "frustration.jsonl",
        }.items()
    }
    expected_counts = manifest.get("counts", {})
    actual_counts = {name: len(rows) for name, rows in datasets.items()}
    if actual_counts != expected_counts:
        raise ValueError(
            f"Fixture counts do not match manifest: expected={expected_counts}, "
            f"actual={actual_counts}"
        )
    return manifest, datasets


def evaluate_fixtures(fixtures: Path) -> dict[str, Any]:
    """Validate fixture integrity only; this result is never a release decision."""

    manifest, datasets = _load_fixture_bundle(fixtures)
    retrieval = datasets["retrieval"]
    recalls = [
        recall_at_k(row["retrieved_document_ids"], row["relevant_document_ids"], k=10)
        for row in retrieval
    ]
    ndcgs = [
        ndcg_at_k(
            row["retrieved_document_ids"],
            {document_id: 1.0 for document_id in row["relevant_document_ids"]},
            k=10,
        )
        for row in retrieval
    ]
    frustration = datasets["frustration"]
    return {
        "fixture_version": manifest["version"],
        "scientific_gold": bool(manifest.get("scientific_gold", False)),
        "sme_reviewed": bool(manifest.get("sme_reviewed", False)),
        "release_gate_eligible": False,
        "counts": {name: len(rows) for name, rows in datasets.items()},
        "recorded_mean_recall_at_10": fmean(recalls),
        "recorded_mean_ndcg_at_10": fmean(ndcgs),
        "frustration_positive": sum(bool(row["label"]) for row in frustration),
        "frustration_hard_negative": sum(
            bool(row["hard_negative"]) for row in frustration
        ),
        "release_decision": "not_evaluated",
    }


async def evaluate_workflow_fixtures(
    fixtures: Path = FIXTURE_DIRECTORY,
) -> EvaluationGateSummary:
    manifest, datasets = _load_fixture_bundle(fixtures)
    with TemporaryDirectory(prefix="deepresearch-eval-") as temporary:
        root = Path(temporary)
        settings = Settings(
            database_path=root / "corpus.sqlite",
            session_database_path=root / "sessions.sqlite",
            corpus_version=f"synthetic-eval-{manifest['version']}",
            runtime_mode="mock",
        )
        corpus = CorpusRepository(settings.database_path)
        corpus.initialize()
        embeddings = HashEmbeddingProvider()
        await _seed_synthetic_corpus(
            corpus=corpus,
            embeddings=embeddings,
            retrieval_rows=datasets["retrieval"],
            snapshot_id=settings.corpus_version,
        )
        sessions = AdkSessionStateStore(settings.session_database_path)
        workflow = ResearchWorkflow(
            settings=settings,
            corpus=corpus,
            embeddings=embeddings,
            sessions=sessions,
        )
        adapter = WorkflowEvaluationAdapter(workflow=workflow, sessions=sessions)
        try:
            return await _execute_suites(
                adapter=adapter,
                settings=settings,
                embedding_model=embeddings.model_name,
                manifest=manifest,
                datasets=datasets,
            )
        finally:
            await workflow.close()


async def _seed_synthetic_corpus(
    *,
    corpus: CorpusRepository,
    embeddings: HashEmbeddingProvider,
    retrieval_rows: Sequence[dict[str, Any]],
    snapshot_id: str,
) -> None:
    queries_by_document: dict[str, set[str]] = defaultdict(set)
    distractors: set[str] = set()
    for row in retrieval_rows:
        for document_id in row["relevant_document_ids"]:
            queries_by_document[str(document_id)].add(str(row["query"]))
        distractors.update(str(item) for item in row["retrieved_document_ids"])
    distractors.difference_update(queries_by_document)
    queries_by_document["D-MMP9"].add(
        "ischemic stroke MMP9 inhibition clinical translation contradictory evidence"
    )

    corpus.save_snapshot(
        snapshot_id,
        "synthetic",
        {
            "synthetic": True,
            "embedding_model": embeddings.model_name,
            "embedding_dimension": embeddings.dimension,
        },
    )
    for document_id, queries in sorted(queries_by_document.items()):
        text = (
            " ".join(sorted(queries))
            + " Synthetic public evidence for deterministic workflow evaluation."
        )
        embedding = (await embeddings.embed([text]))[0]
        corpus.upsert_document(
            Document(
                id=document_id,
                title=f"Synthetic ischemic stroke evidence {document_id}",
                abstract=text,
                canonical_url=HttpUrl(f"https://example.org/{document_id}"),
                source_kind=SourceKind.PUBLIC,
                access_class="public",
                provenance=["synthetic:evaluation"],
            ),
            [
                Chunk(
                    id=f"{document_id}:chunk:0",
                    document_id=document_id,
                    ordinal=0,
                    text=text,
                    token_count=max(1, len(text) // 4),
                    embedding=embedding,
                )
            ],
        )
    for document_id in sorted(distractors):
        text = f"Synthetic unrelated control document {document_id}."
        embedding = (await embeddings.embed([text]))[0]
        corpus.upsert_document(
            Document(
                id=document_id,
                title=f"Synthetic control {document_id}",
                abstract=text,
                canonical_url=HttpUrl(f"https://example.org/{document_id}"),
                source_kind=SourceKind.PUBLIC,
                access_class="public",
                provenance=["synthetic:evaluation"],
            ),
            [
                Chunk(
                    id=f"{document_id}:chunk:0",
                    document_id=document_id,
                    ordinal=0,
                    text=text,
                    token_count=max(1, len(text) // 4),
                    embedding=embedding,
                )
            ],
        )


async def _execute_suites(
    *,
    adapter: WorkflowEvaluationAdapter,
    settings: Settings,
    embedding_model: str,
    manifest: dict[str, Any],
    datasets: dict[str, list[dict[str, Any]]],
) -> EvaluationGateSummary:
    retrieval_observations: list[WorkflowCaseObservation] = []
    retrieval_scores: list[float] = []
    ndcg_scores: list[float] = []
    for row in datasets["retrieval"]:
        observation = await adapter.run_case(
            case_id=str(row["id"]),
            turns=[
                EvaluationTurn(
                    text=str(row["query"]),
                    research_question=str(row["query"]),
                )
            ],
        )
        retrieval_observations.append(observation)
        retrieved_ids = [item.document_id for item in observation.retrieved_evidence]
        relevant_ids = [str(item) for item in row["relevant_document_ids"]]
        retrieval_scores.append(recall_at_k(retrieved_ids, relevant_ids, k=10))
        ndcg_scores.append(
            ndcg_at_k(
                retrieved_ids,
                {document_id: 1.0 for document_id in relevant_ids},
                k=10,
            )
        )

    synthesis_observations = retrieval_observations[: len(datasets["synthesis"])]
    multi_observations: list[tuple[WorkflowCaseObservation, dict[str, Any]]] = []
    for row in datasets["multi_turn_behavior"]:
        observation = await adapter.run_case(
            case_id=str(row["id"]),
            turns=[EvaluationTurn.model_validate(turn) for turn in row["turns"]],
        )
        multi_observations.append((observation, dict(row["expected_state"])))

    metric_samples: dict[str, list[float]] = defaultdict(list)
    incident_counts = {
        "fabricated_citations": 0,
        "citation_registry_mismatches": 0,
        "unsupported_claims": 0,
        "retracted_positive_uses": 0,
        "scope_violations": 0,
        "tool_loops": 0,
        "truncations": 0,
        "retrieved_before_cited_violations": 0,
    }
    all_observations = [*retrieval_observations, *(item[0] for item in multi_observations)]
    for observation in all_observations:
        _score_observation(
            observation=observation,
            metric_samples=metric_samples,
            incident_counts=incident_counts,
        )
    for observation, expected_state in multi_observations:
        metric_samples["multi_turn_retention"].append(
            float(
                multi_turn_retention_score(
                    expected_state,
                    observation.final_state,
                )["score"]
            )
        )
    for observation, specification in zip(
        synthesis_observations,
        datasets["synthesis"],
        strict=True,
    ):
        sections_present = all(
            f"## {section}" in observation.result.answer_markdown
            for section in specification["expected_sections"]
        )
        metric_samples["schema_validity"].append(float(sections_present))

    frustration_rows = datasets["frustration"]
    labels = [bool(row["label"]) for row in frustration_rows]
    predictions = [
        _synthetic_frustration_prediction(str(row["text"]))
        for row in frustration_rows
    ]
    classification = frustration_metrics(labels, predictions)
    metric_samples["frustration_precision"].append(classification.precision)
    metric_samples["frustration_recall"].append(classification.recall)

    context_ratios = [
        observation.result.manifest.context_ratio for observation in all_observations
    ]
    observed_metrics = {
        name: (fmean(values), len(values))
        for name, values in metric_samples.items()
        if values
    }
    observed_metrics["recall_at_10"] = (
        fmean(retrieval_scores),
        len(retrieval_scores),
    )
    observed_metrics["ndcg_at_10"] = (fmean(ndcg_scores), len(ndcg_scores))
    observed_metrics["context_ratio_p95"] = (
        nearest_rank_percentile(context_ratios, 0.95),
        len(context_ratios),
    )
    observed_metrics["latency_p95_ms"] = (
        nearest_rank_percentile(
            [observation.latency_ms for observation in all_observations],
            0.95,
        ),
        len(all_observations),
    )

    return build_gate_summary(
        versions=EvaluationVersions(
            agent=__version__,
            model="deterministic-synthesizer-v1",
            prompt=settings.prompt_version,
            corpus=settings.corpus_version,
            embedding=embedding_model,
            dataset=str(manifest["version"]),
            scorer=SCORER_VERSION,
        ),
        observed_metrics=observed_metrics,
        incidents=incident_counts,
        scientific_gold=bool(manifest.get("scientific_gold", False)),
        sme_reviewed=bool(manifest.get("sme_reviewed", False)),
        release_gate_eligible=bool(manifest.get("release_gate_eligible", False)),
        human_review_passed=None,
    )


def _score_observation(
    *,
    observation: WorkflowCaseObservation,
    metric_samples: dict[str, list[float]],
    incident_counts: dict[str, int],
) -> None:
    result = observation.result
    output = result.model_dump(mode="json")
    sources = [source.model_dump(mode="json") for source in result.sources]
    claims = [claim.model_dump(mode="json") for claim in result.claims]
    evidence = [item.model_dump(mode="json") for item in observation.packed_evidence]
    source_ids = [source.evidence_id for source in result.sources]
    cited_ids = [
        evidence_id for claim in result.claims for evidence_id in claim.evidence_ids
    ]
    packed_ids = [item.id for item in observation.packed_evidence]

    schema = schema_score(output)
    scope = disease_scope_score("ischemic stroke")
    tool = tool_policy_score(result.manifest.tool_counts, result.manifest.flags)
    resolvability = citation_resolvability_score(
        result.answer_markdown,
        source_ids,
    )
    registry = citation_registry_integrity_score(
        result.answer_markdown,
        claims,
        sources,
    )
    coverage = citation_coverage_score(claims)
    entailment = claim_evidence_entailment_score(claims, evidence)
    retrieved = retrieved_before_cited_score(cited_ids, packed_ids)
    truncation = truncation_score(
        result.manifest.finish_reason,
        result.manifest.flags,
    )
    evidence_by_id = {item.id: item for item in observation.packed_evidence}
    stage_scores = [
        evidence_stage_calibration_score(
            claim.text,
            [
                evidence_by_id[evidence_id].evidence_stage.value
                for evidence_id in claim.evidence_ids
                if evidence_id in evidence_by_id
            ],
        )
        for claim in result.claims
    ]
    conflict = contradiction_handling_score(
        result.answer_markdown,
        [item.support_level.value for item in observation.packed_evidence],
    )

    metric_samples["schema_validity"].append(float(schema["score"]))
    metric_samples["disease_scope"].append(float(scope["score"]))
    metric_samples["tool_policy"].append(float(tool["score"]))
    metric_samples["citation_resolvability"].append(float(resolvability["score"]))
    metric_samples["retrieved_before_cited"].append(float(retrieved["score"]))
    metric_samples["claim_citation_coverage"].append(float(coverage["score"]))
    metric_samples["claim_evidence_entailment"].append(float(entailment["score"]))
    metric_samples["evidence_stage_calibration"].append(
        fmean(float(item["score"]) for item in stage_scores) if stage_scores else 1.0
    )
    metric_samples["conflict_handling"].append(float(conflict["score"]))
    metric_samples["source_status"].append(
        float(entailment["retracted_positive_uses"] == 0)
    )
    metric_samples["truncation"].append(float(truncation["score"]))

    incident_counts["fabricated_citations"] += len(resolvability["unresolved"])
    incident_counts["citation_registry_mismatches"] += int(not registry["passed"])
    incident_counts["unsupported_claims"] += len(
        entailment["unsupported_claim_indexes"]
    )
    incident_counts["retracted_positive_uses"] += int(
        entailment["retracted_positive_uses"]
    )
    incident_counts["scope_violations"] += int(not scope["passed"])
    incident_counts["tool_loops"] += int(
        "duplicate_query_loop" in result.manifest.flags
        or "search_budget_exceeded" in result.manifest.flags
    )
    incident_counts["truncations"] += int(not truncation["passed"])
    incident_counts["retrieved_before_cited_violations"] += len(
        retrieved["violations"]
    )


def _synthetic_frustration_prediction(text: str) -> bool:
    normalized = text.casefold()
    return any(
        marker in normalized
        for marker in (
            "ignored my question",
            "third time",
            "still has no supporting citation",
            "keep getting the same irrelevant",
            "not what i asked",
        )
    )


async def _async_main() -> None:
    parser = argparse.ArgumentParser(
        description="Run actual deterministic workflow release-gate evaluation."
    )
    parser.add_argument("--fixtures", type=Path, default=FIXTURE_DIRECTORY)
    parser.add_argument(
        "--require-scientific-release",
        action="store_true",
        help="Exit non-zero unless the SME/human-reviewed scientific gate passes.",
    )
    args = parser.parse_args()
    try:
        summary = await evaluate_workflow_fixtures(args.fixtures)
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc
    print(json.dumps(summary.model_dump(mode="json"), indent=2))
    if summary.technical_smoke_status != GateStatus.PASSED:
        raise SystemExit(1)
    if (
        args.require_scientific_release
        and summary.scientific_release_status != GateStatus.PASSED
    ):
        raise SystemExit(2)


def main() -> None:
    asyncio.run(_async_main())
