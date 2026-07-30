from __future__ import annotations

import asyncio
import hashlib
import re
from dataclasses import dataclass
from pathlib import Path

import fitz
from pydantic import BaseModel, ConfigDict

from deepresearch_agent.domain.models import Chunk, Document, SourceKind
from deepresearch_agent.infrastructure.embeddings import EmbeddingProvider

_SENTENCE_BOUNDARY = re.compile(r"(?<=[.!?])\s+(?=[A-Z0-9])")
_SECTION_HEADING = re.compile(r"^[A-Z][A-Z0-9 :/&-]{2,80}$")


class InternalManifestRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    internal_id: str
    filename: str
    title: str
    doi: str | None = None
    pmid: str | None = None
    access_class: str
    license: str | None = None
    external_send_allowed: bool = False


@dataclass(frozen=True, slots=True)
class IngestionReport:
    status: str
    reason: str | None
    document: Document | None
    chunks: tuple[Chunk, ...]


def _token_count(text: str) -> int:
    return max(1, len(text.split()))


def _resolve_approved_pdf(pdf_directory: Path, filename: str) -> tuple[Path | None, str | None]:
    approved_directory = pdf_directory.resolve()
    path = (approved_directory / filename).resolve()
    if approved_directory not in path.parents:
        return None, "manifest filename escapes the approved folder"
    if not path.exists() or path.suffix.casefold() != ".pdf":
        return None, "approved PDF was not found"
    return path, None


def _extract_pdf(path: Path) -> tuple[list[str], str]:
    pdf = fitz.open(path)
    try:
        pages = [page.get_text("text") for page in pdf]
    finally:
        pdf.close()
    return pages, hashlib.sha256(path.read_bytes()).hexdigest()


def _chunk_page(
    *,
    document_id: str,
    page_number: int,
    text: str,
    start_ordinal: int,
    min_tokens: int = 350,
    max_tokens: int = 700,
) -> list[Chunk]:
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    section: str | None = None
    paragraphs: list[tuple[str | None, str]] = []
    for line in lines:
        if _SECTION_HEADING.fullmatch(line):
            section = line.title()
        else:
            paragraphs.append((section, line))
    sentences: list[tuple[str | None, str]] = []
    for paragraph_section, paragraph in paragraphs:
        sentences.extend(
            (paragraph_section, sentence.strip())
            for sentence in _SENTENCE_BOUNDARY.split(paragraph)
            if sentence.strip()
        )

    result: list[Chunk] = []
    current: list[str] = []
    current_section: str | None = None
    current_tokens = 0
    ordinal = start_ordinal
    previous_sentence: str | None = None
    for sentence_section, sentence in sentences:
        sentence_tokens = _token_count(sentence)
        if current and current_tokens + sentence_tokens > max_tokens:
            body = " ".join(current)
            result.append(
                Chunk(
                    id=f"{document_id}:p{page_number}:c{ordinal}",
                    document_id=document_id,
                    ordinal=ordinal,
                    text=body,
                    page_start=page_number,
                    page_end=page_number,
                    section=current_section,
                    token_count=_token_count(body),
                )
            )
            ordinal += 1
            current = [previous_sentence] if previous_sentence else []
            current_tokens = _token_count(previous_sentence) if previous_sentence else 0
        if not current_section:
            current_section = sentence_section
        current.append(sentence)
        current_tokens += sentence_tokens
        previous_sentence = sentence
    if current and (current_tokens >= min_tokens or not result):
        body = " ".join(current)
        result.append(
            Chunk(
                id=f"{document_id}:p{page_number}:c{ordinal}",
                document_id=document_id,
                ordinal=ordinal,
                text=body,
                page_start=page_number,
                page_end=page_number,
                section=current_section,
                token_count=_token_count(body),
            )
        )
    return result


async def ingest_internal_pdf(
    *,
    pdf_directory: Path,
    record: InternalManifestRecord,
    embedding_provider: EmbeddingProvider,
    ingestion_enabled: bool,
    external_embedding_approved: bool = False,
) -> IngestionReport:
    if not ingestion_enabled:
        return IngestionReport("disabled", "internal ingestion is not approved", None, ())
    if embedding_provider.external and not (
        external_embedding_approved and record.external_send_allowed
    ):
        return IngestionReport(
            "disabled",
            "external embedding of internal content is not approved",
            None,
            (),
        )
    path, rejection_reason = await asyncio.to_thread(
        _resolve_approved_pdf, pdf_directory, record.filename
    )
    if path is None:
        return IngestionReport("rejected", rejection_reason, None, ())

    pages, checksum = await asyncio.to_thread(_extract_pdf, path)
    non_whitespace = sum(len(re.sub(r"\s", "", page)) for page in pages)
    if not pages or non_whitespace / max(len(pages), 1) < 120:
        return IngestionReport("ocr_required", "PDF has insufficient extractable text", None, ())

    document_id = f"internal:{record.internal_id}"
    chunks: list[Chunk] = []
    ordinal = 0
    for page_number, page_text in enumerate(pages, start=1):
        page_chunks = _chunk_page(
            document_id=document_id,
            page_number=page_number,
            text=page_text,
            start_ordinal=ordinal,
        )
        chunks.extend(page_chunks)
        ordinal += len(page_chunks)
    embeddings = await embedding_provider.embed([chunk.text for chunk in chunks])
    chunks = [
        chunk.model_copy(update={"embedding": embedding})
        for chunk, embedding in zip(chunks, embeddings, strict=True)
    ]
    document = Document(
        id=document_id,
        title=record.title,
        doi=record.doi,
        pmid=record.pmid,
        source_kind=SourceKind.INTERNAL,
        internal_id=record.internal_id,
        access_class=record.access_class,
        license=record.license,
        full_text_stored=True,
        provenance=["approved_internal_manifest"],
        metadata={"sha256": checksum, "filename": record.filename},
    )
    return IngestionReport("ingested", None, document, tuple(chunks))
