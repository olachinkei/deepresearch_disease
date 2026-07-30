from __future__ import annotations

import argparse
import asyncio
import json
import os
from pathlib import Path

from deepresearch_agent.domain.models import Chunk
from deepresearch_agent.infrastructure.corpus import CorpusRepository
from deepresearch_agent.infrastructure.embeddings import HashEmbeddingProvider
from deepresearch_agent.seed.collector import DEFAULT_QUERY, PublicSeedCollector, abstract_chunk_id


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Collect a metadata-only public ischemic-stroke seed corpus."
    )
    parser.add_argument("--output", type=Path, required=True, help="Snapshot manifest JSON path")
    parser.add_argument("--database", type=Path, help="Optional corpus SQLite path")
    parser.add_argument("--limit", type=int, default=200)
    parser.add_argument("--query", default=DEFAULT_QUERY)
    parser.add_argument("--crossref-mailto", default=os.getenv("CROSSREF_MAILTO"))
    parser.add_argument("--unpaywall-email", default=os.getenv("UNPAYWALL_EMAIL"))
    return parser


async def collect_and_write(args: argparse.Namespace) -> int:
    if args.limit < 1 or args.limit > 1000:
        raise ValueError("--limit must be between 1 and 1000")
    collector = PublicSeedCollector(
        crossref_mailto=args.crossref_mailto,
        unpaywall_email=args.unpaywall_email,
    )
    try:
        manifest = await collector.collect(query=args.query, limit=args.limit)
    finally:
        await collector.close()

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(manifest.model_dump(mode="json"), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    if args.database:
        repository = CorpusRepository(args.database)
        repository.initialize()
        embedder = HashEmbeddingProvider()
        for document in manifest.documents:
            text = document.abstract or document.title
            embedding = (await embedder.embed([text]))[0]
            chunk = Chunk(
                id=abstract_chunk_id(document),
                document_id=document.id,
                ordinal=0,
                text=text,
                section="Abstract" if document.abstract else "Title",
                token_count=max(1, len(text.split())),
                embedding=embedding,
            )
            repository.upsert_document(document, [chunk])
        repository.save_snapshot(
            manifest.snapshot_id,
            manifest.created_at.isoformat(),
            manifest.model_dump(mode="json"),
        )
    return 0


def main() -> None:
    args = build_parser().parse_args()
    raise SystemExit(asyncio.run(collect_and_write(args)))
