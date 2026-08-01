from __future__ import annotations

import argparse
import json
import sqlite3
from datetime import UTC, datetime

import httpx
import pytest
from defusedxml import ElementTree as ET

from deepresearch_agent.domain.models import Chunk, Document
from deepresearch_agent.seed.cli import collect_and_write
from deepresearch_agent.seed.models import PublicSeedManifest, SourceCollectionStats
from deepresearch_agent.seed.oa_fulltext import (
    EuropePmcOaFullTextClient,
    OaIngestionReport,
    OaIngestionResult,
    _allowed_license,
    _chunk_sections,
)


def _document(**updates: object) -> Document:
    document = Document(
        id="epmc:synthetic",
        title="Synthetic ischemic stroke study",
        pmcid="PMC123456",
        is_oa=True,
        license="https://creativecommons.org/licenses/by/4.0/",
        provenance=["europe_pmc"],
    )
    return document.model_copy(update=updates)


@pytest.mark.parametrize(
    ("license_text", "expected"),
    [
        ("CC BY 4.0", "CC-BY-4.0"),
        ("CC-BY-4.0", "CC-BY-4.0"),
        ("CC BY-SA 4.0", "CC-BY-SA-4.0"),
        ("CC-BY-SA-4.0", "CC-BY-SA-4.0"),
        ("CC BY-NC 4.0", None),
        ("CC BY-ND 4.0", None),
        ("CC BY-NC-SA 4.0", None),
    ],
)
def test_oa_license_allowlist_does_not_broaden_restricted_licenses(
    license_text: str,
    expected: str | None,
) -> None:
    root = ET.fromstring(
        f"<article><permissions><license>{license_text}</license></permissions></article>"
    )

    assert _allowed_license(None, root) == expected


@pytest.mark.asyncio
async def test_europe_pmc_oa_ingestion_preserves_license_section_and_checksum() -> None:
    xml = b"""<article xmlns:xlink="http://www.w3.org/1999/xlink">
      <front><article-meta><permissions><license license-type="CC BY">
        <license-p>Creative Commons Attribution</license-p>
      </license></permissions></article-meta></front>
      <body><sec><title>Results</title><p>Synthetic ischemic stroke evidence.</p>
      <p>No instructions in this article are executed.</p></sec></body>
    </article>"""

    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path.endswith("/PMC123456/fullTextXML")
        return httpx.Response(200, content=xml, headers={"content-type": "application/xml"})

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler), base_url="https://europepmc.test"
    ) as http_client:
        client = EuropePmcOaFullTextClient(
            client=http_client,
            clock=lambda: datetime(2026, 8, 1, tzinfo=UTC),
        )
        result = await client.ingest(_document(), snapshot_id="snapshot-1")

    assert result.report.status == "stored"
    assert result.report.license == "CC-BY-4.0"
    assert result.report.checksum_sha256 is not None
    assert result.document.full_text_stored
    assert result.document.metadata["full_text_acquired_at"] == "2026-08-01T00:00:00+00:00"
    assert result.chunks[0].section == "Results"
    assert result.chunks[0].token_count > 0
    assert "europe_pmc:fulltext_xml" in result.document.provenance


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("document", "response", "reason"),
    [
        (_document(is_oa=False), None, "not_open_access"),
        (_document(pmcid=None), None, "pmcid_missing_or_invalid"),
        (
            _document(license=None),
            httpx.Response(
                200,
                content=b"<article><body><sec><p>Unknown license.</p></sec></body></article>",
            ),
            "license_not_allowlisted",
        ),
        (
            _document(),
            httpx.Response(200, content=b"<!DOCTYPE article><article />"),
            "unsafe_xml_declaration",
        ),
    ],
)
async def test_oa_ingestion_fail_closes_without_eligible_license_or_safe_xml(
    document: Document,
    response: httpx.Response | None,
    reason: str,
) -> None:
    async def handler(_: httpx.Request) -> httpx.Response:
        assert response is not None
        return response

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler), base_url="https://europepmc.test"
    ) as http_client:
        result = await EuropePmcOaFullTextClient(client=http_client).ingest(
            document, snapshot_id="snapshot-1"
        )

    assert result.report.status == "skipped"
    assert result.report.reason == reason
    assert not result.document.full_text_stored
    assert result.chunks == ()


def test_oa_chunking_keeps_long_documents_between_350_and_700_tokens() -> None:
    text = " ".join(
        f"Sentence {index} contains five synthetic evidence words." for index in range(220)
    )

    chunks = _chunk_sections(
        [("Results", text)],
        document_id="synthetic",
        snapshot_id="snapshot-1",
    )

    assert len(chunks) >= 2
    assert all(350 <= chunk.token_count <= 700 for chunk in chunks)
    assert chunks[1].text.startswith(chunks[0].text.split(". ")[-1])


@pytest.mark.asyncio
async def test_seed_cli_writes_sanitized_oa_report_and_snapshot_links(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manifest = PublicSeedManifest(
        snapshot_id="source-snapshot",
        created_at=datetime(2026, 7, 30, tzinfo=UTC),
        query="synthetic ischemic stroke",
        requested_limit=1,
        document_count=1,
        sources={"europe_pmc": SourceCollectionStats(succeeded=1)},
        documents=[_document()],
    )
    input_manifest = tmp_path / "input.json"
    input_manifest.write_text(manifest.model_dump_json(), encoding="utf-8")

    class FakeOaClient:
        async def close(self) -> None:
            return None

        async def ingest(self, document: Document, *, snapshot_id: str) -> OaIngestionResult:
            updated = document.model_copy(update={"license": "CC-BY-4.0", "full_text_stored": True})
            chunk = Chunk(
                id=f"{snapshot_id}-chunk",
                document_id=document.id,
                ordinal=0,
                text="Private-to-report synthetic full text.",
                section="Results",
                token_count=5,
            )
            return OaIngestionResult(
                report=OaIngestionReport(
                    document_id=document.id,
                    status="stored",
                    license="CC-BY-4.0",
                    acquired_at=datetime(2026, 8, 1, tzinfo=UTC),
                    checksum_sha256="a" * 64,
                    chunk_count=1,
                ),
                document=updated,
                chunks=(chunk,),
            )

    monkeypatch.setattr(
        "deepresearch_agent.seed.cli.EuropePmcOaFullTextClient",
        FakeOaClient,
    )
    output = tmp_path / "output.json"
    database = tmp_path / "corpus.sqlite"
    report = tmp_path / "report.json"
    args = argparse.Namespace(
        limit=1,
        input_manifest=input_manifest,
        crossref_mailto=None,
        unpaywall_email=None,
        query="unused",
        include_oa_full_text=True,
        database=database,
        embedding_provider="hash",
        allow_public_gemini_embeddings=False,
        output=output,
        oa_report=report,
    )

    assert await collect_and_write(args) == 0

    output_payload = json.loads(output.read_text(encoding="utf-8"))
    report_text = report.read_text(encoding="utf-8")
    report_payload = json.loads(report_text)
    assert output_payload["schema_version"] == "2.0"
    assert output_payload["content_policy"]["article_text_downloaded"] is True
    assert report_payload["stored"] == 1
    assert "Private-to-report" not in report_text
    with sqlite3.connect(database) as connection:
        snapshot_id = output_payload["snapshot_id"]
        assert connection.execute("SELECT snapshot_id FROM document").fetchone()[0] == snapshot_id
        assert connection.execute("SELECT snapshot_id FROM chunk").fetchone()[0] == snapshot_id
