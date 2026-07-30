from __future__ import annotations

import json
import re
import sqlite3
import struct
from collections.abc import Iterable, Sequence
from contextlib import closing
from pathlib import Path

from deepresearch_agent.domain.models import (
    Chunk,
    Document,
    Evidence,
    PublicationStatus,
    SourceKind,
    VerificationStatus,
)
from deepresearch_agent.infrastructure.embeddings import cosine_similarity

_FTS_TOKEN = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]*")

SCHEMA = """
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;

CREATE TABLE IF NOT EXISTS corpus_snapshot (
    id TEXT PRIMARY KEY,
    created_at TEXT NOT NULL,
    manifest_json TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS document (
    id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    doi TEXT,
    pmid TEXT,
    canonical_url TEXT,
    source_kind TEXT NOT NULL,
    retracted INTEGER NOT NULL DEFAULT 0,
    payload_json TEXT NOT NULL
);

CREATE UNIQUE INDEX IF NOT EXISTS document_doi_unique
ON document(doi) WHERE doi IS NOT NULL;

CREATE UNIQUE INDEX IF NOT EXISTS document_pmid_unique
ON document(pmid) WHERE pmid IS NOT NULL;

CREATE TABLE IF NOT EXISTS chunk (
    id TEXT PRIMARY KEY,
    document_id TEXT NOT NULL REFERENCES document(id) ON DELETE CASCADE,
    ordinal INTEGER NOT NULL,
    text TEXT NOT NULL,
    page_start INTEGER,
    page_end INTEGER,
    section TEXT,
    token_count INTEGER NOT NULL,
    embedding BLOB,
    embedding_dimension INTEGER,
    UNIQUE(document_id, ordinal)
);

CREATE VIRTUAL TABLE IF NOT EXISTS chunk_fts USING fts5(
    chunk_id UNINDEXED,
    title,
    text,
    tokenize='porter unicode61'
);
"""


def _encode_embedding(values: Sequence[float] | None) -> bytes | None:
    if values is None:
        return None
    return struct.pack(f"<{len(values)}f", *values)


def _decode_embedding(value: bytes | None, dimension: int | None) -> list[float] | None:
    if value is None or dimension is None:
        return None
    return list(struct.unpack(f"<{dimension}f", value))


def _fts_query(query: str) -> str:
    tokens = _FTS_TOKEN.findall(query.casefold())
    return " OR ".join(f'"{token}"' for token in tokens[:24])


class CorpusRepository:
    def __init__(self, path: Path) -> None:
        self.path = path

    def initialize(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with closing(sqlite3.connect(self.path)) as connection:
            connection.executescript(SCHEMA)

    def save_snapshot(self, snapshot_id: str, created_at: str, manifest: dict[str, object]) -> None:
        with closing(self._connect()) as connection:
            connection.execute(
                """
                INSERT INTO corpus_snapshot(id, created_at, manifest_json)
                VALUES (?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    created_at=excluded.created_at,
                    manifest_json=excluded.manifest_json
                """,
                (snapshot_id, created_at, json.dumps(manifest, ensure_ascii=False)),
            )
            connection.commit()

    def upsert_document(self, document: Document, chunks: Iterable[Chunk] = ()) -> None:
        payload = json.dumps(document.model_dump(mode="json"), ensure_ascii=False)
        with closing(self._connect()) as connection:
            connection.execute(
                """
                INSERT INTO document(
                    id, title, doi, pmid, canonical_url, source_kind, retracted, payload_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    title=excluded.title,
                    doi=excluded.doi,
                    pmid=excluded.pmid,
                    canonical_url=excluded.canonical_url,
                    source_kind=excluded.source_kind,
                    retracted=excluded.retracted,
                    payload_json=excluded.payload_json
                """,
                (
                    document.id,
                    document.title,
                    document.doi,
                    document.pmid,
                    str(document.canonical_url) if document.canonical_url else None,
                    document.source_kind.value,
                    int(document.retracted),
                    payload,
                ),
            )
            for chunk in chunks:
                connection.execute("DELETE FROM chunk_fts WHERE chunk_id = ?", (chunk.id,))
                connection.execute(
                    """
                    INSERT INTO chunk(
                        id, document_id, ordinal, text, page_start, page_end, section,
                        token_count, embedding, embedding_dimension
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(id) DO UPDATE SET
                        document_id=excluded.document_id,
                        ordinal=excluded.ordinal,
                        text=excluded.text,
                        page_start=excluded.page_start,
                        page_end=excluded.page_end,
                        section=excluded.section,
                        token_count=excluded.token_count,
                        embedding=excluded.embedding,
                        embedding_dimension=excluded.embedding_dimension
                    """,
                    (
                        chunk.id,
                        chunk.document_id,
                        chunk.ordinal,
                        chunk.text,
                        chunk.page_start,
                        chunk.page_end,
                        chunk.section,
                        chunk.token_count,
                        _encode_embedding(chunk.embedding),
                        len(chunk.embedding) if chunk.embedding else None,
                    ),
                )
                connection.execute(
                    "INSERT INTO chunk_fts(chunk_id, title, text) VALUES (?, ?, ?)",
                    (chunk.id, document.title, chunk.text),
                )
            connection.commit()

    def get_document(self, document_id: str) -> Document | None:
        with closing(self._connect()) as connection:
            row = connection.execute(
                "SELECT payload_json FROM document WHERE id = ?", (document_id,)
            ).fetchone()
        return Document.model_validate_json(row["payload_json"]) if row else None

    def count_documents(self) -> int:
        with closing(self._connect()) as connection:
            row = connection.execute("SELECT count(*) AS count FROM document").fetchone()
        return int(row["count"])

    def search(
        self,
        *,
        query: str,
        query_embedding: Sequence[float],
        limit: int = 10,
        rrf_k: int = 60,
    ) -> list[Evidence]:
        lexical_ranks = self._lexical_ranks(query, limit=max(limit * 4, 40))
        vector_ranks = self._vector_ranks(query_embedding, limit=max(limit * 4, 40))
        combined: dict[str, float] = {}
        for ranks in (lexical_ranks, vector_ranks):
            for index, chunk_id in enumerate(ranks, start=1):
                combined[chunk_id] = combined.get(chunk_id, 0.0) + 1.0 / (rrf_k + index)
        ranked_ids = sorted(combined, key=combined.__getitem__, reverse=True)[:limit]
        if not ranked_ids:
            return []
        placeholders = ",".join("?" for _ in ranked_ids)
        with closing(self._connect()) as connection:
            rows = connection.execute(
                f"""
                SELECT c.*, d.payload_json
                FROM chunk c
                JOIN document d ON d.id = c.document_id
                WHERE c.id IN ({placeholders})
                """,  # noqa: S608 -- placeholders are generated, not user-provided
                ranked_ids,
            ).fetchall()
        by_id = {row["id"]: row for row in rows}
        result: list[Evidence] = []
        for index, chunk_id in enumerate(ranked_ids, start=1):
            row = by_id[chunk_id]
            document = Document.model_validate_json(row["payload_json"])
            result.append(
                Evidence(
                    id=f"E{index}",
                    document_id=document.id,
                    source_kind=document.source_kind,
                    title=document.title,
                    excerpt=row["text"][:1200],
                    canonical_url=document.canonical_url,
                    doi=document.doi,
                    pmid=document.pmid,
                    page=row["page_start"],
                    section=row["section"],
                    score=combined[chunk_id],
                    retracted=document.retracted,
                    verification_status=(
                        VerificationStatus.VERIFIED
                        if document.source_kind == SourceKind.PUBLIC
                        and "europe_pmc" in document.provenance
                        else VerificationStatus.UNVERIFIED
                    ),
                    publication_status=(
                        PublicationStatus.RETRACTED
                        if document.retracted
                        else (
                            PublicationStatus.CURRENT
                            if document.source_kind == SourceKind.PUBLIC
                            and "europe_pmc" in document.provenance
                            else PublicationStatus.UNKNOWN
                        )
                    ),
                    provenance=document.provenance,
                )
            )
        return result

    def _lexical_ranks(self, query: str, limit: int) -> list[str]:
        expression = _fts_query(query)
        if not expression:
            return []
        with closing(self._connect()) as connection:
            rows = connection.execute(
                """
                SELECT chunk_id, bm25(chunk_fts) AS score
                FROM chunk_fts
                WHERE chunk_fts MATCH ?
                ORDER BY score ASC
                LIMIT ?
                """,
                (expression, limit),
            ).fetchall()
        return [str(row["chunk_id"]) for row in rows]

    def _vector_ranks(self, query_embedding: Sequence[float], limit: int) -> list[str]:
        with closing(self._connect()) as connection:
            rows = connection.execute(
                "SELECT id, embedding, embedding_dimension FROM chunk WHERE embedding IS NOT NULL"
            ).fetchall()
        scored = []
        for row in rows:
            embedding = _decode_embedding(row["embedding"], row["embedding_dimension"])
            if embedding is not None and len(embedding) == len(query_embedding):
                scored.append((str(row["id"]), cosine_similarity(query_embedding, embedding)))
        scored.sort(key=lambda item: item[1], reverse=True)
        return [chunk_id for chunk_id, _ in scored[:limit]]

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        return connection
