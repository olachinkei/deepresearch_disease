from __future__ import annotations

from deepresearch_agent.application.dedupe import deduplicate_documents, document_identity
from deepresearch_agent.domain.models import Chunk, Document
from deepresearch_agent.infrastructure.corpus import CorpusRepository


def test_document_dedupe_prefers_doi_and_merges_metadata() -> None:
    first = Document(
        id="a",
        title="Inflammation after stroke",
        doi="https://doi.org/10.1000/TEST",
        provenance=["europe_pmc"],
    )
    second = Document(
        id="b",
        title="Different title",
        doi="10.1000/test",
        pmid="123",
        is_oa=True,
        provenance=["crossref"],
    )

    merged = deduplicate_documents([first, second])

    assert len(merged) == 1
    assert document_identity(merged[0]) == "doi:10.1000/test"
    assert merged[0].pmid == "123"
    assert merged[0].is_oa
    assert merged[0].provenance == ["crossref", "europe_pmc"]


def test_hybrid_search_returns_fts_and_vector_ranked_evidence(tmp_path) -> None:
    repository = CorpusRepository(tmp_path / "corpus.sqlite")
    repository.initialize()
    relevant = Document(id="relevant", title="NLRP3 inhibition in ischemic stroke")
    other = Document(id="other", title="Unrelated publication")
    repository.upsert_document(
        relevant,
        [
            Chunk(
                id="relevant:0",
                document_id=relevant.id,
                ordinal=0,
                text="NLRP3 inhibition reduced ischemic stroke inflammation.",
                token_count=7,
                embedding=[1.0, 0.0],
            )
        ],
    )
    repository.upsert_document(
        other,
        [
            Chunk(
                id="other:0",
                document_id=other.id,
                ordinal=0,
                text="A synthetic unrelated abstract.",
                token_count=4,
                embedding=[0.0, 1.0],
            )
        ],
    )

    results = repository.search(
        query="NLRP3 ischemic stroke",
        query_embedding=[1.0, 0.0],
        limit=2,
    )

    assert results[0].document_id == "relevant"
    assert results[0].id == "E1"
    assert repository.count_documents() == 2
