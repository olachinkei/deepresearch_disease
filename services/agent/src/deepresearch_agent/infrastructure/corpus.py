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
    snapshot_id TEXT NOT NULL,
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
    snapshot_id TEXT NOT NULL,
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
            self._migrate_snapshot_columns(connection)

    def assert_embedding_contract(
        self, *, snapshot_id: str, model_name: str, dimension: int
    ) -> None:
        with closing(self._connect()) as connection:
            document_count = int(connection.execute("SELECT count(*) FROM document").fetchone()[0])
            row = connection.execute(
                "SELECT manifest_json FROM corpus_snapshot WHERE id = ?", (snapshot_id,)
            ).fetchone()
        if document_count == 0:
            return
        if row is None:
            raise ValueError("configured corpus snapshot does not exist")
        try:
            manifest = json.loads(row["manifest_json"])
            embedding = manifest.get("embedding", {})
            stored_model = embedding.get("model", manifest.get("embedding_model"))
            stored_dimension = embedding.get("dimension", manifest.get("embedding_dimension"))
        except (AttributeError, TypeError, ValueError) as exc:
            del exc
            raise ValueError("corpus snapshot embedding metadata is invalid") from None
        if stored_model != model_name or stored_dimension != dimension:
            raise ValueError("corpus snapshot embedding contract does not match provider")
        with closing(self._connect()) as connection:
            mismatched_documents = int(
                connection.execute(
                    "SELECT count(*) FROM document WHERE snapshot_id IS NULL OR snapshot_id != ?",
                    (snapshot_id,),
                ).fetchone()[0]
            )
            mismatched_chunks = int(
                connection.execute(
                    "SELECT count(*) FROM chunk WHERE snapshot_id IS NULL OR snapshot_id != ?",
                    (snapshot_id,),
                ).fetchone()[0]
            )
        if mismatched_documents or mismatched_chunks:
            raise ValueError("corpus rows contain a mixed or missing snapshot ID")

    def save_snapshot(self, snapshot_id: str, created_at: str, manifest: dict[str, object]) -> None:
        serialized = json.dumps(manifest, ensure_ascii=False, sort_keys=True)
        with closing(self._connect()) as connection:
            existing = connection.execute(
                "SELECT id, created_at, manifest_json FROM corpus_snapshot"
            ).fetchall()
            if existing:
                if len(existing) != 1 or existing[0]["id"] != snapshot_id:
                    raise ValueError("corpus database is already bound to another snapshot")
                try:
                    same_manifest = json.loads(existing[0]["manifest_json"]) == manifest
                except (TypeError, ValueError):
                    same_manifest = False
                if existing[0]["created_at"] != created_at or not same_manifest:
                    raise ValueError("immutable corpus snapshot cannot be modified")
                return
            connection.execute(
                """
                INSERT INTO corpus_snapshot(id, created_at, manifest_json)
                VALUES (?, ?, ?)
                """,
                (snapshot_id, created_at, serialized),
            )
            connection.commit()

    def upsert_document(
        self,
        document: Document,
        chunks: Iterable[Chunk] = (),
        *,
        snapshot_id: str,
    ) -> None:
        payload = json.dumps(document.model_dump(mode="json"), ensure_ascii=False)
        prepared_chunks = tuple(chunks)
        with closing(self._connect()) as connection:
            snapshot = connection.execute(
                "SELECT 1 FROM corpus_snapshot WHERE id = ?", (snapshot_id,)
            ).fetchone()
            if snapshot is None:
                raise ValueError("snapshot must be saved before corpus rows")
            existing = connection.execute(
                "SELECT snapshot_id, payload_json FROM document WHERE id = ?", (document.id,)
            ).fetchone()
            if existing is not None:
                if existing["snapshot_id"] != snapshot_id:
                    raise ValueError("document cannot move between immutable snapshots")
                existing_chunks = connection.execute(
                    """
                    SELECT id, ordinal, text, page_start, page_end, section, token_count,
                           embedding, embedding_dimension
                    FROM chunk WHERE document_id = ? ORDER BY ordinal
                    """,
                    (document.id,),
                ).fetchall()
                if existing["payload_json"] != payload or not _chunks_equal(
                    existing_chunks, prepared_chunks
                ):
                    raise ValueError("immutable corpus document cannot be modified")
                return
            connection.execute(
                """
                INSERT INTO document(
                    id, snapshot_id, title, doi, pmid, canonical_url, source_kind,
                    retracted, payload_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    document.id,
                    snapshot_id,
                    document.title,
                    document.doi,
                    document.pmid,
                    str(document.canonical_url) if document.canonical_url else None,
                    document.source_kind.value,
                    int(document.retracted),
                    payload,
                ),
            )
            for chunk in prepared_chunks:
                connection.execute(
                    """
                    INSERT INTO chunk(
                        id, snapshot_id, document_id, ordinal, text, page_start, page_end,
                        section, token_count, embedding, embedding_dimension
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        chunk.id,
                        snapshot_id,
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

    @staticmethod
    def _migrate_snapshot_columns(connection: sqlite3.Connection) -> None:
        document_columns = {
            str(row[1]) for row in connection.execute("PRAGMA table_info(document)")
        }
        chunk_columns = {
            str(row[1]) for row in connection.execute("PRAGMA table_info(chunk)")
        }
        if "snapshot_id" not in document_columns:
            connection.execute("ALTER TABLE document ADD COLUMN snapshot_id TEXT")
        if "snapshot_id" not in chunk_columns:
            connection.execute("ALTER TABLE chunk ADD COLUMN snapshot_id TEXT")
        snapshot_ids = [
            str(row[0])
            for row in connection.execute("SELECT id FROM corpus_snapshot ORDER BY id")
        ]
        if len(snapshot_ids) == 1:
            connection.execute(
                "UPDATE document SET snapshot_id = ? WHERE snapshot_id IS NULL",
                (snapshot_ids[0],),
            )
            connection.execute(
                "UPDATE chunk SET snapshot_id = ? WHERE snapshot_id IS NULL",
                (snapshot_ids[0],),
            )
        connection.execute(
            "CREATE INDEX IF NOT EXISTS document_snapshot_idx ON document(snapshot_id)"
        )
        connection.execute(
            "CREATE INDEX IF NOT EXISTS chunk_snapshot_idx ON chunk(snapshot_id)"
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


def _chunks_equal(rows: Sequence[sqlite3.Row], chunks: Sequence[Chunk]) -> bool:
    if len(rows) != len(chunks):
        return False
    for row, chunk in zip(rows, chunks, strict=True):
        if (
            row["id"] != chunk.id
            or row["ordinal"] != chunk.ordinal
            or row["text"] != chunk.text
            or row["page_start"] != chunk.page_start
            or row["page_end"] != chunk.page_end
            or row["section"] != chunk.section
            or row["token_count"] != chunk.token_count
            or row["embedding_dimension"]
            != (len(chunk.embedding) if chunk.embedding else None)
            or row["embedding"] != _encode_embedding(chunk.embedding)
        ):
            return False
    return True
