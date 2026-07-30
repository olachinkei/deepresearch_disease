from __future__ import annotations

import pytest

from deepresearch_agent.evaluation.runner import FIXTURE_DIRECTORY, evaluate_fixtures
from deepresearch_agent.evaluation.scorers import (
    citation_coverage_score,
    citation_registry_integrity_score,
    citation_resolvability_score,
    claim_evidence_entailment_score,
    frustration_metrics,
    ndcg_at_k,
    recall_at_k,
    release_gate,
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
            "citation_resolvability": 1.0,
            "claim_citation_coverage": 0.95,
            "entailment": 0.90,
            "recall_at_10": 0.80,
            "ndcg_at_10": 0.75,
            "context_ratio_p95": 0.79,
            "frustration_precision": 0.80,
            "frustration_recall": 0.85,
        }
    )
    assert gate == {"passed": True, "failures": []}


def test_versioned_fixture_manifest_and_metrics() -> None:
    summary = evaluate_fixtures(FIXTURE_DIRECTORY)
    assert summary["counts"] == {
        "retrieval": 36,
        "synthesis": 24,
        "multi_turn_behavior": 18,
        "frustration": 100,
    }
    assert summary["mean_recall_at_10"] >= 0.80
    assert summary["mean_ndcg_at_10"] >= 0.75
    assert summary["frustration_positive"] == 50
    assert summary["frustration_hard_negative"] == 50
    assert not summary["release_gate_eligible"]
