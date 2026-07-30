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
from deepresearch_agent.application.workflow import _contains_term
from deepresearch_agent.domain.models import Claim, Evidence, SourceKind, SupportLevel


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


def test_citation_verifier_rejects_unknown_uncited_and_retracted_positive_use() -> None:
    evidence = [
        Evidence(
            id="E1",
            document_id="d1",
            source_kind=SourceKind.PUBLIC,
            title="Public evidence",
            excerpt="Synthetic evidence excerpt.",
        ),
        Evidence(
            id="E2",
            document_id="d2",
            source_kind=SourceKind.PUBLIC,
            title="Retracted evidence",
            excerpt="Synthetic retracted excerpt.",
            retracted=True,
        ),
    ]
    claims = [
        Claim(text="supported", evidence_ids=["E2"], support_level=SupportLevel.SUPPORTS),
        Claim(text="uncited", evidence_ids=[], support_level=SupportLevel.UNKNOWN),
    ]

    result = verify_citations(
        answer_markdown="Claim [E1] and an invented citation [E99].",
        claims=claims,
        evidence=evidence,
    )

    assert not result.valid
    assert result.unresolved == {"E99"}
    assert result.uncited_claim_indexes == (1,)
    assert result.retracted_positive_use == {"E2"}


def test_target_filter_requires_target_in_title_or_excerpt() -> None:
    assert _contains_term("NLRP3 inflammasome inhibition", "nlrp3")
    assert _contains_term("Evidence for soluble MMP 9 after stroke", "MMP 9")
    assert not _contains_term("General inflammation after stroke", "NLRP3")
