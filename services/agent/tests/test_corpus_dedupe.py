from __future__ import annotations

import sqlite3

import pytest

from deepresearch_agent.application.dedupe import deduplicate_documents, document_identity
from deepresearch_agent.domain.models import Chunk, Document, SourceKind
from deepresearch_agent.infrastructure.corpus import CorpusRepository


def test_corpus_snapshot_rejects_embedding_space_mismatch(tmp_path) -> None:
    repository = CorpusRepository(tmp_path / "corpus.sqlite")
    repository.initialize()
    repository.save_snapshot(
        "snapshot-hash",
        "2026-08-01T00:00:00Z",
        {"embedding": {"model": "local-hash-embedding-v1", "dimension": 768}},
    )
    repository.upsert_document(
        Document(
            id="D-contract",
            title="Synthetic contract test",
            source_kind=SourceKind.PUBLIC,
        ),
        snapshot_id="snapshot-hash",
    )

    with pytest.raises(ValueError, match="does not match provider"):
        repository.assert_embedding_contract(
            snapshot_id="snapshot-hash",
            model_name="gemini-embedding-2",
            dimension=768,
        )


def test_corpus_snapshot_is_immutable_and_rows_cannot_move(tmp_path) -> None:
    repository = CorpusRepository(tmp_path / "corpus.sqlite")
    repository.initialize()
    manifest = {"embedding": {"model": "test", "dimension": 2}}
    repository.save_snapshot("snapshot-1", "2026-08-01T00:00:00Z", manifest)
    document = Document(id="immutable", title="Original")
    repository.upsert_document(document, snapshot_id="snapshot-1")
    repository.upsert_document(document, snapshot_id="snapshot-1")

    with pytest.raises(ValueError, match="cannot be modified"):
        repository.upsert_document(
            document.model_copy(update={"title": "Changed"}),
            snapshot_id="snapshot-1",
        )
    with pytest.raises(ValueError, match="another snapshot"):
        repository.save_snapshot("snapshot-2", "2026-08-01T00:00:01Z", manifest)
    empty_repository = CorpusRepository(tmp_path / "empty.sqlite")
    empty_repository.initialize()
    with pytest.raises(ValueError, match="saved before"):
        empty_repository.upsert_document(
            document,
            snapshot_id="missing",
        )


def test_legacy_single_snapshot_rows_are_backfilled_by_migration(tmp_path) -> None:
    database = tmp_path / "legacy.sqlite"
    with sqlite3.connect(database) as connection:
        connection.executescript(
            """
            CREATE TABLE corpus_snapshot (
                id TEXT PRIMARY KEY, created_at TEXT NOT NULL, manifest_json TEXT NOT NULL
            );
            CREATE TABLE document (
                id TEXT PRIMARY KEY, title TEXT NOT NULL, doi TEXT, pmid TEXT,
                canonical_url TEXT, source_kind TEXT NOT NULL,
                retracted INTEGER NOT NULL DEFAULT 0, payload_json TEXT NOT NULL
            );
            CREATE TABLE chunk (
                id TEXT PRIMARY KEY, document_id TEXT NOT NULL, ordinal INTEGER NOT NULL,
                text TEXT NOT NULL, page_start INTEGER, page_end INTEGER, section TEXT,
                token_count INTEGER NOT NULL, embedding BLOB, embedding_dimension INTEGER,
                UNIQUE(document_id, ordinal)
            );
            INSERT INTO corpus_snapshot VALUES (
                'legacy-snapshot', '2026-07-30T00:00:00Z',
                '{"embedding":{"model":"test","dimension":2}}'
            );
            INSERT INTO document VALUES (
                'legacy-doc', 'Legacy', NULL, NULL, NULL, 'public', 0,
                '{"id":"legacy-doc","title":"Legacy"}'
            );
            INSERT INTO chunk VALUES (
                'legacy-chunk', 'legacy-doc', 0, 'Legacy text', NULL, NULL,
                'Abstract', 2, NULL, NULL
            );
            """
        )

    CorpusRepository(database).initialize()

    with sqlite3.connect(database) as connection:
        document_snapshot = connection.execute(
            "SELECT snapshot_id FROM document"
        ).fetchone()[0]
        chunk_snapshot = connection.execute("SELECT snapshot_id FROM chunk").fetchone()[0]
    assert document_snapshot == chunk_snapshot == "legacy-snapshot"


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
    repository.save_snapshot(
        "snapshot-test",
        "2026-08-01T00:00:00Z",
        {"embedding": {"model": "test", "dimension": 2}},
    )
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
        snapshot_id="snapshot-test",
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
        snapshot_id="snapshot-test",
    )

    results = repository.search(
        query="NLRP3 ischemic stroke",
        query_embedding=[1.0, 0.0],
        limit=2,
    )

    assert results[0].document_id == "relevant"
    assert results[0].id == "E1"
    assert repository.count_documents() == 2
