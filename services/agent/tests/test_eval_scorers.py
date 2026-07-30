from __future__ import annotations

import pytest

from deepresearch_agent.evaluation.gates import (
    MAXIMUM_THRESHOLDS,
    MINIMUM_THRESHOLDS,
    ZERO_INCIDENT_KEYS,
    EvaluationVersions,
    GateStatus,
    build_gate_summary,
    nearest_rank_percentile,
)
from deepresearch_agent.evaluation.runner import (
    FIXTURE_DIRECTORY,
    evaluate_fixtures,
    evaluate_workflow_fixtures,
)
from deepresearch_agent.evaluation.scorers import (
    citation_coverage_score,
    citation_registry_integrity_score,
    citation_resolvability_score,
    claim_evidence_entailment_score,
    frustration_metrics,
    ndcg_at_k,
    recall_at_k,
    release_gate,
    retrieved_before_cited_score,
    tool_policy_score,
)


def test_retrieval_metrics_and_citation_scores() -> None:
    assert recall_at_k(["a", "x", "b"], {"a", "b"}, k=3) == 1.0
    assert ndcg_at_k(["a", "b"], {"a": 3, "b": 1}, k=2) == pytest.approx(1.0)
    assert citation_resolvability_score("Claim [E1] [E9]", ["E1"])["unresolved"] == [
        "E9"
    ]
    assert citation_coverage_score(
        [{"evidence_ids": ["E1"]}, {"evidence_ids": []}]
    )["score"] == 0.5
    assert retrieved_before_cited_score(["E1", "E9"], ["E1"])["violations"] == [
        "E9"
    ]
    assert citation_registry_integrity_score(
        "Alpha finding [E2]\nBeta finding [E2]",
        [
            {
                "text": "Alpha finding",
                "evidence_ids": ["E1"],
                "support_level": "background",
            },
            {
                "text": "Beta finding",
                "evidence_ids": ["E2"],
                "support_level": "background",
            },
        ],
        [{"evidence_id": "E1"}, {"evidence_id": "E2"}],
    ) == {
        "passed": False,
        "score": 0.0,
        "mismatched_ids": ["E1"],
        "claim_mapping_mismatch_indexes": [0],
        "duplicate_source_ids": 0,
    }


def test_claim_evidence_entailment_and_retraction_score() -> None:
    result = claim_evidence_entailment_score(
        [
            {
                "text": "MMP9 supports the synthetic finding",
                "evidence_ids": ["E1"],
                "support_level": "supports",
            }
        ],
        [
            {
                "id": "E1",
                "document_id": "d1",
                "source_kind": "public",
                "title": "MMP9 synthetic finding",
                "excerpt": "MMP9 supports the synthetic finding.",
                "support_level": "supports",
                "retracted": True,
            }
        ],
    )

    assert not result["passed"]
    assert result["score"] == 1.0
    assert result["unsupported_claim_indexes"] == []
    assert result["retracted_positive_uses"] == 1


def test_policy_frustration_and_release_gate() -> None:
    assert tool_policy_score(
        {"internal_search": 2, "exa_search": 2, "contents": 1, "metadata": 1},
        [],
    )["passed"]
    metrics = frustration_metrics(
        [True, True, False, False],
        [True, False, True, False],
    )
    assert metrics.precision == 0.5
    assert metrics.recall == 0.5
    gate = release_gate(
        {
            **{key: 0 for key in ZERO_INCIDENT_KEYS},
            "citation_resolvability": 1.0,
            "retrieved_before_cited": 1.0,
            "claim_citation_coverage": 0.95,
            "entailment": 0.90,
            "recall_at_10": 0.80,
            "ndcg_at_10": 0.75,
            "context_ratio_p95": 0.79,
            "frustration_precision": 0.80,
            "frustration_recall": 0.85,
        }
    )
    assert gate == {
        "passed": True,
        "failures": [],
        "missing_required_metrics": [],
    }
    missing = release_gate({"citation_resolvability": 1.0})
    assert not missing["passed"]
    assert "retrieved_before_cited" in missing["missing_required_metrics"]


def test_versioned_fixture_manifest_and_metrics() -> None:
    summary = evaluate_fixtures(FIXTURE_DIRECTORY)
    assert summary["counts"] == {
        "retrieval": 36,
        "synthesis": 24,
        "multi_turn_behavior": 18,
        "frustration": 100,
    }
    assert summary["recorded_mean_recall_at_10"] >= 0.80
    assert summary["recorded_mean_ndcg_at_10"] >= 0.75
    assert summary["frustration_positive"] == 50
    assert summary["frustration_hard_negative"] == 50
    assert not summary["release_gate_eligible"]
    assert summary["release_decision"] == "not_evaluated"


def _passing_observed_metrics() -> dict[str, tuple[float, int]]:
    values = {
        name: (minimum, 1)
        for name, minimum in MINIMUM_THRESHOLDS.items()
    }
    values.update(
        {
            name: (maximum - 0.001, 1)
            for name, maximum in MAXIMUM_THRESHOLDS.items()
        }
    )
    return values


def _versions() -> EvaluationVersions:
    return EvaluationVersions(
        agent="test-agent",
        model="test-model",
        prompt="test-prompt",
        corpus="test-corpus",
        embedding="test-embedding",
        dataset="test-dataset",
        scorer="test-scorer",
    )


def test_release_summary_fails_closed_and_separates_review_sources() -> None:
    incidents = {key: 0 for key in ZERO_INCIDENT_KEYS}
    technical_only = build_gate_summary(
        versions=_versions(),
        observed_metrics=_passing_observed_metrics(),
        incidents=incidents,
        scientific_gold=False,
        sme_reviewed=False,
        release_gate_eligible=False,
        review_metrics={"advisory_llm_quality": (1.0, 5, "llm_judge")},
    )
    assert technical_only.technical_smoke_status == GateStatus.PASSED
    assert technical_only.scientific_release_status == GateStatus.INELIGIBLE
    assert technical_only.metrics["advisory_llm_quality"].source == "llm_judge"

    missing_metrics = _passing_observed_metrics()
    missing_metrics.pop("retrieved_before_cited")
    missing = build_gate_summary(
        versions=_versions(),
        observed_metrics=missing_metrics,
        incidents=incidents,
        scientific_gold=True,
        sme_reviewed=True,
        release_gate_eligible=True,
        human_review_passed=True,
    )
    assert missing.technical_smoke_status == GateStatus.FAILED
    assert missing.missing_required_metrics == ["retrieved_before_cited"]
    assert missing.scientific_release_status == GateStatus.FAILED

    no_human = build_gate_summary(
        versions=_versions(),
        observed_metrics=_passing_observed_metrics(),
        incidents=incidents,
        scientific_gold=True,
        sme_reviewed=True,
        release_gate_eligible=True,
        review_metrics={"advisory_llm_quality": (1.0, 5, "llm_judge")},
    )
    assert no_human.scientific_release_status == GateStatus.INELIGIBLE


def test_threshold_boundaries_and_nearest_rank_p95_are_fixed() -> None:
    assert nearest_rank_percentile([0.1, 0.2, 0.3, 0.4], 0.95) == 0.4
    incidents = {key: 0 for key in ZERO_INCIDENT_KEYS}
    boundary = _passing_observed_metrics()
    boundary["context_ratio_p95"] = (0.80, 20)
    summary = build_gate_summary(
        versions=_versions(),
        observed_metrics=boundary,
        incidents=incidents,
        scientific_gold=True,
        sme_reviewed=True,
        release_gate_eligible=True,
        human_review_passed=True,
    )
    assert summary.technical_smoke_status == GateStatus.FAILED
    assert not summary.metrics["context_ratio_p95"].passed


@pytest.mark.asyncio
async def test_actual_workflow_evaluation_passes_technical_demo_only() -> None:
    summary = await evaluate_workflow_fixtures()

    assert not summary.missing_required_metrics
    assert summary.metrics["recall_at_10"].sample_count == 36
    assert summary.metrics["recall_at_10"].value == 1.0
    assert summary.metrics["ndcg_at_10"].value == 1.0
    assert summary.metrics["schema_validity"].sample_count > 24
    assert summary.metrics["multi_turn_retention"].sample_count == 18
    assert summary.metrics["retrieved_before_cited"].value == 1.0
    assert summary.technical_smoke_status == GateStatus.PASSED
    assert summary.scientific_release_status == GateStatus.INELIGIBLE
    assert "dataset_not_sme_reviewed" in summary.scientific_release_reasons
    assert "technical_gate_failed" not in summary.scientific_release_reasons
