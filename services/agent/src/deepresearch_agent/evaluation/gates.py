from __future__ import annotations

import math
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class GateStatus(StrEnum):
    PASSED = "passed"
    FAILED = "failed"
    INELIGIBLE = "ineligible"


class EvaluationVersions(BaseModel):
    model_config = ConfigDict(extra="forbid")

    agent: str
    model: str
    prompt: str
    corpus: str
    embedding: str
    dataset: str
    scorer: str


class MetricResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    value: float
    passed: bool
    source: Literal["deterministic", "llm_judge", "human"]
    sample_count: int = Field(ge=0)


class EvaluationGateSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    versions: EvaluationVersions
    metrics: dict[str, MetricResult]
    incidents: dict[str, int]
    missing_required_metrics: list[str]
    technical_smoke_status: GateStatus
    scientific_release_status: GateStatus
    scientific_release_reasons: list[str]
    scientific_gold: bool
    sme_reviewed: bool
    release_gate_eligible: bool


MINIMUM_THRESHOLDS: dict[str, float] = {
    "schema_validity": 1.0,
    "disease_scope": 1.0,
    "tool_policy": 1.0,
    "recall_at_10": 0.80,
    "ndcg_at_10": 0.75,
    "citation_resolvability": 1.0,
    "retrieved_before_cited": 1.0,
    "claim_citation_coverage": 0.95,
    "claim_evidence_entailment": 0.90,
    "evidence_stage_calibration": 1.0,
    "conflict_handling": 1.0,
    "multi_turn_retention": 1.0,
    "source_status": 1.0,
    "truncation": 1.0,
    "frustration_precision": 0.80,
    "frustration_recall": 0.85,
}
MAXIMUM_THRESHOLDS: dict[str, float] = {
    "context_ratio_p95": 0.80,
}
ZERO_INCIDENT_KEYS = frozenset(
    {
        "fabricated_citations",
        "citation_registry_mismatches",
        "unsupported_claims",
        "retracted_positive_uses",
        "scope_violations",
        "tool_loops",
        "truncations",
        "retrieved_before_cited_violations",
    }
)
REQUIRED_METRICS = frozenset(MINIMUM_THRESHOLDS | MAXIMUM_THRESHOLDS)


def nearest_rank_percentile(values: list[float], percentile: float) -> float:
    if not values:
        raise ValueError("percentile requires at least one value")
    if not 0 < percentile <= 1:
        raise ValueError("percentile must be in (0, 1]")
    ordered = sorted(values)
    rank = max(1, math.ceil(percentile * len(ordered)))
    return ordered[rank - 1]


def build_gate_summary(
    *,
    versions: EvaluationVersions,
    observed_metrics: dict[str, tuple[float, int]],
    incidents: dict[str, int],
    scientific_gold: bool,
    sme_reviewed: bool,
    release_gate_eligible: bool,
    human_review_passed: bool | None = None,
    review_metrics: (
        dict[str, tuple[float, int, Literal["llm_judge", "human"]]] | None
    ) = None,
) -> EvaluationGateSummary:
    missing = sorted(
        (REQUIRED_METRICS - observed_metrics.keys())
        | (ZERO_INCIDENT_KEYS - incidents.keys())
    )
    metrics: dict[str, MetricResult] = {}
    for name, (value, sample_count) in observed_metrics.items():
        if name in MINIMUM_THRESHOLDS:
            passed = value >= MINIMUM_THRESHOLDS[name]
        elif name in MAXIMUM_THRESHOLDS:
            passed = value < MAXIMUM_THRESHOLDS[name]
        else:
            passed = True
        metrics[name] = MetricResult(
            value=value,
            passed=passed,
            source="deterministic",
            sample_count=sample_count,
        )
    for name, (value, sample_count, source) in (review_metrics or {}).items():
        if name in metrics:
            raise ValueError(f"review metric collides with deterministic metric: {name}")
        metrics[name] = MetricResult(
            value=value,
            passed=True,
            source=source,
            sample_count=sample_count,
        )

    incident_failures = [
        name
        for name in ZERO_INCIDENT_KEYS
        if name in incidents and incidents[name] != 0
    ]
    metric_failures = [
        name for name in REQUIRED_METRICS if name in metrics and not metrics[name].passed
    ]
    technical_passed = not missing and not incident_failures and not metric_failures
    technical_status = GateStatus.PASSED if technical_passed else GateStatus.FAILED

    scientific_reasons: list[str] = []
    if not scientific_gold:
        scientific_reasons.append("dataset_not_scientific_gold")
    if not sme_reviewed:
        scientific_reasons.append("dataset_not_sme_reviewed")
    if not release_gate_eligible:
        scientific_reasons.append("dataset_not_release_gate_eligible")
    if human_review_passed is not True:
        scientific_reasons.append("human_review_missing_or_failed")
    if technical_status != GateStatus.PASSED:
        scientific_reasons.append("technical_gate_failed")
    if (
        not scientific_gold
        or not sme_reviewed
        or not release_gate_eligible
        or human_review_passed is not True
    ):
        scientific_status = GateStatus.INELIGIBLE
    elif technical_status == GateStatus.PASSED:
        scientific_status = GateStatus.PASSED
    else:
        scientific_status = GateStatus.FAILED

    return EvaluationGateSummary(
        versions=versions,
        metrics=metrics,
        incidents={key: int(value) for key, value in incidents.items()},
        missing_required_metrics=missing,
        technical_smoke_status=technical_status,
        scientific_release_status=scientific_status,
        scientific_release_reasons=sorted(set(scientific_reasons)),
        scientific_gold=scientific_gold,
        sme_reviewed=sme_reviewed,
        release_gate_eligible=release_gate_eligible,
    )
