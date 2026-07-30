from __future__ import annotations

import re
from dataclasses import dataclass

from deepresearch_agent.domain.models import Claim, Evidence

_CITATION = re.compile(r"\[(E[0-9A-Za-z_-]+)\]")


@dataclass(frozen=True, slots=True)
class CitationCheck:
    valid: bool
    unresolved: frozenset[str]
    uncited_claim_indexes: tuple[int, ...]
    retracted_positive_use: frozenset[str]


def verify_citations(
    *,
    answer_markdown: str,
    claims: list[Claim],
    evidence: list[Evidence],
) -> CitationCheck:
    available = {item.id for item in evidence}
    cited = set(_CITATION.findall(answer_markdown))
    unresolved = cited - available
    uncited = tuple(index for index, claim in enumerate(claims) if not claim.evidence_ids)
    retracted = {item.id for item in evidence if item.retracted}
    positive_retracted = {
        evidence_id
        for claim in claims
        if claim.support_level == "supports"
        for evidence_id in claim.evidence_ids
        if evidence_id in retracted
    }
    return CitationCheck(
        valid=not unresolved and not uncited and not positive_retracted,
        unresolved=frozenset(unresolved),
        uncited_claim_indexes=uncited,
        retracted_positive_use=frozenset(positive_retracted),
    )


def remove_invalid_claims(claims: list[Claim], valid_evidence_ids: set[str]) -> list[Claim]:
    return [
        claim.model_copy(
            update={
                "evidence_ids": [
                    item for item in claim.evidence_ids if item in valid_evidence_ids
                ]
            }
        )
        for claim in claims
        if any(item in valid_evidence_ids for item in claim.evidence_ids)
    ]
