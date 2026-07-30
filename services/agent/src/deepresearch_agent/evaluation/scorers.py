from __future__ import annotations

import math
import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import Any

from pydantic import ValidationError

from deepresearch_agent.domain.models import ResearchResult

_CITATION = re.compile(r"\[(E[0-9A-Za-z_-]+)\]")
_WORD = re.compile(r"[A-Za-z0-9]{3,}")


def schema_score(output: dict[str, Any]) -> dict[str, Any]:
    try:
        ResearchResult.model_validate(output)
    except ValidationError as exc:
        return {"passed": False, "score": 0.0, "error_count": len(exc.errors())}
    return {"passed": True, "score": 1.0, "error_count": 0}


def disease_scope_score(disease: str) -> dict[str, Any]:
    passed = disease.casefold() == "ischemic stroke"
    return {"passed": passed, "score": float(passed)}


def tool_policy_score(tool_counts: dict[str, int], flags: Sequence[str]) -> dict[str, Any]:
    limits = {"internal_search": 2, "exa_search": 2, "contents": 1, "metadata": 1}
    within_individual = all(tool_counts.get(key, 0) <= limit for key, limit in limits.items())
    within_total = sum(tool_counts.values()) <= 6
    no_loop = "duplicate_query_loop" not in flags and "search_budget_exceeded" not in flags
    passed = within_individual and within_total and no_loop
    return {
        "passed": passed,
        "score": float(passed),
        "total_calls": sum(tool_counts.values()),
    }


def recall_at_k(
    retrieved_ids: Sequence[str], relevant_ids: Iterable[str], *, k: int = 10
) -> float:
    relevant = set(relevant_ids)
    if not relevant:
        return 1.0
    return len(set(retrieved_ids[:k]) & relevant) / len(relevant)


def ndcg_at_k(
    retrieved_ids: Sequence[str],
    relevance: dict[str, float],
    *,
    k: int = 10,
) -> float:
    def dcg(values: Sequence[float]) -> float:
        return sum((2**value - 1) / math.log2(index + 2) for index, value in enumerate(values))

    gains = [relevance.get(item, 0.0) for item in retrieved_ids[:k]]
    ideal = sorted(relevance.values(), reverse=True)[:k]
    ideal_score = dcg(ideal)
    return dcg(gains) / ideal_score if ideal_score else 1.0


def citation_resolvability_score(
    answer_markdown: str, source_evidence_ids: Iterable[str]
) -> dict[str, Any]:
    citations = set(_CITATION.findall(answer_markdown))
    available = set(source_evidence_ids)
    unresolved = sorted(citations - available)
    passed = not unresolved
    return {
        "passed": passed,
        "score": 1.0 if passed else len(citations - set(unresolved)) / max(len(citations), 1),
        "unresolved": unresolved,
    }


def citation_coverage_score(claims: Sequence[dict[str, Any]]) -> dict[str, Any]:
    if not claims:
        return {"passed": True, "score": 1.0, "covered": 0, "total": 0}
    covered = sum(bool(claim.get("evidence_ids")) for claim in claims)
    score = covered / len(claims)
    return {"passed": score >= 0.95, "score": score, "covered": covered, "total": len(claims)}


def context_budget_score(context_ratio: float) -> dict[str, Any]:
    passed = context_ratio < 0.80
    return {"passed": passed, "score": max(0.0, 1.0 - context_ratio), "ratio": context_ratio}


def truncation_score(finish_reason: str | None, flags: Sequence[str]) -> dict[str, Any]:
    passed = finish_reason not in {"MAX_TOKENS", "length"} and "truncation" not in flags
    return {"passed": passed, "score": float(passed)}


def lexical_groundedness_score(
    claim: str,
    cited_evidence: Sequence[str],
) -> dict[str, Any]:
    """Cheap regression signal only; it is never treated as scientific ground truth."""

    claim_tokens = set(_WORD.findall(claim.casefold()))
    evidence_tokens = set(_WORD.findall(" ".join(cited_evidence).casefold()))
    score = len(claim_tokens & evidence_tokens) / max(len(claim_tokens), 1)
    return {"passed": score >= 0.30, "score": score}


def evidence_stage_calibration_score(
    claim: str,
    cited_stages: Sequence[str],
) -> dict[str, Any]:
    clinical_language = bool(
        re.search(
            r"\b(proven|effective|clinically effective|improves outcomes)\b",
            claim.casefold(),
        )
    )
    clinical_stage = any(stage == "clinical" for stage in cited_stages)
    passed = not clinical_language or clinical_stage
    return {"passed": passed, "score": float(passed)}


def contradiction_handling_score(
    answer_markdown: str,
    evidence_support_levels: Sequence[str],
) -> dict[str, Any]:
    has_conflict = any(level in {"contradicts", "mixed"} for level in evidence_support_levels)
    acknowledges = "矛盾・negative evidence" in answer_markdown
    passed = not has_conflict or acknowledges
    return {"passed": passed, "score": float(passed)}


def multi_turn_retention_score(
    expected: dict[str, Any],
    actual: dict[str, Any],
) -> dict[str, Any]:
    keys = {"target_molecule", "mechanism", "disease"}
    matches = sum(expected.get(key) == actual.get(key) for key in keys)
    score = matches / len(keys)
    return {"passed": score == 1.0, "score": score}


@dataclass(frozen=True, slots=True)
class ClassificationMetrics:
    precision: float
    recall: float


def frustration_metrics(
    labels: Sequence[bool], predictions: Sequence[bool]
) -> ClassificationMetrics:
    if len(labels) != len(predictions):
        raise ValueError("labels and predictions must have equal length")
    pairs = list(zip(labels, predictions, strict=True))
    true_positive = sum(label and prediction for label, prediction in pairs)
    false_positive = sum(not label and prediction for label, prediction in pairs)
    false_negative = sum(label and not prediction for label, prediction in pairs)
    precision = (
        true_positive / (true_positive + false_positive)
        if true_positive + false_positive
        else 1.0
    )
    recall = (
        true_positive / (true_positive + false_negative)
        if true_positive + false_negative
        else 1.0
    )
    return ClassificationMetrics(precision=precision, recall=recall)


def release_gate(summary: dict[str, float | int]) -> dict[str, Any]:
    zero_incident_keys = [
        "fabricated_citations",
        "retracted_positive_uses",
        "scope_violations",
        "tool_loops",
        "truncations",
    ]
    failures = [key for key in zero_incident_keys if summary.get(key, 0) != 0]
    thresholds = {
        "citation_resolvability": 1.0,
        "claim_citation_coverage": 0.95,
        "entailment": 0.90,
        "recall_at_10": 0.80,
        "ndcg_at_10": 0.75,
        "frustration_precision": 0.80,
        "frustration_recall": 0.85,
    }
    failures.extend(
        key for key, minimum in thresholds.items() if float(summary.get(key, 0)) < minimum
    )
    if float(summary.get("context_ratio_p95", 1.0)) >= 0.80:
        failures.append("context_ratio_p95")
    return {"passed": not failures, "failures": sorted(set(failures))}
