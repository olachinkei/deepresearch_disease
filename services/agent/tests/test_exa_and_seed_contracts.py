from __future__ import annotations

import httpx
import pytest

from deepresearch_agent.domain.models import (
    Evidence,
    EvidenceStage,
    PublicationStatus,
    SourceKind,
    VerificationStatus,
)
from deepresearch_agent.infrastructure.exa import (
    ExaAdapterError,
    ExaErrorKind,
    ExaSearchClient,
)
from deepresearch_agent.infrastructure.publication_metadata import (
    EuropePmcMetadataVerifier,
    MetadataVerificationError,
)
from deepresearch_agent.seed.collector import PublicSeedCollector


@pytest.mark.asyncio
async def test_exa_request_uses_publication_highlights_contract() -> None:
    captured: dict[str, object] = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        captured["payload"] = request.read().decode()
        captured["api_key"] = request.headers["x-api-key"]
        return httpx.Response(
            200,
            json={
                "results": [
                    {
                        "id": "paper-1",
                        "title": "Synthetic stroke publication",
                        "url": "https://doi.org/10.1000/SYNTHETIC",
                        "highlights": ["Relevant extractive evidence."],
                        "summary": "This generated summary must not be used.",
                    }
                ]
            },
        )

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(
        transport=transport, base_url="https://api.exa.test"
    ) as http_client:
        client = ExaSearchClient(api_key="test-key", client=http_client)
        evidence = await client.search_publications("ischemic stroke NLRP3")

    payload = str(captured["payload"])
    assert '"type":"auto"' in payload
    assert '"category":"publication"' in payload
    assert '"highlights"' in payload
    assert '"context"' not in payload
    assert captured["api_key"] == "test-key"
    assert evidence[0].excerpt == "Relevant extractive evidence."
    assert "generated summary" not in evidence[0].excerpt
    assert evidence[0].verification_status == VerificationStatus.UNVERIFIED
    assert evidence[0].provenance == ["exa:search"]
    assert evidence[0].doi == "10.1000/synthetic"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("status_code", "kind", "retryable"),
    [
        (401, ExaErrorKind.AUTH, False),
        (429, ExaErrorKind.RATE_LIMIT, True),
        (503, ExaErrorKind.UPSTREAM, True),
        (422, ExaErrorKind.REQUEST, False),
    ],
)
async def test_exa_maps_http_failures_without_leaking_payload(
    status_code: int,
    kind: ExaErrorKind,
    retryable: bool,
) -> None:
    async def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(status_code, text="raw-provider-secret")

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler), base_url="https://api.exa.test"
    ) as http_client:
        client = ExaSearchClient(api_key="secret-api-key", client=http_client)
        with pytest.raises(ExaAdapterError) as caught:
            await client.search_publications("sensitive-query")

    assert caught.value.kind == kind
    assert caught.value.retryable is retryable
    assert "raw-provider-secret" not in str(caught.value)
    assert "sensitive-query" not in str(caught.value)
    assert "secret-api-key" not in str(caught.value)
    assert caught.value.__cause__ is None
    assert caught.value.__suppress_context__


@pytest.mark.asyncio
async def test_exa_maps_timeout_and_schema_drift_to_stable_errors() -> None:
    async def timeout_handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("raw-timeout-detail", request=request)

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(timeout_handler),
        base_url="https://api.exa.test",
    ) as http_client:
        client = ExaSearchClient(api_key="test-key", client=http_client)
        with pytest.raises(ExaAdapterError) as timeout:
            await client.search_publications("sensitive-query")
    assert timeout.value.kind == ExaErrorKind.TIMEOUT
    assert timeout.value.retryable
    assert "raw-timeout-detail" not in str(timeout.value)

    async def schema_handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"results": "raw-schema-drift"})

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(schema_handler),
        base_url="https://api.exa.test",
    ) as http_client:
        client = ExaSearchClient(api_key="test-key", client=http_client)
        with pytest.raises(ExaAdapterError) as schema:
            await client.search_publications("sensitive-query")
    assert schema.value.kind == ExaErrorKind.SCHEMA
    assert not schema.value.retryable
    assert "raw-schema-drift" not in str(schema.value)


@pytest.mark.asyncio
async def test_metadata_verification_is_one_batch_and_preserves_provenance() -> None:
    calls = 0

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        assert 'DOI:"10.1000/test"' in request.url.params["query"]
        assert 'EXT_ID:"12345"' in request.url.params["query"]
        return httpx.Response(
            200,
            json={
                "resultList": {
                    "result": [
                        {
                            "doi": "10.1000/test",
                            "pmid": "12345",
                            "pubTypeList": {
                                "pubType": ["Clinical Trial", "Retracted Publication"]
                            },
                            "isRetracted": "Y",
                        }
                    ]
                }
            },
        )

    evidence = [
        Evidence(
            id="EXA-1",
            document_id="exa:1",
            source_kind=SourceKind.PUBLIC,
            title="Synthetic trial",
            excerpt="Synthetic extract.",
            canonical_url="https://example.org/1",
            doi="10.1000/test",
            provenance=["exa:search"],
        ),
        Evidence(
            id="EXA-2",
            document_id="exa:2",
            source_kind=SourceKind.PUBLIC,
            title="Synthetic PMID record",
            excerpt="Synthetic extract.",
            canonical_url="https://example.org/2",
            pmid="12345",
            provenance=["exa:search"],
        ),
    ]
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
        base_url="https://europepmc.test",
    ) as http_client:
        verifier = EuropePmcMetadataVerifier(client=http_client)
        verified = await verifier.verify(evidence)
        cached = await verifier.verify(evidence)

    assert calls == 1
    assert cached == verified
    assert all(item.verification_status == VerificationStatus.VERIFIED for item in verified)
    assert all(item.publication_status == PublicationStatus.RETRACTED for item in verified)
    assert all(item.retracted for item in verified)
    assert all(item.evidence_stage == EvidenceStage.CLINICAL for item in verified)
    assert all("europe_pmc:verified" in item.provenance for item in verified)


@pytest.mark.asyncio
async def test_metadata_failure_is_sanitized_and_invalid_identifier_is_not_sent() -> None:
    calls = 0

    async def handler(_: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(503, text="raw-metadata-provider-payload")

    invalid = Evidence(
        id="EXA-invalid",
        document_id="exa:invalid",
        source_kind=SourceKind.PUBLIC,
        title="Synthetic invalid identifier",
        excerpt="Synthetic extract.",
        doi='10.1000/test" OR *:*',
    )
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
        base_url="https://europepmc.test",
    ) as http_client:
        verifier = EuropePmcMetadataVerifier(client=http_client)
        unchanged = await verifier.verify([invalid])
        assert unchanged == [invalid]
        assert calls == 0

        valid = invalid.model_copy(update={"doi": "10.1000/test"})
        with pytest.raises(MetadataVerificationError) as caught:
            await verifier.verify([valid])

    assert calls == 1
    assert "raw-metadata-provider-payload" not in str(caught.value)


@pytest.mark.asyncio
async def test_seed_collector_is_metadata_only_and_enriches_crossref() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        if "europepmc" in request.url.path:
            return httpx.Response(
                200,
                json={
                    "resultList": {
                        "result": [
                            {
                                "id": "1",
                                "source": "MED",
                                "pmid": "1",
                                "doi": "10.1000/test",
                                "title": "Synthetic ischemic stroke study",
                                "abstractText": "Synthetic abstract.",
                                "pubYear": "2015",
                                "isOpenAccess": "N",
                            }
                        ]
                    }
                },
            )
        if "crossref" in request.url.host:
            return httpx.Response(
                200,
                json={
                    "message": {
                        "container-title": ["Synthetic Journal"],
                        "license": [{"URL": "https://creativecommons.org/licenses/by/4.0/"}],
                    }
                },
            )
        raise AssertionError(f"Unexpected URL: {request.url}")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        collector = PublicSeedCollector(client=client)
        manifest = await collector.collect(limit=1)

    assert manifest.document_count == 1
    assert manifest.content_policy.metadata_only
    assert not manifest.content_policy.article_text_downloaded
    assert not manifest.documents[0].full_text_stored
    assert manifest.documents[0].journal == "Synthetic Journal"
    assert manifest.sources["crossref"].succeeded == 1
