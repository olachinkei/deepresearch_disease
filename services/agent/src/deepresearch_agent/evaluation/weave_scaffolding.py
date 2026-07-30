from __future__ import annotations

from collections.abc import Callable
from typing import Any

from deepresearch_agent.evaluation.scorers import (
    citation_coverage_score,
    citation_registry_integrity_score,
    citation_resolvability_score,
    claim_evidence_entailment_score,
    contradiction_handling_score,
    disease_scope_score,
    retrieved_before_cited_score,
    schema_score,
    tool_policy_score,
    truncation_score,
)


def build_weave_evaluation(
    *,
    name: str,
    rows: list[dict[str, Any]],
    model: Callable[..., Any],
) -> object:
    """Create, but do not run, a versionable Weave Dataset/Evaluation."""

    import weave

    dataset = weave.Dataset(name=f"{name}-dataset", rows=rows)  # type: ignore[arg-type]

    @weave.op(name="disease_scope")
    def disease_scope_scorer(disease: str, output: dict[str, Any]) -> dict[str, Any]:
        del output
        return disease_scope_score(disease)

    @weave.op(name="tool_policy")
    def tool_policy_scorer(output: dict[str, Any]) -> dict[str, Any]:
        manifest = output.get("manifest", {})
        return tool_policy_score(
            manifest.get("tool_counts", {}),
            manifest.get("flags", []),
        )

    @weave.op(name="citation_quality")
    def citation_scorer(output: dict[str, Any]) -> dict[str, Any]:
        sources = output.get("sources", [])
        result = citation_resolvability_score(
            output.get("answer_markdown", ""),
            [source.get("evidence_id", "") for source in sources],
        )
        coverage = citation_coverage_score(output.get("claims", []))
        registry = citation_registry_integrity_score(
            output.get("answer_markdown", ""),
            output.get("claims", []),
            sources,
        )
        entailment = claim_evidence_entailment_score(
            output.get("claims", []),
            output.get("evidence", []),
        )
        cited_ids = [
            evidence_id
            for claim in output.get("claims", [])
            for evidence_id in claim.get("evidence_ids", [])
        ]
        retrieved = retrieved_before_cited_score(
            cited_ids,
            [item.get("id", "") for item in output.get("evidence", [])],
        )
        return {
            "passed": (
                result["passed"]
                and coverage["passed"]
                and registry["passed"]
                and entailment["passed"]
                and retrieved["passed"]
            ),
            "resolvability": result["score"],
            "coverage": coverage["score"],
            "registry_integrity": registry["score"],
            "entailment": entailment["score"],
            "retracted_positive_uses": entailment["retracted_positive_uses"],
            "retrieved_before_cited": retrieved["score"],
        }

    @weave.op(name="output_safety")
    def output_safety_scorer(output: dict[str, Any]) -> dict[str, Any]:
        manifest = output.get("manifest", {})
        schema = schema_score(output)
        truncation = truncation_score(
            manifest.get("finish_reason"),
            manifest.get("flags", []),
        )
        conflict = contradiction_handling_score(
            output.get("answer_markdown", ""),
            [
                item.get("support_level", "unknown")
                for item in output.get("evidence", [])
            ],
        )
        return {
            "passed": schema["passed"] and truncation["passed"] and conflict["passed"],
            "schema": schema["score"],
            "truncation": truncation["score"],
            "conflict_handling": conflict["score"],
        }

    return weave.Evaluation(
        name=name,
        dataset=dataset,
        scorers=[
            disease_scope_scorer,
            tool_policy_scorer,
            citation_scorer,
            output_safety_scorer,
        ],
        metadata={
            "fixture_status": "synthetic_unreviewed",
            "version": "v1",
            "scorer_version": "v2",
            "scientific_release_eligible": False,
        },
    ), model
