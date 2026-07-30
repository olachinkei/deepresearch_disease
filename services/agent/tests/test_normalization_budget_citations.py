from __future__ import annotations

import pytest

from deepresearch_agent.application.budget import (
    BudgetExceeded,
    ResearchBudget,
    ToolKind,
    pack_evidence_text,
)
from deepresearch_agent.application.citations import verify_citations
from deepresearch_agent.application.normalization import (
    DEFAULT_RESEARCH_QUESTION,
    ScopeError,
    normalize_research_input,
)
from deepresearch_agent.application.workflow import ResearchWorkflow, _contains_term
from deepresearch_agent.domain.models import (
    Claim,
    Evidence,
    SourceKind,
    SourceReference,
    SupportLevel,
)


def test_normalizes_alias_and_default_question() -> None:
    result = normalize_research_input(
        target_molecule="  NLRP3  ",
        mechanism="INHIBITION",
        disease="cerebral infarction",
        research_question=None,
    )

    assert result.disease == "ischemic stroke"
    assert result.target_molecule == "NLRP3"
    assert result.mechanism == "inhibition"
    assert result.research_question == DEFAULT_RESEARCH_QUESTION


def test_rejects_out_of_scope_disease_and_non_english_target() -> None:
    with pytest.raises(ScopeError):
        normalize_research_input(
            target_molecule=None,
            mechanism=None,
            disease="hemorrhagic stroke",
            research_question="test",
        )
    with pytest.raises(ValueError, match="English biomedical term"):
        normalize_research_input(
            target_molecule="炎症",
            mechanism=None,
            disease=None,
            research_question="test",
        )


def test_budget_enforces_tool_duplicate_and_no_progress_stops() -> None:
    budget = ResearchBudget()
    budget.consume(ToolKind.INTERNAL_SEARCH, {"query": "one"})
    budget.consume(ToolKind.INTERNAL_SEARCH, {"query": "two"})
    with pytest.raises(BudgetExceeded, match="call budget"):
        budget.consume(ToolKind.INTERNAL_SEARCH, {"query": "three"})

    duplicate_budget = ResearchBudget(limits={kind: 10 for kind in ToolKind})
    duplicate_budget.consume(ToolKind.EXA_SEARCH, {"query": "same"})
    duplicate_budget.consume(ToolKind.EXA_SEARCH, {"query": "same"})
    with pytest.raises(BudgetExceeded, match="three times"):
        duplicate_budget.consume(ToolKind.EXA_SEARCH, {"query": "same"})
    assert "duplicate_query_loop" in duplicate_budget.flags

    progress_budget = ResearchBudget()
    progress_budget.record_progress(0)
    with pytest.raises(BudgetExceeded, match="no new sources"):
        progress_budget.record_progress(0)


def test_evidence_pack_applies_per_document_and_excerpt_limits() -> None:
    budget = ResearchBudget(max_evidence=3, max_excerpt_chars=5, max_excerpts_per_document=1)
    packed = pack_evidence_text(
        [("d1", "abcdefgh"), ("d1", "second"), ("d2", "123456"), ("d3", "last")],
        budget,
    )
    assert packed == [("d1", "abcde"), ("d2", "12345"), ("d3", "last")]


def _evidence(
    evidence_id: str,
    *,
    document_id: str | None = None,
    title: str | None = None,
    support_level: SupportLevel = SupportLevel.UNKNOWN,
    retracted: bool = False,
) -> Evidence:
    return Evidence(
        id=evidence_id,
        document_id=document_id or evidence_id.lower(),
        source_kind=SourceKind.PUBLIC,
        title=title or f"{evidence_id} finding",
        excerpt=f"{title or evidence_id} synthetic evidence excerpt.",
        doi=f"10.0000/{evidence_id.lower()}",
        support_level=support_level,
        retracted=retracted,
    )


def _source(item: Evidence) -> SourceReference:
    return SourceReference(
        evidence_id=item.id,
        document_id=item.document_id,
        title=item.title,
        source_kind=item.source_kind,
        doi=item.doi,
    )


def test_citation_verifier_rejects_nonexistent_claim_id_even_with_valid_markdown() -> None:
    evidence = [_evidence("E1", title="Valid finding")]
    claims = [
        Claim(
            text="Valid finding",
            evidence_ids=["E999"],
            support_level=SupportLevel.BACKGROUND,
        )
    ]

    result = verify_citations(
        answer_markdown="Valid finding [E1]",
        claims=claims,
        evidence=evidence,
        sources=[_source(evidence[0])],
    )

    assert not result.valid
    assert result.unresolved == frozenset()
    assert result.invalid_claim_evidence_ids == {"E999"}
    assert result.registry_mismatch == {"E1", "E999"}
    assert result.claim_markdown_mismatch_indexes == (0,)


def test_citation_verifier_rejects_citation_reused_for_another_claim() -> None:
    evidence = [
        _evidence("E1", title="Alpha finding"),
        _evidence("E2", title="Beta finding"),
    ]
    claims = [
        Claim(
            text="Alpha finding",
            evidence_ids=["E1"],
            support_level=SupportLevel.BACKGROUND,
        ),
        Claim(
            text="Beta finding",
            evidence_ids=["E2"],
            support_level=SupportLevel.BACKGROUND,
        ),
    ]

    result = verify_citations(
        answer_markdown="Alpha finding [E2]\nBeta finding [E2]",
        claims=claims,
        evidence=evidence,
        sources=[_source(item) for item in evidence],
    )

    assert not result.valid
    assert result.claim_markdown_mismatch_indexes == (0,)
    assert result.registry_mismatch == {"E1"}


def test_source_registry_preserves_multiple_excerpts_from_same_document() -> None:
    evidence = [
        _evidence("E1", document_id="same-document", title="First excerpt"),
        _evidence("E2", document_id="same-document", title="Second excerpt"),
    ]

    sources = ResearchWorkflow._source_references(
        evidence,
        evidence_ids={"E1", "E2"},
    )

    assert [source.evidence_id for source in sources] == ["E1", "E2"]
    assert {source.document_id for source in sources} == {"same-document"}


def test_citation_verifier_rejects_retracted_positive_use_and_support_mismatch() -> None:
    evidence = [
        _evidence(
            "E1",
            title="Retracted positive finding",
            support_level=SupportLevel.SUPPORTS,
            retracted=True,
        ),
        _evidence(
            "E2",
            title="Contradictory finding",
            support_level=SupportLevel.CONTRADICTS,
        ),
    ]
    claims = [
        Claim(
            text="Retracted positive finding",
            evidence_ids=["E1"],
            support_level=SupportLevel.SUPPORTS,
        ),
        Claim(
            text="Contradictory finding",
            evidence_ids=["E2"],
            support_level=SupportLevel.SUPPORTS,
        ),
    ]

    result = verify_citations(
        answer_markdown=(
            "Retracted positive finding [E1]\nContradictory finding [E2]"
        ),
        claims=claims,
        evidence=evidence,
        sources=[_source(item) for item in evidence],
    )

    assert not result.valid
    assert result.retracted_positive_use == {"E1"}
    assert result.unsupported_claim_indexes == (1,)


def test_citation_verifier_accepts_exact_grounded_registry_and_absence_wording() -> None:
    evidence = [
        _evidence(
            "E1",
            title="MMP9 background finding",
            support_level=SupportLevel.BACKGROUND,
        )
    ]
    claims = [
        Claim(
            text="MMP9 background finding",
            evidence_ids=["E1"],
            support_level=SupportLevel.BACKGROUND,
        )
    ]

    valid = verify_citations(
        answer_markdown=(
            "MMP9 background finding [E1]\n"
            "現在の検索範囲では追加根拠を取得できませんでした。"
            "これは根拠が存在しないことを意味しません。"
        ),
        claims=claims,
        evidence=evidence,
        sources=[_source(evidence[0])],
    )
    invalid_absence = verify_citations(
        answer_markdown="MMP9 background finding [E1]\n根拠は存在しない。",
        claims=claims,
        evidence=evidence,
        sources=[_source(evidence[0])],
    )

    assert valid.valid
    assert not valid.absence_overclaim
    assert not invalid_absence.valid
    assert invalid_absence.absence_overclaim


def test_citation_verifier_rejects_public_source_without_resolvable_locator() -> None:
    evidence = [_evidence("E1", title="Resolvable claim")]
    claims = [
        Claim(
            text="Resolvable claim",
            evidence_ids=["E1"],
            support_level=SupportLevel.BACKGROUND,
        )
    ]
    source_without_locator = SourceReference(
        evidence_id="E1",
        document_id="d1",
        title="Missing public locator",
        source_kind=SourceKind.PUBLIC,
    )

    result = verify_citations(
        answer_markdown="Resolvable claim [E1]",
        claims=claims,
        evidence=evidence,
        sources=[source_without_locator],
    )

    assert not result.valid
    assert result.unresolvable_source_ids == {"E1"}


def test_target_filter_requires_target_in_title_or_excerpt() -> None:
    assert _contains_term("NLRP3 inflammasome inhibition", "nlrp3")
    assert _contains_term("Evidence for soluble MMP 9 after stroke", "MMP 9")
    assert not _contains_term("General inflammation after stroke", "NLRP3")
