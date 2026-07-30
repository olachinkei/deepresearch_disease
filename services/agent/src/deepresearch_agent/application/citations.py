from __future__ import annotations

import re
from dataclasses import dataclass

from deepresearch_agent.domain.models import (
    Claim,
    Evidence,
    SourceKind,
    SourceReference,
    SupportLevel,
)

_CITATION = re.compile(r"\[(E[0-9A-Za-z_-]+)\]")
_IMMEDIATE_CITATIONS = re.compile(
    r"^[ \t]*((?:\[(?:E[0-9A-Za-z_-]+)\][ \t]*)+)"
)
_WORD = re.compile(r"[A-Za-z0-9]{3,}")
_CJK_SEQUENCE = re.compile(r"[\u3040-\u30ff\u3400-\u9fff]+")
_ABSENCE_OVERCLAIM = re.compile(
    r"(?:根拠|エビデンス)(?:は|が)存在しない(?:[。.\n]|$)"
    r"|\bthere is no evidence\b"
    r"|\b(?:evidence|study|studies) (?:does|do) not exist\b",
    re.IGNORECASE,
)


@dataclass(frozen=True, slots=True)
class CitationCheck:
    valid: bool
    unresolved: frozenset[str]
    invalid_claim_evidence_ids: frozenset[str]
    invalid_source_evidence_ids: frozenset[str]
    registry_mismatch: frozenset[str]
    uncited_claim_indexes: tuple[int, ...]
    claim_markdown_mismatch_indexes: tuple[int, ...]
    unsupported_claim_indexes: tuple[int, ...]
    unresolvable_source_ids: frozenset[str]
    retracted_positive_use: frozenset[str]
    absence_overclaim: bool


def verify_citations(
    *,
    answer_markdown: str,
    claims: list[Claim],
    evidence: list[Evidence],
    sources: list[SourceReference],
) -> CitationCheck:
    available = {item.id for item in evidence}
    evidence_by_id = {item.id: item for item in evidence}
    cited = set(_CITATION.findall(answer_markdown))
    claim_evidence_ids = {
        evidence_id for claim in claims for evidence_id in claim.evidence_ids
    }
    source_evidence_ids = {source.evidence_id for source in sources}
    unresolved = cited - available
    invalid_claim_ids = claim_evidence_ids - available
    invalid_source_ids = source_evidence_ids - available
    registry_mismatch = (
        cited.symmetric_difference(claim_evidence_ids)
        | claim_evidence_ids.symmetric_difference(source_evidence_ids)
    )
    uncited = tuple(index for index, claim in enumerate(claims) if not claim.evidence_ids)
    markdown_mismatches = tuple(
        index
        for index, claim in enumerate(claims)
        if claim_markdown_evidence_ids(answer_markdown, claim.text)
        != frozenset(claim.evidence_ids)
    )
    unsupported = tuple(
        index
        for index, claim in enumerate(claims)
        if not claim_is_supported(claim, evidence_by_id)
    )
    unresolvable_sources = {
        source.evidence_id
        for source in sources
        if source.source_kind == SourceKind.PUBLIC
        and not (source.url or source.doi or source.pmid)
    }
    retracted = {item.id for item in evidence if item.retracted}
    positive_retracted = {
        evidence_id
        for claim in claims
        if claim.support_level in {SupportLevel.SUPPORTS, SupportLevel.MIXED}
        for evidence_id in claim.evidence_ids
        if evidence_id in retracted
    }
    absence_overclaim = bool(_ABSENCE_OVERCLAIM.search(answer_markdown))
    return CitationCheck(
        valid=not any(
            (
                unresolved,
                invalid_claim_ids,
                invalid_source_ids,
                registry_mismatch,
                uncited,
                markdown_mismatches,
                unsupported,
                unresolvable_sources,
                positive_retracted,
            )
        )
        and not absence_overclaim,
        unresolved=frozenset(unresolved),
        invalid_claim_evidence_ids=frozenset(invalid_claim_ids),
        invalid_source_evidence_ids=frozenset(invalid_source_ids),
        registry_mismatch=frozenset(registry_mismatch),
        uncited_claim_indexes=uncited,
        claim_markdown_mismatch_indexes=markdown_mismatches,
        unsupported_claim_indexes=unsupported,
        unresolvable_source_ids=frozenset(unresolvable_sources),
        retracted_positive_use=frozenset(positive_retracted),
        absence_overclaim=absence_overclaim,
    )


def claim_markdown_evidence_ids(
    answer_markdown: str, claim_text: str
) -> frozenset[str]:
    if not claim_text.strip():
        return frozenset()
    for occurrence in re.finditer(re.escape(claim_text), answer_markdown):
        suffix = answer_markdown[occurrence.end() :]
        match = _IMMEDIATE_CITATIONS.match(suffix)
        if match is not None:
            return frozenset(_CITATION.findall(match.group(1)))
    return frozenset()


def claim_is_supported(claim: Claim, evidence_by_id: dict[str, Evidence]) -> bool:
    return _claim_has_compatible_support(
        claim, evidence_by_id
    ) and _claim_is_lexically_grounded(claim, evidence_by_id)


def _claim_has_compatible_support(
    claim: Claim, evidence_by_id: dict[str, Evidence]
) -> bool:
    linked = [
        evidence_by_id[evidence_id]
        for evidence_id in claim.evidence_ids
        if evidence_id in evidence_by_id
    ]
    if len(linked) != len(claim.evidence_ids) or not linked:
        return False
    levels = {item.support_level for item in linked}
    if claim.support_level == SupportLevel.SUPPORTS:
        return levels <= {SupportLevel.SUPPORTS, SupportLevel.MIXED}
    if claim.support_level == SupportLevel.CONTRADICTS:
        return levels <= {SupportLevel.CONTRADICTS, SupportLevel.MIXED}
    if claim.support_level == SupportLevel.MIXED:
        return SupportLevel.MIXED in levels or {
            SupportLevel.SUPPORTS,
            SupportLevel.CONTRADICTS,
        } <= levels
    if claim.support_level == SupportLevel.BACKGROUND:
        return True
    return False


def _claim_is_lexically_grounded(
    claim: Claim, evidence_by_id: dict[str, Evidence]
) -> bool:
    linked = [
        evidence_by_id[evidence_id]
        for evidence_id in claim.evidence_ids
        if evidence_id in evidence_by_id
    ]
    if not linked:
        return False
    claim_terms = _grounding_terms(claim.text)
    if not claim_terms:
        return False
    evidence_terms = _grounding_terms(
        " ".join(f"{item.title} {item.excerpt}" for item in linked)
    )
    return len(claim_terms & evidence_terms) / len(claim_terms) >= 0.20


def _grounding_terms(text: str) -> set[str]:
    normalized = text.casefold()
    terms = set(_WORD.findall(normalized))
    for sequence in _CJK_SEQUENCE.findall(normalized):
        if len(sequence) == 1:
            terms.add(sequence)
        else:
            terms.update(
                sequence[index : index + 2] for index in range(len(sequence) - 1)
            )
    return terms
