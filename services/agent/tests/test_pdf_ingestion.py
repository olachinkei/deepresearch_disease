from __future__ import annotations

import fitz
import pytest

from deepresearch_agent.infrastructure.embeddings import HashEmbeddingProvider
from deepresearch_agent.infrastructure.pdf_ingestion import (
    InternalManifestRecord,
    ingest_internal_pdf,
)


def _record(filename: str = "approved.pdf") -> InternalManifestRecord:
    return InternalManifestRecord(
        internal_id="synthetic-1",
        filename=filename,
        title="Synthetic internal paper",
        access_class="internal",
    )


@pytest.mark.asyncio
async def test_internal_ingestion_is_deny_by_default(tmp_path) -> None:
    result = await ingest_internal_pdf(
        pdf_directory=tmp_path,
        record=_record(),
        embedding_provider=HashEmbeddingProvider(),
        ingestion_enabled=False,
    )
    assert result.status == "disabled"
    assert result.document is None


@pytest.mark.asyncio
async def test_pdf_ingestion_preserves_page_and_rejects_path_escape(tmp_path) -> None:
    pdf_path = tmp_path / "approved.pdf"
    document = fitz.open()
    page = document.new_page()
    sentence = "Synthetic ischemic stroke evidence supports mechanistic evaluation. "
    page.insert_textbox(
        fitz.Rect(36, 36, 560, 800),
        "\n".join(sentence * 3 for _ in range(12)),
        fontsize=9,
    )
    document.save(pdf_path)
    document.close()

    ingested = await ingest_internal_pdf(
        pdf_directory=tmp_path,
        record=_record(),
        embedding_provider=HashEmbeddingProvider(),
        ingestion_enabled=True,
    )
    escaped = await ingest_internal_pdf(
        pdf_directory=tmp_path,
        record=_record("../outside.pdf"),
        embedding_provider=HashEmbeddingProvider(),
        ingestion_enabled=True,
    )

    assert ingested.status == "ingested"
    assert ingested.document is not None
    assert ingested.document.source_kind == "internal"
    assert ingested.chunks[0].page_start == 1
    assert len(ingested.chunks[0].embedding or []) == 768
    assert escaped.status == "rejected"
