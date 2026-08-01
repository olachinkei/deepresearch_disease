from __future__ import annotations

import argparse
import asyncio
import json
import os
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from pydantic import ValidationError

from deepresearch_agent.domain.models import Chunk
from deepresearch_agent.embedding_contract import EMBEDDING_MODEL_VERSION
from deepresearch_agent.infrastructure.corpus import CorpusRepository
from deepresearch_agent.infrastructure.embeddings import (
    EmbeddingDocument,
    EmbeddingProvider,
    GeminiEmbeddingProvider,
    HashEmbeddingProvider,
)
from deepresearch_agent.seed.collector import DEFAULT_QUERY, PublicSeedCollector, abstract_chunk_id
from deepresearch_agent.seed.models import PublicSeedManifest, SeedContentPolicy
from deepresearch_agent.seed.oa_fulltext import (
    EuropePmcOaFullTextClient,
    OaIngestionReport,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Collect a metadata-only public ischemic-stroke seed corpus."
    )
    parser.add_argument("--output", type=Path, required=True, help="Snapshot manifest JSON path")
    parser.add_argument(
        "--input-manifest",
        type=Path,
        help="Re-embed an existing public manifest without collecting metadata again",
    )
    parser.add_argument("--database", type=Path, help="Optional corpus SQLite path")
    parser.add_argument("--limit", type=int, default=200)
    parser.add_argument("--query", default=DEFAULT_QUERY)
    parser.add_argument("--crossref-mailto", default=os.getenv("CROSSREF_MAILTO"))
    parser.add_argument("--unpaywall-email", default=os.getenv("UNPAYWALL_EMAIL"))
    parser.add_argument("--embedding-provider", choices=("hash", "gemini"), default="hash")
    parser.add_argument(
        "--allow-public-gemini-embeddings",
        action="store_true",
        help="Explicitly allow public manifest title/abstract text to be sent to Gemini",
    )
    parser.add_argument(
        "--include-oa-full-text",
        action="store_true",
        help="Store only allowlisted Europe PMC OA JATS XML body text",
    )
    parser.add_argument(
        "--oa-report",
        type=Path,
        help="Sanitized OA ingestion report path (defaults next to output)",
    )
    return parser


async def collect_and_write(args: argparse.Namespace) -> int:
    if args.limit < 1 or args.limit > 1000:
        raise ValueError("--limit must be between 1 and 1000")
    if args.input_manifest:
        try:
            manifest = PublicSeedManifest.model_validate_json(
                args.input_manifest.read_text(encoding="utf-8")
            )
        except (OSError, ValidationError) as exc:
            del exc
            raise ValueError("input manifest is missing or invalid") from None
        now = datetime.now(UTC)
        manifest = manifest.model_copy(
            update={
                "snapshot_id": f"public-seed-{now:%Y%m%d}-{uuid4().hex[:8]}",
                "created_at": now,
            }
        )
    else:
        collector = PublicSeedCollector(
            crossref_mailto=args.crossref_mailto,
            unpaywall_email=args.unpaywall_email,
        )
        try:
            manifest = await collector.collect(query=args.query, limit=args.limit)
        finally:
            await collector.close()

    full_text_chunks: dict[str, list[Chunk]] = {}
    oa_reports: list[OaIngestionReport] = []
    if args.include_oa_full_text:
        if not args.database:
            raise ValueError("OA full text ingestion requires --database")
        oa_client = EuropePmcOaFullTextClient()
        updated_documents = []
        try:
            for document in manifest.documents:
                result = await oa_client.ingest(
                    document,
                    snapshot_id=manifest.snapshot_id,
                )
                updated_documents.append(result.document)
                oa_reports.append(result.report)
                if result.chunks:
                    full_text_chunks[document.id] = list(result.chunks)
        finally:
            await oa_client.close()
        stored_full_text = any(report.status == "stored" for report in oa_reports)
        manifest = manifest.model_copy(
            update={
                "schema_version": "2.0",
                "documents": updated_documents,
                "content_policy": SeedContentPolicy(
                    metadata_only=not stored_full_text,
                    article_text_downloaded=stored_full_text,
                    oa_full_text_handling="europe_pmc_allowlisted_xml",
                ),
            }
        )

    embedder: EmbeddingProvider
    if args.embedding_provider == "gemini":
        if not args.allow_public_gemini_embeddings:
            raise ValueError("Gemini embeddings require explicit public-content approval")
        api_key = os.getenv("GOOGLE_API_KEY")
        if not api_key:
            raise ValueError("Gemini embeddings require GOOGLE_API_KEY")
        embedder = GeminiEmbeddingProvider(api_key=api_key)
    else:
        embedder = HashEmbeddingProvider()
    manifest = manifest.model_copy(
        update={
            "embedding": {
                "provider": "google" if embedder.external else "local",
                "model": embedder.model_name,
                "version": EMBEDDING_MODEL_VERSION if embedder.external else "v1",
                "dimension": embedder.dimension,
            }
        }
    )

    try:
        if args.database:
            repository = CorpusRepository(args.database)
            repository.initialize()
            repository.save_snapshot(
                manifest.snapshot_id,
                manifest.created_at.isoformat(),
                manifest.model_dump(mode="json"),
            )
            chunks_by_document: dict[str, list[Chunk]] = {}
            embedding_inputs: list[EmbeddingDocument] = []
            chunk_order: list[Chunk] = []
            for document in manifest.documents:
                chunks = full_text_chunks.get(document.id)
                if not chunks:
                    text = document.abstract or document.title
                    chunks = [
                        Chunk(
                            id=abstract_chunk_id(document),
                            document_id=document.id,
                            ordinal=0,
                            text=text,
                            section="Abstract" if document.abstract else "Title",
                            token_count=max(1, len(text.split())),
                        )
                    ]
                chunks_by_document[document.id] = chunks
                chunk_order.extend(chunks)
                embedding_inputs.extend(
                    EmbeddingDocument(text=chunk.text, title=document.title)
                    for chunk in chunks
                )
            embeddings = await embedder.embed_documents(embedding_inputs)
            embedded_chunks = {
                chunk.id: chunk.model_copy(update={"embedding": embedding})
                for chunk, embedding in zip(chunk_order, embeddings, strict=True)
            }
            for document in manifest.documents:
                repository.upsert_document(
                    document,
                    [embedded_chunks[chunk.id] for chunk in chunks_by_document[document.id]],
                    snapshot_id=manifest.snapshot_id,
                )
    finally:
        await embedder.close()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(manifest.model_dump(mode="json"), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    if args.include_oa_full_text:
        report_path = args.oa_report or args.output.with_suffix(".oa-report.json")
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(
            json.dumps(
                {
                    "schema_version": "1.0",
                    "policy_version": "public-demo-1.0",
                    "snapshot_id": manifest.snapshot_id,
                    "stored": sum(report.status == "stored" for report in oa_reports),
                    "skipped": sum(report.status == "skipped" for report in oa_reports),
                    "failed": sum(report.status == "failed" for report in oa_reports),
                    "records": [report.model_dump(mode="json") for report in oa_reports],
                },
                ensure_ascii=False,
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
    return 0


def main() -> None:
    args = build_parser().parse_args()
    raise SystemExit(asyncio.run(collect_and_write(args)))
